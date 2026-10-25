# Общая основа и интеграция: пакеты 1, 11, 2, 3, 4, 5 и 9

Пакеты 1/11/2/3 и архив 5 объединены в main, полученный на eab9a2e.
Ветка `matvey/04-09-cases-events` добавляет дела/хронологию и подключает
архив Вани к общей фабрике приложения. Переносить ветку целиком, включая
общие модели, main, зависимости, OpenAPI и скрипт запуска.
Инструкции: [auth-handoff.md](auth-handoff.md),
[rules-handoff.md](rules-handoff.md), [profile-handoff.md](profile-handoff.md),
[cases-events-handoff.md](cases-events-handoff.md).
`.idea`, окружение, `.env`, базы и файлы архива не входят в коммиты.

## Фактическая готовность

Работают `GET /api/v1/health`, `/docs`, `/openapi.json`, lifespan/init SQLite,
короткие транзакции и единый формат ошибок, регистрация/login/me/logout,
публичные destinations/scenarios, личные GET/PUT profile и POST comparisons,
CRUD дел, привязки/чек-лист, архив/извлечение/подтверждение полей,
ручные события и notifications. Процессы и готовность разделены.
Их маршруты имеют `x-implementation: implemented`; остальные маршруты из
`contracts.md` зарегистрированы с типизированными входами/ответами и меткой
`pending`. После входа и CSRF они возвращают 503 до реализации своего пакета.
Без сессии защищённый запрос получает 401, без CSRF на изменении — 403.
Успешная схема pending-маршрута в Swagger описывает будущий ответ.
Сценарии содержат проверенные условия с частичным покрытием и явными
неизвестными. Движок проверок документов 6, маршрут 7 и письма 8 пока
отсутствуют; без проверки 6 файл не получает rules_checked. PDF/OCR-адаптер
Вани подключён, для живого OCR нужен Tesseract rus+eng. AI opt-in, выключен
по умолчанию; startup не вызывает CLI.

Окружение первого запуска: CPython 3.12.15; все фактически установленные
версии, включая тестовые и транзитивные зависимости, — в `requirements.lock`.
На ноутбуке Матвея интерпретатор для `.venv` установлен локально в
исключённый из Git `.runtime/`, отдельно от сбрасываемых демоданных.
При добавлении модулей не перезаписывать lock случайным окружением:
обновить ограничения, установить зависимости, проверить и только затем
пересоздать lock. pwdlib/Argon2 установлен пакетом 11; зависимости 5/AI теперь
в корневом manifest/lock. PDF-извлечение проверено через общий HTTP-сервер;
живой OCR пропущен без Tesseract, живая CLI-проба в этой задаче не выполнялась.

## Общие Python-модели

Импорт: `from app.common.models import ...`. Модели запрещают лишние поля;
публичные входы не содержат owner_id. Идентификаторы ресурсов — UUID,
время — AwareDatetime, даты — date, суммы Money — десятичные строки,
неизвестные ответы — None. Булевы ответы не принимают строки/числа.

| Группа | Модели |
|---|---|
| Аккаунт | User, Credentials, SessionResponse, UserContext (внутренняя) |
| Анкета | ProfileInput, ApplicantProfile, ExistingPermission, TripInput |
| Правила | Source, RuleRef, Rule, Requirement, Scenario, Ruleset, Destination |
| Условия | ComparisonCondition, AllCondition, AnyCondition; без исполнения кода |
| Применимость | ApplicabilityResult, ComparisonInput, DestinationComparison |
| Архив | Document, Field, FieldUpdate, DocumentFieldsPatch, ConfirmedDocumentFacts |
| Дело | CaseCreate, CasePatch, Case, ChecklistItem, ChecklistPatch, Readiness, Process, ProcessStage |
| Проверки | Finding, InputSignature, CheckResult, CaseSnapshot |
| Маршрут | Route, RouteLeg, Transfer, RouteResult |
| Тексты и события | LetterCreate, LetterPatch, Letter, LetterDraft, EventCreate, Event, Notification, AIResult |
| Общие | Items[T], ErrorResponse, Error, Health, Money, ProcessStageTemplate |

Перечисления: DocumentType, ExtractionState, Severity, EntryMode,
DataQuality, Applicability, ChecklistKind, ChecklistState, Outcome, EventType.
У правила kind/parameters ограниченный реестр и семантическую проверку
реализует пакет 2. У answers ключи проверяет схема сценария, а не общий
JSON-контейнер; сравнение принимает объединение questions активных сценариев.
Добавлены Question, AppliedRequirement, статус применимости blocked,
сведения о проверке источников и полноте. DestinationComparison содержит
profile_revision; неизвестное полное число документов остаётся null.

