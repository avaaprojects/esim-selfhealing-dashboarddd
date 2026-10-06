"""Add (or replace) a stored dataset from your own telemetry CSV.

    python tools/import_dataset.py mine.csv --device 89330000000048213 \
        --title "March outage on the yard tracker"

    python tools/import_dataset.py mine.csv --device 8933... --replace isdp_corruption_48213

Everything under ./data is checksummed in data/manifest.json, so a file that is
edited or dropped in by hand is refused by the dashboard as "modified". That is
deliberate. This tool is the supported way to put your own telemetry there: it
validates the CSV exactly as the Input screen does, copies it into
data/telemetry/, and records it in the manifest with a fresh checksum.

It does NOT run the project's generator, so the other datasets are untouched.

The file must carry the five telemetry columns, at least 100 rows, and readings
for the one device you name. An optional `sim_fault_label` column tells the
bundled RSP model what fault to simulate, which is what lets a fix show as
"recovered"; see README_DASHBOARD.md, "Running the agent on your own CSV".

Nothing here is a claim that the data is real: the dataset is labelled
OPERATOR IMPORT wherever the dashboard shows provenance, never
SYNTHETIC / SIMULATED and never verified beyond its format.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import custom_input, datasets                    # noqa: E402

PROVENANCE = "OPERATOR IMPORT"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "imported"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path, help="the telemetry CSV to import")
    ap.add_argument("--device", required=True, help="eUICC id the readings are for (must be in data/devices/devices.csv)")
    ap.add_argument("--id", help="dataset id (default: from the file name)")
    ap.add_argument("--title", help="what the Input screen calls it")
    ap.add_argument("--description", default="", help="one line shown under the title")
    ap.add_argument("--replace", metavar="DATASET_ID", help="replace this dataset instead of adding one")
    ap.add_argument("--fleet-size", type=int, help="devices sharing this profile (default: the device's own)")
    args = ap.parse_args()

    if not args.csv.is_file():
        print(f"error: {args.csv} does not exist", file=sys.stderr)
        return 2

    reg = datasets.registry()
    device = next((d for d in reg["devices"] if d["euicc_id"] == args.device), None)
    if device is None:
        print(f"error: device {args.device} is not in data/devices/devices.csv. Known devices:", file=sys.stderr)
        for d in reg["devices"]:
            print(f"    {d['euicc_id']}  {d['client_id']}  {d['device_label']}", file=sys.stderr)
        return 2

    data = args.csv.read_bytes()
    try:
        summary = custom_input.parse(data, device=device)["summary"]
    except custom_input.CustomInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    manifest_path = ROOT / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entries = manifest["datasets"]

    old = None
    if args.replace:
        old = next((e for e in entries if e["id"] == args.replace), None)
        if old is None:
            print(f"error: no dataset {args.replace}. Known: " + ", ".join(e["id"] for e in entries), file=sys.stderr)
            return 2
    dataset_id = args.id or (old["id"] if old else _slug(args.csv.stem))
    if old is None and any(e["id"] == dataset_id for e in entries):
        print(f"error: dataset {dataset_id} already exists; pass --replace {dataset_id} to overwrite it.", file=sys.stderr)
        return 2

    rel = f"telemetry/{dataset_id}.csv"
    dest = ROOT / "data" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.csv, dest)

    entry = {
        "id": dataset_id, "kind": "telemetry", "file": rel,
        "title": args.title or (old["title"] if old else args.csv.stem),
        "description": args.description or (
            f"Imported from {args.csv.name}: {summary['rows']} rows, "
            f"{summary['time_from']} to {summary['time_to']}."
            + (" No time column, so readings are one second apart." if summary["assumed_time"] else "")
            + ("" if summary["has_fault_labels"] else
               " No sim_fault_label column, so the bundled RSP model is not told about a fault"
               " and a fix cannot show as recovered.")),
        "provenance": PROVENANCE,
        "generator": f"tools/import_dataset.py from {args.csv.name}",
        "seed": None,
        "client_id": device["client_id"], "group_id": device["group_id"],
        "euicc_id": device["euicc_id"], "cell_id": device["cell_id"],
        "fault_class": None,
        "fleet_size": args.fleet_size if args.fleet_size is not None else int(device["fleet_size"]),
        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "imported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "imported_from": args.csv.name,
    }
    if old is not None:
        entries[entries.index(old)] = entry
        if old["file"] != rel:
            (ROOT / "data" / old["file"]).unlink(missing_ok=True)
    else:
        entries.append(entry)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    verb = "replaced" if old else "added"
    print(f"{verb} dataset {dataset_id}")
    print(f"  file        data/{rel}")
    print(f"  device      {device['euicc_id']}  {device['device_label']}  ({device['client_id']})")
    print(f"  rows        {summary['rows']}  {summary['time_from']} to {summary['time_to']}")
    print(f"  provenance  {PROVENANCE}")
    if not summary["has_fault_labels"]:
        print("  note        no sim_fault_label column: the agent will detect and diagnose,")
        print("              but a fix cannot show as recovered (see README_DASHBOARD.md).")
    print("\nRestart the backend, then pick it on the Input screen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
