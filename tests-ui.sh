#!/usr/bin/env bash
# npm ci --prefix services/ui/tests installs the locked, test-only DOM dependencies.
set -euo pipefail
keen_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$keen_root"
report_dir="$keen_root/test-results"
mkdir -p "$report_dir"
if [ ! -f services/ui/tests/node_modules/c8/bin/c8.js ]; then
  echo 'Install frontend test dependencies: npm ci --prefix services/ui/tests' >&2
  exit 1
fi
node services/ui/tests/node_modules/c8/bin/c8.js \
  --all --include='services/ui/public/app.js' --include='services/ui/public/pages/**/*.js' \
  --reporter=text-summary --reporter=json-summary --reporter=lcov --reporter=html \
  --reports-dir="$report_dir/frontend-coverage" \
  node --experimental-vm-modules --test \
  --test-reporter=spec --test-reporter=junit \
  --test-reporter-destination=stdout --test-reporter-destination="$report_dir/frontend.xml" \
  services/ui/tests/*.test.mjs
