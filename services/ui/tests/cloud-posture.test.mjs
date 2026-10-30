import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
const tick = () => new Promise((resolve) => setTimeout(resolve, 15));
const hostile = '<img src=x onerror=alert(1)>';
const check = (extra = {}) => ({
  key: 'identity.mfa',
  label: 'MFA for console users',
  status: 'attention',
  detail: 'MFA off for 1 of 2 console users',
  reason: '',
  subjects: [{ name: hostile, risks: [] }],
  subjects_total: 1,
  event_id: 'e1',
  observed_at: '2026-10-29T00:00:00Z',
  risks: [],
  ...extra,
});
const provider = (extra = {}, checks = [check()]) => ({
  provider: 'aws',
  label: 'AWS',
  scope_noun: 'account',
  enabled: true,
  checked_at: '2026-10-29T00:00:00Z',
  attempted_at: '2026-10-29T00:00:00Z',
  error: '',
  stale: false,
  reads_activity: true,
  activity_at: '2026-10-29T00:00:00Z',
  activity_error: '',
  activity_truncated: false,
  scopes: [
    {
      key: '111122223333',
      label: hostile,
      checked_at: '2026-10-29T00:00:00Z',
      met: 0,
      judged: checks.length,
      checks,
      identities: [
        {
          name: 'bob',
          email: 'bob@example.org',
          kind: 'user',
          admin: true,
          mfa: false,
          last_used: '2026-09-01T00:00:00',
          detail: hostile,
          person: { id: 'p 1', name: hostile },
        },
        { name: 'deploy', email: '', kind: 'service', admin: false, mfa: null, last_used: null },
      ],
    },
  ],
  activity: [
    {
      id: 'a 1',
      timestamp: '2026-10-29T01:00:00Z',
      actor: hostile,
      action: 'ConsoleLogin',
      outcome: 'failure',
      summary: '',
    },
  ],
  ...extra,
});
const posture = (extra = {}, providers = [provider()]) => ({
  providers,
  interval_seconds: 43200,
  can_refresh: false,
  can_create_risk: true,
  ...extra,
});

