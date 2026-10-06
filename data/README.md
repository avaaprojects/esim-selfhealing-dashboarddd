# data/ — predefined datasets

**Everything in this folder is SYNTHETIC / SIMULATED.** No file describes a real customer, device, operator or network. The dashboard reads these files through the API (`backend/services/datasets.py`); nothing here is embedded in frontend code.

`data/` holds *predefined* data only. Files an operator uploads through the Input screen go to `../uploads/`, never here.

| Folder | File | What it is |
|---|---|---|
| `clients/` | `clients.csv`, `groups.csv` | Fictional customers and their network groups |
| `devices/` | `devices.csv` | Devices and eSIM identities (eUICC id, EID, ICCID, profile). Includes the 4 devices of the real-time grid and the 5 the built-in scenarios use |
| `network/` | `network_contexts.csv` | PLMN (ITU test network 001-01), radio access, region, cells |
| `authentication/` | `auth_contexts.csv` | Authentication method and eUICC / SM-SR key epochs |
| `rsp/` | `rsp_environments.csv` | RSP environments (SM-DP+ / SM-SR). Endpoints use the reserved `.example` domain |
| `telemetry/` | `*.csv` | Per-device telemetry: the five features the MONITOR stage reads, one row per second |

There is no `incidents/` folder: incidents are what the agent *produces*, not something that enters it.

## Provenance

* `manifest.json` records, for every file, what it is, how it was generated (generator, seed), which client owns it and a SHA-256. The Input screen shows a checksum badge; a file edited after generation is flagged **Edited since generation** and refused as agent input.
* Every row of every CSV carries `provenance = SYNTHETIC / SIMULATED`, so a file is still labelled if it is copied away from the manifest.
* Telemetry is written by the project's own `esim_selfhealing.telemetry.TelemetryStream` with fixed seeds and a fixed start time (2026-01-05 02:00:00 UTC), so the files are byte-for-byte reproducible:

      python tools/generate_datasets.py

* `sim_fault_label` is the simulator's own label. The orchestrator uses it to keep the bundled RSP model consistent; the agent never reads it.

To add a dataset, add a spec to `tools/generate_datasets.py` and regenerate.
