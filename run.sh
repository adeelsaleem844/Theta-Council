#!/usr/bin/env bash
# Theta Council - launcher
# Usage:  ./run.sh preflight | once | loop | dashboard | test | mock
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
case "${1:-once}" in
  test)      exec "$PY" -m unittest discover -s tests -v ;;
  mock)      exec "$PY" -m council once --mock ;;
  loop)      exec "$PY" -m council loop --interval "${2:-600}" ;;
  dashboard) exec "$PY" -m council dashboard ;;
  *)         exec "$PY" -m council "$@" ;;
esac
