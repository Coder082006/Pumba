"""Amend the baselined SRS .docx in place for the ADR 0013 decision (v1.2).

Accommodation stops being a bookable product and becomes a location reference.
`room_type` and `room_availability` are deferred to v2; a STAY item becomes a
stay anchor carrying location and dates and nothing else.

Every edit is located by a plain-text anchor asserted to occur exactly once in
`word/document.xml`, and the archive is rebuilt entry-by-entry from the original
so that every part except `word/document.xml` is byte-identical.

Sections are **marked deferred in place**, never deleted and never renumbered:
many cross-references in this document depend on the current numbers, and a
renumbering would silently break them.

Applied once, on 2026-08-21, producing SRS v1.2. It is kept in the repository
because a diff of a .docx is unreadable in review, and this file is the only
precise record of what changed inside the binary. Re-running it fails its own
assertions by design: the anchors it looks for no longer exist.

    python scripts/amend_srs_v1_2.py "docs/srs/SRS-....docx"
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path(sys.argv[1])

# Run properties as the document already uses them, so amended text sits in the
# same type as the text around it.
RS = '<w:rPr><w:sz w:val="18"/></w:rPr>'  # table body
RB = '<w:rPr><w:b/><w:sz w:val="18"/></w:rPr>'  # table body, bold
PS = '<w:rPr><w:sz w:val="20"/></w:rPr>'  # running text
PB = '<w:rPr><w:b/><w:sz w:val="20"/></w:rPr>'  # running text, bold
MONO = (
    '<w:rPr><w:rFonts w:ascii="Courier New" w:eastAsia="Courier New" '
    'w:hAnsi="Courier New" w:cs="Courier New"/><w:sz w:val="15"/></w:rPr>'
)

DEFER = "Deferred to v2 (ADR 0013)."

#: The document sets table and identifier names in Courier New, so Word splits
#: such a token into its own run. An anchor that has to span one needs the run
#: boundary spelled out.
COURIER = (
    '<w:r><w:rPr><w:rFonts w:ascii="Courier New" w:eastAsia="Courier New" '
    'w:hAnsi="Courier New" w:cs="Courier New"/><w:sz w:val="15"/></w:rPr>'
)
COMMA = f'</w:r><w:r>{RS}<w:t xml:space="preserve">, </w:t></w:r>{COURIER}'

_para_id = 0x0A130000


def _next_id() -> str:
    global _para_id
    _para_id += 1
    return f"{_para_id:08X}"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run(text: str, rpr: str = RS) -> str:
    return f'<w:r>{rpr}<w:t xml:space="preserve">{esc(text)}</w:t></w:r>'


def para(runs: str, spacing: str = '<w:spacing w:after="0"/>') -> str:
    return (
        f'<w:p w14:paraId="{_next_id()}" w14:textId="77777777" w:rsidR="00CF1EF7" '
        f'w:rsidRDefault="00000000"><w:pPr>{spacing}</w:pPr>{runs}</w:p>'
    )


def body_para(bold_lead: str, text: str) -> str:
    """A running-text paragraph led by a bold amendment marker."""
    return para(
        run(bold_lead, PB) + run(" " + text, PS),
        '<w:spacing w:after="120" w:line="260" w:lineRule="auto"/>'
        '<w:ind w:left="-5" w:hanging="10"/>',
    )


def mono_para(text: str) -> str:
    return para(run(text, MONO), '<w:spacing w:after="36"/>')


# --------------------------------------------------------------------------
# Locating. Every anchor is plain text and must be unique in the whole part.
# --------------------------------------------------------------------------
class Doc:
    def __init__(self, xml: str) -> None:
        self.xml = xml
        self.applied: list[str] = []

    def _at(self, anchor: str) -> int:
        n = self.xml.count(anchor)
        if n != 1:
            raise AssertionError(f"anchor {anchor[:70]!r} occurs {n} times, expected 1")
        return self.xml.index(anchor)

    def _span(self, anchor: str, open_tag: str, close_tag: str) -> tuple[int, int]:
        """The innermost `open_tag`…`close_tag` around `anchor`.

        The guard matters more than it looks. Several sections of this document
        that *read* as tables are loose tab-separated paragraphs — §26.4 is one
        — so asking for the enclosing `<w:tc>` there silently walks back to some
        unrelated cell pages earlier and forward to its close, and the edit eats
        everything between. None of these elements nest, so a second `open_tag`
        inside the span proves the span is wrong.
        """
        i = self._at(anchor)
        start = self.xml.rindex(open_tag, 0, i)
        end = self.xml.index(close_tag, i) + len(close_tag)
        block = self.xml[start + len(open_tag) : end]
        if open_tag in block:
            raise AssertionError(
                f"{anchor[:60]!r} is not inside a {open_tag!r}: the nearest one encloses "
                f"{block.count(open_tag) + 1} of them, so this is the wrong element"
            )
        return start, end

    def _apply(self, label: str, start: int, end: int, replacement: str) -> None:
        self.xml = self.xml[:start] + replacement + self.xml[end:]
        self.applied.append(label)

    # -- exact substitutions -------------------------------------------------
    def sub(self, label: str, old: str, new: str) -> None:
        i = self._at(old)
        self._apply(label, i, i + len(old), new)

    # -- paragraph operations ------------------------------------------------
    def set_para(self, label: str, anchor: str, runs: str) -> None:
        start, end = self._span(anchor, "<w:p ", "</w:p>")
        block = self.xml[start:end]
        head = block[: block.index("</w:pPr>") + len("</w:pPr>")] if "<w:pPr>" in block else (
            block[: block.index(">") + 1]
        )
        self._apply(label, start, end, head + runs + "</w:p>")

    def append_para(self, label: str, anchor: str, runs: str) -> None:
        start, end = self._span(anchor, "<w:p ", "</w:p>")
        self._apply(label, start, end, self.xml[start : end - len("</w:p>")] + runs + "</w:p>")

    def after_para(self, label: str, anchor: str, xml: str) -> None:
        _, end = self._span(anchor, "<w:p ", "</w:p>")
        self._apply(label, end, end, xml)

    def before_para(self, label: str, anchor: str, xml: str) -> None:
        start, _ = self._span(anchor, "<w:p ", "</w:p>")
        self._apply(label, start, start, xml)

    def drop_para(self, label: str, anchor: str) -> None:
        start, end = self._span(anchor, "<w:p ", "</w:p>")
        self._apply(label, start, end, "")

    # -- cell and row operations ---------------------------------------------
    def set_cell(self, label: str, anchor: str, paragraphs: str) -> None:
        start, end = self._span(anchor, "<w:tc>", "</w:tc>")
        block = self.xml[start:end]
        head = block[: block.index("</w:tcPr>") + len("</w:tcPr>")]
        self._apply(label, start, end, head + paragraphs + "</w:tc>")

    def after_row(self, label: str, anchor: str, xml: str) -> None:
        _, end = self._span(anchor, "<w:tr ", "</w:tr>")
        self._apply(label, end, end, xml)


def cell(width: int, runs: str, span: int = 1) -> str:
    grid = f'<w:gridSpan w:val="{span}"/>' if span > 1 else ""
    borders = (
        '<w:tcBorders><w:top w:val="nil"/><w:left w:val="nil"/>'
        '<w:bottom w:val="nil"/><w:right w:val="nil"/></w:tcBorders>'
    )
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{grid}{borders}</w:tcPr>'
        f"{para(runs)}</w:tc>"
    )


def row(*cells: str) -> str:
    return (
        f'<w:tr w:rsidR="00CF1EF7" w14:paraId="{_next_id()}" w14:textId="77777777">'
        f'<w:trPr><w:trHeight w:val="299"/></w:trPr>{"".join(cells)}</w:tr>'
    )


def amend(d: Doc) -> None:
    # ======================================================================
    # Revision history
    # ======================================================================
    d.after_row(
        "revision history: v1.2",
        "docs/adr/0002-web-first-tourist-client.md.",
        row(
            cell(1876, run("1.2") + f"<w:r>{RS}<w:tab/><w:t>2026-08-21</w:t></w:r>", span=2),
            cell(2330, run("Product Owner"), span=2),
            cell(
                4819,
                run(
                    "Accommodation re-scoped from a bookable product to a location reference. "
                    "room_type and room_availability deferred to v2 and removed from the v1 "
                    "schema; a STAY item becomes a stay anchor carrying location and dates, "
                    "with no provider, price, booking or inventory. Amends §2.2, §3.3, "
                    "§5.1, §5.3, §6.4, §7.2–§7.6, §8.4, "
                    "§8.10, §9.2, §9.4, §10.6, §10.7, §14, "
                    "§17, §18.2, §18.4, §20.2, §20.3, §22.1, "
                    "§24.11–§24.13, §26.4, §26.5, §28.2.5, "
                    "§33.12, §37.5, §38.1–§38.5, §41.4, "
                    "§42.2, Appendix B, Appendix C and Appendix D. Rationale and "
                    "consequences in docs/adr/0013-accommodation-is-a-location-reference-in-v1.md."
                ),
                span=2,
            ),
        ),
    )

    # ======================================================================
    # 2.2 Scope
    # ======================================================================
    d.set_para(
        "2.2.1 catalogue bullet",
        "Browsing of a curated, administrator-managed catalogue of Zanzibar destinations, "
        "attractions, activities and accommodation.",
        run(
            "Browsing of a curated, administrator-managed catalogue of Zanzibar destinations, "
            "attractions and activities, and of accommodation as location reference data "
            "(v1.2: accommodation is not sold — §14, ADR 0013).",
            PS,
        ),
    )
    d.set_para(
        "2.2.1 trip construction bullet",
        "accommodation nights, activity selections, and inter-location transport legs.",
        run(
            "Construction of a multi-day trip: dates, party size, inbound and outbound flight "
            "details, stay anchors (where the tourist is staying, and from when to when), "
            "activity selections, and inter-location transport legs.",
            PS,
        ),
    )
    d.set_para(
        "2.2.1 inventory bullet",
        "Availability enforcement, capacity enforcement and inventory holding across "
        "accommodation, activities and transport.",
        run(
            "Availability enforcement, capacity enforcement and inventory holding across "
            "activities and transport. Accommodation carries no inventory in v1 (§14, "
            "ADR 0013).",
            PS,
        ),
    )
    d.after_row(
        "2.2.2 accommodation booking excluded",
        "Any AI/ML capability of any kind",
        row(
            cell(
                3000,
                run(
                    "Accommodation booking, room inventory, rate management and accommodation "
                    "commission"
                ),
            ),
            cell(
                6000,
                run(
                    "Tourists choose and book a hotel months ahead on established OTAs, before "
                    "they consider transfers or excursions; by the time they reach the Platform "
                    "the property is already chosen. V1 records where the tourist is staying as "
                    "a stay anchor (§14) so that transfers can be planned and priced "
                    "around it. Full subsystem returns in v2 — ADR 0013."
                ),
            ),
        ),
    )

    # ======================================================================
    # 3.3 The itinerary artefact
    # ======================================================================
    d.sub(
        "3.3 check-in line becomes a stay anchor",
        ">  16:30  Check-in          Ocean Breeze Hotel, Deluxe Sea View</w:t>",
        ">  16:30  Stay              Ocean Breeze Hotel, Nungwi</w:t>",
    )
    d.sub(
        "3.3 stay line carries no price",
        ">         10 Aug -&gt; 15 Aug, 5 nights, 1 room             USD 465.00</w:t>",
        ">         10 Aug -&gt; 15 Aug, 5 nights        booked separately</w:t>",
    )
    d.drop_para(
        "3.3 accommodation drops out of the cost summary",
        "  Accommodation                                            USD 465.00",
    )
    d.sub(
        "3.3 service fee recomputed",
        ">  Service fee (5%)                                         USD  39.75",
        ">  Service fee (5%)                                         USD  16.50",
    )
    d.sub(
        "3.3 total recomputed",
        ">  TOTAL PAID                                               USD 834.75</w:t>",
        ">  TOTAL PAID                                               USD 346.50</w:t>",
    )
    d.after_para(
        "3.3 anchor note",
        "The exact layout is specified in Section 24.20; the content model is specified in "
        "Section 10.",
        body_para(
            "Amended v1.2.",
            "The stay appears as an anchor line: the property, the dates and no price. The "
            "Platform does not sell the room in v1 (§14, ADR 0013), so the cost summary "
            "covers transfers and activities only, and the total is what the Platform actually "
            "charges. Where the tourist has booked elsewhere, the itinerary says so rather "
            "than implying the stay is part of the basket.",
        ),
    )

    # ======================================================================
    # 5.1 / 5.3 Actors
    # ======================================================================
    d.set_para(
        "5.1 accommodation provider volume",
        "180 properties",
        run("— ") + run(DEFER + " Not a v1 actor.", RB),
    )
    d.append_para(
        "5.3.3 accommodation provider deferred",
        "Register the business; submit business licence and tax registration; create one or "
        "more properties",
        run(" " + DEFER + " ", PB)
        + run(
            "Accommodation is administrator-curated location reference data in v1, not "
            "provider-listed supply, so there is no accommodation provider role, onboarding or "
            "portal surface. The responsibilities above return with the subsystem.",
            PS,
        ),
    )

    # ======================================================================
    # 6.4 Module catalogue
    # ======================================================================
    d.set_cell(
        "6.4 catalogue owns",
        "<w:t>media</w:t>",
        para(
            run(
                "country, region, destination, attraction, activity, activity_schedule, "
                "accommodation, cancellation_policy, tag, media"
            )
        ),
    )
    d.set_cell(
        "6.4 catalogue interface",
        "<w:t>list_room_types()</w:t>",
        para(run("search_activities(), get_destination(), list_accommodation()")),
    )
    d.set_cell(
        "6.4 inventory owns",
        f"<w:t>room_availability</w:t>{COMMA}<w:t>activity_departure</w:t>"
        f"{COMMA}<w:t>inventory_hold</w:t>",
        para(run("activity_departure, inventory_hold"))
        + para(run(DEFER + " room_availability returns with the accommodation subsystem.", RB)),
    )

    # ======================================================================
    # 7 Domain model
    # ======================================================================
    d.append_para(
        "7.2 optimistic locking list",
        " for optimistic locking",
        run(" — room_availability: " + DEFER, RB),
    )
    # Same column width, so the box does not lose its right-hand rule.
    d.sub(
        "7.3 View 3 itinerary_item drops room_type_id",
        ">                     | FK room_type_id       NULL           |</w:t>",
        ">                     |    room_type_id: v2 (ADR 0013)       |</w:t>",
    )
    # The note goes *before* the §7.3.4 heading rather than after the amended
    # line: that line lives inside the diagram cell, and running text dropped in
    # the middle of an ASCII box breaks the box.
    d.before_para(
        "7.3 View 3 deferral note",
        "View 4 — Payment, Finance, Engagement and Operations",
        body_para(
            "Amended v1.2 — ADR 0013.",
            "booking_accommodation is deferred to v2 with the accommodation subsystem, and "
            "booking_type ACCOMMODATION is reserved rather than withdrawn. A STAY "
            "itinerary_item carries accommodation_id (or a free-entry name and coordinate), "
            "starts_at and ends_at, and no room_type_id, quantity, unit_price, line_total or "
            "booking_id: a stay anchor has no price and no booking. The View 2 boxes for "
            "room_type and room_availability are deferred with it.",
        ),
    )

    # ======================================================================
    # 10.6 Validation rules
    # ======================================================================
    d.set_para(
        "10.6 VR-04",
        "Every accommodation night in the trip range is covered by exactly one ",
        run(
            "Every night in the trip range is covered by exactly one STAY anchor, or "
            "explicitly marked as “own arrangement”. Amended v1.2: “own "
            "arrangement” is the normal case, since the Platform does not sell the room "
            "(§14, ADR 0013)."
        ),
    )
    d.set_para(
        "10.6 VR-05",
        "Party size ≤ room occupancy for every stay; party size within ",
        run(
            "Party size within [min_pax, max_pax] for every activity. Amended v1.2: the room "
            "occupancy half of this rule is deferred to v2 with room_type (ADR 0013); a stay "
            "anchor asserts no occupancy."
        ),
    )
    d.set_para(
        "10.6 VR-11",
        "Minimum-nights requirement of each selected room type is satisfied",
        run("Minimum-nights requirement of each selected room type is satisfied. " )
        + run(DEFER, RB),
    )
    d.append_para(
        "10.6 VR-16 retained",
        "The trip has no accommodation for one or more nights",
        run(". Retained in v1.2: a night with no stay anchor is a night whose transfers "
            "cannot be planned around it."),
    )
    d.sub(
        "10.7 STAY carries no line total",
        ">FOR each item:     line_total := unit_price × quantity            "
        "(STAY: nightly rate × nights × rooms)</w:t>",
        ">FOR each item:     line_total := unit_price × quantity            "
        "(STAY: none — an anchor has no price, ADR 0013)</w:t>",
    )

    # ======================================================================
    # 14 Accommodation Reference and Stay Anchoring
    # ======================================================================
    d.sub("14 retitled", ">Accommodation Subsystem</w:t>", ">Accommodation Reference and Stay Anchoring</w:t>")
    d.set_para(
        "14.1 rewritten",
        "Accommodation is a two-level model",
        run(
            "Amended v1.2 — ADR 0013. Accommodation is not sold by the Platform in v1. "
            "It is a curated location reference: an accommodation row holds name, slug, "
            "property type, destination, coordinates, address line, check-in time, check-out "
            "time and is_active, and nothing about price, capacity or availability. It is "
            "administrator-managed catalogue data of the same kind as an attraction, not "
            "provider-listed supply.",
            PS,
        ),
    )
    d.after_para(
        "14.1 stay anchoring specified",
        "Amended v1.2 — ADR 0013. Accommodation is not sold by the Platform in v1.",
        body_para(
            "A stay anchor.",
            "A STAY itinerary item anchors location and dates. It has no provider, no price, "
            "no booking and no inventory. It exists so that the itinerary knows where the "
            "tourist sleeps, which is what the adjacent transfers need.",
        )
        + body_para(
            "Captured one of two ways.",
            "Curated property — the tourist selects a seeded accommodation record; its "
            "coordinates are known and exact, so transfer routing and pricing are accurate. "
            "Free entry — the tourist types any hotel name or address, which is resolved "
            "to a coordinate through the geocoding path of §13.2 and shown as a map pin "
            "the tourist confirms. Per §13.2 an unconfirmed geocode is never silently "
            "persisted: if the tourist does not confirm the pin, no anchor is created.",
        )
        + body_para(
            "What an anchor does.",
            "Either way the item supplies the origin or the target for the transfers on "
            "either side of it, and bounds the day sequencing of §10.4. Multiple "
            "non-overlapping stays across one trip remain supported. VR-16’s warning for "
            "uncovered nights is retained.",
        )
        + body_para(
            "Why no inventory exists in v1.",
            "Selling the room would mean availability calendars, rate resolution, "
            "minimum-stay rules and inventory holds — the heaviest subsystem in this "
            "document — aimed at a decision the tourist has already made elsewhere, "
            "months earlier. §38.2 records an optional Booking.com affiliate deep link "
            "as a SHOULD-HAVE; nothing in the booking, pricing or itinerary path may depend "
            "on it.",
        ),
    )
    d.after_para(
        "14.2-14.4 marked deferred",
        "Supported property types: HOTEL, RESORT, LODGE, GUESTHOUSE, APARTMENT, VILLA. The "
        "type affects presentation and filtering only.",
        body_para(
            "§14.2 to §14.4 are deferred to v2 — ADR 0013.",
            "The rate model, the availability model and the booking rules below are preserved "
            "verbatim, and are not implemented in v1. room_type and room_availability do not "
            "exist in the v1 schema. BR-101 is the one exception: its bound on the stay "
            "— check-out strictly after check-in, maximum 30 nights — applies to a "
            "stay anchor in v1 exactly as it applied to a booking, and is enforced against "
            "the Appendix B setting stay.max_nights.",
        ),
    )
    d.append_para(
        "14.5 check-in and check-out",
        "Early check-in and late check-out are not sold in V1; they are a provider-side "
        "arrangement noted in the booking’s ",
        run(" Amended v1.2. ", PB)
        + run(
            "Where the stay anchor names a curated property, its check_in_time and "
            "check_out_time bound the STAY item and therefore the timing of the arrival "
            "transfer and any first-day activity. Where the anchor is a free-entry address, "
            "the destination defaults in Appendix B apply until the tourist overrides them. "
            "There is no booking, so there are no guest_notes in v1.",
            PS,
        ),
    )
    d.append_para(
        "14.6 policies still apply to activities",
        "A policy is expressed generically as an ordered list of ",
        run(" Amended v1.2. ", PB)
        + run(
            "cancellation_policy is retained in full: activities reference it, and it is "
            "administered under §27.12. Only the accommodation reference to it is "
            "deferred, because a location record has nothing to cancel.",
            PS,
        ),
    )
    d.set_para(
        "14.7 provider operations",
        "Providers manage properties, room types, photographs, amenities, the availability "
        "and rate calendar, arriving bookings, and cancellations, through the portal "
        "(Section 26).",
        run("Amended v1.2 — ADR 0013. ", PB)
        + run(
            "There are no accommodation provider operations in v1. Accommodation records are "
            "created and maintained by a catalogue administrator through the console "
            "(§27.8) and by the seed loader (Appendix C). The provider portal covers "
            "transport and activity providers. The paragraph this replaces — properties, "
            "room types, the availability and rate calendar, arriving bookings and "
            "cancellations — returns with the subsystem in v2.",
            PS,
        ),
    )

    # ======================================================================
    # 17 Inventory
    # ======================================================================
    d.set_para(
        "17.1 I1",
        "Capacity counters live in exactly one place per resource type: ",
        run("I1")
        + f"<w:r>{RS}<w:tab/></w:r>"
        + run(
            "Capacity counters live in exactly one place per resource type. In v1 that is "
            "activity_departure alone; room_availability is deferred to v2 with the "
            "accommodation subsystem (ADR 0013)."
        ),
    )
    d.append_para(
        "17 scoped to activity departures",
        "The critical section for placing a hold:",
        run(" Amended v1.2. ", PB)
        + run(
            "V1 holds inventory for activity departures only. The routine below is written "
            "against room_availability, which does not exist in the v1 schema; read "
            "activity_departure, departs_at and capacity_held for room_availability, "
            "stay_date and rooms_held. The lock discipline, the ascending primary-key "
            "ordering, the application assertion and the CHECK backstop are unchanged, and "
            "the accommodation form returns verbatim in v2.",
            PS,
        ),
    )

    # ======================================================================
    # 18 Pricing
    # ======================================================================
    d.set_cell(
        "18.2 accommodation line component",
        "<w:t>Accommodation line</w:t>",
        para(run("Accommodation line ") + run(DEFER, RB)),
    )
    d.set_para(
        "18.4 catalogue currency",
        "The currency in which a provider publishes a price (",
        run("The currency in which a provider publishes a price (activity.currency; "
            "room_type.currency returns with v2)"),
    )

    # ======================================================================
    # 20 Booking engine
    # ======================================================================
    d.set_cell(
        "20.3 accommodation column deferred",
        f'<w:t>room_availability</w:t></w:r><w:r>{RS}<w:t xml:space="preserve"> nights</w:t>',
        para(run("Inventory") + f"<w:r>{RS}<w:tab/></w:r>" + run(DEFER, RB))
        + para(run("(room_availability nights)")),
    )
    d.after_para(
        "20.2 booking type reserved",
        "Transitions are validated by a single table-driven guard function; an illegal "
        "transition raises ",
        body_para(
            "Amended v1.2 — ADR 0013.",
            "ACCOMMODATION is not a v1 booking type: the v1 set is ACTIVITY and TRANSFER. The "
            "enum value ACCOMMODATION and the booking_accommodation subtype table are "
            "reserved, not withdrawn, so that reviving the subsystem in v2 renumbers nothing. "
            "The lifecycle, the guards and the audit trail are unchanged.",
        ),
    )

    # ======================================================================
    # 22.1 Worked revenue example
    # ======================================================================
    d.sub("22.1 gross", ">   Tourist pays gross                        e.g. USD 834.75</w:t>",
          ">   Tourist pays gross                        e.g. USD 346.50</w:t>")
    d.sub(
        "22.1 service fee",
        ">        +-- Platform service fee (tourist-facing)      USD  39.75         |</w:t>",
        ">        +-- Platform service fee (tourist-facing)      USD  16.50         |</w:t>",
    )
    d.drop_para(
        "22.1 accommodation provider line",
        "             +-- Accommodation provider  465.00",
    )
    d.sub(
        "22.1 accommodation commission line",
        ">             |       - commission 15%     -69.75  -&gt; net 395.25              "
        "+-- Activity provider        170.00</w:t>",
        ">             +-- Activity provider        170.00</w:t>",
    )
    d.sub(
        "22.1 revenue",
        ">   Platform revenue = service fee 39.75 + commissions 132.35 = USD 172.10</w:t>",
        ">   Platform revenue = service fee 16.50 + commissions  62.60 = USD  79.10</w:t>",
    )
    d.sub(
        "22.1 payouts",
        ">   Provider payouts  = 395.25 + 139.40 + 128.00               = USD 662.65</w:t>",
        ">   Provider payouts  = 139.40 + 128.00                        = USD 267.40</w:t>",
    )

    # ======================================================================
    # 24 Tourist screens
    # ======================================================================
    d.sub("24.11 retitled", ">24.11 Accommodation Search</w:t>", ">24.11 Where Are You Staying</w:t>")
    d.set_para(
        "24.11 rewritten",
        "Find a stay for the trip dates.",
        run("Purpose", PB)
        + run(
            " Record where the tourist is staying, so the itinerary can plan transfers around "
            "it. ",
            PS,
        )
        + run("Components", PB)
        + run(
            " One screen. A search field over the curated accommodation list for this "
            "destination, with a “can’t find it?” affordance that accepts any "
            "hotel name or address as free entry; a map showing the pin, which the tourist "
            "confirms; check-in and check-out date pickers; an “I haven’t booked "
            "yet” option that skips the anchor and leaves VR-16’s warning standing. "
            "No prices, no availability, no room types. ",
            PS,
        )
        + run("Validation", PB)
        + run(
            " Check-out strictly after check-in; stay ≤ stay.max_nights (default 30); "
            "dates within the trip range; a free-entry address must have a confirmed geocode "
            "before the anchor is created (§13.2). ",
            PS,
        )
        + run("API", PB)
        + run(
            " GET /accommodations?destination=… for the curated list; POST "
            "/trips/{id}/items with item_type STAY. ",
            PS,
        )
        + run("Navigation", PB)
        + run(" → Trip Planner. ", PS)
        + run("States", PB)
        + run(
            " Skeleton list; a no-match state that offers free entry rather than a dead end; "
            "a geocode-failed state that asks the tourist to drop the pin themselves; error "
            "with retry. Where an affiliate deep link is configured (§38.2) a property "
            "card may offer “book this stay”, which leaves the Platform and is "
            "never presented as part of the basket.",
            PS,
        ),
    )
    d.sub("24.12 retitled", ">24.12 Accommodation Details</w:t>",
          ">24.12 Accommodation Details (deferred to v2)</w:t>")
    d.sub("24.13 retitled", ">24.13 Room Selection</w:t>",
          ">24.13 Room Selection (deferred to v2)</w:t>")
    d.append_para(
        "24.13 deferral note",
        "Unavailable room types are shown greyed with the reason",
        run(" " + DEFER + " ", PB)
        + run(
            "§24.12 and §24.13 are preserved with their numbering so that "
            "cross-references keep resolving. Neither screen is built in v1; §24.11 in "
            "its amended form is the whole of the accommodation experience.",
            PS,
        ),
    )

    # ======================================================================
    # 26 Provider portal
    # ======================================================================
    # §26.4 reads as a table but is tab-separated paragraphs, so this is a
    # paragraph edit: label, tab, capability text.
    d.set_para(
        "26.4 accommodation listing management",
        "Create and edit properties, room types, photographs (ordered, with a primary), "
        "amenities, house rules, check-in/check-out times, cancellation policy selection, "
        "activate/deactivate",
        run("Accommodation")
        + f"<w:r>{RS}<w:tab/></w:r>"
        + run(DEFER + " ", RB)
        + run(
            "The v1 provider portal covers transport and activity providers only. "
            "Accommodation records are catalogue reference data maintained by an "
            "administrator (§27.8) and by the seed loader."
        ),
    )
    d.append_para(
        "26.5 rate calendar scoped to activities",
        "A conflict (attempting to reduce availability below what is already sold or held) is "
        "rejected with the specific dates named.",
        run(" Amended v1.2. ", PB)
        + run(
            "In v1 the calendar exists for activity departures only; PUT "
            "/provider/room-types/{id}/availability is deferred to v2 with room_type "
            "(ADR 0013).",
            PS,
        ),
    )

    # ======================================================================
    # 33.12 Test cases
    # ======================================================================
    d.set_cell(
        "TC-020 amended",
        "<w:t>Search accommodation</w:t>",
        para(run("Search accommodation — ") + run("void in v1", RB)),
    )
    d.after_row(
        "TC-020 void note",
        "200; only room types available every night",
        row(
            cell(1000, run("")),
            cell(8650, run(
                "TC-020 is void in v1 and is amended rather than deleted, because §41.4 "
                "and §42.2 refer to it by number. There is no accommodation search with "
                "availability in v1 (ADR 0013); the v1 equivalent is that GET /accommodations "
                "returns curated location records for a destination, with no price and no "
                "availability, and that a stay anchor can be created from one. TC-020 returns "
                "with the subsystem in v2.",
            ), span=4),
        ),
    )
    d.set_cell(
        "TC-050 scoped to activity departures",
        f'<w:t xml:space="preserve">200; </w:t></w:r>{COURIER}<w:t>rooms_held</w:t>',
        para(run("200; capacity_held incremented; quote_expires_at set (v1.2: "
                 "activity departures only — rooms_held returns with v2)")),
    )
    d.set_cell(
        "TC-053 scoped to activity departures",
        "<w:t>Concurrent quote for the last room</w:t>",
        para(run("Concurrent quote for the last seat on a departure")),
    )
    d.set_cell(
        "TC-053 preconditions",
        "<w:t>1 room; 2 simultaneous quotes</w:t>",
        para(run("1 seat; 2 simultaneous quotes")),
    )
    d.set_cell(
        "TC-053 expectation",
        '<w:t xml:space="preserve">Exactly one 200, one 409; </w:t>',
        para(run("Exactly one 200, one 409; capacity_held = 1")),
    )

    # ======================================================================
    # 37.5 Phase 5
    # ======================================================================
    d.sub(
        "37.5 retitled",
        ">Phase 5 — Accommodation and Inventory (3 weeks)</w:t>",
        ">Phase 5 — Activity Inventory and Holds (3 weeks)</w:t>",
    )
    d.set_para(
        "37.5 rescoped",
        "Availability calendar, rate resolution, the hold lifecycle",
        run("Features", PB)
        + run(
            " The activity departure calendar, the hold lifecycle, the concurrency-safe hold "
            "and commit routines, the expiry sweeper, and the reconciliation checker; "
            "provider departure and capacity APIs. ",
            PS,
        )
        + run("Dependencies", PB)
        + run(" Phases 3 and 4. ", PS)
        + run("Deliverables", PB)
        + run(" Activity search with authoritative capacity; hold mechanics. ", PS)
        + run("Acceptance", PB)
        + run(
            " TC-050 to TC-053 pass against activity departures, including the concurrency "
            "test with zero oversell under LT-03. Amended v1.2 — ADR 0013: rate "
            "resolution and the room availability calendar are deferred to v2.",
            PS,
        ),
    )

    # ======================================================================
    # 38 MVP
    # ======================================================================
    d.set_cell(
        "38.1 accommodation row becomes stay anchoring",
        "<w:t>Search, availability, rates, room selection, booking</w:t>",
        para(
            run(
                "Stay anchoring: a curated property or a free-entry address with a confirmed "
                "geocode, plus check-in and check-out. No inventory, no price, no booking "
                "(§14, ADR 0013)."
            )
        ),
    )
    d.sub(
        "38.2 affiliate deep links",
        ">Promotion and discount codes; multi-currency presentment beyond USD and TZS;",
        ">Booking.com affiliate deep links from a curated accommodation record, non-blocking "
        "and outside the basket (Appendix D10); promotion and discount codes; multi-currency "
        "presentment beyond USD and TZS;",
    )
    d.sub(
        "38.3 accommodation subsystem deferred",
        ">Flight status integration and automatic transfer re-timing;",
        ">The full accommodation subsystem — room types, rate resolution, availability "
        "calendars, room inventory holds and accommodation commission (ADR 0013); flight "
        "status integration and automatic transfer re-timing;",
    )
    d.sub(
        "38.5 provider success criterion",
        ">≥ 60 accommodation, ≥ 40 activity, ≥ 100 drivers</w:t>",
        ">≥ 40 activity, ≥ 100 drivers (v1.2: the accommodation provider target is "
        "withdrawn — ADR 0013)</w:t>",
    )

    # ======================================================================
    # 41.4 Acceptance criteria
    # ======================================================================
    d.set_para(
        "41.4 rewritten against stay anchoring",
        "Search returns only room types available for every night of the requested stay",
        run("Amended v1.2 — ADR 0013. ", PB)
        + run(
            "A tourist can record where they are staying, either by choosing a curated "
            "property from the destination’s accommodation list or by entering any hotel "
            "name or address and confirming the map pin. A free-entry address with no "
            "confirmed geocode creates no anchor. The resulting STAY item carries location "
            "and dates and carries no provider, price, booking or inventory. Check-out is "
            "strictly after check-in and the stay does not exceed stay.max_nights. "
            "Transfers on either side of the anchor quote from its coordinates, and a "
            "curated property quotes to the metre. A trip with a night that no anchor covers "
            "still quotes, and raises VR-16. Multiple non-overlapping stays in one trip are "
            "supported. Tests TC-021, plus the stay-anchor cases of TC-040 and TC-043. "
            "TC-020 and TC-050 to TC-053 as written against rooms return with v2.",
            PS,
        ),
    )

    # ======================================================================
    # 42.2 Traceability
    # ======================================================================
    d.after_row(
        "42.2 amended rows",
        "<w:t>FR-101</w:t>",
        row(
            cell(
                9650,
                run("Amended v1.2 — ADR 0013. ", RB)
                + run(
                    "FR-013 becomes “Tourist records where they are staying”, against GET "
                    "/accommodations and POST /trips/{id}/items, entities accommodation "
                    "only, screen §24.11, rule BR-101, test TC-021. FR-030 keeps "
                    "inventory_hold and activity_departure and drops room_availability. "
                    "FR-101 covers activity and transport listings only. The rows above are "
                    "left as written so that every FR number keeps resolving.",
                ),
                span=8,
            )
        ),
    )

    # ======================================================================
    # Appendix B, C and D
    # ======================================================================
    d.set_cell(
        "Appendix B availability horizon",
        "<w:t>Calendar auto-extension</w:t>",
        para(run("Calendar auto-extension. " ) + run(DEFER, RB)),
    )
    d.set_para(
        "Appendix C seeding",
        " seeded; they are created by verified providers through the portal, which is the "
        "correct source of truth for price, availability and capacity.",
        run("Amended v1.2 — ADR 0013. ", PB)
        + run(
            "Activity records are not seeded: they are created by verified providers through "
            "the portal, which is the correct source of truth for price, availability and "
            "capacity. Accommodation records are seeded, because in v1 they are location "
            "reference data and assert nothing about price or availability. Seeding roughly "
            "forty known Zanzibar properties with coordinates makes transfer pricing exact "
            "for the tourists most likely to arrive with a hotel already booked.",
            PS,
        ),
    )
    d.after_row(
        "Appendix C accommodation seed row",
        "Stone Town sites, Jozani Forest, Prison Island, Nungwi turtle sanctuary, spice "
        "farms, Mnemba Atoll, and others",
        row(
            cell(2000, run("Accommodation (location records)")),
            cell(1000, run("~40")),
            cell(6650, run(
                "Known Zanzibar properties with name, property type, destination, coordinates "
                "and address. Reference data, not inventory: no price, no capacity, no "
                "availability (§14, ADR 0013)."
            )),
        ),
    )
    d.after_row(
        "Appendix D10",
        "Support operating hours and escalation contacts",
        row(
            cell(600, run("D 10")),
            cell(5000, run(
                "Booking.com affiliate enrolment — whether to join the affiliate "
                "programme and surface deep links from curated accommodation records at "
                "roughly 4% on completed stays. Airbnb offers no public API or affiliate "
                "route; Booking.com’s Demand API is approval-gated and unavailable "
                "pre-launch."
            )),
            cell(2000, run("Commercial / Product Owner")),
            cell(2050, run("Non-blocking. No v1 dependency — ADR 0013.")),
        ),
    )

    # ======================================================================
    # 7.4 Relationship register, 7.5.7, 7.5.8, 7.6
    # ======================================================================
    d.set_cell(
        "7.4 R13 note",
        "<w:t>One row per room-type per date</w:t>",
        para(run("One row per room-type per date. ") + run(DEFER, RB))
        + para(run("R12 (accommodation 1 : 1..* room_type) and R24's ", RS))
        + para(run("booking_accommodation subtype are deferred with it.", RS)),
    )
    # This runs before the fields-of-note edit: that edit's replacement text
    # contains the same "(HOTEL/..." phrase, and would make this anchor
    # ambiguous if it landed first.
    d.set_para(
        "7.5.7 room_type deferred",
        "(HOTEL/RESORT/LODGE/GUESTHOUSE/APARTMENT), ",
        run("Deferred to v2 — ADR 0013. ", PB)
        + run(
            "provider_id, star_rating, amenities, cancellation_policy_id and "
            "child_policy leave the v1 table with the subsystem: accommodation is a "
            "curated location record, not provider-listed supply, and a property that "
            "cannot be booked has nothing to cancel. The room_type specification "
            "below is preserved verbatim and is not implemented in v1; the table does "
            "not exist in the v1 schema. room_type:",
            PS,
        ),
    )
    d.set_para(
        "7.5.7 accommodation fields of note",
        '<w:t>accommodation</w:t></w:r><w:r>' + PS + '<w:t xml:space="preserve"> fields of note: ',
        run("accommodation", MONO)
        + run(" fields of note, as amended in v1.2 — ADR 0013: ", PS)
        + run(
            "provider_id, destination_id, property_type "
            "(HOTEL/RESORT/LODGE/GUESTHOUSE/APARTMENT), coordinates, address_line, "
            "check_in_time TIME, check_out_time TIME, is_active, deleted_at.",
            PS,
        ),
    )
    # A new paragraph before the constraints line, not appended to it: that
    # paragraph carries the §7.5.9 heading text too, so an append would land
    # after it and read as though it applied to activity_departure.
    d.before_para(
        "7.5.8 room_availability deferred",
        " enforced by trigger. ",
        body_para(
            "§7.5.8 is deferred to v2 — ADR 0013.",
            "room_availability does not exist in the v1 schema; the specification above "
            "is preserved for the day it returns. §7.5.9 below is unaffected: "
            "activity_departure is v1, and in v1 it is the only capacity counter.",
        ),
    )
    d.set_cell(
        "7.6 room_availability index",
        "<w:t>Availability scan; nightly sweeps</w:t>",
        para(run("Availability scan; nightly sweeps. ") + run(DEFER, RB)),
    )

    # ======================================================================
    # 8.4 transaction policy, 8.10 caching
    # ======================================================================
    d.set_cell(
        "8.4 hold critical section",
        "<w:t>Serialises the capacity check-anddecrement; PK ordering prevents deadlock</w:t>",
        para(
            run(
                "Serialises the capacity check-and-decrement; PK ordering prevents "
                "deadlock. Amended v1.2 — ADR 0013: activity_departure rows only in "
                "v1; room_availability returns with the accommodation subsystem."
            )
        ),
    )
    d.set_cell(
        "8.10 availability cache key",
        "<w:t>Availability search result</w:t>",
        para(run("Availability search result (activity departures in v1). ")
             + run(DEFER.replace("Deferred", "The room form is deferred"), RB)),
    )

    # ======================================================================
    # 9.2 envelope example, 9.4.3, 9.4.5
    # ======================================================================
    d.sub(
        "9.2 envelope message",
        '>"The selected room is no longer available for 12-14 Aug 2027."</w:t>',
        '>"This departure is no longer available."</w:t>',
    )
    d.sub(
        "9.2 envelope field path",
        '>"items[1].room_type_id"</w:t>',
        '>"items[1].departure_id"</w:t>',
    )
    d.append_para(
        "9.4.3 deferred",
        "Business rules applied: a room type appears only if every night in the range has ",
        run(" " + DEFER + " ", PB)
        + run(
            "GET /accommodations/{id}/room-types is not a v1 endpoint. GET "
            "/accommodations returns curated location records for a destination — name, "
            "property type, coordinates, address, check-in and check-out times — with no "
            "price and no availability, because a location record asserts neither.",
            PS,
        ),
    )
    d.set_para(
        "9.4.5 quote no longer locks room nights",
        "For each accommodation item: lock the ",
        run("Amended v1.2 — ADR 0013. ", PB)
        + run(
            "A STAY item is an anchor: it locks nothing, holds nothing and prices "
            "nothing, so the quote skips it entirely. The step this replaces — locking "
            "room_availability rows for every night, asserting capacity, incrementing "
            "rooms_held and creating an inventory_hold — returns with the subsystem in "
            "v2. The activity step below is unchanged.",
            PS,
        ),
    )

    # ======================================================================
    # 28.2.5 SD-05
    # ======================================================================
    d.append_para(
        "28.2.5 SD-05 deferred",
        "28.2.5 SD-05 Accommodation Booking (search to hold)",
        run("  " + DEFER, RB),
    )


def main() -> int:
    with zipfile.ZipFile(SRC) as z:
        names = z.namelist()
        infos = {i.filename: i for i in z.infolist()}
        blobs = {n: z.read(n) for n in names}

    d = Doc(blobs["word/document.xml"].decode("utf-8"))
    amend(d)
    for label in d.applied:
        print(f"  applied: {label}")
    print(f"{len(d.applied)} edits")
    blobs["word/document.xml"] = d.xml.encode("utf-8")

    backup = SRC.with_suffix(".docx.bak")
    shutil.copy2(SRC, backup)
    tmp = SRC.with_suffix(".docx.tmp")
    with zipfile.ZipFile(tmp, "w") as out:
        for n in names:
            src_info = infos[n]
            info = zipfile.ZipInfo(n, date_time=src_info.date_time)
            info.compress_type = src_info.compress_type
            info.external_attr = src_info.external_attr
            info.create_system = src_info.create_system
            out.writestr(info, blobs[n])
    tmp.replace(SRC)
    backup.unlink()
    print(f"patched {SRC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
