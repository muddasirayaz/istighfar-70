#!/usr/bin/env python3
"""hypothesis_proposer — turns a high-confidence correlation_scout finding
into a scoped, testable proposal a human can act on.

Reads Estate/out/correlations.jsonl (written by correlation_scout.py). For
each correlation at or above --min-confidence that doesn't already have a
hypothesis record, asks a cheap/fast model (default: claude-haiku-4-5,
same tier Fable 5 recommended for this role) to draft exactly four fields:
goal, scope, rollback_plan, expected_evidence. Lands in
Estate/out/hypotheses.jsonl -- a review queue, never auto-applied. Nothing
in this module runs git or writes outside that one file.

Fails CLOSED like commit_gate.py: no API key, a timeout, a malformed
response, or an empty correlation are all recorded as
status=generation_failed, never fabricated as a proposal. A correlation
that already has ANY hypothesis record (proposed or failed) is not
re-attempted automatically -- rerun with --retry-failed to force it.

STATUS FLOW (fail-closed human-approval gate, added 2026-07-09): a fresh
draft lands as status=proposed. autonomous_loop.py will NOT act on a
proposed hypothesis -- mutation_validator.spec_for_project() only returns
a spec once its status is human_approved. `approve`/`reject` are the two
ways a proposed record's status changes; there is no timeout-based or
automatic path from proposed to human_approved. A hypothesis stays
pending in the queue indefinitely until a human explicitly decides.

If a human-approved hypothesis is later implemented as an actual diff,
that diff MUST still clear the existing two-model commit_gate before
landing -- this module never bypasses it, and approving a hypothesis
grants it NO commit authority of its own. `hypothesis_proposer.py
commit-check` is the explicit hook: it calls commit_gate.review() with
the hypothesis's own goal+scope as the review description (not a generic
placeholder) and records APPROVE/REJECT against the hypothesis record, so
the review path is exercised through this module's plumbing rather than
assumed.

CLI:
  hypothesis_proposer.py run [--min-confidence 0.5] [--dry-run] [--model M] [--retry-failed]
  hypothesis_proposer.py list [--json]
  hypothesis_proposer.py status
  hypothesis_proposer.py approve <correlation_id> [--notes N]
  hypothesis_proposer.py reject <correlation_id> [--notes N]
  hypothesis_proposer.py commit-check <hypothesis_id> --diff-file PATH [--model M]
"""
import argparse
import json
import os
import sys
import time

ESTATE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ESTATE)
sys.path.insert(0, os.path.join(ESTATE, "agent"))
import steward       # noqa: E402
import commit_gate    # noqa: E402
import correlation_scout as scout  # noqa: E402

QUEUE = os.path.join(ESTATE, "out", "hypotheses.jsonl")
DEFAULT_MODEL = os.environ.get("ESTATE_HYPOTHESIS_MODEL", "claude-haiku-4-5-20251001")
DEFAULT_MIN_CONFIDENCE = 0.5

PROPOSER_SYSTEM_PROMPT = """You draft scoped, testable engineering proposals from a \
structural correlation detected in a personal solo-operator workspace called The \
Estate. You did not detect the correlation yourself -- you are only given its type, \
subjects, and evidence string. Do not invent facts not implied by the evidence.

Respond with JSON ONLY, no markdown fences, matching exactly this shape:
{
  "goal": "one sentence: what change would address this correlation",
  "scope": "one or two sentences: exactly what files/behavior would change, bounded",
  "rollback_plan": "one sentence: how to safely undo this if it goes wrong",
  "expected_evidence": "one sentence: the concrete check that would prove this worked"
}

If the evidence is too thin to responsibly propose a scoped change, respond with
exactly: {"insufficient_evidence": "<one sentence why>"}
Never propose anything destructive, never propose skipping tests, never propose a
change wider than the evidence justifies.
"""


def _now():
    return time.time()


def _append(record, path=None):
    path = path or QUEUE
    # abspath first: dirname of a bare relative filename is "", which makedirs rejects
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _read_all(path=None):
    path = path or QUEUE
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def fold(path=None):
    """Latest record per correlation_id (commit-check appends a follow-up
    record referencing the same correlation_id; last one wins)."""
    latest = {}
    for r in _read_all(path):
        latest[r["correlation_id"]] = r
    return latest


SPEC_FIELDS = ("goal", "scope", "rollback_plan", "expected_evidence")


