# Spindle staging validation — plan

Record of record for scope/schema decisions — update as decisions
change, don't let it drift out of sync with reality (same convention
as `nrem_reclass_project/PLAN.md`). Rewritten clean 2026-09-19 after a
real methodology redesign (blind periodic sampling, reclassification-
aware transitions, 8-dataset scope) made the incremental-patch history
harder to follow than the current state itself — see HANDOFF.md for
the session-by-session history, including the original (now
superseded) sighted/fixed-position methodology this replaced.

## What this validates, and how it differs from `nrem_reclass_project/`

`nrem_reclass_project/` validates the gap-fill *reclassification rule*
in isolation (does the rule correctly turn artifact-adjacent NREM into
Wake). This project validates the **full pipeline's** staging — raw
SPINDLE, gap-fill reclassification, and the EMG-RMS gate when it
applies — against independent human judgment. Kept as a separate
project folder (not a new goal in `nrem_reclass_project/`) because
it's infrastructure-level, not scoped to the reclassification
investigation specifically. Reuses `lib.nrem_reclass` (now including
`compute_full_reclass_pipeline`, added for this project) and the
`ebb_viewer` EDF viewer.

## Decisions locked in

### Scope: 8 datasets, a hand-picked subset of files each

`PHP_pre`, `PHP_6_week`, `SRA_pre`, `SRA_6_week`, `RNA_KO`,
`Stxbp1_characterization`, `vgat` (`Stxbp1-vgat_CKO`), `vglut`
(`Stxbp1-vglut2_CKO`) — registered in `datasets.py` (one shared dict,
not duplicated per script anymore now that there are 8 of them).
`file_manifest.csv` currently has one animal per dataset (picked by
hand — familiar/well-characterized ones where available, e.g. the
same `RNA_KO`/`Mice-C414` and `Stxbp1_characterization`/`Mice-1254`
used earlier); intended to expand with more animals per dataset over
time, added to `file_manifest.csv` by hand as needed — no fixed target
count decided yet.

### Periodic census: blind, randomly-positioned within each 4h segment

One 4-hour segment per file starting at recording start (0h, 4h, 8h,
...); within each segment, a 5-minute window at a **random** position
(not fixed at the segment start) gets fully annotated, every epoch,
all states. **Blind**: no algorithm state is shown until after a
verdict is recorded — a genuine spot-check of SPINDLE's staging
shouldn't anchor on the algorithm's own call. (Superseded an earlier
sighted, fixed-position version — see HANDOFF.md; all of that data was
discarded, not migrated, since it isn't comparable to this
methodology.)

Randomness is **seeded per `window_id`**, not system entropy —
rerunning `select_periodic_windows.py` after `file_manifest.csv`
changes never moves an already-selected (and possibly already
mid-annotation) window, only adds new ones. Verdict: `Wake` / `NREM` /
`REM` / `Unsure` (keys 1-4).

### Transitions: selected and shown on the FINAL pipeline state, sighted

Transitions are detected on the state *after* the full reclassification
pipeline (gap-fill, then the EMG-RMS gate when it applies to that
file) — not SPINDLE's raw state. The question this validates is
whether the algorithm's claimed transition, after everything this
project does to the raw output, is real — so that's the state that has
to be sampled and shown. ~100 epochs/file: 45 NREM→Wake, 45 Wake→NREM,
~10 REM-involving (either direction, not split further), evenly spread
across the recording (not randomly sampled — transitions are already
sparse events, so index-evenly-spaced sampling within each type is
what keeps coverage spread across the whole file).

**Sighted**, unlike periodic: the viewer widens to show 2 epochs
before the target and 2 after (16s total, final-pipeline-state
labeled) as context for judging the claimed transition — only the
target epoch is highlighted, the neighbors are context, not additional
annotation targets.

