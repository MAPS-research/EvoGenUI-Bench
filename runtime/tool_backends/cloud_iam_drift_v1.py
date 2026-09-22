from __future__ import annotations

import copy

from runtime.types import JsonDict, ToolDefinition

REPO = "acme/cloud-platform"
REQUEST_ID = "iam-review-4421"
PRINCIPAL = "svc-billing-export@acme-prod.iam.gserviceaccount.com"


def _object(required: list[str], properties: JsonDict) -> JsonDict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
COMMON_INPUT = {
    "repo": {"type": "string", "enum": [REPO]},
    "request_id": {"type": "string", "enum": [REQUEST_ID]},
}
COMPARE_EVIDENCE_INPUT = _object(
    ["iam_compare_access_drift"],
    {
        "iam_compare_access_drift": {
            "type": "string",
            "description": "Use the evidence_token returned by iam_compare_access_drift.",
        }
    },
)
APPLY_EVIDENCE_INPUT = _object(
    ["iam_compare_access_drift", "iam_create_remediation_plan"],
    {
        "iam_compare_access_drift": {
            "type": "string",
            "description": "Use the evidence_token returned by iam_compare_access_drift.",
        },
        "iam_create_remediation_plan": {
            "type": "string",
            "description": "Use the evidence_token returned inside iam_create_remediation_plan.plan.",
        },
    },
)

