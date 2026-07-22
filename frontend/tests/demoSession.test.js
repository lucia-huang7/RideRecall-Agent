import test from 'node:test'
import assert from 'node:assert/strict'
import { readDemoWorkspace, saveDemoWorkspace, sessionKey } from '../src/lib/demoSession.js'

function storage(entries) {
  const values = new Map(entries)
  return { get length() { return values.size }, key: index => [...values.keys()][index], getItem: key => values.get(key), setItem: (key,value) => values.set(key,value) }
}

test('a saved same-origin demo workspace survives a storage-key migration', () => {
  const store = storage([['previous-app.rides.session','saved-rider-id']])
  assert.equal(readDemoWorkspace(store).session, 'saved-rider-id')
  assert.equal(store.getItem(sessionKey), 'saved-rider-id')
})

test('multiple saved workspaces do not silently override the current one', () => {
  const store = storage([[sessionKey,'current-rider'],['previous-app.rides.session','saved-rider'],['unrelated-key','ignore-me']])
  const value = readDemoWorkspace(store)
  assert.equal(value.session, 'current-rider')
  assert.deepEqual(value.alternatives, ['saved-rider'])
  saveDemoWorkspace('saved-rider','current-rider',store)
  assert.deepEqual(readDemoWorkspace(store).alternatives, ['current-rider'])
})

test('blocked or malformed local storage falls back safely', () => {
  const blocked = { getItem() { throw new Error('Blocked') }, setItem() { throw new Error('Blocked') } }
  assert.match(readDemoWorkspace(blocked).session, /^[0-9a-f-]{36}$/)
  const malformed = storage([[sessionKey,'invalid session!'],['previous-app.rides.session','invalid too!']])
  assert.match(readDemoWorkspace(malformed).session, /^[0-9a-f-]{36}$/)
})
