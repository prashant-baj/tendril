"""Seed a garden's known farm work (ADR-0038) from the owner's records, idempotently.

Writes, with stable ids (re-runs never duplicate):

- the **humic acid drench** as a recurring activity (every 21-28 days, owner-confirmed);
- its log of **23 Oct 2026** (the archive's inputs log; its Activity event already came from CP-04);
- the **15 Nov 2026 foliar spray** log: neem oil 40 ml + Dhartivita 10 g + sticker 5 ml, 2 pumps
  (owner-confirmed), plus its Activity event.

`--assess` then publishes `activity.planned` / `activity.logged` so the Supervisor (ADR-0039)
assesses them. Use it after the Supervisor is deployed.

Dry run by default; `--apply` writes. Runs with your own AWS credentials (not CI):

    python scripts/seed_farm_work.py --env dev --garden <id>                     # dry run
    python scripts/seed_farm_work.py --env dev --garden <id> --apply --assess
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "api"))
import activities as act  # noqa: E402  (the Client API's own due-date rules)

NAMESPACE = uuid.UUID("2c5b1f0e-8d4a-4e3b-9a77-1f6d2e4c9b30")
EVENT_SOURCE = "tendril.client-api"

HUMIC = {
    "key": "humic",
    "title": "Humic acid drench",
    "kind": "input",
    "details": "Drench via drip, all basins.",
    "dose": "via drip",
    "schedule": {"every_days_min": 21, "every_days_max": 28},
}
LOGS = [
    {"key": "humic-2026-10-23", "activity": "humic", "done_on": "2026-10-23", "event": False},
    {
        "key": "spray-2026-11-15",
        "activity": None,
        "done_on": "2026-11-15",
        "title": "Foliar spray",
        "kind": "input",
        "product": "Neem oil 40 ml + Dhartivita 10 g + sticker 5 ml",
        "dose": "2 pumps",
        "event": True,
    },
]


def sid(garden_id: str, key: str) -> str:
    return uuid.uuid5(NAMESPACE, f"{garden_id}|{key}").hex


def activity_item(garden_id: str) -> dict[str, Any]:
    last_done = max(log["done_on"] for log in LOGS if log.get("activity") == "humic")
    next_due, overdue = act.due_window(HUMIC["schedule"], last_done, last_done)
    return {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"ACTIVITY#{sid(garden_id, HUMIC['key'])}",
        "activity_id": sid(garden_id, HUMIC["key"]),
        "title": HUMIC["title"],
        "kind": HUMIC["kind"],
        "details": HUMIC["details"],
        "dose": HUMIC["dose"],
        "targets": "all",
        "schedule": HUMIC["schedule"],
        "status": "active",
        "start_on": last_done,
        "last_done_on": last_done,
        "next_due_on": next_due,
        "overdue_on": overdue,
        "version": 1,
        "source": "user",
        "created_at": datetime.now(UTC).isoformat(),
    }


def log_items(garden_id: str) -> list[dict[str, Any]]:
    items = []
    for log in LOGS:
        base = HUMIC if log.get("activity") == "humic" else log
        log_id = sid(garden_id, log["key"])
        item = {
            "pk": f"GARDEN#{garden_id}",
            "sk": f"ACTLOG#{log['done_on']}#{log_id}",
            "log_id": log_id,
            "done_on": log["done_on"],
            "title": base["title"],
            "kind": base["kind"],
            "targets": "all",
            "created_at": datetime.now(UTC).isoformat(),
        }
        for k in ("details", "product", "dose"):
            if base.get(k):
                item[k] = base[k]
        if log.get("activity"):
            item["activity_id"] = sid(garden_id, HUMIC["key"])
        items.append(item)
    return items


def event_items(garden_id: str) -> list[dict[str, Any]]:
    out = []
    for log in LOGS:
        if not log["event"]:
            continue
        event_id = sid(garden_id, f"event-{log['key']}")
        created = f"{log['done_on']}T00:00:00+00:00"
        out.append(
            {
                "pk": f"GARDEN#{garden_id}",
                "sk": f"EVENT#{created}#{event_id}",
                "event_id": event_id,
                "type": "input.applied",
                "payload": {
                    "input": f"{log['title']}: {log['product']}, {log['dose']}",
                    "logId": sid(garden_id, log["key"]),
                    "doneOn": log["done_on"],
                },
                "created_at": created,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, choices=["dev", "prod"])
    ap.add_argument("--garden", required=True)
    ap.add_argument("--region", default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--assess", action="store_true", help="ask the Supervisor to assess them")
    args = ap.parse_args(argv)

    session = boto3.session.Session(region_name=args.region)
    table = session.resource("dynamodb").Table(f"tendril-{args.env}-app")
    if not table.get_item(Key={"pk": f"GARDEN#{args.garden}", "sk": "METADATA"}).get("Item"):
        print("No such garden.")
        return 1
    items = [activity_item(args.garden), *log_items(args.garden), *event_items(args.garden)]
    written = 0
    for item in items:
        label = f"{item['sk'].split('#')[0]} {item.get('title') or item.get('type')}"
        if not args.apply:
            print(f"  would write {label}")
            continue
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            written += 1
            print(f"  wrote {label}")
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            print(f"  exists {label}")
    if args.apply and args.assess:
        events = session.client("events")
        entries = [
            {
                "Source": EVENT_SOURCE,
                "DetailType": "activity.planned",
                "Detail": json.dumps(
                    {"gardenId": args.garden, "activityId": items[0]["activity_id"]}
                ),
            }
        ] + [
            {
                "Source": EVENT_SOURCE,
                "DetailType": "activity.logged",
                "Detail": json.dumps(
                    {"gardenId": args.garden, "logId": i["log_id"], "doneOn": i["done_on"]}
                ),
            }
            for i in items
            if i["sk"].startswith("ACTLOG#")
        ]
        events.put_events(Entries=entries)
        print(f"Asked the Supervisor to assess {len(entries)} item(s).")
    print(f"Wrote {written} item(s)." if args.apply else "Dry run only. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
