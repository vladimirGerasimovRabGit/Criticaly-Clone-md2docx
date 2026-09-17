#!/usr/bin/env python3
"""Снимает с инстанса Ivanti справочник Valid-значений и сохраняет reference.json.

Использование:
  IVANTI_API_KEY=... python3 build_reference.py \
      --base-url https://host/api --out reference.json
"""

import argparse
import json
import re
import ssl
import sys
import urllib.request

GUID_RE = re.compile(r"^[0-9A-Fa-f]{32}$")

PAIRS = [
    ("Service", "Service_Valid"),
    ("Source", "Source_Valid"),
    ("Priority", "Priority_Valid"),
    ("Category", "Category_Valid"),
    ("ActualCategory", "ActualCategory_Valid"),
    ("CauseCode", "CauseCode_Valid"),
    ("Urgency", "Urgency_Valid"),
    ("Impact", "Impact_Valid"),
    ("Owner", "Owner_Valid"),
    ("OwnerTeam", "OwnerTeam_Valid"),
    ("Status", "Status_Valid"),
]
EXTRA = ["ProfileLink_RecID", "HoursOfOperation"]
SELECT = ",".join([n for pair in PAIRS for n in pair]) + "," + ",".join(EXTRA)
PAGE = 100


def get(url, api_key, insecure):
    req = urllib.request.Request(url, headers={
        "Authorization": f"rest_api_key={api_key}",
        "Accept": "application/json",
    })
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
    parser.add_argument("--out", default="reference.json")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    rows = []
    for page in range(args.max_pages):
        url = f"{base}/odata/businessobject/incidents?$top={PAGE}&$skip={page * PAGE}&$select={SELECT}"
        data = get(url, args.api_key, args.insecure)
        batch = data.get("value") or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
    if not rows:
        print("Инциденты не найдены.", file=sys.stderr)
        sys.exit(1)

    enums = {}
    skipped = []
    for name_f, valid_f in PAIRS:
        mapping = {}
        for r in rows:
            valid, name = r.get(valid_f), r.get(name_f)
            if not valid or name is None:
                continue
            if not GUID_RE.match(valid.strip()):
                skipped.append((valid_f, valid, name))
                continue
            mapping.setdefault(valid.strip(), name)
        enums[valid_f] = mapping
    if skipped:
        print(f"Пропущено повреждённых GUID: {len(skipped)}", file=sys.stderr)
        for f, v, n in skipped:
            print(f"  {f} = {v!r} ({n})", file=sys.stderr)

    profiles = sorted({r["ProfileLink_RecID"] for r in rows if r.get("ProfileLink_RecID")})
    hoo = sorted({r["HoursOfOperation"] for r in rows if r.get("HoursOfOperation")})

    reference = {
        "defaults": {
            "HoursOfOperation": hoo[0] if hoo else "Weekly HOP",
            "CreatedBy": "HEATAdmin",
            "LastModBy": "HEATAdmin",
            "ProfileLink_RecID": profiles[0] if profiles else None,
        },
        "email_domain": "saasitdemo.com",
        "enums": enums,
        "profiles": profiles,
        "hours_of_operation": hoo,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(reference, fh, ensure_ascii=False, indent=2)

    print(f"Инцидентов просканировано: {len(rows)}")
    for f, mapping in enums.items():
        print(f"  {f}: {len(mapping)} значений")
    print(f"  ProfileLink_RecID: {len(profiles)} профилей")
    print(f"Сохранено: {args.out}")


if __name__ == "__main__":
    main()
