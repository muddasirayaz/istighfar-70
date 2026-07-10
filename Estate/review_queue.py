#!/usr/bin/env python3
"""review_queue — renders the human-approval queue as a single static HTML
page, in the same visual language as the Estate dashboard.

The status flow's whole safety story rests on a human explicitly deciding
each proposed hypothesis (see hypothesis_proposer.py's docstring), but the
only way to see what's pending has been `hypothesis_proposer.py list` --
a flat CLI dump with no correlation context, no age, no sense of what's
been sitting unreviewed. This module is the reviewing human's surface:
one page showing everything awaiting judgment, everything approved but
not yet implemented, everything that failed to generate, and every
high-confidence correlation nothing has been drafted for yet -- each with
the exact command to act on it.

Same authority posture as correlation_scout: NO commit authority, no
model calls, no mutation of either ledger. Reads Estate/out/
correlations.jsonl and Estate/out/hypotheses.jsonl, writes exactly one
file (Estate/out/review_queue.html), touches nothing else. Rendering the
page never changes any record's status -- the approve/reject commands it
displays still have to be run by the human, through the same fail-closed
hypothesis_proposer plumbing as always.

CLI:
  review_queue.py render [--out PATH] [--min-confidence 0.5]
  review_queue.py status
"""
import argparse
import html
import os
import sys
import time

ESTATE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ESTATE)
import correlation_scout as scout      # noqa: E402
import hypothesis_proposer as proposer  # noqa: E402

OUT_HTML = os.path.join(ESTATE, "out", "review_queue.html")

# Mirrors hypothesis_proposer's status vocabulary exactly; anything else
# that ever appears in the queue lands in the "unknown" bucket and is shown
# rather than silently dropped (fail-closed for the reviewer's attention).
PENDING = "proposed"
APPROVED = "human_approved"
FAILED = "generation_failed"
RESOLVED = ("human_rejected", "commit_reviewed_approved", "commit_reviewed_rejected")

STATUS_COLORS = {
    "proposed": "#d29922",
    "human_approved": "#3fb950",
    "human_rejected": "#f85149",
    "generation_failed": "#8b949e",
    "commit_reviewed_approved": "#3fb950",
    "commit_reviewed_rejected": "#f85149",
}

