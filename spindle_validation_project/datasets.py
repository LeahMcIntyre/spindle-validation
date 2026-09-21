"""Shared dataset registry for spindle_validation_project.

One place to add a dataset -- select_periodic_windows.py,
select_transition_epochs.py, and annotate_spindle_validation.py all
import DATASETS/RATERS from here instead of each keeping their own
copy. Grew from 3 datasets to 8 in one step (2026-09-19); duplicating
this dict three ways stopped being reasonable at that point.
"""

from pathlib import Path

DATASETS = {
    "PHP_pre": Path("/Users/leahmcintyre/Data/nri/PHP.eB/PHP_pre"),
    "PHP_6_week": Path("/Users/leahmcintyre/Data/nri/PHP.eB/PHP_6_week"),
    "SRA_pre": Path("/Users/leahmcintyre/Data/nri/SRA/SRA_pre"),
    "SRA_6_week": Path("/Users/leahmcintyre/Data/nri/SRA/SRA_6_week"),
    "RNA_KO": Path("/Users/leahmcintyre/Data/nri/RNA KO"),
    "Stxbp1_characterization": Path("/Users/leahmcintyre/Data/nri/Stxbp1_characterization"),
    "vgat": Path("/Users/leahmcintyre/Data/nri/Stxbp1-vgat_CKO"),
    "vglut": Path("/Users/leahmcintyre/Data/nri/Stxbp1-vglut2_CKO"),
}

# Two raters. Stable-hash-based assignment (see select_*.py) computes
# `hash(target_id) % len(RATERS)` -- changing the *count* reshuffles
# every existing assignment, so don't add/remove a rater once real work
# has been assigned. Reordering the tuple itself is harmless.
RATERS = ("A", "B")
