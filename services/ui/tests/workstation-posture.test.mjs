import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
const tick = () => new Promise((resolve) => setTimeout(resolve, 15));
const hostile = '<img src=x onerror=alert(1)>';
const result = (hostname = hostile) => ({
  total: 1,
  next_offset: null,
  freshness_hours: 24,
  devices: [
    {
      hostname,
      agent_id: 'a',
      active: true,
      last_seen: null,
      met: 1,
      target: 5,
      checks: [
        {
          label: 'Antivirus',
          status: 'met',
          observations: [
            {
              provider: hostile,
              scope: hostile,
              reason: hostile,
              observed_at: '2026-10-26T00:00:00Z',
              event_id: 'event-1',
              status: 'met',
            },
          ],
        },
      ],
    },
  ],
});
test('device totals and evidence links escape hostile names; refresh and clear work', async (t) => {
  let calls = 0;
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async () => {
        calls++;
        return result();
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host'),
    controller = ui.api.workstationPosture(host, true);
  controller.select({ id: 'p1', name: hostile });
  await tick();
  assert.match(host.textContent, /1 \/ 5 checks met/);
  assert.equal(host.querySelector('img'), null);
  assert.equal(host.querySelector('a').getAttribute('href'), '/event.html?id=event-1');
  host.querySelector('[data-posture-refresh]').click();
  await tick();
  assert.equal(calls, 2);
  controller.select(null);
  assert.equal(host.hidden, true);
  assert.equal(host.textContent, '');
});
test('permission gating and late response do not disclose another person', async (t) => {
  const pending = [];
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: () => new Promise((resolve) => pending.push(resolve)),
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, false).select({ id: 'private', name: 'Private' });
  assert.equal(pending.length, 0);
  const c = ui.api.workstationPosture(host, true);
  c.select({ id: 'first', name: 'First' });
  c.select({ id: 'second', name: 'Second' });
  pending[1](result('second-laptop'));
  await tick();
  pending[0](result('first-laptop'));
  await tick();
  assert.match(host.textContent, /second-laptop/);
  assert.doesNotMatch(host.textContent, /first-laptop/);
});
test('failed loading is retryable and empty devices have useful instructions', async (t) => {
  let fail = true;
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async () => {
        if (fail) throw Error('offline');
        return { ...result(), devices: [], total: 0 };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, true).select({ id: 'p', name: 'Person' });
  await tick();
  assert.match(host.textContent, /could not be loaded/);
  fail = false;
  host.querySelector('button').click();
  await tick();
  assert.match(host.textContent, /No workstation agents linked/);
});
test('revoked agents are hidden until Show revoked agents is ticked', async (t) => {
  const requests = [];
  const active = { ...result('replacement').devices[0], agent_id: 'new' };
  const revoked = {
    ...result('old-laptop').devices[0],
    agent_id: 'old',
    active: false,
    revoked: true,
  };
  const expired = {
    ...result('spare').devices[0],
    agent_id: 'spare',
    active: false,
    revoked: false,
  };
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async (raw) => {
        const url = new URL(raw, 'https://keen.example');
        requests.push([url.searchParams.get('offset'), url.searchParams.get('include_revoked')]);
        const all = url.searchParams.get('include_revoked') === 'true';
        const devices = all ? [revoked, active, expired] : [active, expired];
        return { ...result(), devices, total: devices.length, revoked_total: 1 };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const controller = ui.api.workstationPosture(host, true);
  controller.select({ id: 'p1', name: 'Person' });
  await tick();
  const box = () => host.querySelector('[data-posture-show-revoked]');
  const names = () =>
    [...host.querySelectorAll('[data-posture-device] h3')].map((h) => h.textContent);
  assert.deepEqual(requests, [['0', 'false']]);
  assert.equal(box().checked, false);
  assert.equal(
    host.querySelector(`label[for="${box().id}"]`).textContent,
    'Show revoked agents (1)',
  );
  assert.deepEqual(names(), ['replacement', 'spare']);
  assert.match(host.textContent, /2 devices linked · 1 revoked device hidden\./);
  // A device whose credential lapsed is expired, not revoked, and stays listed.
  assert.match(host.querySelectorAll('[data-posture-device]')[1].textContent, /Credential expired/);

  box().click();
  await tick();
  assert.deepEqual(requests.at(-1), ['0', 'true']);
  assert.equal(box().checked, true);
  assert.equal(ui.document.activeElement, box());
  assert.deepEqual(names(), ['old-laptop', 'replacement', 'spare']);
  assert.match(host.querySelector('[data-posture-device]').textContent, /Credential revoked · /);
  assert.match(host.textContent, /3 devices linked\./);

  // The choice holds for Refresh and for the next person.
  host.querySelector('[data-posture-refresh]').click();
  await tick();
  controller.select({ id: 'p2', name: 'Another' });
  await tick();
  assert.deepEqual(requests.slice(-2), [
    ['0', 'true'],
    ['0', 'true'],
  ]);
  assert.notEqual(ui.document.activeElement, box());
  box().click();
  await tick();
  assert.deepEqual(requests.at(-1), ['0', 'false']);
  assert.equal(box().checked, false);
});
test('a person with only revoked devices is told how to see them', async (t) => {
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async () => ({ ...result(), devices: [], total: 0, revoked_total: 2 }),
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, true).select({ id: 'p', name: 'Person' });
  await tick();
  assert.match(
    host.textContent,
    /Every device linked to this person is revoked\. Tick Show revoked agents to review them\./,
  );
  assert.match(host.textContent, /0 devices linked · 2 revoked devices hidden\./);
});
test('without revoked agents there is nothing to show', async (t) => {
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async () => ({ ...result(), revoked_total: 0 }),
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, true).select({ id: 'p', name: 'Person' });
  await tick();
  assert.equal(host.querySelector('[data-posture-show-revoked]'), null);
  assert.match(host.textContent, /1 devices linked\./);
});
test('devices collapse with their platform shown and long evidence lists fold', async (t) => {
  const observation = (n) => ({
    provider: 'p' + n,
    scope: 'host',
    reason: 'components_detected',
    observed_at: '2026-10-26T00:00:00Z',
    event_id: 'e' + n,
    status: 'met',
  });
  const device = (id, platform) => ({
    ...result(id).devices[0],
    agent_id: id,
    platform,
    checks: [{ label: 'Antivirus', status: 'met', observations: [observation(1), observation(2)] }],
  });
  let data = {
    ...result(),
    total: 2,
    devices: [device('mac', 'darwin'), device('pc', 'nonsense')],
  };
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: { withFramework: (url) => url, apiGet: async () => data },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, true).select({ id: 'p', name: 'Person' });
  await tick();
  const devices = () => [...host.querySelectorAll('details[data-posture-device]')];
  assert.deepEqual(
    devices().map((d) => d.open),
    [false, false],
  );
  assert.equal(devices()[0].querySelector('summary .bi-apple').getAttribute('title'), 'macOS');
  assert.equal(devices()[1].querySelector('summary .platform-icon'), null);
  const folded = devices()[0].querySelector('details[data-posture-evidence]');
  assert.equal(folded.querySelector('summary').textContent, '2 observations');
  assert.equal(folded.open, false);
  // An opened device stays open when the panel refreshes.
  devices()[1].open = true;
  await tick();
  host.querySelector('[data-posture-refresh]').click();
  await tick();
  assert.deepEqual(
    devices().map((d) => d.open),
    [false, true],
  );
  // A person with one device sees it open.
  data = { ...result(), devices: [device('only', 'windows')] };
  host.querySelector('[data-posture-refresh]').click();
  await tick();
  assert.equal(devices()[0].open, true);
  assert.ok(devices()[0].querySelector('summary .bi-windows'));
});

