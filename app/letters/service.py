"""Grounded drafts: AI orders approved sentences; it cannot add factual claims."""
import asyncio
import json
import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

from starlette.concurrency import run_in_threadpool

from app.auth.ownership import ResourceKind, require_owned
from app.common.errors import AppError
from app.common.models import InputSignature, Letter


def read_letter(row, signature=None, *, cached=False):
    stored = InputSignature.model_validate_json(row['input_signature_json'])
    return Letter(
        id=row['id'], case_id=row['case_id'], content=row['content'],
        missing_facts=json.loads(row['missing_facts_json']), generation_mode=row['generation_mode'],
        from_cache=cached, input_signature=stored,
        stale=bool(row['stale']) or (signature is not None and stored != signature),
        edited_by_user=bool(row['edited_by_user']), created_at=row['created_at'],
    )


def grounded_sentences(snapshot):
    profile, trip = snapshot.profile, snapshot.case.trip
    sentences, missing = [], []
    name = ' '.join(value for value in (profile.given_name, profile.surname) if value)
    passport_names = []
    for document in snapshot.documents:
        if document.document_type == 'passport':
            values = {field.key: field.value for field in document.fields}
            if values.get('given_name') and values.get('surname'):
                passport_names.append(values['given_name'] + ' ' + values['surname'])
    if name and any(value.casefold() != name.casefold() for value in passport_names):
        missing.append('Имя в профиле расходится с подтверждённым паспортом; устраните расхождение.')
        name = ''
    if name and profile.given_name and profile.surname:
        sentences.append(f'My name is {name}.')
    else:
        missing.append('Уточните имя и фамилию латиницей.')
    if trip.purpose == 'tourism':
        sentences.append(f'I plan to visit {trip.destination_country} for tourism.')
    else:
        missing.append('Уточните туристическую цель поездки.')
    if trip.starts_on and trip.ends_on:
        sentences.append(f'My intended travel dates are {trip.starts_on} to {trip.ends_on}.')
    else:
        missing.append('Укажите даты начала и окончания поездки.')
    if profile.citizenship:
        sentences.append(f'My citizenship is {profile.citizenship}.')
    if profile.residence_country:
        sentences.append(f'My country of residence is {profile.residence_country}.')
    # The current schema has no proof of bookings, income or reasons to return.
    missing.extend(['Добавьте подтверждённые сведения о проживании и бронировании.',
                    'Добавьте сведения о доходе и финансировании поездки.',
                    'Уточните занятость и обстоятельства возвращения.'])
    return sentences, missing


class LetterService:
    def __init__(self, database, cases, provider, timeout=60):
        self.database, self.cases, self.provider = database, cases, provider
        self.timeout = timeout

    def signature(self, state):
        identity = json.dumps({'template': 'cover-letter-1',
                               'model': getattr(self.provider, 'model', None),
                               'enabled': getattr(self.provider, 'enabled', None)}, sort_keys=True)
        version = hashlib.sha256(identity.encode()).hexdigest()[:16]
        return state['signature'].model_copy(update={'algorithm_version': f'letters-1-{version}'})

    def list(self, owner, case_id):
        state = self.cases._prepare(owner, case_id)
        with self.database.transaction() as connection:
            self.cases._assert_unchanged(connection, owner, case_id, state)
            rows = connection.execute('SELECT * FROM letters WHERE case_id = ? ORDER BY created_at, rowid', (str(case_id),)).fetchall()
            items = [read_letter(row, self.signature(state)) for row in rows]
            for item in items:
                if item.stale:
                    connection.execute('UPDATE letters SET stale = 1 WHERE id = ?', (str(item.id),))
            return items

    def get(self, owner, letter_id):
        with self.database.connection() as connection:
            row = require_owned(connection, owner, ResourceKind.LETTER, letter_id)
            case_id = UUID(row['case_id'])
        return next(item for item in self.list(owner, case_id) if item.id == letter_id)

    def edit(self, owner, letter_id, content):
        with self.database.transaction() as connection:
            require_owned(connection, owner, ResourceKind.LETTER, letter_id)
            connection.execute('UPDATE letters SET content = ?, edited_by_user = 1, from_cache = 0, updated_at = ? WHERE id = ?',
                               (content, datetime.now(timezone.utc).isoformat(), str(letter_id)))
        return self.get(owner, letter_id)

    async def generate(self, owner, case_id):
        state = await run_in_threadpool(self.cases._prepare, owner, case_id)
        cached = await run_in_threadpool(self.list, owner, case_id)
        for item in reversed(cached):
            if not item.stale and not item.edited_by_user and item.input_signature == self.signature(state):
                found = await run_in_threadpool(self._cached, owner, case_id, state, item.id)
                if found is not None:
                    return found
        snapshot = self.cases._snapshot(owner, state)
        sentences, missing = grounded_sentences(snapshot)
        order = list(range(len(sentences)))
        mode = 'template'
        # Closed vocabulary prevents hallucinated bookings, income or promises.
        schema = {'type': 'object', 'properties': {'paragraph_order': {
            'type': 'array', 'items': {'type': 'integer', 'enum': order},
            'minItems': len(order), 'maxItems': len(order)}},
            'required': ['paragraph_order'], 'additionalProperties': False}
        if sentences:
            try:
                result = await asyncio.wait_for(self.provider.generate(
                    'cover_letter',
                    {'sentences': sentences}, schema), timeout=self.timeout)
                proposed = result.output.get('paragraph_order') if isinstance(result.output, dict) else None
                if (result.status == 'available' and isinstance(proposed, list)
                        and all(type(i) is int for i in proposed) and sorted(proposed) == order):
                    order, mode = proposed, 'ai'
            except (TimeoutError, OSError, ValueError):
                pass
        content = 'Dear Visa Officer,\n\n' + '\n\n'.join(sentences[i] for i in order) + '\n\nSincerely,\n[Signature]\n'
        if mode == 'template':
            missing.insert(0, 'Шаблон: AI недоступен или не вернул допустимый результат. Проверьте и дополните черновик.')
        return await run_in_threadpool(self._save, owner, case_id, state, content, missing, mode)

    def _cached(self, owner, case_id, state, letter_id):
        with self.database.transaction() as connection:
            self.cases._assert_unchanged(connection, owner, case_id, state)
            row = require_owned(connection, owner, ResourceKind.LETTER, letter_id)
            if not row['stale'] and not row['edited_by_user']:
                return read_letter(row, self.signature(state), cached=True)
        return None

    def _save(self, owner, case_id, state, content, missing, mode):
        now, letter_id = datetime.now(timezone.utc).isoformat(), str(uuid4())
        with self.database.transaction() as connection:
            self.cases._assert_unchanged(connection, owner, case_id, state)
            connection.execute(
                'INSERT INTO letters VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, ?, ?)',
                (letter_id, str(case_id), 'cover_letter', 'en', content, json.dumps(missing, ensure_ascii=False),
                 mode, self.signature(state).model_dump_json(), now, now))
            return read_letter(connection.execute('SELECT * FROM letters WHERE id = ?', (letter_id,)).fetchone())