async function fixture(t, { data, posts = [], fail = {} } = {}) {
  const calls = [];
  const ui = await loadUi('pages/cloud-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url, fw) => `${url}&framework=${fw}`,
      apiGet: async (url) => {
        calls.push(url);
        if (url === '/api/v1/risks/users') return { items: [{ id: 'u1', username: 'owner' }] };
        if (fail.posture) throw Error('offline');
        return typeof data === 'function' ? data() : data || posture();
      },
      apiPost: async (url, body) => {
        posts.push({ url, body: JSON.parse(JSON.stringify(body)) });
        if (fail.post) throw Error(fail.post);
        return { risk_id: 'r1', asset_id: 'a1' };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const controller = ui.api.cloudPosture(host, true);
  return { ui, host, controller, calls, posts };
}

test('checks, identities and activity render escaped, with evidence links', async (t) => {
  const data = posture({}, [
    provider({}, [
      check(),
      check({
        key: 'findings',
        label: 'Security Hub findings',
        status: 'unknown',
        detail: '',
        reason: 'It is not set up for this account.',
        subjects: [],
        subjects_total: 0,
        event_id: null,
      }),
    ]),
  ]);
  const { host, controller, calls } = await fixture(t, { data });
  controller.select({ id: 'v 1', name: hostile });
  await tick();
  assert.equal(host.querySelector('img'), null);
  assert.equal(calls[0], '/api/v1/isms/vendors/v%201/cloud-posture');
  assert.match(host.textContent, /every 12 hours/);
  assert.match(host.querySelector('[data-cloud-scope] summary').textContent, /<img/);
  assert.match(host.textContent, /Last refreshed 2026-10-29T00:00:00Z/);
  assert.equal(host.querySelector('[data-cloud-refresh]'), null);
  const scope = host.querySelector('details[data-cloud-scope]');
  assert.equal(scope.open, true, 'a single scope opens');
  assert.match(scope.querySelector('summary').textContent, /111122223333.*0 \/ 2 met/);
  assert.equal(scope.querySelector('a[href^="/event"]').getAttribute('href'), '/event.html?id=e1');
  assert.match(scope.textContent, /It is not set up for this account\./);
  assert.deepEqual(
    [...scope.querySelectorAll('.badge.text-bg-warning, .badge.text-bg-secondary')].map(
      (b) => b.textContent,
    ),
    ['Needs attention', 'Not established'],
  );
  assert.equal(scope.querySelectorAll('[data-cloud-risk]').length, 1);
  const identities = scope.querySelector('[data-cloud-identities]');
  assert.match(identities.querySelector('summary').textContent, /2 identities/);
  assert.equal(
    identities.querySelector('a[href^="/person"]').getAttribute('href'),
    '/person.html?id=p%201&framework=F1',
  );
  const rows = [...identities.querySelectorAll('tbody tr')].map((r) =>
    [...r.children].map((c) => c.textContent),
  );
  assert.deepEqual(rows[0].slice(1, 5), ['User', 'Yes', 'Off', '2026-09-01T00:00:00']);
  assert.deepEqual(rows[1].slice(1, 5), ['Service', 'No', '—', 'Never']);
  const activity = host.querySelector('[data-cloud-activity]');
  assert.match(activity.textContent, /ConsoleLogin.*Failed/);
  assert.equal(activity.querySelector('a').getAttribute('href'), '/event.html?id=a%201');
  assert.equal(
    host.querySelector('a[href^="/events.html"]').getAttribute('href'),
    '/events.html?source=aws&framework=F1',
  );
});

test('listed subjects open on request and record their own risks', async (t) => {
  const posts = [];
  const { host, controller, ui } = await fixture(t, { posts });
  controller.select({ id: 'v1', name: 'AWS' });
  await tick();
  const toggle = host.querySelector('[data-cloud-subjects]');
  assert.equal(toggle.textContent, '1 listed');
  toggle.click();
  await tick();
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  const row = host.querySelector('[data-cloud-subject-row="0"]');
  assert.equal(row.hidden, false);
  assert.equal(row.querySelector('img'), null);
  row.querySelector('[data-cloud-subject-risk]').click();
  await tick();
  const form = row.querySelector('[data-posture-risk-form]');
  const title = form.querySelector('input');
  assert.equal(ui.document.activeElement, title);
  assert.equal(title.value, `${hostile}: MFA for console users needs attention`);
  assert.match(
    form.querySelector('textarea').value,
    /is listed by the MFA for console users check/,
  );
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await tick();
  await tick();
  assert.deepEqual(posts[0], {
    url: '/api/v1/isms/vendors/v1/cloud-posture/risks',
    body: {
      provider: 'aws',
      scope_key: '111122223333',
      check: 'identity.mfa',
      subject_key: hostile,
      title: title.value,
      threat_summary: posts[0].body.threat_summary,
      treatment_strategy: '',
      treatment_due_at: null,
      risk_owner_user_id: null,
      framework: 'F1',
    },
  });
  const notice = host.querySelector('[data-cloud-notice]');
  assert.equal(notice.hidden, false);
  assert.equal(notice.querySelector('a').getAttribute('href'), '/risk.html?id=r1&framework=F1');
});

test('whole-check risks, accepted subjects and refused saves', async (t) => {
  const posts = [];
  const fail = {};
  const accepted = check({
    subjects: [
      { name: 'bob', risks: [{ id: 'r2', title: 'Bob', accepted: true, due: '2026-12-01' }] },
    ],
  });
  const { host, controller, ui } = await fixture(t, {
    posts,
    fail,
    data: posture({}, [provider({}, [accepted])]),
  });
  controller.select({ id: 'v1', name: 'AWS' });
  await tick();
  host.querySelector('[data-cloud-subjects]').click();
  await tick();
  const row = host.querySelector('[data-cloud-subject-row="0"]');
  assert.match(row.textContent, /Risk accepted until 2026-12-01/);
  assert.equal(row.querySelector('[data-cloud-subject-risk]'), null);
  fail.post = 'An accepted risk needs an owner';
  host.querySelector('[data-cloud-risk]').click();
  await tick();
  const form = host.querySelector('[data-posture-risk-form]');
  assert.match(form.querySelector('h4').textContent, /Record a risk for MFA for console users in/);
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await tick();
  assert.equal(posts[0].body.subject_key, '');
  assert.match(form.querySelector('[role="status"]').textContent, /needs an owner/);
});

test('administrators queue a refresh and see why one was refused', async (t) => {
  const posts = [];
  const fail = {};
  const { host, controller } = await fixture(t, {
    posts,
    fail,
    data: posture({ can_refresh: true }),
  });
  controller.select({ id: 'v1', name: 'AWS' });
  await tick();
  const button = host.querySelector('[data-cloud-refresh]');
  button.click();
  await tick();
  assert.deepEqual(posts[0], { url: '/api/v1/isms/vendors/v1/cloud-posture/refresh', body: {} });
  const notice = host.querySelector('[data-cloud-notice]');
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /Refresh queued/);
  fail.post = hostile;
  button.click();
  await tick();
  assert.equal(notice.className, 'alert alert-warning');
  assert.equal(notice.textContent, hostile);
  assert.equal(notice.querySelector('img'), null);
});

test('warnings, failures and empty states explain themselves', async (t) => {
  const fail = { posture: true };
  const quiet = provider({
    enabled: false,
    error: hostile,
    stale: true,
    checked_at: null,
    scopes: [],
    activity: [],
    activity_error: 'KEEN was refused.',
    activity_truncated: true,
    reads_activity: false,
  });
  const { host, controller } = await fixture(t, {
    fail,
    data: posture({ can_create_risk: false }, [quiet]),
  });
  controller.select({ id: 'v1', name: 'AWS' });
  await tick();
  assert.match(host.textContent, /could not be loaded/);
  fail.posture = false;
  host.querySelector('button').click();
  await tick();
  const warnings = [...host.querySelectorAll('[data-cloud-warning]')].map((w) => w.textContent);
  assert.equal(warnings.length, 3);
  assert.match(warnings[0], /turned off in the deployment's settings/);
  assert.match(warnings[1], /could not read AWS: <img/);
  assert.match(warnings[2], /older than two refresh intervals/);
  assert.equal(host.querySelector('img'), null);
  assert.match(host.textContent, /Not refreshed yet · No accounts read yet/);
  assert.match(host.textContent, /The last activity read failed: KEEN was refused\./);
  assert.match(host.textContent, /oldest in that window were skipped/);
  assert.match(host.textContent, /No activity recorded\. View all AWS activity/);
});

test('without risk management there is no risk column; no providers says so', async (t) => {
  let data = posture({ can_create_risk: false }, [provider({}, [check({ subjects: [] })])]);
  const { host, controller } = await fixture(t, { data: () => data });
  controller.select({ id: 'v1', name: 'AWS' });
  await tick();
  assert.equal(host.querySelector('[data-cloud-risk]'), null);
  assert.equal(host.querySelectorAll('details[data-cloud-scope] > div thead th').length, 4);
  assert.equal(host.querySelector('[data-cloud-subjects]'), null);
  data = posture({}, []);
  controller.select({ id: 'v2', name: 'Other' });
  await tick();
  assert.match(host.textContent, /No cloud provider shows its posture on this vendor\./);
  controller.select(null);
  assert.equal(host.hidden, true);
});
