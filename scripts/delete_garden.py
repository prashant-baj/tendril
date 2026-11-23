"""Delete one garden completely from an environment (records + photos). Irreversible.

Deletes every item in the garden's partition (`GARDEN#{id}`: metadata, plants, stake lookups,
goals, plans, tasks, messages, proposals, history, media records, events), the owner's
`USER#{owner}/GARDEN#{id}` index item, and the garden's objects in the media bucket
(`uploads/{id}/`, `derived/{id}/` and the pre-ADR-0033 flat `{id}/` layout).

Dry run by default: lists what would go. `--apply` deletes. `--name` must match the garden's
name, as a guard against deleting the wrong id. Runs with your own AWS credentials (not CI):

    python scripts/delete_garden.py --env dev --garden <id> --name "Coconut Baug"            # dry run
    python scripts/delete_garden.py --env dev --garden <id> --name "Coconut Baug" --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

import boto3
from boto3.dynamodb.conditions import Key


def partition_keys(table, garden_id: str) -> list[dict[str, str]]:
    keys: list[dict[str, str]] = []
    kwargs = {
        "KeyConditionExpression": Key("pk").eq(f"GARDEN#{garden_id}"),
        "ProjectionExpression": "pk, sk",
    }
    while True:
        resp = table.query(**kwargs)
        keys.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return keys
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def object_keys(s3, bucket: str, garden_id: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in (f"uploads/{garden_id}/", f"derived/{garden_id}/", f"{garden_id}/"):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
    return keys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, choices=["dev", "prod"])
    ap.add_argument("--garden", required=True, help="garden id")
    ap.add_argument("--name", required=True, help="the garden's name (safety check)")
    ap.add_argument("--region", default=None)
    ap.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    args = ap.parse_args(argv)

    session = boto3.session.Session(region_name=args.region)
    table = session.resource("dynamodb").Table(f"tendril-{args.env}-app")
    s3 = session.client("s3")
    bucket = f"tendril-{args.env}-media"

    metadata = table.get_item(Key={"pk": f"GARDEN#{args.garden}", "sk": "METADATA"}).get("Item")
    if not metadata:
        print("No such garden.")
        return 1
    if metadata.get("name") != args.name:
        print(
            f"Refusing: garden {args.garden} is named {metadata.get('name')!r}, not {args.name!r}."
        )
        return 2

    keys = partition_keys(table, args.garden)
    objects = object_keys(s3, bucket, args.garden)
    owner = metadata.get("owner_user_id")
    kinds = Counter(k["sk"].split("#")[0] for k in keys)
    print(f"Garden {args.name!r} ({args.garden}), owner {owner}:")
    print("  records: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    print(f"  photos/derived files in {bucket}: {len(objects)}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to delete (irreversible).")
        return 0

    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key={"pk": key["pk"], "sk": key["sk"]})
        if owner:
            batch.delete_item(Key={"pk": f"USER#{owner}", "sk": f"GARDEN#{args.garden}"})
    for start in range(0, len(objects), 1000):
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in objects[start : start + 1000]], "Quiet": True},
        )
    print(f"Deleted {len(keys) + (1 if owner else 0)} records and {len(objects)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
