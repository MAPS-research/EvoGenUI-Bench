from __future__ import annotations

import copy
from datetime import datetime, timezone

from runtime.types import JsonDict, ToolDefinition

ISSUE_PAIR_ID = "mirror-pair-1847-921"
REPO_ID = "acme/payments-gateway"
SYNC_TOKEN_PREFIX = "sync-ev"


def _object(required: list[str], properties: JsonDict) -> JsonDict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}

ISSUE_SCHEMA = _object(
    [
        "source",
        "issue_id",
        "title",
        "state",
        "labels",
        "severity",
        "milestone",
        "body",
        "affected_files",
        "repro_steps",
        "linked_commits",
        "updated_at",
    ],
    {
        "source": {"type": "string", "enum": ["github", "gitlab"]},
        "issue_id": {"type": "string"},
        "title": {"type": "string"},
        "state": {"type": "string"},
        "labels": STRING_ARRAY,
        "severity": {"type": "string"},
        "milestone": {"type": "string"},
        "body": {"type": "string"},
        "affected_files": STRING_ARRAY,
        "repro_steps": STRING_ARRAY,
        "linked_commits": STRING_ARRAY,
        "updated_at": {"type": "string"},
    },
)

COMMENT_SCHEMA = _object(
    ["source", "comment_id", "author", "body", "created_at", "security_relevant"],
    {
        "source": {"type": "string", "enum": ["github", "gitlab"]},
        "comment_id": {"type": "string"},
        "author": {"type": "string"},
        "body": {"type": "string"},
        "created_at": {"type": "string"},
        "security_relevant": {"type": "boolean"},
    },
)

DIFF_SCHEMA = _object(
    ["file_path", "old_sha", "new_sha", "hunks", "risk_notes"],
    {
        "file_path": {"type": "string"},
        "old_sha": {"type": "string"},
        "new_sha": {"type": "string"},
        "hunks": {
            "type": "array",
            "items": _object(
                ["header", "removed", "added", "line_notes"],
                {
                    "header": {"type": "string"},
                    "removed": STRING_ARRAY,
                    "added": STRING_ARRAY,
                    "line_notes": STRING_ARRAY,
                },
            ),
        },
        "risk_notes": STRING_ARRAY,
    },
)

PLAN_SCHEMA = _object(
    ["plan_id", "issue_pair_id", "target", "field_updates", "blocked_fields", "evidence_token"],
    {
        "plan_id": {"type": "string"},
        "issue_pair_id": {"type": "string"},
        "target": {"type": "string", "enum": ["gitlab"]},
        "field_updates": {"type": "object", "additionalProperties": True},
        "blocked_fields": STRING_ARRAY,
        "evidence_token": {"type": "string"},
    },
)

OP_SCHEMA = _object(
    ["operation_id", "status", "target", "field_updates", "message"],
    {
        "operation_id": {"type": "string"},
        "status": {"type": "string", "enum": ["applied", "rejected"]},
        "target": {"type": "string", "enum": ["gitlab"]},
        "field_updates": {"type": "object", "additionalProperties": True},
        "message": {"type": "string"},
    },
)

COMMON_INPUT = {
    "repo": {"type": "string", "enum": [REPO_ID]},
    "issue_pair_id": {"type": "string", "enum": [ISSUE_PAIR_ID]},
}

COMPARE_EVIDENCE_INPUT = _object(
    ["mirror_compare_issue_pair"],
    {
        "mirror_compare_issue_pair": {
            "type": "string",
            "description": "Use the evidence_token returned by mirror_compare_issue_pair.",
        }
    },
)

APPLY_EVIDENCE_INPUT = _object(
    ["mirror_compare_issue_pair", "mirror_create_sync_plan"],
    {
        "mirror_compare_issue_pair": {
            "type": "string",
            "description": "Use the evidence_token returned by mirror_compare_issue_pair.",
        },
        "mirror_create_sync_plan": {
            "type": "string",
            "description": "Use the evidence_token returned inside mirror_create_sync_plan.plan.",
        },
    },
)

