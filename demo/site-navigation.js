import { currentGraphUrl } from './graph-navigation.js';

export function updateGraphLinks() {
  for (const link of document.querySelectorAll('a[href]')) {
    const url = new URL(link.getAttribute('href'), location.href);
    if (!url.pathname.endsWith('/graph.html') || url.search) continue;
    // Keep the original destination so repeated pageshow events can refresh it.
    link.dataset.graphDestination ??= url.href;
  }
  for (const link of document.querySelectorAll('[data-graph-destination]')) {
    try {
      link.href = currentGraphUrl(new URL(link.dataset.graphDestination), sessionStorage);
    } catch { /* Leave the normal link available when storage is disabled. */ }
  }
}

updateGraphLinks();
window.addEventListener('pageshow', updateGraphLinks);
