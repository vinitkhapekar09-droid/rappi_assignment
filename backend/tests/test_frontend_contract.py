"""Run the real TypeScript fixture/HTTP adapter against the strict Python API.

Requires the frontend's installed dev tools (npm ci); no network is used to
produce the requests. The API test uses the isolated PostgreSQL test database.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.schemas import CaseCreateRequest, EvidenceRequest, RecommendationRequest
from tests.conftest import requires_db

FRONTEND = Path(__file__).resolve().parents[2] / 'frontend'
VITE_NODE = FRONTEND / 'node_modules/.bin/vite-node'


@pytest.fixture()
def frontend_requests(tmp_path):
    if not shutil.which('node') or not VITE_NODE.exists():
        pytest.skip('Run npm ci in frontend/ for the cross-language contract regression')
    script = tmp_path / 'frontend-requests.ts'
    script.write_text(f'''
import {{ DEFAULT_CASE, SAMPLE_EVIDENCE }} from {json.dumps(str(FRONTEND / 'src/api/fixtures.ts'))}
import {{ createHttpApi }} from {json.dumps(str(FRONTEND / 'src/api/httpApi.ts'))}
const requests = []
globalThis.fetch = async (url, options) => {{
  requests.push({{url, method: options.method, body: JSON.parse(options.body)}})
  return new Response('{{}}')
}}
const api = createHttpApi('')
await api.createCase(DEFAULT_CASE)
await api.putEvidence('test-case', SAMPLE_EVIDENCE)
await api.generateRecommendation('test-case')
console.log(JSON.stringify({{ requests, original_evidence: SAMPLE_EVIDENCE }}))
''')
    result = subprocess.run([str(VITE_NODE), str(script)], cwd=FRONTEND,
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


def test_actual_frontend_requests_match_pydantic(frontend_requests):
    case, evidence, investigation = frontend_requests['requests']
    CaseCreateRequest.model_validate(case['body'])
    parsed = EvidenceRequest.model_validate(evidence['body'])
    RecommendationRequest.model_validate(investigation['body'])
    assert parsed.forecast_demand == 1000
    assert parsed.incoming_orders[0].quantity == 100
    assert 'incoming_order_id' not in evidence['body']['incoming_orders'][0]
    assert 'supplier_offer_id' not in evidence['body']['supplier_offers'][0]


@requires_db
def test_actual_frontend_sample_through_api(client, frontend_requests):
    case, evidence, investigation = frontend_requests['requests']
    created = client.post('/api/v1/cases', json=case['body'])
    assert created.status_code == 201, created.text
    cid = created.json()['case_id']
    # The original response-shaped fixture must still be rejected by strict validation.
    invalid = client.put(f'/api/v1/cases/{cid}/evidence',
                         json=frontend_requests['original_evidence'])
    assert invalid.status_code == 422
    assert {tuple(e['loc']) for e in invalid.json()['error']['details']['errors']} == {
        ('body', 'incoming_orders', 0, 'incoming_order_id'),
        ('body', 'supplier_offers', 0, 'supplier_offer_id'),
    }
    loaded = client.put(f'/api/v1/cases/{cid}/evidence', json=evidence['body'])
    assert loaded.status_code == 200, loaded.text
    assert client.get(f'/api/v1/cases/{cid}').json()['evidence'] is not None
    recommendation = client.post(f'/api/v1/cases/{cid}/recommendations',
                                 json=investigation['body'])
    assert recommendation.status_code == 201, recommendation.text
    assert recommendation.json()['recommended_quantity'] == 800
