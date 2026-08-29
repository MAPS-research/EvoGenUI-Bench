from __future__ import annotations

from runtime.types import JsonDict

# ---------------------------------------------------------------------------
# Dimension definitions
# ---------------------------------------------------------------------------
TURN_LEVEL_DIMENSIONS = ("Presentation", "Execution", "Alignment")
DIMENSIONS = TURN_LEVEL_DIMENSIONS
EXECUTION_DIMENSIONS = ("Execution",)
PRESENTATION_DIMENSIONS = ("Presentation",)
ALIGNMENT_DIMENSIONS = ("Alignment",)
UNIFIED_NON_PRESENTATION_DIMENSIONS = ("Execution", "Alignment")

DIMENSION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("execution", EXECUTION_DIMENSIONS),
    ("presentation", PRESENTATION_DIMENSIONS),
    ("alignment", ALIGNMENT_DIMENSIONS),
)

# ---------------------------------------------------------------------------
# Allowed failure types
# ---------------------------------------------------------------------------
DIMENSION_ALLOWED_FAILURE_TYPES: dict[str, tuple[str, ...]] = {
    "Presentation": (
        "unreadable_ui",
        "broken_layout",
        "unfinished_default_ui",
        "visual_process_breakage",
        "weak_domain_visual_model",
        "weak_hierarchy",
        "poor_discoverability",
        "accessibility_issue",
    ),
    "Execution": (
        "missing_result",
        "incomplete_requirement",
        "stale_requirement",
        "entrypoint_only",
        "missing_runtime_evidence",
        "broken_control",
        "derived_state_mismatch",
        "runtime_failure",
        "wrong_tool_or_args",
        "missing_confirmation",
    ),
    "Alignment": (
        "misaligned_claim",
        "misaligned_capability",
        "runtime_misalignment",
        "stale_description",
        "partial_alignment",
        "assistant_text_underclaim",
        "text_ui_state_mismatch",
        "tool_claim_without_evidence",
    ),
}

DIMENSION_FAILURE_TYPE_ALIASES: dict[str, dict[str, str]] = {
    "Presentation": {
        "raw_math_tokens_visible": "unfinished_default_ui",
        "sparse_unfinished_layout": "unfinished_default_ui",
        "large_unused_right_side": "weak_hierarchy",
        "control_heavy_sparse_layout": "unfinished_default_ui",
        "near_blank_screenshot": "unreadable_ui",
        "undefined_css_classes": "unfinished_default_ui",
    },
    "Execution": {
        "partial_alignment": "incomplete_requirement",
    },
    "Alignment": {
        "minor_mismatch": "partial_alignment",
        "derived_state_mismatch": "runtime_misalignment",
    },
}

DIMENSION_UNKNOWN_FAILURE_TYPE_FALLBACKS: dict[str, str] = {
    "Presentation": "unfinished_default_ui",
    "Execution": "incomplete_requirement",
    "Alignment": "partial_alignment",
}

