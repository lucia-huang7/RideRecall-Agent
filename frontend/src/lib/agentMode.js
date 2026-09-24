const KEY = "llm-agent-system-support-mode";
export const normalizeAgentMode = (value) =>
  value === "multi" ? "multi" : "single";
export function loadAgentMode() {
  try {
    let value = localStorage.getItem(KEY);
    if (value === null) {
      const previous = [];
      for (let i = 0; i < Math.min(localStorage.length || 0, 64); i++) {
        const key = localStorage.key(i);
        if (key !== KEY && key?.endsWith("-support-mode")) {
          const saved = localStorage.getItem(key);
          if (["single", "multi"].includes(saved) && !previous.includes(saved))
            previous.push(saved);
        }
      }
      if (previous.length === 1) value = previous[0];
      if (value) localStorage.setItem(KEY, value);
    }
    return normalizeAgentMode(value);
  } catch {
    return "single";
  }
}
export function saveAgentMode(value) {
  try {
    localStorage.setItem(KEY, normalizeAgentMode(value));
  } catch {
    /* The in-memory choice still works. */
  }
}
export function analysisPayload(body, mode) {
  return !body.action || body.action === "message"
    ? { ...body, agent_mode: normalizeAgentMode(mode) }
    : body;
}
