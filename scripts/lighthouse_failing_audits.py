"""Name the Lighthouse audits that cost a category its points.

`lhci assert` reports a category score — "categories.seo failure for minScore
assertion, expected >=1, found 0.92". That names a category, not a defect. It
is the difference between knowing the gate is red and knowing what to fix, and
on this repository the gap is expensive: job logs need repository-admin auth to
read and artifact downloads need a token, so a contributor without credentials
has no way to open the reports at all. GitHub *annotations* are public, and
this script writes to that channel.

Run against the directory `lhci collect` writes:

    python3 scripts/lighthouse_failing_audits.py .lighthouseci

Every audit that carries weight in a failing category and scored below 1 is
printed as a `::error::` annotation, with its URL, its id, its score and its
weight. The weight matters as much as the score: a category loses points in
proportion to it, so a 0-scoring audit with weight 1 out of a total of 13 is
the whole of a 0.92, and knowing that is what makes the arithmetic check out
rather than merely look plausible.

Audits with no weight are skipped. Lighthouse carries a large number of them
as informational or manual entries, and they never move a score — listing them
would bury the one line that matters.

**A null score is reported when, and only when, the audit errored.** Lighthouse
uses `score: null` for two opposite situations, distinguished only by
`scoreDisplayMode`: `notApplicable` means the audit did not apply and is
dropped from the weighted average, while `error` means it could not run and is
counted as zero. They are identical in the JSON apart from that one field, and
treating them alike is how the first version of this script reported a failing
SEO category containing no failing SEO audits.
"""

from __future__ import annotations

import glob
import json
import os
import sys

#: Lighthouse rounds a displayed category score to two places, so an audit can
#: score fractionally below 1 without being a failure anybody should chase.
_PASS = 1.0


def failing_audits(report: dict) -> list[str]:
    """Annotation lines for one Lighthouse report."""
    url = report.get("finalDisplayedUrl") or report.get("requestedUrl") or "(unknown url)"
    audits = report.get("audits", {})
    lines: list[str] = []

    for category in report.get("categories", {}).values():
        score = category.get("score")
        if score is None or score >= _PASS:
            continue
        for ref in category.get("auditRefs", []):
            weight = ref.get("weight", 0)
            if not weight:
                continue
            audit = audits.get(ref["id"], {})
            audit_score = audit.get("score")
            mode = audit.get("scoreDisplayMode", "")

            # A null score means one of two opposite things, and conflating
            # them is how this script missed the audit it was written to find.
            #
            #   notApplicable — the audit did not apply to this page. It is
            #                   dropped from the weighted average entirely and
            #                   costs nothing.
            #   error         — the audit could not run. Lighthouse counts it
            #                   as **zero**, so it costs the category its full
            #                   weight while looking, in the JSON, exactly like
            #                   the harmless case.
            #
            # The first version of this file skipped every null score, and so
            # reported a failing SEO category with no failing SEO audits in it.
            if audit_score is None and mode != "error":
                continue
            if audit_score is not None and audit_score >= _PASS:
                continue

            detail = audit.get("title", "")
            if mode == "error":
                detail = (
                    f"AUDIT ERRORED (counts as zero): "
                    f"{audit.get('errorMessage') or detail}"
                )
            lines.append(
                f"::error::{url} — {category.get('title', '?')}: "
                f"audit '{ref['id']}' scored {audit_score} (weight {weight}). "
                f"{detail}".strip()
            )
    return lines


def main(directory: str) -> int:
    paths = sorted(glob.glob(os.path.join(directory, "lhr-*.json")))
    if not paths:
        print(f"::warning::No Lighthouse reports found in {directory}.")
        return 0

    for path in paths:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
        for line in failing_audits(report):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ".lighthouseci"))
