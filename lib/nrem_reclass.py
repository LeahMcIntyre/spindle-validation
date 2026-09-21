"""NREM-artifact reclassification: SPINDLE loading, the context/gap-fill
rule, and bout/summary metrics built on top of it.

Promoted out of `nrem_misclassification/nrem_artifact_reclassification.ipynb`
("Shared helpers" + Part 2's `context_summary_row`) so Goal 1 and Goal 2
of `nrem_reclass_project/` can call the same, already-validated code
instead of re-deriving it. `reclassify_nrem_by_gap_fill`'s defaults
(`gap_radius=1`, `noise_thresh=0.5`) are the ones checked against manual
review at 98.8% agreement -- see that notebook's "Manual review results"
section, or the migrated verdicts in
`nrem_reclass_project/annotation/legacy_reviews/context_reclass/`.

Pure computation only, no plotting -- same split as `psd_compute.py` /
`motion_compute.py`. Callers build their own figures from these
DataFrames.
"""

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd
from openseize.file_io import edf

from ebb.core.naming import parse_animal_id
from ebb.io.edf_header import read_edf_start_datetime
from ebb.io.spindle_csv import NOISE_COLUMNS, STATE_COLUMNS, resolve_column
from ebb.process.sleep_compute import DEFAULT_SPINDLE_EPOCH_SEC

DARK_HOURS = set(range(20, 24)) | set(range(0, 6))  # 20:00-06:00
RAW_STATE_TO_NAME = {"w": "Wake", "n": "NREM", "r": "REM"}
STATE_ORDER = ["Wake", "NREM", "REM"]

# Validated defaults for reclassify_nrem_by_gap_fill -- see module docstring.
DEFAULT_GAP_RADIUS = 1
DEFAULT_NOISE_THRESH = 0.5

DEFAULT_FS = 250
EMG_CHANNEL_IDX = 3  # fixed LFC=0/RSC=1/LSC=2/EMG=3 convention throughout ebb
EEG_CHANNEL_IDXS = [0, 1, 2]  # LFC, RSC, LSC

# EMG-RMS-gate default, chosen from nrem_reclass_project/emg_noise/
# emg_rms_wake_vs_nrem.ipynb's k-sweep against annotated Wake/NREM labels:
# pooled, k=5 gives ~88.7% sensitivity / ~1.3% false-positive rate (real
# clean NREM wrongly flagged). Per-animal behavior is uneven at this k --
# see that notebook for exceptions (Mice-C525 needed k~21 to clean up;
# CW0DI2/Mice-C366 stayed poorly separated at any k).
DEFAULT_EMG_GATE_K = 5

# EMG-EEG cross-channel correlation gate defaults -- see
# emg_noise/emg_eeg_gate_wake_vs_nrem.ipynb. Real movement contaminates
# every channel at once (mechanical), while the EMG-only false-positive
# cases (e.g. cardiac bleed-through) don't correlate with EEG at all --
# this held up across all 8 well-powered annotated animals, including
# CW0DI2/Mice-C366, which no EMG-only feature (raw RMS, per-animal
# normalization, sub-chunk statistics, local baseline) could separate.
# Unlike EMG RMS, correlation is already a bounded, roughly comparable-
# across-animals statistic, so this gate uses one fixed threshold rather
# than a per-animal population baseline.
DEFAULT_EMG_EEG_CONTEXT_SEC = 8.0
DEFAULT_EMG_EEG_SUBWIN_SEC = 0.5
DEFAULT_EMG_EEG_CORR_THRESHOLD = 0.3


def edf_path_for(edf_dir: str | Path, animal_id: str) -> Path:
    return next(p for p in Path(edf_dir).glob("*PROCESSED.edf") if parse_animal_id(p) == animal_id)


def load_spindle_epochs(
    spindle_csv: str | Path,
    animal_id: str,
    edf_dir: str | Path,
    epoch_sec: int = DEFAULT_SPINDLE_EPOCH_SEC,
) -> pd.DataFrame:
    """Per-epoch SPINDLE table: epoch_idx, epoch_start_sec, state
    (Wake/NREM/REM), is_artifact, is_dark."""
    header = pd.read_csv(spindle_csv, nrows=0).columns.tolist()
    state_col = resolve_column(header, STATE_COLUMNS)
    noise_col = resolve_column(header, NOISE_COLUMNS)
    usecols = [state_col, noise_col] + (["threshold"] if "threshold" in header else [])

    df = pd.read_csv(spindle_csv, usecols=usecols).rename(
        columns={state_col: "rawstate", noise_col: "noise_prob"}
    )
    df["epoch_idx"] = np.arange(len(df))
    df["epoch_start_sec"] = df["epoch_idx"] * epoch_sec
    df["state"] = df["rawstate"].map(RAW_STATE_TO_NAME)

    noise_thresh = df["threshold"].dropna().iloc[0] if "threshold" in df.columns else DEFAULT_NOISE_THRESH
    df["is_artifact"] = df["noise_prob"] >= noise_thresh

    rec_start = read_edf_start_datetime(edf_path_for(edf_dir, animal_id))
    clock_times = rec_start + pd.to_timedelta(df["epoch_start_sec"], unit="s")
    df["is_dark"] = clock_times.dt.hour.isin(DARK_HOURS)
    return df


def reclassify_nrem_artifacts(df: pd.DataFrame) -> pd.DataFrame:
    """Pure per-epoch rule: every NREM epoch SPINDLE itself flags as
    artifact -> Wake. Adds reclassified (bool) and reclass_state columns.

    Kept for comparison against the gap-fill rule below (see
    `context_summary_row`'s pct_nrem_flipped_pure) -- not the rule this
    project standardizes on.
    """
    result = df.copy()
    flip = (result["state"] == "NREM") & result["is_artifact"]
    result["reclassified"] = flip
    result["reclass_state"] = np.where(flip, "Wake", result["state"])
    return result


