from __future__ import annotations

import base64
import json

from runner.tools.task_loader import load_task
from runtime.types import JsonDict, TaskDefinition, to_json_dict

RUNTIME_TEMPLATE = """
const __GENUI_TASK_ID__ = "__TASK_ID__";
const __GENUI_TURN__ = __TURN__;

(() => {
  const toolDefinitions = JSON.parse(atob("__TOOLS_B64__"));
  const resourceDefinitions = JSON.parse(atob("__RESOURCES_B64__"));
  const scenarioFixtures = JSON.parse(atob("__SCENARIO_B64__"));
  const initialScenarioStates = JSON.parse(atob("__INITIAL_STATE_B64__"));

  const scenarioStates = new Map();
  const toolLogs = [];
  const resourceLogs = [];
  const sideEffectLogs = [];
  const confirmationEvents = [];

  const getScenario = () => new URLSearchParams(window.location.search).get('scenario') ?? 'default';
  const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const stableJson = (value) => JSON.stringify(value);

  const toolsByName = Object.fromEntries(toolDefinitions.map((tool) => [tool.name, tool]));
  const resourcesByUri = Object.fromEntries(resourceDefinitions.map((resource) => [resource.uri, resource]));

  const recordConfirmationEvent = (type, message, result) => {
    confirmationEvents.push({
      index: confirmationEvents.length + 1,
      type,
      message: String(message ?? ''),
      result: clone(result),
      scenario: getScenario(),
      timestamp: new Date().toISOString(),
    });
  };

  const originalAlert = window.alert.bind(window);
  const originalConfirm = window.confirm.bind(window);
  const originalPrompt = window.prompt.bind(window);

  window.alert = (message) => {
    recordConfirmationEvent('alert', message, undefined);
    return originalAlert(message);
  };

  window.confirm = (message) => {
    const result = originalConfirm(message);
    recordConfirmationEvent('confirm', message, result);
    return result;
  };

  window.prompt = (message, defaultValue) => {
    const result = originalPrompt(message, defaultValue);
    recordConfirmationEvent('prompt', message, result);
    return result;
  };

  const getScenarioFixture = (scenario) => {
    if (!Object.prototype.hasOwnProperty.call(scenarioFixtures, scenario)) {
      throw new Error(`Missing scenario fixture for scenario ${scenario}`);
    }
    return scenarioFixtures[scenario];
  };

  const getScenarioState = (scenario) => {
    if (!scenarioStates.has(scenario)) {
      if (initialScenarioStates && typeof initialScenarioStates[scenario] === 'object' && initialScenarioStates[scenario] !== null) {
        scenarioStates.set(scenario, clone(initialScenarioStates[scenario]));
      } else {
        scenarioStates.set(scenario, clone(getScenarioFixture(scenario).initial_state));
      }
    }
    return scenarioStates.get(scenario);
  };

  const hasType = (value, expected) => {
    if (Array.isArray(expected)) return expected.some((item) => hasType(value, item));
    if (expected === 'null') return value === null;
    if (expected === 'integer') return Number.isInteger(value);
    if (expected === 'number') return typeof value === 'number' && Number.isFinite(value);
    if (expected === 'array') return Array.isArray(value);
    if (expected === 'object') return typeof value === 'object' && value !== null && !Array.isArray(value);
    return typeof value === expected;
  };

  const typeLabel = (expected) => Array.isArray(expected) ? expected.join(' or ') : expected;
  const schemaTypeIncludes = (schema, expected) => {
    if (!schema.type) return false;
    if (Array.isArray(schema.type)) return schema.type.includes(expected);
    return schema.type === expected;
  };

  const schemaValueMatches = (actual, expected) => stableJson(actual) === stableJson(expected);

  const validateShape = (schema, payload, label) => {
    if (!schema || typeof schema !== 'object') return;
    if (schema.type && !hasType(payload, schema.type)) {
      throw new Error(`${label} must be ${typeLabel(schema.type)}`);
    }
    if (Array.isArray(schema.enum) && !schema.enum.some((item) => schemaValueMatches(payload, item))) {
      throw new Error(`${label} must be one of ${schema.enum.map((item) => stableJson(item)).join(', ')}`);
    }
    if (Object.prototype.hasOwnProperty.call(schema, 'const') && !schemaValueMatches(payload, schema.const)) {
      throw new Error(`${label} must be ${stableJson(schema.const)}`);
    }
    if (Array.isArray(payload)) {
      if (schema.items && typeof schema.items === 'object') {
        payload.forEach((item, index) => validateShape(schema.items, item, `${label}[${index}]`));
      }
      return;
    }
    if (!payload || typeof payload !== 'object') return;
    const hasObjectKeywords = schema.properties || schema.required || schema.additionalProperties === false;
    if (!schemaTypeIncludes(schema, 'object') && !(hasObjectKeywords && !schema.type)) return;
    const properties = schema.properties ?? {};
    for (const requiredKey of schema.required ?? []) {
      if (!(requiredKey in payload)) {
        throw new Error(`${label} missing required field: ${requiredKey}`);
      }
    }
    for (const [key, spec] of Object.entries(properties)) {
      if (key in payload && payload[key] !== null) {
        validateShape(spec, payload[key], `${label} field "${key}"`);
      }
    }
    if (schema.additionalProperties === false) {
      for (const key of Object.keys(payload)) {
        if (!(key in properties)) {
          throw new Error(`${label} unknown field: ${key}`);
        }
      }
    }
  };

  const normalizeArgsForSchema = (schema, payload) => {
    if (!schema || typeof schema !== 'object') {
      return payload === undefined ? undefined : JSON.parse(JSON.stringify(payload));
    }
    if (payload === undefined) {
      return undefined;
    }
    const schemaTypes = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (typeof payload === 'number' && !Number.isFinite(payload) && (schemaTypes.includes('number') || schemaTypes.includes('integer'))) {
      return undefined;
    }
    if (Array.isArray(payload)) {
      return payload.map((item) => normalizeArgsForSchema(schema.items ?? {}, item));
    }
    if (schema.type !== 'object' || !payload || typeof payload !== 'object') {
      return payload;
    }
    const properties = schema.properties ?? {};
    const required = new Set(Array.isArray(schema.required) ? schema.required : []);
    return Object.fromEntries(
      Object.entries(payload)
        .filter(([key, item]) => {
          if (item === undefined) return false;
          if (item === '' && !required.has(key)) return false;
          return true;
        })
        .map(([key, item]) => [key, normalizeArgsForSchema(properties[key] ?? {}, item)])
        .filter(([, item]) => item !== undefined),
    );
  };

  window.__GENUI_TOOL_CALL__ = async (name, args) => {
    const tool = toolsByName[name];
    if (!tool) {
      throw new Error(`Unknown tool: ${name}`);
    }
    const scenario = getScenario();

    if (tool.backend === 'python') {
      const normalizedArgs = normalizeArgsForSchema(tool.input_schema, args);
      validateShape(tool.input_schema ?? {}, normalizedArgs ?? {}, `Tool ${name} args`);
      try {
        const response = await fetch('/__genui/tool-call', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name, args: normalizedArgs ?? {}, scenario}),
        });
        const payload = await response.json();
        if (!response.ok || payload.error) {
          throw new Error(payload.error || `Tool ${name} failed`);
        }
        validateShape(tool.output_schema, payload.result, `Tool ${name} result`);
        if (payload.evidence && payload.evidence.state_after && typeof payload.evidence.state_after === 'object') {
          scenarioStates.set(scenario, clone(payload.evidence.state_after));
        }
        toolLogs.push({ name, args: clone(normalizedArgs), scenario, result: clone(payload.result), evidence: clone(payload.evidence) });
        return clone(payload.result);
      } catch (error) {
        toolLogs.push({ name, args: clone(normalizedArgs), scenario, error: String(error) });
        throw error;
      }
    }

    const error = `Unsupported tool backend for ${name}: ${tool.backend}; only python is supported`;
    toolLogs.push({ name, args: clone(args), scenario, error });
    throw new Error(error);
  };

  window.__GENUI_RESOURCE_READ__ = async (uri) => {
    const resource = resourcesByUri[uri];
    if (!resource) {
      throw new Error(`Unknown resource: ${uri}`);
    }
    const scenario = getScenario();
    const scenarioFixture = getScenarioFixture(scenario);
    const fixture = (scenarioFixture.resources ?? {})[uri];
    if (typeof fixture === 'undefined') {
      const error = `Missing fixture for resource ${uri} in scenario ${scenario}`;
      resourceLogs.push({ uri, scenario, error });
      throw new Error(error);
    }
    if (fixture && typeof fixture === 'object' && 'error' in fixture) {
      const error = String(fixture.error);
      resourceLogs.push({ uri, scenario, error });
      throw new Error(error);
    }
    const result = clone(fixture);
    resourceLogs.push({ uri, scenario, result });
    return result;
  };

  Object.defineProperty(window, '__GENUI_GET_RUNTIME_LOGS__', {
    value: () => ({
      scenarios: Object.fromEntries(
        Object.entries(scenarioFixtures).map(([scenario, fixture]) => [
          scenario,
          {
            initial_state: clone(fixture?.initial_state ?? {}),
            state: clone(getScenarioState(scenario)),
          },
        ]),
      ),
      tool_logs: clone(toolLogs),
      resource_logs: clone(resourceLogs),
      side_effect_logs: clone(sideEffectLogs),
      confirmation_events: clone(confirmationEvents),
    }),
    enumerable: false,
    configurable: false,
    writable: false,
  });
})();
""".strip()


