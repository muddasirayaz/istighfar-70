#!/usr/bin/env python3
"""correlation_scout — advisory-only daily pass that mines the Umran model +
Estate/out/* artifacts for cross-project signal a human hasn't consciously
named yet.

Six deterministic, non-LLM detectors (no model call in the detection path —
pattern-matching over data already on disk, consistent with the rest of
Estate's deterministic-first design):

  implicit_dependency   house A `needs` X, house B `exports` X, but no
                         connection (built or proposed) links B -> A.
  unused_abstraction     a tool in tools.umran tends only houses that are all
                         status parked/doc, or tends exactly one house despite
                         being a generalizable kind (harness/loop/workflow).
  shared_failure_mode    2+ distinct houses have `blocked` work items whose
                         notes share a significant keyword.
  repeated_code_shape    a distinctively-named file basename appears under
                         2+ house/utility paths with no existing `pattern`
                         connection between them.
  skill_reimplementation a house's file basenames + declared `vocab` overlap
                         a cataloged skill's name/description on 2+
                         significant tokens — the project may be
                         reimplementing what 00_Governance/Skills/Open_Standard
                         already covers as a reusable skill.
  archive_divergence     2+ archive files (.zip) under registered house/
                         utility paths share a normalized basename (one is a
                         "copy"/" 2"/"(1)" variant of the other) but their
                         sha256 content hashes differ — a silent fork of what
                         was meant to be the same archive, the exact failure
                         mode the 2026-07-08 architecture review found by
                         hand for bayt-al-hikma.zip vs "bayt-al-hikma 2.zip"
                         with no automated detector to catch a recurrence.

Fold semantics match promise_ledger.py: append-only JSONL, stable content
hash as id, a rerun only appends correlations not already in the ledger (no
duplicate spam on the daily cadence). This module has NO commit authority —
it writes exactly one file, Estate/out/correlations.jsonl, and touches
nothing else.

CLI:
  correlation_scout.py run [--dry-run]   detect + append new correlations
  correlation_scout.py list [--json]     print the full ledger
  correlation_scout.py status            counts, exit 0 always (advisory)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

ESTATE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(ESTATE)
sys.path.insert(0, ESTATE)
sys.path.insert(0, os.path.join(ESTATE, "umran"))
import umran  # noqa: E402

LEDGER = os.path.join(ESTATE, "out", "correlations.jsonl")
SKILLS_DIR = os.path.join(WORKSPACE, "00_Governance", "Skills", "Open_Standard")

# Generic tokens excluded from the skill-reimplementation overlap so two
# unrelated projects/skills that both happen to say "python" or "script"
# don't count as a match.
SKILL_MATCH_STOPWORDS = {
    "which", "these", "their", "there", "about", "before", "after", "should",
    "would", "could", "still", "using", "used", "into", "onto", "trigger",
    "triggers", "whenever", "instead", "rather", "always", "never", "skill",
    "project", "provided", "description", "unknown", "example", "examples",
}

# Directory names skipped during the repeated-code-shape file walk — vendor/
# build/asset trees that are expensive to walk and never hand-authored.
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "pods", "derivedata", "build",
    ".build", "dist", ".next", "venv", "env", ".venv", "library", "obj",
    "bin", ".dart_tool", "carthage", ".expo", ".gradle", "vendor", "target",
    ".cache", "coverage", "out",
}
# Bias the file-basename scan toward distinctively-named modules, not
# boilerplate (index.html, package.json, README.md, ...).
INTERESTING_TOKENS = (
    "harness", "engine", "extractor", "validator", "parser", "sync", "gate",
    "score", "scaffold", "pipeline", "digest", "ledger", "manifest",
    "watcher", "monitor", "reconcil", "normalizer", "detector",
)
# Generic basenames that match an interesting token but are near-universal
# boilerplate (every project has its own unrelated manifest.json) — verified
# during dry-run testing to be pure noise, not a real repeated code shape.
DENY_BASENAMES = {"manifest.json"}
MAX_FILES_PER_HOUSE = 2000  # hard cap so one huge (Unity/Xcode) tree can't stall the run

# Stopwords excluded from shared-failure-mode keyword co-occurrence — pure
# connective/grammatical tokens, not domain signal.
STOPWORDS = {
    "before", "after", "without", "cannot", "wont", "won't", "status",
    "operator", "session", "product", "unlocks", "unmet", "permanent",
    "precondition", "destabilizing", "reviewed", "review", "human", "gate",
    "place", "misreading", "enters", "signed", "re-signed",
}

# Archive-divergence detector: scoped to .zip only (the one real pattern the
# 2026-07-08 architecture review actually found) rather than guessing at a
# broader set of "data file" extensions with no observed real case yet.
ARCHIVE_EXTENSIONS = {".zip"}
# Matches a "copy" variant suffix immediately before the extension:
# "foo 2.zip", "foo(1).zip", "foo (1).zip", "foo copy.zip", "foo-copy.zip" —
# all normalize to "foo.zip" so they're compared against the same base name.
_ARCHIVE_COPY_RE = re.compile(
    r"^(?P<base>.*?)(?:[\s_-]*\(?\d+\)?|[\s_-]+copy)(?P<ext>\.[A-Za-z0-9]+)$",
    re.IGNORECASE,
)
# Hard cap so a single huge archive can't stall the daily scan hashing it —
# consistent in spirit with MAX_FILES_PER_HOUSE above. Files over this size
# are skipped (not flagged, not crashed on); they're rare enough among
# committed .zip archives that skipping is honest and safe.
MAX_HASH_BYTES = 500 * 1024 * 1024
# Generic archive basenames that are near-universal noise, not a real
# "meant to be the same archive" relationship -- verified against the live
# Estate model during dry-run testing: "files.zip"/"files (N).zip" is
# macOS's own auto-incrementing name for unrelated browser-downloaded zips
# that happen to land in different project directories, producing a
# 5-unrelated-house false positive with no genuine shared identity. Mirrors
# the DENY_BASENAMES convention above for the exact same reason.
ARCHIVE_DENY_NORMALIZED_NAMES = {"files.zip", "archive.zip", "download.zip", "downloads.zip"}


def _normalize_archive_name(basename):
    m = _ARCHIVE_COPY_RE.match(basename)
    if m:
        return (m.group("base").rstrip() + m.group("ext")).lower()
    return basename.lower()


def _hash_file(path):
    """sha256 of a file's content, chunked. Returns None (not an exception)
    if the file is too large to hash cheaply or unreadable -- the caller
    treats None as "cannot compare", not as a divergence."""
    try:
        if os.path.getsize(path) > MAX_HASH_BYTES:
            return None
    except OSError:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _now():
    return time.time()


def stable_id(corr):
    key = json.dumps({"type": corr["type"], "subjects": sorted(corr["subjects"]),
                       "evidence": corr["evidence"]}, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _append(corr, path=None):
    path = path or LEDGER
    # abspath first: dirname of a bare relative filename is "", which makedirs rejects
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(corr, sort_keys=True) + "\n")


def _read_all(path=None):
    path = path or LEDGER
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _existing_ids(path=None):
    return {c["id"] for c in _read_all(path)}


# ---- detectors -------------------------------------------------------

def detect_implicit_dependencies(model):
    """house A needs X, house B exports X, no connection B->A exists."""
    houses = model["house"]
    exports_index = {}  # export token -> set(house_id)
    for hid, h in houses.items():
        for exp in h.get("exports", []):
            exports_index.setdefault(exp, set()).add(hid)

    linked = set()
    for c in model["connection"].values():
        linked.add((c["from"], c["to"]))

    found = []
    for hid, h in houses.items():
        for need in h.get("needs", []):
            providers = exports_index.get(need, set()) - {hid}
            for provider in sorted(providers):
                if (provider, hid) in linked:
                    continue
                found.append({
                    "type": "implicit_dependency",
                    "subjects": sorted([provider, hid]),
                    "evidence": ("house '%s' needs '%s'; house '%s' exports '%s'; "
                                 "no connection '%s' -> '%s' declared in connections.umran"
                                 % (hid, need, provider, need, provider, hid)),
                    "confidence": 0.6,
                })
    return found


def detect_unused_abstractions(model):
    """tools whose entire tended set is dead, or single-house generalizable tools."""
    houses = model["house"]
    found = []
    generalizable_kinds = {"harness", "loop", "workflow"}
    for tid, t in model["tool"].items():
        tended = [h for h in t.get("tends", []) if h != "*"]
        if not tended:
            continue
        statuses = [houses[h]["status"] for h in tended if h in houses]
        if statuses and all(s in ("parked", "doc") for s in statuses):
            found.append({
                "type": "unused_abstraction",
                "subjects": sorted([tid] + tended),
                "evidence": ("tool '%s' (kind=%s) tends only %s, all status parked/doc — "
                             "abstraction may be stale/orphaned" % (tid, t.get("kind"), tended)),
                "confidence": 0.6,
            })
        elif len(tended) == 1 and t.get("kind") in generalizable_kinds:
            found.append({
                "type": "unused_abstraction",
                "subjects": sorted([tid] + tended),
                "evidence": ("tool '%s' (kind=%s) tends only one house (%s) despite being a "
                             "generalizable kind — candidate to widen, cf. call-pattern-gios "
                             "precedent (score_harness.mjs generalized to 8 modules)"
                             % (tid, t.get("kind"), tended[0])),
                "confidence": 0.4,
            })
    return found


def _tokenize(note):
    words = "".join(c if c.isalnum() else " " for c in note.lower()).split()
    return {w for w in words if len(w) >= 6 and w not in STOPWORDS}


def detect_shared_failure_modes(model):
    """2+ distinct houses blocked with notes sharing a significant keyword.

    One correlation per distinct house-set, keywords merged: two houses
    whose blocked notes share four significant words are ONE shared failure
    mode with four pieces of evidence, not four near-duplicate ledger
    entries each demanding its own hypothesis decision. (Grouping changes
    the evidence string, hence the stable id — an existing ledger keeps its
    old per-token records untouched, append-only as always; only newly
    detected correlations use the grouped shape.)"""
    by_token = {}  # token -> {house_id: note}
    for w in model["work"].values():
        if w.get("status") != "blocked":
            continue
        hid = w["house"]
        note = w.get("note", "")
        for tok in _tokenize(note):
            by_token.setdefault(tok, {})[hid] = note

    by_house_set = {}  # frozenset(house_ids) -> {"tokens": set, "notes": {house_id: note}}
    for tok, houses_notes in by_token.items():
        if len(houses_notes) < 2:
            continue
        key = frozenset(houses_notes)
        group = by_house_set.setdefault(key, {"tokens": set(), "notes": {}})
        group["tokens"].add(tok)
        group["notes"].update(houses_notes)

    found = []
    for key, group in by_house_set.items():
        hids = sorted(key)
        toks = sorted(group["tokens"])
        quotes = "; ".join("%s: \"%s\"" % (h, group["notes"][h][:100]) for h in hids)
        found.append({
            "type": "shared_failure_mode",
            "subjects": hids,
            "evidence": "blocked work in %s shares keyword%s %s — %s"
                        % (hids, "" if len(toks) == 1 else "s",
                           ", ".join("'%s'" % t for t in toks), quotes),
            "confidence": min(0.5 + 0.1 * (len(hids) - 2), 0.8),
        })
    return found


def _walk_basenames(path, exclude_abspath=None):
    """exclude_abspath skips a specific subtree entirely -- used to keep the
    skill catalog's own packaged files (which are literally named after
    skills) from self-matching in detect_skill_reimplementation when a
    house's path happens to contain 00_Governance/Skills."""
    basenames = {}  # basename -> True (existence only, per this house)
    visited = 0
    for root, dirs, files in os.walk(path):
        if exclude_abspath and os.path.abspath(root) == exclude_abspath:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if visited >= MAX_FILES_PER_HOUSE:
                return basenames
            visited += 1
            low = fn.lower()
            if low in DENY_BASENAMES:
                continue
            if any(tok in low for tok in INTERESTING_TOKENS):
                basenames[fn] = True
    return basenames


