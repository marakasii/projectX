// Same-origin client for the contract in docs/contracts.md.
export class ApiError extends Error {
  constructor(message, status = 0, code = 'network_error', details = {}) {
    super(message); this.name = 'ApiError'; this.status = status; this.code = code; this.details = details;
  }
}

export class ApiClient {
  constructor(base = '/api/v1') { this.base = base; this.csrfToken = null; }

  async request(path, { method = 'GET', body, signal } = {}) {
    const headers = { Accept: 'application/json' };
    const changing = !['GET', 'HEAD'].includes(method);
    if (changing && this.csrfToken) headers['X-CSRF-Token'] = this.csrfToken;
    if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
    let response;
    try {
      response = await fetch(`${this.base}${path}`, {
        method, credentials: 'include', headers, signal,
        body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body)
      });
    } catch (error) {
      throw new ApiError('Нет соединения с backend.', 0, 'network_error', { cause: String(error) });
    }
    if (response.status === 204) return null;
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) {
      const issue = payload?.error || {};
      throw new ApiError(issue.message || `Ошибка сервера (${response.status})`, response.status, issue.code || 'http_error', issue.details || {});
    }
    if (!payload) throw new ApiError('Сервер вернул ответ не в формате JSON.', response.status, 'invalid_response');
    if (payload.csrf_token) this.csrfToken = payload.csrf_token;
    return payload;
  }

  health() { return this.request('/health'); }
  me() { return this.request('/auth/me'); }
  login(username, password) { return this.request('/auth/login', { method:'POST', body:{ username, password } }); }
  register(username, password) { return this.request('/auth/register', { method:'POST', body:{ username, password } }); }
  logout() { return this.request('/auth/logout', { method:'POST' }); }
  destinations() { return this.request('/destinations'); }
  scenario(id) { return this.request(`/scenarios/${encodeURIComponent(id)}`); }
  profile() { return this.request('/profile'); }
  saveProfile(data) { return this.request('/profile', { method:'PUT', body:data }); }
  compare(data) { return this.request('/comparisons', { method:'POST', body:data }); }
  cases() { return this.request('/cases'); }
  case(id) { return this.request(`/cases/${encodeURIComponent(id)}`); }
  createCase(data) { return this.request('/cases', { method:'POST', body:data }); }
  updateCase(id, data) { return this.request(`/cases/${encodeURIComponent(id)}`, { method:'PATCH', body:data }); }
  deleteCase(id) { return this.request(`/cases/${encodeURIComponent(id)}`, { method:'DELETE' }); }
  attachDocument(caseId, documentId) { return this.request(`/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(documentId)}`, { method:'PUT' }); }
  detachDocument(caseId, documentId) { return this.request(`/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(documentId)}`, { method:'DELETE' }); }
  updateChecklist(caseId, itemId, data) { return this.request(`/cases/${encodeURIComponent(caseId)}/checklist/${encodeURIComponent(itemId)}`, { method:'PATCH', body:data }); }
  documents() { return this.request('/documents'); }
  document(id) { return this.request(`/documents/${encodeURIComponent(id)}`); }
  deleteDocument(id) { return this.request(`/documents/${encodeURIComponent(id)}`, { method:'DELETE' }); }
  uploadDocument(file, documentType) { const body = new FormData(); body.append('file', file); body.append('document_type', documentType); return this.request('/documents', { method:'POST', body }); }
  extractDocument(id) { return this.request(`/documents/${encodeURIComponent(id)}/extract`, { method:'POST' }); }
  updateFields(id, fields, revision) { return this.request(`/documents/${encodeURIComponent(id)}/fields`, { method:'PATCH', body:{ expected_facts_revision: revision, fields: fields.map(({key,value,user_confirmed}) => ({key,value,user_confirmed})) } }); }
  runChecks(caseId) { return this.request(`/cases/${encodeURIComponent(caseId)}/checks`, { method:'POST' }); }
  checks(caseId) { return this.request(`/cases/${encodeURIComponent(caseId)}/checks`); }
  saveRoute(caseId, route) { return this.request(`/cases/${encodeURIComponent(caseId)}/route`, { method:'PUT', body:route }); }
  checkRoute(caseId) { return this.request(`/cases/${encodeURIComponent(caseId)}/route/check`, { method:'POST' }); }
  letters(caseId) { return this.request(`/cases/${encodeURIComponent(caseId)}/letters`); }
  generateLetter(caseId) { return this.request(`/cases/${encodeURIComponent(caseId)}/letters`, { method:'POST', body:{type:'cover_letter'} }); }
  updateLetter(letterId, content) { return this.request(`/letters/${encodeURIComponent(letterId)}`, { method:'PATCH', body:{ content } }); }
  events(caseId) { return this.request(`/cases/${encodeURIComponent(caseId)}/events`); }
  addEvent(caseId, data) { return this.request(`/cases/${encodeURIComponent(caseId)}/events`, { method:'POST', body:data }); }
  notifications(caseId) { return this.request(`/cases/${encodeURIComponent(caseId)}/notifications`); }
}
