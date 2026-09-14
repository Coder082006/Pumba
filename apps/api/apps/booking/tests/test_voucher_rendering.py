"""Rendering a voucher — `DocumentPort`, ADR 0026.

The real adapter is asked for by name here, because the test settings resolve
the port to the fake. What matters about it is small and absolute: it produces
a PDF, and the same content produces the same bytes, which is what lets a
re-rendered voucher be checked against the hash recorded when it was issued.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from apps.booking.adapters.pdf import ReportLabDocuments
from apps.common.ports_registry import get_document_port
from ports.document import DocumentPort, VoucherContent
from ports.fakes import FakeDocuments

CONTENT = VoucherContent(
    booking_reference="BKG-2027-4821093",
    trip_reference="TRP-2027-1102934",
    issue_number=1,
    issued_at=datetime(2027, 3, 1, 9, 30, tzinfo=UTC),
    service_title="Sunset Dhow Cruise",
    service_kind="Activity",
    when="Tue 14 Sep 2027, 16:45 (Africa/Dar_es_Salaam)",
    party="2 adults",
    provider_name="North Coast Reef Excursions",
    provider_contact="+255700100002 · bookings@northcoast-reef.example",
    meeting_point="Nungwi beach, by the lighthouse",
    amount_paid="76,000.00 TZS",
    cancellation_terms="Full refund more than 7 days before; half between 7 days and 48 hours.",
    support_contact="support@pumba.example",
    lead_traveller="Ada Lovelace",
)


class TestTheRealRenderer:
    def test_it_satisfies_the_port(self) -> None:
        assert isinstance(ReportLabDocuments(), DocumentPort)

    def test_it_produces_a_pdf(self) -> None:
        data = ReportLabDocuments().render_voucher(CONTENT)
        assert data.startswith(b"%PDF-")
        assert data.rstrip().endswith(b"%%EOF")

    def test_the_same_content_produces_the_same_bytes(self) -> None:
        """ADR 0026 decision 3: without this, every re-render fails its hash."""
        first = ReportLabDocuments().render_voucher(CONTENT)
        second = ReportLabDocuments().render_voucher(CONTENT)
        assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()

    def test_a_different_issue_is_a_different_document(self) -> None:
        first = ReportLabDocuments().render_voucher(CONTENT)
        reissued = ReportLabDocuments().render_voucher(replace(CONTENT, issue_number=2))
        assert first != reissued

    def test_the_reference_is_written_into_the_document(self) -> None:
        """Uncompressed pages, so the text is findable — a voucher someone scans
        with a text search should find its own reference."""
        data = ReportLabDocuments().render_voucher(CONTENT)
        assert b"BKG-2027-4821093" in data


class TestTheFake:
    def test_tests_get_the_fake(self) -> None:
        assert isinstance(get_document_port(), FakeDocuments)

    def test_it_is_readable_and_deterministic(self) -> None:
        fake = FakeDocuments()
        data = fake.render_voucher(CONTENT)
        assert b"Sunset Dhow Cruise" in data
        assert data == FakeDocuments().render_voucher(CONTENT)
        assert fake.rendered == [CONTENT]
