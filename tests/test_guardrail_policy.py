"""Static checks on the guardrail policy data (guardrails/hello_guardrail.json).

Bedrock enforces length limits that CDK's early change-set validation does NOT catch
(only the service does, at create time) — e.g. topic *definitions* max 200 chars. These
tests catch an over-length or malformed policy locally/in CI, before a failed deploy.
No AWS or CDK needed.
"""

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POLICY = REPO / "guardrails" / "hello_guardrail.json"

# Bedrock Guardrail field limits (chars).
MAX_DESCRIPTION = 200
MAX_TOPIC_DEFINITION = 200
MAX_TOPIC_EXAMPLE = 100
MAX_BLOCKED_MESSAGING = 500


@pytest.fixture(scope="module")
def policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def test_policy_file_exists_and_parses(policy):
    assert policy["name"]
    assert policy["blockedInputMessaging"]
    assert policy["blockedOutputsMessaging"]


def test_description_within_limit(policy):
    assert len(policy.get("description", "")) <= MAX_DESCRIPTION


def test_blocked_messaging_within_limit(policy):
    assert len(policy["blockedInputMessaging"]) <= MAX_BLOCKED_MESSAGING
    assert len(policy["blockedOutputsMessaging"]) <= MAX_BLOCKED_MESSAGING


def test_topic_definitions_and_examples_within_limits(policy):
    topics = policy["topicPolicy"]["topics"]
    assert topics, "expected at least one denied topic"
    for t in topics:
        assert len(t["definition"]) <= MAX_TOPIC_DEFINITION, (
            f"{t['name']} definition is {len(t['definition'])} > {MAX_TOPIC_DEFINITION}"
        )
        for ex in t.get("examples", []):
            assert len(ex) <= MAX_TOPIC_EXAMPLE, f"{t['name']} example too long: {ex!r}"


def test_expected_topics_present(policy):
    names = {t["name"] for t in policy["topicPolicy"]["topics"]}
    assert {"NonGardeningAdvice", "UnsafeChemicalUse"} <= names


def test_prompt_attack_filter_present(policy):
    types = {f["type"] for f in policy["contentPolicy"]["filters"]}
    assert "PROMPT_ATTACK" in types


def test_pii_entities_anonymized(policy):
    entities = policy["sensitiveInformationPolicy"]["piiEntities"]
    assert {e["type"] for e in entities} >= {"EMAIL", "PHONE", "NAME", "ADDRESS"}
    assert all(e["action"] == "ANONYMIZE" for e in entities)