TOOL_SCHEMAS: dict[str, JsonDict] = {
    "mirror_get_repository": {
        "description": "Read the repository mirror configuration and canonical tracker policy.",
        "inputSchema": _object(["repo"], {"repo": COMMON_INPUT["repo"]}),
        "outputSchema": _object(
            ["repo", "canonical_source", "mirror_source", "release_gate", "sync_policy"],
            {
                "repo": {"type": "string"},
                "canonical_source": {"type": "string", "enum": ["github"]},
                "mirror_source": {"type": "string", "enum": ["gitlab"]},
                "release_gate": {"type": "string"},
                "sync_policy": STRING_ARRAY,
            },
        ),
    },
    "mirror_list_issue_pairs": {
        "description": "List GitHub/GitLab issue pairs for a repository mirror without reading full details.",
        "inputSchema": _object(["repo"], {"repo": COMMON_INPUT["repo"]}),
        "outputSchema": _object(
            ["repo", "pairs"],
            {
                "repo": {"type": "string"},
                "pairs": {
                    "type": "array",
                    "items": _object(
                        [
                            "issue_pair_id",
                            "github_issue_id",
                            "gitlab_issue_id",
                            "title_hint",
                            "last_sync_status",
                        ],
                        {
                            "issue_pair_id": {"type": "string"},
                            "github_issue_id": {"type": "string"},
                            "gitlab_issue_id": {"type": "string"},
                            "title_hint": {"type": "string"},
                            "last_sync_status": {"type": "string"},
                        },
                    ),
                },
            },
        ),
    },
    "github_get_issue": {
        "description": "Read the canonical GitHub issue. GitHub is the current source of truth for security labels, severity, affected files, and release blockers.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": ISSUE_SCHEMA,
    },
    "gitlab_get_issue": {
        "description": "Read the GitLab mirror issue. GitLab may lag GitHub and must be compared before any sync is applied.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": ISSUE_SCHEMA,
    },
    "github_get_comments": {
        "description": "Read canonical GitHub issue comments, including security and CI discussion.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(["repo", "issue_pair_id", "comments"], {"repo": {"type": "string"}, "issue_pair_id": {"type": "string"}, "comments": {"type": "array", "items": COMMENT_SCHEMA}}),
    },
    "gitlab_get_comments": {
        "description": "Read GitLab mirror comments. These comments can be incomplete relative to GitHub.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(["repo", "issue_pair_id", "comments"], {"repo": {"type": "string"}, "issue_pair_id": {"type": "string"}, "comments": {"type": "array", "items": COMMENT_SCHEMA}}),
    },
    "mirror_get_patch_diff": {
        "description": "Read the patch diff attached to the canonical issue. The vulnerable lines and missing regression test must be visible before claiming triage readiness.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(["repo", "issue_pair_id", "diffs"], {"repo": {"type": "string"}, "issue_pair_id": {"type": "string"}, "diffs": {"type": "array", "items": DIFF_SCHEMA}}),
    },
    "mirror_get_ci_trace": {
        "description": "Read current CI signals for the issue pair. A green mirror pipeline is not enough if the canonical branch still has a targeted failing regression test.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "issue_pair_id", "github_ci", "gitlab_ci", "warnings"],
            {
                "repo": {"type": "string"},
                "issue_pair_id": {"type": "string"},
                "github_ci": {"type": "object", "additionalProperties": True},
                "gitlab_ci": {"type": "object", "additionalProperties": True},
                "warnings": STRING_ARRAY,
            },
        ),
    },
    "mirror_get_security_advisory": {
        "description": "Read the security advisory draft linked to the canonical issue.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "issue_pair_id", "advisory_id", "severity", "affected_versions", "required_labels", "release_blocker"],
            {
                "repo": {"type": "string"},
                "issue_pair_id": {"type": "string"},
                "advisory_id": {"type": "string"},
                "severity": {"type": "string"},
                "affected_versions": STRING_ARRAY,
                "required_labels": STRING_ARRAY,
                "release_blocker": {"type": "boolean"},
            },
        ),
    },
    "mirror_compare_issue_pair": {
        "description": "Compare canonical GitHub and GitLab mirror fields. Returns the current mismatch list and an evidence_token for creating a scoped sync plan.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(
            ["repo", "issue_pair_id", "mismatches", "security_findings", "evidence_token"],
            {
                "repo": {"type": "string"},
                "issue_pair_id": {"type": "string"},
                "mismatches": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "security_findings": STRING_ARRAY,
                "evidence_token": {"type": "string"},
            },
        ),
    },
    "mirror_get_stale_summary": {
        "description": "Read an older generated summary. It is reference-only and cannot justify sync readiness or closure.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(["repo", "issue_pair_id", "authoritative", "summary"], {"repo": {"type": "string"}, "issue_pair_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "summary": {"type": "string"}}),
    },
    "mirror_get_ai_duplicate_hint": {
        "description": "Read a non-authoritative duplicate hint from a triage assistant. It may be wrong and cannot replace issue comparison.",
        "inputSchema": _object(["repo", "issue_pair_id"], COMMON_INPUT),
        "outputSchema": _object(["repo", "issue_pair_id", "authoritative", "hint"], {"repo": {"type": "string"}, "issue_pair_id": {"type": "string"}, "authoritative": {"type": "boolean", "const": False}, "hint": {"type": "string"}}),
    },
    "mirror_create_sync_plan": {
        "description": "Create a non-mutating GitLab sync plan. Pass evidence_tokens.mirror_compare_issue_pair using the latest evidence_token returned by mirror_compare_issue_pair. This does not update either tracker.",
        "inputSchema": _object(
            ["repo", "issue_pair_id", "include_fields", "evidence_tokens"],
            {
                **COMMON_INPUT,
                "include_fields": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "title",
                            "labels",
                            "severity",
                            "body",
                            "affected_files",
                            "repro_steps",
                            "linked_commits",
                            "milestone",
                        ],
                    },
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "evidence_tokens": COMPARE_EVIDENCE_INPUT,
            },
        ),
        "outputSchema": _object(
            ["success", "error", "plan"],
            {
                "success": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
                "plan": {"anyOf": [PLAN_SCHEMA, {"type": "null"}]},
            },
        ),
    },
    "mirror_apply_sync_plan": {
        "description": "Apply a scoped sync plan to GitLab. Pass evidence_tokens.mirror_compare_issue_pair and evidence_tokens.mirror_create_sync_plan from the current comparison and plan with matching issue scope.",
        "inputSchema": _object(
            ["repo", "issue_pair_id", "plan_id", "evidence_tokens"],
            {
                **COMMON_INPUT,
                "plan_id": {"type": "string"},
                "evidence_tokens": APPLY_EVIDENCE_INPUT,
            },
        ),
        "outputSchema": _object(
            ["success", "error", "operation", "gitlab_issue"],
            {
                "success": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
                "operation": OP_SCHEMA,
                "gitlab_issue": ISSUE_SCHEMA,
            },
        ),
    },
    "mirror_get_audit_log": {
        "description": "Read backend audit events for compare, planning, and sync operations.",
        "inputSchema": _object(["repo"], {"repo": COMMON_INPUT["repo"]}),
        "outputSchema": _object(["repo", "events"], {"repo": {"type": "string"}, "events": STRING_ARRAY}),
    },
}


