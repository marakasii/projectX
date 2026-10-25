import { ApiClient, ApiError } from './api.js';
import { destinations as sampleDestinations, entryLabels, sampleProfile, sampleCase, sampleDocuments, sampleEvents } from './data.js';

const api = new ApiClient();
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const icon = (name, className = '') => `<svg class="${className}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
const path = () => decodeURI((location.hash.slice(1) || '/').split('?')[0]);
const query = () => new URLSearchParams((location.hash.split('?')[1] || ''));
const entryText = (mode) => entryLabels[mode] || entryLabels.needs_verification;
const dateText = (value) => value ? new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric'}).format(new Date(`${value.slice(0,10)}T12:00:00`)) : 'Не указана';
const clone = value => JSON.parse(JSON.stringify(value));

const state = {
  mode: 'loading', catalogFallback: false, user: null, profile: null, destinations: [], cases: [], documents: [],
  case: null, document: null, scenario: null, events: [], notifications: [], letters: [],
  search: '', continent: 'all', schengen: 'all', entry: 'all', visible: 6, comparison: null, comparisonSelected: [], comparisonInputs: { starts_on:'2027-06-10', ends_on:'2027-06-20', departure_city:'Москва', country_a:'FR_TOURISM', country_b:'JP_TOURISM', country_c:'' }, routeResults: {},
  letter: null, selectedCountry: null, authTab: 'login', mobileMenu: false,
  demo: { cases: [clone(sampleCase)], documents: clone(sampleDocuments), events: clone(sampleEvents), letters: [] }
};

function countryId(country) { return country.id || country.scenario_id || `${country.destination_country}_TOURISM`; }
function countryName(country) { return country.name || country.destination_name || country.destination_country || 'Направление'; }
function countryIcon(country) { return country.icon || sampleDestinations.find(item => item.id === countryId(country))?.icon || 'globe'; }
function countryContinent(country) { return country.continent || sampleDestinations.find(item => item.id === countryId(country))?.continent || 'Континент не указан'; }
function countrySchengen(country) { return country.schengen ?? sampleDestinations.find(item => item.id === countryId(country))?.schengen ?? null; }
function countryEntry(country) { return country.entry_mode || country.sampleEntry || 'needs_verification'; }
function safeHttpUrl(value) { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; } }
function caseCountry(item) { return state.destinations.find(country => countryId(country) === item.scenario_id) || sampleDestinations.find(country => country.id === item.scenario_id); }
function listItems(value) { return Array.isArray(value) ? value : Array.isArray(value?.items) ? value.items : []; }
function badge(text, kind = '') { return `<span class="badge ${kind}">${esc(text)}</span>`; }
function notice(text, kind = 'info') { return `<div class="inline-notice ${kind}">${icon('info')}<span>${text}</span></div>`; }
function empty(title, description, action = '') { return `<div class="empty-panel">${icon('file')}<h3>${title}</h3><p>${description}</p>${action}</div>`; }
function progress(value, label) { const number = Number.isFinite(value) ? Math.max(0,Math.min(100,value)) : null; return `<div class="progress-stat"><div><span>${label}</span><strong>${number === null ? 'Нужна проверка' : `${number}%`}</strong></div><div class="progress-track"><i style="width:${number ?? 0}%"></i></div></div>`; }
function updateDemoReadiness(item) {
  const required = listItems(item.checklist).filter(entry => entry.mandatory && entry.readiness_contributing !== false && entry.kind !== 'process_action');
  const applicable = required.filter(entry => entry.applicability === 'yes');
  const satisfied = applicable.filter(entry => entry.state === 'satisfied').length;
  const unknown = required.filter(entry => entry.applicability === 'unknown' || (entry.applicability === 'yes' && ['needs_verification','received'].includes(entry.state))).length;
  const blocking = applicable.filter(entry => ['missing','issues'].includes(entry.state)).length;
  const next = required.find(entry => entry.applicability === 'unknown' || (entry.applicability === 'yes' && entry.state !== 'satisfied'));
  const percent = applicable.length ? Math.floor(100 * satisfied / applicable.length) : null;
  item.readiness = {percent,required_total:applicable.length,satisfied_required:satisfied,unknown_required_conditions:unknown,blocking_count:blocking,ready:percent===100 && unknown===0 && blocking===0,reasons:next?[`Проверьте: ${next.title || next.requirement_id || 'обязательное требование'}`]:[]};
}
function fmtError(error) { return error instanceof ApiError ? error.message : 'Не удалось выполнить действие. Попробуйте ещё раз.'; }
let toastTimer;
function toast(message, kind = 'info') { const element = $('toast'); element.textContent = message; element.className = `toast visible ${kind}`; clearTimeout(toastTimer); toastTimer = setTimeout(() => element.classList.remove('visible'), 5000); }

async function init() {
  try {
    await api.health();
    state.mode = 'api';
    const [destinations, me] = await Promise.allSettled([api.destinations(), api.me()]);
    state.destinations = destinations.status === 'fulfilled' ? listItems(destinations.value) : [];
    if (me.status === 'fulfilled') { state.user = me.value.user || me.value; api.csrfToken = me.value.csrf_token || api.csrfToken; }
    if (!state.destinations.length) { state.catalogFallback = true; state.destinations = sampleDestinations.map(item => ({...item, entry_mode:'needs_verification', data_quality:'unavailable'})); }
  } catch {
    state.mode = 'demo';
    state.user = { id:'demo-user', username:'demo' };
    state.profile = clone(sampleProfile);
    state.destinations = clone(sampleDestinations);
    state.demo.cases.forEach(updateDemoReadiness);
    state.cases = state.demo.cases;
    state.documents = state.demo.documents;
  }
  await navigate();
}

function header() {
  const active = path();
  const nav = [['/','Главная'],['/destinations','Направления'],['/compare','Сравнение'],['/cases','Мои дела'],['/archive','Архив'],['/about','О сервисе']];
  const isActive = href => active === href || (href !== '/' && active.startsWith(href + '/'));
  return `<header class="site-header"><div class="container nav-inner"><a class="brand" href="#/" aria-label="VisaMate — главная">Visa<span>Mate</span></a><nav class="main-nav ${state.mobileMenu?'open':''}" id="main-nav" aria-label="Главная навигация">${nav.map(([href,label]) => `<a href="#${href}" class="${isActive(href)?'active':''}">${label}</a>`).join('')}</nav><div class="nav-right">${state.mode === 'demo' ? badge('ДЕМО', 'demo') : ''}${state.user ? `<a href="#/profile" class="account-link" aria-label="Профиль">${icon('file')} Профиль</a>${state.mode==='api'?'<button class="logout-button" type="button" data-action="logout">Выйти</button>':''}` : `<a href="#/auth" class="button button-primary nav-cta">Войти</a>`}<button class="menu-toggle icon-button" data-action="menu" type="button" aria-expanded="${state.mobileMenu}" aria-controls="main-nav" aria-label="Открыть меню"><span></span><span></span><span></span></button></div></div></header>`;
}

function footer() { return `<footer class="site-footer"><div class="container footer-inner"><a class="brand" href="#/">Visa<span>Mate</span></a><p>Подготовка к поездке без догадок.</p><small>Внутренний прототип. Для реальной поездки проверяйте условия в официальных источниках. Демо использует только вымышленные данные.</small></div></footer>`; }

function workspace(title, subtitle, content, current = '', actions = '') {
  return `<div class="workspace-bg"><div class="container workspace-layout"><div class="workspace-main"><div class="page-head"><div><p class="eyebrow">VISAMATE / КАБИНЕТ</p><h1>${title}</h1><p>${subtitle}</p></div>${actions}</div>${content}</div></div></div>`;
}

function caseTabs(caseId, active) {
  const base = `/cases/${encodeURIComponent(caseId)}`;
  return `<nav class="case-tabs" aria-label="Разделы дела">${[['','Обзор'],['/checklist','Чек-лист'],['/route','Маршрут'],['/letter','Письмо'],['/timeline','Хронология']].map(([suffix,label])=>`<a href="#${base}${suffix}" class="${active===suffix?'active':''}">${label}</a>`).join('')}</nav>`;
}

function sampleWarning() {
  if (state.mode === 'demo') return notice('Демонстрационный режим: данные и результаты вымышлены. Загрузка и изменения действуют только до обновления страницы.', 'warning');
  if (state.catalogFallback) return notice('Каталог backend недоступен. Показаны образцы направлений без подтверждённых правил въезда.', 'warning');
  return '';
}

function home() {
  return `<main><section class="hero"><div class="container hero-layout"><div class="hero-copy"><p class="eyebrow">ДЛЯ ВЗРОСЛОГО ТУРИСТА С ПАСПОРТОМ РФ</p><h1>Подготовьтесь к визе и поездке <span>без догадок</span></h1><p class="hero-description">Выберите направление, соберите документы и следите за следующим шагом. Требования объясняются с источниками, а неизвестные условия остаются видимыми.</p><div class="hero-actions"><a class="button button-primary" href="#/destinations">Выбрать направление ${icon('arrow')}</a><a class="button button-outline" href="#/about">Как это работает</a></div></div></div><div class="container"><form class="route-card" data-form="quick-route"><div class="route-fields"><label class="field"><span>Город отправления</span><span class="field-control">${icon('pin')}<input name="departure_city" value="Москва" required></span></label><label class="field"><span>Страна назначения</span><span class="field-control">${icon('globe')}<select name="scenario_id">${state.destinations.map(item=>`<option value="${esc(countryId(item))}">${esc(countryName(item))}</option>`).join('')}</select></span></label><label class="field"><span>Начало поездки</span><span class="field-control">${icon('calendar')}<input name="starts_on" type="date" value="2027-06-10" required></span></label><label class="field"><span>Окончание поездки</span><span class="field-control">${icon('calendar')}<input name="ends_on" type="date" value="2027-06-20" required></span></label><button class="button button-primary route-submit" type="submit">Продолжить ${icon('arrow')}</button></div><p class="route-caption">${icon('info')} Правила въезда и пересадок уточняются для профиля и маршрута. Наличие рейсов не проверяется.</p></form></div></section><section class="destinations home-destinations"><div class="container"><p class="eyebrow">КУДА ОТПРАВИМСЯ?</p><h2>Мир начинается с плана</h2><p class="section-subtitle">${state.destinations.length} направлений. Найдите свой сценарий подготовки.</p>${sampleWarning()}${notice('Фильтр Шенгена показывает географию направления. Визовое требование определяется по вашему профилю и актуальному источнику.')}${destinationPanel()}<p class="transit-note">${icon('route')} Правила пересадки зависят от маршрута и должны проверяться отдельно.</p></div></section><section class="how-section"><div class="container"><p class="eyebrow">ОТ ПЕРВОГО ВОПРОСА ДО ПОДАЧИ</p><h2>Как работает VisaMate</h2><div class="steps">${[['01','pin','Выберите направление','Сравните требования и неизвестные условия.'],['02','file','Соберите документы','Используйте общий архив в нескольких делах.'],['03','landmark','Пройдите официальные шаги','Подача остаётся на стороне уполномоченного органа.'],['04','calendar','Ведите историю','Добавляйте события вручную и следите за следующим шагом.']].map(([n,i,t,d])=>`<article class="step-card"><span class="step-number">${n}</span>${icon(i)}<h3>${t}</h3><p>${d}</p></article>`).join('')}</div></div></section></main>`;
}

function countryCard(country) {
  const id = countryId(country), mode = countryEntry(country);
  return `<article class="country-card"><div class="country-card-top"><span class="country-icon">${icon(countryIcon(country))}</span><div><h3>${esc(countryName(country))}</h3><span class="country-type">${esc(entryText(mode))}${state.mode==='demo'?' · демо':''}</span></div></div><p class="country-description">${esc(country.hint || 'Посмотрите условия и создайте дело для персонального сценария.')}</p><a href="#/destinations/${encodeURIComponent(id)}" class="country-details">Подробнее ${icon('up-right')}</a></article>`;
}

function filteredDestinations() {
  const term = state.search.toLocaleLowerCase('ru').replaceAll('ё', 'е');
  return state.destinations.filter(country => {
    const name = `${countryName(country)} ${country.alias || ''}`.toLocaleLowerCase('ru').replaceAll('ё', 'е');
    return name.includes(term)
      && (state.continent === 'all' || countryContinent(country) === state.continent)
      && (state.schengen === 'all' || countrySchengen(country) === (state.schengen === 'yes'))
      && (state.entry === 'all' || countryEntry(country) === state.entry);
  });
}

function destinationPanel() {
  const found = filteredDestinations();
  const continents = [...new Set(state.destinations.map(countryContinent))].filter(Boolean);
  const isHome = path() === '/';
  const visible = isHome ? 6 : state.visible;
  return `<div class="destination-panel surface-card"><div class="search-controls"><label class="country-search">${icon('search')}<input id="country-search" type="search" value="${esc(state.search)}" placeholder="Поиск по странам" aria-label="Поиск по странам"></label><label class="select-control"><span class="sr-only">Континент</span><select id="continent-filter"><option value="all">Континент: Все</option>${continents.map(c=>`<option value="${esc(c)}" ${state.continent===c?'selected':''}>${esc(c)}</option>`).join('')}</select>${icon('chevron')}</label><label class="select-control"><span class="sr-only">Шенгенская зона</span><select id="schengen-filter"><option value="all">Шенген: Любой</option><option value="yes" ${state.schengen==='yes'?'selected':''}>В Шенгене</option><option value="no" ${state.schengen==='no'?'selected':''}>Вне Шенгена</option></select>${icon('chevron')}</label><label class="select-control"><span class="sr-only">Тип въезда</span><select id="entry-filter"><option value="all">Тип въезда: Любой</option>${Object.entries(entryLabels).map(([value,label])=>`<option value="${value}" ${state.entry===value?'selected':''}>${label}</option>`).join('')}</select>${icon('chevron')}</label></div><div class="results-heading"><h2>Направления</h2><span>${found.length} результатов</span></div>${found.length?`<div class="country-grid">${found.slice(0,visible).map(countryCard).join('')}</div>${found.length>visible?isHome?'<a class="button button-outline show-more" href="#/destinations">Все результаты</a>':'<button type="button" class="button button-outline show-more" data-action="show-more">Показать ещё</button>':''}`:empty('Ничего не найдено','Попробуйте другой запрос или сбросьте фильтры.','<button class="button button-outline" data-action="reset-search">Сбросить фильтры</button>')}</div>`;
}

function destinationsPage() {
  return `<main class="page-surface"><div class="container"><div class="page-head"><div><p class="eyebrow">КУДА ОТПРАВИМСЯ?</p><h1>Мир начинается с плана</h1><p>Поиск по ${state.destinations.length} направлениям и параметрам поездки.</p></div><a class="button button-outline" href="#/compare">Сравнить направления</a></div>${sampleWarning()}${notice('Фильтр Шенгена показывает географию направления. Визовое требование определяется по вашему профилю и актуальному источнику.')} ${destinationPanel()}<p class="transit-note">${icon('route')} Нет прямого перелёта — не значит, что поездка недоступна. Важны маршрут пересадок и транзитные требования.</p></div></main>`;
}

function destinationDetail(id) {
  const country = state.destinations.find(item => countryId(item) === id);
  if (!country) return notFound();
  const scenario = state.scenario;
  const requirements = listItems(scenario?.requirements);
  const sources = listItems(scenario?.sources);
  return `<main class="page-surface"><div class="container narrow"><a class="back-link" href="#/destinations">← Все направления</a><article class="surface-card detail-card"><div class="detail-title"><span class="country-icon large">${icon(countryIcon(country))}</span><div><p class="eyebrow">НАПРАВЛЕНИЕ</p><h1>${esc(countryName(country))}</h1><p>${esc(countryContinent(country))} · ${esc(entryText(scenario?.entry_mode || countryEntry(country)))}</p></div></div><div class="detail-grid"><section><h2>Что известно о сценарии</h2><p>${esc(country.hint || 'Требования уточняются после заполнения профиля и параметров поездки.')}</p><p>Персональные условия зависят от паспорта, дат, цели, места подачи и маршрута.</p></section><section class="light-card"><h3>Качество данных</h3><p>${state.mode==='demo'?'Образец интерфейса; реальные источники не проверены.':esc(scenario?.data_quality || country.data_quality || 'Не указано')}</p><p>Версия сценария: ${esc(scenario?.version || country.version || 'не указана')}.</p><p>Неизвестное условие не считается выполненным требованием.</p></section></div>${requirements.length?`<section class="detail-list"><h2>Требования сценария</h2>${requirements.map(item=>`<div class="detail-list-row"><strong>${esc(item.title || item.name || item.id || 'Требование')}</strong><p>${esc(item.description || item.explanation || 'Уточните применимость в персональном деле.')}</p></div>`).join('')}</section>`:''}${sources.length?`<section class="detail-list"><h2>Источники</h2>${sources.map(source=>{const url=safeHttpUrl(source.url);return `<div class="detail-list-row"><strong>${esc(source.title || source.name || source.id || 'Источник')}</strong><p>Проверено: ${esc(source.checked_at || source.verified_at || 'дата не указана')}</p>${url?`<a class="text-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Открыть источник ${icon('up-right')}</a>`:''}</div>`}).join('')}</section>`:state.mode==='demo'?'':notice('Проверенные источники для этого направления пока не представлены. Уточните условия в официальном источнике.','warning')}<div class="detail-actions"><a href="#/cases/new?scenario=${encodeURIComponent(id)}" class="button button-primary">Создать дело ${icon('arrow')}</a><a href="#/compare" class="button button-outline">Сравнить страны</a></div></article></div></main>`;
}

function notFound() { return `<main class="page-surface"><div class="container">${empty('Страница не найдена','Вернитесь к направлениям или в кабинет.','<a class="button button-primary" href="#/">На главную</a>')}</div></main>`; }

function authPage() {
  const register = state.authTab === 'register';
  return `<main class="auth-page"><div class="container auth-layout"><div class="auth-copy"><p class="eyebrow">ЛИЧНЫЙ КАБИНЕТ</p><h1>Все поездки и документы в одном месте</h1><p>Общий архив позволяет использовать свой документ в разных делах. Результаты проверок остаются привязаны к конкретной поездке.</p><div class="auth-points"><span>${icon('check')} Несколько дел в одном аккаунте</span><span>${icon('check')} Отдельные прогрессы подготовки и процесса</span><span>${icon('check')} История изменений и замечаний</span></div></div><div class="surface-card auth-card"><div class="auth-tabs"><button type="button" data-action="auth-tab" data-tab="login" class="${!register?'active':''}">Вход</button><button type="button" data-action="auth-tab" data-tab="register" class="${register?'active':''}">Регистрация</button></div><h2>${register?'Создать аккаунт':'С возвращением'}</h2><p>${register?'Начните с профиля и поездки.':'Войдите, чтобы продолжить работу с делами.'}</p><form data-form="auth"><input type="hidden" name="action" value="${state.authTab}"><label class="form-field">Имя пользователя<input name="username" autocomplete="username" minlength="3" required></label><label class="form-field">Пароль<input type="password" name="password" autocomplete="${register?'new-password':'current-password'}" minlength="8" required></label><button class="button button-primary full" type="submit">${register?'Создать аккаунт':'Войти'} ${icon('arrow')}</button></form>${state.mode==='demo'?`<div class="demo-login">${notice('Backend сейчас не подключён. Демо-кабинет использует вымышленные данные и не сохраняет пароль.','warning')}<a class="button button-outline full" href="#/cases">Открыть демо-кабинет</a></div>`:''}</div></div></main>`;
}

function profilePage() {
  const p = state.profile || {};
  return workspace('Профиль заявителя','Общие данные для ваших дел.',`${sampleWarning()}<form class="surface-card form-card" data-form="profile"><div class="form-section-title"><h2>Личные данные</h2><p>Сценарий рассчитан на совершеннолетнего туриста из РФ с обычным загранпаспортом.</p></div><div class="form-grid"><label class="form-field">Имя<input name="given_name" value="${esc(p.given_name||'')}" required></label><label class="form-field">Фамилия<input name="surname" value="${esc(p.surname||'')}" required></label><label class="form-field">Дата рождения<input type="date" name="birth_date" value="${esc(p.birth_date||'')}"></label><label class="form-field">Тип загранпаспорта<select name="passport_kind"><option value="" ${!p.passport_kind?'selected':''}>Неизвестно</option><option value="biometric" ${p.passport_kind==='biometric'?'selected':''}>Биометрический</option><option value="non_biometric" ${p.passport_kind==='non_biometric'?'selected':''}>Небиометрический</option></select></label><label class="form-field">Дата выдачи паспорта<input type="date" name="passport_issued_on" value="${esc(p.passport_issued_on||'')}"></label><label class="form-field">Срок действия паспорта<input type="date" name="passport_expires_on" value="${esc(p.passport_expires_on||'')}"></label><label class="form-field">Второе гражданство<select name="has_other_citizenship">${unknownOptions(p.has_other_citizenship)}</select></label><label class="form-field">Иностранный ВНЖ<select name="has_foreign_residence_permit">${unknownOptions(p.has_foreign_residence_permit)}</select></label></div><div class="form-actions"><button class="button button-primary" type="submit">Сохранить профиль</button><a class="button button-outline" href="#/compare">Перейти к сравнению</a></div></form>`, '/profile');
}

function unknownOptions(value) { return `<option value="unknown" ${value==null?'selected':''}>Неизвестно</option><option value="no" ${value===false?'selected':''}>Нет</option><option value="yes" ${value===true?'selected':''}>Да</option>`; }

function comparePage() {
  const unsupported = state.profile && (state.profile.citizenship !== 'RU' || state.profile.residence_country !== 'RU' || state.profile.has_other_citizenship === true || state.profile.has_foreign_residence_permit === true);
  const rows = listItems(state.comparison?.results || state.comparison);
  const input = state.comparisonInputs;
  const countryOptions = (selected, optional = false) => `${optional?'<option value="">Без третьей страны</option>':''}${state.destinations.map(country=>`<option value="${esc(countryId(country))}" ${countryId(country)===selected?'selected':''}>${esc(countryName(country))}</option>`).join('')}`;
  const selectedRows = state.comparisonSelected.map(id => rows.find(row => (row.scenario_id || row.id) === id) || {scenario_id:id,summary:'Результат для этой страны пока не получен. Попробуйте повторить сравнение.'});
  return workspace('Сравнение направлений','Выберите две или три страны и параметры поездки.',`${sampleWarning()}${unsupported?notice('Этот профиль выходит за границы поддерживаемого туристического сценария. Персональное сравнение недоступно.','warning'):''}<form class="surface-card form-card compact" data-form="compare"><div class="form-grid"><label class="form-field">Начало поездки<input name="starts_on" type="date" value="${esc(input.starts_on)}" required></label><label class="form-field">Окончание поездки<input name="ends_on" type="date" value="${esc(input.ends_on)}" required></label><label class="form-field">Город отправления<input name="departure_city" value="${esc(input.departure_city)}" required></label></div><div class="compare-country-fields"><label class="form-field">Первая страна<select name="country_a" required>${countryOptions(input.country_a)}</select></label><label class="form-field">Вторая страна<select name="country_b" required>${countryOptions(input.country_b)}</select></label><label class="form-field">Третья страна, по желанию<select name="country_c">${countryOptions(input.country_c,true)}</select></label></div><button class="button button-primary compare-submit" type="submit" ${unsupported?'disabled':''}>Сравнить направления ${icon('arrow')}</button></form><section class="compare-results" id="compare-results"><div class="section-split"><h2>Сравнение стран</h2>${badge(state.mode==='demo'?'Демо-данные':'Персональный расчёт',state.mode==='demo'?'demo':'')}</div>${selectedRows.length?`<div class="comparison-grid">${selectedRows.map(comparisonCard).join('')}</div>`:empty('Выберите страны','Результаты выбранных направлений появятся здесь после сравнения.')}</section>`, '/compare');
}

function comparisonCard(row) {
  const country = state.destinations.find(item=>countryId(item)===(row.scenario_id||row.id)) || row;
  const factors = row.factors || [];
  return `<article class="comparison-country surface-card"><div class="comparison-country-head"><span class="country-icon">${icon(countryIcon(country))}</span><h3>${esc(countryName(country))}</h3></div><dl class="comparison-facts"><div><dt>Регион</dt><dd>${esc(countryContinent(country))}</dd></div><div><dt>Шенгенская зона</dt><dd>${countrySchengen(country)===true?'Да':countrySchengen(country)===false?'Нет':'Неизвестно'}</dd></div><div><dt>Въезд</dt><dd>${esc(entryText(countryEntry(country)))}</dd></div></dl><p>${esc(row.summary || 'Требования и неизвестные условия показаны отдельно.')}</p><div class="factor-list">${(Array.isArray(factors)?factors:[]).slice(0,3).map(f=>badge(f.label||f.message||String(f),'neutral')).join('')}${row.unknown_count!=null?badge(`${row.unknown_count} неизвестных условий`,'warning'):''}</div><a class="button button-outline" href="#/destinations/${encodeURIComponent(countryId(country))}">Подробнее ${icon('arrow')}</a></article>`;
}

function casesPage() {
  return workspace('Мои дела','Одна поездка и один сценарий в каждом деле. Архив документов остаётся общим.',`${sampleWarning()}${state.cases.length?`<div class="case-list">${state.cases.map(item=>{const country=caseCountry(item);return `<article class="surface-card case-list-card"><div class="case-list-top"><span class="country-icon">${icon(countryIcon(country||{}))}</span><div><p class="eyebrow">ТУРИСТИЧЕСКАЯ ПОЕЗДКА</p><h2>${esc(countryName(country||{}))}</h2><p>${dateText(item.trip?.starts_on)} — ${dateText(item.trip?.ends_on)}</p></div>${badge(item.readiness?.ready?'Пакет готов':'В работе',item.readiness?.ready?'success':'warning')}</div><div class="case-list-progress">${progress(item.readiness?.percent,'Готовность пакета')}${progress(item.process?.percent,'Прогресс процесса')}</div><div class="case-list-actions"><a class="button button-primary" href="#/cases/${encodeURIComponent(item.id)}">Открыть дело ${icon('arrow')}</a><a class="button button-outline" href="#/cases/${encodeURIComponent(item.id)}/checklist">Чек-лист</a></div></article>`}).join('')}</div>`:empty('Дел пока нет','Создайте дело для выбранной поездки.','<a class="button button-primary" href="#/cases/new">Создать дело</a>')}`, '/cases', '<a class="button button-primary" href="#/cases/new">Новое дело</a>');
}

function newCasePage() {
  const selected = query().get('scenario') || state.destinations[0]?.id;
  const startsOn = query().get('starts_on') || '2027-06-10';
  const endsOn = query().get('ends_on') || '2027-06-20';
  const departureCity = query().get('departure_city') || 'Москва';
  return workspace('Новое дело','Укажите параметры одной туристической поездки. Страну в созданном деле нельзя заменить без нового сценария.',`${sampleWarning()}<form class="surface-card form-card" data-form="new-case"><div class="form-grid"><label class="form-field">Направление<select name="scenario_id">${state.destinations.map(c=>`<option value="${esc(countryId(c))}" ${countryId(c)===selected?'selected':''}>${esc(countryName(c))}</option>`).join('')}</select></label><label class="form-field">Цель поездки<input value="Туризм" disabled><input type="hidden" name="purpose" value="tourism"></label><label class="form-field">Начало поездки<input name="starts_on" type="date" value="${esc(startsOn)}" required></label><label class="form-field">Окончание поездки<input name="ends_on" type="date" value="${esc(endsOn)}" required></label><label class="form-field">Город отправления<input name="departure_city" value="${esc(departureCity)}" required></label><label class="form-field">Проживание<select name="accommodation_type"><option value="unknown">Неизвестно</option><option value="hotel">Отель</option><option value="invitation">Приглашение</option></select></label><label class="form-field">Финансирование<select name="funding_type"><option value="unknown">Неизвестно</option><option value="self">Самостоятельно</option><option value="sponsor">Спонсор</option></select></label></div><div class="form-actions"><button class="button button-primary" type="submit">Создать дело ${icon('arrow')}</button><a class="button button-outline" href="#/destinations">Назад к направлениям</a></div></form>`, '/cases');
}

function caseOverview(id) {
  const item = state.case;
  if (!item) return notFound();
  const country = caseCountry(item);
  const findings = listItems(item.findings || item.checks?.findings);
  return workspace(`${esc(countryName(country||{}))} · туристическая поездка`,'Пакет документов и этапы процесса показываются отдельно.',`${sampleWarning()}${caseTabs(id,'')}<div class="case-edit-link"><a class="text-link" href="#/cases/${encodeURIComponent(id)}/edit">Изменить поездку ${icon('arrow')}</a></div><div class="case-overview-grid"><article class="surface-card overview-card"><p class="eyebrow">СОСТОЯНИЕ ДЕЛА</p>${progress(item.readiness?.percent,'Готовность пакета')}<p class="small-muted">${item.readiness?.satisfied_required??0} из ${item.readiness?.required_total??0} обязательных требований выполнено. ${item.readiness?.unknown_required_conditions?`${item.readiness.unknown_required_conditions} требуют проверки.`:''}</p>${progress(item.process?.percent,'Прогресс процесса')}<p class="small-muted">Текущий этап: ${esc(item.process?.current_stage || 'Не указан')}. Исход: ${esc(item.process?.outcome || 'unknown')}.</p></article><article class="next-step overview-next"><p class="next-label">${icon('arrow')} СЛЕДУЮЩИЙ ШАГ</p><h2>${esc(item.readiness?.reasons?.[0] || 'Откройте чек-лист и проверьте требования')}</h2><a class="button button-primary" href="#/cases/${encodeURIComponent(id)}/checklist">Перейти к чек-листу</a></article></div><div class="workspace-columns"><section class="surface-card inner-card"><div class="section-split"><h2>Замечания</h2><button class="button button-outline small" data-action="run-checks" data-id="${esc(id)}">Запустить проверку</button></div>${findings.length?findings.map(f=>`<div class="finding-row">${badge(f.severity || 'info',f.severity==='error'?'warning':'neutral')}<div><strong>${esc(f.message)}</strong><p>${esc(f.next_action||'')}</p></div></div>`).join(''):empty('Проверки ещё нет','Добавьте и подтвердите поля документов, затем запустите проверку.','')}</section><section class="surface-card inner-card"><h2>Документы дела</h2><p class="small-muted">Один архивный документ можно прикрепить к нескольким своим делам. Проверка рассчитывается отдельно для каждой поездки.</p>${(item.attached_document_ids||[]).length?(item.attached_document_ids||[]).map(docId=>{const doc=state.documents.find(d=>d.id===docId);return `<div class="attached-document"><a class="attached-row" href="#/archive/${encodeURIComponent(docId)}">${icon('file')} ${esc(doc?.original_filename||docId)} ${icon('up-right')}</a><button class="detach-button" type="button" data-action="detach-document" data-case="${esc(id)}" data-id="${esc(docId)}">Отвязать</button></div>`}).join(''):empty('Пока пусто','Добавьте документы из личного архива.')}<a class="button button-outline full" href="#/archive">Открыть архив</a></section></div>`, '/cases');
}

function editCasePage(id) {
  const item = state.case;
  if (!item) return notFound();
  const trip = item.trip || {};
  const country = caseCountry(item);
  return workspace('Изменить поездку','После изменения дат или ответов проверки нужно выполнить повторно.',`${sampleWarning()}${caseTabs(id,'')}<form class="surface-card form-card" data-form="edit-case" data-id="${esc(id)}"><p class="eyebrow">${esc(countryName(country || {}))} · туристическая поездка</p><div class="form-grid"><label class="form-field">Начало поездки<input name="starts_on" type="date" value="${esc(trip.starts_on || '')}" required></label><label class="form-field">Окончание поездки<input name="ends_on" type="date" value="${esc(trip.ends_on || '')}" required></label><label class="form-field">Город отправления<input name="departure_city" value="${esc(trip.departure_city || '')}" required></label></div><div class="form-actions"><button class="button button-primary" type="submit">Сохранить поездку</button><a class="button button-outline" href="#/cases/${encodeURIComponent(id)}">Отмена</a></div></form><div class="case-danger surface-card"><div><strong>Удалить дело</strong><p>Документы останутся в общем архиве.</p></div><button class="button button-outline" type="button" data-action="delete-case" data-id="${esc(id)}">Удалить дело</button></div>`, '/cases');
}

function checklistPage(id) {
  const item = state.case;
  if (!item) return notFound();
  const list = listItems(item.checklist);
  return workspace('Smart Checklist','Требования применяются к этому делу; файл сам по себе не закрывает пункт.',`${sampleWarning()}${caseTabs(id,'/checklist')}${notice('Неизвестное обязательное условие оставляет готовность в состоянии «нужна проверка». Успешная загрузка документа не равна прохождению проверки.')}<div class="checklist-list">${list.map(c=>{const kind=c.state==='satisfied'?'success':c.state==='missing'?'warning':'neutral';return `<article class="surface-card checklist-card"><div class="checklist-head"><span class="checklist-state ${kind}">${icon(c.state==='satisfied'?'check':'info')}</span><div><h2>${esc(c.title||c.requirement_id||'Требование')}</h2><p>${c.mandatory?'Обязательное':'Рекомендованное'} · применимость: ${esc(c.applicability||'unknown')}</p></div>${badge(c.state==='satisfied'?'Выполнено':c.state==='missing'?'Не хватает':'Нужна проверка',kind)}</div><p>${esc(c.explanation||'Подробности уточняются в сценарии.')}</p><div class="checklist-actions"><span>${esc(c.next_action||'Откройте документ или источник.')}</span>${c.kind==='manual'?`<button class="button button-outline small" type="button" data-action="toggle-checklist" data-id="${esc(c.id)}" data-case="${esc(id)}" data-state="${esc(c.state)}">${c.state==='satisfied'?'Снять отметку':'Отметить действие'}</button>`:`<a class="button button-outline small" href="#/archive">К документам</a>`}</div></article>`}).join('')}</div>`, '/cases');
}

const docNames = { passport:'Загранпаспорт', bank_statement:'Банковская выписка', insurance:'Страховой полис' };
function archivePage() {
  return workspace('Личный архив','Документы принадлежат аккаунту и могут использоваться в нескольких ваших делах.',`${sampleWarning()}<div class="workspace-columns archive-top"><form class="surface-card inner-card upload-card" data-form="upload"><h2>Добавить документ</h2><p>Только полностью вымышленные образцы PDF, JPEG или PNG. До 10 МиБ.</p><label class="form-field">Тип документа<select name="document_type"><option value="passport">Загранпаспорт</option><option value="bank_statement">Банковская выписка</option><option value="insurance">Страховой полис</option></select></label><label class="file-drop">${icon('file')}<strong>Выбрать файл</strong><span>PDF, JPEG, PNG · до 10 МиБ</span><input type="file" name="file" accept="application/pdf,image/jpeg,image/png" required></label><button class="button button-primary full" type="submit">Загрузить в архив</button></form><div class="surface-card inner-card"><h2>Как работает архив</h2><div class="archive-explainer"><p>${icon('check')} Оригинал вымышленного файла хранится на backend до удаления владельцем.</p><p>${icon('check')} Один документ можно прикрепить к нескольким делам.</p><p>${icon('info')} Поля и результаты проверок имеют собственные версии; после правки нужна повторная проверка.</p></div></div></div><div class="section-split archive-list-head"><h2>Мои документы</h2><span class="small-muted">${state.documents.length} файлов</span></div>${state.documents.length?`<div class="archive-list">${state.documents.map(doc=>`<article class="surface-card document-card"><span class="document-icon">${icon('file')}</span><div><h3>${esc(doc.original_filename)}</h3><p>${esc(docNames[doc.document_type]||doc.document_type)} · ${esc(doc.extraction_state||'uploaded')} · ${Math.round((doc.size_bytes||0)/1024)} КиБ</p></div><a class="button button-outline small" href="#/archive/${encodeURIComponent(doc.id)}">Открыть ${icon('arrow')}</a></article>`).join('')}</div>`:empty('В архиве пока пусто','Добавьте вымышленный документ, чтобы увидеть поля и связать его с делом.')}`, '/archive');
}

function documentPage(id) {
  const doc = state.document;
  if (!doc) return notFound();
  const fields = listItems(doc.fields);
  return workspace(esc(doc.original_filename),'Извлечённые поля нужно сверить. Уверенность OCR не означает, что значение верно.',`${sampleWarning()}<a class="back-link" href="#/archive">← Личный архив</a><div class="document-danger"><button class="detach-button" type="button" data-action="delete-document" data-id="${esc(id)}">Удалить документ из архива</button></div><div class="workspace-columns doc-detail-grid"><section class="surface-card inner-card"><div class="section-split"><div><p class="eyebrow">${esc(docNames[doc.document_type]||doc.document_type)}</p><h2>Поля документа</h2></div>${badge(doc.extraction_state||'uploaded','neutral')}</div><p class="small-muted">Источник значения и уверенность показаны отдельно. Пользовательское подтверждение относится к текущей версии фактов.</p>${fields.length?`<form data-form="fields" data-id="${esc(id)}"><div class="field-list">${fields.map((field,index)=>`<div class="field-edit"><label class="form-field">${esc(field.key)}<input name="field-${index}" value="${esc(field.value||'')}"></label><div class="field-meta"><span>Источник: ${esc(field.source_kind||'неизвестно')}</span><span>${field.confidence==null?'Уверенность не указана':`OCR ${Math.round(field.confidence*100)}%`}</span><label><input type="checkbox" name="confirm-${index}" ${field.user_confirmed?'checked':''}> Подтверждено мной</label></div></div>`).join('')}</div><button class="button button-primary" type="submit">Сохранить и подтвердить</button></form>`:empty('Поля ещё не извлечены','Запустите извлечение, затем проверьте результат.')}<div class="form-actions"><button class="button button-outline" data-action="extract" data-id="${esc(id)}">Извлечь поля</button>${state.mode==='api'?`<button class="button button-outline" data-action="download-document" data-id="${esc(id)}">Открыть файл</button>`:''}</div></section><section class="surface-card inner-card"><h2>Замечания качества</h2>${listItems(doc.quality_findings).length?listItems(doc.quality_findings).map(f=>`<div class="finding-row">${badge(f.severity||'warning','warning')}<p>${esc(f.message||'Проверьте поле.')}</p></div>`).join(''):empty('Замечаний нет','Это не означает, что документ проверен по правилам дела.')}<h2 class="block-gap">Прикрепить к делу</h2><form data-form="attach-document" data-id="${esc(id)}"><label class="form-field">Выберите своё дело<select name="case_id">${state.cases.map(c=>`<option value="${esc(c.id)}">${esc(countryName(caseCountry(c)||{}))} · ${dateText(c.trip?.starts_on)}</option>`).join('')}</select></label><button class="button button-primary full" type="submit" ${state.cases.length?'':'disabled'}>Прикрепить документ</button></form></section></div>`, '/archive');
}

function routePage(id) {
  const item = state.case;
  if (!item) return notFound();
  const result = state.routeResults[id];
  return workspace('Маршрут и пересадки','Введите условия пересадки вручную. Наличие рейсов и билетов не проверяется.',`${sampleWarning()}${caseTabs(id,'/route')}<div class="workspace-columns route-grid"><form class="surface-card inner-card" data-form="route" data-id="${esc(id)}"><h2>Пересадка</h2><p class="small-muted">В прототипе предусмотрены правила для Турции и ОАЭ. Другие страны и непокрытые условия требуют проверки.</p><div class="form-grid"><label class="form-field">Страна пересадки<select name="country"><option value="none">Без пересадки</option><option value="TR">Турция</option><option value="AE">ОАЭ</option><option value="other">Другая страна</option></select></label><label class="form-field">Аэропорт<input name="airport" placeholder="Код или название"></label><label class="form-field">Время прибытия<input name="arrival_at" type="datetime-local"></label><label class="form-field">Время вылета<input name="departure_at" type="datetime-local"></label><label class="form-field">Билеты<select name="ticket_type"><option value="unknown">Неизвестно</option><option value="single">Единый билет</option><option value="separate">Разные билеты</option></select></label><label class="form-field">Выход из транзитной зоны<select name="leaves_airside">${unknownOptions(null)}</select></label><label class="form-field">Повторная регистрация багажа<select name="rechecks_baggage">${unknownOptions(null)}</select></label><label class="form-field">Разрешение на въезд/транзит<input name="permissions" placeholder="Если известно"></label></div><button class="button button-primary" type="submit">Проверить условия ${icon('arrow')}</button></form><section class="surface-card inner-card"><h2>Результат</h2>${result?`<div class="result-card ${result.status==='blocked'?'warning':''}">${badge(result.status==='blocked'?'Есть препятствие':result.status==='no_known_blockers'?'Известных препятствий не найдено':'Требует проверки',result.status==='blocked'?'warning':'neutral')}<p>${esc(result.message||'Результат относится только к указанным условиям.')}</p></div>${listItems(result.findings).map(f=>`<div class="finding-row">${badge(f.severity||'info','neutral')}<p>${esc(f.message)}</p></div>`).join('')}<p class="small-muted">Наличие рейсов: не проверено. Сохраните подтверждения и проверьте актуальные официальные условия.</p>`:empty('Маршрут ещё не проверен','Укажите пересадку и её условия. Неизвестные ответы остаются неизвестными.')}</section></div>`, '/cases');
}

function letterPage(id) {
  const item = state.case;
  if (!item) return notFound();
  const letter = state.letter || state.letters[0];
  return workspace('Сопроводительное письмо','Черновик составляется из подтверждённых фактов и остаётся редактируемым.',`${sampleWarning()}${caseTabs(id,'/letter')}<div class="workspace-columns letter-grid"><section class="surface-card inner-card"><h2>Черновик</h2>${letter?`<form data-form="letter" data-id="${esc(letter.id)}"><label class="form-field">Текст письма<textarea name="content" rows="16">${esc(letter.content||'')}</textarea></label><div class="form-actions"><button class="button button-primary" type="submit">Сохранить правки</button><button class="button button-outline" type="button" data-action="download-letter" data-id="${esc(letter.id)}">Скачать .txt</button></div></form>`:empty('Письмо ещё не создано','Для черновика используются только подтверждённые данные.',`<button class="button button-primary" data-action="generate-letter" data-id="${esc(id)}">Создать черновик</button>`)}</section><section class="surface-card inner-card"><h2>Что войдёт в текст</h2><p class="small-muted">Имя, цель и даты берутся из подтверждённых данных профиля и дела. Недостающие факты не выдумываются.</p>${letter?.missing_facts?.length?`<h3>Нужно уточнить</h3><ul>${letter.missing_facts.map(f=>`<li>${esc(f)}</li>`).join('')}</ul>`:''}${notice('Новая генерация не должна молча заменить ваши ручные правки. Перед повторной генерацией сохраните нужный текст.','warning')}<p class="small-muted">Режим: ${esc(letter?.generation_mode||'ещё не создавалось')} · ${letter?.stale?'результат устарел':'состояние проверяется по версии входных данных'}</p></section></div>`, '/cases');
}

const eventNames = { form_prepared:'Анкета подготовлена', appointment_booked:'Запись оформлена', submitted:'Документы поданы', under_review:'На рассмотрении', additional_documents_requested:'Запрошены документы', decision_received:'Решение получено', passport_returned:'Паспорт возвращён', entry_preparation_completed:'Подготовка ко въезду завершена' };
function timelinePage(id) {
  const item = state.case;
  if (!item) return notFound();
  return workspace('Хронология дела','События добавляются вручную. Это не доступ к внутреннему статусу консульства.',`${sampleWarning()}${caseTabs(id,'/timeline')}<div class="workspace-columns timeline-grid"><section class="surface-card inner-card"><h2>События</h2>${state.events.length?`<ol class="timeline">${state.events.map(event=>`<li><time>${dateText(event.occurred_at)}</time><div><strong>${esc(eventNames[event.event_type]||event.event_type)}</strong><p>${esc(event.note||'Без комментария')}</p><small>Источник: ${esc(event.source||'manual')}</small></div></li>`).join('')}</ol>`:empty('Событий пока нет','Добавьте этап, который действительно произошёл.')}</section><section class="surface-card inner-card"><h2>Добавить событие</h2><form data-form="event" data-id="${esc(id)}"><label class="form-field">Тип события<select name="event_type">${Object.entries(eventNames).map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label><label class="form-field">Дата события<input name="occurred_at" type="date" value="${new Date().toISOString().slice(0,10)}" required></label><label class="form-field">Исход (для полученного решения)<select name="outcome"><option value="">Неизвестно</option><option value="approved">Одобрено</option><option value="refused">Отказ</option></select></label><label class="form-field">Комментарий<textarea name="note" rows="4" placeholder="Только фактически произошедшее"></textarea></label><button class="button button-primary full" type="submit">Добавить вручную</button></form><h2 class="block-gap">Внутренние уведомления</h2>${state.notifications.length?state.notifications.map(n=>`<div class="notification-row">${icon('info')}<span>${esc(n.message||n.title||'Уведомление')}</span></div>`).join(''):'<p class="small-muted">Новых уведомлений нет.</p>'}</section></div>`, '/cases');
}

function aboutPage() {
  return `<main class="page-surface"><div class="container narrow"><div class="page-head"><div><p class="eyebrow">О ПРОДУКТЕ</p><h1>Что делает VisaMate</h1><p>Внутренний прототип для подготовки туристической поездки.</p></div></div><article class="surface-card inner-card prose-card"><h2>Один понятный следующий шаг</h2><p>Сервис связывает профиль, поездку, требования и документы. Он показывает, что добавить, какое поле исправить и какое условие проверить. Визовая подготовка и прохождение процесса отображаются отдельно.</p><h2>Границы поддержки</h2><p>Поддерживается один взрослый гражданин РФ, проживающий в РФ, с обычным загранпаспортом и туристической целью. Второе гражданство, иностранный ВНЖ, несовершеннолетние, работа и учёба в этот прототип не входят.</p><h2>Данные и проверка</h2><p>Демо использует полностью вымышленные документы. Автоматическая проверка не подтверждает подлинность файла, не гарантирует допуск и не прогнозирует решение консульства. Официальную подачу пользователь выполняет вне сервиса.</p><a href="#/destinations" class="button button-primary">Выбрать направление ${icon('arrow')}</a></article></div></main>`;
}

async function loadForRoute(route) {
  state.case = null; state.document = null; state.letter = null; state.scenario = null;
  if (state.mode === 'demo') {
    state.cases = state.demo.cases; state.documents = state.demo.documents;
    if (route.startsWith('/cases/') && route !== '/cases/new') state.case = state.demo.cases.find(item => route.split('/')[2] === item.id) || null;
    if (route.startsWith('/archive/')) state.document = state.demo.documents.find(item => route.split('/')[2] === item.id) || null;
    if (route.endsWith('/timeline')) { state.events = state.demo.events.filter(item=>item.case_id===state.case?.id); state.notifications = []; }
    if (route.endsWith('/letter')) { state.letters = state.demo.letters.filter(item=>item.case_id===state.case?.id); state.letter = state.letters.at(-1) || null; }
    return;
  }
  if (route === '/' && state.user) {
    try { state.cases = listItems(await api.cases()); } catch { state.cases = []; }
  }
  if (route.startsWith('/destinations/')) {
    try { state.scenario = await api.scenario(route.split('/')[2]); } catch { state.scenario = null; }
  }
  if (!state.user || !['/profile','/compare','/cases','/archive'].some(prefix=>route===prefix||route.startsWith(prefix+'/'))) return;
  try {
    if (route === '/profile' || route === '/compare') state.profile = await api.profile();
    if (route === '/cases' || route === '/archive' || route.startsWith('/archive/')) state.cases = listItems(await api.cases());
    if (route === '/archive' || route.startsWith('/archive/') || route.startsWith('/cases/')) state.documents = listItems(await api.documents());
    if (route === '/archive') return;
    if (route.startsWith('/archive/')) { state.document = await api.document(route.split('/')[2]); return; }
    if (route.startsWith('/cases/') && route !== '/cases/new') {
      const id = route.split('/')[2];
      state.case = await api.case(id);
      if (route === `/cases/${id}`) {
        try { state.case.checks = await api.checks(id); } catch { /* The case still renders without a check result. */ }
      }
      if (route.endsWith('/letter')) { state.letters = listItems(await api.letters(id)); state.letter = state.letters.at(-1) || null; }
      if (route.endsWith('/timeline')) {
        const [events, notifications] = await Promise.allSettled([api.events(id),api.notifications(id)]);
        state.events = events.status==='fulfilled'?listItems(events.value):[];
        state.notifications = notifications.status==='fulfilled'?listItems(notifications.value):[];
      }
    }
  } catch (error) {
    if (error.status === 401) { state.user = null; api.csrfToken = null; }
    else toast(fmtError(error),'error');
  }
}

function routeContent(route) {
  if (route === '/') return home();
  if (route === '/destinations') return destinationsPage();
  if (route.startsWith('/destinations/')) return destinationDetail(route.split('/')[2]);
  if (route === '/auth') return authPage();
  if (route === '/about') return aboutPage();
  if (state.mode === 'api' && !state.user) return `<main class="page-surface"><div class="container narrow">${empty('Войдите в кабинет','Профиль, дела и архив доступны после входа.','<a href="#/auth" class="button button-primary">Войти</a>')}</div></main>`;
  if (route === '/profile') return profilePage();
  if (route === '/compare') return comparePage();
  if (route === '/cases') return casesPage();
  if (route === '/cases/new') return newCasePage();
  if (route.startsWith('/cases/')) {
    const id = route.split('/')[2];
    if (route.endsWith('/edit')) return editCasePage(id);
    if (route.endsWith('/checklist')) return checklistPage(id);
    if (route.endsWith('/route')) return routePage(id);
    if (route.endsWith('/letter')) return letterPage(id);
    if (route.endsWith('/timeline')) return timelinePage(id);
    return caseOverview(id);
  }
  if (route === '/archive') return archivePage();
  if (route.startsWith('/archive/')) return documentPage(route.split('/')[2]);
  return notFound();
}

let navigationVersion = 0;
async function navigate() {
  const version = ++navigationVersion;
  state.mobileMenu = false;
  const route = path();
  await loadForRoute(route);
  if (version !== navigationVersion) return;
  $('app').innerHTML = header() + routeContent(route) + footer();
  const titles = {'/':'Главная','/destinations':'Направления','/compare':'Сравнение','/cases':'Мои дела','/archive':'Личный архив','/profile':'Профиль','/auth':'Вход','/about':'О сервисе'};
  const title = titles[route] || (route.startsWith('/cases/') ? 'Дело' : route.startsWith('/archive/') ? 'Документ' : route.startsWith('/destinations/') ? 'Направление' : 'VisaMate');
  document.title = `${title} — VisaMate`;
  window.scrollTo(0,0);
}

window.addEventListener('hashchange', navigate);

document.addEventListener('input', event => {
  if (event.target.id === 'country-search') {
    const caret = event.target.selectionStart;
    state.search = event.target.value; state.visible = 6;
    const panel = event.target.closest('.destination-panel');
    const scroll = window.scrollY;
    const scratch = document.createElement('div'); scratch.innerHTML = destinationPanel();
    panel.replaceWith(scratch.querySelector('.destination-panel'));
    const search = $('country-search'); search.focus(); search.setSelectionRange(caret,caret);
    window.scrollTo(0,scroll);
  }
});

document.addEventListener('change', event => {
  if (['continent-filter', 'schengen-filter', 'entry-filter'].includes(event.target.id)) {
    state.continent = $('continent-filter').value; state.schengen = $('schengen-filter').value;
    state.entry = $('entry-filter').value; state.visible = 6;
    const scroll = window.scrollY; $('app').innerHTML = header()+routeContent(path())+footer(); window.scrollTo(0,scroll);
  }
});

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'menu') { state.mobileMenu = !state.mobileMenu; $('main-nav').classList.toggle('open',state.mobileMenu); button.setAttribute('aria-expanded',String(state.mobileMenu)); return; }
  if (action === 'auth-tab') { state.authTab = button.dataset.tab; $('app').innerHTML = header()+authPage()+footer(); return; }
  if (action === 'show-more') { state.visible += 6; const scroll=window.scrollY; $('app').innerHTML=header()+routeContent(path())+footer();window.scrollTo(0,scroll);return; }
  if (action === 'reset-search') { state.search='';state.continent='all';state.schengen='all';state.entry='all';state.visible=6;$('app').innerHTML=header()+routeContent(path())+footer();return; }
  if (action === 'logout') {
    try { await api.logout(); state.user=null; state.profile=null; state.cases=[]; state.documents=[]; api.csrfToken=null; toast('Вы вышли из кабинета.','success'); location.hash='#/'; await navigate(); }
    catch(error) { toast(fmtError(error),'error'); }
    return;
  }
  button.disabled = true;
  try {
    if (action === 'delete-case') await deleteCase(button.dataset.id);
    if (action === 'detach-document') await detachDocument(button.dataset.case,button.dataset.id);
    if (action === 'delete-document') await deleteDocument(button.dataset.id);
    if (action === 'run-checks') await runChecks(button.dataset.id);
    if (action === 'toggle-checklist') await toggleChecklist(button.dataset.case,button.dataset.id,button.dataset.state);
    if (action === 'extract') await extractDocument(button.dataset.id);
    if (action === 'download-document') await downloadDocument(button.dataset.id);
    if (action === 'generate-letter') await generateLetter(button.dataset.id);
    if (action === 'download-letter') await downloadLetter(button.dataset.id);
  } catch(error) { toast(fmtError(error),'error'); }
  finally { button.disabled = false; }
});

document.addEventListener('submit', async event => {
  const form = event.target.closest('[data-form]');
  if (!form) return;
  event.preventDefault();
  const action = form.dataset.form;
  const button = form.querySelector('button[type="submit"]'); if (button) button.disabled=true;
  try {
    if (action === 'quick-route') { const d=Object.fromEntries(new FormData(form)); location.hash=`#/cases/new?scenario=${encodeURIComponent(d.scenario_id)}&starts_on=${encodeURIComponent(d.starts_on)}&ends_on=${encodeURIComponent(d.ends_on)}&departure_city=${encodeURIComponent(d.departure_city)}`; return; }
    if (action === 'auth') await submitAuth(form);
    if (action === 'profile') await submitProfile(form);
    if (action === 'compare') await submitComparison(form);
    if (action === 'new-case') await submitCase(form);
    if (action === 'edit-case') await submitCaseEdit(form);
    if (action === 'upload') await uploadDocument(form);
    if (action === 'fields') await saveFields(form);
    if (action === 'attach-document') await attachDocument(form);
    if (action === 'route') await submitRoute(form);
    if (action === 'letter') await saveLetter(form);
    if (action === 'event') await addEvent(form);
  } catch(error) { toast(fmtError(error),'error'); }
  finally { if (button) button.disabled=false; }
});

