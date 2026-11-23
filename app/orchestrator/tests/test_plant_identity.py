"""Unit tests for the ADR-0034 identity policy (plant_identity.py) — pure logic, no AWS."""

import plant_identity as pid
import pytest

# Garden: stakes 1..105 exist, plant ids "p<n>"; #59 is missing (never created).
PLANTS = {n: f"p{n}" for n in range(1, 106) if n != 59}
lookup = PLANTS.get
RANGE = (1, 105)


def read(stake_no, confidence="high", alternatives=(), visible=True):
    return pid.StakeRead(
        stake_visible=visible,
        stake_no=stake_no,
        confidence=confidence,
        alternatives=tuple(alternatives),
    )


# --- parsing (fail safe) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '{"stake_visible": true, "stake_no": 74, "confidence": "high", "alternatives": []}',
            (True, 74, "high", ()),
        ),
        (
            '```json\n{"stake_visible": true, "stake_no": "#10", "confidence": "high"}\n```',
            (True, 10, "high", ()),
        ),
        (
            'Sure! {"stake_visible": true, "stake_no": 71, "confidence": "low", "alternatives": [17, "7", 71]}',
            (True, 71, "low", (17, 7)),
        ),
        # "high" but with alternatives is not unambiguous
        (
            '{"stake_visible": true, "stake_no": 8, "confidence": "high", "alternatives": [3]}',
            (True, 8, "low", (3,)),
        ),
        (
            '{"stake_visible": false, "stake_no": null, "confidence": "low"}',
            (False, None, "low", ()),
        ),
        (
            '{"stake_visible": true, "stake_no": null}',
            (False, None, "low", ()),
        ),  # visible but unreadable
        ('{"stake_visible": true, "stake_no": 0}', (False, None, "low", ())),
        (
            '{"stake_visible": true, "stake_no": true}',
            (False, None, "low", ()),
        ),  # bool is not a number
        ("I can't see any stake.", (False, None, "low", ())),
        ("{not json", (False, None, "low", ())),
        ("", (False, None, "low", ())),
        (None, (False, None, "low", ())),
    ],
)
def test_parse_stake_read_fails_safe(text, expected):
    r = pid.parse_stake_read(text)
    assert (r.stake_visible, r.stake_no, r.confidence, r.alternatives) == expected


def test_guardrail_blocked_message_parses_as_no_read():
    blocked = '{"stake_visible": false, "stake_no": null, "confidence": "low", "alternatives": [], "reason": "input blocked by guardrail"}'
    assert pid.parse_stake_read(blocked) == pid.StakeRead(reason="input blocked by guardrail")


# --- policy: a plant was chosen (goal or task) --------------------------------------------------


def test_match_when_read_agrees_with_the_chosen_plant():
    d = pid.decide(
        read(10), lookup, expected_plant_id="p10", expected_stake_no=10, stake_range=RANGE
    )
    assert d.kind == "match" and not d.conflicts


def test_the_dev_incident_10_on_1s_task_is_a_mismatch():
    # 2026-11-23: a #10 photo checked in on palm #1's task.
    d = pid.decide(read(10), lookup, expected_plant_id="p1", expected_stake_no=1, stake_range=RANGE)
    assert d.kind == "mismatch" and d.plant_id == "p10" and d.conflicts


def test_unsure_read_that_could_be_the_chosen_plant_is_not_a_conflict():
    d = pid.decide(read(71, "low", [7]), lookup, expected_plant_id="p7", expected_stake_no=7)
    assert d.kind == "match"


def test_confident_mismatch_is_never_auto_even_when_low_alternatives_differ():
    d = pid.decide(read(71, "low", [17]), lookup, expected_plant_id="p7", expected_stake_no=7)
    assert d.kind == "mismatch"


def test_chosen_plant_with_unknown_number_out_of_range():
    d = pid.decide(
        read(406), lookup, expected_plant_id="p40", expected_stake_no=40, stake_range=RANGE
    )
    assert d.kind == "out_of_range" and d.plant_id is None


def test_no_stake_visible_never_conflicts():
    d = pid.decide(pid.NO_READ, lookup, expected_plant_id="p1", expected_stake_no=1)
    assert d.kind == "no_stake" and not d.conflicts


# --- policy: no plant chosen ------------------------------------------------------------------


def test_confident_read_of_an_existing_plant():
    d = pid.decide(read(10), lookup, stake_range=RANGE)
    assert d.kind == "confident" and d.plant_id == "p10"


def test_unsure_read_asks_with_candidates_that_exist():
    d = pid.decide(read(71, "low", [17, 7]), lookup, stake_range=RANGE)
    assert d.kind == "ask"
    assert [(c.stake_no, c.plant_id) for c in d.candidates] == [(71, "p71"), (17, "p17"), (7, "p7")]


def test_missing_plant_inside_the_range():
    d = pid.decide(read(59), lookup, stake_range=RANGE)
    assert d.kind == "missing_plant" and d.plant_id is None


def test_number_outside_the_range_is_probably_a_misread():
    d = pid.decide(read(406), lookup, stake_range=RANGE)
    assert d.kind == "out_of_range"


def test_unsure_missing_number_whose_alternative_exists_just_asks():
    d = pid.decide(read(59, "low", [58]), lookup, stake_range=RANGE)
    assert d.kind == "ask"


def test_no_stake_range_means_unknown_numbers_are_missing_plants():
    d = pid.decide(read(406), lookup, stake_range=None)
    assert d.kind == "missing_plant"


def test_as_record_shape():
    r = read(10, "low", [16])
    assert r.as_record() == {
        "stake_visible": True,
        "stake_no": 10,
        "confidence": "low",
        "alternatives": [16],
        "reason": "",
    }