GITHUB_ISSUE: JsonDict = {
    "source": "github",
    "issue_id": "GH-1847",
    "title": "OAuth redirect validation bypass on encoded callback",
    "state": "open",
    "labels": ["security", "oauth", "P1", "release-blocker"],
    "severity": "critical",
    "milestone": "1.14.2",
    "body": "Encoded network-path callbacks such as %2f%2fevil.test pass the pre-decode allow-list check in src/auth/redirect.ts. The fix must decode before allow-list validation and add a regression test.",
    "affected_files": ["src/auth/redirect.ts", "tests/auth/redirect-encoding.test.ts"],
    "repro_steps": ["POST /oauth/callback?next=%2f%2fevil.test", "observe Location header leaves trusted domain", "expected: reject before redirect"],
    "linked_commits": ["9f4c1de", "b7a88a2"],
    "updated_at": "2026-05-17T18:43:00Z",
}

GITLAB_ISSUE: JsonDict = {
    "source": "gitlab",
    "issue_id": "GL-921",
    "title": "Login redirect open redirect",
    "state": "open",
    "labels": ["bug", "triage"],
    "severity": "medium",
    "milestone": "1.15.0",
    "body": "Some login redirect values can leave the app. Needs product confirmation.",
    "affected_files": ["src/auth/redirect.ts"],
    "repro_steps": ["try unusual next values after login"],
    "linked_commits": [],
    "updated_at": "2026-05-15T09:12:00Z",
}

