from contextlib import closing
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


class PlanStore:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        seed = (ROOT / "examples/chat-plan.json").read_text(encoding="utf-8")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS revisions (
                plan_id TEXT NOT NULL REFERENCES plans(id), revision INTEGER NOT NULL,
                document TEXT NOT NULL, PRIMARY KEY (plan_id, revision))""")
            db.execute("INSERT OR IGNORE INTO plans VALUES ('chat-demo', 1)")
            db.execute("INSERT OR IGNORE INTO revisions VALUES ('chat-demo', 1, ?)", (seed,))

    def read(self, plan_id, revision=None):
        with closing(self.connect()) as db:
            row = db.execute("""SELECT r.revision, r.document, EXISTS(SELECT 1 FROM artifacts a WHERE a.plan_id=r.plan_id AND a.revision=r.revision AND a.kind='confirmation') FROM revisions r
                JOIN plans p ON p.id = r.plan_id
                WHERE p.id = ? AND r.revision = COALESCE(?, p.revision)""",
                (plan_id, revision)).fetchone()
        if row is None:
            raise KeyError(plan_id)
        plan = json.loads(row[1])
        if row[2]:
            plan["status"] = "confirmed"
        return {"plan_id": plan_id, "revision": row[0], "plan": plan}

    def append(self, plan_id, expected_revision, draft):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise KeyError(plan_id)
            if row[0] != expected_revision:
                raise RevisionConflict(row[0])
            revision = expected_revision + 1
            db.execute("INSERT INTO revisions VALUES (?, ?, ?)",
                       (plan_id, revision, json.dumps(draft, ensure_ascii=False)))
            db.execute("UPDATE plans SET revision = ? WHERE id = ?", (revision, plan_id))
        return {"plan_id": plan_id, "revision": revision, "plan": draft}


class RevisionConflict(Exception):
    pass
