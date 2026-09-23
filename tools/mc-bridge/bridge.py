#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Локальный мост к расширению MonkeyCode.

Расширение MonkeyCode — это тонкий релей над chrome.debugger: оно соединяется
по WebSocket с локальным "agent" (по умолчанию ws://127.0.0.1:7440/ext) и
выполняет любые приходящие CDP-команды. Приложение monkeycode-desktop.exe в
сборке 26091601.0.0 ломает browser_snapshot (парсер ждёт массив, получает
строку), из-за чего клики недоступны. Этот скрипт встаёт на место "agent":

  * слушает тот же WebSocket-протокол на /ext (handshake + hello.ok),
  * отдаёт локальный HTTP-API, через который можно гнать произвольный CDP:
    реальные клики мышью (Input.dispatchMouseEvent), ввод, навигация,
    скриншоты, Runtime.evaluate.

Запуск:  python bridge.py            (порт по умолчанию 8791)
Точки API (все на том же порту, кроме /ext):
  GET  /status
  GET  /tabs
  POST /op     {"op":"tabs.list"}                       любой кадр протокола
  POST /cdp    {"method":"...","params":{},"tabId":N}
  POST /eval   {"expression":"...","tabId":N}
  POST /click  {"selector":"...","index":0,"count":2} | {"x":N,"y":N,"tabId":N}
  POST /drag   {"x1":N,"y1":N,"x2":N,"y2":N}       выделение протяжкой
  POST /select {"selector":"...","index":0}         выделить текст элемента
  POST /edit   {"command":"copy"}                  selectAll|copy|cut|paste|undo|redo
  POST /key    {"key":"c","modifiers":["ctrl"],"tabId":N}
  POST /type   {"text":"...","tabId":N}
  POST /shot   {"path":"C:/tmp/shot.png","tabId":N}
"""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("MCB_PORT", "8791"))
TIMEOUT = float(os.environ.get("MCB_TIMEOUT", "40"))
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.environ.get("MCB_LOG", os.path.join(HERE, "bridge.log"))
# Токен, который отдаём расширению в hello.ok. По умолчанию — тот, который
# расширение само предъявило (то есть выданный приложением): тогда возврат на
# порт 7440 не требует перепаривания. Жёстко задать можно через MCB_TOKEN.
TOKEN = os.environ.get("MCB_TOKEN", "")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

lock = threading.RLock()
client_sock = None
client_info = {}
pending = {}
seq = [0]
default_tab_id = [None]
log_file = open(LOG_PATH, "a", encoding="utf-8", buffering=1)


def log(*parts):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in parts)
    print(line, flush=True)
    try:
        log_file.write(line + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- WS
def ws_accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("соединение закрыто")
        buf += chunk
    return buf


def ws_read_frame(sock):
    """Один кадр: (fin, opcode, payload)."""
    b1, b2 = recv_exact(sock, 2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(sock, 8))[0]
    masked = b2 & 0x80
    mask = recv_exact(sock, 4) if masked else None
    data = recv_exact(sock, length) if length else b""
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return fin, opcode, data


def ws_read_message(sock):
    """Целое сообщение. Расширение фрагментирует крупные ответы (скриншоты,
    длинные тела), поэтому кадры с FIN=0 нужно склеивать до последнего."""
    fin, opcode, data = ws_read_frame(sock)
    if opcode >= 0x8:                      # control-кадры не фрагментируются
        return opcode, data
    buf = data
    while not fin:
        fin, _opcode, chunk = ws_read_frame(sock)
        buf += chunk
    return opcode, buf


def ws_send(sock, text):
    data = text.encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(len(data))
    elif len(data) < 65536:
        header.append(126)
        header += struct.pack(">H", len(data))
    else:
        header.append(127)
        header += struct.pack(">Q", len(data))
    with lock:
        sock.sendall(bytes(header) + data)


def ws_send_raw(sock, opcode, payload=b""):
    header = bytearray([0x80 | opcode])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header.append(126)
        header += struct.pack(">H", len(payload))
    else:
        header.append(127)
        header += struct.pack(">Q", len(payload))
    with lock:
        sock.sendall(bytes(header) + payload)


# ------------------------------------------------------------------ клиент-поток
def handle_message(msg):
    if msg.get("event") == "hello":
        ext = msg.get("ext") or {}
        with lock:
            client_info.clear()
            client_info.update(ext)
            client_info["browser"] = msg.get("browser", "")
            client_info["proto"] = msg.get("proto")
            sock = client_sock
        log("hello:", json.dumps(client_info, ensure_ascii=False))
        presented = ((msg.get("auth") or {}).get("token") or "").strip()
        token = TOKEN or presented or os.urandom(16).hex()
        log("hello.ok: token = %s" % ("свой (MCB_TOKEN)" if TOKEN else
                                      ("от расширения" if presented else "новый, сгенерирован")))
        if sock is not None:
            ws_send(sock, json.dumps({"event": "hello.ok", "token": token}))
        return
    if msg.get("op") == "ping":
        with lock:
            sock = client_sock
        if sock is not None:
            ws_send(sock, json.dumps({"event": "pong"}))
        return
    mid = msg.get("id")
    if mid is not None:
        with lock:
            slot = pending.pop(mid, None)
        if slot is not None:
            slot["msg"] = msg
            slot["event"].set()
        return
    if msg.get("event") not in ("cdp",):
        log("event:", json.dumps(msg, ensure_ascii=False)[:300])


def client_loop(sock):
    global client_sock
    try:
        while True:
            opcode, data = ws_read_message(sock)
            if opcode == 0x8:
                break
            if opcode == 0x9:
                ws_send_raw(sock, 0xA, data[:125])
                continue
            if opcode != 0x1:
                continue
            try:
                msg = json.loads(data.decode("utf-8", "replace"))
            except Exception as exc:
                log("нечитаемый кадр:", exc)
                continue
            handle_message(msg)
    except Exception as exc:
        log("клиент отключился:", exc)
    finally:
        with lock:
            if client_sock is sock:
                client_sock = None
                client_info.clear()
            slots = list(pending.values())
        for slot in slots:
            slot["event"].set()
        try:
            sock.close()
        except Exception:
            pass


# ------------------------------------------------------------------- вызовы ops
def call_op(op, **kwargs):
    with lock:
        if client_sock is None:
            raise RuntimeError("расширение не подключено к мосту")
        seq[0] += 1
        mid = seq[0]
        slot = {"event": threading.Event(), "msg": None}
        pending[mid] = slot
        payload = {"id": mid, "op": op}
        payload.update(kwargs)
        sock = client_sock
    ws_send(sock, json.dumps(payload, ensure_ascii=False))
    if not slot["event"].wait(TIMEOUT):
        with lock:
            pending.pop(mid, None)
        raise TimeoutError("нет ответа на %s за %ss" % (op, TIMEOUT))
    msg = slot["msg"]
    if msg is None:
        raise RuntimeError("соединение оборвалось во время %s" % op)
    if msg.get("error"):
        raise RuntimeError(json.dumps(msg["error"], ensure_ascii=False))
    return msg.get("result")


def list_tabs():
    tabs = call_op("tabs.list") or []
    return tabs


def pick_tab(tab_id=None):
    tabs = list_tabs()
    if tab_id:
        for t in tabs:
            if int(t.get("tabId", -1)) == int(tab_id):
                default_tab_id[0] = int(tab_id)
                return int(tab_id)
        raise RuntimeError("вкладка %s не найдена" % tab_id)
    if default_tab_id[0] is not None:
        for t in tabs:
            if int(t.get("tabId", -1)) == default_tab_id[0]:
                return default_tab_id[0]
    for t in tabs:
        if t.get("controlled") and t.get("active"):
            default_tab_id[0] = int(t["tabId"])
            return default_tab_id[0]
    for t in tabs:
        if t.get("controlled"):
            default_tab_id[0] = int(t["tabId"])
            return default_tab_id[0]
    raise RuntimeError("нет вкладок под управлением; передайте tabId явно")


def cdp(method, params=None, tab_id=None):
    tid = pick_tab(tab_id)
    return call_op("cdp", tabId=tid, method=method, params=params or {})


def evaluate(expression, tab_id=None):
    res = cdp("Runtime.evaluate",
              {"expression": expression, "returnByValue": True,
               "awaitPromise": True, "userGesture": True},
              tab_id)
    if res.get("exceptionDetails"):
        raise RuntimeError("JS: " + json.dumps(res["exceptionDetails"], ensure_ascii=False)[:500])
    return (res.get("result") or {}).get("value")


KEY_CODES = {
    "Tab": (9, "Tab", "Tab"), "Enter": (13, "Enter", "Enter"), "Escape": (27, "Escape", "Escape"),
    "Backspace": (8, "Backspace", "Backspace"), "Delete": (46, "Delete", "Delete"),
    "Home": (36, "Home", "Home"), "End": (35, "End", "End"),
    "ArrowLeft": (37, "ArrowLeft", "ArrowLeft"), "ArrowUp": (38, "ArrowUp", "ArrowUp"),
    "ArrowRight": (39, "ArrowRight", "ArrowRight"), "ArrowDown": (40, "ArrowDown", "ArrowDown"),
    "PageUp": (33, "PageUp", "PageUp"), "PageDown": (34, "PageDown", "PageDown"),
    "Space": (32, " ", "Space"), "F5": (116, "F5", "F5"),
}


MODIFIERS = {"alt": 1, "ctrl": 2, "control": 2, "meta": 4, "cmd": 4, "win": 4, "shift": 8}
LETTERS = {chr(c): ("Key" + chr(c).upper(), c) for c in range(ord("a"), ord("z") + 1)}
DIGITS = {str(d): ("Digit" + str(d), ord("0") + d) for d in range(10)}


def modifier_mask(names):
    mask = 0
    for name in (names or []):
        mask |= MODIFIERS.get(str(name).lower(), 0)
    return mask


def key_code(key):
    """→ (key, code, windowsVirtualKeyCode). Без vkCode браузер не признаёт
    сочетания вида Ctrl+C/Ctrl+V, поэтому буквы и цифры расписываем сами."""
    if key in KEY_CODES:
        vk, keyname, dom = KEY_CODES[key]
        return keyname, dom, vk
    low = key.lower()
    if low in LETTERS:
        dom, vk = LETTERS[low]
        return key, dom, vk
    if key in DIGITS:
        dom, vk = DIGITS[key]
        return key, dom, vk
    return key, "", 0


def press_key(key, tab_id=None, modifiers=0, commands=None):
    keyname, dom, vk = key_code(key)
    params = {"key": keyname, "code": dom, "modifiers": modifiers,
              "windowsVirtualKeyCode": vk or 0, "nativeVirtualKeyCode": vk or 0}
    down = dict(params, type="rawKeyDown" if (commands or not modifiers) else "keyDown")
    if commands:
        down["commands"] = list(commands)
    cdp("Input.dispatchKeyEvent", down, tab_id)
    if len(key) == 1 and not modifiers and not commands:
        cdp("Input.dispatchKeyEvent", dict(params, type="char", text=keyname), tab_id)
    cdp("Input.dispatchKeyEvent", dict(params, type="keyUp"), tab_id)
    return True


# Chromium не считает командами правки внедрённые нажатия: сочетание Ctrl+C
# доходит до страницы как keydown, но текст в буфер не попадает. Работает только
# явный параметр commands у Input.dispatchKeyEvent.
EDIT_COMMANDS = {
    "selectall": ("a", 65, "selectAll"),
    "copy": ("c", 67, "copy"),
    "cut": ("x", 88, "cut"),
    "paste": ("v", 86, "paste"),
    "undo": ("z", 90, "undo"),
    "redo": ("y", 89, "redo"),
}


def send_command(name, tab_id=None):
    item = EDIT_COMMANDS.get(str(name).replace("_", "").replace("-", "").lower())
    if not item:
        raise RuntimeError("неизвестная команда: %s (есть: %s)" % (name, ", ".join(EDIT_COMMANDS)))
    key, vk, command = item
    params = {"key": key, "code": "Key" + key.upper(), "modifiers": 2,
              "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    cdp("Input.dispatchKeyEvent", dict(params, type="rawKeyDown", commands=[command]), tab_id)
    cdp("Input.dispatchKeyEvent", dict(params, type="keyUp"), tab_id)
    return command


SELECT_JS = """(() => {
  const el = Array.from(document.querySelectorAll(%s))[%d];
  if (!el) return {found: false};
  el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
  if (el.focus) el.focus();
  const sel = window.getSelection();
  sel.removeAllRanges();
  if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') { el.select(); }
  else {
    const r = document.createRange();
    r.selectNodeContents(el);
    sel.addRange(r);
  }
  return {found: true, tag: el.tagName.toLowerCase(), text: String(sel).replace(/\\s+/g, ' ').trim().slice(0, 160)};
})()"""

# Страница для самопроверки моста: поле ввода, текст для выделения, счётчик кликов.
TEST_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>MC bridge test</title>
<style>body{font:16px system-ui;margin:24px}input,textarea{font:inherit;width:560px;padding:6px}
#count{font-weight:700}</style></head>
<body>
<h2>Тестовая страница моста</h2>
<p>Поле ввода: <input id="inp" placeholder="введите текст"></p>
<p>Textarea: <textarea id="ta" rows="2" placeholder="textarea"></textarea></p>
<p id="para">Альфа бета гамма дельта эпсилон — текст для выделения двойным щелчком и протяжкой мышью.</p>
<p>Кликов: <span id="count">0</span> <button id="btn">Кнопка</button></p>
<p id="dbg"></p>
<script>
  window.__events = [];
  document.getElementById('btn').addEventListener('click', function () {
    var c = document.getElementById('count');
    c.textContent = String(Number(c.textContent) + 1);
  });
  document.addEventListener('click', function (e) {
    window.__events.push('click detail=' + e.detail + ' on ' + (e.target.id || e.target.tagName));
  }, true);
  document.addEventListener('dblclick', function (e) {
    window.__events.push('dblclick on ' + (e.target.id || e.target.tagName));
  }, true);
  document.addEventListener('copy', function (e) {
    window.__events.push('copy trusted=' + e.isTrusted);
  }, true);
  document.addEventListener('paste', function (e) {
    window.__events.push('paste:' + (e.clipboardData ? e.clipboardData.getData('text') : '') + ' trusted=' + e.isTrusted);
  }, true);
  document.addEventListener('selectionchange', function () {
    document.getElementById('dbg').textContent = 'Выделено: ' + String(window.getSelection()).slice(0, 120);
  });
</script>
</body></html>
"""


def click_at(x, y, tab_id=None, count=1):
    """Настоящий клик мышью; count=2 — двойной, 3 — тройной (выделение слова/строки)."""
    base = {"x": float(x), "y": float(y), "button": "left", "modifiers": 0}
    cdp("Input.dispatchMouseEvent", dict(base, type="mouseMoved", buttons=0, clickCount=0), tab_id)
    for i in range(1, int(count) + 1):
        cdp("Input.dispatchMouseEvent", dict(base, type="mousePressed", buttons=1, clickCount=i), tab_id)
        cdp("Input.dispatchMouseEvent", dict(base, type="mouseReleased", buttons=0, clickCount=i), tab_id)
    return True


def drag(x1, y1, x2, y2, tab_id=None, steps=12):
    """Протяжка мышью с зажатой кнопкой — настоящее выделение текста."""
    base = {"button": "left", "modifiers": 0, "clickCount": 1}
    cdp("Input.dispatchMouseEvent", dict(base, type="mouseMoved", x=float(x1), y=float(y1), buttons=0), tab_id)
    cdp("Input.dispatchMouseEvent", dict(base, type="mousePressed", x=float(x1), y=float(y1), buttons=1), tab_id)
    for i in range(1, int(steps) + 1):
        t = i / float(steps)
        cdp("Input.dispatchMouseEvent",
            dict(base, type="mouseMoved", x=x1 + (x2 - x1) * t, y=y1 + (y2 - y1) * t, buttons=1), tab_id)
    cdp("Input.dispatchMouseEvent", dict(base, type="mouseReleased", x=float(x2), y=float(y2), buttons=0), tab_id)
    return True


FIND_JS = """(() => {
  const sel = %s;
  const idx = %d;
  const what = %s;              // null | {x:0,y:0}
  const all = Array.from(document.querySelectorAll(sel));
  const el = all[idx];
  if (!el) return {found: false, count: all.length};
  el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
  const r = el.getBoundingClientRect();
  return {found: true, count: all.length, x: r.x + r.width / 2, y: r.y + r.height / 2,
          w: r.width, h: r.height, tag: el.tagName.toLowerCase(),
          text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 80)};
})()"""


# ------------------------------------------------------------------- HTTP слой
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mc-bridge"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise RuntimeError("тело запроса не JSON")

    # --- WebSocket-точка, куда подключается расширение ---
    def _serve_ws(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._json({"error": "нет Sec-WebSocket-Key"}, 400)
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept_key(key))
        self.end_headers()
        self.wfile.flush()
        global client_sock
        sock = self.connection
        with lock:
            old = client_sock
            client_sock = sock
        if old is not None and old is not sock:
            log("заменяю предыдущее подключение расширения")
            try:
                old.close()
            except Exception:
                pass
        log("расширение подключилось")
        self.close_connection = True
        client_loop(sock)

    def do_GET(self):
        if self.path.split("?")[0] == "/ext":
            self._serve_ws()
            return
        path = self.path.split("?")[0]
        try:
            if path == "/status":
                with lock:
                    info = dict(client_info)
                    connected = client_sock is not None
                self._json({"connected": connected, "client": info,
                            "defaultTabId": default_tab_id[0], "port": PORT})
            elif path == "/tabs":
                self._json({"tabs": list_tabs()})
            elif path == "/testpage":
                page = TEST_PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            else:
                self._json({"error": "неизвестный путь", "path": path}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
            tab_id = body.get("tabId")
            if path == "/op":
                op = body.pop("op", None)
                if not op:
                    raise RuntimeError("нужно поле op")
                self._json({"result": call_op(op, **body)})
            elif path == "/cdp":
                self._json({"result": cdp(body.get("method", ""), body.get("params") or {}, tab_id)})
            elif path == "/eval":
                self._json({"value": evaluate(body.get("expression", ""), tab_id)})
            elif path == "/click":
                count = int(body.get("count", 1))
                if "selector" in body:
                    found = evaluate(FIND_JS % (json.dumps(body["selector"]), int(body.get("index", 0)), "null"), tab_id)
                    if not found or not found.get("found"):
                        self._json({"error": "элемент не найден", "detail": found}, 404)
                        return
                    click_at(found["x"], found["y"], tab_id, count)
                    self._json({"clicked": found, "count": count})
                elif "x" in body and "y" in body:
                    click_at(body["x"], body["y"], tab_id, count)
                    self._json({"clicked": {"x": body["x"], "y": body["y"]}, "count": count})
                else:
                    raise RuntimeError("нужен selector или x/y")
            elif path == "/drag":
                drag(body["x1"], body["y1"], body["x2"], body["y2"], tab_id, int(body.get("steps", 12)))
                self._json({"dragged": [body["x1"], body["y1"], body["x2"], body["y2"]]})
            elif path == "/select":
                res = evaluate(SELECT_JS % (json.dumps(body.get("selector", "body")), int(body.get("index", 0))), tab_id)
                if not res or not res.get("found"):
                    self._json({"error": "элемент не найден", "detail": res}, 404)
                    return
                self._json({"selected": res})
            elif path == "/key":
                self._json({"ok": press_key(body.get("key", "Tab"), tab_id,
                                            modifier_mask(body.get("modifiers")),
                                            body.get("commands"))})
            elif path == "/edit":
                self._json({"command": send_command(body.get("command", ""), tab_id)})
            elif path == "/type":
                text = body.get("text", "")
                self._json({"ok": bool(cdp("Input.insertText", {"text": text}, tab_id) is not None)})
            elif path == "/shot":
                res = cdp("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}, tab_id)
                data = base64.b64decode(res["data"])
                out = body.get("path") or os.path.join(HERE, "shot-%d.png" % int(time.time()))
                with open(out, "wb") as fh:
                    fh.write(data)
                self._json({"path": out, "bytes": len(data)})
            else:
                self._json({"error": "неизвестный путь", "path": path}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    log("мост слушает http://%s:%d  (WS: ws://%s:%d/ext)" % (HOST, PORT, HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("остановлен")
