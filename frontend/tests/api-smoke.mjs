// Real same-origin API client. Run only against an isolated SAMPLE server.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {ApiClient, ApiError} from '../api.js';

const origin = process.env.VISAPILOT_TEST_ORIGIN;
assert.ok(origin, 'Set VISAPILOT_TEST_ORIGIN to an isolated test server');
const nativeFetch = globalThis.fetch;
let cookie = '';
globalThis.fetch = async (path, options = {}) => {
  const headers = new Headers(options.headers);
  headers.set('Origin', origin);
  if (cookie) headers.set('Cookie', cookie);
  const response = await nativeFetch(new URL(path, origin), {...options, headers});
  const setCookie = response.headers.get('set-cookie');
  if (setCookie) cookie = setCookie.split(';')[0];
  return response;
};
const api = new ApiClient();
const username = 'sample_frontend_' + Date.now();
await api.register(username, 'SAMPLE-Password-123');
await api.login(username, 'SAMPLE-Password-123');
await api.saveProfile({given_name:'ALEXEI', surname:'SAMPLEOV', birth_date:'1996-03-14',
  citizenship:'RU', residence_country:'RU', has_other_citizenship:false, has_foreign_residence_permit:false,
  passport_kind:'biometric', passport_issued_on:'2022-04-15', passport_expires_on:'2032-04-15'});
const item = await api.createCase({scenario_id:'FR_TOURISM', trip:{purpose:'tourism', starts_on:'2026-12-01', ends_on:'2026-12-10'}, answers:{schengen_prior_stays:[]}});
const bytes = await readFile(new URL('../../demo/documents/good_passport.pdf', import.meta.url));
const file = new Blob([bytes], {type:'application/pdf'});
const uploaded = await api.uploadDocument(file, 'passport');
const extracted = await api.extractDocument(uploaded.id);
// API client must strip server-only field metadata and send expected revision.
const confirmed = await api.updateFields(uploaded.id, extracted.fields.map(field => ({...field, user_confirmed:true})), extracted.facts_revision);
assert.ok(confirmed.fields.every(field => field.user_confirmed));
await api.attachDocument(item.id, uploaded.id);
assert.equal((await api.runChecks(item.id)).stale, false);
const letter = await api.generateLetter(item.id);
assert.equal(letter.generation_mode, 'template');
assert.equal((await api.generateLetter(item.id)).from_cache, true);
await api.updateLetter(letter.id, 'SAMPLE manual edit. Примечание.');
assert.equal(await (await fetch(`/api/v1/letters/${letter.id}/file`)).text(), 'SAMPLE manual edit. Примечание.');
await api.addEvent(item.id, {event_type:'passport_returned', occurred_at:'2026-10-01T10:00:00Z', note:'SAMPLE'});
assert.equal((await api.case(item.id)).process.outcome, 'unknown');
assert.equal((await api.letters(item.id)).items[0].stale, true);
await assert.rejects(api.checkRoute(item.id), error => error instanceof ApiError && error.status === 503);
await api.deleteCase(item.id);
await api.deleteDocument(uploaded.id);
await api.logout();
await assert.rejects(api.cases(), error => error.status === 401);
console.log('Frontend ApiClient + real HTTP passed: login/profile/case/archive/revision/confirmation/checks/letter/edit/file/event/logout.');
