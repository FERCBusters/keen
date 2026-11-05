// A person's Workstation posture tab: organisation targets, devices last
// seen or with too many scopes, observations without a reason, paging
// through devices, and people changed while a load is still running.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const tick = () => new Promise((resolve) => setTimeout(resolve, 15));
const device = (extra = {}) => ({
  hostname: 'laptop-01',
  agent_id: 'agent-1',
  platform: 'linux',
  active: true,
  revoked: false,
  last_seen: null,
  overflow: false,
  met: 0,
  target: 5,
  checks: [
    {
      check: 'disk_encryption',
      label: 'Disk encryption',
      status: 'met',
      observations: [],
      risks: [],
    },
  ],
  ...extra,
});
const result = (extra = {}) => ({
  devices: [device()],
  total: 1,
  revoked_total: 0,
  offset: 0,
  next_offset: null,
  freshness_hours: 24,
  rules_version: 'keen-2026.10',
  can_create_risk: false,
  targets: {},
  ...extra,
});

async function fixture(t, apiGet) {
  const calls = [];
  const ui = await loadUi('pages/workstation-posture.js', {
    html: '<section id="host"></section>',
    vendor: {
      withFramework: (url) => url,
      apiGet: async (url) => {
        calls.push(url);
        if (url === '/api/v1/risks/users') return { items: [] };
        return apiGet(url);
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  return { ui, host, calls, controller: ui.api.workstationPosture(host, true) };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

test("the organisation's targets are listed with their comparisons", async (t) => {
  const { host, controller } = await fixture(t, async () =>
    result({
      targets: {
        screen_lock: { operator: 'lte', value: 300, unit: 'seconds' },
        antivirus: { operator: 'lt', value: 7, unit: 'days' },
      },
    }),
  );
  controller.select({ id: 'p1', name: 'Pat' });
  await tick();
  assert.match(
    host.querySelector('.small-muted').textContent,
    /Organisation targets: Screen lock ≤ 300 seconds; Antivirus signature age < 7 days\. Rules: keen-2026\.10\./,
  );
});

test('a device last seen, or with too many scopes to judge, says so', async (t) => {
  const { host, controller } = await fixture(t, async () =>
    result({ devices: [device({ last_seen: '2026-10-01T08:00:00Z', overflow: true })] }),
  );
  controller.select({ id: 'p1', name: 'Pat' });
  await tick();
  const details = host.querySelector('[data-posture-device]');
  assert.match(details.textContent, /Active credential · Last contact: 2026-10-01T08:00:00Z/);
  assert.equal(
    details.querySelector('.text-warning').textContent,
    'Too many observation scopes to establish device completion. Review the agent configuration.',
  );
});

test('an attention observation without a reason is summarised without one', async (t) => {
  const attention = {
    check: 'firewall',
    label: 'Firewall',
    status: 'attention',
    observations: [
      {
        provider: 'ufw',
        scope: 'host',
        status: 'attention',
        reason: '',
        detail: 'Inactive',
        observed_at: '2026-10-28T00:00:00Z',
        event_id: 'e1',
      },
    ],
    risks: [],
  };
  const { ui, host, controller } = await fixture(t, async () =>
    result({ can_create_risk: true, devices: [device({ checks: [attention] })] }),
  );
  controller.select({ id: 'p1', name: 'Pat' });
  await tick();
  assert.match(
    host.querySelector('[data-posture-device] .small-muted:not(p)').textContent,
    /^ · Inactive$/,
  );
  host.querySelector('[data-posture-risk]').click();
  await tick();
  assert.equal(
    ui.document.getElementById('posture-risk-agent-1-firewall-summary').value,
    'The Firewall check needs attention on laptop-01.\nufw · host · Inactive',
  );
});

test('devices page forward and back twenty at a time', async (t) => {
  const pages = {
    0: result({ total: 21, next_offset: 20 }),
    20: result({ total: 21, offset: 20, devices: [device({ hostname: 'laptop-21' })] }),
  };
  const { host, controller, calls } = await fixture(t, async (url) => {
    const offset = Number(new URL(url, 'https://keen.example').searchParams.get('offset'));
    return pages[offset];
  });
  const buttons = () =>
    [...host.querySelectorAll('[data-posture-pages] button')].map((b) => b.textContent);
  controller.select({ id: 'p1', name: 'Pat' });
  await tick();
  assert.deepEqual(buttons(), ['More devices']);
  host.querySelector('[data-posture-pages] button').click();
  await tick();
  assert.equal(
    calls.at(-1),
    '/api/v1/isms/people/p1/workstation-posture?offset=20&include_revoked=false',
  );
  assert.match(host.querySelector('h3').textContent, /laptop-21/);
  assert.deepEqual(buttons(), ['Previous devices']);
  host.querySelector('[data-posture-pages] button').click();
  await tick();
  assert.equal(
    calls.at(-1),
    '/api/v1/isms/people/p1/workstation-posture?offset=0&include_revoked=false',
  );
  assert.deepEqual(buttons(), ['More devices']);
});

test('a load failing after another person was chosen leaves that person shown', async (t) => {
  const pending = [];
  const { host, controller } = await fixture(t, () => {
    pending.push(deferred());
    return pending.at(-1).promise;
  });
  controller.select({ id: 'p1', name: 'First person' });
  controller.select({ id: 'p2', name: 'Second person' });
  pending[1].resolve(result());
  await tick();
  pending[0].reject(new Error('offline'));
  await tick();
  assert.match(host.textContent, /Workstation posture · Second person/);
  assert.doesNotMatch(host.textContent, /could not be loaded/);
});

test('choosing the person already shown keeps their devices without reloading', async (t) => {
  const { host, controller, calls } = await fixture(t, async () => result());
  controller.select({ id: 'p1', name: 'Pat' });
  await tick();
  const shown = host.innerHTML;
  controller.select({ id: 'p1', name: 'Pat (renamed)' });
  await tick();
  assert.equal(calls.length, 1);
  assert.equal(host.innerHTML, shown);
});
