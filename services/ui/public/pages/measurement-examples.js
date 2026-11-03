// Examples for reporting jobs that sign submissions with a measure's own secret.
// The payload names the measure's expected source so entries are attributed to
// the same Keen source the measure was configured with.
import { apiDelete, apiPost, confirmAction, errorText, fmtTs } from '/app.js';

const SIGNING =
  'Each submission is signed: an HMAC-SHA256 over the Unix timestamp, a full stop and ' +
  'the exact request body, keyed with the measure’s submission secret, sent as ' +
  'X-Keen-Timestamp and X-Keen-Signature: v1=<hex>. Keen accepts a signature within ' +
  'five minutes of its timestamp, once: resending the identical request returns ' +
  '409 Conflict, meaning Keen already recorded it.';

export function measurementExamples({
  origin,
  key,
  framework,
  unit = '',
  value = 0,
  sourceType = 'other',
}) {
  const endpoint =
    origin + '/api/v1/webhooks/isms/effectiveness-metrics/' + encodeURIComponent(key);
  const payload = {
    framework,
    value: Number(value) || 0,
    unit,
    source_type: String(sourceType || '').trim() || 'other',
    source_title: 'Scheduled measurement',
  };
  const json = JSON.stringify(payload, null, 2);
  const quote = (value) => "'" + String(value).replaceAll("'", "'\\''") + "'";
  const curl = [
    '#!/bin/sh',
    '# Sends one signed measurement to Keen. Needs curl and openssl.',
    "# KEEN_METRICS_SECRET is this measure's submission secret. Keep the",
    "# reporting host's clock synchronised: Keen accepts a signature within",
    '# five minutes of its timestamp.',
    'set -eu',
    ': "${KEEN_METRICS_SECRET:?Set KEEN_METRICS_SECRET to the measure\'s submission secret}"',
    '',
    '# Replace value with the observed result for the period.',
    "body=$(cat <<'KEEN_METRIC_JSON'",
    json,
    'KEEN_METRIC_JSON',
    ')',
    '',
    '# Sign the timestamp, a full stop and the exact body that curl sends.',
    '# openssl receives the secret as an argument, which other users of a shared',
    '# host can see while it runs; there, use the Python example instead.',
    'timestamp=$(date +%s)',
    'signature=$(printf \'%s.%s\' "$timestamp" "$body" |',
    '  openssl dgst -sha256 -hmac "$KEEN_METRICS_SECRET" | awk \'{print $NF}\')',
    '',
    '# Retries resend the same signed request. 409 means Keen already has it.',
    'response=$(curl --silent --show-error --retry 4 --retry-delay 15 --retry-connrefused \\',
    "  --write-out '\\n%{http_code}' \\",
    "  -H 'Content-Type: application/json' \\",
    '  -H "X-Keen-Timestamp: $timestamp" \\',
    '  -H "X-Keen-Signature: v1=$signature" \\',
    '  --data-binary "$body" \\',
    '  ' + quote(endpoint) + ')',
    'status=$(printf \'%s\\n\' "$response" | tail -n 1)',
    "printf '%s\\n' \"$response\" | sed '$d' >&2",
    'case "$status" in',
    '  200) echo "Recorded" >&2 ;;',
    '  409) echo "Already recorded" >&2 ;;',
    '  *) echo "Keen answered HTTP $status" >&2; exit 1 ;;',
    'esac',
  ].join('\n');
  const python = [
    '"""Sends one signed measurement to Keen, using only the standard library.',
    '',
    "KEEN_METRICS_SECRET is this measure's submission secret. Keep the reporting",
    "host's clock synchronised: Keen accepts a signature within five minutes of",
    'its timestamp.',
    '"""',
    '',
    'import hashlib',
    'import hmac',
    'import json',
    'import os',
    'import time',
    'from urllib.error import HTTPError, URLError',
    'from urllib.request import Request, urlopen',
    '',
    'URL = ' + JSON.stringify(endpoint),
    'secret = os.environ["KEEN_METRICS_SECRET"].encode()',
    '',
    '# Replace value with the observed result for the period.',
    'payload = json.loads(' + JSON.stringify(json) + ')',
    '',
    '# Sign the timestamp, a full stop and the exact bytes sent.',
    'body = json.dumps(payload).encode()',
    'timestamp = str(int(time.time()))',
    'signature = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256)',
    'request = Request(URL, data=body, method="POST", headers={',
    '    "Content-Type": "application/json",',
    '    "X-Keen-Timestamp": timestamp,',
    '    "X-Keen-Signature": "v1=" + signature.hexdigest(),',
    '})',
    '',
    '# Retries resend the same signed request. 409 means Keen already has it.',
    'for attempt in range(5):',
    '    try:',
    '        with urlopen(request, timeout=30) as response:',
    '            print("Recorded:", response.read().decode())',
    '        break',
    '    except HTTPError as error:',
    '        if error.code == 409:',
    '            print("Already recorded")',
    '            break',
    '        if error.code not in (429, 503) or attempt == 4:',
    '            raise SystemExit(f"Keen answered HTTP {error.code}: {error.read().decode()}")',
    '    except URLError:',
    '        if attempt == 4:',
    '            raise',
    '    time.sleep(15)',
  ].join('\n');
  return { curl, python };
}

