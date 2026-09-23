# -*- coding: utf-8 -*-
# Самопроверка моста mc-bridge: ввод, двойной/тройной клик, выделение протяжкой,
# копирование и вставка через реальные события CDP.

$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8791'

function Post($path, $obj) {
  Invoke-RestMethod -Uri "$base$path" -Method Post -Body ($obj | ConvertTo-Json -Compress -Depth 6) `
    -ContentType 'application/json; charset=utf-8' -TimeoutSec 45
}
function Eval($expr, $tab) { (Post '/eval' @{ expression = $expr; tabId = $tab }).value }
function Check($name, $ok, $detail) { "{0} {1} — {2}" -f $(if ($ok) { '[OK] ' } else { '[НЕТ]' }), $name, $detail }

$savedClip = $null
try { $savedClip = Get-Clipboard -Raw -ErrorAction SilentlyContinue } catch { }

$tab = (Post '/op' @{ op = 'tabs.create'; params = @{ url = "$base/testpage" } }).result.tabId
"вкладка теста: $tab"

for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 400
  if ((Eval 'document.readyState' $tab) -eq 'complete') { break }
}
Check 'страница загружена' ((Eval 'document.title' $tab) -like '*bridge test*') (Eval 'document.title' $tab)

# --- 1. ввод текста -------------------------------------------------------
Post '/click' @{ selector = '#inp'; tabId = $tab } | Out-Null
Post '/type' @{ text = 'Привет, мост!'; tabId = $tab } | Out-Null
$v = Eval 'document.getElementById("inp").value' $tab
Check 'ввод текста (Input.insertText)' ($v -eq 'Привет, мост!') "значение поля: '$v'"

# --- 2. двойной клик по слову --------------------------------------------
$wordJs = @'
(() => {
  const p = document.getElementById('para');
  const i = p.textContent.indexOf('гамма');
  const r = document.createRange();
  r.setStart(p.firstChild, i);
  r.setEnd(p.firstChild, i + 5);
  const b = r.getBoundingClientRect();
  return JSON.stringify({x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2)});
})()
'@
$pos = Eval $wordJs $tab | ConvertFrom-Json
Post '/click' @{ x = $pos.x; y = $pos.y; count = 2; tabId = $tab } | Out-Null
Start-Sleep -Milliseconds 300
$sel = Eval 'String(window.getSelection()).trim()' $tab
Check 'двойной клик выделяет слово' ($sel -eq 'гамма') "выделено: '$sel'"

# --- 3. тройной клик (строка) --------------------------------------------
Post '/click' @{ x = $pos.x; y = $pos.y; count = 3; tabId = $tab } | Out-Null
Start-Sleep -Milliseconds 300
$sel3 = Eval 'String(window.getSelection()).trim()' $tab
Check 'тройной клик выделяет строку' ($sel3.Length -gt $sel.Length) ("длина выделения: " + $sel3.Length)

# --- 4. выделение протяжкой мышью ----------------------------------------
$rectJs = 'JSON.stringify((function(){var r=document.getElementById("para").getBoundingClientRect();return {l:r.left,ri:r.right,t:r.top,b:r.bottom,h:r.height};})())'
$rect = Eval $rectJs $tab | ConvertFrom-Json
Post '/drag' @{ x1 = $rect.l + 3; y1 = $rect.t + ($rect.h / 2); x2 = $rect.ri - 3; y2 = $rect.b - 3; tabId = $tab } | Out-Null
Start-Sleep -Milliseconds 300
$selDrag = Eval 'String(window.getSelection()).trim()' $tab
Check 'выделение протяжкой' ($selDrag -like 'Альфа бета гамма*') ("выделено: '" + $selDrag + "'")

# --- 5. копирование (команда copy) ---------------------------------------
Set-Clipboard -Value 'ДО-КОПИРОВАНИЯ'
Post '/edit' @{ command = 'copy'; tabId = $tab } | Out-Null
Start-Sleep -Milliseconds 500
$clip = Get-Clipboard -Raw
Check 'копирование кладёт выделенное в буфер' ($clip -like 'Альфа бета гамма*') ("в буфере: '" + ($clip -replace '\s+', ' ').Trim() + "'")

# --- 6. вставка (команда paste) -------------------------------------------
Set-Clipboard -Value 'ВСТАВКА-ЧЕРЕЗ-БУФЕР'
Post '/click' @{ selector = '#inp'; tabId = $tab } | Out-Null
Post '/edit' @{ command = 'selectall'; tabId = $tab } | Out-Null
Start-Sleep -Milliseconds 200
Post '/edit' @{ command = 'paste'; tabId = $tab } | Out-Null
Start-Sleep -Milliseconds 400
$v2 = Eval 'document.getElementById("inp").value' $tab
Check 'вставка из буфера' ($v2 -eq 'ВСТАВКА-ЧЕРЕЗ-БУФЕР') "значение поля: '$v2'"
$pasteEv = Eval 'JSON.stringify((window.__events||[]).filter(function(e){return e.indexOf("paste")===0;}))' $tab
Check 'страница увидела доверенное событие paste' ($pasteEv -like '*ВСТАВКА-ЧЕРЕЗ-БУФЕР*trusted=true*') $pasteEv

# --- 7. журнал событий страницы ------------------------------------------
'--- события на тестовой странице ---'
Eval 'JSON.stringify(window.__events, null, 1)' $tab

# --- уборка --------------------------------------------------------------
Post '/op' @{ op = 'tabs.close'; tabId = $tab } | Out-Null
if ($null -ne $savedClip) { Set-Clipboard -Value $savedClip }
"тестовая вкладка закрыта, буфер обмена восстановлен"