def reclassify_nrem_by_gap_fill(
    df: pd.DataFrame, gap_radius: int = DEFAULT_GAP_RADIUS
) -> pd.DataFrame:
    """Reclassifies a NREM epoch to Wake if it is not itself SPINDLE-artifact
    flagged (`is_artifact`) but sits within `gap_radius` epochs of an
    artifact-flagged epoch on *both* sides -- of any state (Wake/NREM/REM).
    `gap_radius=1` (the default, and most conservative setting) means a
    single clean NREM epoch with an artifact-flagged epoch immediately
    before AND immediately after it.

    Operates directly on the raw epoch sequence, no bout construction --
    this matters because it also catches gaps that occur *within* a single
    NREM bout that is only partly artifact-flagged (confirmed against the
    EEG viewer on Mice-C280: epochs 31459-31464 are one continuous NREM
    bout with artifact/clean/artifact/clean epochs interleaved). A
    bout-to-bout comparison can't see this -- that whole bout already
    registers `has_artifact=True` from its other flagged epochs and gets
    skipped entirely by any rule that only looks at whether a *bout* is
    clean.

    Adds gap_trigger, reclassified, reclass_state columns.
    `reclassified & ~is_artifact` isolates the epochs this rule catches
    beyond the pure per-epoch rule.
    """
    d = df.sort_values("epoch_start_sec").reset_index(drop=True)
    is_artifact = d["is_artifact"].to_numpy()
    is_nrem = (d["state"] == "NREM").to_numpy()
    n = len(d)

    gap_trigger = np.zeros(n, dtype=bool)
    for i in range(n):
        if is_artifact[i] or not is_nrem[i]:
            continue
        lo, hi = max(0, i - gap_radius), min(n, i + gap_radius + 1)
        left = is_artifact[lo:i]
        right = is_artifact[i + 1 : hi]
        if left.any() and right.any():
            gap_trigger[i] = True

    flip = is_nrem & (is_artifact | gap_trigger)
    result = d.copy()
    result["gap_trigger"] = gap_trigger
    result["reclassified"] = flip
    result["reclass_state"] = np.where(flip, "Wake", result["state"])
    return result


def compute_emg_rms_per_epoch(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    fs: int = DEFAULT_FS,
    epoch_sec: int = DEFAULT_SPINDLE_EPOCH_SEC,
) -> pd.DataFrame:
    """Adds an `emg_rms` column: per-epoch RMS of the raw EMG channel, for
    every epoch in `df` (any state). Reads the whole EMG channel once and
    extracts every epoch via vectorized fancy indexing rather than one
    `edf.Reader` open per epoch -- checked at <1s for a 72h recording
    (~25k epochs), see emg_rms_wake_vs_nrem.ipynb's "Major-motion-artifact
    outlier detector" section.
    """
    edf_path = edf_path_for(edf_dir, animal_id)
    epoch_len = epoch_sec * fs
    n_samples = int(df["epoch_start_sec"].max() * fs) + epoch_len
    with edf.Reader(edf_path) as reader:
        reader.channels = [EMG_CHANNEL_IDX]
        emg = reader.read(start=0, stop=n_samples)[0]

    starts = np.round(df["epoch_start_sec"].to_numpy() * fs).astype(int)
    idx = starts[:, None] + np.arange(epoch_len)[None, :]
    rms = np.sqrt(np.mean(np.square(emg[idx]), axis=1))

    result = df.copy()
    result["emg_rms"] = rms
    return result


def compute_emg_zcr_per_epoch(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    fs: int = DEFAULT_FS,
    epoch_sec: int = DEFAULT_SPINDLE_EPOCH_SEC,
) -> pd.DataFrame:
    """Adds an `emg_zcr` column: per-epoch zero-crossing rate (mean-
    crossings/sec, DC-corrected per epoch) of the raw EMG channel, for
    every epoch in `df` (any state) -- same vectorized whole-channel-read
    approach as `compute_emg_rms_per_epoch`.

    Motivation: RMS is dominated by amplitude, which varies enormously
    across animals (whole-recording NREM median RMS ranges ~17-180uV
    across the animals checked in emg_rms_annotated_wake_vs_nrem.ipynb)
    and conflates two different things -- a periodic, narrowband cardiac
    spike train (see the confirmed ~9.3Hz ECG bleed-through) vs. genuine
    broadband muscle activation. Zero-crossing rate instead reflects the
    signal's frequency content, which is largely independent of absolute
    voltage scale -- a candidate for a feature that doesn't need
    per-animal renormalization the way the RMS gate's `population_median
    + k*MAD` does. Not yet validated against annotations -- see that
    notebook's zero-crossing-rate section for the real numbers.
    """
    edf_path = edf_path_for(edf_dir, animal_id)
    epoch_len = epoch_sec * fs
    n_samples = int(df["epoch_start_sec"].max() * fs) + epoch_len
    with edf.Reader(edf_path) as reader:
        reader.channels = [EMG_CHANNEL_IDX]
        emg = reader.read(start=0, stop=n_samples)[0]

    starts = np.round(df["epoch_start_sec"].to_numpy() * fs).astype(int)
    idx = starts[:, None] + np.arange(epoch_len)[None, :]
    epoch_samples = emg[idx]

    centered = epoch_samples - epoch_samples.mean(axis=1, keepdims=True)
    signs = np.sign(centered)
    signs[signs == 0] = 1  # flat (exactly-zero-centered) samples don't count as a crossing on their own
    n_crossings = np.sum(np.diff(signs, axis=1) != 0, axis=1)
    zcr = n_crossings / epoch_sec

    result = df.copy()
    result["emg_zcr"] = zcr
    return result


