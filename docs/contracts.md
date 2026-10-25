# VisaPilot — контракт backend и frontend

Статус: согласованный продуктовый объём, инженерная спецификация для реализации. Источник истины о HTTP после пакета 1 — генерируемый openapi.json, соответствующий этому документу. Изменения общих схем координирует владелец пакета 1.

## Общие соглашения

Префикс /api/v1. JSON snake_case, UUID-строки, даты YYYY-MM-DD, время ISO 8601 с часовым поясом, денежные суммы строкой и ISO-валюта. Неизвестное значение — null, а не false/0/пустая строка. Успешные ответы — объект ресурса; коллекции {"items": [...]}. Удаление — 204.

Ошибка: {"error":{"code":"...","message":"...","details":{}}}. Основные статусы: 401 вход отсутствует/истёк; 403 CSRF; 404 ресурс не найден или чужой; 409 конфликт/занято; 413 файл велик; 415 тип не поддержан; 422 некорректные поля; 503 AI недоступен, если нет содержательного локального результата. Не возвращать трассировку, токены и локальные пути.

Cookie-сессия, credentials=include. GET /auth/me возвращает user и csrf_token; авторизованные POST/PATCH/PUT/DELETE передают X-CSRF-Token. Регистрация/вход проверяют Origin и не используют старую сессию для смены пользователя. Сервер сам назначает владельца. В разработке /api проксируется на backend; на демо сайт и API открываются по одному адресу ноутбука.

## Маршруты и владельцы пакетов

| Пакет | Метод и путь после /api/v1 | Смысл |
|---|---|---|
| 1 | GET /health | Готовность приложения и признак demo_mode |
| 11 | POST /auth/register | username/password → user; затем обычный вход |
| 11 | POST /auth/login | username/password → user, csrf_token, Set-Cookie |
| 11 | GET /auth/me | Текущий пользователь и CSRF для frontend |
| 11 | POST /auth/logout | Отзыв сессии, очистка cookie |
| 2 | GET /destinations | 12 направлений, версии и качество источников |
| 2 | GET /scenarios/{scenario_id} | Публичная схема сценария, требования, источники, этапы |
| 3 | GET /profile | Профиль текущего аккаунта |
| 3 | PUT /profile | Изменить профиль, увеличить revision |
| 3 | POST /comparisons | trip + ответы → результаты по 12 странам; профиль из сессии |
| 4 | POST /cases | scenario_id + trip + answers → новое дело |
| 4 | GET /cases | Только дела текущего аккаунта |
| 4 | GET /cases/{case_id} | Дело, checklist, readiness, process, stale/revision |
| 4 | PATCH /cases/{case_id} | Изменить поездку/ответы; смена сценария — новое дело |
| 4 | DELETE /cases/{case_id} | Удалить дело, сохранить общий архив |
| 4 | PUT /cases/{case_id}/documents/{document_id} | Прикрепить свой документ; идемпотентно |
| 4 | DELETE /cases/{case_id}/documents/{document_id} | Отвязать, не удалять архив |
| 4 | PATCH /cases/{case_id}/checklist/{item_id} | Отметить разрешённое ручное действие/подтверждение |
| 5 | POST /documents | multipart file + document_type → запись личного архива |
| 5 | GET /documents | Только личный архив |
| 5 | GET /documents/{document_id} | Метаданные, extraction_state, fields и revisions |
| 5 | GET /documents/{document_id}/file | Авторизованное скачивание файла |
| 5 | POST /documents/{document_id}/extract | OCR/извлечение → fields и замечания качества |
| 5 | PATCH /documents/{document_id}/fields | Правка и подтверждение полей, facts_revision |
| 5 | DELETE /documents/{document_id} | Удалить файл/поля, отвязать от дел, инвалидировать результаты |
| 6 | POST /cases/{case_id}/checks | Выполнить актуальные проверки прикреплённых полей |
| 6 | GET /cases/{case_id}/checks | Последний результат, revisions и stale |
| 7 | PUT /cases/{case_id}/route | Сохранить участки и параметры пересадок |
| 7 | POST /cases/{case_id}/route/check | Проверить покрытые правила, вернуть неизвестные условия |
| 8 | POST /cases/{case_id}/letters | type=cover_letter → черновик/шаблон и недостающие факты |
| 8 | GET /cases/{case_id}/letters | Сохранённые черновики своего дела |
| 8 | PATCH /letters/{letter_id} | Пользователь редактирует текст |
| 8 | GET /letters/{letter_id}/file | Скачать .txt через проверку владельца |
| 9 | POST /cases/{case_id}/events | Добавить ручное событие, проверить допустимость исхода |
| 9 | GET /cases/{case_id}/events | Хронология и источник каждого события |
| 9 | GET /cases/{case_id}/notifications | Внутренние уведомления и следующие действия |

