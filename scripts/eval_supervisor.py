"""Evaluate the Supervisor (ADR-0039 §6) on scenarios from the owner's practice, against the live
model, with the same prompt-building code and structured outputs as production.

Each scenario builds the garden summary (`supervisor_rollup`) from fixture data, so no garden is
touched. 'assess' scenarios score the verdict (and that a conflict is named, when expected);
'agenda' scenarios score the daily review's suggestion. The scheduling (agenda) scenarios are the
gate for `supervisor_proposals_enabled` (CDK context).

Specialists are not consulted here (the eval measures the Supervisor's own judgement). Costs one or
two model calls per scenario; runs with your own AWS credentials, never in CI:

    python scripts/eval_supervisor.py --model global.amazon.nova-2-lite-v1:0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "supervisor"))
SCENARIOS = ROOT / "tests" / "eval" / "supervisor" / "scenarios.json"


def score_assessment(scenario: dict, out) -> tuple[bool, str]:
    if out is None:
        return False, "no structured output"
    if out.verdict not in scenario["verdicts"]:
        return False, f"verdict {out.verdict}, expected {scenario['verdicts']}"
    if scenario.get("expect_conflict") and not out.conflicts:
        return False, "expected a named conflict"
    return True, out.verdict


def score_agenda(scenario: dict, out) -> tuple[bool, str]:
    if out is None:
        return False, "no structured output"
    expected = scenario.get("expect_suggestion")
    if expected is None:
        return (not out.suggestions), f"{len(out.suggestions)} suggestion(s)"
    hit = any(
        s.type == expected["type"] and s.activity_id == expected["activity_id"]
        for s in out.suggestions
    )
    return hit, ", ".join(f"{s.type}:{s.activity_id}" for s in out.suggestions) or "none"


def plants(counts: dict) -> list[dict]:
    out, n = [], 1
    for status, k in counts.items():
        for _ in range(k):
            out.append({"plant_id": f"p{n}", "stake_no": n, "latest_status": status})
            n += 1
    return out


def summary(data: dict, scenario: dict, rollup) -> str:
    return (
        rollup.build_rollup(
            metadata=data["garden"],
            plants=plants(data["plants"]),
            notes_by_plant={},
            logs=[{"targets": "all", **r} for r in scenario.get("recent", [])],
            activities=[{"targets": "all", **a} for a in scenario.get("activities", [])],
            forecast=scenario.get("forecast"),
            today=data["today"],
        )
        + "\n\nWatch list notes:\n"
        + "\n".join(data.get("watch_notes", []))
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=None, help="Bedrock model id (default: the SDK default)")
    ap.add_argument("--only", default=None, help="run one scenario id")
    args = ap.parse_args(argv)
    if args.model:
        os.environ["MODEL_ID"] = args.model
    os.environ.setdefault("AGENT_MANIFEST", "{}")  # no specialists in the eval
    import supervisor as sup  # noqa: E402  (after env is set)
    import supervisor_rollup as rollup  # noqa: E402

    # The deployed prompt text (prompts/supervisor-system.md), not the built-in fallback.
    prompt_text = (ROOT / "prompts" / "supervisor-system.md").read_text(encoding="utf-8")
    sup.load_system_prompt = lambda: prompt_text
    data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    results = []
    for sc in data["scenarios"]:
        if args.only and sc["id"] != args.only:
            continue
        text = summary(data, sc, rollup)
        if sc["kind"] == "assess":
            item = {"targets": "all", **sc["activity"]}
            prompt = (
                f"{text}\n\n---\n\n{rollup.describe(item, logged=sc['logged'], today=data['today'])}\n\n"
                "Assess this work for the farm: its benefits, and any issues or harm (timing "
                "against recent work, interactions, dose, soil, weather, plants on the watch list)."
            )
            out = sup.run_structured(prompt, sup.AssessmentOutput, garden_id="eval")
            ok, detail = score_assessment(sc, out)
        else:
            prompt = (
                f"{text}\n\n---\n\nReview today's farm work. Suggest moving, pausing, resuming or "
                "re-spacing a planned activity only where it clearly helps (rain forecast before a "
                "spray or drench, overdue work, work too close to a recent input). Use activity ids "
                "from the summary."
            )
            out = sup.run_structured(prompt, sup.AgendaOutput, garden_id="eval")
            ok, detail = score_agenda(sc, out)
        results.append((sc, ok))
        print(f"  {'PASS' if ok else 'FAIL'} {sc['id']}: {detail}")

    assess = [ok for sc, ok in results if sc["kind"] == "assess"]
    agenda = [ok for sc, ok in results if sc["kind"] == "agenda"]
    print(
        f"Assessments: {sum(assess)}/{len(assess)} passed. Agenda: {sum(agenda)}/{len(agenda)} passed."
    )
    if agenda and all(agenda):
        print("Daily proposals READY: set CDK context supervisor_proposals_enabled=true.")
    else:
        print("Daily proposals NOT ready: keep supervisor_proposals_enabled off.")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
