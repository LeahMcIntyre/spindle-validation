# Spindle staging validation — handoff

Read `PLAN.md` (this directory) first for the project's goals/schema;
this doc covers what changed since, session by session (same convention
as `nrem_reclass_project/HANDOFF.md`).

## 2026-09-18 — scaffolded, nothing annotated yet

Initial scaffold: `PLAN.md`, `file_manifest.csv` (seeded with one file
— `PHP_pre`/`CW0DA1`/`LFC_RSC`, already familiar from
`nrem_reclass_project`), `select_periodic_windows.py`,
`select_transition_epochs.py`, `annotate_spindle_validation.py`.
`summarize_validation.py` deliberately not built yet — nothing to
summarize until real annotation exists.

Both selection scripts run against the real seeded file and verified
by inspection, not just written and trusted:
- `select_periodic_windows.py` → `targets/periodic_windows.csv`: 18
  windows (CW0DA1's ~72h recording / 4h interval), 75 epochs each (5
  min / 4s epochs), split 9/9 between raters A/B.
- `select_transition_epochs.py` → `targets/transition_epochs.csv`: 100
  epochs, exactly 45 `nrem_to_wake` / 45 `wake_to_nrem` / 10
  `rem_involving` as specified, split 53/47 A/B.

`annotate_spindle_validation.py`'s queue-building functions
(`build_periodic_queue`, `build_transition_queue`,
`build_reconcile_queue`) were run directly (headless, `QT_QPA_PLATFORM=
offscreen`, no real GUI) against the real targets CSVs and returned
correct counts/ids. The `AnnotationPanel` record/back/reconcile logic
was exercised end-to-end with a fake EDF-viewer stub (no real viewer
window, so the actual `ebb_viewer` trace-view integration is *not* yet
confirmed — it reuses `launch_edf_window`/`jump_to_sample`/`Mask`
calls verbatim from `nrem_reclass_project/annotation/
annotate_nrem_windows.py`, which is already proven, but hasn't been run
end-to-end as a real on-screen session yet):
- Recording 3 verdicts (`NREM`/`Wake`/`Unsure`) writes exactly those 3
  rows to `results/periodic/{window_id}.csv`, in order.
- Back/undo removes the last row and lets it be re-recorded.
- Reopening the tool on the same window skips already-resolved epochs
  (resolved 3 → 72 remaining of 75) but keeps an `Unsure` epoch in the
  queue for revisiting.
- `MODE="reconcile"` correctly picks up an `Unsure` epoch, and once
  reconciled, that epoch no longer appears in a fresh reconcile queue
  (checked against `results/reconciled.csv`, not just in-memory state).

