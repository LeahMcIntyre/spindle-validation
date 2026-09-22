"""Shared annotation GUI for spindle_validation_project -- validates
the FULL reclassification pipeline's staging (SPINDLE raw -> gap-fill
-> EMG gate when applicable), independent of the gap-fill
reclassification nrem_reclass_project validates in isolation. See
PLAN.md (this directory) for the sampling design (periodic census +
transition sampling) and the blind/sighted, rater-split, and reconcile
decisions this script implements.

One tool, three MODEs (set the constants below, then rerun):

  MODE="periodic"    -- steps through EVERY periodic-census window
                         assigned to RATER for FILE
                         (targets/periodic_windows.csv), in
                         chronological order, window after window, with
                         no need to relaunch between windows. **Blind**:
                         no algorithm state is shown until after you
                         record a verdict (VERDICTS below:
                         Wake/NREM/REM/Unsure) -- this is a genuine spot
                         check of SPINDLE's staging, so seeing any of
                         the algorithm's calls first would anchor the
                         judgment. Results -> results/periodic_{RATER}.csv.
  MODE="transition"  -- steps through this rater's assigned transition
                         epochs (targets/transition_epochs.csv) for
                         FILE, in chronological order. Transitions are
                         selected from -- and the viewer/context show --
                         the FINAL pipeline state (after gap-fill, and
                         after the EMG-RMS gate too when this file's own
                         light-period NREM bout count increased enough
                         to trigger it -- see
                         lib.nrem_reclass.compute_full_reclass_pipeline),
                         not SPINDLE's raw state: the question is
                         whether the algorithm's claimed transition,
                         after everything this project does to it, is
                         real. **Sighted**: the viewer widens to also
                         show CONTEXT_BEFORE epochs before the target
                         and CONTEXT_AFTER-1 after (visual context only
                         -- only the target epoch is highlighted, the
                         neighbors are not separate options). Verdict =
                         TRANSITION_VERDICTS below: does the transition
                         really happen (Transition), not really
                         (No transition), genuinely a transition but the
                         algorithm has the wrong states for it
                         (Transition (wrong states) -- pick the actual
                         states in the two dropdowns that appear), or
                         Unsure. Results -> results/transition_{RATER}.csv.
  MODE="reconcile"   -- for two raters sitting down together: steps
                         through every target (either mode, whichever
                         rater recorded it) for FILE whose latest
                         recorded verdict is "Unsure", and records a
                         joint final call to results/reconciled.csv (a
                         separate ledger -- the original Unsure row is
                         left in place as history, not overwritten). A
                         reconciled target's verdict options (and
                         sighted/blind display) match its *original*
                         mode -- the panel switches per-target via
                         _verdicts_for, since a reconcile session can
                         mix both -- except reconcile is always sighted
                         regardless of source mode (two people resolving
                         an Unsure case benefit from seeing the
                         algorithm's read; that doesn't compromise the
                         periodic mode's *independent first-pass*
                         judgment, which is what blindness protects).
                         Also set RECONCILED_BY (both raters' initials).

FILE = (dataset, animal_id) is the one setting shared by all three
modes -- pick a file once, get every target assigned to you for that
whole file in one continuous queue, whichever mode you're in. Periodic
and transition stay two separate sessions/modes rather than one merged
queue -- census scanning (contiguous window, every epoch) and
transition scrutiny (isolated scattered points) are different
annotation tasks, and keeping them separate keeps each session's rhythm
consistent.

OUTPUT COLUMNS, both modes: `algorithm_rawstate` (SPINDLE's own call),
`algorithm_gapfill_state` (after gap-fill radius=1 reclassification),
`algorithm_emg_gate_state` (after the EMG-RMS gate too -- blank if this
file's bout-count-increase didn't trigger the gate; see
`emg_gate_applicable` in targets/*.csv). Transition rows additionally
get `algorithm_pre_state`/`algorithm_post_state` (the final-pipeline
transition the target represents) and, only when the "wrong states"
verdict is used (required) or optionally with "No transition" to say
what the states really were, `corrected_pre_state`/`corrected_post_state`
(from the two dropdowns).

RESULTS STORAGE: one append-only CSV per rater per mode
(results/periodic_A.csv, results/periodic_B.csv,
results/transition_A.csv, results/transition_B.csv), not one file per
window/epoch target. A target's own id (target_id column) still
identifies which window/epoch each row belongs to -- only the on-disk
grouping changed. Safe with no concurrent-write risk because the real
safety boundary is RATER (a rater only ever appends to their own file),
not the target. See PLAN.md's storage schema section and the
2026-09-19 HANDOFF.md entries for the full history here (including a
real schema-corruption incident and its fix -- `_ensure_schema` below
migrates a results file's header automatically before every write, so
a future column addition can't repeat it).

RATER identifies whose assignment to pull for periodic/transition modes
(see targets/*.csv's assigned_rater column, computed by
select_periodic_windows.py / select_transition_epochs.py). Irrelevant
for MODE="reconcile", which ignores assignment and pulls every Unsure
target for the file regardless of who recorded it.

HIDE_DEFAULT_MASKS turns off the viewer's default commutator/sd/
spindle_noise masks at launch -- real profiling showed rebuilding their
overlay spans dominates redraw time, and leaving them on independently
anchors annotation toward another algorithm's artifact calls. Still a
checkbox click away in the Masks dock if wanted for a specific epoch.

USE_OPENGL (set before the ebb_viewer import since trace_view.py reads
EBB_VIEWER_USE_OPENGL at module-import time) turns on GPU-accelerated
curve rendering instead of Qt's software rasterizer -- real profiling
found window resizes costing 1.4-2.8s under software rendering, ~97%
of it in QPainter.drawPath; confirmed on a real screen to fix it with
no visual glitches.

Requires the ebb repo's `ebb` / `ebb_viewer` packages installed
editable alongside this repo's own `lib`, same as
nrem_reclass_project/annotation/annotate_nrem_windows.py.

Usage: run from this repo's root with the ebb-dev environment active.
    python spindle_validation_project/annotate_spindle_validation.py
"""

