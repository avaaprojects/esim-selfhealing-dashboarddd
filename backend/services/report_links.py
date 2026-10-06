"""Connects a report to the objects that already exist in the agent session:

    CLIENT REPORT -> CLIENT -> DEVICE -> INCIDENT -> TELEMETRY
                   -> SELF-HEALING AGENT -> REMEDIAL ACTION

`reports.py` stores a report and remembers *which* device / incident it points
at. This module is where those ids are checked against the live session on the
way in, and turned back into something the UI can draw on the way out.

Devices and incidents belong to the running `AgentSession`; they are lost on a
restart or an `agent/reset`. So on submission we keep a small snapshot of each
(`*_brief`), and on read we prefer the live object and fall back to the
snapshot, flagging which one you are looking at with `live`.

No FastAPI imports here: routes translate `LinkError` into a 422.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from . import estate


class LinkError(ValueError):
    """The device / incident a report names does not exist or does not match."""


# -- compact views ----------------------------------------------------------
def incident_brief(inc: Dict[str, Any]) -> Dict[str, Any]:
    """The part of an `AgentSession.incidents()` row a report needs: what the
    agent saw, what it decided, and what happened (the remedial-action end of
    the chain)."""
    obs = inc.get("observation") or {}
    return {
        "incident_id": inc["incident_id"],
        "euicc_id": obs.get("euicc_id"),
        "cell_id": obs.get("cell_id"),
        "opened_at": inc.get("opened_at"),
        "status": inc.get("status"),
        "fault_class": inc.get("fault_class"),
        "fault_label": inc.get("fault_label"),
        "confidence": inc.get("confidence"),
        "selected_action": inc.get("selected_action"),
        "selected_label": inc.get("selected_label"),
        "dispatched": inc.get("dispatched"),
        "success": inc.get("success"),
        "source": inc.get("source"),
        # Same registry context every other screen uses, kept in the snapshot so
        # the report still names the client and device after a session reset.
        "context": inc.get("context") or estate.context_for(obs.get("euicc_id")),
    }


def device_brief(dev: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "euicc_id": dev["euicc_id"],
        "cell_id": dev.get("cell_id"),
        "state": dev.get("state"),
        "active_fault_label": dev.get("active_fault_label"),
        "incident_count": dev.get("incident_count"),
        "open_incidents": dev.get("open_incidents"),
        "source": dev.get("source"),
        "context": dev.get("context") or estate.context_for(dev.get("euicc_id")),
    }


# -- submission -------------------------------------------------------------
def resolve_links(session: Any, device_id: Optional[str],
                  incident_id: Optional[str]) -> Dict[str, Any]:
    """Validate the optional device / incident a report names.

    * An incident implies its device: picking only an incident links both.
    * If both are given they must agree.
    * Unknown ids are rejected rather than silently stored.

    Returns {device_id, incident_id, device_snapshot, incident_snapshot}.
    """
    device_id = (device_id or "").strip() or None
    incident_id = (incident_id or "").strip() or None
    device_snap = incident_snap = None

    if incident_id:
        match = next((i for i in session.incidents()
                      if i["incident_id"] == incident_id), None)
        if match is None:
            raise LinkError(f"Incident {incident_id} is not in the running session. "
                            "Pick one from the list, or leave it blank.")
        incident_snap = incident_brief(match)
        owner_device = incident_snap["euicc_id"]
        if device_id and device_id != owner_device:
            raise LinkError(f"Incident {incident_id} was opened on device {owner_device}, "
                            f"not {device_id}. Change one of the two.")
        device_id = owner_device

    if device_id:
        match_dev = next((d for d in session.devices() if d["euicc_id"] == device_id), None)
        if match_dev is None:
            raise LinkError(f"Device {device_id} is not known to the dashboard. "
                            "Pick one from the list, or leave it blank.")
        device_snap = device_brief(match_dev)

    return {"device_id": device_id, "incident_id": incident_id,
            "device_snapshot": device_snap, "incident_snapshot": incident_snap}


# -- reading ----------------------------------------------------------------
def enrich(session: Any, reports: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach `device` and `incident` objects to each report.

    Each is `None` when the report has no such link, otherwise the live brief
    with `live: True`, or - if the session no longer has it - the snapshot
    taken at submission with `live: False`. Live lookups are built once for the
    whole batch, so listing N reports costs one pass over the session, not N.
    """
    reports = list(reports)
    inc_by_id = {i["incident_id"]: i for i in session.incidents()}
    dev_by_id = {d["euicc_id"]: d for d in session.devices()}

    out = []
    for r in reports:
        r = dict(r)
        snap_dev = r.pop("device_snapshot", None)
        snap_inc = r.pop("incident_snapshot", None)

        device = incident = None
        if r["device_id"]:
            live = dev_by_id.get(r["device_id"])
            device = ({**device_brief(live), "live": True} if live
                      else {**(snap_dev or {"euicc_id": r["device_id"]}), "live": False})
        if r["incident_id"]:
            live = inc_by_id.get(r["incident_id"])
            incident = ({**incident_brief(live), "live": True} if live
                        else {**(snap_inc or {"incident_id": r["incident_id"]}), "live": False})
        r["device"] = device
        r["incident"] = incident
        out.append(r)
    return out


def with_counts(items: List[Dict[str, Any]], key: str,
                counts: Dict[str, int], field: str = "report_count") -> List[Dict[str, Any]]:
    """Stamp a report count onto rows of incidents or devices, in place."""
    for item in items:
        item[field] = counts.get(item[key], 0)
    return items
