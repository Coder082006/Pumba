"""DocumentPort — rendering a booking voucher. SRS §41.8, §9.3.5, ADR 0026.

§41.8: "Vouchers generate for every confirmed booking." §9.3.5 makes them PDFs.
Rendering is a port for hard rule 13's reason — the PDF library is a vendor
dependency and lives in one adapter — and because the fake lets a test assert
what a voucher *says* without parsing a PDF byte stream.

**Everything on a voucher arrives in `VoucherContent`.** The renderer reads no
database and no clock. `issued_at` is part of the content, and the adapter
takes the document's creation date from it, so the same content always
produces the same bytes — which is what lets ADR 0026 check a re-rendered file
against the hash recorded when it was first issued.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = ["VoucherContent", "DocumentPort"]


@dataclass(frozen=True, slots=True, kw_only=True)
class VoucherContent:
    """What a voucher says — ADR 0026 decision 2, inferred from §41.10.

    Times are already formatted in the destination's timezone, with the zone
    named: a renderer that converted would be a second place the trip's
    timezone rule lived. There is no driver and no pickup PIN until those exist.
    """

    booking_reference: str
    trip_reference: str
    issue_number: int
    issued_at: datetime
    service_title: str
    service_kind: str
    #: e.g. "Tue 14 Sep 2027, 16:45 (Africa/Dar_es_Salaam)".
    when: str
    party: str
    provider_name: str
    provider_contact: str
    meeting_point: str
    amount_paid: str
    cancellation_terms: str
    support_contact: str
    lead_traveller: str


@runtime_checkable
class DocumentPort(Protocol):
    def render_voucher(self, content: VoucherContent) -> bytes: ...
