"""Reconcile file after file: same loop as run_unsure_review.py, but
opens annotate_spindle_validation.py in MODE="reconcile" -- every
rater's Unsures for the file not yet in results/reconciled.csv, recorded
as joint calls by RECONCILED_BY. Files are offered most-Unsures-left
first, counting all raters; files with nothing left to reconcile
(e.g. PHP_pre CW0DA1/CW0DI1) are never opened, and neither is SKIP.

Usage: run from this repo's root with the ebb-dev environment active.
    python spindle_validation_project/run_reconcile_review.py
"""

from run_unsure_review import review_loop, unsure_todo
from unsure_progress import RATERS

RECONCILED_BY = "A+B"
SKIP = {
    ("PHP_6_week", "CW3209"),  # rater A's own Unsure review not done yet
}


def main() -> None:
    review_loop("reconcile", unsure_todo(RATERS), SKIP, {"RECONCILED_BY": RECONCILED_BY})


if __name__ == "__main__":
    main()