`InputSignature.facts_revisions` — отображение document_id → facts_revision;
также обязательны case_revision, profile_revision, ruleset_version,
algorithm_version. Для маршрута/писем предусмотрены route_revision,
template_version и model. `CaseSnapshot` — внутренний снимок, не HTTP-вход.
Пакет 5 возвращает в ConfirmedDocumentFacts только поля, подтверждённые на
текущей facts_revision. Для правки полей клиент передаёт
expected_facts_revision; несовпадение должно давать 409 в пакете 5.

## Подключение модулей Вани

Протоколы без реализации находятся в `app/common/interfaces.py`:
AuthGateway, RuleRegistry, ApplicabilityEvaluator, CaseRebuilder,
DocumentService, AIProvider, CaseChecker, RouteChecker, LetterGenerator,
ProcessProjector. IO с OCR/AI — async; детерминированные проекции — sync.
Владелец передаётся явно в операциях с личными данными.

Матвей подключает готовый APIRouter через `create_app` в `app/main.py`:

```python
app = create_app(
    feature_routers={5: documents_router, 6: checks_router},
)
```

Роутер модуля содержит пути `/documents`, `/cases/{case_id}/checks` и т. п.
без `/api/v1`; префикс добавляет composition root. Роутер целиком заменяет
контрактный роутер соответствующего пакета; дубликаты путей не подключать.
Пакеты 11/2/3/4/5/9 подключены по умолчанию; 6/7/8 без роутера недоступны.

Готовые порты: `app.state.rule_registry` и `app.state.applicability_evaluator`.
Последний оценивает только заявленные профиль/поездку, не подтверждает
документы. `app.state.profile_service.get(owner)` возвращает текущую ревизию.
PUT профиля атомарно сбрасывает проекции собственных дел и помечает связанные
checks/routes/letters stale. Пакет 4 должен пересчитать перед показом
готовности; старые input_signature и ручные тексты сохраняются.
CaseService уже выполняет пересчёт при чтении/изменении, подписывает ручные
отметки и подключён как callback архива. Порт snapshot/record_check для 6,
общий AIProvider и изменения ProcessProjector описаны в cases-events-handoff.

Для защищённых операций модуль использует `Depends(require_user)` и
`Depends(require_csrf)` из `app.common.dependencies`; это мост к пакету 11.
Зависимости возвращают UserContext, а не credentials. Реальный AuthService
пакета 11 подключён по умолчанию: 401 без сессии и 403 при ошибке CSRF.
Те же импорты доступны из `app.auth.dependencies`; там же находятся общие
require_owned/require_owned_case_document. Детали — в auth-handoff.md.
В SQL чтения/изменения фильтровать **id и owner_id**, для вложенных ресурсов
проверять дело и документ. Чужой ID — AppError(404, "not_found", ...).

БД и настройки доступны через `request.app.state.database/settings`;
`get_database` — готовая FastAPI-зависимость. Для ошибки использовать
AppError(status_code, code, message, details), без секретов и локальных путей.

```python
with database.connection() as connection:  # чтение, соединение закрывается
    row = connection.execute(
        "SELECT * FROM documents WHERE id = ? AND owner_id = ?",
        (str(document_id), str(owner.user_id)),
    ).fetchone()

with database.transaction() as connection:  # BEGIN IMMEDIATE / commit / rollback
    ...  # только короткие записи, без await, OCR, subprocess и AI
```

Каждое соединение включает foreign_keys и busy_timeout; БД использует WAL.
DDL и версия PRAGMA user_version=1 — в `app/common/schema.sql`/`db.py`.
Новая версия схемы требует явной миграции владельцем, а не удаления БД.
JSON перед записью валидировать общей моделью и сохранять model_dump_json().
В sessions сохраняются только хеш bearer-токена, хеш CSRF и сроки отзыва;
для восстановления CSRF на /me пакет 11 использует HMAC-SHA256 со случайным
cookie-токеном и сверяет хеш. Схема БД v1 не менялась, миграция не требуется.

Схема содержит 12 таблиц из domain-model.md. Составной FK в case_documents
запрещает чужую привязку даже при ошибке сервиса. Удаление дела удаляет связи
и результаты, сохраняя документы; удаление документа снимает связи.
**Физическое удаление файла, инвалидация проверок/писем/маршрута и пересчёт
дел выполняются сервисами пакетов 4/5.** Каскад FK не заменяет эти действия.
Начальные projection_json/result_json — NULL, stale=1; это не готовность.

Закрытые каталоги создаются при старте: `local_data/uploads` и `local_data/tmp`.
Статика из них не публикуется. storage_key/raw_text не входят в публичный
Document. Пакет 5 генерирует имя хранения и проверяет принадлежность перед
скачиванием; исходное имя файла не является путём.

`request.app.state.processing` содержит общие documents/ai WorkGate и
pdfium_mutex. Синхронную долгую работу вызывать так:

```python
result = await request.app.state.processing.documents.run(process_file)
```