# ---------------------------------------------------------------------------
# Rubrics
# ---------------------------------------------------------------------------
DIMENSION_RUBRICS: dict[str, str] = {
    "Presentation": (
        "Before scoring, confirm:\n"
        "- [ ] I inspected the final screenshot directly (not just read DOM labels)\n"
        "- [ ] I noted any static_visual_audit findings\n"
        "- [ ] I verified that primary visible evidence is present in the screenshot\n"
        "- [ ] I scanned for overlapping or colliding elements\n"
        "- [ ] I checked for clipped, truncated, or overflowed text\n"
        "- [ ] I verified component alignment and consistent spacing\n"
        "- [ ] I confirmed no visible broken_layout or weak_hierarchy defects\n"
        "- [ ] I verified that any charts, graphs, or figures represent real, accurate data rather than placeholder or random visuals\n"
        "- [ ] I confirmed that displayed states, values, and visual outcomes are plausible and reflect reality\n"
        "- [ ] I applied any scoring_caps exactly as declared\n"
        "Judge the rendered UI quality: layout, hierarchy, readability, styling, discoverability, "
        "and whether screenshots show a coherent domain-appropriate interface rather than a sparse, "
        "unstyled, placeholder, or visibly broken page. A merely readable scaffold with cards, "
        "source chips, and correct text is not enough to pass Presentation if it still looks like "
        "a rough wireframe, sparse prototype, or unfinished layout. For screenshot-grounded "
        "presentation artifacts, checklist presence is not visual quality: final screenshot "
        "readability and primary-evidence visibility govern the score. "
        "Presentation should not give high credit to an apparatus that is only decorative or generic. "
        "A simplified schematic is acceptable only if it carries the requested domain semantics and "
        "the visual elements actually change in response to user interaction. "
        "Do not award high Presentation scores to charts, graphs, or visualizations that are purely "
        "decorative, contain placeholder or randomized data, or depict states that are physically or "
        "logically impossible. Visual elements must truthfully represent the underlying data or process "
        "they claim to show. "
        "A score of 5 requires a completely clean layout: no visible element overlaps, no collisions, "
        "no clipped or truncated text, no crowding, and no ambiguous visual relationships among components. "
        "Any overlap, collision, crowding, or spatial ambiguity precludes a top score, even if it does not "
        "block primary functionality."
    ),
    "Execution": (
        "Before scoring, confirm:\n"
        "- [ ] I checked runtime logs and tool logs for actual calls, not just source-code intent\n"
        "- [ ] I verified that visible functional results and runtime outcomes match the current user request\n"
        "- [ ] I distinguished working controls from broken or non-functional ones\n"
        "- [ ] I checked whether required UI-derived outputs (warnings, summaries, previews) update correctly in the visible UI or runtime logs\n"
        "- [ ] I compared output values from runtime logs, tool results, or backend state against the user request and assistant claims\n"
        "- [ ] I checked the generated source for logic that would produce incorrect or hardcoded outputs\n"
        "- [ ] I flagged any computed states, tool results, or backend values that contradict the user request or assistant claims\n"
        "- [ ] I distinguished actor execution gaps (actor did not attempt or got stuck without objective UI defect) from genuine UI execution failures (broken controls, incorrect outputs, missing runtime evidence)\n"
        "Judge whether the current turn's requested requirements are implemented as working UI "
        "behavior or evidence-backed state. This includes required surfaces, normal "
        "interaction wiring, state updates, tool/resource calls, derived results, committed "
        "readback, and relevant still-valid prior requirements the current turn depends on. "
        "Controls, buttons that trigger actions, raw tool responses, empty states, setup controls, "
        "or optimistic confirmation copy are not enough when the required functional UI result, "
        "committed UI or backend state, or derived UI surfaces are missing, incomplete, stale, or disconnected "
        "from the requested outcome. Distinguish actor limitations from UI defects. If the actor "
        "reports a failure (e.g., stuck, could not complete, control unresponsive), you must find "
        "corroborating objective evidence — such as DOM structure showing a broken or missing control, "
        "runtime logs showing an error, backend state contradicting the requirement, or a screenshot "
        "proving the interaction surface is inaccessible — before treating it as an execution defect. "
        "An actor report alone, without corroborating evidence from screenshots, DOM, runtime logs, "
        "tool logs, or backend state, is insufficient to penalize Execution. If the UI objectively "
        "supports the required flow but the actor did not reach or attempt it, that is an actor "
        "coverage gap, not a UI execution failure. "
        "Factual correctness is part of Execution: if runtime outputs, tool results, or backend state "
        "contain incorrect data, hardcoded values where dynamic results are required, or states that "
        "contradict the user request or assistant claims, that is an execution defect. Do not assume "
        "code logic is correct merely because the code compiles; verify that computed results, "
        "derived states, and runtime outputs are accurate and grounded in the actual tool calls, "
        "backend state, or user request."
    ),
    "Alignment": (
        "Before scoring, confirm:\n"
        "- [ ] I identified any claimed live state, saved data, computed results in the UI, or synchronized views\n"
        "- [ ] I found matching evidence in the visible UI, runtime logs, or backend state\n"
        "- [ ] I flagged any claim that is supported only by source-code intent or assistant text alone\n"
        "Judge whether assistant_text, generated source, visible UI, runtime logs, and actor "
        "observations describe the same concrete behavior. Claims about generated results in the UI, live "
        "state, synchronized views, saved changes, or available capabilities must be supported by "
        "the implementation and observed UI/runtime evidence. Source-code intent alone is not "
        "enough for claims about live state, committed effects, saved/submitted data, filtered "
        "views, computed results, or synchronized surfaces. If the public request or validation "
        "contract declares assistant_text as an evidence surface, generic setup copy or a terse "
        "build notification is not enough even when the UI itself executes."
    ),
}