const unmet = (extra = {}) => ({
  ...result('laptop-01'),
  can_create_risk: true,
  devices: [
    {
      ...result('laptop-01').devices[0],
      agent_id: 'agent-1',
      checks: [
        {
          check: 'disk_encryption',
          label: 'Disk encryption',
          status: 'attention',
          risks: [],
          observations: [
            {
              provider: 'lsblk',
              scope: '/',
              reason: 'measured',
              detail: hostile,
              observed_at: '2026-10-26T00:00:00Z',
              event_id: 'e1',
              status: 'attention',
            },
          ],
          ...extra,
        },
        { check: 'antivirus', label: 'Antivirus', status: 'met', risks: [], observations: [] },
      ],
    },
  ],
});
test('unmet checks offer an inline risk form that records the risk', async (t) => {
  let data = unmet();
  const posts = [];
  let fail = false;
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url, fw) => `${url}&framework=${fw}`,
      apiGet: async (url) =>
        url === '/api/v1/risks/users' ? { items: [{ id: 'u1', username: hostile }] } : data,
      apiPost: async (url, body) => {
        posts.push({ url, body });
        if (fail) throw Error('An accepted risk needs an owner');
        return { risk_id: 'r1', asset_id: 'a1' };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, true).select({ id: 'p 1', name: 'Pat' });
  await tick();
  const buttons = [...host.querySelectorAll('[data-posture-risk]')];
  assert.equal(buttons.length, 1, 'only the unmet check offers a risk');
  assert.equal(buttons[0].textContent, 'Create a risk');
  buttons[0].click();
  await tick();
  const form = host.querySelector('[data-posture-risk-form]');
  const $ = (suffix) =>
    ui.document.getElementById(`posture-risk-agent-1-disk_encryption-${suffix}`);
  assert.equal(ui.document.activeElement, $('title'));
  assert.equal($('title').value, 'laptop-01: Disk encryption needs attention');
  assert.match($('summary').value, /The Disk encryption check needs attention on laptop-01/);
  assert.match($('summary').value, /lsblk · \/ · measured · <img/);
  assert.equal(host.querySelector('img'), null);
  assert.deepEqual(
    [...$('owner').options].map((o) => o.textContent),
    ['Nobody yet', hostile],
  );
  assert.equal($('due').getAttribute('aria-describedby'), $('due').id + '-help');
  // Accepting needs an owner and a review date before anything is sent.
  $('strategy').value = 'accept';
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await tick();
  assert.equal(posts.length, 0);
  assert.match(form.querySelector('[role="status"]').textContent, /owner and a review date/);
  $('title').value = '  ';
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.match(form.querySelector('[role="status"]').textContent, /Give the risk a title/);
  $('title').value = 'Unencrypted laptop';
  $('owner').value = 'u1';
  $('due').value = '2026-12-01';
  // A refused save shows the reason and can be retried.
  fail = true;
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await tick();
  assert.match(form.querySelector('[role="status"]').textContent, /needs an owner/);
  assert.equal(form.querySelector('[type="submit"]').disabled, false);
  fail = false;
  data = unmet({
    status: 'accepted',
    risks: [{ id: 'r1', title: hostile, accepted: true, due: '2026-12-01' }],
  });
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await tick();
  await tick();
  // The body was built in the page's realm; compare its JSON.
  assert.deepEqual(JSON.parse(JSON.stringify(posts.at(-1))), {
    url: '/api/v1/isms/people/p%201/workstation-posture/risks',
    body: {
      agent_id: 'agent-1',
      check: 'disk_encryption',
      title: 'Unencrypted laptop',
      threat_summary: posts.at(-1).body.threat_summary,
      treatment_strategy: 'accept',
      treatment_due_at: '2026-12-01',
      risk_owner_user_id: 'u1',
      framework: 'F1',
    },
  });
  const notice = host.querySelector('[data-posture-notice]');
  assert.equal(notice.hidden, false);
  assert.equal(notice.querySelector('a').getAttribute('href'), '/risk.html?id=r1&framework=F1');
  // The reloaded result shows the acceptance and no longer offers a risk.
  assert.match(host.textContent, /Risk accepted until 2026-12-01/);
  assert.equal(host.querySelector('[data-posture-risk]'), null);
  const linked = host.querySelector('[data-posture-linked-risk] a');
  assert.equal(linked.textContent, hostile);
  assert.equal(host.querySelector('img'), null);
  assert.ok([...host.querySelectorAll('.badge')].some((b) => b.textContent === 'Risk accepted'));
});
test('cancelling the risk form returns focus; owners failing to load still allows saving', async (t) => {
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async (url) => {
        if (url === '/api/v1/risks/users') throw Error('forbidden');
        return unmet();
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, true).select({ id: 'p', name: 'Pat' });
  await tick();
  const button = host.querySelector('[data-posture-risk]');
  button.click();
  await tick();
  const form = host.querySelector('[data-posture-risk-form]');
  assert.match(form.querySelector('[role="status"]').textContent, /Owners could not be loaded/);
  [...form.querySelectorAll('button')].find((b) => b.textContent === 'Cancel').click();
  assert.equal(host.querySelector('[data-posture-risk-form]'), null);
  assert.equal(ui.document.activeElement, button);
});
test('without risk management, recorded risks show without titles or actions', async (t) => {
  const data = {
    ...unmet({
      risks: [
        { id: 'r9', title: null, accepted: false, acceptance_expired: true, due: '2026-01-01' },
        { id: 'r8', title: null, accepted: false, due: null },
      ],
    }),
    can_create_risk: false,
  };
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: { withFramework: (url) => url, apiGet: async () => data },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.workstationPosture(host, true).select({ id: 'p', name: 'Pat' });
  await tick();
  const rows = [...host.querySelectorAll('[data-posture-linked-risk]')].map((r) => r.textContent);
  assert.deepEqual(rows, ['Acceptance expired', 'Risk recorded']);
  assert.equal(host.querySelector('[data-posture-linked-risk] a'), null);
  assert.equal(host.querySelector('[data-posture-risk]'), null);
  // Devices without risks or risk management keep the three-column table.
  data.devices[0].checks[0].risks = [];
  host.querySelector('[data-posture-refresh]').click();
  await tick();
  assert.equal(host.querySelectorAll('thead th').length, 3);
});
