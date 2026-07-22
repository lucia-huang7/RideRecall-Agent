"""Original end-to-end evaluator and job queue with explicit offline rubric contracts."""
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from agents.agent_orchestrator import AgentType
from core.intent_recognizer import IntentCategory
from evaluation.evaluator import EndToEndEvaluator, LLMJudge, IntentTestCase
from evaluation.jobs import EvaluationJobs
from mobility.providers import response


class RubricModel:
    """Mechanical citation/boundary checks, not a model or a semantic quality score."""
    base_url = "local://ride-rubric-v1"
    def __init__(self):
        self.messages = self

    async def create(self, **kwargs):
        prompt = kwargs['messages'][-1]['content']
        answer = prompt.split('BEGIN_RESPONSE\n', 1)[-1].split('\nEND_RESPONSE', 1)[0]
        cited = bool(re.search(r'\[[a-f0-9]{8}:\d+\]', answer))
        boundary = 'No payment action was performed' in answer
        next_step = 'case controls' in answer
        return response(json.dumps(dict(relevance=float(bool(answer.strip())), accuracy=float(cited and boundary), completeness=float(cited and next_step), helpfulness=float(next_step))))


class RideJudge(LLMJudge):
    JUDGE_PROMPT = 'Check the fictional ride response using the explicit offline rubric. {few_shot_examples}\nQUESTION: {question}\nBEGIN_RESPONSE\n{response}\nEND_RESPONSE\n{context_section}'

    @staticmethod
    def few_shot_prompt():
        return 'Rubric v1: require a nonempty answer, a retrieved source citation, an explicit no-payment boundary, and a reference to case controls. These mechanical checks do not establish factual correctness or helpfulness.'


class EvaluationAdapter:
    def __init__(self, engine):
        self.engine = engine
        self.records = {}
        from mobility.diagnostics import DiagnosticCases
        self.fixtures = DiagnosticCases(engine)

    async def run(self, req):
        record = self.records.setdefault(req.conv_id, dict(id=uuid4().hex, owner=uuid4().hex, trip_id='trip-river', messages=[]))
        run = await self.fixtures.turn(record,req.message)
        req.conversation_state = run.get('memory',{}).get('state',{})
        req.entities = req.conversation_state.get('entities',{})
        return SimpleNamespace(response=run['response'], status=run['status'], agent_type=AgentType(run['agents'][0] if run['agents'] else 'general'), intent=IntentCategory(run['intent']), tools_used=[t.get('tool_name') for t in run['tools']])

    async def cleanup(self):
        await self.fixtures.close()
        self.records.clear()


class RideEvaluator(EndToEndEvaluator):
    def _save_baseline(self, report):
        if report.total and report.passed == report.total:
            super()._save_baseline(report)

    def _comparison_baseline(self, suite_id):
        candidates = list(reversed(self._history)) + ([self._baseline] if self._baseline else [])
        return next((r for r in candidates if r.status == "complete" and r.passed == r.total and not r.regressions and r.suite_id == suite_id), None)

    def _suite_id(self, intent_cases, dialog_cases):
        base = super()._suite_id(intent_cases, dialog_cases)
        return hashlib.sha256((base+RideJudge.JUDGE_PROMPT+RideJudge.few_shot_prompt()).encode()).hexdigest()


class RideQuality:
    def __init__(self, engine):
        self.adapter = EvaluationAdapter(engine)
        self.evaluator = RideEvaluator(self.adapter, engine.recognizer, api_key='unused', judge=RideJudge(RubricModel(), 'offline-rubric-v1'), baseline_path=str(Path(engine.support.store.path).with_suffix('.baseline.json')))
        self.jobs = EvaluationJobs(capacity=2, retained=10, timeout=180)
        self.engine = engine

    async def run(self, progress):
        try:
            cases = [dict(conv_id='correction', turns=['The driver never arrived. I dispute $6.50.', 'Correction: the amount I dispute is $5.25.', 'Please connect me to a human representative.'], expected_agent='escalation', expected_intent='human_handoff', expected_entities={'amount':['$5.25']}, required_tools=['get_case_context','search_knowledge_base','mcp_rides_read_trip_record']), dict(conv_id='compound', turns=['The app crashed with error 500 and I was charged a cancellation fee.'], expected_agent='technical', required_tools=['get_trip','search_knowledge_base','mcp_rides_read_trip_record'])]
            intents = [IntentTestCase(message='Please connect me to a human representative.', expected_intent='human_handoff'),IntentTestCase(message='The app crashed with error 500.',expected_intent='technical_crash')]
            baseline = bool(self.evaluator._comparison_baseline(self.evaluator._suite_id(intents,cases)))
            report = await self.evaluator.run(intent_cases=intents, dialog_cases=cases, progress=progress)
            data = asdict(report)
            data.update(status=report.status, baseline_compared=baseline, rubric='Mechanical citation and boundary checks; not LLM-as-judge or real-world quality.', regression_note='Compared with the same fixture and rubric configuration.' if baseline else 'First run: a comparable baseline will be saved.', recommendations=['Review failed execution or rubric checks.' if report.passed<report.total else 'All current fixture checks passed. Expand independent fixtures before making quality claims.'])
            data['regressions'] = [re.sub(r'\([^)]*\)$', '(decreased)', item) for item in data['regressions']]
            for item in data['results']:
                item['detail'] = 'Passed execution and rubric checks.' if item['passed'] else 'Execution or rubric checks need attention.'
                item['metadata']['judge_few_shot_version']='ride-rubric-v1'
                item['metadata']['judge_few_shot_count']=0
            with self.engine.support.store.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS quality_reports(id TEXT PRIMARY KEY,data TEXT)')
                db.execute('INSERT INTO quality_reports VALUES(?,?)',(uuid4().hex,json.dumps(data)))
                db.execute('DELETE FROM quality_reports WHERE id NOT IN (SELECT id FROM quality_reports ORDER BY rowid DESC LIMIT 10)')
            return data
        finally:
            await self.adapter.cleanup()

    def history(self):
        with self.engine.support.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS quality_reports(id TEXT PRIMARY KEY,data TEXT)')
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM quality_reports ORDER BY rowid DESC LIMIT 10')]

    def public_job(self, job_id):
        job = self.jobs.get(job_id)
        if job and job.get('error'):
            job['error'] = 'Evaluation could not complete. Retry or inspect the local service.'
        return job