def build_runtime_script_for_task(
    task: TaskDefinition,
    *,
    initial_scenario_state: dict[str, JsonDict] | None = None,
) -> str:
    tools_b64 = base64.b64encode(
        json.dumps(
            [tool.runtime_contract(include_handler=False) for tool in task.tools],
            ensure_ascii=False,
        ).encode("utf-8")
    ).decode("ascii")
    resources_b64 = base64.b64encode(
        json.dumps(
            [to_json_dict(resource) for resource in task.resources], ensure_ascii=False
        ).encode("utf-8")
    ).decode("ascii")
    scenario_b64 = base64.b64encode(
        json.dumps(task.private_eval.get("scenario_fixtures", {}), ensure_ascii=False).encode(
            "utf-8"
        )
    ).decode("ascii")
    initial_state_b64 = base64.b64encode(
        json.dumps(initial_scenario_state or {}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    script = (
        RUNTIME_TEMPLATE.replace("__TOOLS_B64__", tools_b64)
        .replace("__RESOURCES_B64__", resources_b64)
        .replace("__SCENARIO_B64__", scenario_b64)
        .replace("__INITIAL_STATE_B64__", initial_state_b64)
        .replace("__TASK_ID__", task.task_id)
        .replace("__TURN__", str(task.turn_index))
    )

    return script


def build_runtime_script(
    task_id: str,
    *,
    turn: int = 1,
    initial_scenario_state: dict[str, JsonDict] | None = None,
) -> str:
    return build_runtime_script_for_task(
        load_task(task_id, turn=turn),
        initial_scenario_state=initial_scenario_state,
    )
