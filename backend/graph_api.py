"""Read-only graph views over the existing member 5 plan and artifact tables."""
from contextlib import closing
import json

from fastapi import APIRouter, HTTPException, Query


def graph_router(store):
    router = APIRouter()

    @router.get('/plans')
    def plans():
        with closing(store.connect()) as db:
            rows = db.execute('''SELECT p.id, p.revision, r.document FROM plans p
                JOIN revisions r ON r.plan_id=p.id AND r.revision=p.revision ORDER BY p.id''').fetchall()
        result = []
        for pid, revision, document in rows:
            doc = json.loads(document)
            spec = doc.get('spec') or doc.get('normalized_spec') or {}
            product = spec.get('product') or (doc.get('spec_ref') or {}).get('spec_id') or pid
            title = product.get('name', pid) if isinstance(product, dict) else product
            result.append({'plan_id': pid, 'title': title, 'latest_revision': revision})
        return result

    @router.get('/plans/{pid}/revisions')
    def revisions(pid: str):
        with closing(store.connect()) as db:
            rows = db.execute('SELECT revision FROM revisions WHERE plan_id=? ORDER BY revision DESC', (pid,)).fetchall()
        if not rows:
            raise HTTPException(404, '计划不存在')
        return [{'revision': row[0]} for row in rows]

    @router.get('/plans/{pid}/graph')
    def graph(pid: str, revision: int | None = Query(None, ge=1)):
        with closing(store.connect()) as db:
            db.execute('BEGIN')
            row = db.execute('''SELECT r.revision, r.document FROM revisions r JOIN plans p ON p.id=r.plan_id
                WHERE p.id=? AND r.revision=COALESCE(?,p.revision)''', (pid, revision)).fetchone()
            if row is None:
                raise HTTPException(404, '计划或修订不存在')
            artifacts = {kind: json.loads(body) for kind, body in db.execute(
                'SELECT kind,body FROM artifacts WHERE plan_id=? AND revision=?', (pid, row[0]))}
        plan = json.loads(row[1])
        execution = artifacts.get('execution')
        validation = artifacts.get('validation')
        batches = execution['batches'] if execution else plan.get('batches', [])
        status = 'confirmed' if 'confirmation' in artifacts else 'compiled' if execution or batches else 'validated' if validation and not validation.get('errors') else 'draft'
        plan_payload = {
            **plan,
            'plan_id': pid,
            'revision': row[0],
            'batches': batches,
            'status': status,
        }
        if 'spec' not in plan_payload and 'normalized_spec' in plan:
            plan_payload['spec'] = plan['normalized_spec']
        if execution and execution.get('batch_details') is not None:
            plan_payload['batch_details'] = execution['batch_details']
        if execution and execution.get('topological_levels') is not None:
            plan_payload['topological_levels'] = execution['topological_levels']
        return {
            'plan_id': pid,
            'revision': row[0],
            'plan': plan_payload,
            'execution': execution,
            'validation': validation,
        }

    return router