GET /destinations, GET /scenarios/{id}, GET /health доступны без входа. Все остальные маршруты требуют сессию. OpenAPI — /openapi.json, интерактивная документация — /docs. Эти адреса не являются frontend-страницами продукта.

## Общие модели

**User:** id, username, created_at. Без password_hash/session_hash.

**ApplicantProfile:** id, revision, given_name, surname, birth_date, citizenship, residence_country, has_other_citizenship, has_foreign_residence_permit, passport_kind, passport_issued_on, passport_expires_on, employment_status, existing_permissions. Даты/ответы могут быть null до заполнения. Указание другого гражданства/ВНЖ возвращает границу поддержки, а не молчаливую подмену.

**TripInput:** purpose, destination_country, starts_on, ends_on, departure_city, application_country, accommodation_type, funding_type. Детали и допвопросы — answers с ключами, определёнными схемой сценария. Не принимать произвольные исполняемые правила от клиента.

**Scenario:** id (например FR_TOURISM), destination_country, version, entry_mode (visa/evisa/visa_free/needs_verification), supported_profile, requirements, process_stages, sources, data_quality. entry_mode заполняется после проверки источника; неизвестное не выдаётся за установленный факт.

**Rule:** id, version, kind, applies_if, requirement_id, parameters, mandatory, source_ids. Подробный набор параметров задаётся ограниченным реестром Python-проверок в пакете 2.

**Case:** id, scenario_id, owner определяется сервером, trip, answers, revision, profile_revision, ruleset_version, attached_document_ids, checklist, readiness, process, created_at, updated_at.

**Document:** id, document_type (passport/bank_statement/insurance), original_filename, mime_type, size_bytes, sha256, extraction_state (uploaded/extracted/needs_input/failed), facts_revision, fields, quality_findings, created_at. Файловый путь и полный сырой OCR-текст не выдаются в списке архива.

**Field:** key, value, confidence (число или null), source_page, source_kind (pdf_text/ocr/manual), user_confirmed, confirmed_revision. PDF-текст не получает выдуманную OCR-уверенность 100%.

**ChecklistItem:** id, requirement_id, kind, mandatory, applicability (yes/no/unknown), readiness_contributing, state, document_ids, verification_method, findings, explanation, source_refs, next_action.

**Finding:** code, severity, message, next_action, document_ids, field_paths, rule_ref (id/version либо null), origin (rule/heuristic/technical), resolved. Для heuristic обязателен явный признак эвристики.

**Readiness:** percent (int/null), required_total, satisfied_required, ready, unknown_required_conditions, blocking_count, reasons. **Process:** percent (int/null), stages, current_stage, outcome; вычисляется отдельно.

**CheckResult:** id, case_id, evaluated_at, input_signature, findings, stale. Подпись включает case_revision, profile_revision, facts_revision всех документов, ruleset_version и версию алгоритма.

**Route:** legs и transfers. Transfer содержит country, airport, arrival_at, departure_at, ticket_type, leaves_airside, rechecks_baggage, permissions. Результат: status (no_known_blockers/blocked/needs_verification), findings, covered_countries, uncovered_countries, flight_availability=not_checked. no_known_blockers не называется гарантией допустимости перелёта.