TOOL_SCHEMAS: dict[str, JsonDict] = {
    "iam_get_environment": {
        "description": "Read the cloud environment policy, owner boundaries, and least-privilege review rules.",
        "inputSchema": _object(["repo"], {"repo": COMMON_INPUT["repo"]}),
        "outputSchema": _object(
            ["repo", "cloud", "scope", "canonical_source", "policy"],
            {
                "repo": {"type": "string"},
                "cloud": {"type": "string"},
                "scope": {"type": "string"},
                "canonical_source": {"type": "string"},
                "policy": STRING_ARRAY,
            },
        ),
    },
    "iam_list_reviews": {
        "description": "List active IAM drift review requests for the environment.",
        "inputSchema": _object(["repo"], {"repo": COMMON_INPUT["repo"]}),
        "outputSchema": _object(
            ["repo", "reviews"],
            {
                "repo": {"type": "string"},
                "reviews": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            },
        ),
    },
    "terraform_get_policy_diff": {
        "description": "Read the Terraform policy diff. Terraform is the intended declared state, but it must be compared with live cloud bindings before applying changes.",
        "inputSchema": _object(["repo", "request_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "request_id", "diff"],
            {"repo": {"type": "string"}, "request_id": {"type": "string"}, "diff": {"type": "object", "additionalProperties": True}},
        ),
    },
    "cloud_get_current_bindings": {
        "description": "Read live cloud IAM bindings for the principal. This is the current state that remediation must change and later refresh.",
        "inputSchema": _object(["repo", "request_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "request_id", "principal", "bindings"],
            {"repo": {"type": "string"}, "request_id": {"type": "string"}, "principal": {"type": "string"}, "bindings": {"type": "array", "items": {"type": "object", "additionalProperties": True}}},
        ),
    },
    "iam_get_principal_activity": {
        "description": "Read recent activity for the principal to distinguish required data access from unused admin permissions.",
        "inputSchema": _object(["repo", "request_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "request_id", "activity"],
            {"repo": {"type": "string"}, "request_id": {"type": "string"}, "activity": {"type": "array", "items": {"type": "object", "additionalProperties": True}}},
        ),
    },
    "iam_get_breakglass_ticket": {
        "description": "Read the breakglass ticket linked to the broad IAM binding. Expired breakglass cannot justify keeping owner permissions.",
        "inputSchema": _object(["repo", "request_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "request_id", "ticket_id", "status", "expires_at", "allowed_roles"],
            {"repo": {"type": "string"}, "request_id": {"type": "string"}, "ticket_id": {"type": "string"}, "status": {"type": "string"}, "expires_at": {"type": "string"}, "allowed_roles": STRING_ARRAY},
        ),
    },
    "iam_compare_access_drift": {
        "description": "Compare Terraform declared state, live cloud bindings, activity, and breakglass status. Returns drift findings and an evidence_token for remediation planning.",
        "inputSchema": _object(["repo", "request_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "request_id", "principal", "findings", "recommended_changes", "evidence_token"],
            {"repo": {"type": "string"}, "request_id": {"type": "string"}, "principal": {"type": "string"}, "findings": STRING_ARRAY, "recommended_changes": {"type": "array", "items": {"type": "object", "additionalProperties": True}}, "evidence_token": {"type": "string"}},
        ),
    },
    "iam_get_ai_risk_summary": {
        "description": "Read a non-authoritative AI risk summary. It may be stale and cannot replace live binding, activity, or breakglass comparison.",
        "inputSchema": _object(["repo", "request_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "request_id", "authoritative", "summary"],
            {"repo": {"type": "string"}, "request_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "summary": {"type": "string"}},
        ),
    },
    "iam_create_remediation_plan": {
        "description": "Create a non-mutating least-privilege remediation plan. Pass evidence_tokens.iam_compare_access_drift using the latest evidence_token returned by iam_compare_access_drift.",
        "inputSchema": _object(
            ["repo", "request_id", "change_set", "evidence_tokens"],
            {
                **COMMON_INPUT,
                "change_set": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "evidence_tokens": COMPARE_EVIDENCE_INPUT,
            },
        ),
        "outputSchema": _object(
            ["success", "error", "plan"],
            {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "plan": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}},
        ),
    },
    "iam_apply_remediation_plan": {
        "description": "Apply the scoped IAM remediation. Pass current comparison and plan evidence tokens, then refresh live bindings before claiming owner access was removed.",
        "inputSchema": _object(
            ["repo", "request_id", "plan_id", "evidence_tokens"],
            {**COMMON_INPUT, "plan_id": {"type": "string"}, "evidence_tokens": APPLY_EVIDENCE_INPUT},
        ),
        "outputSchema": _object(
            ["success", "error", "operation", "bindings"],
            {"success": {"type": "boolean"}, "error": {"type": ["string", "null"]}, "operation": {"type": "object", "additionalProperties": True}, "bindings": {"type": "array", "items": {"type": "object", "additionalProperties": True}}},
        ),
    },
    "iam_get_audit_log": {
        "description": "Read audit events for IAM comparison, planning, and remediation operations.",
        "inputSchema": _object(["repo"], {"repo": COMMON_INPUT["repo"]}),
        "outputSchema": _object(["repo", "events"], {"repo": {"type": "string"}, "events": STRING_ARRAY}),
    },
}

TERRAFORM_DIFF = {
    "remove": [{"scope": "project/prod-payments", "role": "roles/owner", "member": PRINCIPAL}],
    "add": [{"scope": "bigquery/dataset/billing_exports", "role": "roles/bigquery.dataViewer", "member": PRINCIPAL}],
    "unchanged": [{"scope": "storage/bucket/monthly-invoices", "role": "roles/storage.objectViewer", "member": PRINCIPAL}],
}
INITIAL_BINDINGS = [
    {"scope": "project/prod-payments", "role": "roles/owner", "member": PRINCIPAL, "source": "manual-breakglass"},
    {"scope": "storage/bucket/monthly-invoices", "role": "roles/storage.objectViewer", "member": PRINCIPAL, "source": "terraform"},
]
TARGET_BINDINGS = [
    {"scope": "bigquery/dataset/billing_exports", "role": "roles/bigquery.dataViewer", "member": PRINCIPAL, "source": "terraform-remediation"},
    {"scope": "storage/bucket/monthly-invoices", "role": "roles/storage.objectViewer", "member": PRINCIPAL, "source": "terraform"},
]
ACTIVITY = [
    {"time": "2026-05-18T06:12:00Z", "service": "bigquery", "action": "jobs.create", "resource": "billing_exports.daily"},
    {"time": "2026-05-18T06:13:00Z", "service": "bigquery", "action": "tables.getData", "resource": "billing_exports.daily"},
]


def _state(state: JsonDict) -> JsonDict:
    if "cloud_iam_drift_v1" not in state:
        state["cloud_iam_drift_v1"] = {
            "bindings": copy.deepcopy(INITIAL_BINDINGS),
            "evidence": {},
            "plans": {},
            "audit": ["environment_loaded: acme/cloud-platform prod-payments"],
            "counter": 0,
        }
    return state["cloud_iam_drift_v1"]


def _check_scope(args: JsonDict) -> None:
    if args.get("repo") != REPO:
        raise ValueError(f"repo_mismatch: expected {REPO}")
    if args.get("request_id") is not None and args.get("request_id") != REQUEST_ID:
        raise ValueError(f"request_id_mismatch: expected {REQUEST_ID}")


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"iam-ev::{action}::{REQUEST_ID}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _compare(current: JsonDict) -> JsonDict:
    token = _token(current, "iam_compare_access_drift")
    owner_live = any(binding["role"] == "roles/owner" for binding in current["bindings"])
    findings = [
        "Live cloud state still grants roles/owner on project/prod-payments.",
        "Terraform intends to remove project owner and add BigQuery dataset viewer.",
        "Recent activity only shows BigQuery export reads, not admin operations.",
        "Breakglass ticket BG-781 expired before the current review window.",
    ]
    if not owner_live:
        findings = ["Live cloud state no longer grants roles/owner after remediation refresh."]
    changes = [
        {"action": "remove", "scope": "project/prod-payments", "role": "roles/owner", "member": PRINCIPAL},
        {"action": "add", "scope": "bigquery/dataset/billing_exports", "role": "roles/bigquery.dataViewer", "member": PRINCIPAL},
    ]
    current["audit"].append(f"compared_access_drift: {REQUEST_ID} token={token}")
    return {
        "repo": REPO,
        "request_id": REQUEST_ID,
        "principal": PRINCIPAL,
        "findings": findings,
        "recommended_changes": changes,
        "evidence_token": token,
    }


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    current = _state(state)
    _check_scope(args)
    name = tool.name
    if name == "iam_get_environment":
        return {
            "repo": REPO,
            "cloud": "gcp",
            "scope": "project/prod-payments",
            "canonical_source": "terraform + live cloud readback",
            "policy": [
                "Project owner is not allowed for service accounts outside active breakglass.",
                "Terraform diff must be compared with live bindings before remediation.",
                "Post-change UI must refresh live cloud bindings before claiming completion.",
            ],
        }
    if name == "iam_list_reviews":
        return {"repo": REPO, "reviews": [{"request_id": REQUEST_ID, "principal": PRINCIPAL, "status": "drift_open", "risk": "critical_owner_binding"}]}
    if name == "terraform_get_policy_diff":
        return {"repo": REPO, "request_id": REQUEST_ID, "diff": copy.deepcopy(TERRAFORM_DIFF)}
    if name == "cloud_get_current_bindings":
        return {"repo": REPO, "request_id": REQUEST_ID, "principal": PRINCIPAL, "bindings": copy.deepcopy(current["bindings"])}
    if name == "iam_get_principal_activity":
        return {"repo": REPO, "request_id": REQUEST_ID, "activity": copy.deepcopy(ACTIVITY)}
    if name == "iam_get_breakglass_ticket":
        return {"repo": REPO, "request_id": REQUEST_ID, "ticket_id": "BG-781", "status": "expired", "expires_at": "2026-05-16T23:59:00Z", "allowed_roles": ["roles/owner"]}
    if name == "iam_compare_access_drift":
        return _compare(current)
    if name == "iam_get_ai_risk_summary":
        return {"repo": REPO, "request_id": REQUEST_ID, "authoritative": False, "summary": "Older model summary: service account looks automated and probably safe to leave unchanged."}
    if name == "iam_create_remediation_plan":
        expected = current["evidence"].get("iam_compare_access_drift")
        if args.get("evidence_tokens", {}).get("iam_compare_access_drift") != expected:
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "plan": None}
        plan_token = _token(current, "iam_create_remediation_plan")
        plan_id = f"iam-plan-{current['counter']}"
        plan = {"plan_id": plan_id, "request_id": REQUEST_ID, "principal": PRINCIPAL, "change_set": list(args["change_set"]), "evidence_token": plan_token}
        current["plans"][plan_id] = copy.deepcopy(plan)
        current["audit"].append(f"remediation_plan_created: {plan_id}")
        return {"success": True, "error": None, "plan": plan}
    if name == "iam_apply_remediation_plan":
        tokens = args.get("evidence_tokens", {})
        if tokens.get("iam_compare_access_drift") != current["evidence"].get("iam_compare_access_drift"):
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "operation": {"status": "rejected"}, "bindings": copy.deepcopy(current["bindings"])}
        if tokens.get("iam_create_remediation_plan") != current["evidence"].get("iam_create_remediation_plan"):
            return {"success": False, "error": "missing_or_invalid_plan_evidence", "operation": {"status": "rejected"}, "bindings": copy.deepcopy(current["bindings"])}
        if args.get("plan_id") not in current["plans"]:
            return {"success": False, "error": "unknown_plan_id", "operation": {"status": "rejected"}, "bindings": copy.deepcopy(current["bindings"])}
        current["bindings"] = copy.deepcopy(TARGET_BINDINGS)
        op = {"operation_id": "iam-remediate-1", "status": "applied", "authoritative": True, "message": "Removed owner and added dataset-scoped viewer."}
        current["audit"].append("remediation_applied: iam-remediate-1")
        return {"success": True, "error": None, "operation": op, "bindings": copy.deepcopy(current["bindings"])}
    if name == "iam_get_audit_log":
        return {"repo": REPO, "events": copy.deepcopy(current["audit"])}
    raise ValueError(f"Unknown cloud IAM drift tool: {name}")
