#!/usr/bin/env python3
"""Собирает из существующих инцидентов согласованные наборы Valid-значений
(скелеты), которые гарантированно проходят зависимые validation-списки Ivanti.

Использование:
  IVANTI_API_KEY=... python3 build_skeletons.py --base-url https://host/api --out skeletons.json
"""

import argparse
import json
import re
import ssl
import sys
import urllib.request

COMBOS = [
    "Service_Valid", "Category_Valid", "ActualCategory_Valid", "CauseCode_Valid",
    "Priority_Valid", "Urgency_Valid", "Impact_Valid", "Owner_Valid",
    "OwnerTeam_Valid", "ProfileLink_RecID", "Source_Valid", "Status_Valid",
]
NAMES = ["Service", "Category", "ActualCategory", "CauseCode", "Priority",
         "Urgency", "Impact", "Owner", "OwnerTeam", "Status"]
SELECT = ",".join(COMBOS + NAMES + ["HoursOfOperation", "ProfileLink_Category"])
PAGE = 100
GUID_RE = re.compile(r"^[0-9A-Fa-f]{32}$")


def get(url, api_key, insecure):
    req = urllib.request.Request(url, headers={
        "Authorization": f"rest_api_key={api_key}", "Accept": "application/json"})
    context = None
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=60, context=context) as resp:
        raw = resp.read().decode("utf-8", errors="replace").strip()
    return json.loads(raw) if raw else {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--out", default="skeletons.json")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    rows = []
    for page in range(args.max_pages):
        url = f"{base}/odata/businessobject/incidents?$top={PAGE}&$skip={page*PAGE}&$select={SELECT}"
        batch = get(url, args.api_key, args.insecure).get("value") or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break

    skeletons = []
    for r in rows:
        sk = {"combo": {}, "names": {}}
        ok = True
        for f in COMBOS:
            v = r.get(f)
            if v is None:
                continue
            if isinstance(v, str) and not GUID_RE.match(v.strip()):
                continue
            sk["combo"][f] = v.strip() if isinstance(v, str) else v
        for f in NAMES:
            if r.get(f) is not None:
                sk["names"][f] = r[f]
        sk["HoursOfOperation"] = r.get("HoursOfOperation")
        if sk["combo"].get("ProfileLink_RecID") and sk["combo"].get("Category_Valid") and sk["combo"].get("Service_Valid"):
            skeletons.append(sk)

    uniq = {}
    for sk in skeletons:
        key = json.dumps(sk["combo"], sort_keys=True)
        uniq.setdefault(key, sk)
    result = list(uniq.values())

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(f"Инцидентов просканировано: {len(rows)}")
    print(f"Уникальных согласованных комбинаций: {len(result)}")
    cat = {}
    for sk in result:
        cat[sk["names"].get("Category")] = cat.get(sk["names"].get("Category"), 0) + 1
    print(f"Категорий представлено: {len(cat)}")
    print(f"Сохранено: {args.out}")


if __name__ == "__main__":
    main()
