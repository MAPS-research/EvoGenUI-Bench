from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

SERVICE = "checkout-api"
INCIDENT_ID = "inc-2026-05-17-checkout-5xx"


def _object(required: list[str], properties: JsonDict) -> JsonDict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
COMMON_INPUT = {
    "service": {"type": "string", "enum": [SERVICE]},
    "incident_id": {"type": "string", "enum": [INCIDENT_ID]},
}
COMPARE_EVIDENCE_INPUT = _object(
    ["incident_compare_hypotheses"],
    {
        "incident_compare_hypotheses": {
            "type": "string",
            "description": "Use the evidence_token returned by incident_compare_hypotheses.",
        }
    },
)
APPLY_EVIDENCE_INPUT = _object(
    ["incident_compare_hypotheses", "incident_create_mitigation_plan"],
    {
        "incident_compare_hypotheses": {
            "type": "string",
            "description": "Use the evidence_token returned by incident_compare_hypotheses.",
        },
        "incident_create_mitigation_plan": {
            "type": "string",
            "description": "Use the evidence_token returned inside incident_create_mitigation_plan.plan.",
        },
    },
)

TOOL_SCHEMAS: dict[str, JsonDict] = {
    "incident_get_case": {
        "description": "Read the incident header, current severity, customer impact, and required closeout policy.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["service", "incident_id", "severity", "status", "impact", "closeout_policy"],
            {"service": {"type": "string"}, "incident_id": {"type": "string"}, "severity": {"type": "string"}, "status": {"type": "string"}, "impact": {"type": "string"}, "closeout_policy": STRING_ARRAY},
        ),
    },
    "incident_get_pager_timeline": {
        "description": "Read PagerDuty timeline events. Pager events identify impact windows but do not by themselves prove root cause.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(["service", "incident_id", "events"], {"service": {"type": "string"}, "incident_id": {"type": "string"}, "events": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "incident_get_deploy_events": {
        "description": "Read deploy events near the incident window.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(["service", "incident_id", "deploys"], {"service": {"type": "string"}, "incident_id": {"type": "string"}, "deploys": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "incident_get_metric_window": {
        "description": "Read service, database, and cache metrics for the incident window.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(["service", "incident_id", "metrics"], {"service": {"type": "string"}, "incident_id": {"type": "string"}, "metrics": {"type": "object", "additionalProperties": True}}),
    },
    "incident_get_log_excerpts": {
        "description": "Read sampled logs. Logs must be tied to deploy and metric timing before being used as root-cause evidence.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(["service", "incident_id", "logs"], {"service": {"type": "string"}, "incident_id": {"type": "string"}, "logs": {"type": "array", "items": {"type": "object", "additionalProperties": True}}}),
    },
    "incident_compare_hypotheses": {
        "description": "Compare deploy, pager, metric, and log evidence. Returns the supported hypothesis and an evidence_token for mitigation planning.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["service", "incident_id", "supported_hypothesis", "rejected_hypotheses", "evidence", "evidence_token"],
            {"service": {"type": "string"}, "incident_id": {"type": "string"}, "supported_hypothesis": {"type": "string"}, "rejected_hypotheses": STRING_ARRAY, "evidence": STRING_ARRAY, "evidence_token": {"type": "string"}},
        ),
    },
    "incident_get_ai_rca_hint": {
        "description": "Read a non-authoritative generated RCA hint. It can be wrong and cannot replace cross-source incident evidence.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(["service", "incident_id", "authoritative", "hint"], {"service": {"type": "string"}, "incident_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "hint": {"type": "string"}}),
    },
    "incident_create_mitigation_plan": {
        "description": "Create a non-mutating mitigation plan. Pass evidence_tokens.incident_compare_hypotheses from the latest incident_compare_hypotheses result.",
        "inputSchema": _object(
            ["service", "incident_id", "actions", "evidence_tokens"],
            {**COMMON_INPUT, "actions": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "evidence_tokens": COMPARE_EVIDENCE_INPUT},
        ),
        "outputSchema": _object(["success", "error", "plan"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "plan": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}}),
    },
    "incident_apply_mitigation": {
        "description": "Apply the scoped mitigation plan. Pass current compare and plan evidence tokens, then refresh case and statuspage before claiming recovery.",
        "inputSchema": _object(
            ["service", "incident_id", "plan_id", "evidence_tokens"],
            {**COMMON_INPUT, "plan_id": {"type": "string"}, "evidence_tokens": APPLY_EVIDENCE_INPUT},
        ),
        "outputSchema": _object(["success", "error", "operation", "case"], {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": {"type": "object", "additionalProperties": True}, "case": {"type": "object", "additionalProperties": True}}),
    },
    "incident_get_statuspage": {
        "description": "Read customer-facing statuspage state. Recovery can only be claimed after mitigation and statuspage readback agree.",
        "inputSchema": _object(["service", "incident_id"], COMMON_INPUT),
        "outputSchema": _object(["service", "incident_id", "statuspage"], {"service": {"type": "string"}, "incident_id": {"type": "string"}, "statuspage": {"type": "object", "additionalProperties": True}}),
    },
    "incident_get_audit_log": {
        "description": "Read audit events for evidence comparison, mitigation planning, and applied mitigation.",
        "inputSchema": _object(["service"], {"service": COMMON_INPUT["service"]}),
        "outputSchema": _object(["service", "events"], {"service": {"type": "string"}, "events": STRING_ARRAY}),
    },
}

CASE = {
    "service": SERVICE,
    "incident_id": INCIDENT_ID,
    "severity": "SEV-1",
    "status": "mitigation_needed",
    "impact": "checkout 5xx peaked at 18.7% for EU traffic from 14:06 to 14:34 UTC",
    "closeout_policy": [
        "Tie root cause to at least two evidence sources.",
        "Do not accept generated RCA hints without metric/log/deploy support.",
        "Refresh incident and statuspage state after mitigation.",
    ],
}
PAGER_EVENTS = [
    {"time": "2026-05-17T14:06:00Z", "event": "checkout-api 5xx alert fired"},
    {"time": "2026-05-17T14:10:00Z", "event": "on-call acknowledged"},
    {"time": "2026-05-17T14:34:00Z", "event": "5xx below page threshold after retry gate disabled"},
]
DEPLOYS = [
    {"time": "2026-05-17T13:58:00Z", "release": "checkout-api@2026.05.17-a", "change": "raised inventory retry fanout and timeout budget"},
    {"time": "2026-05-17T14:29:00Z", "release": "runtime-flag", "change": "disabled inventory retry gate"},
]
METRICS = {
    "checkout_5xx_peak_percent": 18.7,
    "inventory_timeout_peak_ms": 9200,
    "database_cpu_peak_percent": 41,
    "cache_error_rate_percent": 0.3,
}
LOGS = [
    {"time": "2026-05-17T14:07:12Z", "level": "error", "message": "inventory retry fanout exhausted after 6 attempts"},
    {"time": "2026-05-17T14:08:03Z", "level": "warn", "message": "checkout request held while inventory timeout budget exceeded"},
    {"time": "2026-05-17T14:28:51Z", "level": "info", "message": "runtime flag inventory_retry_gate=false"},
]


def _state(state: JsonDict) -> JsonDict:
    if "incident_timeline_rca_v1" not in state:
        state["incident_timeline_rca_v1"] = {
            "case": copy.deepcopy(CASE),
            "statuspage": {"state": "degraded", "message": "Checkout errors elevated for EU customers."},
            "evidence": {},
            "plans": {},
            "audit": ["incident_loaded: inc-2026-05-17-checkout-5xx"],
            "counter": 0,
        }
    return state["incident_timeline_rca_v1"]


def _check_scope(args: JsonDict) -> None:
    if args.get("service") != SERVICE:
        raise ValueError(f"service_mismatch: expected {SERVICE}")
    if args.get("incident_id") is not None and args.get("incident_id") != INCIDENT_ID:
        raise ValueError(f"incident_id_mismatch: expected {INCIDENT_ID}")


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"inc-ev::{action}::{INCIDENT_ID}::{current['counter']}"
    current["evidence"][action] = token
    return token


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    current = _state(state)
    _check_scope(args)
    name = tool.name
    if name == "incident_get_case":
        return copy.deepcopy(current["case"])
    if name == "incident_get_pager_timeline":
        return {"service": SERVICE, "incident_id": INCIDENT_ID, "events": copy.deepcopy(PAGER_EVENTS)}
    if name == "incident_get_deploy_events":
        return {"service": SERVICE, "incident_id": INCIDENT_ID, "deploys": copy.deepcopy(DEPLOYS)}
    if name == "incident_get_metric_window":
        return {"service": SERVICE, "incident_id": INCIDENT_ID, "metrics": copy.deepcopy(METRICS)}
    if name == "incident_get_log_excerpts":
        return {"service": SERVICE, "incident_id": INCIDENT_ID, "logs": copy.deepcopy(LOGS)}
    if name == "incident_compare_hypotheses":
        token = _token(current, "incident_compare_hypotheses")
        current["audit"].append(f"hypotheses_compared: {INCIDENT_ID} token={token}")
        return {
            "service": SERVICE,
            "incident_id": INCIDENT_ID,
            "supported_hypothesis": "checkout-api release 2026.05.17-a increased inventory retry fanout, causing request timeouts and EU 5xx.",
            "rejected_hypotheses": ["database saturation: database CPU stayed below 41%", "cache outage: cache error rate stayed under 0.3%"],
            "evidence": ["deploy at 13:58 preceded 14:06 alert", "logs show inventory retry fanout exhaustion", "5xx dropped after inventory_retry_gate=false"],
            "evidence_token": token,
        }
    if name == "incident_get_ai_rca_hint":
        return {"service": SERVICE, "incident_id": INCIDENT_ID, "authoritative": False, "hint": "Generated hint: likely database overload due checkout traffic spike."}
    if name == "incident_create_mitigation_plan":
        if args.get("evidence_tokens", {}).get("incident_compare_hypotheses") != current["evidence"].get("incident_compare_hypotheses"):
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "plan": None}
        token = _token(current, "incident_create_mitigation_plan")
        plan_id = f"mitigation-plan-{current['counter']}"
        plan = {"plan_id": plan_id, "incident_id": INCIDENT_ID, "actions": list(args["actions"]), "evidence_token": token}
        current["plans"][plan_id] = copy.deepcopy(plan)
        current["audit"].append(f"mitigation_plan_created: {plan_id}")
        return {"success": True, "error": None, "plan": plan}
    if name == "incident_apply_mitigation":
        tokens = args.get("evidence_tokens", {})
        if tokens.get("incident_compare_hypotheses") != current["evidence"].get("incident_compare_hypotheses"):
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "operation": {"status": "rejected"}, "case": copy.deepcopy(current["case"])}
        if tokens.get("incident_create_mitigation_plan") != current["evidence"].get("incident_create_mitigation_plan"):
            return {"success": False, "error": "missing_or_invalid_plan_evidence", "operation": {"status": "rejected"}, "case": copy.deepcopy(current["case"])}
        if args.get("plan_id") not in current["plans"]:
            return {"success": False, "error": "unknown_plan_id", "operation": {"status": "rejected"}, "case": copy.deepcopy(current["case"])}
        current["case"]["status"] = "mitigated_monitoring"
        current["case"]["severity"] = "SEV-2"
        current["statuspage"] = {"state": "monitoring", "message": "Checkout error rate is back below threshold after retry gate mitigation."}
        op = {"operation_id": "mitigation-apply-1", "status": "applied", "authoritative": True, "message": "Disabled inventory retry gate and downgraded to monitoring."}
        current["audit"].append("mitigation_applied: mitigation-apply-1")
        return {"success": True, "error": None, "operation": op, "case": copy.deepcopy(current["case"])}
    if name == "incident_get_statuspage":
        return {"service": SERVICE, "incident_id": INCIDENT_ID, "statuspage": copy.deepcopy(current["statuspage"])}
    if name == "incident_get_audit_log":
        return {"service": SERVICE, "events": copy.deepcopy(current["audit"])}
    raise ValueError(f"Unknown incident timeline RCA tool: {name}")
