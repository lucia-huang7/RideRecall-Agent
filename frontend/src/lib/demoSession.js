// Demo workspace IDs are local role-play state, never production credentials.
export const sessionKey = 'kinderuber.rides.session'
const savedKey = 'kinderuber.saved.rides.session'
const valid = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value)
function browserStore() { try { return globalThis.localStorage } catch { return null } }

export function readDemoWorkspace(store = browserStore()) {
  let current, candidates = []
  try {
    current = store?.getItem(sessionKey)
    for (let index = 0; index < Math.min(store?.length || 0, 64); index++) {
      const key = store.key(index)
      if (key !== sessionKey && key?.endsWith('.rides.session')) {
        const value = store.getItem(key)
        if (valid(value) && !candidates.includes(value)) candidates.push(value)
      }
    }
  } catch { /* Storage restrictions do not block a fresh page-local demo. */ }
  const session = valid(current) ? current : candidates.length === 1 ? candidates[0] : crypto.randomUUID()
  try { store?.setItem(sessionKey, session) } catch { /* Keep the page-local workspace. */ }
  return { session, alternatives: candidates.filter(value => value !== session).slice(0, 4) }
}

export function saveDemoWorkspace(session, previous, store = browserStore()) {
  try {
    if (valid(previous) && previous !== session) store?.setItem(savedKey, previous)
    store?.setItem(sessionKey, session)
  } catch { /* An already recovered page can continue without writable storage. */ }
}
