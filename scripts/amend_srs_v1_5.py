"""Amend the baselined SRS .docx in place for the ADR 0018 decision (v1.5).

§4.2's geographic model gains a level. `market` sits between `country` and
`region`, because the hierarchy as specified has no tier at which "Zanzibar"
and "Arusha" are peers — §4.2's own Region examples list two parts of Zanzibar
alongside the whole of Arusha — and the landing page's destination selector
needs exactly that tier.

§41.12's acceptance test gains a step and keeps its pass condition: the
administrator creates a market as well as a country, region and destination,
and creating one is still an INSERT rather than a migration.

Every edit is located by a plain-text anchor asserted to occur exactly once in
`word/document.xml`, and the archive is rebuilt entry-by-entry from the original
so that every part except `word/document.xml` is byte-identical.

    python scripts/amend_srs_v1_5.py "docs/srs/SRS-....docx"
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else None

RS = '<w:rPr><w:sz w:val="18"/></w:rPr>'  # table body

_para_id = 0x0A150000


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

        The guard is the same one `amend_srs_v1_2.py` documents: several
        sections that *read* as tables are loose tab-separated paragraphs, so a
        span that contains a second `open_tag` proves it walked back into the
        wrong element and would eat everything between.
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

    def after_row(self, label: str, anchor: str, xml: str) -> None:
        _, end = self._span(anchor, "<w:tr ", "</w:tr>")
        self.xml = self.xml[:end] + xml + self.xml[end:]
        self.applied.append(label)


PS = '<w:rPr><w:sz w:val="20"/></w:rPr>'  # running text
PB = '<w:rPr><w:b/><w:sz w:val="20"/></w:rPr>'  # running text, bold


def body_para(bold_lead: str, text: str) -> str:
    """A running-text paragraph led by a bold amendment marker."""
    return para(
        run(bold_lead, PB) + run(" " + text, PS),
        '<w:spacing w:after="120" w:line="260" w:lineRule="auto"/>'
        '<w:ind w:left="-5" w:hanging="10"/>',
    )


def _after_para(d: Doc, label: str, anchor: str, xml: str) -> None:
    _, end = d._span(anchor, "<w:p ", "</w:p>")
    d.xml = d.xml[:end] + xml + d.xml[end:]
    d.applied.append(label)


def amend(d: Doc) -> None:
    # ======================================================================
    # Revision history
    # ======================================================================
    d.after_row(
        "revision history: v1.5",
        "docs/adr/0017-ranking-uses-the-displayable-rating.md.",
        row(
            cell(1876, run("1.5") + f"<w:r>{RS}<w:tab/><w:t>2026-08-29</w:t></w:r>", span=2),
            cell(2330, run("Product Owner"), span=2),
            cell(
                4819,
                run(
                    "Geographic model extended to six levels: a market tier between "
                    "country and region, because the five-level model has no tier at "
                    "which Zanzibar and Arusha are peers - the Region examples in 4.2 "
                    "list two parts of Zanzibar alongside the whole of Arusha. A market "
                    "is listed in the destination selector on is_active alone and opens "
                    "its catalogue on launch_date, so an announced market is visible "
                    "while everything beneath it stays hidden. Amends 4.2 and 41.12. "
                    "Rationale, and why country and region were both rejected, in "
                    "docs/adr/0018-market-is-a-tier-between-country-and-region.md."
                ),
                span=2,
            ),
        ),
    )

    # ======================================================================
    # 4.2 Architectural Consequence: Destination Independence
    # ======================================================================
    _after_para(
        d,
        "4.2 geographic model gains a market tier",
        "The geographic model is a five-level hierarchy",
        body_para(
            "Amended v1.5 - ADR 0018.",
            "The hierarchy is six levels, not five: Country > Market > Region > "
            "Destination > Attraction / Accommodation > Activity. The Region examples "
            "above show why. “Zanzibar Urban/West” and “Zanzibar North” "
            "are parts of Zanzibar; “Arusha” is a whole. In the seeded catalogue "
            "Zanzibar is not a row at all - it is three regions, with Pemba contributing "
            "two more - so there is no level at which the two places a tourist chooses "
            "between are the same kind of thing. Country cannot serve, because both are "
            "TZ; region cannot, because 12.4 step 3 prices the metered transfer fallback "
            "per region and collapsing Zanzibar's three into one would erase that. "
            "A market carries name, slug, summary, is_active, launch_date, soft deletion "
            "and its own media gallery, and it is read through two distinct predicates. "
            "is_listed - active and not deleted, ignoring launch_date - governs "
            "appearance in the destination selector. is_open - active, not deleted, and "
            "launch_date reached - governs whether its catalogue is browsable. A market "
            "may therefore be announced on the landing page while every region, "
            "destination, attraction and activity beneath it remains absent from every "
            "public endpoint and from the sitemap, and returns 404 on direct URL. "
            "is_open is the 4.1 visibility rule applied to one more ancestor, not a "
            "second rule. launch_date consequently exists at two levels: on destination "
            "as specified above, and on market, which is the level “scheduled market "
            "launch” was always naming. The 12.4 tariff resolution ladder is "
            "unchanged; whether a market-level fallback belongs between its region and "
            "country steps is a pricing question for 12.4, recorded here and left open.",
        ),
    )

    # ======================================================================
    # 41.12 Destination Independence
    # ======================================================================
    _after_para(
        d,
        "41.12 acceptance test gains a market step",
        "no deployment, and no database migration is required",
        body_para(
            "Amended v1.5 - ADR 0018.",
            "The administrator creates a market as well, above the new region. The pass "
            "condition is unchanged and still holds: creating a market is an INSERT, not "
            "a migration. Zanzibar and Pemba are seeded as markets; Arusha is "
            "deliberately not, because this test is what creates it. Seeding Arusha "
            "would replace the criterion with a fixture.",
        ),
    )


def main() -> int:
    if SRC is None or not SRC.is_file():
        print(__doc__)
        return 2

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
