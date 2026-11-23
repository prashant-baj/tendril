"""Evaluate the plant_identifier's stake reading against the owner's archive (ADR-0034 §5).

Ground truth: the archive's per-plant folders (`<archive>/<NN>/…jpg`) are human-verified plant
numbers. For each photo this script:

1. makes the same analysis copy production uses (JPEG, long edge <= 2048 px, <= 1.5 MB, metadata
   stripped — ADR-0033), locally with Pillow;
2. uploads it under `eval/plant-identifier/<run-id>/` in the media bucket and presigns a GET;
3. invokes the deployed `plant_identifier` runtime exactly like the orchestrator does;
4. parses the reply with the orchestrator's own `plant_identity.parse_stake_read`.

Reports, overall and as CSV:
- **wrong_high_rate** — share of *high-confidence* reads whose number is wrong. Auto-link may be
  enabled (`AUTO_LINK_ENABLED=true`) only when this is **below 1%** (owner decision).
- high_coverage — share of photos read with high confidence.
- exact_accuracy — share of all photos read correctly (any confidence).
- miss_rate — share with no stake read at all.

Costs one model call per photo; runs with your own AWS credentials, never in CI. Resource names come
from `--env`; the runtime ARN is read from the `tendril-<env>-agentcore` stack outputs.

    python scripts/eval_plant_identifier.py --env dev --archive "<path to Coconut Tracking>" --limit 20
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "orchestrator"))
import plant_identity as pid  # noqa: E402  (the orchestrator's own parser — same rules as prod)

AUTO_LINK_TARGET = 0.01  # owner decision: wrong high-confidence reads below 1%
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
FOLDER_PATTERN = re.compile(r"^\d{1,5}$")


def archive_photos(root: Path) -> list[tuple[int, Path]]:
    """(true plant number, photo path) for every photo in a numbered folder. `raw/` and anything
    not in a numbered folder is skipped — only human-verified mappings count."""
    photos = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and FOLDER_PATTERN.match(p.name)):
        for photo in sorted(folder.iterdir()):
            if photo.suffix.lower() in IMAGE_EXTENSIONS:
                photos.append((int(folder.name), photo))
    return photos


def analysis_copy(path: Path) -> bytes:
    """The ADR-0033 analysis copy, made locally (same limits as the Image Tool)."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    for quality in (85, 80, 75, 70):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= 1_500_000:
            return buf.getvalue()
    return buf.getvalue()


RUNTIME_OUTPUT_KEY = (
    "Plant_identifierRuntimeArn"  # AgentCoreStack: f"{name.capitalize()}RuntimeArn"
)


def runtime_arn(cfn, env: str) -> str:
    outputs = cfn.describe_stacks(StackName=f"tendril-{env}-agentcore")["Stacks"][0]["Outputs"]
    for o in outputs:
        if o["OutputKey"] == RUNTIME_OUTPUT_KEY:
            return o["OutputValue"]
    raise SystemExit("plant_identifier runtime ARN not found — is ADR-0034 deployed?")


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    high = [r for r in rows if r["confidence"] == "high"]
    wrong_high = [r for r in high if r["read"] != r["truth"]]
    return {
        "photos": n,
        "exact_accuracy": sum(r["read"] == r["truth"] for r in rows) / n if n else 0.0,
        "high_coverage": len(high) / n if n else 0.0,
        "wrong_high": len(wrong_high),
        "wrong_high_rate": len(wrong_high) / len(high) if high else 0.0,
        "miss_rate": sum(r["read"] is None for r in rows) / n if n else 0.0,
        "auto_link_ready": bool(high) and len(wrong_high) / len(high) < AUTO_LINK_TARGET,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, choices=["dev"])
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=0, help="evaluate at most N photos (0 = all)")
    ap.add_argument("--region", default=None)
    ap.add_argument("--out", type=Path, default=None, help="CSV report path")
    args = ap.parse_args(argv)

    session = boto3.session.Session(region_name=args.region)
    s3, core = session.client("s3"), session.client("bedrock-agentcore")
    arn = runtime_arn(session.client("cloudformation"), args.env)
    bucket = f"tendril-{args.env}-media"
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    photos = archive_photos(args.archive)
    if args.limit:
        photos = photos[: args.limit]
    print(f"Evaluating {len(photos)} photo(s) against {arn.rsplit('/', 1)[-1]} (run {run_id})")

    rows = []
    for truth, path in photos:
        key = f"eval/plant-identifier/{run_id}/{truth}/{uuid.uuid4().hex}.jpg"
        s3.put_object(Bucket=bucket, Key=key, Body=analysis_copy(path), ContentType="image/jpeg")
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=600
        )
        payload = {
            "prompt": "Read the number on the plant's stake in this photo.",
            "gardenId": "eval",
            "imageUrl": url,
            "imageFormat": "jpeg",
        }
        try:
            resp = core.invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
                payload=json.dumps(payload).encode("utf-8"),
            )
            read = pid.parse_stake_read(json.loads(resp["response"].read()).get("result", ""))
        except Exception as e:  # count as a miss, keep going
            print(f"  {path.name}: error {e.__class__.__name__}")
            read = pid.NO_READ
        row = {
            "truth": truth,
            "read": read.stake_no,
            "confidence": read.confidence,
            "alternatives": " ".join(map(str, read.alternatives)),
            "file": f"{path.parent.name}/{path.name}",
        }
        rows.append(row)
        mark = "ok " if row["read"] == truth else "BAD" if read.confidence == "high" else "~  "
        print(f"  {mark} #{truth:<4} read={row['read']} ({read.confidence}) {row['file']}")

    summary = summarize(rows)
    out = args.out or Path(f"eval_plant_identifier_{run_id}.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["truth"])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, indent=2))
    print(f"Report: {out}")
    print(
        "Auto-link READY (wrong high-confidence reads < 1%)."
        if summary["auto_link_ready"]
        else "Auto-link NOT ready: keep AUTO_LINK_ENABLED=false (every read is confirmed)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
