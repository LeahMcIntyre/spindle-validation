"""Re-check your own transition marks file after file: same loop as
run_unsure_review.py, but opens annotate_spindle_validation.py in
MODE="transition_review" -- RATER's transitions whose latest verdict is
in REVIEW_VERDICTS (that module's constant, default "No transition"),
with the pre/post boxes pre-filled from your mark. Files are offered
most-left first. Like the mode itself, a mark that already has both
pre/post states counts as done when REVIEW_SKIP_FILLED is on, so
rerunning only offers what's left.

Usage: run from this repo's root with the ebb-dev environment active.
    python spindle_validation_project/run_transition_review.py
"""

from pathlib import Path

import pandas as pd

from annotate_spindle_validation import REVIEW_SKIP_FILLED, REVIEW_VERDICTS
from run_unsure_review import review_loop

HERE = Path(__file__).resolve().parent

RATER = "A"
SKIP: set = set()


def todo() -> list[tuple[str, str, str]]:
    path = HERE / "results" / f"transition_{RATER}.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path).drop_duplicates(subset=["target_id", "epoch_idx"], keep="last")
    df = df[df["human_verdict"].isin(REVIEW_VERDICTS)]
    if REVIEW_SKIP_FILLED:
        filled = df["corrected_pre_state"].notna() & df["corrected_post_state"].notna()
        df = df[~filled]
    counts = df.groupby(["dataset", "animal_id"]).size().sort_values(ascending=False, kind="stable")
    label = " / ".join(REVIEW_VERDICTS)
    return [(ds, animal, f"{n} marked {label}") for (ds, animal), n in counts.items()]


def main() -> None:
    review_loop("transition_review", todo, SKIP, {"RATER": RATER})


if __name__ == "__main__":
    main()
