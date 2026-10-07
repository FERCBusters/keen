#!/usr/bin/env bash
# Run the complete KEEN API suite against a disposable test database.
set -euo pipefail
keen_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${KEEN_TEST_DATABASE_URL:?Set KEEN_TEST_DATABASE_URL to a disposable PostgreSQL database (never production)}"
command -v poetry >/dev/null
command -v node >/dev/null
export KEEN_DATABASE_URL="$KEEN_TEST_DATABASE_URL"
export KEEN_RETENTION_TEST_DATABASE_URL="$KEEN_TEST_DATABASE_URL"
export KEEN_MFA_TEST_DATABASE_URL="$KEEN_TEST_DATABASE_URL"
export KEEN_HOME_TEST_DATABASE_URL="$KEEN_TEST_DATABASE_URL"
export KEEN_BOOTSTRAP_ADMIN_USERNAME=ci-admin
export KEEN_BOOTSTRAP_ADMIN_PASSWORD=ci-only-not-a-real-password
export AWS_EC2_METADATA_DISABLED=true
export PYTHONUNBUFFERED=1
export TZ=UTC
export PYTHONPATH="$keen_root/services/api:$keen_root/services/api/scripts"
export KEEN_RULES_PATH="$keen_root/config/rules.yml"
export KEEN_RISK_MITIGATOR_RULES_PATH="$keen_root/config/risk_mitigator.yml"
report_dir="$keen_root/test-results"
mkdir -p "$report_dir"
cd "$keen_root/services/api"

poetry run python - <<'PY'
import os, time
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
url = os.environ['KEEN_TEST_DATABASE_URL']
if make_url(url).get_backend_name() != 'postgresql':
    raise SystemExit('The full suite requires a disposable PostgreSQL database.')
engine = create_engine(url, connect_args={'connect_timeout': 3})
for attempt in range(10):
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        break
    except Exception:
        if attempt == 9:
            raise SystemExit('Test PostgreSQL did not become ready.') from None
        time.sleep(1)
engine.dispose()
PY

result=0
poetry run python -m pytest tests -ra --tb=short \
  --junitxml="$report_dir/pytest.xml" || result=1

# A missing test-DB setting must never turn the full CI job green via skips.
poetry run python - "$report_dir/pytest.xml" <<'PY' || result=1
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
cases = list(root.iter('testcase'))
if not cases:
    raise SystemExit('No tests were reported.')
skipped = [c.attrib.get('name', '?') for c in cases if c.find('skipped') is not None]
if skipped:
    raise SystemExit('Full suite unexpectedly skipped tests: ' + ', '.join(skipped))
PY

# The UI currently has no npm test command. Check all first-party JS syntax.
while IFS= read -r -d '' file; do
  node --check "$file" || result=1
done < <(find "$keen_root/services/ui/public" -path '*/vendor' -prune -o -type f -name '*.js' -print0)
exit "$result"