def compute_emg_median_subwindow_rms_per_epoch(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    fs: int = DEFAULT_FS,
    epoch_sec: int = DEFAULT_SPINDLE_EPOCH_SEC,
    subwin_sec: float = 1.0,
) -> pd.DataFrame:
    """Adds an `emg_rms_median_subwin` column: splits each epoch into
    `subwin_sec`-long sub-windows (default 1s, so 4 per 4s epoch), takes
    RMS within each sub-window, then the *median* across sub-windows --
    same vectorized whole-channel-read approach as
    `compute_emg_rms_per_epoch`, which this is a direct alternative to.

    Motivation: whole-epoch RMS averages uniformly over all 4s, so a
    single loud second (or a single quiet one) pulls the epoch-level
    number in either direction by exactly 1/4 regardless of what the
    other three seconds looked like. The median of four sub-window
    values is robust to exactly one outlier second in either
    direction -- a real hazard here, since epoch boundaries don't line
    up with either genuine movement bursts or individual cardiac beats.
    Not yet validated against annotations -- see
    emg_rms_annotated_wake_vs_nrem.ipynb for the real numbers.
    """
    edf_path = edf_path_for(edf_dir, animal_id)
    epoch_len = epoch_sec * fs
    subwin_len = int(round(subwin_sec * fs))
    n_subwins = epoch_len // subwin_len
    n_samples = int(df["epoch_start_sec"].max() * fs) + epoch_len
    with edf.Reader(edf_path) as reader:
        reader.channels = [EMG_CHANNEL_IDX]
        emg = reader.read(start=0, stop=n_samples)[0]

    starts = np.round(df["epoch_start_sec"].to_numpy() * fs).astype(int)
    idx = starts[:, None] + np.arange(epoch_len)[None, :]
    epoch_samples = emg[idx]

    trimmed = epoch_samples[:, : n_subwins * subwin_len].reshape(len(df), n_subwins, subwin_len)
    subwin_rms = np.sqrt(np.mean(np.square(trimmed), axis=2))
    median_rms = np.median(subwin_rms, axis=1)

    result = df.copy()
    result["emg_rms_median_subwin"] = median_rms
    return result


def population_emg_baseline(
    df: pd.DataFrame, state_col: str = "state", target_state: str = "NREM"
) -> tuple[float, float]:
    """(median, MAD) of `emg_rms` across every `target_state` epoch in
    `df`. Requires `df` to already have an `emg_rms` column (see
    `compute_emg_rms_per_epoch`) and to cover the animal's *whole*
    recording, not an annotated subset -- the point is a baseline that
    doesn't need per-animal human labels to compute, so it generalizes to
    every file. Deliberately not filtered by `is_artifact`: that flag is
    exactly what's unreliable per-animal, so trusting "SPINDLE says
    non-artifact" as the definition of "clean" would just launder the
    same bias back in (see emg_rms_wake_vs_nrem.ipynb's normalization
    section for why that was rejected). This only assumes most of an
    animal's NREM time is roughly typical, not that any specific epoch's
    label is trustworthy.
    """
    values = df.loc[df[state_col] == target_state, "emg_rms"]
    median = float(values.median())
    mad = float((values - median).abs().median())
    return median, mad


def reclassify_nrem_by_gap_fill_with_emg_gate(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    gap_radius: int = DEFAULT_GAP_RADIUS,
    k: float = DEFAULT_EMG_GATE_K,
) -> pd.DataFrame:
    """Same as `reclassify_nrem_by_gap_fill`, but an epoch only actually
    flips to Wake if it *also* clears an EMG-RMS gate: `emg_rms >
    population_median + k * population_mad`, using this animal's own
    whole-recording NREM population as the baseline (see
    `population_emg_baseline`). Strictly tightens the existing rule --
    can only leave epochs as NREM that the gap-fill rule alone would have
    flipped, never flip anything new. Adds `emg_rms`, `emg_gate_passes`,
    and `reclassified_pre_emg_gate` (the gap-fill-only flip decision,
    kept for comparison) alongside gap-fill's usual `gap_trigger`/
    `reclassified`/`reclass_state` columns -- `reclassified`/
    `reclass_state` are overwritten with the gated result.
    """
    gap_result = reclassify_nrem_by_gap_fill(df, gap_radius=gap_radius)
    gap_result = compute_emg_rms_per_epoch(gap_result, edf_dir, animal_id)
    median, mad = population_emg_baseline(gap_result)

    emg_gate_passes = gap_result["emg_rms"] > median + k * mad
    gated_flip = gap_result["reclassified"] & emg_gate_passes

    result = gap_result.copy()
    result["emg_gate_passes"] = emg_gate_passes
    result["reclassified_pre_emg_gate"] = gap_result["reclassified"]
    result["reclassified"] = gated_flip
    result["reclass_state"] = np.where(gated_flip, "Wake", result["state"])
    return result


def _subwindow_rms(x: npt.NDArray[np.floating], subwin_len: int) -> npt.NDArray[np.floating]:
    n = len(x) // subwin_len
    if n == 0:
        return np.array([])
    x = x[: n * subwin_len].reshape(n, subwin_len)
    return np.sqrt(np.mean(np.square(x), axis=1))


