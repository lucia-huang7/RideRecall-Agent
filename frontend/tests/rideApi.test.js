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
