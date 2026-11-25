"""Runtime invariants and integration against existing planning/delivery services.

Model outputs are deterministic test doubles. These tests do not claim live model
quality, external platform validation, or code execution.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.api import create_app
from backend.store import PlanStore
from backend.studio import storage
from backend.studio.runtime import call_model
from backend.app.planner.smart_rules import plan_with_provider as original_plan

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "examples/contracts/chat/design-spec.json").read_text())
ENV = {"PM_BASE_URL":"http://model.invalid", "PM_API_KEY":"test-only", "PM_MODEL":"test-only"}


def model(kind, context, role=None):
    if kind == "specialists":
        return {"roles": []}
    if kind == "proposal":
        return dict(summary=role["name"]+"独立方案", sections=[dict(title="职责设计",content="围绕已确认需求提供具体设计与验收边界",requirement_ids=["R1"])],
                    replies=[dict(proposal_id=context["reply_targets"][0]["id"],position="supplement",reason="补充边界",change="补充方案中的异常处理")] if context.get("reply_targets") else [],
                    evidence=["用户提供的规格"], risks=[], questions=[], disagreements=[])
    if kind == "synthesis":
        return dict(summary="保留输入规格，确认已有设计", spec=copy.deepcopy(context.get("human_draft") or context.get("baseline") or SPEC), assumptions=[], questions=[], disagreements=[],
                    decisions=[dict(proposal_id=p["id"],disposition="accepted",reason="符合已确认范围") for p in context["proposals"]])
    return dict(summary="审查通过", passed=True, issues=[])


def offline_pm(spec, template, config, **kwargs):
    return original_plan(spec, template, config, mode="offline_rule")


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"studio.sqlite3"
        self.env=patch.dict("os.environ",ENV)
        self.env.start(); self.addCleanup(self.env.stop)
        self.mock=patch("backend.studio.runtime.call_model",side_effect=model).start()
        self.addCleanup(patch.stopall)
        patch("backend.workflow.plan_with_provider",side_effect=offline_pm).start()
        self.client=TestClient(create_app(self.path))
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def create(self):
        response=self.client.post('/studio/runs',json={"source":json.dumps(SPEC),"input_mode":"spec"})
        response.raise_for_status();return response.json()["id"]

    def read(self,rid):return self.client.get('/studio/runs/'+rid).json()

    def command(self,rid,action,**extra):
        response=self.client.post('/studio/runs/'+rid+'/commands',json={"action":action,"expected_sequence":self.read(rid)["sequence"],**extra})
        response.raise_for_status();return self.read(rid)

    def test_three_human_gates_then_existing_planner_compile_confirm_export(self):
        rid=self.create()
        for stage,next_stage in [('requirements','product'),('product','architecture'),('architecture','planning')]:
            state=self.command(rid,'start')
            self.assertEqual(state['stage'],stage)
            self.assertEqual(state['status'],'awaiting_confirmation')
            self.assertFalse(state['stages'][stage].get('confirmed',False))
            state=self.command(rid,'confirm')
            self.assertEqual(state['stage'],next_stage)
            self.assertEqual(state['status'],'ready')
        state=self.command(rid,'start')
        self.assertEqual(state['status'],'awaiting_confirmation',state.get('error'))
        self.assertEqual(state['stage'],'confirmation')
        self.assertTrue(state['execution']['compiled'])
        ref=state['plan_ref']
        self.assertEqual(self.client.get(f"/plans/{ref['plan_id']}/export?revision=1").status_code,409)
        state=self.command(rid,'confirm')
        self.assertEqual(state['status'],'complete')
        exported=self.client.get(f"/plans/{ref['plan_id']}/export?revision=1")
        exported.raise_for_status()
        self.assertEqual(exported.json()['execution'],state['execution'])
        self.assertEqual(self.mock.call_count,30) # 3 x (role selection + 3 proposals + synthesis + review)

    def test_reviewer_receives_real_proposals_and_gate_uses_consolidated_questions(self):
        def questions(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='proposal':result['questions']=['重复问题']
            if kind=='synthesis':result['questions']=['合并后的阻断问题']
            return result
        self.mock.side_effect=questions
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['stages']['requirements']['questions'],['合并后的阻断问题'])
        review_call=next(c for c in self.mock.call_args_list if c.args[0]=='review')
        self.assertEqual(len(review_call.args[1]['proposals']),6)
        self.assertTrue(all(p['summary'] for p in review_call.args[1]['proposals']))

    def test_initial_proposals_are_independent(self):
        rid=self.create();self.command(rid,'start')
        calls=[c for c in self.mock.call_args_list if c.args[0]=='proposal' and c.args[1]['discussion_round']==0]
        self.assertEqual(len(calls),3)
        self.assertTrue(all(c.args[1]['previous_discussion']==[] for c in calls))
        self.assertEqual(len({c.args[2]['id'] for c in calls}),3)

    def test_requires_user_never_auto_confirms_or_revises(self):
        def ask(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='synthesis':result['questions']=['是否要求后台持续定位？']
            return result
        self.mock.side_effect=ask
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['status'],'needs_input',state.get('error'))
        self.assertEqual(len(state['artifacts']),1)
        response=self.client.post('/studio/runs/'+rid+'/commands',json={"action":"confirm","expected_sequence":state['sequence']})
        self.assertEqual(response.status_code,409)

    def test_review_repairs_at_most_twice_and_keeps_all_versions(self):
        def fail_review(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='review':result=dict(summary="接口证据不足",passed=False,issues=[dict(id='ARC1',severity='blocking',message='需要补设计',requires_user=False)])
            return result
        self.mock.side_effect=fail_review
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['status'],'needs_input',state.get('error'))
        self.assertEqual(len(state['artifacts']),3)
        self.assertEqual(len(state['feedback']),2)
        self.assertEqual(sum(c.args[0]=='review' for c in self.mock.call_args_list),3)
        self.assertEqual(sum(c.args[0]=='proposal' for c in self.mock.call_args_list),6)

    def test_explicit_risk_acceptance_keeps_failed_review_and_requires_confirmation(self):
        def fail_review(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='review':result=dict(summary="尚有技术风险",passed=False,issues=[dict(id='ARC1',severity='blocking',message='平台需后续验证',requires_user=False)])
            return result
        self.mock.side_effect=fail_review
        rid=self.create();self.command(rid,'start')
        state=self.command(rid,'accept_risk',note='接受平台风险，将验证列为开发前置条件')
        self.assertEqual(state['status'],'awaiting_confirmation')
        self.assertFalse(state['stages']['requirements']['review']['passed'])
        self.assertTrue(state['stages']['requirements']['risk_accepted'])
        self.assertEqual(self.command(rid,'confirm')['stage'],'product')

    def test_unresolved_discussion_has_at_most_two_extra_rounds(self):
        def disagree(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='synthesis':result['disagreements']=['平台能力尚无证据']
            return result
        self.mock.side_effect=disagree
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['status'],'needs_input',state.get('error'))
        self.assertEqual(sum(c.args[0]=='synthesis' for c in self.mock.call_args_list),3)
        self.assertEqual(sum(c.args[0]=='proposal' for c in self.mock.call_args_list),9)

    def test_program_gate_rejects_changed_imported_scope_even_when_model_passes(self):
        def change_scope(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='synthesis':result['spec']['scope']['included'].append('支付')
            return result
        self.mock.side_effect=change_scope
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['status'],'needs_input',state.get('error'))
        self.assertFalse(state['stages']['requirements']['review']['passed'])
        self.assertTrue(any(i['id']=='BASELINE-scope' for i in state['stages']['requirements']['review']['issues']))

    def test_cross_stage_replan_invalidates_downstream_but_preserves_history(self):
        rid=self.create()
        for _ in range(3):self.command(rid,'start');self.command(rid,'confirm')
        before=self.read(rid)
        state=self.command(rid,'replan',target_stage='product',note='重新核对页面异常状态')
        self.assertTrue(state['stages']['requirements']['confirmed'])
        self.assertFalse(state['stages']['product'].get('confirmed',False))
        self.assertFalse(state['stages']['architecture'].get('confirmed',False))
        self.assertGreater(len(state['artifacts']),len(before['artifacts']))
        self.assertTrue(any(a['superseded'] for a in state['artifacts']))
        self.assertEqual(state['feedback'][-1]['scope'],'cross_stage')
        snapshot=self.client.get(f"/studio/runs/{rid}?sequence={before['sequence']}").json()
        self.assertEqual(snapshot,before)

    def test_stale_commands_and_browsing_do_not_mutate(self):
        rid=self.create();before=self.read(rid)
        response=self.client.post('/studio/runs/'+rid+'/commands',json={"action":"start","expected_sequence":999})
        self.assertEqual(response.status_code,409)
        self.client.get('/studio/definition');self.client.get('/studio/runs/'+rid+'/events')
        self.client.get('/studio/runs/'+rid+'?sequence=1')
        self.assertEqual(self.read(rid),before)

    def test_pause_at_call_boundary_resume_reuses_successful_results(self):
        rid=self.create();triggered=False
        def pause(kind,context,role=None):
            nonlocal triggered
            result=model(kind,context,role)
            if kind=='proposal' and not triggered:
                triggered=True
                storage.change(PlanStore(self.path),rid,'command.pause',mutate=lambda s:s.update(pause_requested=True,status='pausing'))
            return result
        self.mock.side_effect=pause
        state=self.command(rid,'start')
        self.assertEqual(state['status'],'paused')
        self.assertEqual(sum(c.args[0]=='proposal' for c in self.mock.call_args_list),1)
        state=self.command(rid,'resume')
        self.assertEqual(state['status'],'awaiting_confirmation')
        self.assertEqual(sum(c.args[0]=='proposal' for c in self.mock.call_args_list),6)

    def test_bad_model_response_is_failed_not_silently_simulated(self):
        self.mock.side_effect=ValueError('模型 JSON 无效')
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['artifacts'],[])
        self.assertIn('模型 JSON 无效',state['error'])
        self.mock.side_effect=model
        self.assertEqual(self.command(rid,'resume')['status'],'awaiting_confirmation')

    def test_server_restart_preserves_events_and_pauses_interrupted_run(self):
        rid=self.create()
        storage.change(PlanStore(self.path),rid,'command.start',mutate=lambda s:s.update(status='running'))
        with TestClient(create_app(self.path)) as restarted:
            state=restarted.get('/studio/runs/'+rid).json()
            self.assertEqual(state['status'],'paused')
            events=restarted.get('/studio/runs/'+rid+'/events').json()
            self.assertEqual([e['sequence'] for e in events],list(range(1,len(events)+1)))
            self.assertEqual(events[-1]['type'],'run.interrupted')

    def test_single_step_runs_one_new_node_and_pauses(self):
        rid=self.create()
        state=self.command(rid,'step')
        self.assertEqual(state['status'],'paused')
        self.assertEqual(len([e for e in state['executions'] if e['status']=='succeeded']),1)
        self.assertEqual(self.mock.call_count,1)
        state=self.command(rid,'step')
        self.assertEqual(state['status'],'paused')
        self.assertEqual(self.mock.call_count,2)
        self.assertEqual(self.command(rid,'resume')['status'],'awaiting_confirmation')

    def test_mandatory_peer_review_references_real_other_role_proposals(self):
        rid=self.create();state=self.command(rid,'start')
        rounds=[call for call in self.mock.call_args_list if call.args[0]=='proposal']
        self.assertEqual([call.args[1]['discussion_round'] for call in rounds],[0,0,0,1,1,1])
        for call in rounds[3:]:
            self.assertTrue(call.args[1]['reply_targets'])
            self.assertTrue(all(proposal['role']['id']!=call.args[2]['id'] for proposal in call.args[1]['reply_targets']))
        self.assertEqual(state['status'],'awaiting_confirmation')

    def test_invented_reply_target_is_rejected(self):
        def invented(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='proposal' and context['discussion_round']:
                result['replies'][0]['proposal_id']='invented-proposal'
            return result
        self.mock.side_effect=invented
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['status'],'failed')
        self.assertIn('回应引用',state['error'])
        self.assertEqual(state['artifacts'],[])

    def test_human_edit_reuses_proposals_and_only_integrates_then_reviews(self):
        def editable(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='synthesis' and context.get('human_draft'):
                result['spec']=copy.deepcopy(context['human_draft'])
            return result
        self.mock.side_effect=editable
        rid=self.create();before=self.command(rid,'start')
        count=len(self.mock.call_args_list)
        edited=copy.deepcopy(before['stages']['requirements']['draft']['spec'])
        edited['requirements'][0]['acceptance']='发送成功后返回消息编号，并在历史中可查询'
        after=self.command(rid,'replan',target_stage='requirements',note='补充验收条件',spec=edited)
        self.assertEqual([call.args[0] for call in self.mock.call_args_list[count:]],['synthesis','review'])
        self.assertEqual(after['status'],'awaiting_confirmation')
        self.assertFalse(after['stages']['requirements'].get('confirmed',False))
        self.assertEqual(after['stages']['requirements']['draft']['spec'],edited)
        self.assertGreater(len(after['artifacts']),len(before['artifacts']))

    def test_ai_options_and_free_answer_are_recorded_before_reintegration(self):
        def ask(kind,context,role=None):
            result=model(kind,context,role)
            if kind=='synthesis' and not context['answers']:
                result['questions']=['是否复用现有登录？']
                result['decision_requests']=[dict(id='Q1',question='是否复用现有登录？',recommendation='建议复用',options=[dict(label='复用',description='减少重复开发')],impact='影响鉴权设计')]
            return result
        self.mock.side_effect=ask
        rid=self.create();state=self.command(rid,'start')
        self.assertEqual(state['status'],'needs_input')
        count=len(self.mock.call_args_list)
        answer=dict(question_id='Q1',selected='复用',free_text='只接学校统一认证')
        state=self.command(rid,'answer',note='复用现有登录，只接学校统一认证',answers=[answer])
        self.assertEqual(state['answers'][-1]['choices'],[answer])
        self.assertEqual([call.args[0] for call in self.mock.call_args_list[count:]],['synthesis','review'])
        self.assertEqual(state['status'],'awaiting_confirmation')

    def test_opinion_only_model_output_cannot_count_as_role_design(self):
        with patch('backend.studio.runtime.HttpProvider.from_env') as provider:
            provider.return_value.complete.return_value=json.dumps(dict(summary='只列了风险，没有方案',evidence=[],risks=[],questions=[],disagreements=[]))
            with self.assertRaisesRegex(Exception,'返回格式无效'):
                call_model('proposal',{},dict(id='business',name='业务分析',focus='需求'))

    def test_model_output_shape_is_checked(self):
        with patch('backend.studio.runtime.HttpProvider.from_env') as provider:
            provider.return_value.complete.return_value='{"passed":"yes"}'
            with self.assertRaisesRegex(Exception,'返回格式无效'):call_model('review',{})

if __name__=='__main__':unittest.main()