def compute_emg_eeg_correlation_for_epochs(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    fs: int = DEFAULT_FS,
    epoch_sec: int = DEFAULT_SPINDLE_EPOCH_SEC,
    context_sec: float = DEFAULT_EMG_EEG_CONTEXT_SEC,
    subwin_sec: float = DEFAULT_EMG_EEG_SUBWIN_SEC,
) -> pd.DataFrame:
    """Adds `emg_eeg_corr`: correlation between the EMG and (mean-LFC/RSC/
    LSC) EEG RMS envelopes over a +/-context_sec window around each epoch
    (envelopes built from subwin_sec sub-windows). A real movement
    artifact is mechanical, so it should show up on every channel at
    once (high correlation); a source local to the EMG lead alone (e.g.
    cardiac bleed-through) shouldn't correlate with EEG at all -- this
    separated Wake from NREM far better than any EMG-only feature tried
    in emg_noise/, including on CW0DI2/Mice-C366 (see
    emg_eeg_gate_wake_vs_nrem.ipynb).

    Only reads small per-epoch spans (one `edf.Reader` open, one
    `.read()` per row in `df`) rather than the whole recording --
    deliberately NOT vectorized like `compute_emg_rms_per_epoch`, since
    callers only need this for a candidate subset (e.g. gap-fill's own
    flip decisions), not every epoch in the file (checked at ~1.5ms/read,
    so a few thousand candidates is a few seconds, not the tens of
    thousands a whole-file pass would need).
    """
    edf_path = edf_path_for(edf_dir, animal_id)
    epoch_len = epoch_sec * fs
    context_len = int(context_sec * fs)
    subwin_len = int(subwin_sec * fs)

    corrs = np.full(len(df), np.nan)
    with edf.Reader(edf_path) as reader:
        reader.channels = EEG_CHANNEL_IDXS + [EMG_CHANNEL_IDX]
        n_total_samples = reader.shape[-1] if hasattr(reader, "shape") else None
        for i, epoch_start_sec in enumerate(df["epoch_start_sec"].to_numpy()):
            start_sample = int(round(epoch_start_sec * fs))
            lo = max(0, start_sample - context_len)
            hi = start_sample + epoch_len + context_len
            if n_total_samples is not None:
                hi = min(hi, n_total_samples)
            channels = reader.read(start=lo, stop=hi)
            eeg = np.mean(np.stack(channels[: len(EEG_CHANNEL_IDXS)]), axis=0)
            emg = channels[len(EEG_CHANNEL_IDXS)]

            emg_series = _subwindow_rms(emg, subwin_len)
            eeg_series = _subwindow_rms(eeg, subwin_len)
            if len(emg_series) >= 4 and emg_series.std() > 0 and eeg_series.std() > 0:
                corrs[i] = np.corrcoef(emg_series, eeg_series)[0, 1]

    result = df.copy()
    result["emg_eeg_corr"] = corrs
    return result


def reclassify_nrem_by_gap_fill_with_emg_eeg_gate(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    gap_radius: int = DEFAULT_GAP_RADIUS,
    corr_threshold: float = DEFAULT_EMG_EEG_CORR_THRESHOLD,
    context_sec: float = DEFAULT_EMG_EEG_CONTEXT_SEC,
    subwin_sec: float = DEFAULT_EMG_EEG_SUBWIN_SEC,
) -> pd.DataFrame:
    """Same idea as `reclassify_nrem_by_gap_fill_with_emg_gate`, but the
    gate is EMG-EEG cross-channel correlation instead of EMG RMS: an
    epoch only actually flips to Wake if it also clears
    `emg_eeg_corr > corr_threshold`. Only computes the (more expensive,
    per-epoch-read) correlation feature for epochs gap-fill already
    flagged, not the whole file. Adds `emg_eeg_corr`, `emg_eeg_gate_passes`,
    and `reclassified_pre_emg_gate`, and overwrites `reclassified`/
    `reclass_state` with the gated result -- same column conventions as
    the RMS-gate function, so callers can swap between them.
    """
    gap_result = reclassify_nrem_by_gap_fill(df, gap_radius=gap_radius)
    candidates = gap_result[gap_result["reclassified"]]
    scored = compute_emg_eeg_correlation_for_epochs(
        candidates, edf_dir, animal_id, context_sec=context_sec, subwin_sec=subwin_sec
    )

    result = gap_result.copy()
    result["emg_eeg_corr"] = np.nan
    result.loc[scored.index, "emg_eeg_corr"] = scored["emg_eeg_corr"].to_numpy()

    emg_eeg_gate_passes = result["emg_eeg_corr"] > corr_threshold
    gated_flip = result["reclassified"] & emg_eeg_gate_passes.fillna(False)

    result["emg_eeg_gate_passes"] = emg_eeg_gate_passes
    result["reclassified_pre_emg_gate"] = gap_result["reclassified"]
    result["reclassified"] = gated_flip
    result["reclass_state"] = np.where(gated_flip, "Wake", result["state"])
    return result


