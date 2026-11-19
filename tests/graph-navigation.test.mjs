import test from 'node:test';
import assert from 'node:assert/strict';
import { currentGraphUrl, rememberGraph } from '../demo/graph-navigation.js';

const fallback = new URL('http://localhost:8010/graph.html');
function memoryStorage() {
  const values = new Map();
  return { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
}

test('navigation retains the current backend plan, API and exact revision', () => {
  const storage = memoryStorage();
  const current = new URL('graph.html?api=http&plan_id=PLAN-2&revision=4', fallback);
  rememberGraph(current, storage);
  assert.equal(currentGraphUrl(fallback, storage), current.href);
});

test('navigation retains local plans and explicitly selected example revisions', () => {
  const storage = memoryStorage();
  for (const query of ['sample=current&revision=2', 'sample=community&revision=1']) {
    const current = new URL(`?${query}`, fallback);
    rememberGraph(current, storage);
    assert.equal(currentGraphUrl(fallback, storage), current.href);
  }
});

test('missing, inaccessible or invalid saved destinations keep the normal link', () => {
  assert.equal(currentGraphUrl(fallback, memoryStorage()), fallback.href);
  assert.equal(currentGraphUrl(fallback, { getItem() { throw new Error('disabled'); } }), fallback.href);
  for (const invalid of ['https://elsewhere.test/graph.html', '/examples.html', '//elsewhere.test/graph.html']) {
    assert.equal(currentGraphUrl(fallback, { getItem: () => invalid }), fallback.href);
  }
});

test('examples sidebar restores the current graph while example cards keep their targets', async () => {
  const storage = memoryStorage();
  const current = new URL('?api=http&plan_id=PLAN-2&revision=4', fallback);
  rememberGraph(current, storage);
  const links = ['graph.html', 'graph.html?sample=chat', 'graph.html?sample=community'].map(href => ({
    href, dataset: {}, getAttribute: () => href,
  }));
  globalThis.location = new URL('examples.html', fallback);
  globalThis.sessionStorage = storage;
  globalThis.document = { querySelectorAll: selector => selector === 'a[href]' ? links : links.filter(link => link.dataset.graphDestination) };
  let onPageShow;
  globalThis.window = { addEventListener: (name, callback) => { onPageShow = callback; } };
  await import('../demo/site-navigation.js');
  assert.equal(links[0].href, current.href);
  assert.equal(links[1].href, 'graph.html?sample=chat');
  assert.equal(links[2].href, 'graph.html?sample=community');
  const next = new URL('?sample=current&revision=5', fallback);
  rememberGraph(next, storage);
  onPageShow();
  assert.equal(links[0].href, next.href);
});
