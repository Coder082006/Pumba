"""Extract a .docx to text with table structure preserved.

Unlike pdftotext, the OOXML holds the table grid explicitly, so a row is a row
and a cell is a cell. This is the lossless source for re-deriving SRS 6.4.
"""

from __future__ import annotations

import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def para_text(p: ET.Element) -> str:
    parts: list[str] = []
    for node in p.iter():
        if node.tag == f"{W}t":
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag == f"{W}br":
            parts.append("\n")
    return "".join(parts)


def cell_text(tc: ET.Element) -> str:
    return " ".join(
        para_text(p).strip() for p in tc.findall(f"{W}p")
    ).strip()


def walk(body: ET.Element) -> list[str]:
    out: list[str] = []
    for child in body:
        if child.tag == f"{W}p":
            out.append(para_text(child))
        elif child.tag == f"{W}tbl":
            out.append("<<<TABLE>>>")
            for tr in child.findall(f"{W}tr"):
                cells = [cell_text(tc) for tc in tr.findall(f"{W}tc")]
                out.append(" | ".join(cells))
            out.append("<<<END TABLE>>>")
    return out


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    with zipfile.ZipFile(src) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(f"{W}body")
    assert body is not None
    lines = walk(body)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"wrote {len(lines)} blocks to {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
