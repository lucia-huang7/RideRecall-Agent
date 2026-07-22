import { credentialHeaders } from './auth.js'
const root = import.meta.env?.VITE_RIDE_API_URL || '/ride-api'
const sessionKey = 'kinderuber.rides.session'
let session = crypto.randomUUID()
try {
  session = localStorage.getItem(sessionKey) || session
  localStorage.setItem(sessionKey, session)
} catch { /* A blocked browser store falls back to this page's demo session. */ }
const attempts = new Map()
export function resetRequests() { attempts.clear() }

export async function request(path, { body, agent = false, file } = {}) {
  const headers = credentialHeaders() || { 'X-Demo-Session': session, ...(agent ? { 'X-Demo-Agent': 'local-demo-agent' } : {}) }
  const key = JSON.stringify([path, body, file?.name, file?.size, file?.lastModified])
  if (body || file) {
    if (!attempts.has(key)) attempts.set(key, crypto.randomUUID())
    headers['X-Request-ID'] = attempts.get(key)
  }
  let payload
  if (file) { payload = new FormData(); payload.append('file', file) }
  else if (body) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  const response = await fetch(root + path, { method: payload ? 'POST' : 'GET', headers, body: payload, signal: AbortSignal.timeout(path.includes('evaluations') ? 90000 : 45000) })
  let result
  try { result = await response.json() } catch { throw new Error('The service returned an unreadable response. Please try again.') }
  if (!response.ok) {
    if (response.status < 500) attempts.delete(key)
    throw new Error(typeof result.detail === 'string' ? result.detail : 'Please check the details and try again.')
  }
  attempts.delete(key)
  return result
}

export async function downloadEvidence(caseId, evidence, agent = false) {
  const response = await fetch(`${root}/api/cases/${caseId}/attachments/${evidence.id}`, { headers: credentialHeaders() || { 'X-Demo-Session': session, ...(agent ? { 'X-Demo-Agent': 'local-demo-agent' } : {}) } })
  if (!response.ok) throw new Error('The attachment could not be downloaded.')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a'); link.href = url; link.download = evidence.filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const money = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value / 100)
export const statusLabel = value => ({ reviewing: 'Checking details', needs_review: 'Needs a closer look', awaiting_confirmation: 'Ready for your review', awaiting_rider: 'Waiting for you', waiting_for_agent: 'In the review queue', in_review: 'Specialist reviewing', resolved: 'Resolved' }[value] || value)
