from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from backend.api import create_app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/"plans.db"

    def test_custom_product_through_export_and_revision_change(self):
        with TestClient(create_app(self.path)) as client:
            spec={"product":"书架","requirements":[{"id":"R1","goal":"收藏图书","acceptance":"刷新仍可读取"}]}
            sid=client.post("/specs",json=spec).json()["spec_id"]
            req={"spec_ref":{"spec_id":sid,"version":1},"config":{"max_concurrency":1}}
            self.assertEqual(client.post("/planning-jobs",json=req).status_code,409)
            client.post(f"/specs/{sid}/confirm",json={"expected_version":1}).raise_for_status()
            self.assertEqual(client.post("/planning-jobs",json=dict(req,mode="live_llm")).status_code,503)
            submitted=client.post("/planning-jobs",json=req)
            self.assertEqual(submitted.status_code,202)
            job=client.get("/planning-jobs/"+submitted.json()["job_id"]).json()
            self.assertEqual(job["status"],"succeeded")
            route="/plans/"+job["plan_ref"]["plan_id"]
            revision={"revision":1}
            report=client.post(route+"/validate",json=revision).json()
            self.assertEqual(report["errors"],[])
            execution=client.post(route+"/compile",json=revision).json()
            self.assertEqual(execution["batches"],[["R1-I"],["R1-V"]])
            self.assertEqual(execution["validation_ref"],report["validation_ref"])
            self.assertEqual(client.get(route+"/export?revision=1").status_code,409)
            confirm=dict(revision,execution_plan_id=execution["execution_plan_id"])
            client.post(route+"/confirm",json=confirm).raise_for_status()
            bundle=client.get(route+"/export?revision=1").json()
            self.assertEqual(bundle["plan"]["spec"],spec)
            self.assertEqual(bundle["execution"],execution)
            self.assertIn("收藏图书",client.get(route+"/export?revision=1&format=markdown").text)
            draft=client.get(route).json()["plan"]
            self.assertEqual(draft["status"],"confirmed")
            draft["tasks"][0]["depends_on"]=["R1-V"]
            client.put(route,json={"expected_revision":1,"plan":draft}).raise_for_status()
            self.assertEqual(client.post(route+"/confirm",json=dict(confirm,revision=2)).status_code,409)
            self.assertEqual(client.post(route+"/compile",json={"revision":2}).status_code,422)
        with TestClient(create_app(self.path)) as client:
            self.assertEqual(client.get(route+"/export?revision=1").json(),bundle)
