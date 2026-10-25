# VisaPilot backend

Локальный прототип с вымышленными документами. В общем приложении подключены
**пакеты 1, 11, 2, 3, 4, 5, 6, 8 и 9**: аккаунты/CSRF, 12 направлений, профиль,
сравнение, личные дела, архив документов с извлечением/подтверждением полей,
Smart Checklist, реальные проверки, редактируемые письма и ручная хронология.
Покрытие правил частичное; источники и неизвестные условия отображаются явно.
Пакет **7** остаётся за Ваней: маршруты/транзит после входа отвечают
`503 module_not_ready`. Неполные требования сохраняют ready=false.

Нужен Python 3.12. Команды из корня репозитория:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e '.[test]'
test -f .env || cp .env.example .env
./scripts/run.sh
```

- Health: <http://127.0.0.1:8000/api/v1/health>
- Swagger: <http://127.0.0.1:8000/docs>
- OpenAPI: <http://127.0.0.1:8000/openapi.json>, снимок [openapi.json](openapi.json).

Веб-вход: <http://127.0.0.1:8000/> — существующий frontend подключён к API.
Сервер использует `frontend/dist`, затем статический `frontend/`;
можно явно указать `VISAPILOT_FRONTEND_DIST` в `.env`. Этот же
сервер отдаёт SPA/ресурсы и `/api/v1`, включая перезагрузку вложенных страниц.
Frontend использует относительные `/api/v1/...`, cookie и CSRF; отдельный
CORS не нужен. Исходники дизайна/UI в этой задаче не создавались.

PDF-текст реально извлекается; для изображений/сканов нужен внешний
Tesseract с `rus+eng` (подробности в [пакете 5](app/documents/README.md)).
Без него архив сохраняется, а поля можно заполнить и подтвердить вручную.
AI по умолчанию выключен, startup не вызывает CLI.

Проверки:

```bash
python -m pytest -q
python -m pip check
python -m app.common.export_openapi --check
python tests/auth_http_smoke.py
python tests/rules_profile_http_smoke.py
python tests/integration/package5_http_smoke.py
python tests/integration/cases_events_http_smoke.py
node frontend/tests/smoke.mjs
```

Для изменения схем обновите снимок: `python -m app.common.export_openapi`.
Остановка сервера — Ctrl+C. Перезапуск сохраняет `local_data/visapilot.sqlite3`.

[Передача Ване и frontend, модели, хранение и ограничения](docs/backend-handoff.md).
[Аккаунты: Origin, cookie, CSRF и проверка владельца](docs/auth-handoff.md).
[Правила: версии, источники и пределы покрытия](docs/rules-handoff.md).
[Профиль и сравнение: API, вопросы и инвалидация](docs/profile-handoff.md).
[Дела, события, связи пакетов и единый веб-запуск](docs/cases-events-handoff.md).
[Письма, интеграция реального пакета 6 и стык пакета 7](docs/letters-integration-handoff.md).
[Репетиция, отдельные SAMPLE-аккаунты, запуск/остановка и явный сброс](docs/runbook.md).
[Продуктовая документация и распределение пакетов](docs/README.md).