async function submitAuth(form) {
  if (state.mode === 'demo') { toast('В демо вход не создаёт настоящую учётную запись. Откройте демо-кабинет.','warning'); return; }
  const data = Object.fromEntries(new FormData(form));
  if (data.action === 'register') { await api.register(data.username,data.password); toast('Аккаунт создан. Теперь войдите.','success'); state.authTab='login'; $('app').innerHTML=header()+authPage()+footer(); return; }
  const response = await api.login(data.username,data.password);
  state.user = response.user || response;
  api.csrfToken = response.csrf_token || api.csrfToken;
  toast('Вы вошли в кабинет.','success'); location.hash='#/cases';
}

function nullableChoice(value) { return value==='unknown'?null:value==='yes'; }
async function submitProfile(form) {
  const data={...Object.fromEntries(new FormData(form)),citizenship:'RU',residence_country:'RU',};
  for (const key of ['has_other_citizenship','has_foreign_residence_permit']) data[key]=nullableChoice(data[key] || 'unknown');
  for (const key of ['birth_date','passport_kind','passport_issued_on','passport_expires_on']) if (!data[key]) data[key]=null;
  if (state.mode==='api') state.profile=await api.saveProfile(data);
  else state.profile={...state.profile,...data};
  toast('Профиль сохранён.','success'); await navigate();
}

