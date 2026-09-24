"""Behavioral regressions for discourse revisions and guarded non-revisions."""

import pytest
from fastapi.testclient import TestClient
from ride_agent.api.app import create_app
from ride_agent.domain.claims import ClaimLedger

from tests.integration.test_ride_engine import create
from tests.regression.test_ride_memory import send


def ledger(first, second):
    return ClaimLedger([dict(role="rider", content=first), dict(role="rider", content=second)])


@pytest.mark.parametrize(
    "revision",
    [
        "Actually, that was wrong. It was not OAK-GATE; the sign read PINE-GATE.",
        "Replace OAK-GATE with PINE-GATE.",
        "Use PINE-GATE instead of OAK-GATE.",
        "It was not OAK-GATE but PINE-GATE.",
        "No, I meant PINE-GATE.",
        "Make that PINE-GATE.",
        "I meant PINE-GATE, not OAK-GATE.",
        "Please change it from OAK-GATE to PINE-GATE.",
        "Actually, the sign reads PINE-GATE.",
    ],
)
def test_informal_revision_is_grounded_in_original_rider_source(revision):
    value = ledger("The pickup sign said OAK-GATE.", revision)
    context = value.context("pickup sign")
    assert not context["requires_clarification"]
    assert "PINE-GATE" in context["account"] and "OAK-GATE" not in context["account"]
    assert any((f["value"] == "OAK-GATE" and (not f["active"]) for f in value.facts))
    assert all((r["source"] == 1 and r["old"] == "OAK-GATE" for r in value.revisions))


@pytest.mark.parametrize(
    "message",
    [
        "What if we replace OAK-GATE with PINE-GATE?",
        "The driver said replace OAK-GATE with PINE-GATE.",
        "Do not replace OAK-GATE with PINE-GATE.",
        "I don't want to replace OAK-GATE with PINE-GATE.",
        "I walked from OAK-GATE to PINE-GATE.",
    ],
)
def test_non_revision_does_not_change_active_claim(message):
    value = ledger("My entrance is OAK-GATE.", message)
    assert [(f["field"], f["value"]) for f in value.facts if f["active"]] == [("entrance", "OAK-GATE")]


def test_ambiguous_anaphora_does_not_pick_a_field_by_recency():
    value = ledger(
        "My entrance is OAK-GATE. My receipt code is RECEIPT-X.",
        "No, I meant PINE-GATE.",
    )
    assert value.context("review")["requires_clarification"]
    assert not value.revisions


def test_same_value_in_two_fields_needs_explicit_target():
    value = ledger(
        "My pickup code is SHARED. My receipt code is SHARED.",
        "Replace SHARED with DIFFERENT.",
    )
    assert value.context("codes")["requires_clarification"]
    assert not value.revisions


def test_contradictory_replacements_do_not_depend_on_pattern_order():
    value = ledger(
        "My entrance is OAK-GATE.",
        "Replace OAK-GATE with PINE-GATE. Replace OAK-GATE with ELM-GATE.",
    )
    assert value.context("entrance")["requires_clarification"]
    assert not value.revisions


def test_revising_one_detail_preserves_unrelated_fields():
    value = ledger(
        "My entrance is OAK-GATE. My receipt code is RECEIPT-X.",
        "Use PINE-GATE instead of OAK-GATE.",
    )
    context = value.context("entrance and receipt")
    assert "PINE-GATE" in context["account"] and "RECEIPT-X" in context["account"]
    assert "OAK-GATE" not in context["account"]


def test_currency_revision_does_not_restore_negated_amount():
    value = ledger("I dispute $6.50.", "I dispute $4.25 instead of $6.50.")
    assert [(f["field"], f["value"]) for f in value.facts if f["active"]] == [("disputed amount", "$4.25")]


def test_original_failed_informal_case_survives_compression_and_restart(tmp_path):
    path = tmp_path / "colloquial.sqlite3"
    with TestClient(create_app(path)) as client:
        case = create(client, text="The pickup sign said OAK-GATE.")
        case = send(
            client,
            case,
            "Actually, that was wrong. It was not OAK-GATE; the sign read PINE-GATE.",
        )
        for i in range(11):
            case = send(client, case, f"Keep reviewing evidence, update {i}.")
        case = send(
            client,
            case,
            "Review the driver pickup and fare payment after app error 500.",
        )
        assert "PINE-GATE" in case["engine_runs"][-1]["response"]
        assert "OAK-GATE" not in case["engine_runs"][-1]["response"]
        assert case["adjustment"] is None
    with TestClient(create_app(path)) as client:
        case = send(client, case, "Keep my current pickup sign attached for specialist review.")
        assert "PINE-GATE" in case["engine_runs"][-1]["response"]
        assert "OAK-GATE" not in case["engine_runs"][-1]["response"]


