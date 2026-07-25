import test from 'node:test'
import assert from 'node:assert/strict'

const storage = new Map()
globalThis.localStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) }
const { request, money, statusLabel } = await import('../src/lib/rideApi.js')

test('formats integer cents without floating-point business calculations', () => {
  assert.equal(money(650), '$6.50')
  assert.equal(statusLabel('waiting_for_agent'), 'In the review queue')
})

test('a retry after a network failure keeps its operation ID', async () => {
  const seen = []
  globalThis.fetch = async (url, options) => {
    seen.push(options.headers['X-Request-ID'])
    if (seen.length === 1) throw new TypeError('Network interrupted')
    return { ok: true, json: async () => ({ id: 'case-a' }) }
  }
  const options = { body: { action: 'confirm' } }
  await assert.rejects(request('/api/cases/a/actions', options))
  await request('/api/cases/a/actions', options)
  assert.equal(seen[0], seen[1])
  await request('/api/cases/a/actions', options)
  assert.notEqual(seen[1], seen[2])
})

test('validation errors expose a readable message and release the operation ID', async () => {
  const seen = []
  globalThis.fetch = async (url, options) => {
    seen.push(options.headers['X-Request-ID'])
    return { ok: false, status: 409, json: async () => ({ detail: 'There is no eligible adjustment.' }) }
  }
  await assert.rejects(request('/api/cases/b/actions', { body: { action: 'confirm' } }), /no eligible adjustment/)
  await assert.rejects(request('/api/cases/b/actions', { body: { action: 'confirm' } }))
  assert.notEqual(seen[0], seen[1])
})

test('only specialist calls carry public local-demo role credentials', async () => {
  const seen = []
  globalThis.fetch = async (url, options) => {
    seen.push(options.headers)
    return { ok: true, json: async () => [] }
  }
  await request('/api/cases')
  await request('/api/agent/cases', { agent: true })
  assert.ok(seen[0]['X-Demo-Session'])
  assert.equal(seen[0]['X-Demo-Agent'], undefined)
  assert.equal(seen[1]['X-Demo-Agent'], 'local-demo-agent')
})

test('authenticated requests omit demo identity headers and never persist credentials', async () => {
  const { setCredential, clearCredential } = await import('../src/lib/auth.js')
  const { resetRequests } = await import('../src/lib/rideApi.js')
  const token = 'ephemeral-test-credential-' + 'z'.repeat(32)
  const seen = []
  globalThis.fetch = async (url, options) => {
    seen.push(options.headers)
    return { ok: true, json: async () => [] }
  }
  try {
    setCredential(token);resetRequests()
    await request('/api/cases')
    await request('/api/agent/cases', { agent: true })
    assert.equal(seen[0].Authorization, `Bearer ${token}`)
    assert.equal(seen[1].Authorization, `Bearer ${token}`)
    assert.equal(seen[0]['X-Demo-Session'], undefined)
    assert.equal(seen[1]['X-Demo-Agent'], undefined)
    assert.ok(![...storage.values()].includes(token))
  } finally { clearCredential();resetRequests() }
  await request('/api/cases')
  assert.equal(seen[2].Authorization, undefined)
})

test('navigation follows explicit roles and preserves permitted deep links', async () => {
  const { roleHome, allowsRoute } = await import('../src/lib/auth.js')
  assert.equal(roleHome(['rider']), '#/trips')
  assert.equal(roleHome(['specialist']), '#/agent')
  assert.equal(roleHome(['operator']), '#/manage')
  assert.equal(allowsRoute(['rider'], 'case'), true)
  assert.equal(allowsRoute(['rider'], 'agent'), false)
  assert.equal(allowsRoute(['specialist'], 'manage'), false)
  assert.equal(allowsRoute(['operator'], 'agent'), false)
  assert.equal(allowsRoute(['specialist', 'operator'], 'manage'), true)
  assert.equal(allowsRoute(['operator'], 'account'), true)
})

test('blocked browser storage does not prevent requests or credential sign-in', async () => {
  const original = globalThis.localStorage
  let sent
  try {
    globalThis.localStorage = { getItem() { throw new Error('Storage blocked') }, setItem() { throw new Error('Storage blocked') } }
    const isolated = await import('../src/lib/rideApi.js?storage-denied')
    globalThis.fetch = async (url, options) => { sent = options.headers; return { ok: true, json: async () => [] } }
    await isolated.request('/api/cases')
    assert.ok(sent['X-Demo-Session'])
  } finally { globalThis.localStorage = original }
})

test('conditional retries retain the command ID while sending the displayed revision', async () => {
  const seen = []
  globalThis.fetch = async (url, options) => {
    seen.push(options.headers)
    if (seen.length === 1) throw new TypeError('Response lost after commit')
    return { ok: true, json: async () => ({ revision: 8 }) }
  }
  await assert.rejects(request('/api/cases/conditional/actions', { body: { action: 'confirm' }, revision: 7 }))
  await request('/api/cases/conditional/actions', { body: { action: 'confirm' }, revision: 8 })
  assert.equal(seen[0]['If-Match'], '"7"')
  assert.equal(seen[1]['If-Match'], '"8"')
  assert.equal(seen[0]['X-Request-ID'], seen[1]['X-Request-ID'])
})

