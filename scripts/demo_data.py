"""Create/reset an isolated SAMPLE cabinet. Never reuse or reset an existing data directory."""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MARKER = '.visapilot-sample-data.json'
PASSWORD = 'SAMPLE-Password-123'
PROFILE = {
    'given_name': 'ALEXEI', 'surname': 'SAMPLEOV', 'birth_date': '1996-03-14',
    'citizenship': 'RU', 'residence_country': 'RU', 'has_other_citizenship': False,
    'has_foreign_residence_permit': False, 'passport_kind': 'biometric',
    'passport_issued_on': '2022-04-15', 'passport_expires_on': '2032-04-15',
    'employment_status': 'employed', 'existing_permissions': [],
}
TRIP = {'purpose': 'tourism', 'starts_on': '2026-12-01', 'ends_on': '2026-12-10',
        'application_country': 'RU', 'funding_type': 'self', 'accommodation_type': 'hotel'}


def require(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f'SAMPLE operation failed: HTTP {response.status_code}')
    return response.json() if status != 204 else None


def seed(directory):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.common.config import Settings

    # Exclusive creation protects every existing database, even another seed.
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / MARKER).write_text(json.dumps({'kind': 'VisaPilot SAMPLE', 'version': 1}), encoding='utf-8')
    settings = Settings(_env_file=None, data_dir=directory, allowed_origins=['http://testserver'], ai_enabled=False)
    with TestClient(create_app(settings), headers={'Origin': 'http://testserver'}) as client:
        cases, documents = {}, {}
        for username in ('sample_alice', 'sample_bob'):
            require(client.post('/api/v1/auth/register', json={'username': username, 'password': PASSWORD}), 201)
            session = require(client.post('/api/v1/auth/login', json={'username': username, 'password': PASSWORD}))
            headers = {'X-CSRF-Token': session['csrf_token']}
            require(client.put('/api/v1/profile', headers=headers, json=PROFILE))
            if username == 'sample_bob':
                bob = require(client.post('/api/v1/cases', headers=headers, json={
                    'scenario_id': 'JP_TOURISM', 'trip': TRIP}), 201)
                cases['bob'] = bob['id']
                continue
            for group in ('good', 'bad', 'corrected'):
                case = require(client.post('/api/v1/cases', headers=headers, json={
                    'scenario_id': 'FR_TOURISM', 'trip': TRIP, 'answers': {'schengen_prior_stays': []}}), 201)
                cases[group] = case['id']
                url = '/api/v1/cases/' + case['id']
                for kind in ('passport', 'bank_statement', 'insurance'):
                    # Read original SAMPLE bytes. Nothing imports manifest facts.
                    content = (ROOT / 'demo/documents' / f'{group}_{kind}.pdf').read_bytes()
                    document = require(client.post('/api/v1/documents', headers=headers,
                        files={'file': ('SAMPLE-renamed.pdf', content, 'application/pdf')},
                        data={'document_type': kind}), 201)
                    doc_url = '/api/v1/documents/' + document['id']
                    document = require(client.post(doc_url + '/extract', headers=headers))
                    if not document['fields'] or any(field['value'] is None for field in document['fields']):
                        raise RuntimeError('SAMPLE PDF fields were not extracted; no synthetic pass is substituted')
                    document = require(client.patch(doc_url + '/fields', headers=headers, json={
                        'expected_facts_revision': document['facts_revision'],
                        'fields': [{'key': field['key'], 'value': field['value'], 'user_confirmed': True}
                                   for field in document['fields']]}))
                    require(client.put(url + '/documents/' + document['id'], headers=headers))
                    documents[f'{group}_{kind}'] = document['id']
                result = require(client.post(url + '/checks', headers=headers))
                if result['stale']:
                    raise RuntimeError('SAMPLE check did not publish a current result')
                require(client.post(url + '/letters', headers=headers, json={}), 201)
            later = require(client.post('/api/v1/cases', headers=headers, json={
                'scenario_id': 'FR_TOURISM', 'trip': {**TRIP, 'starts_on': '2032-04-01', 'ends_on': '2032-04-10'},
                'answers': {'schengen_prior_stays': []}}), 201)
            cases['later'] = later['id']
            require(client.put('/api/v1/cases/' + later['id'] + '/documents/' + documents['good_passport'], headers=headers))
            require(client.post('/api/v1/cases/' + later['id'] + '/checks', headers=headers))
        # Only IDs and synthetic metadata; no sessions, hashes or tokens in manifest.
        (directory / 'sample-manifest.json').write_text(json.dumps({'cases': cases, 'documents': documents}, indent=2), encoding='utf-8')
    print(f'SAMPLE cabinet created at {directory}')
    print(f'Users: sample_alice / sample_bob; password: {PASSWORD}')


def reset(directory, confirmation):
    if confirmation != 'DELETE_DEMO_DATA':
        raise ValueError('Reset requires --confirm DELETE_DEMO_DATA')
    if directory in (ROOT, ROOT / 'local_data') or directory == Path(directory.anchor):
        raise ValueError('Refusing to reset the project or primary cabinet')
    marker = directory / MARKER
    if marker.is_symlink() or json.loads(marker.read_text(encoding='utf-8')) != {'kind': 'VisaPilot SAMPLE', 'version': 1}:
        raise ValueError('Not a directory created by this SAMPLE tool')
    shutil.rmtree(directory)
    print(f'SAMPLE data removed: {directory}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['seed', 'reset'])
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--confirm')
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    try:
        if args.action == 'seed':
            seed(directory)
        else:
            reset(directory, args.confirm)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