def _decide(correlation_id, new_status, queue_path=None, notes=None):
    """Shared implementation for approve()/reject(): fail-closed status
    transition, only ever FROM 'proposed' -- there is no legitimate reason
    to approve/reject a generation_failed record (nothing was ever
    proposed) or a record already resolved via commit-check (the mutation
    already happened; re-deciding it now is meaningless). Copies the
    original spec fields forward so the approved/rejected record is a
    complete, self-contained spec -- fold()'s "latest record wins" means
    this record becomes what spec_for_project() sees, so it must carry the
    goal/scope/rollback_plan/expected_evidence itself, not just a status
    flip referencing the old record."""
    existing = fold(queue_path)
    hyp = existing.get(correlation_id)
    if hyp is None:
        raise KeyError("no hypothesis for correlation id: %s" % correlation_id)
    if hyp["status"] != "proposed":
        raise ValueError(
            "correlation %s is not awaiting a decision (current status: %s) -- "
            "only a 'proposed' hypothesis can be approved or rejected"
            % (correlation_id, hyp["status"]))
    record = {"correlation_id": correlation_id, "status": new_status, "created_at": _now()}
    for k in SPEC_FIELDS:
        record[k] = hyp.get(k)
    record["correlation_type"] = hyp.get("correlation_type")
    record["correlation_subjects"] = hyp.get("correlation_subjects")
    if notes:
        record["notes"] = notes
    _append(record, queue_path)
    return record


def approve(correlation_id, queue_path=None, notes=None):
    """Mark a proposed hypothesis human_approved. This is the ONLY status
    mutation_validator.spec_for_project() will act on -- see module
    docstring. Grants no commit authority: an approved diff still has to
    clear commit_gate like everything else."""
    return _decide(correlation_id, "human_approved", queue_path=queue_path, notes=notes)


def reject(correlation_id, queue_path=None, notes=None):
    """Mark a proposed hypothesis human_rejected. autonomous_loop will
    never act on it; it stays in the queue as a permanent record of the
    decision (append-only, same as every other status transition here)."""
    return _decide(correlation_id, "human_rejected", queue_path=queue_path, notes=notes)


def _parse_proposal(text):
    # snippet, not text[:200]: text can be None (a provider path that
    # returned nothing without raising), and slicing None would crash the
    # whole run instead of failing closed on just this record.
    snippet = (text or "")[:200]
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        return None, "model did not return valid JSON: %r" % snippet
    if not isinstance(data, dict):
        return None, "model JSON is not an object: %r" % snippet
    if "insufficient_evidence" in data:
        return None, "model declined: %s" % data["insufficient_evidence"]
    required = {"goal", "scope", "rollback_plan", "expected_evidence"}
    missing = required - set(data)
    if missing:
        return None, "model JSON missing required keys: %s" % sorted(missing)
    return {k: data[k] for k in required}, None