GITHUB_COMMENTS: list[JsonDict] = [
    {
        "source": "github",
        "comment_id": "GHC-14",
        "author": "security-review",
        "body": "Confirmed: decode-before-allow-list is required. This blocks 1.14.2 until redirect-encoding.test.ts covers double-encoded network-path input.",
        "created_at": "2026-05-17T19:04:00Z",
        "security_relevant": True,
    },
    {
        "source": "github",
        "comment_id": "GHC-18",
        "author": "release-manager",
        "body": "GitLab mirror is missing P1/security labels and the regression test reference. Do not close until GL-921 is synced.",
        "created_at": "2026-05-18T08:16:00Z",
        "security_relevant": True,
    },
]

GITLAB_COMMENTS: list[JsonDict] = [
    {
        "source": "gitlab",
        "comment_id": "GLC-3",
        "author": "triage-bot",
        "body": "Looks similar to a UX redirect bug; waiting for product severity.",
        "created_at": "2026-05-15T10:02:00Z",
        "security_relevant": False,
    }
]

DIFFS: list[JsonDict] = [
    {
        "file_path": "src/auth/redirect.ts",
        "old_sha": "4fd19c0",
        "new_sha": "9f4c1de",
        "hunks": [
            {
                "header": "@@ validateRedirect(next: string) @@",
                "removed": ["if (isAllowedHost(next)) return next;", "const decoded = decodeURIComponent(next);"],
                "added": ["const decoded = decodeURIComponent(next);", "if (isAllowedHost(decoded)) return decoded;", "throw new RedirectValidationError('blocked encoded redirect');"],
                "line_notes": ["The previous code checked the encoded string before decoding.", "The added guard is the security-critical behavior."],
            }
        ],
        "risk_notes": ["Network-path redirects encoded as %2f%2f bypassed the old allow-list.", "The GitLab mirror does not mention the regression test file."],
    },
    {
        "file_path": "tests/auth/redirect-encoding.test.ts",
        "old_sha": "missing",
        "new_sha": "b7a88a2",
        "hunks": [
            {
                "header": "@@ rejects encoded network-path callback @@",
                "removed": [],
                "added": ["expect(validateRedirect('%2f%2fevil.test')).toThrow('blocked encoded redirect');"],
                "line_notes": ["This is the regression test that GitLab currently omits."],
            }
        ],
        "risk_notes": ["Without this test the mirror can look green while missing the exploit case."],
    },
]


def _state(state: JsonDict) -> JsonDict:
    if "issue_mirror_diff_v1" not in state:
        state["issue_mirror_diff_v1"] = {
            "gitlab_issue": copy.deepcopy(GITLAB_ISSUE),
            "evidence": {},
            "plans": {},
            "operations": [],
            "audit": ["repo_loaded: acme/payments-gateway canonical=github mirror=gitlab"],
            "counter": 0,
        }
    return state["issue_mirror_diff_v1"]


def _check_scope(args: JsonDict) -> None:
    if args.get("repo") != REPO_ID:
        raise ValueError(f"repo_mismatch: expected {REPO_ID}")
    issue_pair_id = args.get("issue_pair_id")
    if issue_pair_id is not None and issue_pair_id != ISSUE_PAIR_ID:
        raise ValueError(f"issue_pair_id_mismatch: expected {ISSUE_PAIR_ID}")


