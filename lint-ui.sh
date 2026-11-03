#!/usr/bin/env bash
# Checks first-party JavaScript for HTML insertion that bypasses esc() or the
# shared sanitiser, locally, in pre-commit and in CI. Sites that predate the
# check are listed in services/ui/tests/eslint-suppressions.json; --prune removes
# entries once their sites are fixed, so the list only shrinks.
set -euo pipefail
keen_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$keen_root"
mode="${1:---check}"
case "$mode" in
  --check) prune=() ;;
  --prune) prune=(--prune-suppressions) ;;
  *) echo 'Use --check or --prune' >&2; exit 2 ;;
esac
linter=services/ui/tests/node_modules/eslint/bin/eslint.js
if [ ! -f "$linter" ]; then
  echo 'Install frontend tooling: npm ci --prefix services/ui/tests' >&2
  exit 1
fi
exec node "$linter" \
  --config services/ui/tests/eslint.config.js \
  --suppressions-location services/ui/tests/eslint-suppressions.json \
  ${prune[@]+"${prune[@]}"} \
  services/ui/public
