"""Stage 2 - REASON: ReAct diagnosis grounded by retrieval over D.

    Thought_k     ~ pi_theta( . | H_{k-1} )
    Action_k      ~ pi_theta( . | H_{k-1}, Thought_k )
    Observation_k = ToolCall(Action_k)
    H_k           = H_{k-1} u {Thought_k, Action_k, Obs_k}
    until Action_k = FINISH or k = K_max
    (d_t, A_cand) = Parse(H_k)

Two LLM backends ship here:

* ``HeuristicReActLLM``  - default. A deterministic, offline planner that
  produces a real ReAct trace by choosing which tool to call next from the
  evidence gathered so far. No network, no API key, fully reproducible - which
  is what you want for CI and for the worked example in the deck.
* ``AnthropicReActLLM``  - optional. Same interface, calls the Messages API and
  parses ``Thought:`` / ``Action:`` blocks. Used when ANTHROPIC_API_KEY is set.

Both are constrained to the same tool registry, so the safety story does not
change with the backend: the model can only observe, never act. All state
change happens later, in ACT, behind the envelope.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from .config import DEFAULT, ReasonConfig
from .memory_store import IncidentMemory, MemoryRecord
from .schemas import (
    ActionType,
    Diagnosis,
    Incident,
    Observation,
    ReActStep,
)

FINISH = "FINISH"


# ---------------------------------------------------------------------------
# Tool registry - the read-only surface exposed to the reasoning agent.
# ---------------------------------------------------------------------------
# ---- CLASS: Tool ----
@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., str]


# ---- CLASS: ToolRegistry ----
class ToolRegistry:
    """Read-only queries against eUICC state, OTA logs and the RSP audit trail."""

    # ---- METHOD: ToolRegistry.__init__ ----
    def __init__(self, rsp_client, memory: IncidentMemory, cfg: ReasonConfig) -> None:
        self.rsp = rsp_client
        self.memory = memory
        self.cfg = cfg
        self.call_log: List[Tuple[str, Dict[str, Any]]] = []
        self._tools: Dict[str, Tool] = {}
        self._register_defaults()

    # ---- METHOD: ToolRegistry._register_defaults ----
    def _register_defaults(self) -> None:
        self.register(Tool(
            "query_euicc_state",
            "Query ISD-P / ISD-R state and profile checksums for an eUICC.",
            lambda euicc_id, **_: self.rsp.query_euicc_state(euicc_id),
        ))
        self.register(Tool(
            "query_ota_sessions",
            "Return the last SM-DP+/SM-SR OTA session outcomes for an eUICC.",
            lambda euicc_id, **_: self.rsp.query_ota_sessions(euicc_id),
        ))
        self.register(Tool(
            "query_rsp_audit",
            "Return recent ES9+/ES10x audit-trail entries.",
            lambda euicc_id, **_: self.rsp.query_audit_trail(euicc_id),
        ))
        self.register(Tool(
            "query_device_kpi",
            "Return device-side radio KPIs (RSRP, drop rate, latency).",
            lambda euicc_id, **_: self.rsp.query_device_kpi(euicc_id),
        ))
        self.register(Tool(
            "retrieve_similar_incidents",
            "RAG over incident memory D; returns top-N precedents with outcomes.",
            self._retrieve_tool,
        ))

    # ---- METHOD: ToolRegistry.register ----
    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    # ---- METHOD: ToolRegistry.names ----
    def names(self) -> List[str]:
        return list(self._tools)

    # ---- METHOD: ToolRegistry.spec ----
    def spec(self) -> str:
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())

    # ---- METHOD: ToolRegistry._retrieve_tool ----
    def _retrieve_tool(self, observation: Observation, hint: str = "", **_: Any) -> str:
        hits = self.memory.retrieve(
            observation, top_n=self.cfg.top_n,
            min_similarity=self.cfg.min_similarity, hint=hint,
        )
        if not hits:
            return "no precedent above similarity floor (cold start)"
        return "; ".join(
            f"{r.record_id} sim={s:.2f} fault={r.fault_class} action={r.action} "
            f"success={r.success}"
            for r, s in hits
        )

    # ---- METHOD: ToolRegistry.call ----
    def call(self, name: str, **kwargs: Any) -> str:
        if name not in self._tools:
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(self.names())}"
        self.call_log.append((name, kwargs))
        try:
            return self._tools[name].fn(**kwargs)
        except Exception as exc:                       # tool faults must not kill the loop
            return f"ERROR: tool '{name}' failed: {exc}"


# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------
# ---- CLASS: ReActLLM ----
class ReActLLM(Protocol):
    """pi_theta over (Thought, Action) given the history H_{k-1}."""

    # ---- METHOD: ReActLLM.step ----
    def step(self, context: Dict[str, Any], history: Sequence[ReActStep]) -> Tuple[str, str, Dict[str, Any]]:
        """Return (thought, tool_name, tool_args). tool_name == FINISH ends the loop."""


# ---- CLASS: HeuristicReActLLM ----
class HeuristicReActLLM:
    """Deterministic offline stand-in for the reasoning policy.

    It is a genuine ReAct controller - the next tool call depends on what the
    previous observations returned - it simply uses explicit rules instead of a
    language model, so the trace is reproducible and runs with no network.
    """

    # ---- METHOD: HeuristicReActLLM.step ----
    def step(self, context: Dict[str, Any], history: Sequence[ReActStep]) -> Tuple[str, str, Dict[str, Any]]:
        obs: Observation = context["observation"]
        called = {s.tool for s in history}
        seen = " | ".join(s.observation for s in history)
        top = context.get("top_features", [])
        top_names = [n for n, _ in top]

        if "retrieve_similar_incidents" not in called:
            return (
                f"Anomaly on {obs.euicc_id}; dominant deviations {top_names}. "
                "Start from precedent rather than free recall.",
                "retrieve_similar_incidents",
                {"observation": obs, "hint": " ".join(top_names)},
            )

        if "query_euicc_state" not in called:
            return (
                "Precedent points at profile-layer faults. Confirm against live "
                "ISD-P / ISD-R state before proposing anything.",
                "query_euicc_state",
                {"euicc_id": obs.euicc_id},
            )

        if "checksum_mismatch=True" in seen and "query_ota_sessions" not in called:
            return (
                "Checksum mismatch reported. Check whether OTA sessions are also "
                "failing, which separates a corrupt profile from an SM-DP+ outage.",
                "query_ota_sessions",
                {"euicc_id": obs.euicc_id},
            )

        if "smdp_reachable=False" in seen and "query_rsp_audit" not in called:
            return (
                "SM-DP+ unreachable. Inspect the ES9+/ES10x audit trail to see "
                "whether the failure is server-side or bearer-side.",
                "query_rsp_audit",
                {"euicc_id": obs.euicc_id},
            )

        if top_names and top_names[0] in ("rsrp_dbm", "drop_rate") \
                and "query_device_kpi" not in called:
            return (
                f"The largest deviation is {top_names[0]}, a radio-side metric. "
                "Pull device KPIs to rule out a RAN cause before touching the profile.",
                "query_device_kpi",
                {"euicc_id": obs.euicc_id},
            )

        return ("Evidence is sufficient to name a fault class and a candidate set.", FINISH, {})


# ---- CLASS: AnthropicReActLLM ----
class AnthropicReActLLM:
    """Optional backend. Requires ANTHROPIC_API_KEY and the `anthropic` package."""

    PROMPT = (
        "You are the REASON stage of a self-healing eSIM agent. You may only "
        "OBSERVE - you never execute remediation. Reply with exactly:\n"
        "Thought: <one sentence>\nAction: <tool name or FINISH>\n"
        "Args: <JSON object>\n\nAvailable tools:\n{tools}\n"
    )

    # ---- METHOD: AnthropicReActLLM.__init__ ----
    def __init__(self, tools: ToolRegistry, model: str = "claude-sonnet-4-6",
                 max_tokens: int = 400) -> None:
        try:
            import anthropic                             # availability probe only
        except ImportError as exc:                       # pragma: no cover
            raise RuntimeError("pip install anthropic to use AnthropicReActLLM") from exc
        # MINOR CLEANUP (labelled): the module used to be imported a second
        # time here. Harmless (Python caches modules in sys.modules, so it
        # cost nothing at runtime) but redundant - reuse the name bound by
        # the `try` block above instead of importing it twice.
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._tools = tools
        self._model = model
        self._max_tokens = max_tokens

    # ---- METHOD: AnthropicReActLLM.step ----
    def step(self, context: Dict[str, Any], history: Sequence[ReActStep]) -> Tuple[str, str, Dict[str, Any]]:
        obs: Observation = context["observation"]
        transcript = "\n".join(
            f"Thought {s.k}: {s.thought}\nAction {s.k}: {s.tool}\nObservation {s.k}: {s.observation}"
            for s in history
        )
        user = (
            f"eUICC: {obs.euicc_id}  cell: {obs.cell_id}\n"
            f"Telemetry: {json.dumps(obs.as_dict())}\n"
            f"Top deviations: {context.get('top_features')}\n\n"
            f"{transcript}\n\nNext step:"
        )
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self.PROMPT.format(tools=self._tools.spec()),
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return self._parse(text, obs)

    # ---- METHOD: AnthropicReActLLM._parse ----
    @staticmethod
    def _parse(text: str, obs: Observation) -> Tuple[str, str, Dict[str, Any]]:
        thought = _grab(text, "Thought") or "(no thought returned)"
        action = (_grab(text, "Action") or FINISH).strip()
        raw_args = _grab(text, "Args") or "{}"
        try:
            args: Dict[str, Any] = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
        args.setdefault("euicc_id", obs.euicc_id)
        if action == "retrieve_similar_incidents":
            args["observation"] = obs
        return thought, action, args


# ---- FUNCTION: _grab ----
def _grab(text: str, label: str) -> Optional[str]:
    m = re.search(rf"{label}\s*:\s*(.+?)(?=\n[A-Z][a-z]+\s*:|\Z)", text, re.S)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Candidate-set derivation and the REASON stage itself
# ---------------------------------------------------------------------------
#: fault class -> ordered candidate actions A_cand handed to PLAN.
CANDIDATE_PLAYBOOK: Dict[str, List[ActionType]] = {
    "isdp_corruption": [
        ActionType.PROFILE_REPUSH,
        ActionType.ISDP_SWITCH,
        ActionType.ISDP_DELETE_REPROVISION,
    ],
    "smdp_session_outage": [
        ActionType.BEARER_FAILOVER,
        ActionType.PROFILE_REPUSH,
        ActionType.NO_OP,
    ],
    "radio_degradation": [
        ActionType.BEARER_FAILOVER,
        ActionType.NO_OP,
    ],
    "key_desync": [
        ActionType.KEY_ROTATION,
        ActionType.PROFILE_REPUSH,
        ActionType.ISDP_DELETE_REPROVISION,
    ],
    "unknown": [ActionType.NO_OP],
}


# ---- CLASS: Reasoner ----
class Reasoner:
    """Runs the bounded ReAct loop and parses H_k into (d_t, A_cand)."""

    # ---- METHOD: Reasoner.__init__ ----
    def __init__(self, tools: ToolRegistry, memory: IncidentMemory,
                 llm: Optional[ReActLLM] = None, cfg: Optional[ReasonConfig] = None) -> None:
        self.tools = tools
        self.memory = memory
        self.cfg = cfg or DEFAULT.reason
        self.llm = llm or HeuristicReActLLM()

    # ---- METHOD: Reasoner.diagnose ----
    def diagnose(self, incident: Incident, top_features: Optional[List] = None) -> Diagnosis:
        obs = incident.observation
        context = {"observation": obs, "incident": incident, "top_features": top_features or []}

        history: List[ReActStep] = []
        retrieved: List[str] = []
        calls = 0
        t0 = time.perf_counter()

        for k in range(1, self.cfg.k_max + 1):
            thought, tool, args = self.llm.step(context, history)
            calls += 1
            if tool == FINISH:
                history.append(ReActStep(k, thought, FINISH, {}, "loop terminated by policy"))
                break
            result = self.tools.call(tool, **args)
            if tool == "retrieve_similar_incidents":
                retrieved = re.findall(r"HIST-\d+|INC-[0-9A-F]+", result)
            history.append(ReActStep(k, thought, tool, _printable(args), result))
        else:
            history.append(ReActStep(
                self.cfg.k_max, "K_max reached; terminating with best available evidence.",
                FINISH, {}, "loop terminated by K_max bound",
            ))

        fault, confidence, text = self._parse(incident, history)
        candidates = CANDIDATE_PLAYBOOK.get(fault, CANDIDATE_PLAYBOOK["unknown"])

        diag = Diagnosis(
            incident_id=incident.incident_id,
            text=text,
            fault_class=fault,
            confidence=confidence,
            candidate_actions=list(candidates),
            trace=history,
            retrieved_ids=retrieved,
            llm_calls=calls,
        )
        diag.text += f"  [reason latency {(time.perf_counter()-t0)*1000:.0f} ms]"
        return diag

    # -- Parse(H_k) --------------------------------------------------------
    # ---- METHOD: Reasoner._parse ----
    def _parse(self, incident: Incident, history: Sequence[ReActStep]) -> Tuple[str, float, str]:
        """Fuse tool evidence with the Monitor belief b_t into (fault, conf, d_t)."""
        evidence = " ".join(s.observation for s in history)
        votes: Dict[str, float] = {c: 0.0 for c in CANDIDATE_PLAYBOOK if c != "unknown"}

        # 1. Belief prior from the Bayes filter.
        for cls, p in incident.belief.items():
            if cls in votes:
                votes[cls] += 1.2 * p

        # 2. Hard evidence from live tool calls outweighs the prior.
        if "checksum_mismatch=True" in evidence:
            votes["isdp_corruption"] += 1.5
        if "smdp_reachable=False" in evidence:
            votes["smdp_session_outage"] += 1.5
        if "rsrp_dbm=-1" in evidence or "radio_alarm=True" in evidence:
            votes["radio_degradation"] += 1.2
        if "key_epoch_mismatch=True" in evidence:
            votes["key_desync"] += 1.5

        # 3. Retrieved precedent votes with its similarity weight.
        for m in re.finditer(r"sim=([0-9.]+) fault=(\w+)", evidence):
            sim, cls = float(m.group(1)), m.group(2)
            if cls in votes:
                votes[cls] += 0.8 * sim

        # Unexplained mass. Confidence is the winning class's share of the total
        # evidence INCLUDING this constant, so a diagnosis reached from two tool
        # calls and no precedent cannot score as highly as one corroborated by
        # retrieval - even when both point at the same class.
        kappa = 1.0
        fault = max(votes, key=votes.get)
        total = sum(votes.values()) + kappa
        conf = votes[fault] / total if total > 0 else 0.0
        if conf < 0.35:
            fault = "unknown"

        n_tools = sum(1 for s in history if s.tool != FINISH)
        text = (
            f"{incident.incident_id}: fault_class={fault} (confidence {conf:.2f}) "
            f"after {n_tools} tool call(s); g_t={incident.anomaly_score:.1f} vs "
            f"threshold {incident.threshold:.1f}."
        )
        return fault, conf, text


# ---- FUNCTION: _printable ----
def _printable(args: Dict[str, Any]) -> Dict[str, Any]:
    """Strip non-serialisable objects out of the audit trace."""
    return {k: (v if isinstance(v, (str, int, float, bool)) else type(v).__name__)
            for k, v in args.items()}


# ---- FUNCTION: format_trace ----
def format_trace(diag: Diagnosis) -> str:
    lines = [f"ReAct trace H_k for {diag.incident_id}"]
    for s in diag.trace:
        lines.append(f"  Thought {s.k}: {s.thought}")
        lines.append(f"  Action  {s.k}: {s.tool}({_fmt_args(s.tool_args)})")
        lines.append(f"  Obs     {s.k}: {s.observation}")
    return "\n".join(lines)


# ---- FUNCTION: _fmt_args ----
def _fmt_args(args: Dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items())
