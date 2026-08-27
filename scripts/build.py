# -*- coding: utf-8 -*-
"""Собрать открытый датасет доступности из публичного снапшота Freedom Checker.

Полный снапшот весит ~578 КБ, и коммитить его ежедневно значит за год положить
в репозиторий 200+ МБ ради данных, 95 % которых — повторяющаяся метаинформация
о сервисах. Здесь из него вынимается ТО, что меняется: статус проверки для
каждой связки «регион × сервис × оператор × цель».

Формат длинный (одна строка = одно измерение), потому что его одинаково удобно
читать и pandas, и обычным grep, и он не ломается при добавлении нового
оператора или цели.

    python scripts/dataset/build.py --out <каталог>
"""
import argparse, csv, json, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

SRC = "https://freedomchecker.ateo.digital/api/snapshot.json"
FIELDS = ["date", "generated_at", "window_hours", "region", "kind",
          "service", "service_slug", "protocol", "provider", "provider_type",
          "target", "status"]


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ateo-dataset/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def rows(snap: dict):
    gen = snap.get("generated_at", "")
    day = gen[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    win = snap.get("hours_window")
    for region, rdata in (snap.get("regions") or {}).items():
        dash = rdata.get("dashboard") or {}
        # direct — базовая линия «без туннеля», без неё цифры VPN не с чем
        # сравнивать. Форма у него ДРУГАЯ: записи сразу пооператорные, без
        # вложенного by_provider. Обход по общей ветке молча давал ноль строк
        # при полностью успешной сборке — то есть документация обещала базовую
        # линию, которой в файле не было.
        for prov in (dash.get("direct") or []):
            for target, status in (prov.get("targets") or {}).items():
                yield {
                    "date": day, "generated_at": gen, "window_hours": win,
                    "region": region, "kind": "direct", "service": "direct",
                    "service_slug": "", "protocol": "",
                    "provider": prov.get("provider_name") or "—",
                    "provider_type": prov.get("provider_type") or "",
                    "target": target, "status": status,
                }
        for kind, bucket in (("vpn", dash.get("vpn_services") or []),):
            for svc in bucket:
                # group_name — так поле называется в снапшоте; slug полезен
                # тем, кто захочет связать строку с публичной страницей обзора.
                name = svc.get("group_name") or "—"
                slug = svc.get("seo_slug") or ""
                proto = svc.get("protocol_type") or ""
                for prov in (svc.get("by_provider") or []):
                    pname = prov.get("provider_name") or "—"
                    ptype = prov.get("provider_type") or ""
                    for target, status in (prov.get("targets") or {}).items():
                        yield {
                            "date": day, "generated_at": gen, "window_hours": win,
                            "region": region, "kind": kind, "service": name,
                            "service_slug": slug, "protocol": proto,
                            "provider": pname, "provider_type": ptype,
                            "target": target, "status": status,
                        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="корень репозитория с датасетом")
    ap.add_argument("--src", default=SRC)
    a = ap.parse_args()

    snap = fetch(a.src)
    data = list(rows(snap))
    if not data:
        print("снапшот не дал ни одной строки — ничего не пишу", file=sys.stderr)
        return 2
    day = data[0]["date"]

    out = Path(a.out)
    (out / "data" / "daily").mkdir(parents=True, exist_ok=True)
    for path in (out / "data" / "daily" / f"{day}.csv", out / "data" / "latest.csv"):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(data)

    # Сводка одним файлом: доля ok по каждому оператору и региону. Это то, что
    # читают глазами, не открывая CSV на тысячу строк.
    summary = {}
    for r in data:
        if r["kind"] != "vpn":
            continue
        k = (r["region"], r["provider"])
        s = summary.setdefault(k, {"ok": 0, "total": 0})
        s["total"] += 1
        if r["status"] == "ok":
            s["ok"] += 1
    with open(out / "data" / "summary-latest.json", "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": snap.get("generated_at"),
            "window_hours": snap.get("hours_window"),
            "measurements": len(data),
            "by_region_provider": [
                {"region": reg, "provider": prov, "ok": v["ok"], "total": v["total"],
                 "ok_share": round(v["ok"] / v["total"], 4) if v["total"] else None}
                for (reg, prov), v in sorted(summary.items())
            ],
        }, f, ensure_ascii=False, indent=1)

    print(f"дата {day} · измерений {len(data)} · регионов "
          f"{len({r['region'] for r in data})} · сервисов "
          f"{len({r['service'] for r in data})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
