#!/usr/bin/env python3
"""Tests for the correlation_scout -> hypothesis_proposer -> review_queue
pipeline. Pure stdlib (unittest), no network, no model calls: steward,
commit_gate, and umran are stubbed via sys.modules before import, so this
file runs standalone -- outside the full Estate workspace -- which is
exactly the property that makes the fail-closed claims checkable anywhere.

Run:  python3 Estate/test_estate_pipeline.py
"""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

ESTATE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ESTATE)


# ---- sibling-module stubs (must exist before the imports below) --------

def _install_stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class UmranError(Exception):
    pass


def _refuse_network(*_args, **_kwargs):
    raise AssertionError("no model/provider call may happen inside tests")


class GateResult(object):
    def __init__(self, approved, reason):
        self.approved = approved
        self.reason = reason


umran_stub = _install_stub(
    "umran",
    UmranError=UmranError,
    parse_estate=_refuse_network,        # per-test override
    resolve_path=lambda p: p,            # tests pass absolute paths
)
_install_stub("steward", call_provider=_refuse_network)
commit_gate_stub = _install_stub(
    "commit_gate",
    DEFAULT_MODEL="stub-gate-model",
    review=_refuse_network,              # per-test override
)

import correlation_scout as scout        # noqa: E402
import hypothesis_proposer as proposer   # noqa: E402
import review_queue                       # noqa: E402


# ---- fixtures ----------------------------------------------------------

def model(**overrides):
    base = {"house": {}, "utility": {}, "tool": {}, "connection": {}, "work": {}}
    base.update(overrides)
    return base


def correlation(cid="c" * 16, confidence=0.9, ctype="archive_divergence",
                subjects=("a", "b"), evidence="two archives diverged"):
    return {"id": cid, "type": ctype, "subjects": sorted(subjects),
            "evidence": evidence, "confidence": confidence, "detected_at": 0.0}


VALID_PROPOSAL = json.dumps({
    "goal": "reconcile the diverged archives",
    "scope": "only the two zip files under house a and house b",
    "rollback_plan": "restore the pre-reconcile copies from git",
    "expected_evidence": "both archives hash identically after the change",
})


