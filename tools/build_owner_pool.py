#!/usr/bin/env python3
"""Строит пул допустимых пар «исполнитель ↔ команда» по группам service+category.

Ivanti валидирует Owner/OwnerTeam в контексте сервиса и категории инцидента,
поэтому пары собираются только из реально существующих инцидентов и
применяются строго внутри своей группы.

Использование:
  python3 build_owner_pool.py --skeletons skeletons.json --out owners.json
"""

import argparse
import collections
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skeletons", default="skeletons.json")
    parser.add_argument("--out", default="owners.json")
    args = parser.parse_args()

    with open(args.skeletons, "r", encoding="utf-8") as fh:
        skeletons = json.load(fh)

    groups = collections.defaultdict(dict)
    for sk in skeletons:
        combo = sk.get("combo", {})
        names = sk.get("names", {})
        service = combo.get("Service_Valid")
        category = combo.get("Category_Valid")
        owner = combo.get("Owner_Valid")
        team = combo.get("OwnerTeam_Valid")
        if not (service and category and owner and team):
            continue
        key = f"{service}|{category}"
        groups[key][owner] = {
            "Owner_Valid": owner,
            "OwnerTeam_Valid": team,
            "owner_name": names.get("Owner"),
            "team_name": names.get("OwnerTeam"),
        }

    pool = {k: list(v.values()) for k, v in groups.items()}

    teams = collections.Counter()
    for options in pool.values():
        for opt in options:
            teams[opt["team_name"]] += 1

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(pool, fh, ensure_ascii=False, indent=2)

    print(f"Групп service+category: {len(pool)}")
    total = sum(len(v) for v in pool.values())
    print(f"Допустимых пар исполнитель/команда: {total}")
    print("Распределение по командам:")
    for name, count in teams.most_common():
        print(f"  {count:4d}  {name}")
    print(f"Сохранено: {args.out}")


if __name__ == "__main__":
    main()
