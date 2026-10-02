"""Go through RATER's own Unsures file after file: opens
annotate_spindle_validation.py in MODE="unsure" for the file with the
most Unsures left (unsure_progress.unsure_table), and when you close the
viewer (and panel), asks before opening the next. Files in SKIP are
never opened; neither are files with no Unsures left. Each file is
offered once per run, even if you close it with Unsures still left --
rerun to come back to those. Counts are recomputed before every file,
so quitting and rerunning picks up where you left off.

Each file runs in its own process (Qt only allows one QApplication per
process), with MODE/RATER/FILE set on the imported module -- the
constants at the top of annotate_spindle_validation.py are left alone.
run_reconcile_review.py reuses review_loop for MODE="reconcile".

Usage: run from this repo's root with the ebb-dev environment active.
    python spindle_validation_project/run_unsure_review.py
"""

import subprocess
import sys
from pathlib import Path

from unsure_progress import unsure_table

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

RATER = "A"
SKIP = {
    ("PHP_6_week", "CW3209"),
    ("PHP_pre", "CW0DA1"),  # already reconciled
    ("PHP_pre", "CW0DI1"),  # already reconciled
}

LAUNCH = """
import sys
sys.path[:0] = [{here!r}, {root!r}]
import annotate_spindle_validation as a
{settings}
a.main()
"""


def unsure_todo(raters: tuple[str, ...]):
    """todo function for review_loop: files with Unsures left among
    `raters`, most first."""

    def todo() -> list[tuple[str, str, str]]:
        return [
            (r["dataset"], r["animal_id"], f"{r['periodic_left']} periodic + {r['transition_left']} transition Unsures")
            for _, r in unsure_table(raters).iterrows()
            if r["total_left"] > 0
        ]

    return todo


def review_loop(mode: str, todo, skip: set, settings: dict) -> None:
    """Open annotate_spindle_validation.py in `mode` for each file
    todo() returns -- (dataset, animal_id, summary) rows, in order,
    recomputed before every file -- skipping `skip`. `settings` are
    extra module constants to set (e.g. RATER, RECONCILED_BY)."""
    skipped: set = set(skip)  # plus files opened or skipped this run
    while True:
        rows = [r for r in todo() if (r[0], r[1]) not in skipped]
        if not rows:
            print("No more files with anything left (outside SKIP).")
            return
        dataset, animal_id, summary = rows[0]
        print(f"\n{len(rows)} files left. Next: {dataset} {animal_id} ({summary})")
        choice = input("Enter = open it, s = skip this file, q = quit: ").strip().lower()
        if choice == "q":
            return
        skipped.add((dataset, animal_id))
        if choice == "s":
            continue
        assignments = {"MODE": mode, **settings, "FILE": (dataset, animal_id)}
        code = LAUNCH.format(
            here=str(HERE),
            root=str(REPO_ROOT),
            settings="\n".join(f"a.{k} = {v!r}" for k, v in assignments.items()),
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT)
        if result.returncode != 0:
            print(f"Annotation tool exited with code {result.returncode} on {dataset} {animal_id}.")
            if input("Continue to the next file? [y/N]: ").strip().lower() != "y":
                return


def main() -> None:
    review_loop("unsure", unsure_todo((RATER,)), SKIP, {"RATER": RATER})


if __name__ == "__main__":
    main()
