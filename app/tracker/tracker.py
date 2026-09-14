"""Tracker/Scheduler Lambda (Phase 7.5+) — polls `TasksDueIndex` for pending tasks whose
follow-up is due and emits one `followup.due` event per hit for the orchestrator to act on.

Triggered on a fixed schedule (EventBridge Scheduler, `AgentCoreStack`), not by an application
event — this is the one Lambda in the system that wakes itself up rather than reacting to
something a user or another service did. Deliberately dumb: a plain DynamoDB query + event-emit,
no `strands-agents`, no model calls, no domain logic beyond "is this task overdue" — the
orchestrator's own `handle_followup_due` (`app/orchestrator/orchestrator.py`) decides what to do
about it.

`gardenId`/`goalId`/`taskId` are parsed off each hit's own `pk`/`sk`
(`GARDEN#{garden_id}` / `TASK#{goal_id}#{task_id}`, data-architecture.md §2) rather than requiring
extra projected attributes — the GSI's `ALL` projection already includes the full item.

Known, accepted MVP gap: this Lambda does not itself bump a task's `gsi1sk` forward after
emitting an event for it — that happens in the orchestrator's `handle_followup_due`, tied to
actually having nudged the gardener. EventBridge Scheduler's at-least-once semantics mean a
double-invocation before that bump lands could double-fire the same task; low severity (one extra
chat message) and a narrow window at this schedule's cadence — not fixed here, see
`docs/stories/tracker-scheduler.md`'s "Carried forward" section.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# force=True: same Lambda-pre-attached-root-logger fix as orchestrator.py/garden_handler.py —
# without it, logger.info() calls here are silently dropped.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
logger = logging.getLogger("tendril.tracker")

APP_TABLE_NAME = os.getenv("APP_TABLE_NAME")  # injected by AgentCoreStack; never hardcoded

TASK_STATUS_PENDING_GSI1PK = "TASK_STATUS#pending"
# Must match infra/stacks/agentcore_stack.py's FollowupDueRule event pattern.
FOLLOWUP_DUE_EVENT_SOURCE = "tendril.tracker"
FOLLOWUP_DUE_DETAIL_TYPE = "followup.due"

_table = None
_events = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(APP_TABLE_NAME)
    return _table


def _get_events():
    global _events
    if _events is None:
        _events = boto3.client("events")
    return _events


def _query_due_tasks(now_iso: str) -> list[dict[str, Any]]:
    """Every pending task whose follow-up is due at or before `now_iso`. Paginated — a truncated
    page would silently mean an overdue task never gets nudged, a real correctness bug, not an
    edge case to skip."""
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "IndexName": "TasksDueIndex",
        "KeyConditionExpression": Key("gsi1pk").eq(TASK_STATUS_PENDING_GSI1PK)
        & Key("gsi1sk").lte(now_iso),
    }
    while True:
        resp = _get_table().query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return items


def _parse_task_ids(item: dict[str, Any]) -> tuple[str, str, str] | None:
    """(garden_id, goal_id, task_id) from a Task item's own pk/sk, or None if either doesn't
    match the expected shape (defensive — should never happen for a real Task item)."""
    pk = item.get("pk", "")
    sk = item.get("sk", "")
    if not pk.startswith("GARDEN#") or not sk.startswith("TASK#"):
        return None
    garden_id = pk[len("GARDEN#") :]
    rest = sk[len("TASK#") :]
    parts = rest.split("#", 1)
    if len(parts) != 2:
        return None
    goal_id, task_id = parts
    return garden_id, goal_id, task_id


def handler(event: dict[str, Any], _context: Any) -> None:
    now = datetime.now(UTC).isoformat()
    due_tasks = _query_due_tasks(now)
    logger.info("tracker_tick due_count=%d", len(due_tasks))

    for item in due_tasks:
        ids = _parse_task_ids(item)
        if ids is None:
            logger.warning(
                "tracker_skipped_unparseable_item pk=%r sk=%r", item.get("pk"), item.get("sk")
            )
            continue
        garden_id, goal_id, task_id = ids
        try:
            _get_events().put_events(
                Entries=[
                    {
                        "Source": FOLLOWUP_DUE_EVENT_SOURCE,
                        "DetailType": FOLLOWUP_DUE_DETAIL_TYPE,
                        "Detail": json.dumps(
                            {"gardenId": garden_id, "goalId": goal_id, "taskId": task_id}
                        ),
                    }
                ]
            )
        except Exception:
            # One bad item must never abort the whole tick — the rest still get their nudge.
            logger.exception(
                "tracker_publish_failed garden_id=%s goal_id=%s task_id=%s",
                garden_id,
                goal_id,
                task_id,
            )
            continue