async function submitComparison(form) {
  const data=Object.fromEntries(new FormData(form));
  if (data.starts_on>data.ends_on) { toast('Окончание поездки должно быть позже начала.','error');return; }
  const selected=[data.country_a,data.country_b,data.country_c].filter(Boolean);
  if (selected.length<2 || new Set(selected).size!==selected.length) { toast('Выберите две или три разные страны.','error');return; }
  const trip={purpose:'tourism',destination_country:null,starts_on:data.starts_on,ends_on:data.ends_on,departure_city:data.departure_city,application_country:null,accommodation_type:null,funding_type:null};
  if (state.mode==='api') state.comparison=await api.compare({trip,answers:{}});
  else state.comparison={results:state.destinations.map(country=>({scenario_id:countryId(country),summary:'Демо: точные требования не проверены.',unknown_count:1,factors:[{label:'Нужна сверка источника'}]}))};
  state.comparisonInputs=data; state.comparisonSelected=selected;
  toast('Сравнение подготовлено.','success'); await navigate();
  $('compare-results')?.scrollIntoView?.({behavior:'smooth',block:'start'});
}

async function submitCase(form) {
  const data=Object.fromEntries(new FormData(form));
  if (data.starts_on>data.ends_on) { toast('Проверьте даты поездки.','error');return; }
  const country=state.destinations.find(item=>countryId(item)===data.scenario_id);
  const trip={purpose:'tourism',destination_country:country?.destination_country||data.scenario_id.split('_')[0],starts_on:data.starts_on,ends_on:data.ends_on,departure_city:data.departure_city,application_country:null,accommodation_type:data.accommodation_type==='unknown'?null:data.accommodation_type,funding_type:data.funding_type==='unknown'?null:data.funding_type};
  let item;
  if (state.mode==='api') item=await api.createCase({scenario_id:data.scenario_id,trip,answers:{}});
  else { item={...clone(sampleCase),id:`demo-${Date.now()}`,scenario_id:data.scenario_id,trip,attached_document_ids:[],checklist:clone(sampleCase.checklist).map(c=>({...c,state:'needs_verification'})),process:{percent:0,current_stage:'Дело создано',outcome:'unknown',stages:clone(sampleCase.process.stages)}};updateDemoReadiness(item);state.demo.cases.unshift(item); }
  toast('Дело создано.','success'); location.hash=`#/cases/${encodeURIComponent(item.id)}`;
}

