"""CP-04: migrate the owner's coconut archive into Tendril (one garden, 105 palms, dated history).

Source (on the owner's machine, never in the repo):

    <archive>/<NN>/YYYY-MM-DD[_HHMM]_plant<NN>.jpg   photos, folder = human-verified palm number
    <archive>/<NN>/log.md                             dated notes: "### 19 Oct 2026  [healthy]"
    <archive>/_SUMMARY.md                             "## Inputs applied" -> garden events
    (<archive>/raw/ is skipped: unsorted originals, the mapping isn't verified)

What it writes, all idempotent (deterministic ids + conditional puts, so re-runs never duplicate):

1. **Garden:** the owner's garden named `--name` (found via the `USER#` index, else created with the
   farm details from `--details`, a JSON snapshot of a garden's METADATA plus `plant_template`).
2. **Plants:** palms 1..stake_range.max, with the `STAKE#` lookup (ADR-0032); existing ones are kept.
3. **Photos:** each one is uploaded through the normal path (Media item first, then the S3 object
   under `uploads/`, so the Ingestion Lambda makes the analysis copy and thumbnail, ADR-0033). It
   is filed to its folder's palm.
4. **History (ADR-0035, `source: migration`):** every photo and every log.md note, dated from the
   filename or heading. A note that names its photo carries it, so the photo isn't shown twice.
   Each palm's count and latest date, photo and status are updated, only moving forward in time.
5. **Garden events:** the inputs log (e.g. "23 Oct: humic acid drench via drip") as `input.applied`.

Not done here: the knowledge-base seed (a separate, later step).

Dry run by default; `--apply` writes; `--verify` compares each palm's history with the archive.
Runs with your own AWS credentials (not CI); resource names come from `--env`:

    python scripts/migrate_coconut_archive.py --env dev --archive "<Coconut Tracking>" \\
        --owner <user id> --name "Coconut Plot" --details details.json [--apply | --verify]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "api"))
import plant_history as ph  # noqa: E402  (the Client API's own entry rules)

# Fixed namespace: the same archive file always gets the same ids, so re-runs are no-ops.
ID_NAMESPACE = uuid.UUID("6f1c2b9e-5a43-4c1e-9d0b-6a7e2f3c8b14")
PHOTO_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:_(\d{2})(\d{2}))?_plant(\d+)\.(jpe?g|png)$", re.I)
LOG_HEADING = re.compile(r"^###\s+(\d{1,2}\s+\w{3}\s+\d{4})\s*\[(\w+)\]\s*$")
LOG_PHOTO = re.compile(r"Photo:\s*`([^`]+)`")
INPUT_LINE = re.compile(r"^-\s*(\d{1,2}\s+\w{3}):\s*(.+?)\.?\s*$")
LAST_UPDATED = re.compile(r"Last updated:\s*\d{1,2}\s+\w{3}\s+(\d{4})")


# --- parsing (pure) ------------------------------------------------------------------------------


@dataclass
class Photo:
    stake: int
    path: Path
    observed_on: str
    observed_at: str | None


@dataclass
class Note:
    stake: int
    index: int
    observed_on: str
    status: str | None
    text: str
    photo_file: str | None


@dataclass
class Palm:
    stake: int
    photos: list[Photo] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


def parse_photo_name(name: str) -> tuple[int, str, str | None] | None:
    """`2026-10-23_0839_plant100.jpg` -> (100, "2026-10-23", "2026-10-23T08:39:00")."""
    m = PHOTO_NAME.match(name)
    if not m:
        return None
    day, hh, mm, stake = m.group(1), m.group(2), m.group(3), int(m.group(4))
    return stake, day, (f"{day}T{hh}:{mm}:00" if hh else None)


def _date(text: str) -> str:
    return datetime.strptime(text.strip(), "%d %b %Y").date().isoformat()


def parse_log(stake: int, text: str) -> list[Note]:
    """The dated entries of a palm's log.md: heading `### 19 Oct 2026  [healthy]`, then the note."""
    notes: list[Note] = []
    current: dict[str, Any] | None = None

    def close() -> None:
        if current is None:
            return
        body = " ".join(line.strip() for line in current["lines"] if line.strip())
        photo = LOG_PHOTO.search(body)
        body = LOG_PHOTO.sub("", body).strip()
        status = current["status"] if current["status"] in ph.STATUSES else None
        notes.append(
            Note(
                stake=stake,
                index=len(notes),
                observed_on=current["date"],
                status=status,
                text=body[: ph.NOTE_MAX_CHARS],
                photo_file=photo.group(1) if photo else None,
            )
        )

    for line in text.splitlines():
        heading = LOG_HEADING.match(line.strip())
        if heading:
            close()
            current = {
                "date": _date(heading.group(1)),
                "status": heading.group(2).lower(),
                "lines": [],
            }
        elif line.startswith("#"):
            close()
            current = None
        elif current is not None:
            current["lines"].append(line)
    close()
    return notes


