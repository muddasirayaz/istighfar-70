# The Estate — correlation → hypothesis → human gate pipeline

Modules for the advisory layer of The Estate: a deterministic scanner finds
cross-project signal, a cheap model drafts scoped proposals from it, and a
human — only a human — decides what proceeds. Nothing in this directory has
commit authority; every module writes exactly one ledger/output file and
touches nothing else.

## Pipeline

```
correlation_scout.py          deterministic detectors, no LLM in the detection path
        │  writes out/correlations.jsonl (append-only, stable content-hash ids)
        ▼
hypothesis_proposer.py run    cheap model drafts goal/scope/rollback/evidence
        │  writes out/hypotheses.jsonl        status: proposed | generation_failed
        ▼
review_queue.py render        static HTML review surface for the human gate
        │  writes out/review_queue.html       (read-only view; changes no record)
        ▼
hypothesis_proposer.py approve|reject         status: human_approved | human_rejected
        │  (the ONLY path forward — no timeout, no automatic promotion)
        ▼
  ... implementation happens elsewhere (autonomous_loop.py) ...
        ▼
hypothesis_proposer.py commit-check           two-model commit_gate.review()
           status: commit_reviewed_approved | commit_reviewed_rejected
```

Fail-closed invariants, in order of importance:

1. **A `proposed` hypothesis is inert.** `mutation_validator.spec_for_project()`
   only returns a spec once status is `human_approved`; there is no
   timeout-based or automatic path from `proposed` to `human_approved`.
2. **Approval grants no commit authority.** An implemented diff still clears
   the same two-model `commit_gate` as everything else, via `commit-check`.
3. **`commit-check` refuses anything not `human_approved`** — a diff for a
   merely-proposed or rejected hypothesis is refused outright, so a
   `commit_reviewed_approved` record can never imply vetting that never
   happened.
4. **Generation errors are recorded, never fabricated.** No API key, a
   timeout, a `None`/malformed response — all land as `generation_failed`.
5. **Ledgers are append-only.** Status transitions append a new record
   carrying the full spec forward; `fold()`'s latest-record-wins gives the
   current state while history stays intact.

## Files

| file | role | writes |
|---|---|---|
| `correlation_scout.py` | six deterministic detectors over the Umran model | `out/correlations.jsonl` |
| `hypothesis_proposer.py` | draft / approve / reject / commit-check | `out/hypotheses.jsonl` |
| `review_queue.py` | renders the human-approval queue as one HTML page | `out/review_queue.html` |
| `test_estate_pipeline.py` | stdlib-only tests; stubs `umran`/`steward`/`commit_gate`, runs anywhere | — |
| `docs/estate-dashboard-snapshot.html` | generated Estate dashboard snapshot (model `edccbafa3b2a`) | — |

Sibling modules referenced but not included here (they live in the full
Estate workspace): `umran.py`, `steward.py`, `commit_gate.py`,
`autonomous_loop.py`, `mutation_validator.py`, `promise_ledger.py`,
`zoning.py`, `diwan.py`, `skill_exporter.py`.

## Daily use

```sh
python3 Estate/correlation_scout.py run        # detect; append-only, rerun-safe
python3 Estate/hypothesis_proposer.py run      # draft for new high-confidence findings
python3 Estate/review_queue.py render          # regenerate the review page
open Estate/out/review_queue.html              # decide; run the commands shown
```

## Tests

```sh
python3 Estate/test_estate_pipeline.py
```

No network, no model calls (a stub raises if any code path tries), no
dependence on the surrounding workspace — the suite is the executable form
of the fail-closed claims above.
