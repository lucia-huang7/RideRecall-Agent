export const THEME_KEY = "llmagentsystem.rides.theme";

export function loadTheme(storage = globalThis.localStorage) {
  try {
    let value = storage.getItem(THEME_KEY);
    if (value === null) {
      const previous = [];
      for (let i = 0; i < Math.min(storage.length || 0, 64); i++) {
        const key = storage.key(i);
        if (key !== THEME_KEY && key?.endsWith(".rides.theme")) {
          const saved = storage.getItem(key);
          if (["mono", "rainbow"].includes(saved) && !previous.includes(saved))
            previous.push(saved);
        }
      }
      if (previous.length === 1) value = previous[0];
      if (value) storage.setItem(THEME_KEY, value);
    }
    return value === "rainbow" ? "rainbow" : "mono";
  } catch {
    return "mono";
  }
}

export function applyTheme(theme, root = globalThis.document?.documentElement) {
  const selected = theme === "rainbow" ? "rainbow" : "mono";
  if (root) root.dataset.theme = selected;
  return selected;
}

export function saveTheme(theme, storage = globalThis.localStorage) {
  const selected = applyTheme(theme);
  try {
    storage.setItem(THEME_KEY, selected);
  } catch {
    /* Theme switching works without browser storage. */
  }
  return selected;
}
