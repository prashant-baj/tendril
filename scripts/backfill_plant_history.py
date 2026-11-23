"""Backfill plant history (ADR-0035) for photos filed under a plant before history existed.

Every Media item with a `plant_id` whose plant has no history entry showing it gets one:

- a `checkin` entry when the photo was a task check-in (`task_id`, the task's title as the note);
- otherwise a `photo` entry.

Entries are dated by the photo (`taken_at`, else `uploaded_at`), use the media id as `entry_id`
(the same id later writes use, so re-runs never duplicate), and keep the photo's own link source.
The plant's `history_count` and latest photo/date are updated, each only moving forward in time.

Dry run by default; `--apply` writes. Non-destructive: nothing is deleted or overwritten.
Runs with your own AWS credentials (not CI). Resource names come from `--env`:

    python scripts/backfill_plant_history.py --env dev                  # dry run, every garden
    python scripts/backfill_plant_history.py --env dev --garden <id>    # dry run, one garden
    python scripts/backfill_plant_history.py --env dev --apply          # do it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "api"))
import plant_history as ph  # noqa: E402  (the Client API's own rules — same entries as prod)

SOURCE_FOR_LINK = {"auto": "auto", "confirmed": "confirmed"}  # anything else: the gardener's


def _paged(op, **kwargs) -> list[dict]:
    items: list[dict] = []
    while True:
        resp = op(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def linked_media(table, garden_id: str | None) -> list[dict]:
    """Media items filed under a plant (one garden via Query, else a filtered Scan)."""
    if garden_id:
        return _paged(
            table.query,
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with("MEDIA#"),
            FilterExpression=Attr("plant_id").exists(),
        )
    return _paged(
        table.scan, FilterExpression=Attr("sk").begins_with("MEDIA#") & Attr("plant_id").exists()
    )


def garden_of(item: dict) -> str:
    return item["pk"].removeprefix("GARDEN#")


def plan_entry(table, media: dict) -> tuple[dict[str, Any] | None, str]:
    """The entry to write for one photo, or (None, reason) when it needs none."""
    garden_id, plant_id, media_id = garden_of(media), media["plant_id"], media["media_id"]
    if not table.get_item(Key={"pk": media["pk"], "sk": f"PLANT#{plant_id}"}).get("Item"):
        return None, "plant no longer exists"
    history = _paged(
        table.query,
        KeyConditionExpression=Key("pk").eq(media["pk"])
        & Key("sk").begins_with(ph.prefix(plant_id)),
    )
    if ph.entries_with_media(history, media_id):
        return None, "already in the history"
    if any(media_id in ph.retracted(e) for e in history):
        return None, "moved away from this plant before"
    task = None
    if media.get("task_id") and media.get("goal_id"):
        task = table.get_item(
            Key={"pk": media["pk"], "sk": f"TASK#{media['goal_id']}#{media['task_id']}"}
        ).get("Item")
    done_checkin = bool(task) and task.get("status") == "done"
    entry = ph.new_entry(
        garden_id,
        plant_id,
        kind="checkin" if done_checkin else "photo",
        source=SOURCE_FOR_LINK.get(media.get("plant_link_source") or "", "user"),
        observed_on=ph.observed_on_for_media(media),
        observed_at=ph.observed_at_for_media(media),
        entry_id=media_id,
        media_ids=[media_id],
        note=(task or {}).get("title") if done_checkin else None,
        goal_id=media.get("goal_id"),
        task_id=media.get("task_id") if done_checkin else None,
    )
    return entry, f"{entry['kind']} on {entry['observed_on']}"


def write(table, entry: dict[str, Any]) -> bool:
    try:
        table.put_item(Item=entry, ConditionExpression="attribute_not_exists(pk)")
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    key = {"pk": entry["pk"], "sk": f"PLANT#{entry['plant_id']}"}
    table.update_item(
        Key=key,
        UpdateExpression="ADD history_count :d",
        ExpressionAttributeValues={":d": 1},
        ConditionExpression="attribute_exists(pk)",
    )
    for expression, field in (
        ("SET latest_observed_on = :o", "latest_observed_on"),
        ("SET media_id = :m, latest_photo_on = :o", "latest_photo_on"),
    ):
        values = {":o": entry["observed_on"]}
        if ":m" in expression:
            values[":m"] = entry["media_ids"][0]
        try:
            table.update_item(
                Key=key,
                UpdateExpression=expression,
                ExpressionAttributeValues=values,
                ConditionExpression=(
                    f"attribute_exists(pk) AND (attribute_not_exists({field}) OR {field} <= :o)"
                ),
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # a newer photo/date is already there
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", required=True, choices=["dev", "prod"])
    parser.add_argument("--garden", help="limit to one garden id")
    parser.add_argument("--region", default=None, help="defaults to your AWS config")
    parser.add_argument("--apply", action="store_true", help="make changes (default: dry run)")
    args = parser.parse_args(argv)

    session = boto3.session.Session(region_name=args.region)
    table = session.resource("dynamodb").Table(f"tendril-{args.env}-app")

    media = linked_media(table, args.garden)
    print(f"{len(media)} photo(s) filed under a plant in {table.name}")
    written = 0
    for item in media:
        entry, reason = plan_entry(table, item)
        label = f"{garden_of(item)[:8]}... plant {item['plant_id'][:8]}... photo {item['media_id'][:8]}..."
        if entry is None:
            print(f"  skip {label}: {reason}")
            continue
        print(f"  {'add' if args.apply else 'would add'} {label}: {reason}")
        if args.apply and write(table, entry):
            written += 1
    if args.apply:
        print(f"Added {written} history entr{'y' if written == 1 else 'ies'}.")
    else:
        print("Dry run only. Re-run with --apply to make these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
