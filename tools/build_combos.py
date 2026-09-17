#!/usr/bin/env python3
"""Строит выровненный пул базовых комбинаций инцидента.

Из скелетов берутся уникальные логические комбинации
(service + category + actualcategory + causecode + source, без исполнителя,
команды и полей срочности/влияния/приоритета) и объединяются в один пул.
Это убирает перекос, когда частые в исходных данных сервисы и категории
доминировали в генерации.

Срочность, влияние и приоритет выносятся в отдельные пулы (variation.json),
чтобы подставляться независимо.

Использование:
  python3 build_combos.py --skeletons skeletons.json --out combos.json --variation variation.json
"""

import argparse
import collections
import json

COMBOS = ["Service_Valid", "Category_Valid", "ActualCategory_Valid",
          "CauseCode_Valid", "Source_Valid"]
VARY = ["Urgency_Valid", "Impact_Valid", "Priority_Valid"]
NAMES = ["Service", "Category", "ActualCategory", "CauseCode", "Source",
         "Urgency", "Impact", "Priority", "Status"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skeletons", default="skeletons.json")
    parser.add_argument("--out", default="combos.json")
    parser.add_argument("--variation", default="variation.json")
    args = parser.parse_args()

    with open(args.skeletons, "r", encoding="utf-8") as fh:
        skeletons = json.load(fh)

    unique = {}
    for sk in skeletons:
        combo = sk.get("combo", {})
        if not all(combo.get(f) for f in ("Service_Valid", "Category_Valid", "ActualCategory_Valid")):
            continue
        key = tuple(combo.get(f) for f in COMBOS)
        if key in unique:
            continue
        entry = {
            "combo": {f: combo[f] for f in COMBOS if combo.get(f)},
            "names": {f: sk.get("names", {}).get(f) for f in NAMES
                      if sk.get("names", {}).get(f) is not None},
            "HoursOfOperation": sk.get("HoursOfOperation"),
        }
        unique[key] = entry

    combos = list(unique.values())

    variation = {v: sorted({sk["combo"][v] for sk in skeletons
                            if sk.get("combo", {}).get(v)}) for v in VARY}
    variation_names = {}
    for v in VARY:
        name_field = v[:-6] if v.endswith("_Valid") else v
        variation_names[v] = sorted({sk.get("names", {}).get(name_field)
                                     for sk in skeletons
                                     if sk.get("names", {}).get(name_field) is not None})
    statuses = sorted({(sk["combo"].get("Status_Valid"), sk.get("names", {}).get("Status"))
                       for sk in skeletons if sk["combo"].get("Status_Valid")})

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(combos, fh, ensure_ascii=False, indent=2)
    with open(args.variation, "w", encoding="utf-8") as fh:
        json.dump({"values": variation, "names": variation_names,
                   "statuses": statuses}, fh, ensure_ascii=False, indent=2)

    svc = collections.Counter(c["names"].get("Service") for c in combos)
    cat = collections.Counter(c["names"].get("Category") for c in combos)
    print(f"Уникальных базовых комбинаций: {len(combos)}")
    print(f"Сервисов: {len(svc)}  Категорий: {len(cat)}")
    print("Комбинаций по сервисам:")
    for name, count in svc.most_common():
        print(f"  {count:4d}  {name}")
    for v in VARY:
        print(f"{v}: {len(variation[v])} значений")
    print(f"Сохранено: {args.out}, {args.variation}")


if __name__ == "__main__":
    main()
