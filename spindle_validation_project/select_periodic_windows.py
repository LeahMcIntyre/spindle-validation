"""Selects periodic 5-minute census windows for spindle_validation_project.

Part of the SPINDLE staging-validation effort (see PLAN.md, this
directory) -- distinct from nrem_reclass_project, which validates the
gap-fill reclassification rule specifically. This script only picks
*where* to look; annotate_spindle_validation.py (MODE="periodic") is
what actually records verdicts, blind (no algorithm state shown until
after a verdict is recorded -- see that script's docstring).

For every file in file_manifest.csv: one 4-hour segment starting at
recording start (0h, 4h, 8h, ...), and in each segment a 5-minute
window at a RANDOM position inside it (not fixed at the segment start)
-- every epoch in that window gets annotated (all states, not just
NREM, a general staging spot-check). Randomness is seeded per window_id
(same stable-hash-style determinism as assigned_rater below), not from
system entropy -- rerunning this script after file_manifest.csv changes
never moves an already-selected window, only adds new ones.

Output: targets/periodic_windows.csv, one row per window. Pure function
of file_manifest.csv + the constants below -- safe to rerun any time
the manifest changes. window_id encodes (dataset, animal_id, window
index within that file), so existing ids never change when new files
are appended.

Usage: run from this repo's root with the ebb-dev environment active.
    python spindle_validation_project/select_periodic_windows.py
"""

import hashlib
import random
from pathlib import Path

import pandas as pd

from ebb.core.naming import parse_animal_id
from ebb.io.edf_header import read_edf_start_datetime

import lib.nrem_reclass as nr
from datasets import DATASETS, RATERS

SCRIPT_DIR = Path(__file__).resolve().parent
FILE_MANIFEST = SCRIPT_DIR / "file_manifest.csv"
TARGETS_DIR = SCRIPT_DIR / "targets"
OUT_CSV = TARGETS_DIR / "periodic_windows.csv"

PERIODIC_INTERVAL_HOURS = 4
PERIODIC_WINDOW_MIN = 5
EPOCH_SEC = 4  # SPINDLE's own epoch length


def _stable_seed(s: str) -> int:
    """Deterministic seed from a string -- same idea as the rater-hash
    below, applied to random.Random() instead of a mod-2 bucket, so a
    window's randomly-chosen position is reproducible across reruns."""
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % (2**32)


def assign_rater(target_id: str) -> str:
    """Stable hash -> rater. Deterministic per id, so re-running
    selection never reshuffles already-assigned targets."""
    h = int(hashlib.sha256(target_id.encode()).hexdigest(), 16)
    return RATERS[h % len(RATERS)]


def windows_for_file(dataset: str, animal_id: str, combo: str) -> list[dict]:
    project_root = DATASETS[dataset]
    edf_dir = project_root / "standard"
    edf_path = nr.edf_path_for(edf_dir, animal_id)
    rec_start = read_edf_start_datetime(edf_path)

    spindle_dir = project_root / "spindle_outputs" / combo
    spindle_csv = next(p for p in spindle_dir.glob("*_labels.csv") if parse_animal_id(p) == animal_id)
    epochs = nr.load_spindle_epochs(spindle_csv, animal_id, edf_dir)
    n_total_epochs = len(epochs)

    interval_epochs = int(PERIODIC_INTERVAL_HOURS * 3600 / EPOCH_SEC)
    window_epochs = int(PERIODIC_WINDOW_MIN * 60 / EPOCH_SEC)

    rows = []
    idx = 0
    seg_start_epoch = 0
    while seg_start_epoch < n_total_epochs:
        seg_end_epoch = min(seg_start_epoch + interval_epochs, n_total_epochs)
        available_epochs = seg_end_epoch - seg_start_epoch
        if available_epochs >= window_epochs:
            window_id = f"{dataset}_{animal_id}_p{idx:02d}"
            rng = random.Random(_stable_seed(window_id))
            max_offset_epochs = available_epochs - window_epochs
            offset_epochs = rng.randrange(0, max_offset_epochs + 1) if max_offset_epochs > 0 else 0
            window_start_epoch = seg_start_epoch + offset_epochs
            window_start_sec = window_start_epoch * EPOCH_SEC
            window_end_sec = (window_start_epoch + window_epochs) * EPOCH_SEC
            rows.append(
                {
                    "window_id": window_id,
                    "dataset": dataset,
                    "animal_id": animal_id,
                    "combo": combo,
                    "segment_start_sec": seg_start_epoch * EPOCH_SEC,
                    "window_start_sec": float(window_start_sec),
                    "window_end_sec": float(window_end_sec),
                    "window_start_clock": (rec_start + pd.to_timedelta(window_start_sec, unit="s")).isoformat(),
                    "n_epochs": window_epochs,
                    "assigned_rater": assign_rater(window_id),
                }
            )
            idx += 1
        seg_start_epoch += interval_epochs
    return rows


def main() -> None:
    manifest = pd.read_csv(FILE_MANIFEST)
    all_rows = []
    for _, row in manifest.iterrows():
        all_rows.extend(windows_for_file(row["dataset"], row["animal_id"], row["combo"]))

    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(OUT_CSV, index=False)
    print(f"{len(all_rows)} periodic windows -> {OUT_CSV}")


if __name__ == "__main__":
    main()