def _node_paths(model):
    nodes = {}
    for etype in ("house", "utility"):
        for eid, ent in model[etype].items():
            if "path" in ent:
                nodes[eid] = ent["path"]
    return nodes


_SKILLS_PACKAGE_DIR = os.path.dirname(SKILLS_DIR)  # .../00_Governance/Skills (the .skill zips + Open_Standard)


def _basename_index(model):
    """basename -> set(node_id) for every distinctive file under a house/utility path."""
    index = {}
    for nid, rel_path in _node_paths(model).items():
        abspath = umran.resolve_path(rel_path)
        if not os.path.isdir(abspath):
            continue
        try:
            for fn in _walk_basenames(abspath, exclude_abspath=_SKILLS_PACKAGE_DIR):
                index.setdefault(fn, set()).add(nid)
        except OSError:
            continue
    return index


def detect_repeated_code_shapes(model):
    """a distinctive file basename shows up under 2+ house/utility paths."""
    pattern_linked = set()
    for c in model["connection"].values():
        if c["kind"] == "pattern":
            pattern_linked.add(frozenset((c["from"], c["to"])))

    found = []
    for fn, nids in _basename_index(model).items():
        if len(nids) < 2:
            continue
        nids = sorted(nids)
        pairs_all_linked = all(
            frozenset((a, b)) in pattern_linked
            for i, a in enumerate(nids) for b in nids[i + 1:]
        )
        if pairs_all_linked:
            continue  # already declared as a pattern connection
        found.append({
            "type": "repeated_code_shape",
            "subjects": nids,
            "evidence": "file '%s' appears under %s with no declared 'pattern' connection" % (fn, nids),
            "confidence": 0.5,
        })
    return found