**Letter:** id, case_id, type, language=en, content, missing_facts, generation_mode (ai/template), from_cache, input_signature, stale, edited_by_user, created_at. Новая генерация не перезаписывает молча пользовательский текст.

**Event:** id, case_id, event_type, occurred_at, received_at, source=manual, note, outcome (при применимости). Типы: form_prepared, appointment_booked, submitted, under_review, additional_documents_requested, decision_received, passport_returned, entry_preparation_completed. Шаблон определяет применимые типы/этапы.

## Внутренние интерфейсы между пакетами

- Пакет 11: require_user(request) и require_csrf(request); возвращает user_id без передачи credentials другим модулям.
- Пакет 2: get_scenario(id), get_ruleset(version), get_sources(ids); возвращает проверенные Pydantic-модели и качество данных.
- Пакет 3: evaluate_applicability(profile, trip, answers, scenario) → применимость, требования, барьеры, недостающие ответы.
- Пакет 4: rebuild_case(case_id, reason) → чек-лист и прогрессы; проверяет ownership на HTTP-границе и использует внутренний owner-контекст.
- Пакет 5: get_confirmed_facts(owner_id, document_ids), extract_document(id), AIProvider.generate(task, facts, output_schema) → структурированный результат или явная недоступность.
- Пакет 6: check_case(snapshot) → CheckResult; не изменяет правила и исходные документы.
- Пакет 7: check_route(profile, route, ruleset) → результат с неизвестными условиями.
- Пакет 8: generate_cover_letter(confirmed_snapshot) → черновик и список недостающих фактов.
- Пакет 9: project_process(events, process_template) → этапы/исход; не подменяет результаты проверок пакета 6.

Пакеты 4/6/9 могут разрабатываться параллельно по этим интерфейсам: сначала структуры и тестовые ответы, затем реальные вызовы. Пустая реализация не должна возвращать успешный статус готовности.

## Конкретизация пакета 1

Общие Pydantic-модели: `app/common/models.py`, протоколы:
`app/common/interfaces.py`. Внутренний owner-контекст — UserContext(user_id),
передаваемый явно при пересчёте/извлечении личных данных. InputSignature
содержит `facts_revisions: {document_id: facts_revision}`, case_revision,
profile_revision, ruleset_version, algorithm_version; дополнительные версии
маршрута/шаблона/модели предусмотрены отдельными nullable-полями.
Правка полей документа передаёт expected_facts_revision и список
`{key, value, user_confirmed}`; сервер назначает новую ревизию/происхождение.
Коллекции — Items[T]. Создание аккаунта/дела/документа/письма/события — 201;
logout и все DELETE — 204 без тела.

После пакета 11 работают health и все четыре `/auth/*`. У auth-маршрутов
`x-implementation: implemented`; остальные имеют `pending`. Без сессии
защищённые маршруты возвращают 401; без CSRF на изменении — 403; после
прохождения авторизации ещё не реализованный пакет возвращает
`503 module_not_ready`. Его успешная схема фиксирует целевой контракт.
Снимок контракта — корневой `openapi.json`; регенерация:
`python -m app.common.export_openapi`. Интеграция и примеры:
[backend-handoff.md](backend-handoff.md).

## Конкретизация пакета 11

Username: 3–64 ASCII-символа, первый — буква/цифра, остальные — буквы,
цифры, `_`, `.`, `-`; регистр не различается, сервер возвращает нижний регистр.
Пароль — 8–128 символов, не нормализуется и не возвращается. Регистрация
создаёт аккаунт и пустой профиль с неизвестными ответами, не выполняет вход.
Дубликат username — 409 username_taken. Неверный пароль/неизвестный username
дают одинаковую ошибку 401 invalid_credentials.

Регистрация и login требуют разрешённый заголовок Origin; отсутствие,
`null`, чужой origin или повтор заголовка — 403 origin_not_allowed.
Список задаёт VISAPILOT_ALLOWED_ORIGINS, без wildcard. Схема, host и порт
сравниваются с учётом стандартных портов HTTP(S); Host/X-Forwarded-* не
заменяют разрешённый список. Для dev proxy указать origin браузерного
frontend, для LAN — origin ноутбука с портом.