# ---------------------------------------------------------------------------
# Presentation scoring caps
# ---------------------------------------------------------------------------
PRESENTATION_SCORING_CAPS: tuple[JsonDict, ...] = (
    {
        "cap": 3,
        "condition": (
            "The screenshot shows several significant overlaps, collisions, or dense clusters "
            "among business-critical evidence elements such as labels, icons, route lines, "
            "callout boxes/leaders, chart marks, or source/evidence markers."
        ),
        "required_failure_types": ["broken_layout", "weak_hierarchy"],
    },
    {
        "cap": 3,
        "condition": (
            "A current-turn first-viewport or primary visible evidence requirement is missing "
            "from the observed final screenshot and is supported only by source/DOM or below-scroll "
            "content."
        ),
        "required_failure_types": ["weak_hierarchy"],
    },
    {
        "cap": 4,
        "condition": (
            "The screenshot shows any visible element overlap, collision, clipping, crowding, "
            "or ambiguous visual relationship among UI components, even if minor, isolated, "
            "or not blocking the primary evidence."
        ),
        "required_failure_types": ["weak_hierarchy"],
    },
)

# ---------------------------------------------------------------------------
# Score anchors (compressed for clearer calibration)
# ---------------------------------------------------------------------------
DIMENSION_SCORE_ANCHORS: dict[str, JsonDict] = {
    "Presentation": {
        "1": ("Missing, unreadable, collapsed, or dominated by severe rendering/layout failures."),
        "2": (
            "Barely usable: major overlap, broken layout, unclear labels, inaccessible controls, "
            "or key content extremely hard to read or locate."
        ),
        "3": (
            "Rough or unfinished: crowded, weak hierarchy, inconsistent styling, or poor alignment. "
            "Interactive models may be present but disconnected or topologically wrong after normal use. "
            "Wireframe-like, sparse, or prototype-like layouts belong here."
        ),
        "4": (
            "Professional and coherent: clear workflow, readable text, controlled spacing, modest polish. "
            "Minor imperfections OK. Do not award if the primary experience still looks like unstyled "
            "HTML, a plain text list, a wireframe, or a sparse prototype. "
            "Visible element overlaps, collisions, crowding, or ambiguous spatial relationships cap the score "
            "at 4, even when minor or isolated and even when they do not block primary evidence."
        ),
        "5": (
            "Polished and intentional: well-structured, accessible, responsive. Zero visible element "
            "overlaps, collisions, clipping, crowding, or ambiguous visual relationships. Every component "
            "must have clear boundaries and spatial separation. Domain models behave correctly under normal "
            "manipulation. Screenshot must show a finished application UI, not merely correct labels in source or DOM."
        ),
    },
    "Execution": {
        "1": (
            "No meaningful implementation exists, the primary flow cannot be attempted, or catastrophic "
            "failure prevents any usable interaction."
        ),
        "2": (
            "Only incidental or superficial elements; primary interaction is broken, wrong tool/resource, "
            "produces errors, or never reaches a meaningful result."
        ),
        "3": (
            "Partial execution: some UI interaction or state change occurs, but required UI results, runtime confirmations, "
            "or derived outputs visible in the UI or logs are missing, incorrect, stale, or disconnected from the requested outcome. "
            "A required surface represented only by an entrypoint (button/form/empty state) belongs here. "
            "If the only evidence of failure is an actor report without corroborating objective evidence, "
            "do not score below 4 solely on that basis."
        ),
        "4": (
            "Primary flow succeeds with runtime evidence and a visible result, but has minor gaps such as "
            "weak feedback or incomplete secondary UI actions. Core surfaces are coherent; remaining gaps are "
            "secondary and do not contradict the current state reached through normal interaction. "
            "A primary flow that is objectively present and functional in the UI should not be downgraded "
            "to 3 merely because the actor trace is incomplete or the actor stopped early."
        ),
        "5": (
            "Fully implemented and reliable: all important constraints, requested outputs visible in the UI or logs, required tool/resource "
            "logs, visible state changes, user-visible confirmations, and business-critical surfaces are present, current, "
            "and connected with no meaningful runtime or interaction errors."
        ),
    },
    "Alignment": {
        "1": (
            "Fundamentally contradictory: assistant text, visible UI, and runtime evidence do not agree, "
            "or no reliable evidence ties claimed behavior to the UI."
        ),
        "2": (
            "Major claims are unsupported or contradicted; mostly not present. Includes saved, loaded, "
            "submitted, filtered, or computed claims with no matching visible state, tool log, resource log, "
            "or side-effect evidence."
        ),
        "3": (
            "Some claims match, but important claimed results, capabilities, state changes, or runtime behavior "
            "are missing, exaggerated, stale, or only available as unexecuted UI controls. "
            "Assistant text may superficially address a requested explanation, summary, or claim-evidence surface."
        ),
        "4": (
            "Core behavior agreed: assistant text, UI state, and runtime evidence align with only minor ambiguity, "
            "wording overreach, or secondary mismatch. Core capabilities are supported by visible UI or runtime evidence."
        ),
        "5": (
            "Fully consistent: assistant text, visible UI, runtime logs, and actor-observed behavior describe the "
            "same concrete result, state, and available capabilities."
        ),
    },
}

