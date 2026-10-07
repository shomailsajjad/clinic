#!/usr/bin/env bash
set -euo pipefail
cd /workspace/clinic
export UV_CACHE_DIR=/workspace/.cache/uv
if [ ! -x .venv/bin/python ]; then
    uv venv .venv
fi
uv pip sync --python .venv/bin/python --require-hashes requirements.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check
