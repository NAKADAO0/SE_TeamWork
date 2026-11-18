import { compile } from './compiler.js';

// Independent revision snapshots for the example version selector.
export function exampleVersions(plan) {
  return [3, 2, 1].map(revision => {
    const snapshot = structuredClone(plan);
    snapshot.revision = revision;
    snapshot.tasks = snapshot.tasks.slice(0, snapshot.tasks.length - (3 - revision));
    snapshot.batches = compile(snapshot.tasks, snapshot.capacity);
    return snapshot;
  });
}
