#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT_DIR/app_for_copy.py"
DST="$ROOT_DIR/app.py"

if [[ ! -f "$SRC" ]]; then
  echo "[ERROR] $SRC not found"
  exit 1
fi

cp "$SRC" "$DST"
python -m py_compile "$DST"

echo "[OK] Restored app.py from app_for_copy.py and verified syntax."
