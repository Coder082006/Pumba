"""Amend the baselined SRS .docx in place for the ADR 0016 decision (v1.3).

Appendix D gains **D9 — map tile provider**. Four tourist-web screens need a
map; Appendix D's D2 covers *routing* and §34.6 names routing and geocoding
vendors, but map tiles are named nowhere. The two are separately licensed and
separately priced even when one vendor sells both, so resolving D2 does not
resolve tiles.

This is a much smaller amendment than v1.2: one new row in Appendix D and one
revision-history row. Nothing is renumbered and nothing is deferred. D9 was the
free number — D8 (support operating hours) and D10 (Booking.com affiliate,
added by ADR 0013) already exist, and the register was never contiguous.

Every edit is located by a plain-text anchor asserted to occur exactly once in
`word/document.xml`, and the archive is rebuilt entry-by-entry from the original
so that every part except `word/document.xml` is byte-identical. This mirrors
`amend_srs_v1_2.py`, which is the precedent for editing the binary at all: a
.docx diff is unreadable in review, so the script is the record of what changed.

Re-running it fails its own assertions by design — the D9 row it inserts makes
its own anchor ambiguous on a second pass.

    python scripts/amend_srs_v1_3.py "docs/srs/SRS-....docx"
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


def amend(d: Doc) -> None:
    # ======================================================================
    # Revision history
    # ======================================================================
    d.after_row(
        "revision history: v1.3",
        "docs/adr/0013-accommodation-is-a-location-reference-in-v1.md.",
        row(
            cell(1876, run("1.3") + f"<w:r>{RS}<w:tab/><w:t>2026-08-23</w:t></w:r>", span=2),
            cell(2330, run("Product Owner"), span=2),
            cell(
                4819,
                run(
                    "Map tile provider added to the decisions register as D9. Tiles are "
                    "separately licensed from routing (D2) and are named nowhere in §34.6; "
                    "the tourist web client renders them with MapLibre GL JS against a tile "
                    "URL and attribution string held as system_setting rows, so the provider "
                    "is a configuration change rather than a deployment. Amends Appendix D. "
                    "Rationale and consequences in "
                    "docs/adr/0016-map-tiles-are-a-configured-url-behind-maplibre.md."
                ),
                span=2,
            ),
        ),
    )

    # ======================================================================
    # Appendix D — the decisions register
    #
    # Inserted after D8 so it lands before D10, which ADR 0013 added after the
    # same anchor. The register is ordered by number, not by date of addition.
    # ======================================================================
    d.after_row(
        "Appendix D9",
        "Support operating hours and escalation contacts",
        row(
            cell(600, run("D 9")),
            cell(
                5000,
                run(
                    "Map tile provider — which vendor serves the base map for the four "
                    "tourist-web screens that show one, and on what commercial terms. "
                    "Separate from D2: routing and tiles are separately licensed and "
                    "separately priced even where one vendor sells both. The client uses "
                    "MapLibre GL JS (BSD) against a tile URL template and attribution "
                    "string held in system_setting, so no code depends on the choice. The "
                    "development default is OpenStreetMap, whose tile usage policy does "
                    "not permit commercial production traffic."
                ),
            ),
            cell(2000, run("Commercial / Architecture")),
            cell(
                2050,
                run(
                    "Blocks production launch; does not block Phase 3 — ADR 0016."
                ),
            ),
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