**Verdict**: `Transition` / `No transition` / `Transition (wrong
states)` / `Unsure` (keys 1-4). The third option is for "yes, a real
transition happens here, but the algorithm has the wrong states for
it" — two dropdowns (`Actually: [from] → [to]`, Wake/NREM/REM) appear
whenever a transition-sourced target is showing, and must both be set
before that verdict can be recorded (validated at record time, not
silently defaulted).

### EMG gate: reused exactly, computed fresh per selected file

Per file: gap-fill reclassify, check whether the light-period NREM
bout count increased by more than 10%, and if so apply the validated
EMG-RMS gate (`k=5`, `lib.nrem_reclass.DEFAULT_EMG_GATE_K`) — identical
trigger and gate to `nrem_reclass_project`. Implemented as
`lib.nrem_reclass.compute_full_reclass_pipeline()`, computed fresh for
every file in scope (not reusing `nrem_reclass_project`'s precomputed
bucket assignments, since the file scope here only partly overlaps and
some datasets are new to any reclassification analysis).

### Output columns, both periodic and transition rows

Every recorded row carries `algorithm_rawstate` (SPINDLE), `algorithm_gapfill_state`
(post gap-fill), `algorithm_emg_gate_state` (post EMG gate — **blank**
if that file's bout-count increase didn't trigger the gate, not a
repeat of the gap-fill value). Transition rows additionally carry
`algorithm_pre_state`/`algorithm_post_state` (the final-state
transition being judged) and, only for the "wrong states" verdict,
`corrected_pre_state`/`corrected_post_state`.

### Two raters, split not duplicated; reconcile is always sighted

Unchanged from the original design: each target assigned to exactly
one rater via a stable hash of its id (not a shuffle); `Unsure`
epochs get reconciled together in a joint `MODE="reconcile"` session
later, not full double-annotation. Reconcile always shows the
algorithm's state regardless of whether the target came from (blind)
periodic or (sighted) transition — two people resolving an `Unsure`
case together benefit from seeing the algorithm's read; that doesn't
compromise periodic's *independent first-pass* judgment, which is what
the blindness is actually protecting.

### Results storage: one file per rater per mode, unchanged

`results/periodic_{A,B}.csv`, `results/transition_{A,B}.csv`,
`results/reconciled.csv` — see storage schema below. Still the right
choice at 8-dataset scope for the same reason as before: the real
concurrent-write safety boundary is the rater, not the target, so
one-file-per-target would just be thousands of near-empty files for no
added safety. A results file's header is now auto-migrated
(`_ensure_schema`, in `annotate_spindle_validation.py`) before every
write if it doesn't match the current column list — a real corruption
this project hit once (see HANDOFF.md) is now structurally prevented,
not just fixed that one time.

## Storage schema

`datasets.py` — the dataset registry: `DATASETS` (dataset key ->
project root `Path`), `RATERS` (`("A", "B")`). Imported by both
`select_*.py` scripts and `annotate_spindle_validation.py`.

`file_manifest.csv` — one row per file in scope: `dataset, animal_id,
combo`. Progress isn't tracked here (a `status` column was dropped
2026-09-21) -- `progress.py [A|B]` computes it from `results/`. `combo` is the SPINDLE channel combo (`LFC_RSC` for every file
currently in scope — confirmed available for all 8 datasets).

`targets/periodic_windows.csv` (regenerated by
`select_periodic_windows.py`, pure function of `file_manifest.csv` +
the interval/window-length constants + each window_id's seeded
randomness): `window_id, dataset, animal_id, combo, segment_start_sec,
window_start_sec, window_end_sec, window_start_clock, n_epochs,
assigned_rater`. `segment_start_sec` is the nominal 4h-aligned segment
boundary; `window_start_sec` is the actual (randomly-positioned-within-
that-segment) window start — kept as two separate columns so the
random offset is directly inspectable.