All of the above ran against a temp results directory, not the real
`results/` — no real annotation exists yet. Real annotation session
timing (needed to decide file scope, per `PLAN.md`'s open item) hasn't
started.

## 2026-09-19 — restructured results storage, periodic mode now file-scoped

Two changes, both before any real annotation existed (nothing to
migrate):

**1. Results storage: one-file-per-target → one-file-per-rater-per-mode.**
Ran the real numbers at ~86-file scope: `targets/*.csv` stays trivial
(window/sampled-epoch rows, not per-epoch enumeration — ~1,550 +
~8,600 rows, under 1.5MB combined), but the *old* results layout
(`results/periodic/{window_id}.csv`, `results/transition/{epoch_id}.csv`)
would have produced ~1,548 + ~8,600 ≈ 10,000 files, most of the
transition ones holding exactly one row. Root cause: copied
`nrem_reclass_project`'s one-file-per-target convention without
checking that its actual safety property (no concurrent-write
collisions) comes from the **rater** boundary, not the **target**
boundary — a rater only ever appends to their own targets regardless of
how results are grouped on disk. Consolidated to
`results/periodic_A.csv` / `results/periodic_B.csv` /
`results/transition_A.csv` / `results/transition_B.csv` — 4 files
total regardless of file-scope size, `target_id` column still
identifies which window/epoch each row belongs to. `results/reconciled.csv`
unchanged (already a single shared ledger).

**2. Periodic mode is now file-scoped, matching transition mode.**
Previously required editing `WINDOW_ID` and relaunching for each of a
file's ~18 periodic windows. `build_periodic_queue` now takes
`(dataset, animal_id, rater)`, pulls every window assigned to that
rater for the whole file, and expands them into one chronological
epoch queue spanning all of them — one launch, no relaunching between
windows. Transition mode already worked this way; the two modes are
now symmetric in how they're scoped, though still separate
sessions/modes by design (see PLAN.md).

Both changes verified headlessly (`QT_QPA_PLATFORM=offscreen`, fake
EDF-viewer stub, real `targets/*.csv`) before being called done, not
just written and trusted:
- File-scoped periodic queue for rater A on the seeded file: 675
  epochs across the 9 windows assigned to A (of 18 total), in
  chronological window order, confirmed by inspecting `target_id`
  ordering directly.
- Recording epochs across a full window plus the start of the next
  writes all rows to the single `periodic_A.csv` (no
  `results/periodic/` directory created at all) — confirmed 75 rows
  after one window, `target_id` correctly distinguishing rows once a
  second window's epochs started landing in the same file.
- Back/undo removes only the correct `(target_id, epoch_idx)` row,
  confirmed the other window's 75 rows were untouched.
- Reopening the tool resumes correctly across multiple windows sharing
  one results file (599 of 675 remaining after 76 resolved).
- Reconcile mode correctly located an `Unsure` epoch inside the shared
  per-rater file (not a per-target file), resolved it, and confirmed
  the reconcile queue excludes it afterward.

## 2026-09-19 — transition mode: segment view + segment verdict, not single-epoch

Transition mode previously showed one epoch (the algorithm's claimed
boundary epoch) and asked for its own Wake/NREM/REM/Unsure state, the
same taxonomy as periodic mode. Changed to match what the task actually
is — judging whether the algorithm's claimed transition is real, which
needs to see both sides of it:

- `build_transition_queue` now expands each target into a 4-epoch
  `context_rows` segment via a new `_build_context` helper:
  `CONTEXT_BEFORE=2` epochs before the claimed boundary + `CONTEXT_AFTER=2`
  at/after it (`[idx-2, idx-1, idx, idx+1]`, clipped at recording
  edges). The viewer highlights and centers on the whole 16s segment
  (`TRANSITION_WIN_SEC=20.0`), not a single 4s epoch.
- New verdict set for transition targets: `TRANSITION_VERDICTS =
  ["Transition", "No transition", "Unsure"]`, replacing the 4-way state
  verdict. Periodic targets keep the original `STATE_VERDICTS` (Wake/
  NREM/REM/Unsure) unchanged.
- The panel's 4 verdict buttons are now generic slots relabeled per
  target (`_verdicts_for` / `_refresh_verdict_buttons`) rather than
  fixed at construction — needed because `MODE="reconcile"` can mix
  periodic and transition targets in one session, and each needs its
  own verdict set.
- `results/transition_{rater}.csv` now stores `algorithm_pre_state` /
  `algorithm_post_state` (the algorithm's claim for the segment)
  instead of `algorithm_rawstate` / `algorithm_is_artifact` (a single
  epoch's own call) — `TRANSITION_RESULT_FIELDS`, a different schema
  than `periodic_{rater}.csv`'s `RESULT_FIELDS`. Fine since they're
  already separate files.

Verified headlessly against the real seeded file (fake EDF-viewer
stub, temp results dir), not just written and trusted:
- A transition target's `context_rows` are exactly `[idx-2, idx-1,
  idx, idx+1]`, matching its own `pre_state`/`post_state` claim.
- The active verdict set is `TRANSITION_VERDICTS` for a transition
  target, `STATE_VERDICTS` for a periodic one; an out-of-range slot
  (4, when only 3 verdicts exist) correctly records nothing.
- A recorded transition verdict lands in `transition_A.csv` with the
  pre/post-state columns populated and no `algorithm_rawstate` column;
  a periodic verdict lands in `periodic_A.csv` unaffected, still with
  the original schema.
- A mixed reconcile queue (one Unsure periodic target + one Unsure
  transition target) correctly pulled both, and the panel switched
  verdict sets per-target while stepping through it.

## 2026-09-19, later same day — reverted segment verdict, kept widened context view

The transition-mode redesign above (segment verdict, `Transition`/`No
transition`/`Unsure`) was a misread of the actual ask — corrected same
day, before any real annotation existed on either version. What was
actually wanted: still judge the single target epoch (same
Wake/NREM/REM/Unsure verdict as periodic mode), just *see* more of the
recording around it while doing so — 2 epochs before, 2 after, visible
in the viewer but not offered as annotation options.

Reverted:
- `TRANSITION_VERDICTS` / `TRANSITION_RESULT_FIELDS` removed. Single
  `VERDICTS` (Wake/NREM/REM/Unsure) and single `RESULT_FIELDS` schema
  again, used by periodic and transition mode alike.
- The dynamic per-target verdict-button-slot mechanism
  (`_verdicts_for`/`_refresh_verdict_buttons`/`_record_slot`) removed
  — no longer needed now that there's only one taxonomy; back to the
  original fixed `verdict_buttons` dict keyed by verdict string.
- `Target.pre_state`/`post_state` fields removed (were only feeding the
  now-gone segment schema).

Kept from the segment-verdict attempt, since it was the right idea for
the wrong problem: `_build_context` (the `[idx-2, idx-1, idx, idx+1]`
neighbor lookup) and `context_rows` on `Target`. Repurposed as a
*viewer-only* aid — `_show_current` now highlights only the target
epoch's own 4s span (`highlight_start`/`highlight_end`, computed from
`t.epoch_row` alone) but widens the trace view to `CONTEXT_VIEW_SEC`
(20s) and centers on the full context span's midpoint when
`context_rows` is set, so the 2-before/2-after neighbors are visible
without being separate targets. `algo_label` also shows the context
states with the target epoch bracketed (e.g. `NREM NREM [Wake] Wake`)
as a sighted-mode reading aid.

Verified headlessly with a fake EDF-viewer stub that records what it
was actually told to draw (mask spans, xscale, jump target), not just
that the code ran:
- The highlighted mask span for a transition target is exactly the
  target epoch's own 4s (1000 samples at 250Hz) — not the 16s context
  span.
- The trace view's `xscale` is set to `CONTEXT_VIEW_SEC` (20.0) for a
  transition target, vs. the periodic-mode `WIN_SEC` (10.0) for a
  periodic target (confirmed both in one run).
- The recorded row lands in `transition_A.csv` with the standard
  `RESULT_FIELDS` columns (`algorithm_rawstate`/`algorithm_is_artifact`),
  matching `periodic_A.csv`'s schema exactly — confirmed via
  `list(df.columns) == RESULT_FIELDS`.
- `algo_label`'s text contains the bracketed target epoch among its
  neighbors.

## 2026-09-19, third pass — segment verdict is back (confirmed, not a misread this time), view width now exactly 16s

Two small follow-ups, same day as the prior two transition-mode
entries:

**View width**: `CONTEXT_VIEW_SEC` 20.0 → 16.0 — exactly the 4-epoch
context span (4 × 4s), no extra margin, per explicit request.

**Verdict taxonomy for transition mode is `Transition`/`No transition`/
`Unsure` again** — this is what the "revert to single-epoch verdict"
entry above walked back, on a misread; the actual ask confirmed the
segment-level judgment was correct all along, just wanted the *viewer*
behavior (single highlight, widened context, not separate options)
that the revert had also produced. So this pass keeps the good half of
each attempt:
- From the segment-verdict version: `TRANSITION_VERDICTS =
  ["Transition", "No transition", "Unsure"]`, `Target.pre_state`/
  `post_state` (from `targets/transition_epochs.csv`), the dynamic
  per-target verdict-button-slot mechanism (`_verdicts_for`/
  `_refresh_verdict_buttons`/`_record_slot`) so a mixed reconcile
  session shows the right 3 or 4 options per target.
- From the single-epoch-verdict version: the highlighted mask stays
  exactly the target epoch's own 4s span, the widened view only
  changes what's *visible*, `algo_label` shows the bracketed context
  states as a reading aid.
- New: `RESULT_FIELDS` (used by both `periodic_{rater}.csv` and
  `transition_{rater}.csv`, one shared schema now rather than a fork)
  gained `algorithm_pre_state`/`algorithm_post_state`, populated only
  on transition rows (`t.source_mode == "transition"`) — periodic rows
  leave them blank via `csv.DictWriter`'s default fill, no schema
  fork needed since both already live in separate files by rater+mode.

Verified headlessly (fake EDF-viewer stub recording actual mask spans
and xscale calls, real seeded-file targets), not just written and
trusted:
- Transition mode's active verdict set is `TRANSITION_VERDICTS`; slot 4
  (out of range for a 3-option set) correctly records nothing.
- The highlighted mask span is still exactly one epoch (1000 samples =
  4s at 250Hz) and the view width is still `CONTEXT_VIEW_SEC` (now
  16.0) — the taxonomy change didn't regress the viewer fix from the
  prior entry.
- A recorded `Transition` verdict lands in `transition_A.csv` with
  `algorithm_pre_state`/`algorithm_post_state` populated from the
  target's own claim, using the same `RESULT_FIELDS` column list as
  `periodic_A.csv` (confirmed via `list(df.columns) == RESULT_FIELDS`).
- Periodic mode is unaffected: verdict set is still `VERDICTS`, and a
  periodic row's `algorithm_pre_state` reads back as `NaN` (correctly
  blank, not an error).
- A reconcile queue mixing one Unsure periodic target and one Unsure
  transition target showed `TRANSITION_VERDICTS` for the transition one
  and `VERDICTS` for the periodic one, switching correctly mid-session.

## 2026-09-19, fourth pass — arrow keys pan the EDF viewer again

Left/Right stopped panning the trace view once the annotation panel
existed: `ebb_viewer`'s arrow-key handling
(`TraceView.keyPressEvent`, `ebb/src/ebb_viewer/edf_viewer/trace_view.py:338-349`)
is a plain widget `keyPressEvent` override, not a `QShortcut` — it only
fires while the EDF viewer's own top-level window holds keyboard
focus. This panel is a *separate* top-level window that grabs focus
after most actions (`QTimer.singleShot(0, self.setFocus)`,
`self.setFocus()` in `_record`/`_go_back`), so once the panel has
focus, arrow key presses never reach the viewer at all.

Fixed by adding `Key_Left`/`Key_Right` `QShortcut`s (same
`ApplicationShortcut` pattern as the verdict/back keys, same
`note_edit.hasFocus()` guard so arrow-key cursor movement while typing
a note still works normally) that call a new `_pan(direction)` method.
`_pan` calls `self.edf_win.trace_view.slider.setValue(...)` with the
exact same step computation as the native handler (`max(1, tv.win //
2)`, confirmed by reading `trace_view.py:340-349` directly, not just
trusting a paraphrase) — panning feels identical regardless of which
window technically has focus.

Verified with real Qt key dispatch (`QTest.keyClick`, not calling
`_pan` directly), a fake trace-view stub tracking its own slider value:
- `panel._pan(1)`/`panel._pan(-1)` move the fake slider by exactly
  `max(1, win // 2)` in each direction, matching the source line by
  line.
- A real `Key_Right` press dispatched to the focused panel moves the
  slider by the same step — confirms the `QShortcut` is actually wired,
  not just that the method works in isolation.
- A `Key_Right` press while `note_edit` has focus does *not* pan
  (confirms the guard works and normal text-field cursor movement is
  unaffected).

## 2026-09-19, fifth pass — found and fixed the real rendering bottleneck (mask overlay rebuild, not curve antialiasing)

The peak-downsampling cherry-pick (previous entry) didn't fix it —
user reported the viewer was "still quite slow." Profiled for real
instead of guessing further, on the actual seeded file
(`ebb/src/ebb_viewer/edf_viewer/trace_view.py`, real `launch_edf_window`
call, `QT_QPA_PLATFORM=offscreen`, `time.perf_counter()`):

`TraceView._update_mask_overlays` (`trace_view.py:440-466`) tears down
*every* `pg.LinearRegionItem` overlay and rebuilds one per visible
span, per channel plot (×4), on every `refresh_overlays()`/`_set_window()`
call -- and this dominates redraw time, not the curve rendering the
downsampling fix targeted:

| window | overlay items | masks ON | masks OFF | mask share |
|---|---|---|---|---|
| 10s | 4 | 1.82 ms | 0.004 ms | 99.8% |
| 20s | 24 | 7.67 ms | 0.006 ms | 99.9% |
(`_set_window`, full pan incl. curve redraw, same position: 89-97%
mask share.) Cost scales with overlay-item count, which scales with
window width and with how dense the default commutator/sd/spindle_noise
masks are at that point in the file -- so widening the transition-mode
context view to 16s (an earlier entry this session) also made this
specific cost worse, on top of exposing it as the dominant one.

**Fixed in `annotate_spindle_validation.py` only** (no `ebb_viewer`
changes needed -- the real lever was already there:
`Mask.enabled`/`MasksDock`, `ebb/src/ebb_viewer/edf_viewer/masks_dock.py`).
New `HIDE_DEFAULT_MASKS = True` config constant; `main()` disables every
mask on `win.mask_set` right after `launch_edf_window()`, then calls
`win.masks_dock.rebuild(win.mask_set, win.state_set)` so the dock's
checkboxes stay in sync (not stale-UI showing checked while actually
disabled) and `win.trace_view.refresh_overlays()` once to apply. Still
a checkbox click away to re-enable per-session if wanted for a specific
epoch. Also flagged as arguably the *right* default methodologically,
not just a speed hack -- these are themselves algorithmic artifact
flags, and leaving them on independently anchors sighted annotation the
same way seeing SPINDLE's own call does.

Verified against the real (non-mocked) viewer, not a stub, on the real
seeded file: at the same dense position used above, `refresh_overlays()`
dropped from 20 overlay items / 6.53ms to 0 items / 0.01ms (~1000x),
and `win.masks_dock.findChildren(QtWidgets.QCheckBox)` confirmed all
three default masks' checkboxes actually read unchecked afterward, not
just that `.enabled` was set in memory.

## 2026-09-19, sixth pass — mask fix wasn't enough either; root-caused to Qt software rasterization, OpenGL wired in untested

User reported "still quite slow" after the mask fix (previous entry).
Profiled the actual `win.resize()` path directly this time (hadn't
before -- prior entries only measured `_set_window`/`refresh_overlays`,
not a real resize), on the real seeded file, with `cProfile`:

- A single `win.resize()` + forced repaint costs **1.4-2.8s**, with
  masks already off and downsampling already on from the previous two
  fixes. `cProfile` pinned **~97% of that to `QPainter.drawPath`**
  (`PlotCurveItem.paint`, 16 calls at ~150-170ms each) -- real curve
  rendering cost, not masks this time.
- **Ruled out antialiasing properly**: first attempt (re-calling
  `curve.setPen(...)`) was a no-op -- doesn't touch the actual
  `opts['antialias']` flag `PlotCurveItem.paint` reads. Redid it
  correctly (`curve.opts["antialias"] = False` directly, confirmed via
  `curve.opts["antialias"]` readback) and cost was unchanged (2.82s vs
  2.53s, within noise) -- matches `nrem_reclass_project`'s own
  historical antialiasing finding on a different tool, now re-confirmed
  on this exact code path.
- **Downsampling isn't actually engaging**: instrumented
  `PlotCurveItem.paint` directly -- every call draws the full 4000 raw
  points/channel (16s window x 250Hz) regardless of the cherry-picked
  `setDownsampling(auto=True, method="peak")`, presumably because
  auto-downsampling only kicks in when point count substantially
  exceeds pixel width, which 4000 pts over a ~2000px plot doesn't
  clear. Manually forced it anyway (`ds=2/4/8`, confirmed via re-plot)
  -- even 8x fewer points (500/channel) only cut time ~35% (2.9s ->
  1.9s), not proportional, so point count isn't the dominant factor
  either.
- Also found (not yet chased): a single `resize()` triggers **8**
  separate repaints, not 1 -- Qt's own layout-settling cascade across
  the 4 linked plot widgets multiplying the felt cost further. Flagged,
  not investigated further this session.

**Interpretation**: cost tracks canvas area under Qt's software raster
paint engine more than point count or AA -- textbook case for
GPU-accelerated rendering, which is exactly what the earlier
cherry-picked `EBB_VIEWER_USE_OPENGL` toggle (2026-09-19, second entry)
was for, but it was never actually exercised (env var unset) until now.

**Tried it, and the initial "it works" reading was itself wrong**:
setting `EBB_VIEWER_USE_OPENGL=1` and re-running the same `cProfile`
showed resize cost drop to 0.023s -- initially looked like confirmation
this fixes it. Checked closer before reporting it as real: the profile
under OpenGL showed `pyqtgraph.debug.printExc`/`traceback.format` calls
appearing inside the paint path, and Qt logging `QPainter::pen: Painter
not active` / `QPainter::end: Painter not active, aborted` to stderr --
`PlotCurveItem.paint()` (wrapped in pyqtgraph's own try/except-and-log
decorator) was silently throwing every call, and **zero `drawPath`
calls occurred** in that profile. `QT_QPA_PLATFORM=offscreen` has no
real GPU-backed painter context, so the "0.023s" reading was nothing
being drawn at all, not a real 100x speedup -- caught before reporting
it to the user as a confirmed fix, which it was not.

**Wired in anyway, explicitly untested**: `annotate_spindle_validation.py`
now sets `EBB_VIEWER_USE_OPENGL=1` itself (new `USE_OPENGL = True`
constant) -- has to happen *before* the `ebb_viewer` import, since
`trace_view.py` reads that env var at module-import time, so this
lives as a small bootstrap block above the normal imports rather than
in the usual session-config block with the other toggles. Confirmed
the wiring itself is correct headlessly (`pg.getConfigOption('useOpenGL')
== True` after importing the module, env var correctly set before
`ebb_viewer` loads) -- but whether OpenGL rendering actually looks
right and is actually faster on a real display is genuinely unknown
and can't be checked in this environment. User is testing on their
real screen next; `USE_OPENGL = False` reverts to the previous
(confirmed-working, just slower) software rendering if it doesn't pan
out or renders incorrectly.

**Confirmed on a real screen, same day**: faster, no visual glitches.
The headless environment's own limitation (no real GPU-backed painter
context under `QT_QPA_PLATFORM=offscreen`) meant this could only be
verified by the user directly, not by more automated testing here --
now done. `USE_OPENGL = True` stays the default. Combined with the two
earlier fixes this session (peak downsampling, default masks off), the
rendering-performance thread across all three entries is closed unless
it regresses.

## 2026-09-19, seventh pass — the "Back does nothing" symptom was real data corruption, root-caused and fixed

The back-button investigation from the previous entry turned out to be
a real, confirmed bug, not a UX misunderstanding — the diagnostic
questions asked (does anything print? does the CSV row count change?)
led straight to it: the user's next launch crashed outright with
`pandas.errors.ParserError: Expected 10 fields in line 677, saw 12`.

**Root cause**: `RESULT_FIELDS` gained `algorithm_pre_state`/
`algorithm_post_state` (two entries earlier this session, "segment
verdict is back" pass) while `results/periodic_A.csv` already existed
from before that change, written under the old 10-column header. The
write path only checked `is_new = not t.results_csv.exists()` — since
the file existed, no header got rewritten, and new rows kept appending
under the *old* 10-column header using the *new* 12-column
`RESULT_FIELDS` order. Confirmed directly:
`awk -F',' '{print NF}' periodic_A.csv | sort | uniq -c` → 676 lines
(header + old rows) at 10 fields, 36 lines (the CW0DC2 work) at 12 —
pandas' C parser can't read a file with inconsistent field counts row
to row at all, which is exactly the crash. This also explains the
earlier "Back is clickable but nothing happens" report: `_go_back` ->
`_delete_result` -> `pd.read_csv` was hitting this same corruption and
presumably failing quietly in that context.

**Recovered the data first, before fixing anything**: read the raw
file with `csv.reader` (handles ragged rows fine, unlike pandas),
split rows by their *actual* field count (10 vs 12) rather than
trusting the stale header, mapped each to the correct schema
positionally (old rows get blank `algorithm_pre_state`/
`algorithm_post_state`, not a naive reindex which would have shifted
`human_verdict`/`notes`/`annotator` into the wrong columns), backed up
the corrupted original to `periodic_A.csv.bak_schema_corruption`, wrote
the repaired 711-row file. Verified before moving on: reparsed cleanly,
`annotator` column 100% `"A"` (would show drift if anything shifted),
`human_verdict` only ever one of the 4 valid states, spot-checked one
old-schema and one new-schema row by hand.

**Fixed the actual bug, two parts**:
1. New `_ensure_schema(path, fieldnames)` — if a results file already
   exists with a header that doesn't match the current field list,
   migrates it in place (existing rows keep their values, gain blank
   values for new columns) *before* any further append. Called at the
   top of both write paths in `_record()` (`RESULT_FIELDS` and
   `RECONCILE_FIELDS`), so a future schema change gets picked up the
   very next time the tool touches that file, before any mismatched
   rows can accumulate — this exact corruption becomes structurally
   impossible now, not just fixed this once.
2. While touching write safety: `_delete_result`'s rewrite is now
   write-to-temp-then-`Path.replace()` (atomic on POSIX) instead of a
   direct `to_csv()` overwrite, so a killed/crashed process mid-Back
   can't leave the file half-written either.

**Found a second, unrelated bug while testing the fix**: `_delete_result`
was matching *every* row for `(target_id, epoch_idx)`, not just the
most recent one. Since results are meant to be append-only history
(re-recording an epoch adds a new row rather than overwriting), an
epoch that had been marked `Unsure` in an earlier session and then
re-recorded in a later one would, on a single "Back" press, get *both*
rows deleted — silently destroying the earlier session's history, not
just undoing the one action just taken. Caught by a test that recorded
+ undid a cycle against a copy of the real (already-repaired) 711-row
file and asserted the row count came back unchanged — it came back at
710, one short, which is what surfaced this. Fixed by locating the
*last* matching row's index (`df.index[mask][-1]`) and dropping only
that one.

Verified with 5 targeted tests (not part of the repo,
`/private/tmp/.../scratchpad/test_schema_migration.py` +
`test_delete_last_only.py`): old-schema-file migration with no data
shift, idempotent re-migration, append-after-migration produces a
clean file, atomic rewrite leaves no `.tmp` litter, a full
record+undo cycle against a **copy of the actual real repaired file**
nets to the identical row count, and a dedicated repro of the
multi-row-history case (mark Unsure in a "prior session", re-record
Wake in a "new session", Back removes only the Wake row and the
original Unsure row survives).

## 2026-09-19, eighth pass — methodology redesign: blind periodic, reclassification-aware transitions, 8 datasets

A real redesign, not a bugfix — user asked for it directly, explicitly
inviting clarifying questions first (asked 4, all answered, see
below). Prior sighted/fixed-position periodic data (all of it, across
every animal touched so far) was **discarded outright** per explicit
instruction — not archived — since it isn't comparable to the new
methodology anyway. Old `results/periodic_A.csv` (711 rows),
`results/transition_A.csv` (54 rows), and the schema-corruption backup
from the previous entry were all deleted.

**Clarifying answers that shaped this**:
1. Existing data -> discard (not archive, not keep-alongside).
2. File scope -> "pick N per dataset for now, let me add more later"
   (one per dataset picked by hand for the initial seed — see
   `file_manifest.csv`).
3. EMG gate -> reuse `nrem_reclass_project`'s exact trigger (>10%
   light-period NREM bout increase after gap-fill) and gate (EMG-RMS,
   k=5) exactly.
4. Random periodic sampling -> seeded/reproducible, not re-randomized
   per run.

**Scope grew from 3 datasets to 8**: `PHP_pre`, `PHP_6_week`,
`SRA_pre`, `SRA_6_week`, `RNA_KO`, `Stxbp1_characterization`, `vgat`
(`Stxbp1-vgat_CKO`), `vglut` (`Stxbp1-vglut2_CKO`). Checked the
filesystem directly before assuming any of this (`ls` on
`/Users/leahmcintyre/Data/nri/`) rather than asking the user to spell
out paths — found the exact directory names, confirmed `LFC_RSC`
combo availability for all 8, and picked one real, verified animal per
new dataset (checked each has a real, complete-looking labels CSV
before committing to it: `CW0DA1`/`PHP_6_week` 64801 rows, `AM0185`/
`SRA_pre` and `SRA_6_week` 43201 rows each, `JK1011`/vgat and
`JK1873`/vglut 64801 rows each). `DATASETS` was duplicated 3 ways
before this (once per script) — promoted to a new shared
`datasets.py` (`DATASETS` + `RATERS`) now that there are 8 entries and
duplication stopped being reasonable.

**New shared pipeline**: `lib.nrem_reclass.compute_full_reclass_pipeline()`
— one function, reused by both select scripts and the annotation tool,
so the EMG-gate-applicability decision and the gated state itself are
computed identically everywhere rather than reimplemented per caller.
Verified against real data before building anything on top of it:
`CW0DA1` (`emg_gate_applicable=False`, `gapfill_state == final_state`
for all 64,800 epochs — correct, matches its known "bout_number_decrease"
history) and `Mice-C414` (`emg_gate_applicable=True`, 1,021 epochs
where `final_state != gapfill_state` — correct, matches its known
"bout_number_increase" / large-gap-fill-correction history from
`nrem_reclass_project`).

**`select_periodic_windows.py` rewritten**: each 4h segment now gets a
5-minute window at a *seeded-random* position (`random.Random(stable_seed(window_id))`,
epoch-aligned via `randrange` over epoch counts, not raw seconds, so
window boundaries always land on real 4s epoch edges) instead of
always starting at the segment boundary. Verified on the real 8-file
manifest: 132 windows total (18 each for the four 3-day recordings, 12
each for the two 2-day `SRA` recordings — counts track real recording
length correctly), offsets ranging 140s–14,072s within each ~14,400s-
minus-window-length segment (not clustered near 0, confirms real
randomization), and a full rerun produced a byte-identical file
(confirms the seeding actually makes it reproducible, not just
plausible-looking once).

**`select_transition_epochs.py` rewritten**: transitions now detected
on `final_state` (post gap-fill, post EMG-gate-if-applicable) instead
of SPINDLE's raw state, via `compute_full_reclass_pipeline`. Verified:
800 rows across 8 files (45+45+10 per file, exactly as before), `Mice-C414`
and `Mice-1254` correctly flagged `emg_gate_applicable=True` in the
output (the only two files whose light-period bout-count increase
actually triggers the gate), everyone else `False`. Full run across all
8 files (including the pipeline computation) took ~2 seconds — not a
real cost.

**`annotate_spindle_validation.py` rewritten**, the largest single
change:
- `SIGHTED` toggle removed entirely — periodic is now unconditionally
  blind (no algorithm state shown until after a verdict is recorded,
  a genuine methodological requirement now, not a configurable
  experiment), transition unconditionally sighted, reconcile always
  sighted regardless of a target's original mode (two people resolving
  an `Unsure` case benefit from seeing the algorithm's read — doesn't
  compromise periodic's *independent first-pass* judgment, which is
  what the blindness actually protects).
- `Target`/`_pipeline_epochs` carry all three pipeline states
  (`rawstate`/`gapfill_state`/`final_state`) plus a per-file
  `emg_gate_applicable` flag; `_algo_text` and the context display
  (transition mode's widened view) now show `final_state`, not raw
  `state`.
- `RESULT_FIELDS` grew to carry all three algorithm states plus
  `corrected_pre_state`/`corrected_post_state` — reused the
  `_ensure_schema` migration mechanism from the previous entry, so
  this schema growth (unlike the one that caused real corruption
  before that mechanism existed) can't repeat the problem.
- New verdict for transition mode: `Transition (wrong states)`
  (`TRANSITION_VERDICTS` now 4 options, same 4-slot button layout as
  periodic's `VERDICTS`) — two `QComboBox` dropdowns (`Actually: [from]
  → [to]`, Wake/NREM/REM) appear whenever the current target is
  transition-sourced (any mode, including reconcile), and
  `_record_slot` blocks recording that specific verdict (with an
  inline message, not a silent no-op) until both are set. Dropdowns
  reset to blank every time a new target is shown, so a stale
  selection can't leak into a different epoch.

Verified with a 7-part test against real data (not mocks, real
`build_periodic_queue`/`build_transition_queue`/`build_reconcile_queue`
against the real 8-file targets, real Qt widget show/click/combo-box
interaction) before calling any of this done:
1. Periodic mode's `algo_label` reads exactly "algorithm call: hidden
   until recorded" pre-verdict.
2. A recorded periodic row correctly carries `algorithm_rawstate` +
   `algorithm_gapfill_state`, with `algorithm_emg_gate_state` blank
   (`CW0DA1` isn't gate-applicable).
3. Transition mode's verdict set is the new 4-way `TRANSITION_VERDICTS`,
   sighted (`algo_label` never reads "hidden"), and the corrected-state
   dropdown widget is visible.
4. Recording `Transition (wrong states)` with both dropdowns still
   blank writes nothing (`transition_A.csv` doesn't even exist yet)
   confirming the block is real; setting both (`NREM` → `REM`) and
   recording again writes exactly one row with `corrected_pre_state`/
   `corrected_post_state` populated and `algorithm_pre_state` matching
   the target's own claim; the dropdowns reset to blank afterward.
5. A normal `Transition` verdict leaves `corrected_pre_state`/
   `post_state` blank (not accidentally populated from stale dropdown
   state).
6. Back/undo still removes only the latest row (regression check
   against the previous entry's fix).
7. A reconcile queue built from real `Unsure` periodic + transition
   epochs correctly mixes both `source_mode`s, and `_show_algo_for`
   reads `True` for both regardless of origin (reconcile's
   always-sighted rule, confirmed not just asserted).
8. `Mice-C414`'s transition targets are correctly flagged
   `emg_gate_applicable=True` when rebuilt through the full annotation-
   tool code path (not just the standalone pipeline function checked
   earlier) — confirms the flag survives the whole
   `select_transition_epochs.py` -> `targets/transition_epochs.csv` ->
   `build_transition_queue` chain intact.

`file_manifest.csv` now has 8 rows (one per dataset, animals listed
above). `results/` is empty — a clean start under the new methodology.

## 2026-09-19, ninth pass — blind mode wasn't actually blind, plus explicit dataset/animal_id columns

**Real bug, caught by the user on a real screen, not by any test here**:
periodic mode's top color strip (the EDF viewer's own native state bar)
still showed a solid Wake-colored span during a blind session.
Root cause: `launch_edf_window(edf_path, csv_path=spindle_csv)` builds
that bar straight from SPINDLE's raw labels the moment `csv_path` is
passed (`EegMainWindow.__init__`, `ebb/src/ebb_viewer/edf_viewer/eeg_viewer.py:251-254`)
— entirely independent of anything this tool's own blind/sighted logic
controls. User's own fix suggestion ("launch edf does not need a state
path, can we just run it without it?") was exactly right and is what
got implemented: dropped `csv_path` from the `launch_edf_window` call
for all three modes. This also fixes a second, subtler problem in the
same line: that bar can only ever show SPINDLE's *raw* state, never
the gap-fill/EMG-gate final state this project actually validates, so
even in sighted transition mode it would have been showing something
different from (and potentially contradicting) what `algo_label`
says. Verified against the real viewer: `win.state_set` stays `None`,
`trace_view._state_row.isVisible()` is `False`, `masks_dock.rebuild`
and `refresh_overlays` both handle `state_set=None` without raising.

Separately: a Rainbow CSV "Align" (columns-padded-with-spaces) got
saved into `file_manifest.csv`, which silently broke it —
`pd.read_csv` doesn't strip whitespace, so the header read back as
`'dataset                '` instead of `'dataset'`, a `KeyError` on
every downstream script. Caught by trying to read it, not by
inspection. Restored to plain unpadded CSV. Not a code bug, but worth
remembering: Align is fine to *look* with, not to save.

**`dataset`/`animal_id` added as real columns** to `RESULT_FIELDS` and
`RECONCILE_FIELDS` (`Target` gained matching fields, threaded through
every constructor call site). Prompted by the user asking whether
results should split per-animal now that scope is 8 datasets — decided
against that (see PLAN.md's new "explicitly decided against" note:
any real analysis pools across animals anyway, so per-animal files
would just mean re-concatenating them every time) in favor of adding
these two columns to the existing combined files instead, which gives
the same "separate later" ability (`df[df.dataset == ...]`) without
the growing-file-count cost, and without relying on parsing `dataset`/
`animal_id` back out of `target_id` (ambiguous — dataset names like
`Stxbp1_characterization` and `PHP_6_week` already contain
underscores).

Verified by rerunning the full redesign test suite from the previous
entry with two more assertions added: a recorded periodic row carries
`dataset == "PHP_pre"` / `animal_id == "CW0DA1"`, and `reconciled.csv`
carries the same columns too — all 8 checks (now 8, with the two new
assertions folded into existing ones) still pass.

## 2026-09-21 — reset PHP_pre/CW0DA1's annotations, found a dataset/animal_id gotcha

User asked to start CW0DA1's periodic annotations over. `results/`
only had `periodic_A.csv` (74 rows). Filtering by `dataset == "PHP_pre"
& animal_id == "CW0DA1"` (the columns added the previous entry) only
matched 10 of them — the other 64 predated that schema change, so
`_ensure_schema`'s migration had backfilled `dataset`/`animal_id` with
empty strings, which `pd.read_csv` reads back as `NaN`, invisible to
an equality filter. Caught by checking the remaining row count instead
of trusting the first pass; finished by matching `target_id.str.startswith("PHP_pre_CW0DA1")`
instead, which doesn't depend on those columns being populated.
`results/` is empty again (that file was CW0DA1's only content).

Not fixed further since there's no more legacy data left to be
affected by it — but worth remembering if another schema addition
ever needs to filter existing rows by a newly-added column: check for
`NaN` on old rows, don't assume equality filtering covers everything.

## Open items

- File count per dataset beyond the current one-each seed (see
  `PLAN.md`) — revisit once real annotation timing is in.
- `summarize_validation.py` — still not started; now needs to handle
  the wider schema (three algorithm states, the "wrong states"
  correction fields) when it is.
- The resize-triggers-8-repaints finding (sixth entry, pre-redesign)
  is still unchased — moot given OpenGL resolved the practical
  complaint, noted in case rendering performance regresses again.
- Whether to add a small double-annotated overlap sample for a real
  kappa/agreement number, on top of the split-then-reconcile workflow.
- **Cross-file Unsure review, not yet built** (2026-09-21) — user asked
  for a good way to review Unsure epochs across every file, not just
  one at a time (`MODE="reconcile"` is already file-scoped, one launch
  = all of one file's Unsure epochs). Recommended, not yet built or
  chosen between: (a) a small summary script listing Unsure counts per
  file/mode so file-by-file reconcile sessions can be targeted instead
  of guessed at — simple, no architecture change; (b) one session that
  auto-walks Unsure epochs across *every* file without resetting
  `FILE` each time — real added complexity, since the EDF viewer
  window is tied to one file and crossing into a different animal's
  epochs mid-session means closing/reopening it automatically, not
  just advancing the queue. Waiting on which one (or neither) the user
  wants.