# ---------------------------------------------------------------------------
# Evidence policy
# ---------------------------------------------------------------------------
DIMENSION_EVIDENCE_POLICY = (
    "Use only supplied evidence. Route each requirement to its declared surface: assistant_text "
    "requirements are judged from assistant_text, visible UI requirements from visible UI, "
    "screenshots, actor observations, runtime logs, and source evidence. Use validation_contract "
    "or evaluation_reference when present, only for scenarios active at the current turn. Actor "
    "verification_checks with kind=validation_requirement_check are explicit per-requirement "
    "coverage markers; unresolved markers require inspecting the named evidence surface instead "
    "of assuming the requirement passed. If a validation requirement says a condition visible in the supplied evidence is "
    "a turn-level failure, score the affected "
    "dimension below passing. Do not invent requirements beyond the public task, public grounding, "
    "prior artifact, tool result, backend state rule, or local state rule."
)

# ---------------------------------------------------------------------------
# Suite evaluation profiles
# ---------------------------------------------------------------------------
SUITE_EVALUATION_PROFILES: dict[str, JsonDict] = {
    "tool_grounded_action_ui": {
        "summary": (
            "This suite evaluates a UI-mediated external workflow. The UI must expose the relevant "
            "tool-backed evidence, not invent private results or hide state-changing effects behind "
            "optimistic copy."
        ),
    },
    "interactive_tool_ui": {
        "summary": (
            "This suite evaluates a stateful mini-app, simulator, editor, or conceptual tool. "
            "The UI must expose a real object/process model that remains coherent under direct "
            "manipulation, mode changes, and derived-state updates."
        ),
    },
    "presentation_ui": {
        "summary": (
            "This suite evaluates an evidence-grounded presentation or reading surface. The UI "
            "must preserve traceability from claims to public material while supporting useful "
            "reader controls."
        ),
    },
}