def _token(current: JsonDict, action: str) -> str:
    current["counter"] += 1
    token = f"{SYNC_TOKEN_PREFIX}::{action}::{ISSUE_PAIR_ID}::{current['counter']}"
    current["evidence"][action] = token
    return token


def _mismatches(gitlab_issue: JsonDict) -> list[JsonDict]:
    fields = ["title", "labels", "severity", "milestone", "body", "affected_files", "repro_steps", "linked_commits"]
    mismatches: list[JsonDict] = []
    for field in fields:
        github_value = GITHUB_ISSUE[field]
        gitlab_value = gitlab_issue[field]
        if github_value != gitlab_value:
            mismatches.append(
                {
                    "field": field,
                    "github": copy.deepcopy(github_value),
                    "gitlab": copy.deepcopy(gitlab_value),
                    "risk": "security_release_gap" if field in {"labels", "severity", "repro_steps", "linked_commits"} else "mirror_drift",
                }
            )
    return mismatches


def _compare_payload(current: JsonDict) -> JsonDict:
    token = _token(current, "mirror_compare_issue_pair")
    current["audit"].append(f"compared_issue_pair: {ISSUE_PAIR_ID} token={token}")
    return {
        "repo": REPO_ID,
        "issue_pair_id": ISSUE_PAIR_ID,
        "mismatches": _mismatches(current["gitlab_issue"]),
        "security_findings": [
            "GitLab is missing the security/P1/release-blocker labels.",
            "GitLab lacks the encoded network-path repro and regression test reference.",
            "GitLab milestone is later than the canonical release blocker milestone.",
        ],
        "evidence_token": token,
    }


def _reject(current: JsonDict, reason: str) -> JsonDict:
    current["audit"].append(f"sync_rejected: {reason}")
    return {
        "success": False,
        "error": reason,
        "operation": {
            "operation_id": "",
            "status": "rejected",
            "target": "gitlab",
            "field_updates": {},
            "message": reason,
        },
        "gitlab_issue": copy.deepcopy(current["gitlab_issue"]),
    }