async function submitCaseEdit(form) {
  const data = Object.fromEntries(new FormData(form));
  if (data.starts_on > data.ends_on) { toast('Проверьте даты поездки.','error'); return; }
  const item = state.case;
  const trip = {...item.trip,starts_on:data.starts_on,ends_on:data.ends_on,departure_city:data.departure_city};
  if (state.mode === 'api') await api.updateCase(item.id,{trip});
  else { item.trip = trip; item.revision += 1; item.readiness = {...item.readiness,percent:null,ready:false,reasons:['Проверьте требования после изменения поездки']}; }
  toast('Поездка обновлена. Запустите проверки повторно.','success'); location.hash=`#/cases/${encodeURIComponent(item.id)}`;
}

async function deleteCase(id) {
  if (!window.confirm('Удалить дело? Документы останутся в личном архиве.')) return;
  if (state.mode === 'api') await api.deleteCase(id);
  else state.demo.cases = state.demo.cases.filter(item => item.id !== id);
  delete state.routeResults[id];
  toast('Дело удалено.','success'); location.hash='#/cases'; await navigate();
}

async function uploadDocument(form) {
  const file=form.querySelector('input[type="file"]').files[0];
  if (!file) throw new Error('Выберите файл.');
  if (!['application/pdf','image/jpeg','image/png'].includes(file.type)) { toast('Поддерживаются PDF, JPEG и PNG.','error');return; }
  if (file.size>10*1024*1024) { toast('Файл больше 10 МиБ.','error');return; }
  const type=form.querySelector('[name="document_type"]').value;
  if (state.mode==='api') await api.uploadDocument(file,type);
  else state.demo.documents.unshift({id:`demo-doc-${Date.now()}`,document_type:type,original_filename:file.name,mime_type:file.type,size_bytes:file.size,extraction_state:'uploaded',facts_revision:0,fields:[],quality_findings:[],created_at:new Date().toISOString()});
  toast(state.mode==='demo'?'Файл добавлен только как запись демо. Содержимое не сохраняется.':'Файл добавлен в личный архив.','success'); await navigate();
}

