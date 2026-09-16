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

from ports.document import ItineraryContent, VoucherContent

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

    def render_itinerary(self, content: ItineraryContent) -> bytes:
        """§41.10's emailed trip document: the plan, then every voucher.

        **A5 and one voucher per page, deliberately.** The document exists to be
        read on a phone with no signal, and a tourist showing a provider their
        booking should be able to swipe to a page that is only that booking —
        not scroll a continuous sheet looking for a reference.

        Deterministic like the voucher, for the same reason: `issued_at` fixes
        the creation date, so re-rendering the same trip twice produces the same
        bytes rather than two files that differ only in a timestamp.
        """
        rl_config.invariant = 1
        buffer = BytesIO()
        canvas = Canvas(buffer, pagesize=A5, invariant=1, pageCompression=0)
        canvas.setTitle(f"Itinerary {content.trip_reference}")
        canvas.setAuthor("Pumba")
        canvas.setSubject(content.title)
        canvas.setCreator("Pumba")
        canvas.setDateFormatter(lambda *_: content.issued_at.strftime("D:%Y%m%d%H%M%S+00'00'"))

        y = A5[1] - 16 * mm
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(_LEFT, y, "Your trip")
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(_LEFT + _WIDTH, y, content.trip_reference)

        y -= 11 * mm
        canvas.setFont("Helvetica-Bold", 18)
        for line in _wrap(content.title, 30):
            canvas.drawString(_LEFT, y, line)
            y -= 8 * mm
        canvas.setFont("Helvetica", 10)
        for line in (content.destination, content.dates, content.party):
            canvas.drawString(_LEFT, y, line)
            y -= 5 * mm

        y -= 4 * mm
        for day in content.days:
            y = self._break(canvas, y, needed=18 * mm)
            canvas.setFont("Helvetica-Bold", 11)
            canvas.drawString(_LEFT, y, day.heading)
            y -= 6 * mm
            canvas.setFont("Helvetica", 10)
            for entry in day.lines:
                for line in _wrap(entry, 54):
                    y = self._break(canvas, y, needed=8 * mm)
                    canvas.drawString(_LEFT + 4 * mm, y, line)
                    y -= 5 * mm
            y -= 3 * mm

        y = self._break(canvas, y, needed=24 * mm)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(_LEFT, y, f"Paid: {content.total_paid}")
        y -= 6 * mm
        canvas.setFont("Helvetica", 9)
        canvas.drawString(_LEFT, y, f"Help: {content.support_contact}")

        for voucher in content.vouchers:
            canvas.showPage()
            self._voucher_page(canvas, voucher)

        canvas.showPage()
        canvas.save()
        return buffer.getvalue()

    def _break(self, canvas: Canvas, y: float, *, needed: float) -> float:
        """Start a new page when the next block would fall off this one."""
        if y - needed > 14 * mm:
            return y
        canvas.showPage()
        return A5[1] - 16 * mm

    def _voucher_page(self, canvas: Canvas, content: VoucherContent) -> None:
        """One voucher, on the page the caller has just opened.

        The same fields `render_voucher` draws, and deliberately not a call to
        it: that method owns a whole document, and a voucher inside the
        itinerary is a page inside one.
        """
        top = A5[1] - 16 * mm
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(_LEFT, top, "Booking voucher")
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(_LEFT + _WIDTH, top, content.booking_reference)

        y = top - 12 * mm
        canvas.setFont("Helvetica-Bold", 13)
        for line in _wrap(content.service_title, 34):
            canvas.drawString(_LEFT, y, line)
            y -= 7 * mm

        rows = [
            ("When", content.when),
            ("Travellers", content.party),
            ("Meeting point", content.meeting_point),
            ("Provider", content.provider_name),
            ("Provider contact", content.provider_contact),
            ("Paid", content.amount_paid),
            ("Cancellation", content.cancellation_terms),
        ]
        y -= 4 * mm
        for label, value in rows:
            canvas.setFont("Helvetica", 8)
            canvas.drawString(_LEFT, y, label.upper())
            canvas.setFont("Helvetica", 10)
            for line in _wrap(value, 56):
                y -= 5 * mm
                canvas.drawString(_LEFT, y, line)
            y -= 4 * mm


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
