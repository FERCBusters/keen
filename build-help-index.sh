#!/usr/bin/env bash
# Rebuilds Help's search index from the Help pages. Run it after editing a page
# in services/ui/public/help; the UI tests fail while the index is out of date.
set -euo pipefail
keen_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$keen_root"
if [ ! -d services/ui/tests/node_modules/jsdom ]; then
  echo 'Install frontend tooling: npm ci --prefix services/ui/tests' >&2
  exit 1
fi
node services/ui/tests/support/help-search-index.mjs
bash format-ui.sh --write services/ui/public/help/search-index.js >/dev/null
