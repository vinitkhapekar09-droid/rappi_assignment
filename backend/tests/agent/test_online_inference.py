"""Mocked online transport, provenance and fallback tests; no live keys/network."""
import json

import pytest
from requests import HTTPError, Timeout

from app.agent.config import AgentConfig
from app.agent.llm.openai_compatible import OpenAICompatibleLLMClient
from tests.agent.support import build_harness


class Response:
    def __init__(self, content, *, failure=None, model='test-model-resolved', finish_reason='stop'):
        self.content, self.failure, self.model = content, failure, model
        self.finish_reason = finish_reason

    def raise_for_status(self):
        if self.failure:
            raise self.failure

    def json(self):
        return {'model': self.model, 'choices': [{
            'finish_reason': self.finish_reason, 'message': {'content': self.content},
        }]}


class Session:
    def __init__(self, response=None, failure=None):
        self.response, self.failure = response, failure
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({'url': url, **kwargs})
        if self.failure:
            raise self.failure
        return self.response


def workflow(session):
    config = AgentConfig(llm_provider='openai_compatible', llm_model='test-model',
                         llm_api_key='test-only', llm_base_url='https://provider.example/v1')
    client = OpenAICompatibleLLMClient(model=config.llm_model, api_key=config.llm_api_key,
                                      base_url=config.llm_base_url, session=session, timeout=2)
    harness = build_harness(llm=client, config=config)
    return harness.workflow.recommend(harness.case.case_id)


def test_online_request_includes_actual_evidence_and_constraints():
    content = json.dumps({'summary': 'Requirement is supported.',
                          'key_evidence': ['Forecast demand is 1000 units.'],
                          'trade_offs': ['Preserve budget headroom.'],
                          'caveats': ['Buyer approval is required.'], 'confidence': 'high'})
    session = Session(Response(content))
    outcome = workflow(session)
    assert outcome.inference.mode == 'online'
    assert outcome.inference.online_succeeded is True
    assert outcome.inference.provider == 'provider.example'
    assert outcome.inference.model == 'test-model-resolved'
    assert outcome.recommendation.generated_by == 'agent'
    assert outcome.recommendation.recommended_quantity == 800
    assert outcome.recommendation.total_cost == 10000
    call = session.calls[0]
    assert call['url'] == 'https://provider.example/v1/chat/completions'
    assert call['timeout'] == 2
    assert call['json']['response_format'] == {'type': 'json_object'}
    assert 'do not round, derive, use percentages, or add thousands separators' in call['json']['messages'][0]['content']
    prompt = call['json']['messages'][1]['content']
    facts = json.loads(prompt[prompt.index('{'):])
    assert facts['case_evidence']['usable_inventory'] == 200
    assert facts['case_evidence']['forecast_demand'] == 1000
    assert facts['case_evidence']['incoming_orders'][0]['status'] == 'confirmed'
    assert facts['case_evidence']['incoming_orders'][0]['quantity'] == 100
    assert facts['case_evidence']['supplier_offers'][0]['lead_time_days'] == 3
    assert facts['case']['volume_unit'] == 'liter'
    assert len(facts['constraint_checks']) == 6
    assert all(c['status'] == 'pass' for c in facts['constraint_checks'])
    assert outcome.explanation.trade_offs == ['Preserve budget headroom.']


@pytest.mark.parametrize('session', [
    Session(failure=Timeout('DO_NOT_LEAK')),
    Session(Response('{}', failure=HTTPError('DO_NOT_LEAK'))),
    Session(Response('not JSON')),
    Session(Response('{"summary":123}')),
    Session(Response('{"summary":"ok","quantity":999}')),
    Session(Response('{}')),
    Session(Response(None)),
    Session(Response('{"summary":"partial"}', finish_reason='length')),
    Session(Response('{"summary":"Order 99999 units."}')),
])
def test_provider_or_validation_failure_is_explicit_fallback(session):
    outcome = workflow(session)
    assert outcome.inference.mode == 'provider_fallback'
    assert outcome.inference.online_succeeded is False
    assert outcome.inference.provider == 'offline'
    assert outcome.inference.requested_provider == 'provider.example'
    assert outcome.inference.requested_model == 'test-model'
    assert outcome.inference.fallback_reason
    assert outcome.recommendation.generated_by == 'offline'
    assert outcome.recommendation.recommended_quantity == 800
    assert outcome.recommendation.total_cost == 10000
    assert outcome.status == 'awaiting_approval'
    assert 'DO_NOT_LEAK' not in outcome.model_dump_json()
    assert '99999' not in outcome.recommendation.rationale


def test_offline_is_explicitly_labelled():
    harness = build_harness()
    outcome = harness.workflow.recommend(harness.case.case_id)
    assert outcome.inference.mode == 'offline_fallback'
    assert outcome.inference.online_succeeded is False
    assert outcome.inference.model is None
    assert outcome.inference.fallback_reason


def test_provider_refusal_is_labelled_fallback():
    class RefusalResponse(Response):
        def json(self):
            body = super().json()
            body['choices'][0]['message']['refusal'] = 'Refused'
            return body
    outcome = workflow(Session(RefusalResponse('{"summary":"Cannot explain."}')))
    assert outcome.inference.mode == 'provider_fallback'
    assert outcome.inference.online_succeeded is False
    assert outcome.recommendation.generated_by == 'offline'