def test_revision_supports_quoted_multiword_values_not_only_identifiers():
    value = ledger(
        'The pickup sign read "North Lobby".',
        'Please use "South Entrance" instead of "North Lobby".',
    )
    active = [f for f in value.facts if f["active"]]
    assert [(f["field"], f["value"]) for f in active] == [("pickup sign", "South Entrance")]
    assert "North Lobby" not in value.context("pickup sign")["account"]


def test_short_field_alias_is_ambiguous_when_two_fields_match():
    value = ledger(
        "My pickup code is PICKUP-A. My receipt code is RECEIPT-B.",
        "Actually, my code is CHANGED-C.",
    )
    assert value.context("code")["requires_clarification"]
    assert not value.revisions


@pytest.mark.parametrize(
    "followup",
    [
        "Keep the correction attached for review.",
        "Ask for clarification if a correction is unresolved.",
        "What happened after the correction?",
        "Please review my updated information.",
    ],
)
def test_discussing_a_revision_does_not_start_a_new_revision(followup):
    value = ClaimLedger(
        [
            dict(role="rider", content="My entrance is OAK-GATE."),
            dict(role="rider", content="Use PINE-GATE instead of OAK-GATE."),
            dict(role="rider", content=followup),
        ]
    )
    assert not value.context("entrance")["requires_clarification"]
    assert "PINE-GATE" in value.context("entrance")["account"]


def test_multi_clause_replacement_chain_commits_only_final_active_value():
    value = ledger(
        "My entrance is OAK-GATE.",
        "Replace OAK-GATE with PINE-GATE. Then replace PINE-GATE with ELM-GATE.",
    )
    assert [(f["field"], f["value"]) for f in value.facts if f["active"]] == [("entrance", "ELM-GATE")]
    assert all((token not in value.context("entrance")["account"] for token in ("OAK-GATE", "PINE-GATE")))


def test_cycle_is_not_applied_partially():
    value = ledger(
        "My entrance is OAK-GATE.",
        "Replace OAK-GATE with PINE-GATE. Then replace PINE-GATE with OAK-GATE.",
    )
    assert value.context("entrance")["requires_clarification"]
    assert not value.revisions


def test_clarification_can_be_resolved_by_naming_the_field():
    value = ClaimLedger(
        [
            dict(
                role="rider",
                content="My entrance is OAK-GATE. My receipt code is RECEIPT-X.",
            ),
            dict(role="rider", content="I meant PINE-GATE."),
            dict(role="rider", content="My entrance is PINE-GATE."),
        ]
    )
    assert not value.context("entrance")["requires_clarification"]
    assert "PINE-GATE" in value.context("entrance")["account"]
    assert "OAK-GATE" not in value.context("entrance")["account"]


def test_pronoun_substitution_requires_a_unique_recent_source():
    value = ledger("My entrance is OAK-GATE.", "Replace that with PINE-GATE.")
    assert [(f["field"], f["value"]) for f in value.facts if f["active"]] == [("entrance", "PINE-GATE")]


def test_ungrounded_second_edit_prevents_partial_update():
    value = ledger(
        "My entrance is OAK-GATE.",
        "Replace OAK-GATE with PINE-GATE. Replace UNKNOWN with ELM-GATE.",
    )
    assert value.context("entrance")["requires_clarification"]
    assert not value.revisions


def test_typo_and_intended_value_can_ground_a_field_without_naming_it_again():
    value = ledger(
        "My entrance is OAK-GATE. My receipt code is RECEIPT-X.",
        "I mixed those up: OAK-GATE was a typo; PINE-GATE is what I intended.",
    )
    context = value.context("entrance and receipt")
    assert not context["requires_clarification"]
    assert "PINE-GATE" in context["account"] and "RECEIPT-X" in context["account"]
    assert "OAK-GATE" not in context["account"]
