# Пакеты 4 и 9: дела, чек-лист, процесс и интеграция

Основа ветки `matvey/04-09-cases-events` — актуальный `origin/main` eab9a2e,
полученный fast-forward до начала реализации. Там уже объединены 2/3 и
архив Вани (5). Локальные чужие правки IDE/планов сохранены отдельно от
коммитов задачи. SQLite остаётся v1, существующие аккаунты и архив сохраняются.

## Реальные связи пакетов

Обычный запуск теперь один: `./scripts/run.sh`, то есть
`python -m uvicorn app.main:app --workers 1`. Старая фабрика
`app.documents.application:create_app --factory` делегирует общей и остаётся
совместимой с тестами/инъекциями Вани. Зависимости 5 и AI-адаптера перенесены
в корневые pyproject.toml/requirements.lock; отдельные серверы не нужны.

| Пакет | Реальная связь / состояние |
|---|---|
| 11 | Все личные маршруты используют сессию, изменения — CSRF; владелец только серверный |
| 2/3 | CaseService читает текущий каталог, профиль и применимость; Case.applicability содержит причины и вопросы |
| 4 | `app.state.case_service` и `case_rebuilder` — один CaseService |
| 5 | `app.state.document_archive` / `document_service` — один DocumentArchive; его rebuilder указывает на CaseService |
| 6 | Реализации пока нет; `case_checker=None`, HTTP checks=503, положительные проверки не подставляются |
| 7/8 | В main пока нет; маршруты/письма=503, сохранённые производные помечаются stale при изменениях |
| 9 | `event_service` и `process_projector`; процесс вычисляется отдельно от готовности документов |
| AI | Общий `app.state.ai_provider`, opt-in CodexCLIProvider; default disabled, без вызова на старте и без платного fallback |

При подключении 6 владелец передаёт `create_app(case_checker=checker,
feature_routers={6: checks_router})`. `checker.check_case(snapshot)` —
синхронная детерминированная функция без OCR/AI внутри транзакции.
CaseService передаёт подтверждённые факты 5 и полную InputSignature с
algorithm_version=cases-v1. Ответ должен вернуть эту же подпись, свой UUID,
case_id, evaluated_at, findings и stale=false. Finding с origin=rule требует
реальных rule_ref/version/source_refs текущего сценария; document_ids —
только прикреплённые документы этого снимка.

Для явного HTTP-запуска проверки в пакете 6:

```python
snapshot = request.app.state.case_service.snapshot(owner, case_id)
result = checker.check_case(snapshot)
request.app.state.case_service.record_check(owner, result)
case = request.app.state.case_service.rebuild_case(owner, case_id, "checks_changed")
```

`snapshot` и `record_check` проверяют ownership. Запоздавший результат с
иной подписью получает 409. OCR/AI/проверка выполняются вне транзакции:
перед публикацией сервис повторно сверяет профиль, дело, состав/ревизии
документов, отметки, события, маршрут и последнюю проверку. Изменение входов
во время расчёта даёт 409 input_changed и не публикует старую готовность.

## API дел

Работают POST/GET /cases, GET/PATCH/DELETE /cases/{case_id},
PUT/DELETE /cases/{case_id}/documents/{document_id} и
PATCH /cases/{case_id}/checklist/{item_id}. Все пути имеют `/api/v1`.
Ответы личных маршрутов — no-store. Для чужого/несуществующего ресурса —
одинаковый 404; при привязке проверяются оба владельца и составной FK.

После login/me сохранить csrf_token. Создание (вымышленная поездка):

```http
POST /api/v1/cases
Content-Type: application/json
X-CSRF-Token: <из login/me>

{
  "scenario_id": "FR_TOURISM",
  "trip": {
    "purpose": "tourism", "starts_on": "2027-01-20", "ends_on": "2027-01-31",
    "application_country": "RU", "accommodation_type": "hotel", "funding_type": "self"
  },
  "answers": {"schengen_prior_stays": []}
}
```

Страна назначения выставляется по сценарию; явное противоречие — 422.
PATCH trip объединяет только переданные поля с прежней поездкой;
answers объединяет ключи, значение null означает неизвестный ответ.
Пустой PATCH/идентичная привязка не увеличивают revision. Поля даты
валидируются после объединения. Новый сценарий требует нового дела.

```http
PUT /api/v1/cases/<case_id>/documents/<свой document_id>
X-CSRF-Token: <из login/me>
```

Паспорт можно прикрепить к нескольким своим делам. Проверка и готовность
всегда относятся к конкретным датам/сценарию и InputSignature. Удаление
дела сохраняет документ в архиве; detach сохраняет оригинал. Правка,
повторное извлечение и удаление документа в 5 инвалидируют результаты
всех связанных дел и вызывают настоящий rebuild_case. При ошибке callback
архив сохраняет безопасное stale/NULL, GET дела повторяет пересчёт.

Изменение поездки/ответов, документов или профиля отменяет старые
check_results/routes/letters и проекцию; пользовательские тексты и подписи
не стираются. Отметки сохраняют прежние подписи и требуют подтверждения
после изменения входов. profile_revision/ruleset_version дела обновляются
после успешного пересчёта. Смена активных правил учитывается при GET;
старый каталог остаётся доступен для воспроизведения прежних оснований.
Удалённый/изменивший тип вопрос не ломает чтение: значение не применяется
к новой норме, появляется answers_schema_changed. Исходный JSON сохраняется
до следующего редактирования; frontend получает совместимые ответы и
сохраняет их после проверки новой анкеты.

