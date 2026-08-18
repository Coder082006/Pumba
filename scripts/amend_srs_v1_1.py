"""Amend the baselined SRS .docx in place for the C1 decision.

Every edit is an exact, unique string substitution asserted to fire once, and
the archive is rebuilt entry-by-entry from the original so that every part
except word/document.xml is byte-identical.

Applied once, on 2026-08-18, producing SRS v1.1. It is kept in the repository
because a diff of a .docx is unreadable in review, and this file is the only
precise record of what changed inside the binary. Re-running it fails its own
assertions by design: the anchors it looks for no longer exist.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path(sys.argv[1])
RSZ = '<w:rPr><w:sz w:val="18"/></w:rPr>'
NIL_BORDERS = (
    "<w:tcBorders><w:top w:val=\"nil\"/><w:left w:val=\"nil\"/>"
    "<w:bottom w:val=\"nil\"/><w:right w:val=\"nil\"/></w:tcBorders>"
)


def cell(width: int, text: str, para_id: str, justify: bool = False) -> str:
    jc = '<w:jc w:val="both"/>' if justify else ""
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{NIL_BORDERS}</w:tcPr>'
        f'<w:p w14:paraId="{para_id}" w14:textId="77777777" w:rsidR="00CF1EF7" '
        f'w:rsidRDefault="00000000"><w:pPr><w:spacing w:after="0"/>{jc}</w:pPr>'
        f'<w:r>{RSZ}<w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:tc>'
    )


def wide_cell(width: int, para_id: str, runs: str) -> str:
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>'
        f'<w:gridSpan w:val="2"/>{NIL_BORDERS}</w:tcPr>'
        f'<w:p w14:paraId="{para_id}" w14:textId="77777777" w:rsidR="00CF1EF7" '
        f'w:rsidRDefault="00000000"><w:pPr><w:spacing w:after="0"/></w:pPr>'
        f"{runs}</w:p></w:tc>"
    )


# --- 1. Revision history: a v1.1 row -----------------------------------------
REV_ANCHOR = "<w:t>Consolidated baseline release for development</w:t>"
REV_DESC = (
    "MVP client scope re-baselined on the tourist web application. Flutter tourist "
    "application deferred to v1.1; Flutter driver application retained in MVP. Amends "
    "§38.1, §38.2 and §41.10. Rationale and consequences in "
    "docs/adr/0002-web-first-tourist-client.md."
)
REV_ROW_NEW = (
    '<w:tr w:rsidR="00CF1EF7" w14:paraId="57BC0FA1" w14:textId="77777777">'
    '<w:trPr><w:trHeight w:val="381"/></w:trPr>'
    '<w:tc><w:tcPr><w:tcW w:w="1876" w:type="dxa"/><w:gridSpan w:val="2"/>'
    f"{NIL_BORDERS}</w:tcPr>"
    '<w:p w14:paraId="5CB496C1" w14:textId="77777777" w:rsidR="00CF1EF7" '
    'w:rsidRDefault="00000000"><w:pPr><w:tabs><w:tab w:val="center" w:pos="1243"/></w:tabs>'
    f'<w:spacing w:after="0"/></w:pPr><w:r>{RSZ}<w:t>1.1</w:t></w:r>'
    f"<w:r>{RSZ}<w:tab/><w:t>2026-08-18</w:t></w:r></w:p></w:tc>"
    + wide_cell(2330, "17071101", f"<w:r>{RSZ}<w:t>Product Owner</w:t></w:r>")
    + wide_cell(
        4819,
        "05148BE1",
        f'<w:r>{RSZ}<w:t xml:space="preserve">{REV_DESC}</w:t></w:r>',
    )
    + f'<w:tc><w:tcPr><w:tcW w:w="613" w:type="dxa"/>{NIL_BORDERS}</w:tcPr>'
    '<w:p w14:paraId="00444F01" w14:textId="77777777" w:rsidR="00CF1EF7" '
    'w:rsidRDefault="00CF1EF7"/></w:tc></w:tr>'
)

# --- 2. 38.1 Offline row: scope it to the client that can actually meet it ----
OFFLINE_OLD = (
    "<w:t>Confirmed itinerary, driver details, PIN and support number available offline</w:t>"
)
OFFLINE_NEW = (
    '<w:t xml:space="preserve">Confirmed itinerary, driver details, PIN and support number '
    "available offline in the Flutter tourist application (v1.1). For the tourist web "
    "application the equivalent guarantee is the emailed PDF itinerary of §41.10 as "
    "amended in v1.1.</w:t>"
)

# --- 3. 38.1 gains an explicit Clients row -----------------------------------
QUALITY_ROW_END = "<w:t>The NFR set in Section 29</w:t></w:r></w:p></w:tc></w:tr>"
CLIENTS_TEXT = (
    "Backend API, tourist web application, provider portal, admin console and the Flutter "
    "driver application. The driver application is not substitutable by mobile web: a "
    "90-second offer TTL requires high-priority push, and ARRIVED and COMPLETED transitions "
    "require background location inside a geofence. The Flutter tourist application is v1.1 "
    "(§38.2)."
)
CLIENTS_ROW = (
    '<w:tr w:rsidR="00CF1EF7" w14:paraId="0B791FE1" w14:textId="77777777">'
    '<w:trPr><w:trHeight w:val="253"/></w:trPr>'
    + cell(1766, "Clients", "66E78191")
    + cell(5651, CLIENTS_TEXT, "5A6262F1", justify=True)
    + "</w:tr>"
)

# --- 4. 38.2: web front end leaves, the Flutter companion arrives ------------
SHOULD_OLD = "a public web booking front end;"
SHOULD_NEW = (
    "the Flutter tourist companion application delivering offline itinerary access, live "
    "driver tracking, the pickup PIN and push notification (v1.1 — the public web "
    "booking front end moved into the MVP in v1.1 of this document);"
)

# --- 5. 41.10 gains the honest web-client statement --------------------------
OFFLINE_PARA_END = '<w:t xml:space="preserve"> TC-300, TC-301.</w:t></w:r></w:p>'
AMEND_TEXT = (
    " The offline acceptance criteria above apply to the Flutter tourist client. For the "
    "web client, the equivalent guarantee is delivered out-of-band: on trip confirmation "
    "the tourist receives an emailed PDF itinerary containing the full day-by-day plan, "
    "every booking voucher, driver identity and vehicle where assigned, pickup point, "
    "pickup PIN and the support number, plus downloadable calendar entries. The web "
    "application makes no offline guarantee and must not imply one in the interface."
)
AMEND_PARA = (
    '<w:p w14:paraId="2B232DD1" w14:textId="77777777" w:rsidR="00CF1EF7" '
    'w:rsidRDefault="00000000"><w:pPr><w:spacing w:after="279" w:line="260" '
    'w:lineRule="auto"/><w:ind w:left="-5" w:hanging="10"/></w:pPr>'
    '<w:r><w:rPr><w:b/><w:sz w:val="20"/></w:rPr><w:t>Amended v1.1.</w:t></w:r>'
    f'<w:r><w:rPr><w:sz w:val="20"/></w:rPr><w:t xml:space="preserve">{AMEND_TEXT}</w:t>'
    "</w:r></w:p>"
)

EDITS = [
    ("38.1 Offline row rescoped", OFFLINE_OLD, OFFLINE_NEW),
    ("38.1 Clients row added", QUALITY_ROW_END, QUALITY_ROW_END + CLIENTS_ROW),
    ("38.2 client list updated", SHOULD_OLD, SHOULD_NEW),
    ("41.10 amendment paragraph", OFFLINE_PARA_END, OFFLINE_PARA_END + AMEND_PARA),
]


def main() -> int:
    with zipfile.ZipFile(SRC) as z:
        doc = z.read("word/document.xml").decode("utf-8")
        names = z.namelist()
        infos = {i.filename: i for i in z.infolist()}
        blobs = {n: z.read(n) for n in names}

    # The revision row is inserted after the whole <w:tr> containing the anchor.
    assert doc.count(REV_ANCHOR) == 1, "revision anchor missing or not unique"
    row_end = doc.find("</w:tr>", doc.find(REV_ANCHOR)) + len("</w:tr>")
    doc = doc[:row_end] + REV_ROW_NEW + doc[row_end:]
    print("  applied: revision history v1.1 row")

    for label, old, new in EDITS:
        n = doc.count(old)
        assert n == 1, f"{label}: expected 1 occurrence of anchor, found {n}"
        doc = doc.replace(old, new, 1)
        print(f"  applied: {label}")

    blobs["word/document.xml"] = doc.encode("utf-8")

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
