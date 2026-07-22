export const THEME_KEY = 'kinderuber.rides.theme'

export function loadTheme(storage = globalThis.localStorage) {
  try { return storage.getItem(THEME_KEY) === 'rainbow' ? 'rainbow' : 'mono' }
  catch { return 'mono' }
}

export function applyTheme(theme, root = globalThis.document?.documentElement) {
  const selected = theme === 'rainbow' ? 'rainbow' : 'mono'
  if (root) root.dataset.theme = selected
  return selected
}

export function saveTheme(theme, storage = globalThis.localStorage) {
  const selected = applyTheme(theme)
  try { storage.setItem(THEME_KEY, selected) } catch { /* Theme switching works without browser storage. */ }
  return selected
}