def draft(correlation, model=DEFAULT_MODEL, timeout=60, call_fn=None):
    """Returns (fields_dict_or_None, error_or_None). No side effects."""
    call_fn = call_fn or steward.call_provider
    user_content = json.dumps({
        "type": correlation["type"],
        "subjects": correlation["subjects"],
        "evidence": correlation["evidence"],
        "confidence": correlation["confidence"],
    }, sort_keys=True)
    messages = [
        {"role": "system", "content": PROPOSER_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    args = argparse.Namespace(provider="anthropic", model=model, timeout=timeout)
    try:
        raw = call_fn(args, messages)
    except Exception as e:
        return None, "proposer call failed: %s" % e
    return _parse_proposal(raw)


def run(min_confidence=DEFAULT_MIN_CONFIDENCE, model=DEFAULT_MODEL, dry_run=False,
        retry_failed=False, call_fn=None, corr_path=None, queue_path=None):
    correlations = scout._read_all(corr_path)
    existing = fold(queue_path)
    results = []
    for c in correlations:
        if c["confidence"] < min_confidence:
            continue
        prior = existing.get(c["id"])
        if prior is not None and not (retry_failed and prior["status"] == "generation_failed"):
            continue
        fields, err = draft(c, model=model, call_fn=call_fn)
        if fields is None:
            record = {
                "correlation_id": c["id"], "status": "generation_failed",
                "reason": err, "model": model, "created_at": _now(),
            }
        else:
            record = dict(fields)
            record.update({
                "correlation_id": c["id"], "status": "proposed",
                "model": model, "created_at": _now(),
                "correlation_type": c["type"], "correlation_subjects": c["subjects"],
            })
        results.append(record)
        if not dry_run:
            _append(record, queue_path)
    return results


def review_description(hyp):
    """Build the description commit_gate actually reviews against, from a
    hypothesis's own vetted goal+scope -- never a generic placeholder, and
    never freshly authored by whatever step is proposing the diff (the
    text traces back to hypothesis_proposer's own draft/approval, which
    was already vetted at draft/approval time, not invented at review
    time). Shared by check_commit() below and autonomous_loop.py so both
    call sites describe a diff to commit_gate the same way."""
    goal = hyp.get("goal") or "(no goal recorded)"
    scope = hyp.get("scope") or "(no scope recorded)"
    return "Goal: %s\nScope (should not exceed): %s" % (goal, scope)


def check_commit(hypothesis_correlation_id, diff_text, model=commit_gate.DEFAULT_MODEL,
                  queue_path=None):
    """Explicit wiring: any diff proposed for a hypothesis clears the SAME
    two-model commit_gate as autonomous_loop.py's own commits before it's
    recorded as reviewable -- never a separate, weaker check. Only a
    human_approved hypothesis may be commit-checked: the human gate comes
    strictly before implementation in the status flow, so reviewing a diff
    for a proposed/rejected/failed hypothesis is refused rather than
    letting a commit_reviewed_approved record imply vetting that never
    happened."""
    existing = fold(queue_path)
    hyp = existing.get(hypothesis_correlation_id)
    if hyp is None:
        raise KeyError("no hypothesis for correlation id: %s" % hypothesis_correlation_id)
    if hyp["status"] != "human_approved":
        raise ValueError(
            "correlation %s is not human_approved (current status: %s) -- a diff may "
            "only be commit-checked for a hypothesis a human has explicitly approved; "
            "an APPROVE here would otherwise mint a commit_reviewed_approved record "
            "for work that never cleared the human gate"
            % (hypothesis_correlation_id, hyp["status"]))
    description = review_description(hyp)
    result = commit_gate.review(diff_text, description, model=model)
    record = {
        "correlation_id": hypothesis_correlation_id,
        "status": "commit_reviewed_approved" if result.approved else "commit_reviewed_rejected",
        "reason": result.reason, "model": model, "created_at": _now(),
        "goal": hyp.get("goal"), "scope": hyp.get("scope"),
        "rollback_plan": hyp.get("rollback_plan"), "expected_evidence": hyp.get("expected_evidence"),
    }
    _append(record, queue_path)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="draft proposals for new high-confidence correlations")
    p_run.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    p_run.add_argument("--model", default=DEFAULT_MODEL)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--retry-failed", action="store_true",
                        help="re-attempt correlations whose last record was generation_failed")

    p_list = sub.add_parser("list", help="print the review queue")
    p_list.add_argument("--json", action="store_true")

    sub.add_parser("status", help="counts by status")

    p_commit = sub.add_parser("commit-check",
                               help="review an implemented diff via commit_gate before it lands")
    p_commit.add_argument("hypothesis_id", help="correlation id the hypothesis was drafted for")
    p_commit.add_argument("--diff-file", required=True)
    p_commit.add_argument("--model", default=commit_gate.DEFAULT_MODEL)

    p_approve = sub.add_parser("approve",
                                help="mark a proposed hypothesis human_approved -- "
                                     "the ONLY way autonomous_loop will act on it")
    p_approve.add_argument("correlation_id")
    p_approve.add_argument("--notes")

    p_reject = sub.add_parser("reject", help="mark a proposed hypothesis human_rejected")
    p_reject.add_argument("correlation_id")
    p_reject.add_argument("--notes")

    args = ap.parse_args()

    if args.cmd == "run":
        results = run(min_confidence=args.min_confidence, model=args.model,
                       dry_run=args.dry_run, retry_failed=args.retry_failed)
        verb = "would record" if args.dry_run else "recorded"
        print("hypothesis_proposer: %s %d result(s)" % (verb, len(results)))
        for r in results:
            print("  [%s] %s: %s" % (r["correlation_id"], r["status"],
                                      r.get("goal") or r.get("reason", "")))
    elif args.cmd == "list":
        records = _read_all()
        if args.json:
            print(json.dumps(records, indent=2, sort_keys=True))
        else:
            print("HYPOTHESIS QUEUE (%d record(s))" % len(records))
            for r in records:
                print("  [%s] (%s) %s" % (r["correlation_id"], r["status"],
                                           r.get("goal") or r.get("reason", "")))
    elif args.cmd == "status":
        by_status = {}
        for r in fold().values():
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        print("hypothesis_proposer: %d correlation(s) with a record — %s" %
              (sum(by_status.values()), ", ".join("%s=%d" % kv for kv in sorted(by_status.items()))))
    elif args.cmd == "commit-check":
        with open(args.diff_file) as f:
            diff_text = f.read()
        try:
            result = check_commit(args.hypothesis_id, diff_text, model=args.model)
        except (KeyError, ValueError) as e:
            sys.exit(str(e))
        print("APPROVE" if result.approved else "REJECT: %s" % result.reason)
        sys.exit(0 if result.approved else 1)
    elif args.cmd == "approve":
        try:
            record = approve(args.correlation_id, notes=args.notes)
        except (KeyError, ValueError) as e:
            sys.exit(str(e))
        print("human_approved %s (goal: %s)" % (args.correlation_id, record.get("goal")))
    elif args.cmd == "reject":
        try:
            record = reject(args.correlation_id, notes=args.notes)
        except (KeyError, ValueError) as e:
            sys.exit(str(e))
        print("human_rejected %s (goal: %s)" % (args.correlation_id, record.get("goal")))


if __name__ == "__main__":
    main()
