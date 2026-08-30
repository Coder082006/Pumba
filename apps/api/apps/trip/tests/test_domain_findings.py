"""What the planner reports back — SRS §10.6.

Small, and worth having because two of the rules here are the sort that get
quietly relaxed later. A finding with no code is a banner the client cannot key
its translated copy on, and a finding with no message is one that reaches a
tourist as an empty string — both insert cleanly and fail on screen.
"""

from __future__ import annotations

import pytest

from apps.trip.domain.findings import (
    Finding,
    Severity,
    SuggestedAction,
    worst_severity,
)


def finding(severity: Severity = Severity.WARNING, **overrides: object) -> Finding:
    values: dict[str, object] = {
        "code": "VR-16",
        "severity": severity,
        "message": "Two nights have no accommodation.",
    }
    values.update(overrides)
    return Finding(**values)  # type: ignore[arg-type]


class TestShape:
    def test_a_finding_needs_a_code(self) -> None:
        """§10.6 requires one, and the client keys its copy on it."""
        with pytest.raises(ValueError, match="needs a code"):
            finding(code="")

    def test_a_finding_needs_a_message(self) -> None:
        with pytest.raises(ValueError, match="needs a message"):
            finding(message="")

    def test_it_may_name_no_items(self) -> None:
        """VR-16 is about nights that have *no* item, so there is nothing to
        anchor it against. An empty tuple is the honest answer rather than a
        made-up id."""
        assert finding().item_ids == ()

    def test_the_default_action_is_none(self) -> None:
        assert finding().suggested_action is SuggestedAction.NONE

    def test_a_finding_cannot_be_reassigned(self) -> None:
        """Frozen, so a collector cannot retag an ERROR as a WARNING on its
        way to the client — which would move it across the quote gate."""
        with pytest.raises(AttributeError):
            finding().severity = Severity.ERROR  # type: ignore[misc]

    def test_it_is_not_hashable_and_does_not_claim_to_be(self) -> None:
        """`context` is a mutable mapping because it becomes JSON. Findings
        go into lists, never into sets; this pins the consequence so the
        docstring cannot drift back to claiming otherwise."""
        with pytest.raises(TypeError):
            hash(finding())


class TestQuoteGate:
    def test_an_error_blocks_quoting(self) -> None:
        assert finding(Severity.ERROR).blocks_quoting

    def test_a_warning_does_not(self) -> None:
        """§10.6: WARNING is advisory. VR-16 warns about a night with no
        accommodation rather than blocking — §10.9 supports a trip with no
        accommodation at all."""
        assert not finding(Severity.WARNING).blocks_quoting


class TestWorstSeverity:
    def test_a_clean_itinerary_has_none(self) -> None:
        assert worst_severity(()) is None

    def test_one_error_among_warnings_wins(self) -> None:
        """The banner asks one question — may this be quoted — and the answer
        is decided by the worst finding, not by how many there are."""
        findings = (finding(Severity.WARNING), finding(Severity.ERROR), finding())
        assert worst_severity(findings) is Severity.ERROR

    def test_warnings_alone_report_a_warning(self) -> None:
        assert worst_severity((finding(), finding())) is Severity.WARNING
