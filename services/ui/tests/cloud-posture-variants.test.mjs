// A vendor's Cloud posture tab with the variants providers report: hourly
// reads, group and MFA-protected identities, refreshes that failed before an
// attempt was recorded, partial activity, truncated subject lists, and
// vendors changed while a load is still running.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const tick = () => new Promise((resolve) => setTimeout(resolve, 15));
const check = (extra = {}) => ({
  key: 'identity.mfa',
  label: 'MFA for console users',
  status: 'attention',
  detail: 'MFA off for 1 of 2 console users',
  reason: '',
  subjects: [],
  subjects_total: 0,
  event_id: null,
  observed_at: null,
  risks: [],
  ...extra,
});
const scope = (extra = {}) => ({
  key: 'prod',
  label: 'Production',
  kind: 'project',
  checked_at: '2026-10-29T00:00:00Z',
  met: 0,
  judged: 1,
  checks: [check()],
  identities: [],
  ...extra,
});
const provider = (extra = {}) => ({
  provider: 'gcp',
  label: 'Google Cloud',
  scope_noun: 'project',
  enabled: true,
  checked_at: '2026-10-29T00:00:00Z',
  attempted_at: '2026-10-29T00:00:00Z',
  error: '',
  stale: false,
  reads_activity: false,
  activity_at: null,
  activity_error: '',
  activity_truncated: false,
  scopes: [scope()],
  activity: [],
  ...extra,
});
const posture = (providers = [provider()], extra = {}) => ({
  providers,
  interval_seconds: 43200,
  can_refresh: false,
  can_create_risk: false,
  ...extra,
});

