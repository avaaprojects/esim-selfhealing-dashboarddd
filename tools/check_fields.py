"""Print the field audit: what may be randomised, and within what limits.

    python tools/check_fields.py                 # the table
    python tools/check_fields.py mine.csv        # ...and check a CSV against it

This is the answer to "can we just randomise the inputs?". For each column it
states whether a generator may vary it, the physical limits outside which a
value is impossible rather than merely unusual, and - for the fields that must
not be randomised - why.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esim_selfhealing import fields                                     # noqa: E402
from esim_selfhealing.schemas import FEATURE_NAMES                      # noqa: E402


def main() -> int:
    print(fields.summary_table())
    print()
    print("Why the measured channels cannot be drawn independently:")
    print("  MONITOR scores a sample with the Mahalanobis distance, against an estimated")
    print("  covariance matrix - how unusual this COMBINATION is, given how the channels")
    print("  normally move together. Independent noise destroys that structure, and a real")
    print("  fault moves several channels at once in a fixed pattern (FAULT_SIGNATURES).")
    print("  Randomise them separately and the score still computes, but means nothing.")

    if len(sys.argv) < 2:
        return 0

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"\nerror: {path} does not exist", file=sys.stderr)
        return 2
    print(f"\nChecking {path.name}")
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))
    if not rows:
        print("  the file has no data rows")
        return 1
    missing = [f for f in FEATURE_NAMES if f not in rows[0]]
    if missing:
        print("  missing telemetry column(s): " + ", ".join(missing))
        return 1

    impossible, unusual = [], {}
    for i, row in enumerate(rows, start=2):
        for name in FEATURE_NAMES:
            try:
                value = float(row[name])
            except (TypeError, ValueError):
                impossible.append(f"  row {i}: {name} = {row[name]!r} is not a number")
                continue
            bad = fields.TELEMETRY[name].violates(value)
            if bad:
                impossible.append(f"  row {i}: {bad}")
            elif fields.TELEMETRY[name].implausible(value):
                unusual[name] = unusual.get(name, 0) + 1

    print(f"  {len(rows)} rows")
    if impossible:
        print(f"  IMPOSSIBLE values: {len(impossible)}")
        for line in impossible[:10]:
            print(line)
        if len(impossible) > 10:
            print(f"  ... and {len(impossible) - 10} more")
    else:
        print("  no physically impossible values")
    for name, count in sorted(unusual.items()):
        lo, hi = fields.TELEMETRY[name].typical
        print(f"  {name}: {count}/{len(rows)} rows outside the typical healthy range "
              f"{lo:g}..{hi:g} (expected if the file covers a fault)")
    return 1 if impossible else 0


if __name__ == "__main__":
    raise SystemExit(main())
