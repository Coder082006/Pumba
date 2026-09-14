"""Amend the baselined SRS .docx in place for the ADR 0024 decision (v1.6).

ADR 0024 lets a tourist read every price in any currency in `currency.enabled`
while still being charged in the destination's own. §38.2 lists "multi-currency
presentment beyond USD and TZS" as a SHOULD-HAVE deferred past MVP, so showing
EUR and GBP at all exceeds the document as written. The ADR said so and named
the amendment v1.6; this is the amendment, which was declared and never made.

The paragraph added to §38.2 draws the line the ADR draws: display is in v1,
presentment and settlement are not. The exclusion sentence itself is left
untouched, because it remains true of what is charged.

Every edit is located by a plain-text anchor asserted to occur exactly once in
`word/document.xml`, and the archive is rebuilt entry-by-entry from the original
so that every part except `word/document.xml` is byte-identical.

    python scripts/amend_srs_v1_6.py "docs/srs/SRS-....docx"
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else None

RS = '<w:rPr><w:sz w:val="18"/></w:rPr>'  # table body

_para_id = 0x0A160000


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
        "revision history: v1.6",
        "docs/adr/0018-market-is-a-tier-between-country-and-region.md.",
        row(
            cell(1876, run("1.6") + f"<w:r>{RS}<w:tab/><w:t>2026-09-08</w:t></w:r>", span=2),
            cell(2330, run("Product Owner"), span=2),
            cell(
                4819,
                run(
                    "Display currency brought into v1: a tourist may read every price "
                    "in any currency listed in currency.enabled, beside the amount "
                    "actually charged, which remains destination.default_currency. "
                    "The converted figure is indicative, carries its rate, source and "
                    "as-of time, and is never summed, stored or charged. Presentment "
                    "and settlement in a chosen currency remain outside v1 beyond USD "
                    "and TZS and arrive with payments. Amends 38.2. Rationale in "
                    "docs/adr/0024-a-tourist-may-read-a-price-in-their-own-currency.md."
                ),
                span=2,
            ),
        ),
    )

    # ======================================================================
    # 38.2 SHOULD HAVE
    # ======================================================================
    _after_para(
        d,
        "38.2 display currency is in v1; presentment is not",
        "multi-currency presentment beyond USD and TZS",
        body_para(
            "Amended v1.6 - ADR 0024.",
            "Multi-currency presentment above means the currency a charge is made "
            "and settled in, which the payment provider must support. Display is "
            "not presentment and is in v1: every monetary figure shown to a tourist "
            "may carry an indicative conversion into any currency in "
            "currency.enabled, selected by X-Currency (9.1) or the tourist's "
            "preferred_currency, with its rate, source and as-of time. The trip is "
            "still priced, locked (BR-016) and charged in "
            "destination.default_currency; a converted figure is derived from a "
            "final amount, never summed from converted parts, and is never "
            "persisted. Where no rate is available the conversion is omitted rather "
            "than estimated.",
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