`targets/transition_epochs.csv` (regenerated by
`select_transition_epochs.py`): `epoch_id, dataset, animal_id, combo,
transition_type, epoch_idx, epoch_start_sec, pre_state, post_state,
algorithm_rawstate, algorithm_gapfill_state, algorithm_emg_gate_state,
algorithm_is_artifact, emg_gate_applicable, assigned_rater`.
`pre_state`/`post_state` are the final-pipeline-state transition;
`algorithm_rawstate`/`gapfill_state`/`emg_gate_state` describe the
target epoch itself, for context at selection time (the annotation
tool recomputes these live rather than trusting this cached copy, but
they're here for anyone inspecting the targets file directly).

`results/periodic_{rater}.csv` / `results/transition_{rater}.csv` —
`RESULT_FIELDS` in `annotate_spindle_validation.py`: `target_id,
dataset, animal_id, epoch_idx, epoch_start_sec, clock_time,
algorithm_rawstate, algorithm_gapfill_state, algorithm_emg_gate_state,
algorithm_is_artifact, algorithm_pre_state, algorithm_post_state,
corrected_pre_state, corrected_post_state, human_verdict, notes,
annotator, annotated_at`. `dataset`/`animal_id` are real columns (not
just parsed out of `target_id`, which is ambiguous to split — dataset
names like `Stxbp1_characterization` and `PHP_6_week` already contain
underscores) so a combined file stays easy to filter/split later
without string-parsing. Periodic rows leave
`algorithm_pre_state`/`post_state`/`corrected_*` blank (no transition
concept there); transition rows leave `corrected_*` blank unless the
verdict was `Transition (wrong states)`.

`results/reconciled.csv` — `RECONCILE_FIELDS`: `target_id, dataset,
animal_id, epoch_idx, source_mode, original_annotator,
original_verdict, reconciled_verdict, corrected_pre_state,
corrected_post_state, reconciled_by, notes, reconciled_at`. Separate
from the per-target results files deliberately — the original `Unsure`
row stays in place as history, never overwritten.

**Explicitly decided against splitting results per-animal or per-file**
(2026-09-19, once the 8-dataset scope made this worth asking): any real
analysis has to pool across animals anyway (confusion matrices per
dataset or pooled), so a per-animal split would just mean concatenating
files back together every time instead of reading one — and it
reintroduces the same "file count grows forever as more animals get
added" shape of problem already moved away from for per-target files.
The `dataset`/`animal_id` columns above give the same "separate later"
ability via `df[df.dataset == ...]` / `groupby`, at zero ongoing cost.

## Code

- `datasets.py` — shared `DATASETS`/`RATERS`.
- `select_periodic_windows.py` / `select_transition_epochs.py` —
  regenerate `targets/*.csv` from `file_manifest.csv`. Rerun any time
  the manifest changes; existing target ids, rater assignments, and
  (for periodic) randomly-chosen positions don't change (stable hash /
  seeded RNG, not reshuffled).
- `lib/nrem_reclass.py` — `compute_full_reclass_pipeline()` (new,
  2026-09-19): the shared raw -> gap-fill -> conditional-EMG-gate
  pipeline both select scripts and the annotation tool call, so the
  gate-applicability decision and the gated state itself are computed
  identically everywhere.
- `annotate_spindle_validation.py` — one shared PyQt6 GUI, three modes
  (`"periodic"` / `"transition"` / `"reconcile"`) set via constants at
  the top of the file. Both `"periodic"` and `"transition"` take a
  `(dataset, animal_id)` `FILE` — one launch covers a whole file's
  assigned targets for that mode, no relaunching between windows.
- `summarize_validation.py` — not yet built. Will join
  `results/**/*.csv` + `reconciled.csv` against the pipeline's own
  labels and report confusion matrices with raw TP/FP/FN/TN counts
  alongside percentages, per dataset and pooled.

## Open items

- File count per dataset beyond the current one-each seed — revisit
  once real annotation timing is in from a few sessions.
- Whether to add a small double-annotated overlap sample for a real
  kappa/agreement number, on top of the split-then-reconcile workflow.
- `summarize_validation.py` itself — not started.