# ---------------------------------------------------------------------------
# System prompt builders
# ---------------------------------------------------------------------------
def dimension_judge_system_prompt(
    *, evidence_policy: str, response_contract: str | None = None
) -> str:
    default_response_contract = (
        "Return exactly one JSON object with one top-level key per requested dimension. "
        "Do not include analysis, deliberation, markdown, code fences, or any text before or "
        "after the JSON object. "
        "Each dimension value must be an object: "
        "{score: number, summary: string, failure_types: string[]}. "
        "failure_types must only contain values from that dimension's allowed_failure_types list. "
        "Score each requested dimension independently within [1, 2, 3, 4, 5]. "
        "The benchmark derives pass/fail from score: score >= 4 passes, score < 4 fails. "
        "If a dimension has a hard blocker, assign score < 4 and include a matching failure_type."
    )
    return (
        "You are the EvoGenUI-Bench evaluator. Your job is to grade generated UI code based strictly "
        "on observable evidence. Follow these rules in order:\n\n"
        "1. Evidence-only rule: Use ONLY the evidence supplied in the user messages. "
        "Never infer hidden test hooks, private selectors, unseen behavior, or intended execution "
        "that is missing from logs.\n\n"
        "2. Evidence hierarchy (when sources conflict, trust the earlier item):\n"
        "   a) Event-centered evidence_pack entries that bind an action to before/after DOM/text, "
        "runtime deltas, screenshots, and backend state\n"
        "   b) Screenshots and DOM/SVG/CSS structural evidence\n"
        "   c) Runtime logs, tool logs, and backend state\n"
        "   d) Source code and generated files\n"
        "   e) Actor trace observations (treat as suspicious; actor may overwrite early findings, "
        "miss visible controls, or stop early. Actor reports of failure require corroboration from "
        "screenshots, DOM, or runtime logs before they can penalize Execution)\n"
        "   f) Assistant text claims (never sufficient alone for live-state claims)\n\n"
        "3. Source code is not runtime proof: Generated files are implementation evidence, not a "
        "substitute for runtime UI evidence. A tool call in source code does not prove the tool "
        "was actually invoked correctly at runtime.\n\n"
        "4. Tool-grounding evidence rule: For tool-grounded tasks, judge runtime side effects "
        "from tool_logs, tool result/evidence, scenario_states, runtime deltas, after-state DOM, "
        "and explicit readback evidence.\n\n"
        "5. Screenshot-first rule for Presentation: When images are provided, inspect them directly. "
        "Source, DOM labels, and assistant claims can prove that objects exist, but they CANNOT prove "
        "readability, clipping, spacing, or visual coherence. Apply scoring caps exactly as declared.\n\n"
        "6. Dimension isolation: Presentation-only failure types must never appear in Execution or Alignment. "
        "A beautiful but broken UI should score high on Presentation and low on Execution.\n\n"
        "7. Response format: The first user text block contains shared task evidence. "
        "The second user text block contains the requested dimensions, rubrics, score anchors, "
        "and allowed failure types. "
        f"Evidence policy: {evidence_policy} "
        f"{response_contract or default_response_contract}"
    )