async function saveFields(form) {
  const doc=state.document;
  const fields=listItems(doc.fields).map((field,index)=>({...field,value:form.querySelector(`[name="field-${index}"]`).value,user_confirmed:form.querySelector(`[name="confirm-${index}"]`).checked}));
  if (state.mode==='api') await api.updateFields(form.dataset.id,fields,doc.facts_revision);
  else {doc.fields=fields;doc.facts_revision=(doc.facts_revision||0)+1;doc.extraction_state='extracted';}
  toast('Поля сохранены. Проверьте дело повторно.','success'); await navigate();
}

async function attachDocument(form) {
  const caseId=form.querySelector('[name="case_id"]').value, docId=form.dataset.id;
  if (!caseId) { toast('Сначала создайте дело.','warning');return; }
  if (state.mode==='api') await api.attachDocument(caseId,docId);
  else { const item=state.demo.cases.find(c=>c.id===caseId); if(!item.attached_document_ids.includes(docId))item.attached_document_ids.push(docId); }
  toast('Документ прикреплён к делу. Для оценки нужен отдельный запуск проверок.','success'); location.hash=`#/cases/${encodeURIComponent(caseId)}`;
}

async function detachDocument(caseId, docId) {
  if (state.mode === 'api') await api.detachDocument(caseId,docId);
  else { const item=state.demo.cases.find(c=>c.id===caseId); if(item)item.attached_document_ids=item.attached_document_ids.filter(id=>id!==docId); }
  toast('Документ отвязан от дела. Запустите проверки повторно.','success'); await navigate();
}