def parse_inputs(summary: str) -> list[tuple[str, str]]:
    """`## Inputs applied` bullets -> [(YYYY-MM-DD, "humic acid drench via drip")]. The year comes
    from the summary's "Last updated" line."""
    year_match = LAST_UPDATED.search(summary)
    if not year_match:
        return []
    year = year_match.group(1)
    inputs, in_section = [], False
    for line in summary.splitlines():
        if line.startswith("## "):
            in_section = line.strip().lower() == "## inputs applied"
            continue
        m = INPUT_LINE.match(line.strip()) if in_section else None
        if m:
            inputs.append((_date(f"{m.group(1)} {year}"), m.group(2)))
    return inputs


def read_archive(root: Path) -> tuple[dict[int, Palm], list[tuple[str, str]]]:
    palms: dict[int, Palm] = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and p.name.isdigit()):
        stake = int(folder.name)
        palm = palms.setdefault(stake, Palm(stake))
        for f in sorted(folder.iterdir()):
            parsed = parse_photo_name(f.name)
            if parsed and parsed[0] == stake:
                palm.photos.append(Photo(stake, f, parsed[1], parsed[2]))
            elif f.name.lower() == "log.md":
                palm.notes = parse_log(stake, f.read_text(encoding="utf-8"))
    summary = root / "_SUMMARY.md"
    inputs = parse_inputs(summary.read_text(encoding="utf-8")) if summary.exists() else []
    return palms, inputs


def stable_id(*parts: object) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, "|".join(str(p) for p in parts)))


def content_type(data: bytes) -> str:
    """Declared from the bytes (86 of the archive's .jpg files are really PNG); ingestion still
    records the real format itself."""
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def history_entries(
    garden_id: str, palm: Palm, plant_id: str, media_ids: dict[str, str]
) -> list[dict[str, Any]]:
    """The palm's entries: each note (carrying its named photo) and each remaining photo."""
    entries = []
    carried: set[str] = set()
    for note in palm.notes:
        media = media_ids.get(note.photo_file or "")
        if media:
            carried.add(note.photo_file)
        entries.append(
            ph.new_entry(
                garden_id,
                plant_id,
                kind="log",
                source="migration",
                observed_on=note.observed_on,
                entry_id=stable_id("log", palm.stake, note.observed_on, note.index),
                media_ids=[media] if media else None,
                status=note.status,
                note=note.text or None,
            )
        )
    for photo in palm.photos:
        if photo.path.name in carried:
            continue
        entries.append(
            ph.new_entry(
                garden_id,
                plant_id,
                kind="photo",
                source="migration",
                observed_on=photo.observed_on,
                observed_at=photo.observed_at,
                entry_id=media_ids[photo.path.name],
                media_ids=[media_ids[photo.path.name]],
            )
        )
    return entries


# --- AWS writes ----------------------------------------------------------------------------------