Cookie visapilot_session: host-only, Path=/api/v1, HttpOnly, SameSite=Lax,
Max-Age=43200; Secure зависит от конфигурации HTTPS. Каждый успешный login
создаёт новый случайный токен и отзывает предъявленную старую сессию;
регистрация и неуспешный вход не переключают текущего пользователя.
В SQLite хранится SHA-256 bearer-токена, срок и отметка отзыва. CSRF связан
с сессией и восстанавливается на /me без нового server secret; в БД — его
хеш. Auth-ответы с данными имеют Cache-Control: no-store.

require_csrf сначала проверяет сессию (401), затем X-CSRF-Token (403).
Переданный Origin дополнительно сверяется; для CLI-запроса с валидным CSRF
он необязателен. Logout требует CSRF, отзывает сессию, удаляет cookie с тем
же Path и возвращает 204. Просроченную/отозванную cookie повторно использовать
нельзя; срок 12 часов абсолютный, /me его не продлевает.

Зависимости доступны из app.auth.dependencies и прежнего
app.common.dependencies. Они автоматически добавляют cookie-security в
OpenAPI и возвращают UserContext(user_id). require_owned и
require_owned_case_document проверяют владельца внутри соединения/транзакции;
чужой и отсутствующий ресурс дают одинаковые 404. HTTP-модули 3–9 должны
использовать эти проверки до чтения/изменения/скачивания.
Примеры и границы интеграционной проверки: [auth-handoff.md](auth-handoff.md).

## Конкретизация пакета 2

GET /destinations и GET /scenarios/{scenario_id} реализованы для 12 направлений.
Source дополнен attempted_at, locator, evidence и notes. source_checked требует
фактических checked_at, review_after и evidence; неудачная попытка не становится
датой юридической проверки. Просроченный источник и зависимый от него режим
въезда читаются как needs_verification. Scenario дополнен entry_source_ids,
questions, coverage и unverified_conditions. Все текущие сценарии partial.

Question описывает только определённый ключ answers, его тип/ограничения и
источники. AppliedRequirement расширяет Requirement применимостью,
explanation и rule_refs для пакета 3. Занятость: employed/self_employed/student/
retired/unemployed/other; финансирование: self/sponsor/mixed/other; размещение:
hotel/host/rental/other, каждый ответ также допускает null.
Реестр/параметры проверок, источники и порядок обновления:
[rules-handoff.md](rules-handoff.md). Официальная полнота не подтверждается
одним наличием карточки страны или работающим endpoint.

## Конкретизация пакета 3

GET /profile возвращает профиль с id/revision. PUT /profile принимает только
ProfileInput и полностью заменяет поля; отсутствующее становится null.
Повтор идентичного ввода сохраняет revision, изменение увеличивает её.
В одной транзакции собственные cases получают stale/profile_changed и
projection_json=NULL, связанные check_results/routes/letters — stale=1.
Старые подписи, подтверждения с их подписями, документы, события и правки
писем сохраняются до явного пересчёта/сопоставления с новой ревизией.

POST /comparisons использует текущий профиль из сессии и возвращает
Items[DestinationComparison] для всех 12 направлений, без сортировки по
«лёгкости». Добавлено обязательное profile_revision. known_required_documents
считает известные применимые обязательные документы; required_documents
остаётся null при неполном/неизвестном списке, requirements_complete=false.
external_actions, route_notes и unknown_conditions — отдельные факторы.
Профиль и сравнения имеют Cache-Control: no-store; PUT/POST требуют CSRF.

ApplicabilityResult.status: applicable/blocked/needs_input/unsupported_profile/
needs_verification. Неподдерживаемая или неизвестная базовая область не
получает персональный список требований. Состав требований определяется
правилами, не AI. Неизвестное обязательное условие не становится pass;
источник без подтверждения не доказывает барьер. Заявленная применимость
не заменяет проверку документных фактов и готовность дела.

