"""Amend the baselined SRS .docx in place for the ADR 0017 decision (v1.4).

§16.5's fourth ORDER BY term becomes the *displayable* rating: a subject with
fewer than `review.min_display_count` published reviews ranks as unrated rather
than on a mean BR-127 forbids showing. Ranking and display become the same rule,
so a single five-star review can no longer buy top placement on a page that
reads "New".

The ORDER BY block is a monospace paragraph split across two dozen runs, so it
is **annotated after the fact rather than rewritten** — the same treatment
`amend_srs_v1_2.py` gives sections it marks in place, and for the same reason:
rebuilding a run-split block risks losing formatting that nothing checks.

Every edit is located by a plain-text anchor asserted to occur exactly once in
`word/document.xml`, and the archive is rebuilt entry-by-entry from the original
so that every part except `word/document.xml` is byte-identical.

    python scripts/amend_srs_v1_4.py "docs/srs/SRS-....docx"
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else None

RS = '<w:rPr><w:sz w:val="18"/></w:rPr>'  # table body

_para_id = 0x0A140000


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
        "revision history: v1.4",
        "docs/adr/0016-map-tiles-are-a-configured-url-behind-maplibre.md.",
        row(
            cell(1876, run("1.4") + f"<w:r>{RS}<w:tab/><w:t>2026-08-23</w:t></w:r>", span=2),
            cell(2330, run("Product Owner"), span=2),
            cell(
                4819,
                run(
                    "§16.5's rating term gated by BR-127's display threshold: a subject "
                    "with fewer than review.min_display_count published reviews ranks as "
                    "unrated rather than on a mean that may not be displayed, so ranking and "
                    "display are the same rule. Amends §16.5. Rationale, and why a "
                    "confidence-weighted mean was not used, in "
                    "docs/adr/0017-ranking-uses-the-displayable-rating.md."
                ),
                span=2,
            ),
        ),
    )

    # ======================================================================
    # 16.5 Deterministic Catalogue Ranking
    # ======================================================================
    _after_para(
        d,
        "16.5 rating term gated by BR-127",
        "The tourist may override with an explicit ",
        body_para(
            "Amended v1.4 — ADR 0017.",
            "The rating term is the displayable rating, not the raw mean: it reads "
            "CASE WHEN rating_count >= :min_display_count THEN rating_avg ELSE NULL END "
            "DESC NULLS LAST. BR-127 already forbids displaying a mean for a subject with "
            "fewer than three published reviews; ranking on the figure while hiding it let a "
            "single five-star review take first place on a listing that reads “New”. "
            "Ranking and display are now the same rule, and the threshold is the same "
            "review.min_display_count system_setting. The published explanation of placement "
            "becomes “rating, once a subject has enough published reviews to have one”. "
            "A confidence-weighted mean was considered and rejected: it depends on a global "
            "average that drifts as reviews arrive, which the §9.1 keyset cursor cannot "
            "encode without silently skipping rows between pages.",
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
