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
    vendor: { apiGet: () => new Promise((resolve) => pending.push(resolve)) },
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
