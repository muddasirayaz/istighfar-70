# GI Board Review Course

A single-file, offline-capable study app covering gastroenterology board content:
9 modules, 47 stations, each with objectives, expert-authored teaching panels
(accordions, reference tables, worked examples), an interactive quiz, and a
DOPS-style competency checklist. Progress is stored per-device in
`localStorage`. Open `index.html` in any modern browser — no build, no server,
no network.

> **Design scaffold.** Clinical content requires review by board-certified
> gastroenterologists before any teaching use. Content is tagged **EST**
> (established — from guidelines/literature) or **SYN** (synthesized — a worked
> example or integration to verify). This app's changes are structural and
> UX-only; no clinical facts, numbers, or thresholds were altered.

## What this pass fixed and added

**Correctness**
- Unified a duplicated progress store. `loadProgress`/`saveProgress` were each
  defined twice; the second pair (a `gi-progress` key that ignored its argument)
  silently won via hoisting, so quiz scores and checklist checks were written to
  a store nothing read. There is now one store (`…_v2`), argument-based.
- The router overwrote a station's record with `{visited:true}` on every visit,
  wiping quiz/checklist history. Visits now merge.
- The sidebar progress bar showed "built %" (always 100%). It now reflects the
  learner's real progress — stations visited / total — with a `N/47 visited`
  readout and a **Reset** control.
- Quiz best-scores now persist and show as a pill in the sidebar next to each
  station.

**Reachable content**
- Competency checklists were authored for all 47 stations but only tabbed into
  modules 4/5/7/8/9. A normalization pass wires the missing **Competency
  Checklist** tab (and guarantees the **Knowledge Check** tab) from the data, so
  the 22 checklists in modules 1/2/3/6 are now reachable.

**UX / UI**
- Responsive layout: off-canvas sidebar drawer with a hamburger toggle and
  backdrop below 900px; type/spacing adjustments below 560px.
- Sidebar **station search** that filters and auto-expands matching modules.
- **Keyboard navigation**: ← / → move to the previous/next station.
- An **EST/SYN legend** on the home page.
- Focus-visible outlines, ARIA labels/roles on nav and controls, and a
  `prefers-reduced-motion` guard.

## Verification

Driven end-to-end in headless Chromium (Playwright): no console/page errors;
progress advances on visit; the previously-orphaned checklist tabs render;
score pills, search, keyboard nav, and the mobile drawer all work.
