"""Top up file_manifest.csv to N randomly-drawn animals per dataset.

Eligible = has both a SPINDLE labels CSV (COMBO) and an EDF. Existing
manifest rows are kept and count toward N; only the shortfall is drawn.
PHP pre/6-week and SRA pre/6-week are drawn as matched animal pairs.
Seeded per dataset, so rerunning with the same N and manifest gives the
same picks.

Usage: python spindle_validation_project/pick_files.py [N] [--write]
(without --write, only prints what it would add)
"""

import random
import sys
import zlib
from pathlib import Path

import pandas as pd

from ebb.core.naming import parse_animal_id

import lib.nrem_reclass as nr
from datasets import DATASETS

COMBO = "LFC_RSC"
# Same animals recorded at both time points -- drawn once, added to both.
PAIRED = [("PHP_pre", "PHP_6_week"), ("SRA_pre", "SRA_6_week")]
SEED = 20260921
MANIFEST = Path(__file__).resolve().parent / "file_manifest.csv"


def eligible_animals(dataset: str) -> list[str]:
    root = DATASETS[dataset]
    ids = sorted({parse_animal_id(p) for p in (root / "spindle_outputs" / COMBO).glob("*_labels.csv")})
    return [a for a in ids if nr.edf_path_for(root / "standard", a).exists()]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 5
    manifest = pd.read_csv(MANIFEST)

    groups = list(PAIRED) + [(d,) for d in DATASETS if not any(d in p for p in PAIRED)]
    keep_mask = pd.Series(True, index=manifest.index)
    new_rows = []
    for group in groups:
        # A paired group only keeps animals already present in every member;
        # unpaired leftovers are dropped so the pair stays matched.
        have = set.intersection(*(set(manifest.loc[manifest["dataset"] == d, "animal_id"]) for d in group))
        for d in group:
            keep_mask &= ~((manifest["dataset"] == d) & ~manifest["animal_id"].isin(have))
        pool = sorted(set.intersection(*(set(eligible_animals(d)) for d in group)) - have)
        need = max(0, n - len(have))
        rng = random.Random(zlib.crc32(f"{SEED}:{group[0]}".encode()))
        picks = sorted(rng.sample(pool, min(need, len(pool))))
        print(f"{'+'.join(group)}: keeping {sorted(have)}, {len(pool) + len(have)} eligible, adding {picks}")
        new_rows += [
            {"dataset": d, "animal_id": a, "combo": COMBO} for d in group for a in picks
        ]

    if "--write" in sys.argv:
        out = pd.concat([manifest[keep_mask], pd.DataFrame(new_rows)], ignore_index=True)
        out = out.sort_values("dataset", kind="stable", key=lambda s: s.map(list(DATASETS).index))
        out.to_csv(MANIFEST, index=False)
        print(f"wrote {len(out)} rows to {MANIFEST.name}")


if __name__ == "__main__":
    main()
