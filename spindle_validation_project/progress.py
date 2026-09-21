"""Annotation progress, computed from targets/ and results/ (nothing to
hand-edit, nothing to commit). Per file, per rater, per mode:
done / assigned targets, plus Unsure epochs still awaiting reconcile.

Only needs pandas -- no ebb install or data access.

Usage: python spindle_validation_project/progress.py [A|B]
With a rater, shows only that rater's columns, remaining counts, and the
next file to open. state = annotate (unmarked targets left) / reconcile
(nothing unmarked, only Unsure to resolve) / done.
"""

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RATERS = ("A", "B")


def _latest(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["target_id", "epoch_idx", "human_verdict"])
    df = pd.read_csv(path)
    return df.drop_duplicates(subset=["target_id", "epoch_idx"], keep="last")


def main() -> None:
    manifest = pd.read_csv(HERE / "file_manifest.csv")
    windows = pd.read_csv(HERE / "targets" / "periodic_windows.csv")
    transitions = pd.read_csv(HERE / "targets" / "transition_epochs.csv")
    results = {
        (mode, r): _latest(HERE / "results" / f"{mode}_{r}.csv")
        for mode in ("periodic", "transition")
        for r in RATERS
    }
    rec_path = HERE / "results" / "reconciled.csv"
    reconciled = set()
    if rec_path.exists():
        rec = pd.read_csv(rec_path)
        reconciled = set(zip(rec["target_id"], rec["epoch_idx"].astype(int)))

    rows = []
    for _, f in manifest.iterrows():
        ds, animal = f["dataset"], f["animal_id"]
        row = {"dataset": ds, "animal_id": animal}
        pending_unsure = 0
        left = {r: {} for r in RATERS}
        own_unsure = {r: 0 for r in RATERS}
        for r in RATERS:
            w = windows[(windows.dataset == ds) & (windows.animal_id == animal) & (windows.assigned_rater == r)]
            t = transitions[(transitions.dataset == ds) & (transitions.animal_id == animal) & (transitions.assigned_rater == r)]
            for mode, total, ids in (
                ("periodic", int(w["n_epochs"].sum()), set(w["window_id"])),
                ("transition", len(t), set(t["epoch_id"])),
            ):
                res = results[(mode, r)]
                mine = res[res["target_id"].isin(ids)]
                unsure = mine[mine["human_verdict"] == "Unsure"]
                done = len(mine) - len(unsure)
                row[f"{mode[:5]}_{r}"] = f"{done}/{total}"
                left[r][mode] = total - len(mine)  # unmarked only; Unsure is marked
                n_pending = sum(
                    (tid, int(ei)) not in reconciled for tid, ei in zip(unsure["target_id"], unsure["epoch_idx"])
                )
                pending_unsure += n_pending
                own_unsure[r] += n_pending
        row["unsure_to_reconcile"] = pending_unsure
        for r in RATERS:
            row[f"left_periodic_{r}"] = left[r]["periodic"]
            row[f"left_transition_{r}"] = left[r]["transition"]
            unmarked = left[r]["periodic"] + left[r]["transition"]
            row[f"state_{r}"] = "annotate" if unmarked else ("reconcile" if own_unsure[r] else "done")
        any_unmarked = any(row[f"state_{r}"] == "annotate" for r in RATERS)
        row["state"] = "annotate" if any_unmarked else ("reconcile" if pending_unsure else "done")
        rows.append(row)

    out = pd.DataFrame(rows)
    rater = sys.argv[1].upper() if len(sys.argv) > 1 else None
    if rater:
        if rater not in RATERS:
            raise SystemExit(f"rater must be one of {RATERS}")
        view = out[["dataset", "animal_id", f"perio_{rater}", f"trans_{rater}"]].copy()
        view["periodic_left"] = out[f"left_periodic_{rater}"]
        view["transition_left"] = out[f"left_transition_{rater}"]
        view["state"] = out[f"state_{rater}"]
        print(f"Rater {rater}: done/assigned\n")
        print(view.to_string(index=False))
        todo = view[view.state == "annotate"]
        if todo.empty:
            print("\nNo unmarked targets left.")
        else:
            nxt = todo.iloc[0]
            print(f"\n{len(todo)} files with unmarked targets. Next: FILE = (\"{nxt.dataset}\", \"{nxt.animal_id}\")")
        n_rec = int((view.state == "reconcile").sum())
        if n_rec:
            print(f"{n_rec} more files have only Unsure epochs left (reconcile).")
        return
    out = out[[c for c in out.columns if not c.startswith(("left_", "state_"))]]
    print("done/assigned (Unsure epochs count as not done until re-marked or reconciled)\n")
    print(out.to_string(index=False))
    tot_unsure = int(out["unsure_to_reconcile"].sum())
    print(f"\n{tot_unsure} Unsure epochs awaiting reconcile across all files")


if __name__ == "__main__":
    main()
