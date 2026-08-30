"""Assert that a page's SEO surface is inside `<head>` — SRS §24.8.

Run before Lighthouse, against the same server it is about to measure:

    python3 scripts/check_head_metadata.py http://127.0.0.1:3000/explore ...

**Why "inside `<head>`" rather than "present".** The first version of this
check, written inline in the workflow, grepped the whole document — and passed,
on every run, while Lighthouse scored `meta-description` 0 on the same page.
Both were right. Next 15.2+ streams metadata for pages with an async
`generateMetadata`, writing the tags into the *body* on the assumption that the
client hoists them into `<head>`; measured against a production build with
headless Chrome, it does not. So the tag was in the document and absent from
the head, and a check that could not tell those apart was worse than no check:
it produced a green step immediately before a red one caused by the thing it
had just approved.

A guard that cannot fail for the reason the gate fails is not a guard.

Prints one annotation per URL with what it actually found — the status, the
byte count, the title and the description — whether it passes or not, because
the useful moment for that information is the run where something is wrong and
nobody yet knows what.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request

#: Long enough to cover a cold Next route compiling on a slow runner, short
#: enough that a hung server fails the job rather than the six-hour ceiling.
_TIMEOUT_SECONDS = 60

_HEAD_END = re.compile(r"</head\s*>", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DESCRIPTION = re.compile(
    r"""<meta[^>]*\bname\s*=\s*["']description["'][^>]*>""", re.IGNORECASE
)
_CONTENT = re.compile(r"""\bcontent\s*=\s*["'](.*?)["']""", re.IGNORECASE | re.DOTALL)


def head_of(html: str) -> str:
    """Everything up to `</head>`, or nothing if the document has no head.

    Returning empty rather than the whole document for a headless page is
    deliberate: a page that never closes its head has not passed this check,
    and falling back to searching everything is precisely the bug this file
    exists to correct.
    """
    end = _HEAD_END.search(html)
    return html[: end.start()] if end else ""


def check(url: str) -> list[str]:
    """Problems with one page. Empty means it is fine."""
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            status = response.status
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        print(f"::error::{url} returned {exc.code}.")
        return [f"{url} returned {exc.code}"]
    except OSError as exc:
        print(f"::error::{url} could not be fetched: {exc}")
        return [f"{url} unreachable"]

    head = head_of(html)
    title = _TITLE.search(head)
    description = _DESCRIPTION.search(head)
    content = _CONTENT.search(description.group(0)) if description else None

    print(
        f"::notice::{url} status={status} bytes={len(html)} "
        f"title={title.group(1).strip() if title else 'NONE'} "
        f"description={content.group(1).strip() if content else 'NONE'}"
    )

    problems: list[str] = []
    if status != 200:
        problems.append(f"status {status}")
    if not head:
        problems.append("no <head> element")
    if not title or not title.group(1).strip():
        problems.append("no <title> inside <head>")
    if not description:
        # The distinction that matters, so the message says which case it is.
        elsewhere = "; it is present later in the document" if _DESCRIPTION.search(html) else ""
        problems.append(f"no meta description inside <head>{elsewhere}")
    elif not content or not content.group(1).strip():
        problems.append("meta description is empty")

    for problem in problems:
        print(f"::error::{url}: {problem} (SRS §24.8).")
    return problems


def main(urls: list[str]) -> int:
    if not urls:
        print("::error::check_head_metadata.py needs at least one URL.")
        return 2
    return 1 if any(check(url) for url in urls) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