def invoke(*, tool: ToolDefinition, args: JsonDict, state: JsonDict) -> JsonDict:
    current = _state(state)
    _check_scope(args)
    name = tool.name

    if name == "mirror_get_repository":
        return {
            "repo": REPO_ID,
            "canonical_source": "github",
            "mirror_source": "gitlab",
            "release_gate": "1.14.2 cannot ship while a critical security mirror drift remains unresolved.",
            "sync_policy": [
                "Compare both trackers before planning a sync.",
                "Do not treat stale summaries or duplicate hints as tracker state.",
                "Apply only GitLab fields that are backed by the current comparison and plan.",
            ],
        }
    if name == "mirror_list_issue_pairs":
        return {
            "repo": REPO_ID,
            "pairs": [
                {
                    "issue_pair_id": ISSUE_PAIR_ID,
                    "github_issue_id": "GH-1847",
                    "gitlab_issue_id": "GL-921",
                    "title_hint": "OAuth redirect validation bypass",
                    "last_sync_status": "drift_detected",
                }
            ],
        }
    if name == "github_get_issue":
        return copy.deepcopy(GITHUB_ISSUE)
    if name == "gitlab_get_issue":
        return copy.deepcopy(current["gitlab_issue"])
    if name == "github_get_comments":
        return {"repo": REPO_ID, "issue_pair_id": ISSUE_PAIR_ID, "comments": copy.deepcopy(GITHUB_COMMENTS)}
    if name == "gitlab_get_comments":
        return {"repo": REPO_ID, "issue_pair_id": ISSUE_PAIR_ID, "comments": copy.deepcopy(GITLAB_COMMENTS)}
    if name == "mirror_get_patch_diff":
        return {"repo": REPO_ID, "issue_pair_id": ISSUE_PAIR_ID, "diffs": copy.deepcopy(DIFFS)}
    if name == "mirror_get_ci_trace":
        return {
            "repo": REPO_ID,
            "issue_pair_id": ISSUE_PAIR_ID,
            "github_ci": {"pipeline": "redirect-security-1847", "status": "failed", "failed_test": "redirect-encoding.test.ts::rejects encoded network-path callback"},
            "gitlab_ci": {"pipeline": "mirror-921", "status": "passed", "missing_test": "redirect-encoding.test.ts"},
            "warnings": ["GitLab green status is weaker because the exploit regression test is absent."],
        }
    if name == "mirror_get_security_advisory":
        return {
            "repo": REPO_ID,
            "issue_pair_id": ISSUE_PAIR_ID,
            "advisory_id": "GHSA-redirect-1847",
            "severity": "critical",
            "affected_versions": ["1.13.x", "1.14.0", "1.14.1"],
            "required_labels": ["security", "P1", "release-blocker"],
            "release_blocker": True,
        }
    if name == "mirror_compare_issue_pair":
        return _compare_payload(current)
    if name == "mirror_get_stale_summary":
        return {
            "repo": REPO_ID,
            "issue_pair_id": ISSUE_PAIR_ID,
            "authoritative": False,
            "summary": "Older summary: redirect issue looks medium priority and probably duplicate of UX redirect cleanup.",
        }
    if name == "mirror_get_ai_duplicate_hint":
        return {
            "repo": REPO_ID,
            "issue_pair_id": ISSUE_PAIR_ID,
            "authoritative": False,
            "hint": "Possible duplicate of GL-833. Confidence 0.42; does not include encoded callback diff.",
        }
    if name == "mirror_create_sync_plan":
        compare_token = current["evidence"].get("mirror_compare_issue_pair")
        evidence_tokens = args.get("evidence_tokens", {})
        if evidence_tokens.get("mirror_compare_issue_pair") != compare_token:
            return {"success": False, "error": "missing_or_invalid_compare_evidence", "plan": None}
        include_fields = list(args.get("include_fields", []))
        field_updates = {field: copy.deepcopy(GITHUB_ISSUE[field]) for field in include_fields}
        plan_id = f"sync-plan-{current['counter'] + 1}"
        plan_token = _token(current, "mirror_create_sync_plan")
        plan = {
            "plan_id": plan_id,
            "issue_pair_id": ISSUE_PAIR_ID,
            "target": "gitlab",
            "field_updates": field_updates,
            "blocked_fields": [field for field in ["state"] if field not in include_fields],
            "evidence_token": plan_token,
        }
        current["plans"][plan_id] = copy.deepcopy(plan)
        current["audit"].append(f"sync_plan_created: {plan_id} fields={','.join(include_fields)} token={plan_token}")
        return {"success": True, "error": None, "plan": plan}
    if name == "mirror_apply_sync_plan":
        evidence_tokens = args.get("evidence_tokens", {})
        compare_token = current["evidence"].get("mirror_compare_issue_pair")
        plan_token = current["evidence"].get("mirror_create_sync_plan")
        plan = current["plans"].get(args.get("plan_id"))
        if evidence_tokens.get("mirror_compare_issue_pair") != compare_token:
            return _reject(current, "missing_or_invalid_compare_evidence")
        if evidence_tokens.get("mirror_create_sync_plan") != plan_token:
            return _reject(current, "missing_or_invalid_plan_evidence")
        if not plan:
            return _reject(current, "unknown_plan_id")
        current["gitlab_issue"].update(copy.deepcopy(plan["field_updates"]))
        operation_id = f"mirror-sync-{len(current['operations']) + 1}"
        op = {
            "operation_id": operation_id,
            "status": "applied",
            "target": "gitlab",
            "field_updates": copy.deepcopy(plan["field_updates"]),
            "message": "GitLab mirror fields updated from canonical GitHub issue.",
        }
        current["operations"].append(copy.deepcopy(op))
        current["audit"].append(f"sync_applied: {operation_id} plan={plan['plan_id']}")
        current["gitlab_issue"]["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return {"success": True, "error": None, "operation": op, "gitlab_issue": copy.deepcopy(current["gitlab_issue"])}
    if name == "mirror_get_audit_log":
        return {"repo": REPO_ID, "events": copy.deepcopy(current["audit"])}
    raise ValueError(f"Unknown issue mirror diff tool: {name}")
