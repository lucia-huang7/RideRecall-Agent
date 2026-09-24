import test from 'node:test'
import assert from 'node:assert/strict'
import { loadTheme, THEME_KEY } from '../src/lib/theme.js'
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


test('renaming migrates same-origin theme and explicit mode without old brand aliases', () => {
  const data = new Map([['previous-app-support-mode','multi'],['previous-app.rides.theme','rainbow']])
  globalThis.localStorage = { get length() { return data.size }, key: i => [...data.keys()][i], getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v) }
  assert.equal(loadAgentMode(), 'multi')
  assert.equal(loadTheme(), 'rainbow')
  assert.equal(data.get(THEME_KEY), 'rainbow')
  assert.equal(loadAgentMode(), 'multi')
})