class TempLedgers(unittest.TestCase):
    """Base: every test gets throwaway corr/queue/out paths."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="estate-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.corr_path = os.path.join(self.tmp, "correlations.jsonl")
        self.queue_path = os.path.join(self.tmp, "hypotheses.jsonl")

    def write_correlations(self, *corrs):
        for c in corrs:
            scout._append(c, self.corr_path)

    def queue_records(self):
        return proposer._read_all(self.queue_path)


# ---- correlation_scout detectors ---------------------------------------

class DetectorTests(unittest.TestCase):

    def test_implicit_dependency_found_and_suppressed_by_connection(self):
        m = model(house={
            "a": {"status": "active", "needs": ["auth"]},
            "b": {"status": "active", "exports": ["auth"]},
        })
        found = scout.detect_implicit_dependencies(m)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["subjects"], ["a", "b"])

        m["connection"]["x"] = {"from": "b", "to": "a", "kind": "built"}
        self.assertEqual(scout.detect_implicit_dependencies(m), [])

    def test_unused_abstraction_dead_tended_set_and_lone_generalizable(self):
        m = model(
            house={"p": {"status": "parked"}, "d": {"status": "doc"},
                   "live": {"status": "active"}},
            tool={
                "stale-tool": {"kind": "agent", "tends": ["p", "d"]},
                "lone-harness": {"kind": "harness", "tends": ["live"]},
                "fine-tool": {"kind": "agent", "tends": ["live"]},
            },
        )
        found = {f["evidence"].split("'")[1]: f for f in scout.detect_unused_abstractions(m)}
        self.assertIn("stale-tool", found)
        self.assertAlmostEqual(found["stale-tool"]["confidence"], 0.6)
        self.assertIn("lone-harness", found)
        self.assertAlmostEqual(found["lone-harness"]["confidence"], 0.4)
        self.assertNotIn("fine-tool", found)

    def test_shared_failure_mode_groups_tokens_per_house_set(self):
        m = model(work={
            "w1": {"status": "blocked", "house": "a",
                   "note": "waiting on notarization certificate renewal"},
            "w2": {"status": "blocked", "house": "b",
                   "note": "notarization blocked until certificate arrives"},
            "w3": {"status": "blocked", "house": "c",
                   "note": "totally unrelated dependency conflict"},
        })
        found = scout.detect_shared_failure_modes(m)
        # 'notarization' and 'certificate' are both shared by {a, b}: ONE
        # grouped correlation, not one per token.
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["subjects"], ["a", "b"])
        self.assertIn("notarization", found[0]["evidence"])
        self.assertIn("certificate", found[0]["evidence"])
        self.assertAlmostEqual(found[0]["confidence"], 0.5)

    def test_shared_failure_mode_single_house_is_not_a_correlation(self):
        m = model(work={
            "w1": {"status": "blocked", "house": "a", "note": "notarization stuck"},
            "w2": {"status": "open", "house": "b", "note": "notarization stuck"},
        })
        self.assertEqual(scout.detect_shared_failure_modes(m), [])

    def test_normalize_archive_name_variants(self):
        cases = {
            "foo.zip": "foo.zip",
            "foo 2.zip": "foo.zip",
            "foo(1).zip": "foo.zip",
            "foo (1).zip": "foo.zip",
            "foo copy.zip": "foo.zip",
            "foo-copy.zip": "foo.zip",
            "FOO 2.ZIP": "foo.zip",
        }
        for raw, want in cases.items():
            self.assertEqual(scout._normalize_archive_name(raw), want, raw)


class ArchiveDivergenceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="estate-arch-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _house(self, name, files):
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        for fn, content in files.items():
            with open(os.path.join(path, fn), "wb") as f:
                f.write(content)
        return {"status": "active", "path": path}

    def test_diverged_copies_flagged_identical_copies_not(self):
        m = model(house={
            "a": self._house("a", {"pack.zip": b"ONE"}),
            "b": self._house("b", {"pack 2.zip": b"TWO"}),
            "c": self._house("c", {"same.zip": b"X"}),
            "d": self._house("d", {"same (1).zip": b"X"}),
        })
        found = scout.detect_archive_divergence(m)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["subjects"], ["a", "b"])
        self.assertAlmostEqual(found[0]["confidence"], 0.9)

    def test_deny_listed_basenames_never_flagged(self):
        m = model(house={
            "a": self._house("a", {"files.zip": b"ONE"}),
            "b": self._house("b", {"files (1).zip": b"TWO"}),
        })
        self.assertEqual(scout.detect_archive_divergence(m), [])


class ScoutRunTests(TempLedgers):

    def test_run_appends_once_then_dedups(self):
        m = model(house={
            "a": {"status": "active", "needs": ["auth"]},
            "b": {"status": "active", "exports": ["auth"]},
        })
        umran_stub.parse_estate = lambda: m
        first = scout.run(path=self.corr_path)
        self.assertEqual(len(first), 1)
        self.assertEqual(scout.run(path=self.corr_path), [])
        self.assertEqual(len(scout._read_all(self.corr_path)), 1)

    def test_stable_id_ignores_subject_order(self):
        a = {"type": "t", "subjects": ["x", "y"], "evidence": "e"}
        b = {"type": "t", "subjects": ["y", "x"], "evidence": "e"}
        self.assertEqual(scout.stable_id(a), scout.stable_id(b))


# ---- hypothesis_proposer ------------------------------------------------

class ParseProposalTests(unittest.TestCase):

    def test_accepts_fenced_json(self):
        fields, err = proposer._parse_proposal("```json\n%s\n```" % VALID_PROPOSAL)
        self.assertIsNone(err)
        self.assertEqual(set(fields), set(proposer.SPEC_FIELDS))

    def test_none_response_fails_closed_without_crashing(self):
        fields, err = proposer._parse_proposal(None)
        self.assertIsNone(fields)
        self.assertIn("valid JSON", err)

    def test_insufficient_evidence_and_missing_keys_fail_closed(self):
        fields, err = proposer._parse_proposal('{"insufficient_evidence": "too thin"}')
        self.assertIsNone(fields)
        self.assertIn("too thin", err)
        fields, err = proposer._parse_proposal('{"goal": "g"}')
        self.assertIsNone(fields)
        self.assertIn("missing required keys", err)


class ProposerRunTests(TempLedgers):

    def _run(self, call_fn, **kw):
        return proposer.run(call_fn=call_fn, corr_path=self.corr_path,
                            queue_path=self.queue_path, **kw)

    def test_valid_draft_lands_as_proposed(self):
        self.write_correlations(correlation())
        results = self._run(lambda args, messages: VALID_PROPOSAL)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "proposed")
        self.assertEqual(results[0]["goal"], "reconcile the diverged archives")

    def test_none_and_garbage_responses_record_generation_failed(self):
        self.write_correlations(correlation("a" * 16), correlation("b" * 16))
        calls = iter([None, "not json at all"])
        results = self._run(lambda args, messages: next(calls))
        self.assertEqual([r["status"] for r in results],
                         ["generation_failed", "generation_failed"])

    def test_below_threshold_and_already_recorded_are_skipped(self):
        self.write_correlations(correlation("a" * 16, confidence=0.3),
                                correlation("b" * 16, confidence=0.9))
        results = self._run(lambda args, messages: VALID_PROPOSAL)
        self.assertEqual([r["correlation_id"] for r in results], ["b" * 16])
        # second run: the recorded one is skipped, nothing new appears
        self.assertEqual(self._run(_refuse_network), [])

    def test_retry_failed_retries_only_failures(self):
        self.write_correlations(correlation("a" * 16), correlation("b" * 16))
        calls = iter([VALID_PROPOSAL, None])
        self._run(lambda args, messages: next(calls))
        retried = self._run(lambda args, messages: VALID_PROPOSAL, retry_failed=True)
        self.assertEqual([r["correlation_id"] for r in retried], ["b" * 16])
        self.assertEqual(retried[0]["status"], "proposed")


class DecisionFlowTests(TempLedgers):

    def _propose(self, cid="c" * 16):
        self.write_correlations(correlation(cid))
        proposer.run(call_fn=lambda a, m: VALID_PROPOSAL,
                     corr_path=self.corr_path, queue_path=self.queue_path)
        return cid

    def test_approve_carries_spec_fields_forward(self):
        cid = self._propose()
        rec = proposer.approve(cid, queue_path=self.queue_path, notes="lgtm")
        self.assertEqual(rec["status"], "human_approved")
        for k in proposer.SPEC_FIELDS:
            self.assertTrue(rec[k])
        latest = proposer.fold(self.queue_path)[cid]
        self.assertEqual(latest["status"], "human_approved")
        self.assertEqual(latest["goal"], "reconcile the diverged archives")

    def test_double_decision_and_unknown_id_are_refused(self):
        cid = self._propose()
        proposer.approve(cid, queue_path=self.queue_path)
        with self.assertRaises(ValueError):
            proposer.approve(cid, queue_path=self.queue_path)
        with self.assertRaises(ValueError):
            proposer.reject(cid, queue_path=self.queue_path)
        with self.assertRaises(KeyError):
            proposer.approve("nope", queue_path=self.queue_path)

    def test_reject_is_terminal_for_the_loop(self):
        cid = self._propose()
        rec = proposer.reject(cid, queue_path=self.queue_path, notes="too wide")
        self.assertEqual(rec["status"], "human_rejected")
        self.assertEqual(rec["notes"], "too wide")

    def test_commit_check_refused_unless_human_approved(self):
        cid = self._propose()
        commit_gate_stub.review = _refuse_network  # must not even be reached
        with self.assertRaises(ValueError):
            proposer.check_commit(cid, "diff --git a b", queue_path=self.queue_path)
        with self.assertRaises(KeyError):
            proposer.check_commit("nope", "diff --git a b", queue_path=self.queue_path)

    def test_commit_check_after_approval_uses_the_gate_and_records_verdict(self):
        cid = self._propose()
        proposer.approve(cid, queue_path=self.queue_path)
        seen = {}

        def fake_review(diff_text, description, model=None):
            seen["diff"] = diff_text
            seen["description"] = description
            return GateResult(True, "scoped and safe")

        commit_gate_stub.review = fake_review
        result = proposer.check_commit(cid, "diff --git a b",
                                       queue_path=self.queue_path)
        self.assertTrue(result.approved)
        # the gate reviewed against the hypothesis's own vetted goal+scope
        self.assertIn("reconcile the diverged archives", seen["description"])
        latest = proposer.fold(self.queue_path)[cid]
        self.assertEqual(latest["status"], "commit_reviewed_approved")
        self.assertEqual(latest["goal"], "reconcile the diverged archives")

    def test_commit_check_rejection_recorded(self):
        cid = self._propose()
        proposer.approve(cid, queue_path=self.queue_path)
        commit_gate_stub.review = lambda d, desc, model=None: GateResult(False, "too wide")
        result = proposer.check_commit(cid, "diff", queue_path=self.queue_path)
        self.assertFalse(result.approved)
        latest = proposer.fold(self.queue_path)[cid]
        self.assertEqual(latest["status"], "commit_reviewed_rejected")
        self.assertEqual(latest["reason"], "too wide")


# ---- review_queue -------------------------------------------------------

class ReviewQueueTests(TempLedgers):

    def _seed_exact(self):
        """Deterministic seed: drive each correlation's record directly so
        the test doesn't depend on run()'s iteration order."""
        cids = {}
        for name, ch, status in (("pending", "1", "proposed"),
                                 ("approved", "2", "proposed"),
                                 ("failed", "3", None),
                                 ("rejected", "4", "proposed")):
            cid = ch * 16
            cids[name] = cid
            self.write_correlations(correlation(cid, evidence="evidence for %s" % name))
            if status == "proposed":
                proposer.run(call_fn=lambda a, m: VALID_PROPOSAL,
                             corr_path=self.corr_path, queue_path=self.queue_path)
            else:
                proposer.run(call_fn=lambda a, m: None,
                             corr_path=self.corr_path, queue_path=self.queue_path)
        cids["undrafted"] = "5" * 16
        self.write_correlations(correlation(cids["undrafted"],
                                            evidence="evidence for undrafted"))
        proposer.approve(cids["approved"], queue_path=self.queue_path)
        proposer.reject(cids["rejected"], queue_path=self.queue_path)
        return cids

    def test_buckets_and_undrafted(self):
        cids = self._seed_exact()
        latest = proposer.fold(self.queue_path)
        buckets = review_queue.bucket(latest)
        self.assertEqual([r["correlation_id"] for r in buckets["pending"]], [cids["pending"]])
        self.assertEqual([r["correlation_id"] for r in buckets["approved"]], [cids["approved"]])
        self.assertEqual([r["correlation_id"] for r in buckets["failed"]], [cids["failed"]])
        self.assertEqual([r["correlation_id"] for r in buckets["resolved"]], [cids["rejected"]])
        fresh = review_queue.undrafted(scout._read_all(self.corr_path), latest, 0.5)
        self.assertEqual([c["id"] for c in fresh], [cids["undrafted"]])

    def test_render_writes_one_file_with_actionable_commands(self):
        cids = self._seed_exact()
        out = os.path.join(self.tmp, "out", "review_queue.html")
        written = review_queue.render(out_path=out, corr_path=self.corr_path,
                                      queue_path=self.queue_path)
        self.assertEqual(written, out)
        with open(out) as f:
            page = f.read()
        self.assertIn("approve %s" % cids["pending"], page)
        self.assertIn("reject %s" % cids["pending"], page)
        self.assertIn("commit-check %s" % cids["approved"], page)
        self.assertIn("evidence for undrafted", page)
        self.assertIn("reconcile the diverged archives", page)
        # rendering mutated neither ledger
        self.assertEqual(len(scout._read_all(self.corr_path)), 5)
        self.assertEqual(proposer.fold(self.queue_path)[cids["pending"]]["status"], "proposed")

    def test_render_escapes_ledger_content(self):
        cid = "e" * 16
        self.write_correlations(correlation(
            cid, evidence='<script>alert("x")</script> & more'))
        proposer.run(call_fn=lambda a, m: VALID_PROPOSAL,
                     corr_path=self.corr_path, queue_path=self.queue_path)
        page = review_queue.render_html(scout._read_all(self.corr_path),
                                        proposer.fold(self.queue_path))
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