test('a delayed poll or cached receipt cannot regress the visible case revision', async () => {
  const { latestCase } = await import('../src/lib/rideApi.js')
  const committed = { id: 'case-one', revision: 9, status: 'resolved' }
  assert.equal(latestCase(committed, { id: 'case-one', revision: 8, status: 'awaiting_confirmation' }), committed)
  assert.equal(latestCase(committed, { id: 'case-one', status: 'reviewing' }), committed)
  assert.equal(latestCase(committed, { id: 'case-one', revision: 10, status: 'waiting_for_agent' }).revision, 10)
  assert.equal(latestCase(committed, { id: 'case-two', revision: 1 }).id, 'case-two')
})

test('live summary pages deduplicate updates and preserve deterministic cursor order', async () => {
  const { mergeCaseSummaries } = await import('../src/lib/rideApi.js')
  const old = [{ id: 'KU-B', revision: 3, updated_at: '2026-10-22T02:00:00Z', status: 'in_review' }, { id: 'KU-A', revision: 1, updated_at: '2026-10-22T01:00:00Z' }]
  const incoming = [{ id: 'KU-B', revision: 2, updated_at: '2026-10-22T02:00:00Z', status: 'waiting_for_agent' }, { id: 'KU-C', revision: 1, updated_at: '2026-10-22T02:00:00Z' }]
  const merged = mergeCaseSummaries(old, incoming)
  assert.deepEqual(merged.map(row => row.id), ['KU-C', 'KU-B', 'KU-A'])
  assert.equal(merged[1].status, 'in_review')
  assert.equal(mergeCaseSummaries(merged, incoming).length, 3)
  assert.equal(old.length, 2)
})

test('an empty migrated demo workspace can recover only IDs already saved in this browser', async () => {
  const { clearCredential, setCredential } = await import('../src/lib/auth.js')
  clearCredential()
  const values = new Map([['kinderuber.rides.session','new-rider-id'],['previous-app.rides.session','saved-rider-id']])
  const previousStore = globalThis.localStorage
  globalThis.localStorage = { get length() { return values.size }, key: i => [...values.keys()][i], getItem: key => values.get(key), setItem: (key,value) => values.set(key,value) }
  try {
    const api = await import('../src/lib/rideApi.js?workspace-recovery')
    const seen = []
    globalThis.fetch = async (url,options) => {
      seen.push(options.headers)
      const saved = options.headers['X-Demo-Session'] === 'saved-rider-id'
      return { ok: true, status: 200, json: async () => ({ total: saved ? 1 : 0, items: saved ? [{id:'KU-SAVED'}] : [] }) }
    }
    assert.equal((await api.request('/api/cases/index')).items[0].id, 'KU-SAVED')
    assert.deepEqual(seen.map(h => h['X-Demo-Session']), ['new-rider-id','saved-rider-id'])
    assert.equal(values.get('kinderuber.rides.session'), 'saved-rider-id')
    assert.equal(values.get('kinderuber.saved.rides.session'), 'new-rider-id')
    setCredential('production-credential-example')
    globalThis.fetch = async (url,options) => { seen.push(options.headers);return {ok:false,status:404,json:async()=>({detail:'Case not found.'})} }
    const before = seen.length
    await assert.rejects(api.request('/api/cases/KU-SAVED'))
    assert.equal(seen.length, before + 1)
    assert.equal(seen.at(-1).Authorization, 'Bearer production-credential-example')
  } finally { clearCredential();globalThis.localStorage = previousStore }
})

test('a rejected current credential clears authorization and signals the signed-in UI', async () => {
  const auth = await import('../src/lib/auth.js')
  const previousWindow = globalThis.window
  const events = []
  globalThis.window = { dispatchEvent: event => events.push(event.type) }
  try {
    auth.setCredential('revoked-production-credential')
    globalThis.fetch = async () => ({ok:false,status:401,json:async()=>({detail:'A valid access token is required.'})})
    await assert.rejects(request('/api/cases/index'), /valid access token/)
    assert.equal(auth.credentialHeaders(), null)
    assert.deepEqual(events, [auth.sessionExpiredEvent])
  } finally { auth.clearCredential();globalThis.window = previousWindow }
})

test('a delayed rejection from an old credential cannot sign out a new account', async () => {
  const auth = await import('../src/lib/auth.js')
  auth.setCredential('first-production-credential')
  const old = auth.credentialHeaders()
  auth.setCredential('new-production-credential')
  assert.equal(auth.expireCredentialIfCurrent(old), false)
  assert.equal(auth.credentialHeaders().Authorization, 'Bearer new-production-credential')
  auth.clearCredential()
})
