#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
if [ ! -x "$project_dir/.venv/bin/python" ]; then
    echo 'Создайте .venv на Python 3.12 и установите зависимости по README.md.' >&2
    exit 1
fi
exec "$project_dir/.venv/bin/python" -m uvicorn app.main:app --workers 1 \
    --host "${VISAPILOT_HOST:-127.0.0.1}" --port "${VISAPILOT_PORT:-8000}"
