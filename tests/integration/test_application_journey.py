"""Real SAMPLE PDF content and real document checker, never a positive fixture."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.common.config import Settings
from app.main import create_app
from scripts.demo_data import PROFILE, PASSWORD, seed, reset


def test_sample_sets_real_checks_shared_passport_and_input_changes(tmp_path):
    directory = tmp_path / 'sample'
    seed(directory)
    manifest = json.loads((directory / 'sample-manifest.json').read_text())
    with pytest.raises(FileExistsError):
        seed(directory)
    config = Settings(_env_file=None, data_dir=directory, allowed_origins=['http://testserver'])
    with TestClient(create_app(config), headers={'Origin': 'http://testserver'}) as client:
        login = client.post('/api/v1/auth/login', json={'username': 'sample_alice', 'password': PASSWORD}).json()
        headers = {'X-CSRF-Token': login['csrf_token']}
        def case_url(group):
            return '/api/v1/cases/' + manifest['cases'][group]
        def codes(group):
            response = client.get(case_url(group) + '/checks')
            assert response.status_code == 200, response.text
            return {finding['code'] for finding in response.json()['findings']}
        bad, corrected = codes('bad'), codes('corrected')
        assert {'passport_expired', 'holder_mismatch', 'insurance_dates_not_covered'} <= bad
        assert not {'passport_expired', 'holder_mismatch', 'insurance_dates_not_covered'} & corrected
        assert 'passport_validity' not in codes('good')
        assert 'passport_validity' in codes('later')
        assert all(not client.get(case_url(group)).json()['readiness']['ready'] for group in ('good', 'bad', 'corrected'))
        # New facts invalidate letters and recalculate actual checks in both linked cases.
        doc_url = '/api/v1/documents/' + manifest['documents']['good_passport']
        document = client.get(doc_url).json()
        changed = client.patch(doc_url + '/fields', headers=headers, json={
            'expected_facts_revision': document['facts_revision'],
            'fields': [{'key': 'expires_on', 'value': '2026-01-01', 'user_confirmed': True}]})
        assert changed.status_code == 200, changed.text
        assert 'passport_expired' in codes('good') and 'passport_expired' in codes('later')
        assert client.get(case_url('good') + '/letters').json()['items'][0]['stale']
        assert client.delete(doc_url, headers=headers).status_code == 204
        assert manifest['documents']['good_passport'] not in client.get(case_url('later')).json()['attached_document_ids']
        assert 'required_document_missing' in codes('good')
        changed_profile = dict(PROFILE, given_name='DIFFERENT')
        assert client.put('/api/v1/profile', headers=headers, json=changed_profile).status_code == 200
        assert client.get(case_url('corrected') + '/checks').json()['stale']
        assert client.get(case_url('corrected') + '/letters').json()['items'][0]['stale']
        assert client.post(case_url('corrected') + '/checks', headers=headers).status_code == 200
        assert 'holder_mismatch' in codes('corrected')
        assert client.post('/api/v1/auth/logout', headers=headers).status_code == 204
        assert client.get(case_url('good') + '/letters').status_code == 401
    # Only this temporary SAMPLE directory is removed, with the explicit flag.
    with pytest.raises(ValueError):
        reset(directory, None)
    assert directory.exists()
    reset(directory, 'DELETE_DEMO_DATA')
    assert not directory.exists()
    unmarked = tmp_path / 'personal-data'
    unmarked.mkdir()
    with pytest.raises(FileNotFoundError):
        reset(unmarked, 'DELETE_DEMO_DATA')
    assert unmarked.exists()