async function deleteDocument(docId) {
  if (!window.confirm('Удалить документ из личного архива? Он будет отвязан от всех дел.')) return;
  if (state.mode === 'api') await api.deleteDocument(docId);
  else { state.demo.documents=state.demo.documents.filter(doc=>doc.id!==docId); for(const item of state.demo.cases)item.attached_document_ids=item.attached_document_ids.filter(id=>id!==docId); }
  toast('Документ удалён из архива.','success'); location.hash='#/archive'; await navigate();
}

async function runChecks(caseId) {
  if (state.mode==='api') { await api.runChecks(caseId); toast('Проверка запущена.','success'); await navigate(); }
  else toast('Демо не анализирует документы. Подключите backend для реальной проверки.','warning');
}

async function toggleChecklist(caseId,itemId,current) {
  const completed=current!=='satisfied';
  if (state.mode==='api') await api.updateChecklist(caseId,itemId,{state:completed?'satisfied':'pending'});
  else {const demoCase=state.demo.cases.find(c=>c.id===caseId);const item=demoCase?.checklist.find(c=>c.id===itemId);if(item){item.state=completed?'satisfied':'needs_verification';updateDemoReadiness(demoCase);}}
  toast('Ручное действие обновлено.','success'); await navigate();
}

async function extractDocument(docId) {
  if (state.mode==='api') { await api.extractDocument(docId);toast('Извлечение выполнено. Проверьте поля.','success');await navigate(); }
  else toast('В демо нет OCR. Поля образцов можно просмотреть и подтвердить вручную.','warning');
}