def failure_taxonomy_system_prompt() -> str:
    return (
        "You are a EvoGenUI-Bench posthoc failure taxonomy judge. "
        "Your job is to classify a failed turn for paper-level diagnostic analysis; do not "
        "rescore dimensions and do not invent requirements beyond the supplied request, previous "
        "turns, traces, and snapshots.\n\n"
        "When screenshot_input.status is included, the attached image is the actor's final full-page "
        "screenshot for this turn. Inspect it directly for visual organization, readability, clipping, "
        "and domain-representation evidence. When the status is unavailable, unreadable, or "
        "disabled_by_config, do not invent screenshot evidence; use the supplied textual, source, "
        "build, DOM, actor, and runtime evidence and reflect the limitation in confidence.\n\n"
        "Return two distinct judgments:\n"
        "A. attribution: whether this failed turn should count as a model UI failure, an actor "
        "execution gap, benchmark infrastructure, mixed, or inconclusive.\n"
        "B. capability_failure: assign every executed non-passing turn exactly one primary "
        "interface-maintenance mechanism evidenced by the artifact and execution trace. "
        "Attribution is recorded separately and does not remove this six-way label.\n\n"
        "Attribution decision process:\n"
        "1. If the generated UI/code has an objective task-blocking defect, choose model_ui_failure. "
        "Objective defects include missing required surfaces, unusable layout, nonfunctional controls, "
        "incorrect or stale outputs, fake/hardcoded results where dynamic results are required, or "
        "tool/backend state that contradicts the UI.\n"
        "2. If the actor stopped early, timed out, looped, or missed a visible primary action while "
        "the UI/source clearly supports the required flow and there is no objective UI defect in "
        "the dimension summaries, trace, snapshot, source, or runtime evidence, choose "
        "actor_execution_gap.\n"
        "3. If browser/tool/evaluator infrastructure noise is the main cause, choose benchmark_infra.\n"
        "4. If both a real UI defect and actor/infra limitations materially contributed, choose mixed.\n"
        "5. If evidence is contradictory or insufficient, choose inconclusive.\n\n"
        "This judge is normally called only for failed turns. If dimension_judge.passed is false, "
        "do not choose passed; use model_ui_failure, actor_execution_gap, benchmark_infra, mixed, "
        "or inconclusive.\n\n"
        "Dimension summaries are evidence. If a failed dimension describes a concrete objective "
        "defect such as stale derived state, wrong initial state, broken control, missing result, "
        "runtime error, bad tool arguments, layout collision, or hidden/clipped required content, "
        "treat that as model_ui_failure or mixed even if the source code appears to intend the "
        "right behavior. Source-code intent alone does not erase an observed artifact defect.\n\n"
        "Capability labels (choose exactly one primary label for every input):\n"
        "- requirement_decomposition_failure: the artifact omits a requested capability, workflow, "
        "state, constraint, or feedback condition rather than merely implementing it poorly.\n"
        "- information_architecture_failure: the artifact includes much of the requested information "
        "but fails to organize dense content into a readable, navigable, decision-relevant interface; "
        "for example, key evidence is buried, comparisons are hard to scan, visual hierarchy is weak, "
        "or the layout prevents users from understanding relationships among otherwise present items.\n"
        "- domain_representation_failure: the UI lacks the task-specific conceptual model needed for "
        "the domain, such as a curve, topology, evidence model, physical process, causal chain, "
        "schedule structure, or other domain semantics.\n"
        "- affordance_binding_failure: controls or affordances are present, but operating them does "
        "not change the intended state or cannot complete the action.\n"
        "- derived_state_propagation_failure: a local state change occurs, but dependent charts, "
        "summaries, warnings, evidence panels, scores, explanations, or success conditions remain "
        "stale or inconsistent.\n"
        "- external_state_grounding_failure: the interface is inconsistent with tool calls, backend "
        "state, resource reads, committed writes, schema validation, or runtime readback.\n"
        "\n"
        "Tie-breaking rules:\n"
        "- Prefer the earliest missing modeling capability: if the requested capability is absent, use "
        "requirement_decomposition_failure rather than a downstream interaction label.\n"
        "- If controls do nothing at all, use affordance_binding_failure. If a control changes one "
        "state but dependent surfaces stay stale, use derived_state_propagation_failure.\n"
        "- For presentation-heavy failures, use information_architecture_failure when the problem is "
        "organization, hierarchy, scanability, or comparison structure; do not force these into "
        "domain_representation_failure unless the domain abstraction itself is absent or wrong.\n"
        "- Use domain_representation_failure only when the missing or wrong element is the domain "
        "abstraction itself, not just visual polish.\n"
        "- Use external_state_grounding_failure only when tool/backend/resource/runtime state evidence "
        "is involved.\n"
        "- Even when attribution is actor_execution_gap, benchmark_infra, or inconclusive, choose "
        "the single best-supported primary mechanism from the six labels; use confidence and "
        "the evidence lists to represent uncertainty.\n"
        "- Provide no more than two secondary_capability_failures, and only when they add a distinct "
        "diagnostic mechanism.\n"
        "- Always include every JSON key below. Use [] for empty evidence lists. Use a numeric "
        "confidence between 0 and 1.\n\n"
        "Required JSON shape:\n"
        "{\n"
        '  "attribution": "model_ui_failure|actor_execution_gap|benchmark_infra|mixed|inconclusive",\n'
        '  "count_as_model_failure": true,\n'
        '  "confidence": 0.85,\n'
        '  "capability_failure": "one of the six allowed capability labels",\n'
        '  "secondary_capability_failures": [],\n'
        '  "rationale": "one concise paragraph",\n'
        '  "code_evidence": [],\n'
        '  "actor_evidence": [],\n'
        '  "infra_evidence": [],\n'
        '  "capability_evidence": []\n'
        "}\n"
        "Return exactly one JSON object matching this shape."
    )
