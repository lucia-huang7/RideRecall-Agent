const KEY = 'kinder-uber-support-mode'
export const normalizeAgentMode = value => value === 'multi' ? 'multi' : 'single'
export function loadAgentMode() {
  try { return normalizeAgentMode(localStorage.getItem(KEY)) } catch { return 'single' }
}
export function saveAgentMode(value) {
  try { localStorage.setItem(KEY, normalizeAgentMode(value)) } catch { /* The in-memory choice still works. */ }
}
export function analysisPayload(body, mode) {
  return !body.action || body.action === 'message' ? { ...body, agent_mode: normalizeAgentMode(mode) } : body
}