answers принимает только типизированные questions выбранного сценария;
в /comparisons — объединение questions активного каталога. Неизвестный ключ
или неверный тип даёт 422 invalid_answers. Детали, JSON-примеры, дополнительные
вопросы и порт evaluate_applicability: [profile-handoff.md](profile-handoff.md).
Внутренние get_scenario/get_sources допускают keyword version для чтения
сохранённой версии вместе с get_ruleset(version).

## Конкретизация пакетов 4/9 и общего запуска

CRUD /cases, привязка/отвязка документов, ручные отметки checklist,
events и notifications реализованы в общем app.main. Архив 5 тоже
подключён по умолчанию, отдельная фабрика совместимо делегирует общей.
Case дополнен необязательным applicability, ChecklistItem — title.
PATCH trip/answers объединяет переданные поля/ключи; null внутри объекта
означает неизвестное, null вместо самого объекта — 422. Идентичная запись
не увеличивает ревизию. Детали подписей/переоценки — в handoff ниже.

Ручной checklist confirmed разрешён только для применимого
preparation_action. Документ требует подтверждённых ключей и актуальной
проверки 6. Без неё нет фиктивного satisfied; HTTP checks/route/letters
пакетов 6/7/8 по-прежнему честно возвращают 503. Пустой/неизвестный
знаменатель готовности — null, ready=false. Все реальные наборы partial.

EventCreate.note ограничен 4000 символами. occurred_at не может быть в
будущем, требует часовой пояс. source/id/received_at/case_id назначает сервер.
decision_received требует approved/refused; outcome у иных событий запрещён.
passport_returned сам не задаёт исход и не подтверждает ранние этапы.
В безвизовой ветке нет submitted/decision_received. Notifications —
текущие внутренние сообщения/действия, без внешней доставки.

ProcessProjector принимает необязательные context/unverified_source_ids
для условий и качества этапов. CaseService.snapshot/record_check — порт
будущей проверки 6; поздняя подпись даёт 409. Общий AIProvider доступен
через app.state.ai_provider, по умолчанию выключен без startup-вызова.

Готовый frontend может обслуживаться тем же сервером из frontend/dist или
VISAPILOT_FRONTEND_DIST. При отсутствии сборки / открывает /docs;
эти веб-маршруты не входят в API-снимок. Приватные данные не публикуются
как статика. Полные примеры и границы готовности:
[cases-events-handoff.md](cases-events-handoff.md).

## Конкретизация пакетов 6/8 и интеграции 10

В общем app.main теперь подключены реальные checks и letters. Пакет 7 остаётся
pending/503. Проверки используют единый CaseService.snapshot/record_check,
algorithm_version=documents-1 и route_revision; после изменения входов старый
результат не становится актуальным. Все реальные страны пока partial.

Письма: POST/GET /cases/{case_id}/letters, PATCH /letters/{letter_id} и
GET /letters/{letter_id}/file реализованы с ownership, CSRF для записи и no-store.
PATCH content — 1–32000 символов. POST возвращает 201 и новый ID либо актуальную
неизменённую версию с from_cache=true. Правки пользователя не перезаписываются.
Список упорядочен от старого к новому. Stale-письмо можно экспортировать как
сохранённую версию; download сообщает X-Letter-Stale и UTF-8 text/plain attachment.
Версия шаблона/модель включены в algorithm_version подписи письма letters-1-…;
остальные ревизии совпадают с входами дела. Даже при совпадающей подписи
stale запрещает кэш. По умолчанию AI отключён, template обозначен явно.

Frontend обслуживается из frontend/dist, затем frontend/, либо явного
VISAPILOT_FRONTEND_DIST. Полные границы, AI-композиция и передача Ване:
[letters-integration-handoff.md](letters-integration-handoff.md).
Команды отдельного демокабинета и воспроизводимые проверки: [runbook.md](runbook.md).