WorkGate выполняет работу в потоке и отвечает 409 busy конкурентному запросу.
Отмена HTTP-запроса не освобождает слот до завершения рабочего потока.
PDFium-операции дополнительно защищать pdfium_mutex. Тайм-ауты/завершение
OCR-процесса/CLI, извлечение и валидация JSON реализуются пакетом 5: поток
нельзя считать остановленным только из-за asyncio timeout. Использовать
один Uvicorn worker; Redis/фоновые очереди не добавлять.

## Подключение frontend

Адрес backend: `http://127.0.0.1:8000`. В разработке frontend проксирует `/api`
на этот адрес; в демо браузер обращается к одному origin ноутбука.
CORS не включён. Для LAN запускать с `--host 0.0.0.0`, открывать адрес
ноутбука и добавить этот **origin с портом** в VISAPILOT_ALLOWED_ORIGINS.
Origin обязателен для входа/регистрации; при dev proxy в список добавляется
origin frontend (например http://localhost:5173), а не только backend.

Рабочий запрос сейчас:

```javascript
const response = await fetch('/api/v1/health', { credentials: 'include' });
const payload = await response.json();
if (!response.ok) throw payload.error;
// {status: 'ok', demo_mode: true, database: 'ok', schema_version: 1}
```

Рабочие запросы пакета 11 (браузер сам передаёт Origin для POST):

```javascript
const response = await fetch('/api/v1/auth/login', {
  method: 'POST', credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username, password }),
});
const payload = await response.json();
if (!response.ok) throw payload.error;
let csrfToken = payload.csrf_token;

// Восстановить user/csrfToken после обновления страницы:
const meResponse = await fetch('/api/v1/auth/me', { credentials: 'include' });
const me = await meResponse.json();
if (!meResponse.ok) throw me.error;
csrfToken = me.csrf_token;

const logout = await fetch('/api/v1/auth/logout', {
  method: 'POST', credentials: 'include',
  headers: { 'X-CSRF-Token': csrfToken },
});
// У 204 нет JSON-тела. Тот же заголовок нужен POST/PATCH/PUT/DELETE кабинета.
```

Регистрация выполняется отдельным POST /api/v1/auth/register с тем же телом;
после 201 нужно выполнить login. Username/пароль и ошибки описаны в auth-handoff.md.
При 401 frontend открывает вход; при 403 обновляет контекст сессии;
при 503 показывает error.message. Коллекции имеют `{items: [...]}`,
unknown остаётся null; null percent нельзя показывать как 0% или 100%.

## Проверки перед main

Из корня, в активном Python 3.12 окружении:

```bash
python -m pytest -q
python -m pip check
python -m app.common.export_openapi --check
python tests/auth_http_smoke.py
python tests/rules_profile_http_smoke.py
python tests/integration/cases_events_http_smoke.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
curl http://127.0.0.1:8000/api/v1/health
curl -i http://127.0.0.1:8000/api/v1/destinations
```

Тесты проверяют запуск, все методы/пути contracts.md, схемы и ошибки,
сохранность SQLite при повторном lifespan, rollback, foreign_keys/WAL,
изоляцию привязок на уровне БД, удаление дела без потери архива,
неизвестные значения и работу/отмену долгой операции вне HTTP event loop.
Проверки auth включают реальные пароли, сессии/CSRF, logout, ownership и
отдельный smoke с перезапуском Uvicorn. Изоляция скачивания/привязки проверена
с тестовыми HTTP-адаптерами; настоящий стык 11+4/5 повторяется после появления
их роутеров. Реальный стык 11+4/5 теперь также проверен общим HTTP smoke.
Для 2/3 проверены все 12 сценариев, повреждённые определения,
версии/истечение срока источников, условные требования, null/false,
календарные сроки/история поездок, изоляция профилей и инвалидация снимков.
Отдельный реальный HTTP smoke проверяет профиль/сравнение и перезапуск.
Дела/события 4/9 реализованы; 6/7/8 ещё отсутствуют. Проверки 4/9 покрывают
изоляцию, два дела с одним паспортом, независимые прогрессы, отказ/возврат,
безвизовые события, смену версий и запрет публикации устаревшего расчёта.

В выбранных версиях Starlette сообщает deprecation для TestClient с httpx;
pytest работает, предупреждение не скрыто. httpx сохранён по инженерной
спецификации; миграция тестового клиента не нужна для пакета 1.

## Обновление после пакетов 8/10 — 25 октября 2026

Общий app.main теперь также подключает реальные проверки 6 и письма 8,
а существующий frontend обслуживается тем же origin. Пакеты 4/9 уже были
в main (`2c2dde3`). Старые разделы о pending 6/8 выше описывают прежнюю передачу.
Пакет 7 остаётся за Ваней, endpoints route честно отвечают 503.
Актуальные изменения подписи checker, API писем, AI-проба и список стыков:
[letters-integration-handoff.md](letters-integration-handoff.md).
Отдельные SAMPLE-данные, установка, запуск/остановка и маркированный reset:
[runbook.md](runbook.md). Исходная БД пользователя сохраняется.
