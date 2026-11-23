"""Backfill photos uploaded before ADR-0033 so they get analysis copies and thumbnails.

Media items with no `processing_status` live under the old flat key layout
`{garden_id}/{media_id}/{file}`, which the Ingestion Lambda's role cannot read (it only reads
`uploads/*`). For each one this script:

1. copies the original to `uploads/{garden_id}/{media_id}/{file}` — the copy fires the normal S3
   trigger, so ingestion runs through exactly the same path as a new upload;
2. updates the Media item: `s3_key` -> the new key, `legacy_s3_key` -> the old key,
   `processing_status` -> `pending`.

Non-destructive: the old object is left in place. Idempotent: items that already have a
`processing_status` are skipped. Dry run by default; pass `--apply` to make changes.

Runs with your own AWS credentials (not CI). Resource names come from `--env` (never hardcoded):

    python scripts/backfill_media.py --env dev                  # dry run, every garden
    python scripts/backfill_media.py --env dev --garden <id>    # dry run, one garden
    python scripts/backfill_media.py --env dev --apply          # do it
"""

from __future__ import annotations

import argparse
import sys

import boto3
from boto3.dynamodb.conditions import Attr, Key

UPLOADS_PREFIX = "uploads/"


def legacy_media(table, garden_id: str | None) -> list[dict]:
    """Media items without `processing_status` (one garden via Query, else a filtered Scan)."""
    items: list[dict] = []
    if garden_id:
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with("MEDIA#"),
            "FilterExpression": Attr("processing_status").not_exists(),
        }
        op = table.query
    else:
        kwargs = {
            "FilterExpression": Attr("sk").begins_with("MEDIA#")
            & Attr("processing_status").not_exists()
        }
        op = table.scan
    while True:
        resp = op(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def new_key(item: dict) -> str | None:
    old = item["s3_key"]
    if old.startswith(UPLOADS_PREFIX):
        return None  # already in the new layout; the trigger would have handled it
    file_name = old.rsplit("/", 1)[-1]
    return f"{UPLOADS_PREFIX}{item['garden_id']}/{item['media_id']}/{file_name}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", required=True, choices=["dev", "prod"])
    parser.add_argument("--garden", help="limit to one garden id")
    parser.add_argument("--region", default=None, help="defaults to your AWS config")
    parser.add_argument("--apply", action="store_true", help="make changes (default: dry run)")
    args = parser.parse_args(argv)

    session = boto3.session.Session(region_name=args.region)
    table = session.resource("dynamodb").Table(f"tendril-{args.env}-app")
    s3 = session.client("s3")
    bucket = f"tendril-{args.env}-media"

    items = legacy_media(table, args.garden)
    print(f"{len(items)} media item(s) without processing_status in {table.name}")
    done = 0
    for item in items:
        target = new_key(item)
        if target is None:
            print(f"  skip {item['media_id']}: already under uploads/")
            continue
        print(f"  {'copy' if args.apply else 'would copy'} {item['s3_key']} -> {target}")
        if not args.apply:
            continue
        s3.copy_object(
            Bucket=bucket,
            Key=target,
            CopySource={"Bucket": bucket, "Key": item["s3_key"]},
            MetadataDirective="COPY",
        )
        table.update_item(
            Key={"pk": item["pk"], "sk": item["sk"]},
            UpdateExpression=(
                "SET s3_key = :new, legacy_s3_key = :old, processing_status = :pending"
            ),
            ConditionExpression="attribute_not_exists(processing_status)",
            ExpressionAttributeValues={
                ":new": target,
                ":old": item["s3_key"],
                ":pending": "pending",
            },
        )
        done += 1
    if args.apply:
        print(f"Backfilled {done} item(s); ingestion runs on each copy within seconds.")
    else:
        print("Dry run only. Re-run with --apply to make these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