import os

# Must be set before importing ebb_viewer -- trace_view.py's module-level
# `if os.environ.get("EBB_VIEWER_USE_OPENGL"): pg.setConfigOptions(useOpenGL=True)`
# only runs once, at import time, so this can't live down in the usual
# session-config block with the other toggles below.
USE_OPENGL = True  # confirmed on a real screen: faster, no visual glitches
if USE_OPENGL:
    os.environ["EBB_VIEWER_USE_OPENGL"] = "1"

import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets

from ebb.core.naming import parse_animal_id

import lib.nrem_reclass as nr
from datasets import DATASETS, RATERS
from ebb_viewer.edf_viewer.eeg_viewer import launch_edf_window
from ebb_viewer.edf_viewer.masks import Mask

# ---- session config: edit these, then rerun ----
MODE = "periodic"  # "periodic" | "transition" | "reconcile"
RATER = "A"
FILE = ("PHP_pre", "CW0DO4")  # (dataset, animal_id) -- the one file this session works on, any MODE
RECONCILED_BY = "A+B"         # used when MODE == "reconcile"
HIDE_DEFAULT_MASKS = True     # commutator/sd/spindle_noise off at launch -- see module docstring
# --------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
TARGETS_DIR = SCRIPT_DIR / "targets"
RESULTS_DIR = SCRIPT_DIR / "results"
RECONCILED_CSV = RESULTS_DIR / "reconciled.csv"

RESULT_FIELDS = [
    "target_id", "dataset", "animal_id", "epoch_idx", "epoch_start_sec", "clock_time",
    "algorithm_rawstate", "algorithm_gapfill_state", "algorithm_emg_gate_state",
    "algorithm_is_artifact",
    "algorithm_pre_state", "algorithm_post_state",
    "corrected_pre_state", "corrected_post_state",
    "human_verdict", "notes", "annotator", "annotated_at",
]
RECONCILE_FIELDS = [
    "target_id", "dataset", "animal_id", "epoch_idx", "source_mode", "original_annotator",
    "original_verdict", "reconciled_verdict",
    "corrected_pre_state", "corrected_post_state",
    "reconciled_by", "notes", "reconciled_at",
]
VERDICTS = ["Wake", "NREM", "REM", "Unsure"]
TRANSITION_WRONG_STATES_VERDICT = "Transition (wrong states)"
TRANSITION_VERDICTS = ["Transition", "No transition", TRANSITION_WRONG_STATES_VERDICT, "Unsure"]
# Verdicts that record the "Actually: from -> to" dropdowns. Required for
# wrong-states; optional for "No transition" (e.g. what the states really
# were, such as NREM -> NREM) -- left blank if not set.
CORRECTED_STATE_VERDICTS = ("No transition", TRANSITION_WRONG_STATES_VERDICT)
CORRECTED_STATE_OPTIONS = ["", "Wake", "NREM", "REM"]  # "" = not set (dropdown left untouched)
BACK_KEY = "0"
FS = 250
EPOCH_SEC = 4  # SPINDLE's own epoch length, matches DEFAULT_SPINDLE_EPOCH_SEC
WIN_SEC = 10.0  # periodic mode: view width around the single epoch
CONTEXT_BEFORE = 2  # transition mode: epochs shown before the target epoch
CONTEXT_AFTER = 2  # transition mode: epochs shown at/after the target epoch (the target itself counts as the first "after" epoch)
CONTEXT_VIEW_SEC = 16.0  # transition mode: view width, exactly the 4-epoch context span (4 x 4s), no extra margin
HIGHLIGHT_MASK_NAME = "epoch_under_review"
HIGHLIGHT_COLOR = (255, 0, 220, 60)


