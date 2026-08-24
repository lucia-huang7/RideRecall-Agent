import test from 'node:test'
import assert from 'node:assert/strict'
import { loadAgentMode, saveAgentMode, analysisPayload } from '../src/lib/agentMode.js'

test('default and invalid persisted choices use single agent', () => {
  globalThis.localStorage = { getItem: () => null }
  assert.equal(loadAgentMode(), 'single')
  globalThis.localStorage.getItem = () => 'unknown'
  assert.equal(loadAgentMode(), 'single')
})
test('explicit opt-in persists and reaches analysis requests only', () => {
  let saved
  globalThis.localStorage = { getItem: () => saved, setItem: (_, value) => { saved=value } }
  saveAgentMode('multi')
  assert.equal(loadAgentMode(), 'multi')
  assert.equal(analysisPayload({ trip_id:'trip-river' }, loadAgentMode()).agent_mode, 'multi')
  assert.equal(analysisPayload({ action:'message', text:'Review my fare' }, 'single').agent_mode, 'single')
  assert.deepEqual(analysisPayload({ action:'confirm' }, 'multi'), { action:'confirm' })
  saveAgentMode('single')
  assert.equal(loadAgentMode(), 'single')
})
test('blocked storage still permits a no-key single-agent default', () => {
  globalThis.localStorage = { getItem: () => { throw Error('blocked') }, setItem: () => { throw Error('blocked') } }
  assert.equal(loadAgentMode(), 'single')
  assert.doesNotThrow(() => saveAgentMode('multi'))
})
