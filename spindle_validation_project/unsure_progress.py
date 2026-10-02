"""Unsure progress, computed from results/ (nothing to hand-edit, nothing to
commit). Per file, per mode (periodic / transition): how many epochs were
marked Unsure, how many of those are already in results/reconciled.csv, and
how many are still left to reconcile.

Only needs pandas -- no ebb install or data access.

Usage: python spindle_validation_project/unsure_progress.py [A|B]
With a rater, counts only that rater's Unsure epochs and names the next file
with Unsure epochs left to reconcile.
Files are ranked by total_left, most first.
"""

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RATERS = ("A", "B")
MODES = ("periodic", "transition")


def _latest(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["target_id", "dataset", "animal_id", "epoch_idx", "human_verdict"])
    df = pd.read_csv(path)
    return df.drop_duplicates(subset=["target_id", "epoch_idx"], keep="last")


def unsure_table(raters: tuple[str, ...] = RATERS) -> pd.DataFrame:
    """One row per manifest file: Unsure / reconciled / left counts per
    mode for `raters`, ranked by total_left (most first)."""
    manifest = pd.read_csv(HERE / "file_manifest.csv")
    rec_path = HERE / "results" / "reconciled.csv"
    reconciled = set()
    if rec_path.exists():
        rec = pd.read_csv(rec_path)
        reconciled = set(zip(rec["target_id"], rec["epoch_idx"].astype(int)))

    unsure = {}
    for mode in MODES:
        frames = [_latest(HERE / "results" / f"{mode}_{r}.csv") for r in raters]
        df = pd.concat(frames, ignore_index=True)
        unsure[mode] = df[df["human_verdict"] == "Unsure"]

    rows = []
    for _, f in manifest.iterrows():
        ds, animal = f["dataset"], f["animal_id"]
        row = {"dataset": ds, "animal_id": animal}
        total_left = 0
        for mode in MODES:
            u = unsure[mode]
            u = u[(u.dataset == ds) & (u.animal_id == animal)]
            n_rec = sum((tid, int(ei)) in reconciled for tid, ei in zip(u["target_id"], u["epoch_idx"]))
            left = len(u) - n_rec
            row[f"{mode}_unsure"] = len(u)
            row[f"{mode}_reconciled"] = n_rec
            row[f"{mode}_left"] = left
            total_left += left
        row["total_left"] = total_left
        rows.append(row)

    return pd.DataFrame(rows).sort_values("total_left", ascending=False, kind="stable")


def main() -> None:
    rater = sys.argv[1].upper() if len(sys.argv) > 1 else None
    if rater and rater not in RATERS:
        raise SystemExit(f"rater must be one of {RATERS}")
    out = unsure_table((rater,) if rater else RATERS)
    who = f"Rater {rater}" if rater else "All raters"
    print(f"{who}: Unsure epochs per file (unsure = marked Unsure, reconciled = resolved in reconciled.csv)\n")
    print(out.to_string(index=False))

    print()
    for mode in MODES:
        n = int(out[f"{mode}_unsure"].sum())
        r = int(out[f"{mode}_reconciled"].sum())
        print(f"{mode:<10}  {n} Unsure, {r} reconciled, {n - r} left")
    todo = out[out.total_left > 0]
    if todo.empty:
        print("\nNo Unsure epochs left to reconcile.")
    else:
        nxt = todo.iloc[0]
        print(f"\n{len(todo)} files with Unsure left. Next: FILE = (\"{nxt.dataset}\", \"{nxt.animal_id}\")")


if __name__ == "__main__":
    main()