def _ensure_schema(path: Path, fieldnames: list[str]) -> None:
    """If `path` already exists with a different header than
    `fieldnames`, migrate it in place before any further append: rows
    keep their existing values, gain blank values for any new columns.

    Prevents a real corruption this project hit once already (see
    2026-09-19 HANDOFF.md): RESULT_FIELDS grew a column, and appending
    new rows under the new header below an old file's stale header
    (checked only via `not path.exists()`) left a file with
    inconsistent field counts row to row -- unreadable by pandas' C
    parser at all. Called before every write now, so a schema change
    gets picked up the moment the tool next touches that file, before
    any mismatched rows can accumulate. Safe no-op if the file doesn't
    exist yet, is empty, or its header already matches."""
    if not path.exists():
        return
    with path.open(newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return
        if header == fieldnames:
            return
        rows = [dict(zip(header, row)) for row in reader]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _pipeline_epochs(dataset: str, animal_id: str, combo: str) -> tuple[Path, Path, pd.DataFrame, bool]:
    """The full reclassification pipeline for one file -- see
    lib.nrem_reclass.compute_full_reclass_pipeline. Returns (edf_path,
    spindle_csv, pipeline_df, emg_gate_applicable); pipeline_df columns:
    epoch_idx, epoch_start_sec, clock_time, is_artifact, rawstate,
    gapfill_state, final_state."""
    project_root = DATASETS[dataset]
    edf_dir = project_root / "standard"
    edf_path = nr.edf_path_for(edf_dir, animal_id)
    spindle_dir = project_root / "spindle_outputs" / combo
    spindle_csv = next(p for p in spindle_dir.glob("*_labels.csv") if parse_animal_id(p) == animal_id)
    pipeline, emg_gate_applicable = nr.compute_full_reclass_pipeline(spindle_csv, animal_id, edf_dir)
    return edf_path, spindle_csv, pipeline, emg_gate_applicable


def _build_context(epochs_by_idx: pd.DataFrame, max_idx: int, idx: int) -> list[pd.Series]:
    """CONTEXT_BEFORE epochs before idx + idx itself + CONTEXT_AFTER-1
    more after it -- i.e. [idx-2, idx-1, idx, idx+1] by default. Purely
    for widening what's visible in the viewer around a transition
    target -- doesn't change what's annotated (still just idx) or the
    highlighted span (still just idx's own 4s). Clipped at the
    recording's edges (fewer than 4 rows right at the very start or end
    of a file)."""
    lo = max(0, idx - CONTEXT_BEFORE)
    hi = min(max_idx, idx + CONTEXT_AFTER - 1)
    return [epochs_by_idx.loc[i] for i in range(lo, hi + 1)]


class Target:
    """One epoch to annotate: where to jump the viewer, the pipeline's
    own call for it (epoch_row, from _pipeline_epochs -- rawstate,
    gapfill_state, final_state, is_artifact), and which results CSV its
    verdict gets appended to (results_csv is shared across many targets
    -- one file per rater per mode -- so target_id, not the file path,
    is what makes a target's key unique).

    dataset/animal_id are stored as plain columns on every results row
    (not just embedded in target_id, which is hard to split back apart
    unambiguously -- dataset names like "Stxbp1_characterization" and
    "PHP_6_week" already have underscores in them). emg_gate_applicable
    is a file-level fact (same for every target from the same file) --
    whether algorithm_emg_gate_state should be populated at all when
    this target is recorded. context_rows
    (transition mode only) is extra epochs surrounding epoch_row purely
    for the viewer to show as visual context -- the highlighted span and
    the results row are still just epoch_row. pre_state/post_state
    (transition mode only) is the algorithm's own claimed *final-state*
    transition for this target, from targets/transition_epochs.csv --
    what the Transition/No transition verdict is actually judging."""

    def __init__(
        self,
        target_id: str,
        dataset: str,
        animal_id: str,
        epoch_row: pd.Series,
        results_csv: Path,
        source_mode: str,
        emg_gate_applicable: bool = False,
        context_rows: list[pd.Series] | None = None,
        pre_state: str | None = None,
        post_state: str | None = None,
    ):
        self.target_id = target_id
        self.dataset = dataset
        self.animal_id = animal_id
        self.epoch_row = epoch_row
        self.results_csv = results_csv
        self.source_mode = source_mode
        self.emg_gate_applicable = emg_gate_applicable
        self.context_rows = context_rows
        self.pre_state = pre_state
        self.post_state = post_state

    @property
    def epoch_idx(self) -> int:
        return int(self.epoch_row["epoch_idx"])

    @property
    def key(self) -> tuple[str, int]:
        return (self.target_id, self.epoch_idx)


def build_periodic_queue(dataset: str, animal_id: str, rater: str) -> tuple[list[Target], Path, Path]:
    """Every periodic-census window assigned to `rater` for this file,
    chronological, expanded to one Target per epoch -- lets one session
    walk the whole file's census without relaunching between windows."""
    windows = pd.read_csv(TARGETS_DIR / "periodic_windows.csv")
    mine = windows[
        (windows["dataset"] == dataset)
        & (windows["animal_id"] == animal_id)
        & (windows["assigned_rater"] == rater)
    ].sort_values("window_start_sec")
    if len(mine) == 0:
        raise ValueError(f"no periodic windows assigned to rater {rater!r} for {dataset}/{animal_id}")

    combo = mine.iloc[0]["combo"]
    edf_path, spindle_csv, pipeline, emg_gate_applicable = _pipeline_epochs(dataset, animal_id, combo)
    results_csv = RESULTS_DIR / f"periodic_{rater}.csv"

    targets = []
    for _, w in mine.iterrows():
        in_window = pipeline[
            (pipeline["epoch_start_sec"] >= w["window_start_sec"])
            & (pipeline["epoch_start_sec"] < w["window_end_sec"])
        ].sort_values("epoch_start_sec")
        targets.extend(
            Target(w["window_id"], dataset, animal_id, row, results_csv, "periodic", emg_gate_applicable=emg_gate_applicable)
            for _, row in in_window.iterrows()
        )
    return targets, edf_path, spindle_csv


def build_transition_queue(dataset: str, animal_id: str, rater: str) -> tuple[list[Target], Path, Path]:
    """Every transition epoch assigned to `rater` for this file, each
    carrying its CONTEXT_BEFORE/CONTEXT_AFTER neighbors (see
    _build_context) for the viewer, and the algorithm's claimed
    pre_state/post_state (final-pipeline-state, from
    select_transition_epochs.py) for the verdict to judge."""
    transitions = pd.read_csv(TARGETS_DIR / "transition_epochs.csv")
    mine = transitions[
        (transitions["dataset"] == dataset)
        & (transitions["animal_id"] == animal_id)
        & (transitions["assigned_rater"] == rater)
    ].sort_values("epoch_start_sec")
    if len(mine) == 0:
        raise ValueError(f"no transition targets assigned to rater {rater!r} for {dataset}/{animal_id}")

    combo = mine.iloc[0]["combo"]
    edf_path, spindle_csv, pipeline, _ = _pipeline_epochs(dataset, animal_id, combo)
    epochs_by_idx = pipeline.set_index("epoch_idx", drop=False)
    max_idx = int(pipeline["epoch_idx"].max())
    results_csv = RESULTS_DIR / f"transition_{rater}.csv"

    targets = []
    for _, t in mine.iterrows():
        idx = int(t["epoch_idx"])
        targets.append(
            Target(
                t["epoch_id"],
                dataset,
                animal_id,
                epochs_by_idx.loc[idx],
                results_csv,
                "transition",
                emg_gate_applicable=bool(t["emg_gate_applicable"]),
                context_rows=_build_context(epochs_by_idx, max_idx, idx),
                pre_state=t["pre_state"],
                post_state=t["post_state"],
            )
        )
    return targets, edf_path, spindle_csv


def _reconciled_pairs() -> set[tuple[str, int]]:
    if not RECONCILED_CSV.exists():
        return set()
    df = pd.read_csv(RECONCILED_CSV)
    if df.empty:
        return set()
    return set(zip(df["target_id"], df["epoch_idx"].astype(int)))


def _unsure_targets_for_file(
    results_csv: Path,
    valid_target_ids: set[str],
    source_mode: str,
    dataset: str,
    animal_id: str,
    epochs_by_idx: pd.DataFrame,
    max_idx: int,
    already: set,
    emg_gate_applicable: bool = False,
    transition_meta: dict[str, tuple[str, str, bool]] | None = None,
) -> list[Target]:
    if not results_csv.exists():
        return []
    df = pd.read_csv(results_csv)
    if df.empty:
        return []
    mine = df[df["target_id"].isin(valid_target_ids)]
    if mine.empty:
        return []
    latest = mine.drop_duplicates(subset=["target_id", "epoch_idx"], keep="last")
    unsure = latest[latest["human_verdict"] == "Unsure"]

    out = []
    for _, u in unsure.iterrows():
        key = (u["target_id"], int(u["epoch_idx"]))
        if key in already:
            continue
        idx = int(u["epoch_idx"])
        row = epochs_by_idx.loc[idx]
        if source_mode == "transition":
            pre_state, post_state, t_emg_applicable = transition_meta[u["target_id"]]
            out.append(
                Target(
                    u["target_id"],
                    dataset,
                    animal_id,
                    row,
                    results_csv,
                    source_mode,
                    emg_gate_applicable=t_emg_applicable,
                    context_rows=_build_context(epochs_by_idx, max_idx, idx),
                    pre_state=pre_state,
                    post_state=post_state,
                )
            )
        else:
            out.append(
                Target(u["target_id"], dataset, animal_id, row, results_csv, source_mode, emg_gate_applicable=emg_gate_applicable)
            )
    return out


def build_reconcile_queue(dataset: str, animal_id: str) -> tuple[list[Target], Path, Path]:
    """Every Unsure-verdict target (periodic + transition, whichever
    rater recorded it) for one file, excluding targets already present
    in results/reconciled.csv. Scoped to one file at a time -- rerun
    with a different (dataset, animal_id) for another file's Unsure
    epochs."""
    windows = pd.read_csv(TARGETS_DIR / "periodic_windows.csv")
    transitions = pd.read_csv(TARGETS_DIR / "transition_epochs.csv")
    file_windows = windows[(windows["dataset"] == dataset) & (windows["animal_id"] == animal_id)]
    file_transitions = transitions[(transitions["dataset"] == dataset) & (transitions["animal_id"] == animal_id)]
    if len(file_windows) == 0 and len(file_transitions) == 0:
        raise ValueError(f"no targets found for {dataset}/{animal_id} in targets/")
    combo = file_windows["combo"].iloc[0] if len(file_windows) else file_transitions["combo"].iloc[0]

    edf_path, spindle_csv, pipeline, emg_gate_applicable = _pipeline_epochs(dataset, animal_id, combo)
    epochs_by_idx = pipeline.set_index("epoch_idx", drop=False)
    max_idx = int(pipeline["epoch_idx"].max())

    window_ids = set(file_windows["window_id"])
    transition_meta = {
        row["epoch_id"]: (row["pre_state"], row["post_state"], bool(row["emg_gate_applicable"]))
        for _, row in file_transitions.iterrows()
    }
    already = _reconciled_pairs()

    targets = []
    for rater in RATERS:
        targets.extend(
            _unsure_targets_for_file(
                RESULTS_DIR / f"periodic_{rater}.csv",
                window_ids,
                "periodic",
                dataset,
                animal_id,
                epochs_by_idx,
                max_idx,
                already,
                emg_gate_applicable=emg_gate_applicable,
            )
        )
        targets.extend(
            _unsure_targets_for_file(
                RESULTS_DIR / f"transition_{rater}.csv",
                set(transition_meta),
                "transition",
                dataset,
                animal_id,
                epochs_by_idx,
                max_idx,
                already,
                transition_meta=transition_meta,
            )
        )
    return targets, edf_path, spindle_csv


class AnnotationPanel(QtWidgets.QWidget):
    def __init__(
        self,
        mode: str,
        targets: list[Target],
        edf_win,
        rater: str,
        reconciled_by: str | None,
        parent=None,
    ):
        super().__init__(parent)
        # Plain QWidget defaults to NoFocus, so without this, reclaiming
        # focus from note_edit later (self.setFocus()) would silently
        # do nothing -- same fix as nrem_reclass_project's tool.
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.mode = mode
        self.edf_win = edf_win
        self.rater = rater
        self.reconciled_by = reconciled_by

        if mode == "reconcile":
            # build_reconcile_queue already excludes already-reconciled
            # targets, so everything handed in here is pending.
            self.queue = list(targets)
            self.resolved_keys: set = set()
            self.unsure_keys: set = set()
        else:
            resolved, unsure = self._scan_status(targets)
            pending = [t for t in targets if t.key not in resolved and t.key not in unsure]
            unsure_list = [t for t in targets if t.key in unsure]
            self.queue = pending + unsure_list  # revisit Unsure targets last, same as nrem_reclass_project's tool
            self.resolved_keys = resolved
            self.unsure_keys = unsure
        self.total_targets = len(targets)
        self.cursor = 0

        self.setWindowTitle(f"Spindle validation — {mode}")
        self.progress_label = QtWidgets.QLabel()
        self.algo_label = QtWidgets.QLabel()
        self.algo_label.setStyleSheet("font-weight: bold;")
        self.algo_label.setWordWrap(True)
        self.reveal_label = QtWidgets.QLabel()
        self.reveal_label.setWordWrap(True)
        self.reveal_label.setStyleSheet("color: #888;")

        self.setStyleSheet(
            "QPushButton {"
            "  padding: 4px 6px; font-size: 11px;"
            "  border: 1px solid #888888; border-radius: 4px;"
            "  background-color: #f2f2f2;"
            "}"
            "QPushButton:pressed { background-color: #d0d0d0; }"
            "QPushButton:disabled { color: #aaaaaa; border-color: #cccccc; background-color: #f8f8f8; }"
            "QLabel { font-size: 11px; }"
            "QComboBox { font-size: 11px; }"
        )

        # 4 generic slot buttons, not one per fixed verdict -- periodic
        # targets use VERDICTS, transition targets use
        # TRANSITION_VERDICTS; a reconcile session can show either
        # depending on the current target's source_mode. _show_current
        # relabels slots via _refresh_verdict_buttons each time.
        self.verdict_buttons: list[QtWidgets.QPushButton] = []
        verdict_grid = QtWidgets.QGridLayout()
        for i in range(4):
            btn = QtWidgets.QPushButton()
            btn.clicked.connect(lambda _checked=False, slot=i + 1: self._record_slot(slot))
            self.verdict_buttons.append(btn)
            verdict_grid.addWidget(btn, i // 2, i % 2)

        # Only meaningful for the "wrong states" transition verdict, but
        # left visible (not hidden) whenever the current target is
        # transition-sourced (any mode) -- pre-fillable before pressing
        # that verdict, same spirit as note_edit. Reset to blank every
        # time a new target is shown (_show_current).
        self.corrected_widget = QtWidgets.QWidget()
        corrected_row = QtWidgets.QHBoxLayout(self.corrected_widget)
        corrected_row.setContentsMargins(0, 0, 0, 0)
        corrected_row.addWidget(QtWidgets.QLabel("Actually:"))
        self.corrected_from_combo = QtWidgets.QComboBox()
        self.corrected_from_combo.addItems(CORRECTED_STATE_OPTIONS)
        corrected_row.addWidget(self.corrected_from_combo)
        corrected_row.addWidget(QtWidgets.QLabel("→"))
        self.corrected_to_combo = QtWidgets.QComboBox()
        self.corrected_to_combo.addItems(CORRECTED_STATE_OPTIONS)
        corrected_row.addWidget(self.corrected_to_combo)

        self.back_button = QtWidgets.QPushButton(f"{BACK_KEY}: Back")
        self.back_button.clicked.connect(self._go_back)
        back_row = QtWidgets.QHBoxLayout()
        back_row.addWidget(self.back_button)

        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setPlaceholderText("Optional note for this epoch")

        for i in range(4):
            sc = QtGui.QShortcut(QtGui.QKeySequence(str(i + 1)), self)
            sc.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(lambda slot=i + 1: self._record_slot(slot) if not self.note_edit.hasFocus() else None)
        back_sc = QtGui.QShortcut(QtGui.QKeySequence(BACK_KEY), self)
        back_sc.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        back_sc.activated.connect(lambda: None if self.note_edit.hasFocus() else self._go_back())

        # Left/Right pan the EDF trace view -- this panel is a separate
        # top-level window from the viewer, and QTimer.singleShot below
        # (plus every _record/_go_back) pulls keyboard focus back onto
        # this panel, which would otherwise swallow arrow keys the
        # viewer's own TraceView.keyPressEvent never gets a chance to
        # see. Not a duplicate/competing binding -- ebb_viewer's arrow
        # handling is a plain keyPressEvent override, not a QShortcut,
        # so a key press is dispatched to exactly one of the two
        # (whichever holds focus), never both. Mirrors
        # TraceView.keyPressEvent's own step size (half a window) so
        # panning feels identical regardless of which window has focus.
        left_sc = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Left), self)
        left_sc.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        left_sc.activated.connect(lambda: None if self.note_edit.hasFocus() else self._pan(-1))
        right_sc = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Right), self)
        right_sc.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        right_sc.activated.connect(lambda: None if self.note_edit.hasFocus() else self._pan(1))

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.algo_label)
        layout.addWidget(QtWidgets.QLabel("Verdict (advances to next):"))
        layout.addLayout(verdict_grid)
        layout.addWidget(self.corrected_widget)
        layout.addLayout(back_row)
        layout.addWidget(self.note_edit)
        layout.addWidget(self.reveal_label)

        self.resize(330, 320)
        self._show_current()
        QtCore.QTimer.singleShot(0, self.setFocus)

    def _verdicts_for(self, t: "Target | None") -> list[str]:
        if t is not None and t.source_mode == "transition":
            return TRANSITION_VERDICTS
        return VERDICTS

    def _show_algo_for(self, t: "Target | None") -> bool:
        """Periodic mode is always blind (a genuine spot check of
        SPINDLE's staging shouldn't anchor on the algorithm's own call);
        transition mode is always sighted (judging a claimed transition
        needs to see what's claimed); reconcile is always sighted
        regardless of the target's original mode (two people resolving
        an Unsure case benefit from seeing the algorithm's read -- that
        doesn't compromise periodic's *independent first-pass* judgment,
        which is what blindness protects there)."""
        if t is None:
            return False
        if self.mode == "reconcile":
            return True
        return t.source_mode == "transition"

    def _refresh_verdict_buttons(self, t: "Target | None") -> None:
        verdicts = self._verdicts_for(t)
        for i, btn in enumerate(self.verdict_buttons):
            if i < len(verdicts):
                btn.setText(f"{i + 1}: {verdicts[i]}")
                btn.setVisible(True)
                btn.setEnabled(t is not None)
            else:
                btn.setVisible(False)
        self.corrected_widget.setVisible(t is not None and t.source_mode == "transition")
        self.corrected_from_combo.setCurrentIndex(0)
        self.corrected_to_combo.setCurrentIndex(0)

    def _record_slot(self, slot: int) -> None:
        t = self._current_target()
        verdicts = self._verdicts_for(t)
        if slot > len(verdicts):
            return
        verdict = verdicts[slot - 1]
        if verdict == TRANSITION_WRONG_STATES_VERDICT:
            if not self.corrected_from_combo.currentText() or not self.corrected_to_combo.currentText():
                self.reveal_label.setText(
                    "Pick both “Actually” states first (the two dropdowns above Back), then press this again."
                )
                return
        self._record(verdict)

    def _scan_status(self, targets: list[Target]) -> tuple[set, set]:
        """Latest verdict per target, read from each target's results
        CSV (cached per file -- since results_csv is now shared across
        every target for a given rater+mode, this typically reads just
        one file total, not one per target)."""
        resolved: set = set()
        unsure: set = set()
        cache: dict[Path, pd.DataFrame | None] = {}
        for t in targets:
            if t.results_csv not in cache:
                if t.results_csv.exists():
                    df = pd.read_csv(t.results_csv)
                    cache[t.results_csv] = (
                        df.drop_duplicates(subset=["target_id", "epoch_idx"], keep="last") if not df.empty else None
                    )
                else:
                    cache[t.results_csv] = None
            latest = cache[t.results_csv]
            if latest is None:
                continue
            row = latest[(latest["target_id"] == t.target_id) & (latest["epoch_idx"] == t.epoch_idx)]
            if row.empty:
                continue
            if row.iloc[-1]["human_verdict"] == "Unsure":
                unsure.add(t.key)
            else:
                resolved.add(t.key)
        return resolved, unsure

    def _current_target(self) -> Target | None:
        if self.cursor >= len(self.queue):
            return None
        return self.queue[self.cursor]

    def _pan(self, direction: int) -> None:
        """Pan the EDF trace view left (-1) or right (+1) by half a
        window -- exactly TraceView.keyPressEvent's own Left/Right
        behavior (ebb/src/ebb_viewer/edf_viewer/trace_view.py), called
        directly since that keyPressEvent never fires while this panel
        holds focus (see the Left/Right QShortcut setup in __init__)."""
        tv = self.edf_win.trace_view
        step = max(1, tv.win // 2)
        tv.slider.setValue(tv.start + direction * step)

    def _algo_text(self, t: Target) -> str:
        row = t.epoch_row
        if not t.context_rows:
            # Periodic mode: only ever shown when sighted (reconcile),
            # since _show_current gates this whole label behind
            # _show_algo_for -- otherwise never displayed pre-record.
            return f"epoch {t.epoch_idx} — final state: {row['final_state']} (raw: {row['rawstate']}, gap-fill: {row['gapfill_state']})"
        # Transition mode: neighboring epochs' FINAL states, target
        # epoch bracketed -- viewing aid only, the highlighted span is
        # still just epoch_idx.
        parts = [
            f"[{r['final_state']}]" if int(r["epoch_idx"]) == t.epoch_idx else r["final_state"]
            for r in t.context_rows
        ]
        claim = f"  (algorithm claims {t.pre_state} → {t.post_state})" if t.pre_state else ""
        return f"epoch {t.epoch_idx}  —  context: {' '.join(parts)}{claim}"

    def _show_current(self) -> None:
        t = self._current_target()
        self.back_button.setEnabled(self.cursor > 0)
        self._refresh_verdict_buttons(t)

        if t is None:
            self.progress_label.setText(f"{self.mode}: nothing left in this session's queue")
            self.algo_label.setText("")
            return

        self.progress_label.setText(
            f"{self.mode} — {t.target_id} — "
            f"{len(self.queue) - self.cursor} remaining this session"
            f"{f', {len(self.unsure_keys)} unsure' if self.unsure_keys else ''}"
        )

        show_algo = self._show_algo_for(t)
        self.algo_label.setText(self._algo_text(t) if show_algo else "algorithm call: hidden until recorded")

        row = t.epoch_row
        highlight_start = int(round(row["epoch_start_sec"] * FS))
        highlight_end = highlight_start + FS * EPOCH_SEC
        self.edf_win.mask_set.add(
            Mask(name=HIGHLIGHT_MASK_NAME, color=HIGHLIGHT_COLOR, spans=[(highlight_start, highlight_end)])
        )
        self.edf_win.trace_view.refresh_overlays()

        if t.context_rows:
            # Widen the view to show the neighboring epochs too, but
            # only the target epoch above is highlighted -- the
            # neighbors are visible context, not separate options.
            view_start = int(round(t.context_rows[0]["epoch_start_sec"] * FS))
            view_end = int(round(t.context_rows[-1]["epoch_start_sec"] * FS)) + FS * EPOCH_SEC
            self.edf_win.trace_view.xscale.setValue(CONTEXT_VIEW_SEC)
            self.edf_win.trace_view.jump_to_sample((view_start + view_end) // 2)
        else:
            self.edf_win.trace_view.xscale.setValue(WIN_SEC)
            self.edf_win.trace_view.jump_to_sample(highlight_start + (FS * EPOCH_SEC) // 2)

    def _latest_original_row(self, t: Target) -> dict:
        if not t.results_csv.exists():
            return {}
        df = pd.read_csv(t.results_csv)
        if df.empty:
            return {}
        mine = df[(df["target_id"] == t.target_id) & (df["epoch_idx"] == t.epoch_idx)]
        if mine.empty:
            return {}
        return mine.iloc[-1].to_dict()

    def _record(self, verdict: str) -> None:
        t = self._current_target()
        if t is None:
            return
        row = t.epoch_row
        note = self.note_edit.text().strip()
        now = datetime.now().isoformat(timespec="seconds")
        corrected_from = self.corrected_from_combo.currentText() if verdict in CORRECTED_STATE_VERDICTS else ""
        corrected_to = self.corrected_to_combo.currentText() if verdict in CORRECTED_STATE_VERDICTS else ""

        if self.mode == "reconcile":
            orig = self._latest_original_row(t)
            RECONCILED_CSV.parent.mkdir(parents=True, exist_ok=True)
            _ensure_schema(RECONCILED_CSV, RECONCILE_FIELDS)
            is_new = not RECONCILED_CSV.exists()
            with RECONCILED_CSV.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=RECONCILE_FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow(
                    {
                        "target_id": t.target_id,
                        "dataset": t.dataset,
                        "animal_id": t.animal_id,
                        "epoch_idx": t.epoch_idx,
                        "source_mode": t.source_mode,
                        "original_annotator": orig.get("annotator", ""),
                        "original_verdict": orig.get("human_verdict", ""),
                        "reconciled_verdict": verdict,
                        "corrected_pre_state": corrected_from,
                        "corrected_post_state": corrected_to,
                        "reconciled_by": self.reconciled_by,
                        "notes": note,
                        "reconciled_at": now,
                    }
                )
        else:
            t.results_csv.parent.mkdir(parents=True, exist_ok=True)
            _ensure_schema(t.results_csv, RESULT_FIELDS)
            is_new = not t.results_csv.exists()
            record = {
                "target_id": t.target_id,
                "dataset": t.dataset,
                "animal_id": t.animal_id,
                "epoch_idx": t.epoch_idx,
                "epoch_start_sec": row["epoch_start_sec"],
                "clock_time": row["clock_time"].isoformat(),
                "algorithm_rawstate": row["rawstate"],
                "algorithm_gapfill_state": row["gapfill_state"],
                "algorithm_emg_gate_state": row["final_state"] if t.emg_gate_applicable else "",
                "algorithm_is_artifact": row["is_artifact"],
                "human_verdict": verdict,
                "notes": note,
                "annotator": self.rater,
                "annotated_at": now,
            }
            if t.source_mode == "transition":
                record["algorithm_pre_state"] = t.pre_state
                record["algorithm_post_state"] = t.post_state
            if verdict in CORRECTED_STATE_VERDICTS:
                record["corrected_pre_state"] = corrected_from
                record["corrected_post_state"] = corrected_to
            with t.results_csv.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow(record)

        if verdict == "Unsure" and self.mode != "reconcile":
            self.unsure_keys.add(t.key)
            self.resolved_keys.discard(t.key)
        else:
            self.resolved_keys.add(t.key)
            self.unsure_keys.discard(t.key)

        self.reveal_label.setText("")
        self.note_edit.clear()

        self.cursor += 1
        self._show_current()
        self.setFocus()

    def _go_back(self) -> None:
        if self.cursor == 0:
            return
        self.cursor -= 1
        t = self._current_target()
        prev = self._delete_result(t)
        self.resolved_keys.discard(t.key)
        self.unsure_keys.discard(t.key)
        self._show_current()  # resets the dropdowns, so restore them after
        self.note_edit.setText(prev.get("note", ""))
        if prev.get("corrected_pre"):
            self.corrected_from_combo.setCurrentText(prev["corrected_pre"])
        if prev.get("corrected_post"):
            self.corrected_to_combo.setCurrentText(prev["corrected_post"])
        if prev.get("verdict"):
            actually = (
                f" (actually {prev.get('corrected_pre') or '?'} → {prev.get('corrected_post') or '?'})"
                if (prev.get("corrected_pre") or prev.get("corrected_post"))
                else ""
            )
            self.reveal_label.setText(f"Went back — you had marked: {prev['verdict']}{actually}. Re-mark this epoch.")
        else:
            self.reveal_label.setText("Went back — nothing was recorded for this epoch yet.")
        self.setFocus()

    def _delete_result(self, t: Target) -> dict:
        """Removes only the MOST RECENT row for (t.target_id,
        t.epoch_idx) from its results CSV (or reconciled.csv in
        reconcile mode), if present -- other targets' rows sharing that
        same file are untouched, and so is any OLDER row for this same
        target/epoch from a previous session (results are append-only
        history elsewhere in this tool -- e.g. an epoch re-recorded
        after an earlier "Unsure" pass -- so Back should undo the one
        action just taken, not erase that whole history). Returns the
        deleted row's note, verdict and corrected states (empty dict if
        there was nothing to delete), so Back can show what was marked."""
        path = RECONCILED_CSV if self.mode == "reconcile" else t.results_csv
        if not path.exists():
            return {}
        df = pd.read_csv(path)
        mask = (df["target_id"] == t.target_id) & (df["epoch_idx"] == t.epoch_idx)
        matching = df.index[mask]
        if len(matching) == 0:
            return {}
        last_idx = matching[-1]
        last = df.loc[last_idx].fillna("")
        prev = {
            "note": str(last.get("notes", "")),
            "verdict": str(last.get("reconciled_verdict" if self.mode == "reconcile" else "human_verdict", "")),
            "corrected_pre": str(last.get("corrected_pre_state", "")),
            "corrected_post": str(last.get("corrected_post_state", "")),
        }
        # Write-then-rename, not a direct overwrite -- if this process
        # gets killed mid-write, the live file is untouched (the
        # incomplete .tmp is just orphaned) instead of left half-written.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        df.drop(index=last_idx).to_csv(tmp_path, index=False)
        tmp_path.replace(path)
        return prev


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    dataset, animal_id = FILE

    if MODE == "periodic":
        targets, edf_path, _spindle_csv = build_periodic_queue(dataset, animal_id, RATER)
        reconciled_by = None
    elif MODE == "transition":
        targets, edf_path, _spindle_csv = build_transition_queue(dataset, animal_id, RATER)
        reconciled_by = None
    elif MODE == "reconcile":
        targets, edf_path, _spindle_csv = build_reconcile_queue(dataset, animal_id)
        reconciled_by = RECONCILED_BY
    else:
        raise ValueError(f"unknown MODE {MODE!r}")

    if not targets:
        print(f"{MODE}: nothing to annotate for the current selection.")
        return

    print(f"{MODE}: {len(targets)} epochs queued")

    # No csv_path -- launch_edf_window would otherwise build its own
    # native state bar straight from SPINDLE's raw labels (real bug hit
    # 2026-09-19: it showed a solid Wake-colored strip during a blind
    # periodic session, defeating the blindness). It would also be
    # misleading even when sighted (transition mode): that bar can only
    # ever reflect SPINDLE's *raw* state, never the gap-fill/EMG-gate
    # final state this project actually validates. Our own algo_label
    # is the single source of truth for state now, correctly gated by
    # blind/sighted per mode -- the viewer here is just raw traces.
    win = launch_edf_window(edf_path)
    win.resize(1400, 800)
    win.move(460, 60)
    win.show()

    if HIDE_DEFAULT_MASKS:
        # The default commutator/sd/spindle_noise masks (built live from
        # the raw EDF) are the dominant cost in every redraw here -- real
        # profiling on this exact file showed mask-overlay rebuild
        # (TraceView._update_mask_overlays tears down and recreates a
        # pg.LinearRegionItem per visible span, per channel, on every
        # pan/refresh) accounts for the vast majority of redraw time.
        # Turning them off by default is also arguably the right call
        # methodologically, not just a speed hack -- these are
        # themselves algorithmic artifact flags, independent of the
        # reclassification pipeline this tool validates. Still just a
        # checkbox click away in the Masks dock if wanted for a
        # specific epoch -- set HIDE_DEFAULT_MASKS = False to leave them
        # on by default instead.
        for mask in win.mask_set:
            mask.enabled = False
        win.masks_dock.rebuild(win.mask_set, win.state_set)
        win.trace_view.refresh_overlays()

    panel = AnnotationPanel(MODE, targets, win, RATER, reconciled_by)
    panel.move(20, 60)
    panel.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
