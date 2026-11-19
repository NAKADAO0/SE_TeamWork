const storageKey = 'se.graph.current';

export function rememberGraph(url, storage) {
  try {
    storage.setItem(storageKey, url.pathname + url.search);
  } catch { /* Navigation still works when browser storage is unavailable. */ }
}

export function currentGraphUrl(fallback, storage) {
  try {
    const saved = storage.getItem(storageKey);
    if (!saved) return fallback.href;
    const url = new URL(saved, fallback);
    if (url.origin === fallback.origin && url.pathname === fallback.pathname) return url.href;
  } catch { /* Ignore unavailable storage or an invalid saved URL. */ }
  return fallback.href;
}