def reclassify_nrem_by_gap_fill_with_combined_gate(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    gap_radius: int = DEFAULT_GAP_RADIUS,
    k: float = DEFAULT_EMG_GATE_K,
    corr_threshold: float = DEFAULT_EMG_EEG_CORR_THRESHOLD,
    context_sec: float = DEFAULT_EMG_EEG_CONTEXT_SEC,
    subwin_sec: float = DEFAULT_EMG_EEG_SUBWIN_SEC,
) -> pd.DataFrame:
    """OR of the two gates: an epoch flips to Wake if gap-fill flags it
    AND (the EMG-RMS gate passes OR the EMG-EEG-correlation gate passes).
    Motivated by emg_eeg_gate_wake_vs_nrem.ipynb's validation -- the
    correlation gate alone is *worse* than the RMS gate alone (it barely
    suppresses Mice-1254/Mice-C414/Mice-C419's false positives even at a
    high threshold, while costing much more recall on the animals RMS
    already handled well) -- so this tries to keep RMS as the primary
    signal and use correlation only as a rescue for epochs RMS alone
    would wrongly block (e.g. CW0DI2/Mice-C366's low-amplitude real Wake).
    Adds every column both single-gate functions add, plus
    `combined_gate_passes`.
    """
    gap_result = reclassify_nrem_by_gap_fill(df, gap_radius=gap_radius)
    gap_result = compute_emg_rms_per_epoch(gap_result, edf_dir, animal_id)
    median, mad = population_emg_baseline(gap_result)
    emg_gate_passes = gap_result["emg_rms"] > median + k * mad

    candidates = gap_result[gap_result["reclassified"]]
    scored = compute_emg_eeg_correlation_for_epochs(
        candidates, edf_dir, animal_id, context_sec=context_sec, subwin_sec=subwin_sec
    )
    emg_eeg_corr = pd.Series(np.nan, index=gap_result.index)
    emg_eeg_corr.loc[scored.index] = scored["emg_eeg_corr"].to_numpy()
    emg_eeg_gate_passes = emg_eeg_corr > corr_threshold

    combined_gate_passes = emg_gate_passes | emg_eeg_gate_passes.fillna(False)
    gated_flip = gap_result["reclassified"] & combined_gate_passes

    result = gap_result.copy()
    result["emg_eeg_corr"] = emg_eeg_corr
    result["emg_gate_passes"] = emg_gate_passes
    result["emg_eeg_gate_passes"] = emg_eeg_gate_passes
    result["combined_gate_passes"] = combined_gate_passes
    result["reclassified_pre_emg_gate"] = gap_result["reclassified"]
    result["reclassified"] = gated_flip
    result["reclass_state"] = np.where(gated_flip, "Wake", result["state"])
    return result


def reclassify_nrem_by_gap_fill_with_noise_prob_gate(
    df: pd.DataFrame,
    gap_radius: int = DEFAULT_GAP_RADIUS,
    threshold: float = 0.7,
) -> pd.DataFrame:
    """Same idea as the EMG-RMS/EEG-correlation gates, but the gate is
    SPINDLE's own `noise_prob` column instead of a derived EMG feature --
    no EDF read needed, so this is cheap to compute (unlike the EMG gates,
    which need the raw recording). An epoch gap-fill flips to Wake only
    survives if its own `noise_prob > threshold` (default 0.7, stricter
    than `load_spindle_epochs`'s ~0.5 `is_artifact` cutoff).

    Structurally this gate mostly guts the gap-fill rule's own context
    mechanism rather than just tightening it: `gap_trigger` (see
    `reclassify_nrem_by_gap_fill`) only ever fires on epochs that are NOT
    themselves `is_artifact` -- i.e. `noise_prob` already below the ~0.5
    cutoff -- so essentially none of them can clear a 0.7 gate on their
    own `noise_prob`. A threshold this far above the artifact cutoff
    reduces the survivors to (a slightly stricter version of) the pure
    per-epoch rule (`reclassify_nrem_artifacts`), not gap-fill's
    validated context extension.

    Adds `noise_prob_gate_passes` and `reclassified_pre_noise_prob_gate`
    (gap-fill's original decision, kept for comparison) -- same column
    conventions as the EMG gate functions.
    """
    gap_result = reclassify_nrem_by_gap_fill(df, gap_radius=gap_radius)
    noise_prob_gate_passes = gap_result["noise_prob"] > threshold
    gated_flip = gap_result["reclassified"] & noise_prob_gate_passes

    result = gap_result.copy()
    result["noise_prob_gate_passes"] = noise_prob_gate_passes
    result["reclassified_pre_noise_prob_gate"] = gap_result["reclassified"]
    result["reclassified"] = gated_flip
    result["reclass_state"] = np.where(gated_flip, "Wake", result["state"])
    return result


def _lognormal_log_likelihood(x: npt.NDArray[np.floating], reference: npt.NDArray[np.floating]) -> npt.NDArray[np.floating]:
    """Log-likelihood of `x` under a log-normal fit (mean/std of log(reference))
    -- RMS distributions are consistently right-skewed and roughly
    symmetric on a log axis throughout this project's histograms (see
    emg_rms_annotated_wake_vs_nrem.ipynb), so log-normal is the natural
    parametric family here, not an arbitrary choice."""
    log_ref = np.log(reference)
    mu, sigma = log_ref.mean(), log_ref.std()
    log_x = np.log(x)
    return -0.5 * np.log(2 * np.pi * sigma**2) - (log_x - mu) ** 2 / (2 * sigma**2)


