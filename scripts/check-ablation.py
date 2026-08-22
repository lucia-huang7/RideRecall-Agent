"""Paired no-key ablations through the unchanged synthetic HTTP replay pipeline."""
import argparse
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import re
import time
from unittest.mock import patch

CONFIGURATIONS = ('single_recent4', 'multi_recent4', 'single_durable', 'multi_durable')


def recent_rounds(messages, rounds=4):
    """Keep the last four rider-started rounds, including intervening replies."""
    starts = [i for i, message in enumerate(messages) if message['role'] == 'rider']
    return messages[starts[-min(rounds, len(starts))]:] if starts else messages[-2 * rounds:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--configuration', choices=CONFIGURATIONS, required=True)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite an existing result directory')
    args.output.mkdir(parents=True)
    from agents.agent_orchestrator import AgentType, BaseAgent, RoutingDecision
    from mobility.engine import RideEngine, RideOrchestrator
    from mobility.memory import CaseMemory
    from mobility.providers import LocalMessages
    replay_module = importlib.import_module('mobility.simulation.replay')
    original_init = CaseMemory.__init__
    original_tools = BaseAgent.set_shared_tools
    original_answer = LocalMessages.answer
    original_run = RideEngine._run
    original_score = replay_module.score_case
    metrics = args.output / 'observations.jsonl'
    active_configuration = args.configuration

    def window_init(self, engine, case, trip_id):
        # Restrict every source used by CaseMemory, not only the display prompt.
        visible = dict(case or {})
        visible['messages'] = recent_rounds(visible.get('messages', []))
        visible['memory_snapshot'] = {}
        original_init(self, engine, visible, trip_id)

    def union_tools(self, tools):
        return original_tools(self, {k: replace(v, allowed_agents=None) for k, v in (tools or {}).items()})

    def union_answer(role, data):
        if role != 'general':
            return original_answer(role, data)
        # The same deterministic role renderers and sentence deduplication as the
        # default composer; no oracle, expected answer or new reasoning rules.
        texts = [original_answer(kind, data) for kind in ('technical', 'billing', 'escalation')]
        return ' '.join(dict.fromkeys(s for text in texts for s in re.split(r'(?<=[.!?])\s+', text)))

    with metrics.open('w') as observations:
        def record(data):
            observations.write(json.dumps(data) + '\n')
            observations.flush()

        async def observed_run(self, trip_id, text, case=None):
            before = self.client.total_calls
            start = time.perf_counter()
            result = await original_run(self, trip_id, text, case)
            record(dict(kind='analysis', episode=(case or {}).get('owner', '').removeprefix('replay-'),
                        analysis_id=result['id'], agents=result.get('agents', []),
                        model_calls=self.client.total_calls - before,
                        elapsed_ms=(time.perf_counter() - start) * 1000,
                        visible_rider_rounds=len([m for m in (recent_rounds((case or {}).get('messages', [])) if active_configuration.endswith('recent4') else (case or {}).get('messages', [])) if m['role']=='rider'])))
            return result

        def observed_score(case, label, step):
            checks = original_score(case, label, step)
            if 'engine_status' in label:
                run = case['engine_runs'][-1]
                claim_keys = ('active_claims', 'claim_answer_coverage')
                record(dict(kind='context', episode=case.get('owner', '').removeprefix('replay-'),
                            analysis_id=run['id'], step_kind=step['kind'],
                            eligible=bool(label.get('claims')) and not label['requires_clarification'],
                            context_lost=any(checks.get(k) is False for k in claim_keys),
                            stale_claim=checks.get('no_withdrawn_or_unadopted_claim') is False,
                            required_claims=len(label.get('claims', {})),
                            expected_clarification=label['requires_clarification'],
                            clarification_passed=checks.get('clarification') and checks.get('asks_clarification', True)))
            return checks

        with ExitStack() as stack:
            stack.enter_context(patch.object(RideEngine, '_run', observed_run))
            stack.enter_context(patch.object(replay_module, 'score_case', observed_score))
            if args.configuration.endswith('recent4'):
                stack.enter_context(patch.object(CaseMemory, '__init__', window_init))
            if args.configuration.startswith('single'):
                stack.enter_context(patch.object(RideOrchestrator, '_route_decision', lambda self, req: RoutingDecision(AgentType.GENERAL, reason='Ablation: one generalist with the union of read-only tools', confidence=1)))
                stack.enter_context(patch.object(BaseAgent, 'set_shared_tools', union_tools))
                stack.enter_context(patch.object(LocalMessages, 'answer', staticmethod(union_answer)))
            summary = replay_module.replay(args.dataset, args.output / 'replay', limit=args.limit)
    summary['configuration'] = args.configuration
    summary['observations_sha256'] = hashlib.sha256(metrics.read_bytes()).hexdigest()
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k:summary[k] for k in ('configuration','episodes','passed','http_requests','total_seconds')}, indent=2))


if __name__ == '__main__':
    main()
