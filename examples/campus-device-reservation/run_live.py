import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient
from backend.__main__ import _load_dotenv
from backend.api import create_app

ROOT = Path.cwd()
arg=argparse.ArgumentParser()
arg.add_argument("--out", type=Path,required=True)
opt=arg.parse_args()
out=opt.out.resolve()
out.mkdir(parents=True,exist_ok=True)
db=out/"workflow.sqlite3"
if db.exists():
    raise SystemExit("请选择新的记录目录；不会覆盖现有 SQLite 快照")
_load_dotenv(ROOT/".env")
source=(ROOT/"examples/campus-device-reservation/design-spec.md").read_text(encoding="utf-8-sig")
code="""diff --git a/overlap.py b/overlap.py
new file mode 100644
--- /dev/null
+++ b/overlap.py
@@ -0,0 +1,11 @@
+def overlaps(start, end, existing_start, existing_end):
+    \"Return whether two valid comparable half-open intervals overlap.\"
+    if not (start < end and existing_start < existing_end):
+        raise ValueError("invalid interval")
+    return existing_start < end and start < existing_end
+
+def test_adjacent():
+    assert not overlaps(10, 20, 20, 25)
+
+def test_intersection():
+    assert overlaps(10, 20, 19, 25)
"""
def archive(name,data):
    (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
def backup(name):
    with sqlite3.connect(db) as source_db, sqlite3.connect(out/name) as dest:
        source_db.backup(dest)

def run():
    (out/"input.md").write_text(source,encoding="utf-8")
    (out/"review-input.diff").write_text(code,encoding="utf-8")
    archive("00-metadata.json",{"time_utc":datetime.now(timezone.utc).isoformat(),"source_lines":len(source.splitlines()),
             "input_sha256":hashlib.sha256(source.encode()).hexdigest(),"mode":"real DeepSeek, isolated local DB"})
    with TestClient(create_app(db), raise_server_exceptions=False) as client:
        def req(step,method,path,**kwargs):
            print(step,flush=True)
            response=client.request(method,path,**kwargs)
            try: body=response.json()
            except ValueError: body=response.text
            archive(step+".json",{"status":response.status_code,"response":body})
            response.raise_for_status()
            return body
        reviewed=req("01-spec-ai-review","POST","/specs/review",json={"text":source})
        assert isinstance(reviewed["review"],dict),"model did not provide review findings"
        (out/"reviewed-draft.md").write_text(reviewed["text"],encoding="utf-8")
        # Use the original acceptance baseline if the reviewer proposed semantic changes.
        spec=req("02-submit-source","POST","/specs",json={"text":source})
        assert not spec["issues"]["errors"],spec["issues"]
        sid=quote(spec["spec_id"],safe="")
        confirmed=req("03-human-confirm-spec","POST",f"/specs/{sid}/confirm",json={"expected_version":spec["version"]})
        job=req("04-start-live-planning","POST","/planning-jobs",json={"spec_ref":confirmed["spec_ref"],"mode":"live_llm"})
        done=req("05-live-planning-result","GET",f"/planning-jobs/{job['job_id']}")
        assert done["status"]=="succeeded",done
        pid, rev = done["plan_ref"]["plan_id"],done["plan_ref"]["revision"]
        base="/plans/"+quote(pid,safe="")
        incoming=req("06-taskplan","GET",base,params={"revision":rev})
        checks=req("07-validation","POST",base+"/validate",json={"revision":rev})
        compiled=req("08-execution-plan","POST",base+"/compile",json={"revision":rev})
        req("09-human-confirm-plan","POST",base+"/confirm",json={"revision":rev,"execution_plan_id":compiled["execution_plan_id"]})
        exported=req("10-confirmed-plan-export","GET",base+"/export",params={"revision":rev})
        archive("confirmed-plan.json",exported)
        backup("before-review.sqlite3")
        sha=hashlib.sha1(code.encode()).hexdigest()
        submit={"revision":rev,"commit_sha":sha,"author":"lab-author","change_ref":"local-acceptance-fragment","mode":"live","diff":code}
        req("11-submit-code-for-review","POST",base+"/review",json=submit)
        result=req("12-real-code-review","POST",base+"/review/ai",json={"revision":rev,"commit_sha":sha})
        backup("after-ai-review.sqlite3")
        final_status=result["current"]["status"]
        if final_status=="awaiting_human":
            result=req("13-approve-demo-review","POST",base+"/review/decision",json={
                "revision":rev,"commit_sha":sha,"reviewer":"lab-reviewer",
                "decision":"approve","reason":"验收演示：已复核半开区间边界和输入约束；不代表完整产品已通过测试。"})
            final_status=result["current"]["status"]
        backup("final.sqlite3")
    restore=out/"restored-verification.sqlite3"
    with sqlite3.connect(out/"after-ai-review.sqlite3") as saved, sqlite3.connect(restore) as target:
        saved.backup(target)
    with TestClient(create_app(restore)) as client:
        old=client.get(base+"/review",params={"revision":rev}).json()
        restored_export=client.get(base+"/export",params={"revision":rev}).json()
        assert old["current"]["status"] != "approved" and restored_export==exported
    archive("99-summary.json",{"plan_id":pid,"revision":rev,"generation_mode":incoming["plan"]["generation_mode"],
        "tasks":len(compiled["nodes"]),"batches":len(compiled["batches"]),"validation_errors":len(checks["errors"]),
        "ai_sources":[attempts[-1].get("source") for attempts in result["current"]["checks"].values()],
        "final_status":final_status,"restored_review_status":old["current"]["status"],"restored_export_identical":True})
    print((out/"99-summary.json").read_text(encoding="utf-8"),flush=True)
run()