def reclassify_nrem_by_gap_fill_with_likelihood_gate(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    gap_radius: int = DEFAULT_GAP_RADIUS,
    clean_nrem_rms_override: npt.NDArray[np.floating] | None = None,
) -> pd.DataFrame:
    """EMG-RMS gate, but instead of a single population threshold
    (`reclassify_nrem_by_gap_fill_with_emg_gate`'s `median + k*MAD`),
    this classifies each gap-fill-flagged epoch against two reference
    populations for this animal -- "clean NREM" and `state=="Wake" &
    is_artifact` ("artifact Wake") -- and only lets the flip through if
    the epoch's `emg_rms` is *more likely under the artifact-Wake
    reference than the clean-NREM reference* (log-normal likelihood
    ratio, not just closer to one median: this accounts for each
    population's spread, not only its center, so a wide reference is
    harder to "win" against than a tight one at the same distance).

    `clean_nrem_rms_override`: by default the clean-NREM reference is
    this animal's own whole-recording `state=="NREM" & ~is_artifact`
    population -- but that population almost certainly still contains
    some undetected real movement (the entire premise of this
    investigation), which inflates its spread and blurs the exact
    boundary this method needs to be sharp. Pass an array here (e.g. the
    human-verified `clean_nrem_baseline` annotation's `emg_rms` values)
    to fit the clean-NREM side from a tighter, curated reference instead.

    Motivated by the same emg_rms_annotated_wake_vs_nrem.ipynb Part 3b
    comparison the EMG-RMS gate's own `population_median + k*MAD`
    baseline is built from -- clean-NREM and artifact-Wake are the two
    populations this project has already validated as informative
    references, just combined here as a likelihood ratio instead of a
    single fixed-k threshold. No k to tune, but not necessarily better in
    practice -- validated against annotations at 97.2%/68.5% precision/
    recall with the whole-recording reference, dominated by the plain
    k=5 RMS gate (99.6%/70.7%) on that same set; not yet validated with
    `clean_nrem_rms_override` supplied.

    Adds `emg_rms`, `likelihood_gate_passes`, and
    `reclassified_pre_likelihood_gate` (gap-fill's original decision) --
    same column conventions as the other gate functions.
    """
    gap_result = reclassify_nrem_by_gap_fill(df, gap_radius=gap_radius)
    gap_result = compute_emg_rms_per_epoch(gap_result, edf_dir, animal_id)

    is_nrem = gap_result["state"] == "NREM"
    is_wake = gap_result["state"] == "Wake"
    if clean_nrem_rms_override is not None:
        clean_nrem_rms = clean_nrem_rms_override
    else:
        clean_nrem_rms = gap_result.loc[is_nrem & ~gap_result["is_artifact"], "emg_rms"].to_numpy()
    artifact_wake_rms = gap_result.loc[is_wake & gap_result["is_artifact"], "emg_rms"].to_numpy()

    rms = gap_result["emg_rms"].to_numpy()
    ll_clean_nrem = _lognormal_log_likelihood(rms, clean_nrem_rms)
    ll_artifact_wake = _lognormal_log_likelihood(rms, artifact_wake_rms)
    likelihood_gate_passes = ll_artifact_wake > ll_clean_nrem

    gated_flip = gap_result["reclassified"] & likelihood_gate_passes

    result = gap_result.copy()
    result["likelihood_gate_passes"] = likelihood_gate_passes
    result["reclassified_pre_likelihood_gate"] = gap_result["reclassified"]
    result["reclassified"] = gated_flip
    result["reclass_state"] = np.where(gated_flip, "Wake", result["state"])
    return result


def context_summary_row(animal_id: str, context_df: pd.DataFrame) -> dict:
    """Headline per-animal reclassification-impact numbers: % of NREM
    epochs flipped by the pure rule vs. the gap-fill rule, and the
    gap-fill rule's additional catch (overall / dark / light).
    `context_df` must already have `reclassified` (gap-fill) and
    `is_artifact` (pure-rule-equivalent) columns -- i.e. the output of
    `reclassify_nrem_by_gap_fill`.
    """
    is_nrem = context_df["state"] == "NREM"
    n_nrem = int(is_nrem.sum())
    n_pure = int((is_nrem & context_df["is_artifact"]).sum())
    n_context = int((is_nrem & context_df["reclassified"]).sum())
    n_additional = n_context - n_pure
    additional_mask = is_nrem & context_df["reclassified"] & ~context_df["is_artifact"]
    dark = context_df["is_dark"]
    n_dark_nrem = int((is_nrem & dark).sum())
    n_light_nrem = int((is_nrem & ~dark).sum())
    return {
        "animal_id": animal_id,
        "n_nrem": n_nrem,
        "pct_nrem_flipped_pure": n_pure / n_nrem * 100 if n_nrem else np.nan,
        "pct_nrem_flipped_context": n_context / n_nrem * 100 if n_nrem else np.nan,
        "pct_additional_of_nrem": n_additional / n_nrem * 100 if n_nrem else np.nan,
        "pct_dark_additional": (
            (additional_mask & dark).sum() / n_dark_nrem * 100 if n_dark_nrem else np.nan
        ),
        "pct_light_additional": (
            (additional_mask & ~dark).sum() / n_light_nrem * 100 if n_light_nrem else np.nan
        ),
    }


