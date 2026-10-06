"""Which company's data a signed-in account may see.

The demo data has three fictional companies (`data/clients/clients.csv`). An
OWNER account sees every company. A CLIENT account belongs to exactly one
company (`client_id` on the account) and sees only that company's devices,
incidents, stored datasets and results.

This is enforced by the routes using the helpers below, not by hiding things
in the frontend. The rule fails CLOSED: a client account with no company is
scoped to a company that does not exist, so it sees nothing, rather than being
treated like an owner.

No FastAPI imports: importable and testable on its own.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: What a client with no `client_id` is scoped to: matches no real company.
NO_COMPANY = "__no_company__"


def company_of(entry: Dict[str, Any]) -> Optional[str]:
    """The company an account is limited to, or None for an owner (no limit)."""
    if not isinstance(entry, dict):
        raise TypeError("company_of needs the signed-in account entry")
    if entry.get("role") == "owner":
        return None
    return entry.get("client_id") or NO_COMPANY


def owns(company: Optional[str], context: Optional[Dict[str, Any]]) -> bool:
    """May this company see something whose registry context is `context`?
    Something with no context (an unregistered device) is never a client's."""
    if company is None:
        return True
    return bool(context) and context.get("client_id") == company


def scope_registry(reg: Dict[str, Any], company: Optional[str]) -> Dict[str, Any]:
    """The registry as one company sees it: its own client, groups, devices and
    only the networks / auth / RSP contexts those devices use."""
    if company is None:
        return reg
    devices = [d for d in reg.get("devices", []) if d.get("client_id") == company]
    net_ids = {d.get("network_id") for d in devices}
    rsp_ids = {d.get("rsp_env_id") for d in devices}
    networks = [n for n in reg.get("networks", []) if n.get("network_id") in net_ids]
    auth_ids = {n.get("auth_id") for n in networks}
    scoped = dict(reg)
    scoped["clients"] = [c for c in reg.get("clients", []) if c.get("client_id") == company]
    scoped["groups"] = [g for g in reg.get("groups", []) if g.get("client_id") == company]
    scoped["devices"] = devices
    scoped["networks"] = networks
    scoped["authentication"] = [a for a in reg.get("authentication", []) if a.get("auth_id") in auth_ids]
    scoped["rsp_environments"] = [r for r in reg.get("rsp_environments", []) if r.get("rsp_env_id") in rsp_ids]
    return scoped


def scope_datasets(dsets: Iterable[Dict[str, Any]], company: Optional[str]) -> List[Dict[str, Any]]:
    """Only the stored datasets that belong to the company's own devices."""
    items = list(dsets)
    return items if company is None else [d for d in items if d.get("client_id") == company]