class Migration:
    def __init__(self, session, env: str, apply: bool):
        self.table = session.resource("dynamodb").Table(f"tendril-{env}-app")
        self.s3 = session.client("s3")
        self.bucket = f"tendril-{env}-media"
        self.apply = apply
        self.counts: dict[str, int] = defaultdict(int)

    def _conditional_put(self, item: dict[str, Any]) -> bool:
        if not self.apply:
            return True
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def garden(self, owner: str, name: str, details: dict[str, Any]) -> str:
        owned = self.table.query(
            KeyConditionExpression=Key("pk").eq(f"USER#{owner}") & Key("sk").begins_with("GARDEN#")
        ).get("Items", [])
        for item in owned:
            if item.get("name") == name:
                print(f"Garden '{name}' exists: {item['garden_id']}")
                return item["garden_id"]
        garden_id = str(uuid.uuid4())
        metadata = {
            "pk": f"GARDEN#{garden_id}",
            "sk": "METADATA",
            "garden_id": garden_id,
            "name": name,
            "geolocation": details["geolocation"],
            "owner_user_id": owner,
            "created_at": datetime.now(UTC).isoformat(),
        }
        for k in ("vision", "climate_zone", "context"):
            if details.get(k):
                metadata[k] = details[k]
        print(f"{'Create' if self.apply else 'Would create'} garden '{name}': {garden_id}")
        if self.apply:
            client = self.table.meta.client
            client.transact_write_items(
                TransactItems=[
                    {"Put": {"TableName": self.table.name, "Item": metadata}},
                    {
                        "Put": {
                            "TableName": self.table.name,
                            "Item": {
                                "pk": f"USER#{owner}",
                                "sk": f"GARDEN#{garden_id}",
                                "garden_id": garden_id,
                                "name": name,
                            },
                        }
                    },
                ]
            )
        self.counts["garden"] += 1
        return garden_id

    def plant(self, garden_id: str, stake: int, template: dict[str, str]) -> str:
        lookup = self.table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"STAKE#{stake:05d}"}
        ).get("Item")
        if lookup:
            return lookup["plant_id"]
        plant_id = stable_id("plant", garden_id, stake)
        if self.apply:
            self.table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table.name,
                            "Item": {
                                "pk": f"GARDEN#{garden_id}",
                                "sk": f"PLANT#{plant_id}",
                                "plant_id": plant_id,
                                "garden_id": garden_id,
                                "species": template["species"],
                                "variety": template["variety"],
                                "stage": template["stage"],
                                "stake_no": stake,
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table.name,
                            "Item": {
                                "pk": f"GARDEN#{garden_id}",
                                "sk": f"STAKE#{stake:05d}",
                                "stake_no": stake,
                                "plant_id": plant_id,
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
        self.counts["plants"] += 1
        return plant_id

    def photo(self, garden_id: str, plant_id: str, photo: Photo) -> str:
        """Media item first, then the S3 object: ingestion only processes a photo whose Media
        item already exists (it never creates one)."""
        media_id = stable_id("media", garden_id, photo.stake, photo.path.name)
        data = photo.path.read_bytes()
        key = f"uploads/{garden_id}/{media_id}/{photo.path.name}"
        item = {
            "pk": f"GARDEN#{garden_id}",
            "sk": f"MEDIA#{media_id}",
            "media_id": media_id,
            "garden_id": garden_id,
            "s3_key": key,
            "content_type": content_type(data),
            "size_bytes": len(data),
            "processing_status": "pending",
            "uploaded_at": datetime.now(UTC).isoformat(),
            "plant_id": plant_id,
            "plant_link_source": "user",  # the archive folder is the owner's verified mapping
        }
        if self._conditional_put(item):
            if self.apply:
                self.s3.put_object(
                    Bucket=self.bucket, Key=key, Body=data, ContentType=item["content_type"]
                )
            self.counts["photos"] += 1
        return media_id

    def entry(self, entry: dict[str, Any]) -> None:
        if not self._conditional_put(entry):
            return
        self.counts["history"] += 1
        if not self.apply:
            return
        key = {"pk": entry["pk"], "sk": f"PLANT#{entry['plant_id']}"}
        self.table.update_item(
            Key=key,
            UpdateExpression="ADD history_count :d",
            ExpressionAttributeValues={":d": 1},
            ConditionExpression="attribute_exists(pk)",
        )
        facets = [("latest_observed_on", {})]
        if entry.get("media_ids"):
            facets.append(("latest_photo_on", {"media_id": entry["media_ids"][0]}))
        if entry.get("status"):
            facets.append(("latest_status_on", {"latest_status": entry["status"]}))
        for date_field, extra in facets:
            fields = {date_field: entry["observed_on"], **extra}
            try:
                self.table.update_item(
                    Key=key,
                    UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                    ExpressionAttributeNames={f"#{k}": k for k in fields},
                    ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
                    ConditionExpression=(
                        f"attribute_exists(pk) AND (attribute_not_exists(#{date_field}) "
                        f"OR #{date_field} <= :{date_field})"
                    ),
                )
            except self.table.meta.client.exceptions.ConditionalCheckFailedException:
                pass  # a newer observation is already there

    def input_event(self, garden_id: str, day: str, text: str) -> None:
        event_id = stable_id("input", garden_id, day, text)
        created_at = f"{day}T00:00:00+00:00"
        if self._conditional_put(
            {
                "pk": f"GARDEN#{garden_id}",
                "sk": f"EVENT#{created_at}#{event_id}",
                "event_id": event_id,
                "type": "input.applied",
                "payload": {"input": text, "source": "migration"},
                "created_at": created_at,
            }
        ):
            self.counts["events"] += 1


def verify(table, garden_id: str, palms: dict[int, Palm]) -> int:
    """Per palm: archive photos vs photos in history, and the latest note's status vs the plant's."""
    problems = 0
    for stake, palm in sorted(palms.items()):
        lookup = table.get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"STAKE#{stake:05d}"}).get(
            "Item"
        )
        if not lookup:
            print(f"  #{stake}: missing plant")
            problems += 1
            continue
        plant = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{lookup['plant_id']}"}
        ).get("Item", {})
        entries = table.query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(ph.prefix(lookup["plant_id"]))
        ).get("Items", [])
        shown = sum(len(e.get("media_ids") or []) for e in entries)
        latest_note = max(palm.notes, key=lambda n: (n.observed_on, n.index), default=None)
        expected_status = latest_note.status if latest_note else None
        if shown != len(palm.photos) or (plant.get("latest_status") or None) != expected_status:
            print(
                f"  #{stake}: photos {shown}/{len(palm.photos)}, "
                f"status {plant.get('latest_status')!r} vs {expected_status!r}"
            )
            problems += 1
    print(f"Verified {len(palms)} palms: {problems} mismatch(es).")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, choices=["dev", "prod"])
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--owner", required=True, help="owner user id (X-User-Id of the owner's app)")
    ap.add_argument("--name", required=True, help="garden name, e.g. 'Coconut Plot'")
    ap.add_argument("--details", required=True, type=Path, help="garden details JSON snapshot")
    ap.add_argument("--region", default=None)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--verify", action="store_true", help="compare the garden with the archive")
    args = ap.parse_args(argv)

    palms, inputs = read_archive(args.archive)
    details = json.loads(args.details.read_text(encoding="utf-8"))
    stake_max = int(((details.get("context") or {}).get("stake_range") or {}).get("max") or 0)
    stakes = range(1, max(stake_max, max(palms, default=0)) + 1)
    print(
        f"Archive: {len(palms)} palm folders, {sum(len(p.photos) for p in palms.values())} photos, "
        f"{sum(len(p.notes) for p in palms.values())} notes, {len(inputs)} input(s)"
    )

    session = boto3.session.Session(region_name=args.region)
    run = Migration(session, args.env, apply=args.apply and not args.verify)
    garden_id = run.garden(args.owner, args.name, details)
    if args.verify:
        return 1 if verify(run.table, garden_id, palms) else 0

    template = details["plant_template"]
    for stake in stakes:
        plant_id = run.plant(garden_id, stake, template)
        palm = palms.get(stake)
        if not palm:
            continue
        media_ids = {p.path.name: run.photo(garden_id, plant_id, p) for p in palm.photos}
        for entry in history_entries(garden_id, palm, plant_id, media_ids):
            run.entry(entry)
    for day, text in inputs:
        run.input_event(garden_id, day, text)

    verb = "Wrote" if args.apply else "Would write"
    print(f"{verb}: " + ", ".join(f"{v} {k}" for k, v in sorted(run.counts.items())) + ".")
    if not args.apply:
        print("Dry run only. Re-run with --apply to write.")
    else:
        print(f"Garden id: {garden_id}. Ingestion makes thumbnails within a minute; then --verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
