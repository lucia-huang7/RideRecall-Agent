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