def build_state_bouts(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    state_col: str = "state",
    target_state: str = "NREM",
    epoch_sec: int = DEFAULT_SPINDLE_EPOCH_SEC,
) -> pd.DataFrame:
    """One row per contiguous bout of `target_state` in `state_col`.

    Columns: start_epoch_idx, end_epoch_idx (exclusive), n_epochs,
    duration_sec, start_sec, hour_of_day, is_dark (of bout start),
    n_artifact_epochs, frac_artifact, has_artifact.

    Pass `state_col="reclass_state"` on a `reclassify_nrem_by_gap_fill`
    result to get post-reclassification bouts -- `len(...)` before vs.
    after (optionally filtered to `is_dark`) is this project's bout-count
    change metric.
    """
    d = df.sort_values("epoch_start_sec").reset_index(drop=True)
    is_state = (d[state_col] == target_state).to_numpy()
    is_artifact = d["is_artifact"].to_numpy()

    change = np.diff(is_state.astype(int))
    starts = np.where(change == 1)[0] + 1
    ends = np.where(change == -1)[0] + 1
    if is_state[0]:
        starts = np.r_[0, starts]
    if is_state[-1]:
        ends = np.r_[ends, len(is_state)]

    rec_start = read_edf_start_datetime(edf_path_for(edf_dir, animal_id))
    epoch_start_sec = d["epoch_start_sec"].to_numpy()

    rows = []
    for s, e in zip(starts, ends):
        n_epochs = int(e - s)
        n_artifact = int(is_artifact[s:e].sum())
        start_sec = float(epoch_start_sec[s])
        clock_time = rec_start + pd.to_timedelta(start_sec, unit="s")
        rows.append(
            {
                "start_epoch_idx": int(s),
                "end_epoch_idx": int(e),
                "n_epochs": n_epochs,
                "duration_sec": n_epochs * epoch_sec,
                "start_sec": start_sec,
                "hour_of_day": clock_time.hour,
                "is_dark": clock_time.hour in DARK_HOURS,
                "n_artifact_epochs": n_artifact,
                "frac_artifact": n_artifact / n_epochs,
                "has_artifact": n_artifact > 0,
            }
        )
    return pd.DataFrame(rows)


def bout_stats_row(animal_id: str, bouts: pd.DataFrame) -> dict:
    """Per-animal bout-level summary: count, artifact rate, durations.
    `bouts` is a `build_state_bouts` result -- call once on `state_col=
    "state"` (before) and once on `state_col="reclass_state"` (after) to
    compare."""
    n_bouts = len(bouts)
    n_artifact = int(bouts["has_artifact"].sum())
    artifact_dur = bouts.loc[bouts["has_artifact"], "duration_sec"]
    clean_dur = bouts.loc[~bouts["has_artifact"], "duration_sec"]
    dark = bouts[bouts["is_dark"]]
    light = bouts[~bouts["is_dark"]]
    return {
        "animal_id": animal_id,
        "n_bouts": n_bouts,
        "n_dark_bouts": len(dark),
        "n_light_bouts": len(light),
        "pct_bouts_with_artifact": n_artifact / n_bouts * 100 if n_bouts else np.nan,
        "median_duration_all_s": bouts["duration_sec"].median() if n_bouts else np.nan,
        "median_duration_artifact_s": artifact_dur.median() if len(artifact_dur) else np.nan,
        "median_duration_clean_s": clean_dur.median() if len(clean_dur) else np.nan,
        "pct_dark_bouts_with_artifact": dark["has_artifact"].mean() * 100 if len(dark) else np.nan,
        "pct_light_bouts_with_artifact": light["has_artifact"].mean() * 100 if len(light) else np.nan,
        "mean_frac_artifact_in_artifact_bouts": bouts.loc[bouts["has_artifact"], "frac_artifact"].mean() if n_artifact else np.nan,
        "median_frac_artifact_in_artifact_bouts": bouts.loc[bouts["has_artifact"], "frac_artifact"].median() if n_artifact else np.nan,
    }


def bout_count_delta(
    before_bouts: pd.DataFrame, after_bouts: pd.DataFrame, period: str = "whole"
) -> dict:
    """% change in bout count, before (`state_col="state"`) vs. after
    (`state_col="reclass_state"`) reclassification -- this project's
    Goal 2 file-ranking metric. `period`: "whole" (default, all bouts),
    "dark" (the Goal 2 bucketing signal), or "light".
    """
    if period == "whole":
        b, a = before_bouts, after_bouts
    elif period == "dark":
        b, a = before_bouts[before_bouts["is_dark"]], after_bouts[after_bouts["is_dark"]]
    elif period == "light":
        b, a = before_bouts[~before_bouts["is_dark"]], after_bouts[~after_bouts["is_dark"]]
    else:
        raise ValueError(f"period must be 'whole', 'dark', or 'light' (got {period!r})")
    n_before, n_after = len(b), len(a)
    pct_change = (n_after - n_before) / n_before * 100 if n_before else np.nan
    return {"n_bouts_before": n_before, "n_bouts_after": n_after, "pct_bout_count_change": pct_change}


def state_fraction_by_period(df: pd.DataFrame, state_col: str) -> pd.DataFrame:
    """% of time in each state (Wake/NREM/REM), split Light/Dark/Total."""
    rows = []
    periods = [
        ("Light", ~df["is_dark"]),
        ("Dark", df["is_dark"]),
        ("Total", pd.Series(True, index=df.index)),
    ]
    for period, period_mask in periods:
        sub = df[period_mask]
        counts = sub[state_col].value_counts(normalize=True)
        for state in STATE_ORDER:
            rows.append({"period": period, "state": state, "frac_time": counts.get(state, 0.0)})
    return pd.DataFrame(rows)


