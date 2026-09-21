"""Selects transition-epoch samples for spindle_validation_project.

Complements select_periodic_windows.py: instead of a fixed time grid,
this samples epochs at real Wake<->NREM<->REM state changes -- but on
the FINAL pipeline state (after gap-fill reclassification, and after
the EMG-RMS gate too when this file's own light-period NREM bout count
increased enough to trigger it -- see
lib.nrem_reclass.compute_full_reclass_pipeline), not SPINDLE's raw
state. This matters: the whole point of this sample is validating
whether the algorithm's claimed transitions (after everything this
project does to the raw SPINDLE output) are real, not whether SPINDLE's
own first-pass staging has real transitions.

Per file: every epoch immediately after a final-state change is a
"transition epoch" (pre_state = previous epoch's final state,
post_state = this epoch's final state), classified into:
  - nrem_to_wake / wake_to_nrem
  - rem_involving (REM on either side: Wake<->REM or NREM<->REM,
    either direction -- not split further, per PLAN.md)
and evenly-spaced-index sampled within each type (not random) so the
selected epochs spread across the whole recording rather than
clustering wherever transitions happen to be denser for that file.
Target counts per file: TRANSITION_COUNTS below (45 / 45 / 10, per
PLAN.md) -- if a file has fewer real transitions of a type than the
target count, every one of them is taken.

Output: targets/transition_epochs.csv, one row per sampled epoch,
carrying all three pipeline states (rawstate, gapfill_state, and
emg_gate_state when applicable) for the boundary epoch itself, plus
the pre/post final-state pair the transition verdict actually judges.
Same rerun-safety and assigned_rater scheme as select_periodic_windows.py.

Usage: run from this repo's root with the ebb-dev environment active.
    python spindle_validation_project/select_transition_epochs.py
"""

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from ebb.core.naming import parse_animal_id

import lib.nrem_reclass as nr
from datasets import DATASETS, RATERS

SCRIPT_DIR = Path(__file__).resolve().parent
FILE_MANIFEST = SCRIPT_DIR / "file_manifest.csv"
TARGETS_DIR = SCRIPT_DIR / "targets"
OUT_CSV = TARGETS_DIR / "transition_epochs.csv"

TRANSITION_COUNTS = {"nrem_to_wake": 45, "wake_to_nrem": 45, "rem_involving": 10}


def assign_rater(target_id: str) -> str:
    h = int(hashlib.sha256(target_id.encode()).hexdigest(), 16)
    return RATERS[h % len(RATERS)]


def classify_transition(pre_state: str, post_state: str) -> str | None:
    if pre_state == "NREM" and post_state == "Wake":
        return "nrem_to_wake"
    if pre_state == "Wake" and post_state == "NREM":
        return "wake_to_nrem"
    if pre_state == "REM" or post_state == "REM":
        return "rem_involving"
    return None


def evenly_spaced_sample(items: list, n: int) -> list:
    """Picks up to n items spread evenly across items by position, not
    randomly -- keeps the sample covering the whole recording instead
    of clustering wherever transitions of that type happen to be
    denser for that file."""
    if len(items) <= n:
        return items
    idxs = sorted(set(np.linspace(0, len(items) - 1, n).round().astype(int)))
    return [items[i] for i in idxs]


def transitions_for_file(dataset: str, animal_id: str, combo: str) -> list[dict]:
    project_root = DATASETS[dataset]
    edf_dir = project_root / "standard"
    spindle_dir = project_root / "spindle_outputs" / combo
    spindle_csv = next(p for p in spindle_dir.glob("*_labels.csv") if parse_animal_id(p) == animal_id)

    pipeline, emg_gate_applicable = nr.compute_full_reclass_pipeline(spindle_csv, animal_id, edf_dir)
    pipeline = pipeline.sort_values("epoch_start_sec").reset_index(drop=True)

    states = pipeline["final_state"].to_numpy()
    by_type: dict[str, list[dict]] = {t: [] for t in TRANSITION_COUNTS}
    for i in range(1, len(pipeline)):
        if states[i] == states[i - 1]:
            continue
        ttype = classify_transition(states[i - 1], states[i])
        if ttype is None:
            continue
        row = pipeline.iloc[i]
        by_type[ttype].append(
            {
                "epoch_idx": int(row["epoch_idx"]),
                "epoch_start_sec": float(row["epoch_start_sec"]),
                "pre_state": states[i - 1],
                "post_state": states[i],
                "algorithm_rawstate": row["rawstate"],
                "algorithm_gapfill_state": row["gapfill_state"],
                "algorithm_emg_gate_state": row["final_state"] if emg_gate_applicable else "",
                "algorithm_is_artifact": bool(row["is_artifact"]),
            }
        )

    rows = []
    for ttype, target_n in TRANSITION_COUNTS.items():
        sampled = evenly_spaced_sample(by_type[ttype], target_n)
        for j, item in enumerate(sampled):
            epoch_id = f"{dataset}_{animal_id}_t{j:03d}_{ttype}"
            rows.append(
                {
                    "epoch_id": epoch_id,
                    "dataset": dataset,
                    "animal_id": animal_id,
                    "combo": combo,
                    "transition_type": ttype,
                    **item,
                    "emg_gate_applicable": emg_gate_applicable,
                    "assigned_rater": assign_rater(epoch_id),
                }
            )
    return rows


def main() -> None:
    manifest = pd.read_csv(FILE_MANIFEST)
    all_rows = []
    for _, row in manifest.iterrows():
        all_rows.extend(transitions_for_file(row["dataset"], row["animal_id"], row["combo"]))

    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(OUT_CSV, index=False)
    print(f"{len(all_rows)} transition epochs -> {OUT_CSV}")


if __name__ == "__main__":
    main()
