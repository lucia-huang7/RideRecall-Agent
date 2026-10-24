// Access credentials live only in this page's memory, never browser storage.
let credential = ''
export function setCredential(value) { credential = value.trim() }
export function clearCredential() { credential = '' }
export function credentialHeaders() { return credential ? { Authorization: `Bearer ${credential}` } : null }
export const sessionExpiredEvent = 'llmagentsystem:session-expired'
export function expireCredentialIfCurrent(sentHeaders) {
  if (!credential || sentHeaders?.Authorization !== `Bearer ${credential}`) return false
  credential = ''
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(sessionExpiredEvent))
  return true
}
export async function runtimeConfig() {
  const root = import.meta.env?.VITE_RIDE_API_URL || '/ride-api'
  const response = await fetch(root + '/runtime-config', { signal: AbortSignal.timeout(10000), cache: 'no-store' })
  if (!response.ok) throw new Error('The workspace configuration could not be loaded.')
  return response.json()
}

export function roleHome(roles) { return roles.includes('rider') ? '#/trips' : roles.includes('specialist') ? '#/agent' : '#/manage' }
export function allowsRoute(roles, page) {
  if (page === 'account') return true
  if (page === 'agent') return roles.includes('specialist')
  if (page === 'manage') return roles.includes('operator')
  return roles.includes('rider')
}
