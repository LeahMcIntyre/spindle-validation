# spindle-validation

Two-rater validation of SPINDLE sleep staging (raw -> gap-fill
reclassification -> EMG gate) against human judgment. Design and
decisions: `spindle_validation_project/PLAN.md`; history:
`HANDOFF.md`.

## Setup
1. Clone the `ebb` repo as a sibling of this one; `pip install -r requirements.txt` in an env with PyQt6/pandas.
2. Edit `spindle_validation_project/datasets.py`: point each dataset at the data on your machine/server, then run
   `git update-index --skip-worktree spindle_validation_project/datasets.py` so local paths never get committed.
3. In `spindle_validation_project/annotate_spindle_validation.py` set `RATER` ("A" or "B"), `MODE` and `FILE`.

## Workflow
- `git pull` before a session; commit and push your `results/*_{A|B}.csv` after.
- Each rater only writes their own results files, so there are no merge conflicts.
- `python spindle_validation_project/progress.py A` (or `B`) shows what's left and the next file.
- Once both raters finish a file, do a joint `MODE="reconcile"` session.
- Adding files: `pick_files.py`, then rerun `select_periodic_windows.py` and `select_transition_epochs.py`, commit the manifest and targets.

Run everything from the repo root.
