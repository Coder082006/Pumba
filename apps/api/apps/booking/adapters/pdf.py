"""The real voucher renderer — ReportLab behind `DocumentPort`. ADR 0026.

Hard rule 13: the vendor library is imported here and nowhere else. Swapping
it touches this file and nothing that calls the port.

**Deterministic by construction.** ReportLab stamps a creation date and a random
document id into every PDF by default, so rendering the same voucher twice
would give two different files and ADR 0026's hash check would refuse every
re-render. `invariant` mode fixes the id and the metadata, and the creation date
is set from the voucher's own `issued_at` rather than the clock.
"""

from __future__ import annotations

from io import BytesIO

from reportlab import rl_config
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from ports.document import VoucherContent

__all__ = ["ReportLabDocuments"]

_LEFT = 14 * mm
_WIDTH = A5[0] - 2 * _LEFT


class ReportLabDocuments:
    """`DocumentPort` over ReportLab. Stateless; safe to share."""

    def render_voucher(self, content: VoucherContent) -> bytes:
        rl_config.invariant = 1
        buffer = BytesIO()
        canvas = Canvas(buffer, pagesize=A5, invariant=1, pageCompression=0)
        canvas.setTitle(f"Voucher {content.booking_reference}")
        canvas.setAuthor("Pumba")
        canvas.setSubject(content.service_title)
        canvas.setCreator("Pumba")
        canvas.setDateFormatter(lambda *_: content.issued_at.strftime("D:%Y%m%d%H%M%S+00'00'"))

        top = A5[1] - 16 * mm
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(_LEFT, top, "Booking voucher")
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(
            _LEFT + _WIDTH,
            top,
            f"Issue {content.issue_number} · {content.issued_at:%d %b %Y %H:%M} UTC",
        )

        y = top - 12 * mm
        canvas.setFont("Helvetica-Bold", 22)
        canvas.drawString(_LEFT, y, content.booking_reference)
        y -= 6 * mm
        canvas.setFont("Helvetica", 9)
        canvas.drawString(_LEFT, y, f"Trip {content.trip_reference}")

        rows = [
            (content.service_kind, content.service_title),
            ("When", content.when),
            ("Travellers", content.party),
            ("Lead traveller", content.lead_traveller),
            ("Meeting point", content.meeting_point),
            ("Provider", content.provider_name),
            ("Provider contact", content.provider_contact),
            ("Paid", content.amount_paid),
            ("Cancellation", content.cancellation_terms),
        ]
        y -= 12 * mm
        for label, value in rows:
            canvas.setFont("Helvetica", 8)
            canvas.drawString(_LEFT, y, label.upper())
            canvas.setFont("Helvetica", 11)
            for line in _wrap(value, 52):
                y -= 5 * mm
                canvas.drawString(_LEFT, y, line)
            y -= 5 * mm

        canvas.setFont("Helvetica", 8)
        canvas.drawString(
            _LEFT,
            12 * mm,
            f"Show this voucher to your provider. Help: {content.support_contact}",
        )
        canvas.showPage()
        canvas.save()
        return buffer.getvalue()


def _wrap(text: str, width: int) -> list[str]:
    """Plain word wrap. A voucher line is a name or a sentence, never a table."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    return [*lines, current] if current else lines or [""]