// The secret panel: whether the measure has a submission secret, and the one
// moment its new secret is shown. Without `secretPath` (an unsaved measure),
// it explains where the secret is generated.
function secretPanel(details, { secretPath, issuedAt } = {}) {
  const panel = document.createElement('div');
  panel.className = 'border rounded p-3 my-3';
  const heading = document.createElement('h4');
  heading.className = 'h6';
  heading.textContent = 'Submission secret';
  const status = document.createElement('p');
  status.className = 'small-muted mb-2';
  status.setAttribute('role', 'status');
  panel.append(heading, status);
  details.append(panel);
  if (!secretPath) {
    status.textContent =
      'Each measure signs with its own secret. Save the measure, then generate its secret on the measure’s page.';
    return;
  }
  const actions = document.createElement('div');
  actions.className = 'd-flex flex-wrap gap-2';
  const issue = document.createElement('button');
  issue.type = 'button';
  issue.className = 'btn btn-sm btn-primary';
  const revoke = document.createElement('button');
  revoke.type = 'button';
  revoke.className = 'btn btn-sm btn-outline-danger';
  revoke.textContent = 'Revoke secret';
  actions.append(issue, revoke);
  const reveal = document.createElement('div');
  reveal.className = 'mt-3';
  reveal.hidden = true;
  const warning = document.createElement('p');
  warning.className = 'small mb-1';
  warning.textContent =
    'Copy this secret now and store it as KEEN_METRICS_SECRET on the reporting host. Keen will not show it again.';
  const value = document.createElement('code');
  value.className = 'd-block text-break user-select-all mb-2';
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'btn btn-sm btn-outline-secondary';
  copy.textContent = 'Copy secret';
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(value.textContent);
      warning.textContent = 'Copied. Keen will not show this secret again.';
    } catch {
      warning.textContent = 'Select and copy the secret below. Keen will not show it again.';
    }
  };
  reveal.append(warning, value, copy);
  panel.append(actions, reveal);
  let issued = issuedAt || null;
  const show = () => {
    status.textContent = issued
      ? `Generated ${fmtTs(issued)}. Replacing or revoking it stops the current secret at once.`
      : 'No secret yet: Keen refuses submissions for this measure until you generate one.';
    issue.textContent = issued ? 'Replace secret' : 'Generate secret';
    revoke.hidden = !issued;
  };
  const busy = (state) => {
    issue.disabled = state;
    revoke.disabled = state;
  };
  issue.onclick = async () => {
    if (
      issued &&
      !(await confirmAction({
        title: 'Replace submission secret',
        message:
          'The current secret stops working at once. Reporting jobs fail until they use the new one.',
        confirmLabel: 'Replace',
        danger: true,
      }))
    )
      return;
    busy(true);
    try {
      const result = await apiPost(secretPath, {});
      issued = result.measure?.submission_secret_issued_at || new Date().toISOString();
      value.textContent = result.secret;
      reveal.hidden = false;
      show();
    } catch (error) {
      status.textContent = 'The secret was not generated: ' + errorText(error);
    } finally {
      busy(false);
    }
  };
  revoke.onclick = async () => {
    if (
      !(await confirmAction({
        title: 'Revoke submission secret',
        message: 'Keen refuses this measure’s submissions until you generate a new secret.',
        confirmLabel: 'Revoke',
        danger: true,
      }))
    )
      return;
    busy(true);
    try {
      await apiDelete(secretPath);
      issued = null;
      value.textContent = '';
      reveal.hidden = true;
      show();
    } catch (error) {
      status.textContent = 'The secret was not revoked: ' + errorText(error);
    } finally {
      busy(false);
    }
  };
  show();
}

export function mountMeasurementExamples(container, read, secret = {}) {
  const details = document.createElement('details');
  details.className = 'border rounded p-3 my-3';
  const title = document.createElement('summary');
  title.className = 'fw-semibold';
  title.textContent = 'Send measurements from another system';
  details.append(title);
  const help = document.createElement('p');
  help.className = 'small-muted mt-2';
  help.textContent =
    'Save the measure with a metric key first. ' +
    SIGNING +
    ' Replace the example value with your observed measurement. Each accepted submission creates a metric entry. Optional period_start and period_end accept YYYY-MM-DD.';
  details.append(help);
  secretPanel(details, secret);
  const warning = document.createElement('p');
  warning.setAttribute('role', 'status');
  details.append(warning);
  const panes = [];
  for (const [kind, label] of [
    ['curl', 'curl'],
    ['python', 'Python (standard library)'],
  ]) {
    const heading = document.createElement('h4');
    heading.className = 'h6';
    heading.textContent = label;
    const pre = document.createElement('pre');
    pre.className = 'bg-body-tertiary border rounded p-3';
    pre.style.maxHeight = '22rem';
    const code = document.createElement('code');
    pre.append(code);
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn btn-sm btn-outline-secondary mb-3';
    copy.textContent = 'Copy ' + label;
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(code.textContent);
        warning.textContent = 'Copied ' + label + '.';
      } catch {
        warning.textContent = 'Select and copy the example text below.';
      }
    };
    details.append(heading, copy, pre);
    panes.push({ kind, code, copy });
  }
  function update() {
    const data = read(),
      valid = !!data.key?.trim();
    warning.textContent = valid
      ? 'Example value is 0. Supply the actual measured value before sending.'
      : 'Enter and save a metric key to enable these examples.';
    const snippets = measurementExamples({ origin: location.origin, ...data });
    for (const pane of panes) {
      pane.code.textContent = valid ? snippets[pane.kind] : '';
      pane.copy.disabled = !valid;
    }
  }
  details.addEventListener('toggle', () => {
    if (details.open) update();
  });
  container.append(details);
  return update;
}