def _archive_file_index(model):
    """normalized_name -> list of (node_id, abspath) for every .zip found
    directly under a registered house/utility path (not a deep walk — the
    real observed case, bayt-al-hikma.zip, sits at a house's top level;
    scoped shallow to stay cheap and avoid vendored/build zip noise)."""
    index = {}
    for nid, rel_path in _node_paths(model).items():
        abspath = umran.resolve_path(rel_path)
        if not os.path.isdir(abspath):
            continue
        try:
            names = os.listdir(abspath)
        except OSError:
            continue
        for fn in names:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ARCHIVE_EXTENSIONS:
                continue
            full = os.path.join(abspath, fn)
            if not os.path.isfile(full):
                continue
            norm = _normalize_archive_name(fn)
            index.setdefault(norm, []).append((nid, full))
    return index


def detect_archive_divergence(model):
    """2+ .zip files that normalize to the same base name (a "copy" variant
    of each other) but whose sha256 content hashes differ -- a silent fork
    of what was meant to be one archive. Files that hash identically are not
    flagged (a copy that's still in sync is not a problem); files too large
    to hash cheaply are skipped, never guessed at."""
    found = []
    for norm_name, entries in _archive_file_index(model).items():
        if len(entries) < 2:
            continue
        if norm_name in ARCHIVE_DENY_NORMALIZED_NAMES:
            continue
        hashed = [(nid, path, _hash_file(path)) for nid, path in entries]
        hashed = [(nid, path, h) for nid, path, h in hashed if h is not None]
        distinct_hashes = {h for _, _, h in hashed}
        if len(distinct_hashes) < 2:
            continue  # all comparable copies are identical, or too few to compare
        subjects = sorted({nid for nid, _, _ in hashed})
        listing = "; ".join("%s: %s (sha256 %s)" % (nid, os.path.basename(path), h[:12])
                             for nid, path, h in hashed)
        found.append({
            "type": "archive_divergence",
            "subjects": subjects,
            "evidence": ("archive copies matching normalized name '%s' have diverged "
                         "(content hashes differ) — %s" % (norm_name, listing)),
            "confidence": 0.9,
        })
    return found


