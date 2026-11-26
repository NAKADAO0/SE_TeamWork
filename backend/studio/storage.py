"""Atomic run state and ordered, replayable events in the existing SQLite database."""
import copy
import json
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from ..workflow import transaction


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize(store):
    with closing(store.connect()) as db, db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS studio_runs(id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS studio_events(run_id TEXT NOT NULL REFERENCES studio_runs(id), sequence INTEGER NOT NULL,
            body TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(run_id,sequence));
        ''')
    # A single-process runtime never silently resumes an interrupted external call.
    for run in list_runs(store, limit=-1):
        if run["status"] in ("running", "pausing", "confirming"):
            def interrupt(state):
                state.update(status="paused", error="服务重启，请继续运行", pause_requested=False)
                for execution in state["executions"]:
                    if execution["status"] == "running":
                        execution.update(status="interrupted", ended_at=now())
            change(store, run["id"], "run.interrupted", {"reason": "服务重启，运行已暂停；可继续，历史结果保留。"},
                   interrupt)


def read(store, rid, sequence=None):
    with closing(store.connect()) as db:
        if sequence is not None:
            row = db.execute("SELECT snapshot FROM studio_events WHERE run_id=? AND sequence=?", (rid, sequence)).fetchone()
        else:
            row = db.execute("SELECT body FROM studio_runs WHERE id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "运行或历史事件不存在")
    return json.loads(row[0])


def list_runs(store, limit=50):
    with closing(store.connect()) as db:
        return [json.loads(row[0]) for row in db.execute("SELECT body FROM studio_runs ORDER BY updated_at DESC LIMIT ?", (limit,))]


def create(store, source, input_mode, spec):
    rid = "RUN-" + uuid4().hex[:12]
    state = dict(id=rid, source=source, input_mode=input_mode, status="ready", stage="requirements", sequence=0,
                 created_at=now(), updated_at=now(), spec=spec, stages={}, executions=[], artifacts=[],
                 feedback=[], answers=[], plan_ref=None, error=None, pause_requested=False)
    with transaction(store) as db:
        db.execute("INSERT INTO studio_runs VALUES (?,?,?)", (rid, state["updated_at"], json.dumps(state, ensure_ascii=False)))
    return change(store, rid, "run.created", {"input_mode": input_mode})


def change(store, rid, event_type, payload=None, mutate=None, expected=None):
    with transaction(store) as db:
        row = db.execute("SELECT body FROM studio_runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "运行不存在")
        state = json.loads(row[0])
        if expected is not None and state["sequence"] != expected:
            raise HTTPException(409, "运行已更新，请刷新后操作")
        if mutate:
            mutate(state)
        state["sequence"] += 1
        state["updated_at"] = now()
        event = dict(event_id=uuid4().hex, run_id=rid, sequence=state["sequence"], type=event_type,
                     stage_id=state["stage"], timestamp=state["updated_at"])
        event.update(payload or {})
        body = json.dumps(state, ensure_ascii=False)
        db.execute("UPDATE studio_runs SET updated_at=?,body=? WHERE id=?", (state["updated_at"], body, rid))
        db.execute("INSERT INTO studio_events VALUES (?,?,?,?)", (rid, state["sequence"], json.dumps(event, ensure_ascii=False), body))
    return copy.deepcopy(state)


def events(store, rid, after=0):
    read(store, rid)
    with closing(store.connect()) as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT body FROM studio_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 1000", (rid, after))]
