"""Real backend and archive; AI fixtures explicitly test the provider contract."""
import asyncio
from uuid import UUID

from app.common.models import AIResult
from test_cases import case_client, create_case, upload_passport, confirm_passport
from test_profile import login, input_profile


def generate(client, headers, case):
    response = client.post(f"/api/v1/cases/{case['id']}/letters", headers=headers, json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_template_edit_cache_ownership_and_invalidation(case_client):
    client, headers, owner = case_client
    case = create_case(client, headers)
    url = f"/api/v1/cases/{case['id']}"
    assert client.post(url + '/letters', json={}).status_code == 403
    first = generate(client, headers, case)
    assert first['generation_mode'] == 'template' and not first['from_cache']
    assert any('доход' in fact for fact in first['missing_facts'])
    assert 'booked' not in first['content'] and 'income' not in first['content']
    assert generate(client, headers, case)['from_cache']
    letter_url = '/api/v1/letters/' + first['id']
    edited = client.patch(letter_url, headers=headers, json={'content': 'My own draft. Примечание.'})
    assert edited.status_code == 200 and edited.json()['edited_by_user']
    second = generate(client, headers, case)
    assert second['id'] != first['id'] and not second['edited_by_user']
    download = client.get(letter_url + '/file')
    assert download.content.decode('utf-8') == 'My own draft. Примечание.'
    assert download.headers['cache-control'] == 'no-store'
    assert '.txt' in download.headers['content-disposition']
    assert client.patch(url, headers=headers, json={'trip': {'ends_on': '2027-02-01'}}).status_code == 200
    assert all(x['stale'] for x in client.get(url + '/letters').json()['items'])
    assert client.get(letter_url + '/file').headers['x-letter-stale'] == 'true'
    _, other_headers = login(client, 'sample_letter_other')
    for path in (letter_url + '/file', url + '/letters'):
        assert client.get(path).status_code == 404
    assert client.patch(letter_url, headers=other_headers, json={'content': 'stolen'}).status_code == 404
    assert client.post(url + '/letters', headers=other_headers, json={}).status_code == 404


def test_document_delete_and_profile_change_preserve_edits(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    url = f"/api/v1/cases/{case['id']}"
    doc = confirm_passport(client, headers, upload_passport(client, headers))
    assert client.put(url + '/documents/' + doc['id'], headers=headers).status_code == 200
    letter = generate(client, headers, case)
    client.patch('/api/v1/letters/' + letter['id'], headers=headers, json={'content': 'Keep this'})
    assert client.delete('/api/v1/documents/' + doc['id'], headers=headers).status_code == 204
    saved = client.get(url + '/letters').json()['items'][0]
    assert saved['stale'] and saved['content'] == 'Keep this'
    fresh = generate(client, headers, case)
    profile = input_profile()
    profile['given_name'] = 'ANOTHER'
    assert client.put('/api/v1/profile', headers=headers, json=profile).status_code == 200
    assert all(x['stale'] for x in client.get(url + '/letters').json()['items'])


class OrderingProviderFixture:
    """Contract fixture, NOT a live model response."""
    async def generate(self, task, facts, output_schema):
        assert task == 'cover_letter'
        return AIResult(status='available', output={'paragraph_order': list(reversed(range(len(facts['sentences']))))})


class TimeoutProviderFixture:
    async def generate(self, *args):
        await asyncio.sleep(10)


def test_ai_contract_and_timeout_fallback(case_client):
    client, headers, _ = case_client
    service = client.app.state.letter_service
    service.provider = OrderingProviderFixture()
    letter = generate(client, headers, create_case(client, headers))
    assert letter['generation_mode'] == 'ai'
    service.provider, service.timeout = TimeoutProviderFixture(), .01
    letter = generate(client, headers, create_case(client, headers))
    assert letter['generation_mode'] == 'template'


def test_changed_inputs_during_generation_cannot_publish(case_client):
    client, headers, owner = case_client
    case = create_case(client, headers)
    class RacingProviderFixture:
        async def generate(self, *args):
            # Separate thread/transaction simulates another request during AI.
            await asyncio.to_thread(client.patch, f"/api/v1/cases/{case['id']}", headers=headers,
                                    json={'trip': {'ends_on': '2027-02-01'}})
            return AIResult(status='unavailable', reason='fixture')
    client.app.state.letter_service.provider = RacingProviderFixture()
    response = client.post(f"/api/v1/cases/{case['id']}/letters", headers=headers, json={})
    assert response.status_code == 409
    assert client.get(f"/api/v1/cases/{case['id']}/letters").json()['items'] == []


def test_event_invalidates_same_signature_letter(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    first = generate(client, headers, case)
    response = client.post(f"/api/v1/cases/{case['id']}/events", headers=headers,
                           json={'event_type': 'submitted', 'occurred_at': '2026-10-01T10:00:00Z'})
    assert response.status_code == 201
    second = generate(client, headers, case)
    assert second['input_signature'] == first['input_signature']
    assert second['id'] != first['id'] and not second['from_cache']


def test_model_change_invalidates_cache_and_invalid_ai_cannot_add_facts(case_client):
    client, headers, _ = case_client
    case = create_case(client, headers)
    service = client.app.state.letter_service
    class InvalidProviderFixture:
        model = 'fixture-model-v1'
        async def generate(self, *args):
            return AIResult(status='available', output={'paragraph_order': [0, 0], 'content': 'I have booked flights and earn 100000.'})
    service.provider = InvalidProviderFixture()
    first = generate(client, headers, case)
    assert first['generation_mode'] == 'template' and '100000' not in first['content']
    assert generate(client, headers, case)['from_cache']
    service.provider.model = 'fixture-model-v2'
    second = generate(client, headers, case)
    assert second['id'] != first['id'] and second['input_signature'] != first['input_signature']
    assert client.get(f"/api/v1/cases/{case['id']}/letters").json()['items'][0]['stale']