def _load_skill_corpus(skills_dir=None):
    """original_name -> set(significant tokens) from name + raw_text_snippet,
    read straight from the generated Open_Standard catalog (no .skill zip
    unpacking -- that's skill_exporter.py's job, not this one's)."""
    skills_dir = skills_dir or SKILLS_DIR
    corpus = {}
    if not os.path.isdir(skills_dir):
        return corpus
    for fn in sorted(os.listdir(skills_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(skills_dir, fn)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        meta = data.get("metadata", {})
        name = meta.get("original_name") or os.path.splitext(fn)[0]
        text = "%s %s %s" % (name, data.get("description", ""), meta.get("raw_text_snippet", ""))
        corpus[name] = _tokenize(text) - SKILL_MATCH_STOPWORDS
    return corpus


def _tool_id_tokens_by_house(model):
    """house_id -> set(tokens from the ids of tools already declared to tend
    it) -- used to suppress a skill match that's just the already-correct
    pairing of a house with its own dedicated tool (e.g. house 'call' and
    tool 'call-kernel-harness' sharing 'kernel'/'harness' is not a gap)."""
    by_house = {}
    for tid, t in model["tool"].items():
        tid_tokens = _tokenize(tid) - SKILL_MATCH_STOPWORDS
        for hid in t.get("tends", []):
            if hid != "*":
                by_house.setdefault(hid, set()).update(tid_tokens)
    return by_house


def detect_skill_reimplementation(model, skills_dir=None):
    """a house's repeated-code basenames + declared vocab overlap a cataloged
    skill on 2+ significant tokens -- candidate reimplementation-of-a-skill.
    Suppressed when the overlap is already explained by a tool already
    declared (tools.umran) to tend that same house -- that's a correctly
    wired pairing, not an undetected duplication."""
    corpus = _load_skill_corpus(skills_dir)
    if not corpus:
        return []
    houses = model["house"]
    basename_idx = _basename_index(model)
    tool_tokens_by_house = _tool_id_tokens_by_house(model)

    house_signal = {}  # house_id -> set(tokens) from its own repeated basenames + vocab
    for fn, nids in basename_idx.items():
        base_tokens = _tokenize(os.path.splitext(fn)[0]) - SKILL_MATCH_STOPWORDS
        for nid in nids:
            if nid in houses:
                house_signal.setdefault(nid, set()).update(base_tokens)
    for hid, h in houses.items():
        if h.get("vocab"):
            house_signal.setdefault(hid, set()).update(
                t for t in h["vocab"] if len(t) >= 5 and t not in SKILL_MATCH_STOPWORDS)

    found = []
    for hid, tokens in house_signal.items():
        already_wired = tool_tokens_by_house.get(hid, set())
        for skill_name, skill_tokens in corpus.items():
            shared = sorted(tokens & skill_tokens)
            if len(shared) < 2:
                continue
            skill_name_tokens = _tokenize(skill_name) - SKILL_MATCH_STOPWORDS
            if skill_name_tokens & already_wired:
                continue  # already correctly paired with an existing tool for this house
            found.append({
                "type": "skill_reimplementation",
                "subjects": sorted([hid, "skill:" + skill_name]),
                "evidence": ("house '%s' shares tokens %s with cataloged skill '%s' — "
                             "may be reimplementing what the skill already covers"
                             % (hid, shared, skill_name)),
                "confidence": min(0.4 + 0.1 * len(shared), 0.7),
            })
    return found


DETECTORS = [
    detect_implicit_dependencies,
    detect_unused_abstractions,
    detect_shared_failure_modes,
    detect_repeated_code_shapes,
    detect_skill_reimplementation,
    detect_archive_divergence,
]


def detect_all(model):
    found = []
    for fn in DETECTORS:
        found.extend(fn(model))
    return found


def run(dry_run=False, path=None):
    model = umran.parse_estate()
    candidates = detect_all(model)
    existing = _existing_ids(path)
    new = []
    for c in candidates:
        cid = stable_id(c)
        if cid in existing:
            continue
        c["id"] = cid
        c["detected_at"] = _now()
        new.append(c)
        existing.add(cid)
    if not dry_run:
        for c in new:
            _append(c, path)
    return new


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="detect + append new correlations")
    p_run.add_argument("--dry-run", action="store_true",
                        help="detect and print, do not write to the ledger")

    p_list = sub.add_parser("list", help="print the full ledger")
    p_list.add_argument("--json", action="store_true")

    sub.add_parser("status", help="counts by type")

    args = ap.parse_args()

    if args.cmd == "run":
        try:
            new = run(dry_run=args.dry_run)
        except umran.UmranError as exc:
            sys.exit("UMRAN REJECTED — correlation_scout refuses to run against an invalid model: %s" % exc)
        verb = "would append" if args.dry_run else "appended"
        print("correlation_scout: %s %d new correlation(s)" % (verb, len(new)))
        for c in new:
            print("  [%s] %s: %s" % (c["type"], c["subjects"], c["evidence"][:120]))
    elif args.cmd == "list":
        all_corr = _read_all()
        if args.json:
            print(json.dumps(all_corr, indent=2, sort_keys=True))
        else:
            print("CORRELATIONS (%d total)" % len(all_corr))
            for c in all_corr:
                print("  [%s] (%s, conf=%.1f) %s" %
                      (c["id"], c["type"], c["confidence"], c["evidence"][:120]))
    elif args.cmd == "status":
        all_corr = _read_all()
        by_type = {}
        for c in all_corr:
            by_type[c["type"]] = by_type.get(c["type"], 0) + 1
        print("correlation_scout: %d total — %s" %
              (len(all_corr), ", ".join("%s=%d" % kv for kv in sorted(by_type.items()))))
        sys.exit(0)


if __name__ == "__main__":
    main()
