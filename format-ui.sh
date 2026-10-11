#!/usr/bin/env bash
# Uses the same pinned formatter locally, in pre-commit and in CI.
set -euo pipefail
keen_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$keen_root"
mode="${1:---check}"
if [ "$#" -gt 0 ]; then shift; fi
case "$mode" in --check|--write) ;; *) echo 'Use --check or --write' >&2; exit 2 ;; esac
formatter=services/ui/tests/node_modules/prettier/bin/prettier.cjs
if [ ! -f "$formatter" ]; then
  echo 'Install frontend tooling: npm ci --prefix services/ui/tests' >&2
  exit 1
fi
if [ "$#" -eq 0 ]; then
  set -- 'services/ui/public/**/*.{js,css}' 'services/ui/scripts/**/*.mjs' 'services/ui/tests/**/*.mjs'
fi
node "$formatter" "$mode" "$@"