async function downloadDocument(docId) {
  if (state.mode!=='api') return;
  const response=await fetch(`/api/v1/documents/${encodeURIComponent(docId)}/file`,{credentials:'include'});
  if(!response.ok)throw new ApiError('Не удалось открыть файл.',response.status);
  const blob=await response.blob();const url=URL.createObjectURL(blob);window.open(url,'_blank','noopener');setTimeout(()=>URL.revokeObjectURL(url),60000);
}

async function downloadPrivateFile(path, filename) {
  const response = await fetch(path, {credentials:'include'});
  if (!response.ok) throw new ApiError('Не удалось скачать файл.', response.status);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function submitRoute(form) {
  const data=Object.fromEntries(new FormData(form));
  const transfer=data.country==='none'?[]:[{country:data.country,airport:data.airport||null,arrival_at:data.arrival_at?new Date(data.arrival_at).toISOString():null,departure_at:data.departure_at?new Date(data.departure_at).toISOString():null,ticket_type:data.ticket_type==='unknown'?null:data.ticket_type,leaves_airside:nullableChoice(data.leaves_airside),rechecks_baggage:nullableChoice(data.rechecks_baggage),permissions:data.permissions||null}];
  if (state.mode==='api') { await api.saveRoute(form.dataset.id,{legs:[],transfers:transfer});state.routeResults[form.dataset.id]=await api.checkRoute(form.dataset.id); }
  else state.routeResults[form.dataset.id]={status:'needs_verification',message:data.country==='TR'||data.country==='AE'?'В демо эта страна входит в покрытый набор, но конкретные условия не проверены.':'Для этого маршрута в демо нет проверенного правила.',findings:[],flight_availability:'not_checked'};
  toast('Результат маршрута обновлён.','success'); await navigate();
}

async function generateLetter(caseId) {
  if (state.mode==='api') state.letter=await api.generateLetter(caseId);
  else {
    const item=state.demo.cases.find(c=>c.id===caseId),profile=state.profile||{};
    const name=[profile.given_name,profile.surname].filter(Boolean).join(' ');
    const country=countryName(caseCountry(item)||{});
    const content=`Dear Visa Officer,\n\nMy name is ${name||'[name to confirm]'}. I plan a tourism trip to ${country} from ${item.trip.starts_on} to ${item.trip.ends_on}.\n\nThis is a draft based only on the information entered in the demo. Please verify every fact and add the supporting documents before using it.\n\nSincerely,\n${name||'[name]'}`;
    state.letter={id:`demo-letter-${Date.now()}`,case_id:caseId,type:'cover_letter',language:'en',content,missing_facts:name?[]:['Имя заявителя'],generation_mode:'template',from_cache:false,stale:false,edited_by_user:false};
    state.demo.letters.push(state.letter);
  }
  toast('Черновик создан. Проверьте и отредактируйте текст.','success'); await navigate();
}

async function saveLetter(form) {
  const content=form.querySelector('[name="content"]').value;
  if (state.mode==='api') await api.updateLetter(form.dataset.id,content);
  else { const letter=state.demo.letters.find(l=>l.id===form.dataset.id);letter.content=content;letter.edited_by_user=true; }
  toast('Правки сохранены.','success'); await navigate();
}

async function downloadLetter(letterId) {
  if (state.mode==='api') { await downloadPrivateFile(`/api/v1/letters/${encodeURIComponent(letterId)}/file`, 'VisaMate-cover-letter.txt'); return; }
  const letter=state.letter || state.demo.letters.find(l=>l.id===letterId);
  if (!letter) return;
  const blob=new Blob([letter.content||''],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download='VisaMate-cover-letter.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
}

async function addEvent(form) {
  const data=Object.fromEntries(new FormData(form));
  const body={event_type:data.event_type,occurred_at:new Date(`${data.occurred_at}T12:00:00`).toISOString(),note:data.note||''};
  if (data.event_type==='decision_received' && data.outcome) body.outcome=data.outcome;
  if (state.mode==='api') await api.addEvent(form.dataset.id,body);
  else state.demo.events.unshift({id:`demo-event-${Date.now()}`,case_id:form.dataset.id,...body});
  toast('Событие добавлено вручную.','success'); await navigate();
}

init();