async function fixture(t, apiGet) {
  const calls = [];
  const ui = await loadUi('pages/cloud-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url, fw) => `${url}&framework=${fw}`,
      apiGet: async (url) => {
        calls.push(url);
        if (url === '/api/v1/risks/users') return { items: [] };
        return apiGet(url);
      },
      apiPost: async () => ({ risk_id: 'r1' }),
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  return { ui, host, calls, controller: ui.api.cloudPosture(host, true) };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

test('an hourly read, group and MFA identities and a check without detail read plainly', async (t) => {
  const data = posture(
    [
      provider({
        scopes: [
          scope({
            key: 'prod',
            label: 'prod',
            checks: [check({ detail: '', reason: '' })],
            identities: [
              {
                name: 'admins@example.org',
                email: 'admins@example.org',
                kind: 'group',
                admin: true,
                mfa: null,
                last_used: null,
              },
              {
                name: 'dana',
                email: 'dana@example.org',
                kind: 'user',
                admin: false,
                mfa: true,
                last_used: null,
              },
              // A kind no collector sends today, from a vendor or a stored
              // snapshot, still names its column.
              {
                name: 'robot',
                email: '',
                kind: '<b>robot</b>',
                admin: false,
                mfa: null,
                last_used: null,
              },
            ],
          }),
        ],
      }),
    ],
    { interval_seconds: 3600 },
  );
  const { host, controller } = await fixture(t, async () => data);
  controller.select({ id: 'v1', name: 'Google' });
  await tick();
  assert.match(host.textContent, /read-only access every hour and records/);
  const details = host.querySelector('details[data-cloud-scope]');
  // A scope labelled by its own key shows the key once.
  assert.equal(details.querySelector('summary .mono'), null);
  assert.deepEqual(
    [...details.querySelector('tbody tr').children].map((cell) => cell.textContent),
    ['MFA for console users', 'Needs attention', '—', '—'],
  );
  const identities = [...details.querySelectorAll('[data-cloud-identities] tbody tr')].map((row) =>
    [...row.children].slice(1, 5).map((cell) => cell.textContent),
  );
  assert.deepEqual(identities, [
    ['Group', 'Yes', '—', 'Never'],
    ['User', 'No', 'On', 'Never'],
    ['Other', 'No', '—', 'Never'],
  ]);
});

test('a refused refresh without an attempt time, and activity read but empty, explain themselves', async (t) => {
  const data = posture([
    provider({ error: 'KEEN was refused.', attempted_at: null, reads_activity: true }),
  ]);
  const { host, controller } = await fixture(t, async () => data);
  controller.select({ id: 'v1', name: 'Google' });
  await tick();
  const warnings = [...host.querySelectorAll('[data-cloud-warning]')].map((w) => w.textContent);
  assert.deepEqual(warnings, ['The last refresh could not read Google Cloud: KEEN was refused.']);
  assert.equal(
    host.querySelector('[data-cloud-activity]').textContent,
    'No activity recorded yet. View all Google Cloud activity',
  );
});

test('activity without an actor or a known outcome shows what was recorded', async (t) => {
  const data = posture([
    provider({
      reads_activity: true,
      activity: [
        {
          id: 'a1',
          timestamp: '2026-10-29T01:00:00Z',
          actor: '',
          action: 'SetIamPolicy',
          outcome: 'denied',
        },
        {
          id: 'a2',
          timestamp: '2026-10-29T02:00:00Z',
          actor: '',
          action: 'GetIamPolicy',
          outcome: '',
        },
      ],
    }),
  ]);
  const { host, controller } = await fixture(t, async () => data);
  controller.select({ id: 'v1', name: 'Google' });
  await tick();
  const rows = [...host.querySelectorAll('[data-cloud-activity] tbody tr')].map((row) =>
    [...row.children].slice(1).map((cell) => cell.textContent),
  );
  assert.deepEqual(rows, [
    ['SetIamPolicy', '—', 'denied'],
    ['GetIamPolicy', '—', '—'],
  ]);
});

test('a check listing fewer subjects than it found says how many it shows', async (t) => {
  const data = posture([
    provider({
      scopes: [
        scope({
          checks: [
            check({
              subjects: [{ name: 'ci-bot', risks: [] }],
              subjects_total: 40,
            }),
          ],
        }),
      ],
    }),
  ]);
  const { host, controller } = await fixture(t, async () => data);
  controller.select({ id: 'v1', name: 'Google' });
  await tick();
  const toggle = host.querySelector('[data-cloud-subjects]');
  assert.equal(toggle.textContent, '40 listed');
  toggle.click();
  const cell = host.querySelector('[data-cloud-subject-row="0"] td');
  assert.equal(cell.querySelector('p').textContent, 'Showing 1 of 40.');
  assert.deepEqual(
    [...cell.querySelectorAll('li')].map((item) => item.textContent),
    ['ci-bot'],
  );
  // The scope lists no identities, so it has no identity table.
  assert.equal(host.querySelector('[data-cloud-identities]'), null);
});

test('loads that answer after another vendor was chosen are discarded', async (t) => {
  const pending = [];
  const { host, controller } = await fixture(t, () => {
    pending.push(deferred());
    return pending.at(-1).promise;
  });
  controller.select({ id: 'v1', name: 'First vendor' });
  controller.select({ id: 'v2', name: 'Second vendor' });
  pending[1].resolve(posture([provider({ label: 'Second cloud' })]));
  await tick();
  pending[0].reject(new Error('offline'));
  await tick();
  assert.match(host.textContent, /Cloud posture · Second vendor/);
  assert.doesNotMatch(host.textContent, /could not be loaded/);

  controller.select({ id: 'v3', name: 'Third vendor' });
  controller.select({ id: 'v4', name: 'Fourth vendor' });
  pending[3].resolve(posture([provider({ label: 'Fourth cloud' })]));
  await tick();
  pending[2].resolve(posture([provider({ label: 'Third cloud' })]));
  await tick();
  assert.match(host.textContent, /Cloud posture · Fourth vendor/);
  assert.doesNotMatch(host.textContent, /Third/);
});

test('choosing the vendor already shown keeps its posture without reloading', async (t) => {
  const { host, controller, calls } = await fixture(t, async () => posture());
  controller.select({ id: 'v1', name: 'Google' });
  await tick();
  const shown = host.innerHTML;
  controller.select({ id: 'v1', name: 'Google (renamed)' });
  await tick();
  assert.equal(calls.length, 1);
  assert.equal(host.innerHTML, shown);
});

test('opening a second risk form in a scope replaces the first', async (t) => {
  const data = posture(
    [
      provider({
        scopes: [
          scope({
            checks: [check(), check({ key: 'storage.public', label: 'Public buckets' })],
          }),
        ],
      }),
    ],
    { can_create_risk: true },
  );
  const { host, controller } = await fixture(t, async () => data);
  controller.select({ id: 'v1', name: 'Google' });
  await tick();
  const [mfa, buckets] = host.querySelectorAll('[data-cloud-risk]');
  mfa.click();
  buckets.click();
  await tick();
  const forms = host.querySelectorAll('[data-posture-risk-form]');
  assert.equal(forms.length, 1);
  assert.equal(
    forms[0].querySelector('h4').textContent,
    'Record a risk for Public buckets in Production',
  );
});