## Готовность и ручные отметки

ChecklistItem дополнен title. Case дополнен applicability (совместимое
необязательное поле общей модели). Пункты используют стабильный id требования.
Загруженный файл не даёт satisfied: нужны подтверждённые на текущей ревизии
ключи пакета 5 и актуальная проверка 6 без относящихся к пункту проблем.
Без 6 подтверждённый файл остаётся needs_verification.

Ручное `{"confirmed":true}` разрешено для применимого preparation_action.
Документ, неизвестное требование и process_action нельзя закрыть этим путём;
внешние этапы отмечаются событиями. Blocking/fix не маскируются отметкой.

В знаменатель входят только mandatory + readiness_contributing + yes;
рекомендованное и process_action не учитываются. Процент — целая часть
100 × satisfied / required. Пустой или неизвестный набор даёт null и
ready=false. Неизвестные обязательные условия/поля/покрытие и blocking/fix
не разрешают ready=true даже при 100% известных пунктов. Все реальные
сценарии сейчас partial, поэтому полная готовность не объявляется.
Положительный 100% путь проверен только явно обозначенной полной
контрактной фикстурой, которая не включена в приложение/данные.

## Хронология и процесс

Работают POST/GET /cases/{case_id}/events и
GET /cases/{case_id}/notifications. EventCreate принимает event_type,
occurred_at с часовым поясом, note до 4000 символов и outcome при решении.
Сервер назначает source=manual, id, case_id и received_at UTC. События
выдаются по фактическому occurred_at, затем received_at/id; будущая дата
и тип вне шаблона отклоняются. Неизвестная область заявителя требует анкеты.

```json
{"event_type":"passport_returned","occurred_at":"2026-10-25T10:00:00Z","note":"SAMPLE: получен паспорт"}
```

Исход остаётся unknown. Для решения нужен явно известный исход:

```json
{"event_type":"decision_received","occurred_at":"2026-10-25T09:00:00Z","outcome":"refused"}
```

approved/refused допустимы только у decision_received; неизвестное решение
не подменяется одобрением. Поздний passport_returned сохраняет предыдущий
refused. При нескольких решениях действует последнее по occurred_at, а не
по времени ввода. Событие не закрывает молча более ранние этапы.

Для BR/AR/ZA допустим entry_preparation_completed, outcome=not_applicable;
фиктивные submitted/decision_received отклоняются. Прогресс процесса —
целая часть 100 × completed / applicable stages. Неизвестная применимость
этапа/источника или пустой шаблон дают percent=null. Истечение срока
источника не превращается в подтверждённое завершение.

```python
from app.events.projection import project_process
process = project_process(events, scenario.process_stages, context={
    "profile": profile.model_dump(), "trip": trip.model_dump(), "answers": typed_answers,
})
```

Контекст нужен условным этапам. Порт ProcessProjector расширен необязательными
context/unverified_source_ids; без контекста условие остаётся unknown.
Сервис дела передаёт реальное качество источников. Ручное событие меняет
проекцию процесса, сохраняя актуальную проверку документов. Письма при
событии/ручной отметке становятся stale: будущий пакет 8 обязан учитывать
этот флаг и содержимое снимка при кешировании, даже если подпись документных
входов не изменилась. Текст/edited_by_user при этом сохраняются.

Notifications — текущие замечания, недостающие ответы, следующие действия
и последние 10 ручных событий. Нет email/SMS/push/scheduler/ack доставки;
это список внутри кабинета, не обещание внешнего консульского статуса.

## Веб-запуск и frontend

В репозитории пока нет продуктового frontend. `/` открывает рабочий Swagger.
Владелец UI собирает сайт в frontend/dist либо задаёт
VISAPILOT_FRONTEND_DIST. Uvicorn отдаёт готовую SPA, ресурсы и API с одного
origin, включая вложенные страницы после перезагрузки. Приложение запрещает
использовать data_dir как публичный каталог. API/private URL не заменяются
успешной HTML-страницей. Файлы архива доступны только после входа через 5.

Frontend использует относительные URL, credentials=include и X-CSRF-Token.
Показывать отдельно checklist/readiness/process/outcome и качество правил;
null не превращать в 0/100. Статус 409 предлагает обновить данные и повторить
действие, 503 показывает фактическую недоступность модуля.

## Воспроизведение проверок

```bash
python -m pytest -q
python -m pip check
python -m app.common.export_openapi --check
python tests/integration/cases_events_http_smoke.py
python tests/integration/package5_http_smoke.py
./scripts/run.sh
```

HTTP smoke запускает настоящий app.main в отдельном процессе, использует
два аккаунта, все 12 дел, настоящий архив/PDF-извлечение/подтверждение,
события/исход, общий origin с временной HTML-фикстурой, перезапуск, удаление
и logout. Реальной проверки 6/транзита 7/письма 8 в нём нет, проверяется их
честный 503. Временная HTML-фикстура не является готовым UI команды.
Живой AI не вызывается. Tesseract rus+eng на этом ноутбуке отсутствует:
5 live-OCR тестов пропущены; PDF-текст и ручное заполнение проверены.