def clock_bin_hours(
    epoch_start_sec: npt.NDArray[np.floating],
    rec_start,
    bin_hours: float = 2.0,
    begin_hr: int = 6,
) -> npt.NDArray[np.floating]:
    """Clock-anchored bin label for each epoch (e.g. 6, 8, 10, ..., 30, 32,
    ...) -- NOT elapsed time since this animal's own recording start, and
    NOT folded/wrapped back to 0-23 either. Each animal is anchored to the
    most recent `begin_hr`:00 at/before its own rec_start, then counted
    forward in whole `bin_hours` steps without resetting at 24 -- so the
    label keeps climbing across multiple days (hour 30 is a real distinct
    time from hour 6, not the same clock hour a day later).

    The one thing this construction *does* guarantee across every animal,
    regardless of when they started: `label % 24` always equals that
    epoch's true wall-clock hour-of-day. Two animals that started
    recording a year apart, at completely different times of day, will
    still agree on which of their bins are "hour 21" in the sense that
    both bins are real 9pm-10pm local time -- convenient for cross-animal
    comparisons keyed on circadian phase (e.g. is_dark). It does NOT mean
    the same label corresponds to the same elapsed recording duration, or
    the same calendar date, for different animals -- both of those
    genuinely differ per animal and this label doesn't track either."""
    from datetime import timedelta

    anchor = rec_start.replace(hour=begin_hr, minute=0, second=0, microsecond=0)
    if anchor > rec_start:
        anchor -= timedelta(days=1)
    offset_sec = (rec_start - anchor).total_seconds()
    bin_size_sec = int(bin_hours * 3600)
    elapsed_sec = offset_sec + epoch_start_sec
    return begin_hr + (elapsed_sec // bin_size_sec) * bin_hours


def bout_count_by_clock_bin(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    state_col: str,
    bin_hours: float = 2.0,
    begin_hr: int = 6,
) -> pd.DataFrame:
    """Bout counts per state, per multi-day clock-aligned bin -- one row
    per (bin_start_hour, state)."""
    rec_start = read_edf_start_datetime(edf_path_for(edf_dir, animal_id))
    d = df.sort_values("epoch_start_sec").reset_index(drop=True)
    d["bin_start_hour"] = clock_bin_hours(d["epoch_start_sec"].to_numpy(), rec_start, bin_hours, begin_hr)

    rows = []
    for state in STATE_ORDER:
        is_state = (d[state_col] == state).to_numpy()
        starts = np.where(np.diff(is_state.astype(int)) == 1)[0] + 1
        if is_state[0]:
            starts = np.r_[0, starts]
        bin_of_bout = d["bin_start_hour"].to_numpy()[starts]
        counts = pd.Series(bin_of_bout).value_counts()
        for bin_val, n in counts.items():
            rows.append({"bin_start_hour": bin_val, "state": state, "n_bouts": int(n)})
    return pd.DataFrame(rows)


def time_in_state_by_clock_bin(
    df: pd.DataFrame,
    edf_dir: str | Path,
    animal_id: str,
    state_col: str,
    bin_hours: float = 2.0,
    begin_hr: int = 6,
) -> pd.DataFrame:
    """% time per state, per multi-day clock-aligned bin -- one row per
    (bin_start_hour, state)."""
    rec_start = read_edf_start_datetime(edf_path_for(edf_dir, animal_id))
    d = df.copy()
    d["bin_start_hour"] = clock_bin_hours(d["epoch_start_sec"].to_numpy(), rec_start, bin_hours, begin_hr)

    rows = []
    for bin_val, g in d.groupby("bin_start_hour"):
        counts = g[state_col].value_counts(normalize=True)
        for state in STATE_ORDER:
            rows.append({"bin_start_hour": bin_val, "state": state, "frac_time": counts.get(state, 0.0)})
    return pd.DataFrame(rows)


def compute_full_reclass_pipeline(
    spindle_csv: str | Path,
    animal_id: str,
    edf_dir: str | Path,
    emg_gate_threshold_pct: float = 10.0,
) -> tuple[pd.DataFrame, bool]:
    """Full per-epoch pipeline for one file, end to end: SPINDLE's raw
    state, the gap-fill (radius=1) reclassified state, and -- only if
    this file's own light-period NREM bout count increased by more than
    `emg_gate_threshold_pct` after gap-fill -- the EMG-RMS-gated state
    too (this project's EMG-gate trigger condition; see
    `reclassify_nrem_by_gap_fill_with_emg_gate`, k=`DEFAULT_EMG_GATE_K`).

    Returns `(df, emg_gate_applicable)`. `df` columns: `epoch_idx`,
    `epoch_start_sec`, `clock_time`, `is_dark`, `is_artifact`, `rawstate`
    (SPINDLE's own call), `gapfill_state` (post gap-fill k=1),
    `final_state` (the EMG-gated state if `emg_gate_applicable` else
    `gapfill_state` -- the actual end-of-pipeline output this project
    treats as ground truth for downstream use, e.g. selecting and
    displaying transitions).
    """
    epochs = load_spindle_epochs(spindle_csv, animal_id, edf_dir)
    gapfill = reclassify_nrem_by_gap_fill(epochs)

    before_bouts = build_state_bouts(gapfill, edf_dir, animal_id, state_col="state")
    after_bouts = build_state_bouts(gapfill, edf_dir, animal_id, state_col="reclass_state")
    delta = bout_count_delta(before_bouts, after_bouts, period="light")
    pct_change = delta["pct_bout_count_change"]
    emg_gate_applicable = bool(pct_change == pct_change and pct_change > emg_gate_threshold_pct)  # NaN-safe (NaN != NaN)

    if emg_gate_applicable:
        gated = reclassify_nrem_by_gap_fill_with_emg_gate(epochs, edf_dir, animal_id)
        final_state = gated["reclass_state"]
    else:
        final_state = gapfill["reclass_state"]

    result = pd.DataFrame(
        {
            "epoch_idx": gapfill["epoch_idx"],
            "epoch_start_sec": gapfill["epoch_start_sec"],
            "is_dark": gapfill["is_dark"],
            "is_artifact": gapfill["is_artifact"],
            "rawstate": gapfill["state"],
            "gapfill_state": gapfill["reclass_state"],
            "final_state": final_state,
        }
    )
    rec_start = read_edf_start_datetime(edf_path_for(edf_dir, animal_id))
    result["clock_time"] = rec_start + pd.to_timedelta(result["epoch_start_sec"], unit="s")
    return result, emg_gate_applicable