STYLE = """
body{font:14px -apple-system,sans-serif;background:#0d1117;color:#e6edf3;margin:2rem auto;max-width:1180px;padding:0 1rem}
h1{font-size:1.5rem;margin-bottom:0} h2{font-size:1.1rem;margin-top:2.2rem;border-bottom:1px solid #21262d;padding-bottom:.4rem}
.sub{color:#8b949e} code{font-family:ui-monospace,monospace;font-size:.82rem}
.card{background:#161b22;border:1px solid #21262d;border-left:3px solid #d29922;border-radius:8px;padding:.8rem .9rem;margin-top:.8rem}
.card h3{margin:.1rem 0 .4rem;font-size:.95rem}
.badge{border-radius:1rem;padding:.05rem .55rem;font-size:.72rem;color:#0d1117;font-weight:700;vertical-align:middle}
.ev{color:#8b949e;font-size:.78rem}
.field{margin:.35rem 0;font-size:.85rem} .field b{color:#8b949e;font-size:.75rem;text-transform:uppercase;display:inline-block;min-width:9.5rem;vertical-align:top}
.cmd{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:.4rem .6rem;margin-top:.5rem;font-family:ui-monospace,monospace;font-size:.78rem;color:#79c0ff;overflow-x:auto}
.bar{display:inline-block;height:8px;background:#3fb950;border-radius:4px;vertical-align:middle}
table{border-collapse:collapse;width:100%;margin-top:.8rem}
td,th{border-bottom:1px solid #21262d;padding:.45rem .55rem;text-align:left;font-size:.85rem;vertical-align:top}
th{color:#8b949e;font-size:.75rem;text-transform:uppercase}
"""


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _age(created_at, now):
    if not created_at:
        return "?"
    seconds = max(0, now - created_at)
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        return "%dh" % (seconds // 3600)
    return "%dd" % (seconds // 86400)


def _badge(status):
    color = STATUS_COLORS.get(status, "#8b949e")
    return '<span class=badge style="background:%s">%s</span>' % (color, _esc(status))


def _conf_bar(confidence):
    width = int(round(80 * max(0.0, min(1.0, confidence or 0.0))))
    return '<span class=bar style="width:%dpx"></span> %.2f' % (width, confidence or 0.0)


def bucket(latest):
    """Split fold()'s latest-record-per-correlation view into reviewer
    buckets. Every record lands somewhere -- an unrecognized status goes to
    'unknown' so it is surfaced, never silently dropped."""
    buckets = {"pending": [], "approved": [], "failed": [], "resolved": [], "unknown": []}
    for rec in latest.values():
        status = rec.get("status")
        if status == PENDING:
            buckets["pending"].append(rec)
        elif status == APPROVED:
            buckets["approved"].append(rec)
        elif status == FAILED:
            buckets["failed"].append(rec)
        elif status in RESOLVED:
            buckets["resolved"].append(rec)
        else:
            buckets["unknown"].append(rec)
    for records in buckets.values():
        records.sort(key=lambda r: r.get("created_at") or 0)  # oldest first: longest-waiting on top
    return buckets


def undrafted(correlations, latest, min_confidence):
    """Correlations at/above the proposer threshold that have no hypothesis
    record at all -- the next `hypothesis_proposer.py run` will pick these up."""
    return [c for c in correlations
            if c.get("confidence", 0) >= min_confidence and c["id"] not in latest]


def _hypothesis_card(rec, corr_by_id, now, border="#d29922", commands=()):
    cid = rec["correlation_id"]
    corr = corr_by_id.get(cid, {})
    parts = ['<div class=card style="border-left-color:%s">' % border]
    parts.append("<h3>%s %s <span class=ev>%s · waiting %s</span></h3>"
                 % (_badge(rec.get("status")),
                    _esc(rec.get("correlation_type") or corr.get("type") or "?"),
                    _esc(cid), _age(rec.get("created_at"), now)))
    for label, key in (("Goal", "goal"), ("Scope", "scope"),
                       ("Rollback plan", "rollback_plan"),
                       ("Expected evidence", "expected_evidence")):
        if rec.get(key):
            parts.append("<div class=field><b>%s</b> %s</div>" % (label, _esc(rec[key])))
    if rec.get("reason"):
        parts.append("<div class=field><b>Reason</b> %s</div>" % _esc(rec["reason"]))
    if rec.get("notes"):
        parts.append("<div class=field><b>Notes</b> %s</div>" % _esc(rec["notes"]))
    if corr:
        parts.append("<div class=field><b>Correlation</b> <span class=ev>%s</span></div>"
                     % _esc(corr.get("evidence", "")))
        parts.append("<div class=field><b>Confidence</b> %s</div>" % _conf_bar(corr.get("confidence")))
    for cmd in commands:
        parts.append("<div class=cmd>%s</div>" % _esc(cmd))
    parts.append("</div>")
    return "".join(parts)


def render_html(correlations, latest, min_confidence=proposer.DEFAULT_MIN_CONFIDENCE, now=None):
    """Pure: ledgers in, HTML string out. No file writes here so tests and
    callers can render without touching Estate/out."""
    now = now if now is not None else time.time()
    corr_by_id = {c["id"]: c for c in correlations}
    buckets = bucket(latest)
    fresh = undrafted(correlations, latest, min_confidence)

    doc = ["<!doctype html><meta charset=\"utf-8\"><title>The Estate — Review Queue</title>",
           "<style>%s</style>" % STYLE,
           "<h1>Review Queue</h1>",
           "<div class=sub>Hypotheses awaiting your judgment. Rendering this page changes "
           "nothing — every decision below is a command you run yourself, through the same "
           "fail-closed <code>hypothesis_proposer.py</code> gate as always. Generated %s</div>"
           % time.strftime("%Y-%m-%d %H:%M", time.localtime(now))]

    doc.append("<h2>Awaiting decision (%d)</h2>" % len(buckets["pending"]))
    if buckets["pending"]:
        doc.append("<div class=sub>Oldest first. A hypothesis stays here indefinitely until "
                   "you explicitly approve or reject it — there is no automatic path out.</div>")
        for rec in buckets["pending"]:
            cid = rec["correlation_id"]
            doc.append(_hypothesis_card(rec, corr_by_id, now, border="#d29922", commands=(
                "python3 Estate/hypothesis_proposer.py approve %s --notes '<why>'" % cid,
                "python3 Estate/hypothesis_proposer.py reject %s --notes '<why>'" % cid,
            )))
    else:
        doc.append("<div class=sub>Nothing pending — the queue is clear.</div>")

    doc.append("<h2>Approved, not yet implemented (%d)</h2>" % len(buckets["approved"]))
    if buckets["approved"]:
        doc.append("<div class=sub>Approval grants NO commit authority: an implemented diff "
                   "still has to clear the two-model commit_gate via commit-check.</div>")
        for rec in buckets["approved"]:
            doc.append(_hypothesis_card(rec, corr_by_id, now, border="#3fb950", commands=(
                "python3 Estate/hypothesis_proposer.py commit-check %s --diff-file <path>"
                % rec["correlation_id"],
            )))
    else:
        doc.append("<div class=sub>None.</div>")

    doc.append("<h2>Generation failures (%d)</h2>" % len(buckets["failed"]))
    if buckets["failed"]:
        doc.append("<table><tr><th>Correlation</th><th>Reason</th><th>Age</th></tr>")
        for rec in buckets["failed"]:
            doc.append("<tr><td><code>%s</code></td><td class=ev>%s</td><td>%s</td></tr>"
                       % (_esc(rec["correlation_id"]), _esc(rec.get("reason", "")),
                          _age(rec.get("created_at"), now)))
        doc.append("</table>")
        doc.append("<div class=cmd>python3 Estate/hypothesis_proposer.py run --retry-failed</div>")
    else:
        doc.append("<div class=sub>None.</div>")

    doc.append("<h2>High-confidence correlations with no draft yet (%d)</h2>" % len(fresh))
    if fresh:
        doc.append("<table><tr><th>Type</th><th>Confidence</th><th>Evidence</th></tr>")
        for c in sorted(fresh, key=lambda c: -c.get("confidence", 0)):
            doc.append("<tr><td>%s</td><td>%s</td><td class=ev>%s</td></tr>"
                       % (_esc(c["type"]), _conf_bar(c.get("confidence")), _esc(c["evidence"])))
        doc.append("</table>")
        doc.append("<div class=cmd>python3 Estate/hypothesis_proposer.py run</div>")
    else:
        doc.append("<div class=sub>None at or above confidence %.2f.</div>" % min_confidence)

    doc.append("<h2>Decided &amp; resolved (%d)</h2>" % len(buckets["resolved"]))
    if buckets["resolved"]:
        doc.append("<table><tr><th>Status</th><th>Correlation</th><th>Goal</th><th>Age</th></tr>")
        for rec in reversed(buckets["resolved"]):  # most recent decision first
            doc.append("<tr><td>%s</td><td><code>%s</code></td><td class=ev>%s</td><td>%s</td></tr>"
                       % (_badge(rec.get("status")), _esc(rec["correlation_id"]),
                          _esc(rec.get("goal") or rec.get("reason") or ""),
                          _age(rec.get("created_at"), now)))
        doc.append("</table>")
    else:
        doc.append("<div class=sub>None yet.</div>")

    if buckets["unknown"]:
        doc.append("<h2>Unrecognized statuses (%d) — needs a look</h2>" % len(buckets["unknown"]))
        doc.append("<table><tr><th>Status</th><th>Correlation</th></tr>")
        for rec in buckets["unknown"]:
            doc.append("<tr><td>%s</td><td><code>%s</code></td></tr>"
                       % (_esc(rec.get("status")), _esc(rec.get("correlation_id"))))
        doc.append("</table>")

    return "\n".join(doc)


def render(out_path=None, min_confidence=proposer.DEFAULT_MIN_CONFIDENCE,
           corr_path=None, queue_path=None):
    out_path = out_path or OUT_HTML
    correlations = scout._read_all(corr_path)
    latest = proposer.fold(queue_path)
    page = render_html(correlations, latest, min_confidence=min_confidence)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(page)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="write the review page")
    p_render.add_argument("--out", default=OUT_HTML)
    p_render.add_argument("--min-confidence", type=float,
                          default=proposer.DEFAULT_MIN_CONFIDENCE,
                          help="threshold for the 'no draft yet' section — match what "
                               "you pass to hypothesis_proposer.py run")

    sub.add_parser("status", help="bucket counts, exit 0 always (advisory)")

    args = ap.parse_args()

    if args.cmd == "render":
        out = render(out_path=args.out, min_confidence=args.min_confidence)
        latest = proposer.fold()
        buckets = bucket(latest)
        print("review_queue: wrote %s — pending=%d approved=%d failed=%d resolved=%d"
              % (out, len(buckets["pending"]), len(buckets["approved"]),
                 len(buckets["failed"]), len(buckets["resolved"])))
    elif args.cmd == "status":
        buckets = bucket(proposer.fold())
        fresh = undrafted(scout._read_all(), proposer.fold(), proposer.DEFAULT_MIN_CONFIDENCE)
        print("review_queue: pending=%d approved=%d failed=%d resolved=%d unknown=%d undrafted=%d"
              % (len(buckets["pending"]), len(buckets["approved"]), len(buckets["failed"]),
                 len(buckets["resolved"]), len(buckets["unknown"]), len(fresh)))
        sys.exit(0)


if __name__ == "__main__":
    main()
