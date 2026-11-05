// Keen Agents: rotating bearer tokens, declined key moves and revocations,
// device assignments, deployments without People, and reloading the list.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const naive = (ms) => new Date(Date.now() + ms).toISOString().replace('Z', '');

function agent(overrides = {}) {
  return {
    id: 'a1',
    name: 'web-1',
    enabled: true,
    agent_type: 'server',
    person_id: null,
    credential_type: 'token',
    evidence_mode_ceiling: 'events',
    subject_type: 'pairwise',
    expires_at: naive(86400000),
    last_seen: naive(-1000),
    health: {},
    ...overrides,
  };
}

async function agents(t, options = {}) {
  const state = {
    posted: [],
    patched: [],
    gets: 0,
    gate: null,
    intervals: [],
    data: options.data ?? {
      demo_mode: false,
      people_enabled: true,
      people: [{ id: 'p1', name: '<b>Dana</b>' }],
      agents: [agent()],
    },
  };
  const ui = await page(t, 'agents', {
    htmlName: 'admin',
    confirm: options.confirm ?? true,
    globals: {
      setInterval: (fn, ms) => state.intervals.push({ fn, ms }),
      navigator: { clipboard: { writeText: async () => {} } },
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/admin/agents') {
        state.gets++;
        if (state.gate) await state.gate;
        return structuredClone(state.data);
      }
      if (u.pathname === '/api/v1/admin/agent-enrollment-profiles') return { profiles: [] };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      return { agent: {} };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      return { token: 'keen-agent-token-' + state.posted.length };
    },
  });
  const $ = (id) => ui.el('ka-' + id);
  await settle(() => $('agents').children.length || !$('status').hidden);
  const card = (id) => $('agents').querySelector(`details[data-agent="${id}"]`).closest('article');
  const button = (scope, text) =>
    [...scope.querySelectorAll('button')].find((b) => b.textContent === text);
  return { ui, state, $, card, button };
}

test('rotating a bearer token asks for typed confirmation and shows the replacement once', async (t) => {
  const { ui, state, $, card, button } = await agents(t);
  $('expiry').value = '30';
  button(card('a1'), 'Rotate credential').click();
  await settle(() => $('token').value === 'keen-agent-token-1');
  const asked = ui.confirmations.at(-1);
  assert.equal(asked.title, 'Rotate agent credential');
  assert.equal(asked.typed, 'Rotate');
  assert.match(
    asked.message,
    /Invalidate the current credential and issue a replacement for web-1\?/,
  );
  assert.deepEqual(state.posted, [
    { path: '/api/v1/admin/agents/a1/rotate', body: { expires_days: 30 } },
  ]);
  assert.equal($('token-label').textContent, 'Agent credential');
  assert.equal($('token').type, 'password');
});

test('declining a key move or a revocation leaves the agent as it was', async (t) => {
  const { ui, state, card, button } = await agents(t, { confirm: false });
  button(card('a1'), 'Move to key').click();
  button(card('a1'), 'Revoke').click();
  await settle(() => ui.confirmations.length === 2);
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(
    ui.confirmations.map((c) => c.title),
    ['Move agent to key enrolment', 'Revoke agent'],
  );
  assert.deepEqual(state.posted, []);
  assert.equal(state.gets, 1);
});

test('without People every device registers and stays a server', async (t) => {
  const { ui, state, $ } = await agents(t);
  // A workstation chosen before People was switched off reverts to a server.
  $('type').value = 'workstation';
  $('type').dispatchEvent(new ui.window.Event('change'));
  assert.equal($('person-field').hidden, false);
  state.data = { demo_mode: false, people_enabled: false, people: [], agents: [agent()] };
  $('refresh').click();
  await settle(() => $('type').querySelector('[value="workstation"]').disabled);
  assert.equal($('type').value, 'server');
  assert.equal($('person-field').hidden, true);
  assert.equal($('person').disabled, true);
  assert.equal(
    ui.document.getElementById('agent-type-a1').querySelector('[value="workstation"]').disabled,
    true,
  );
});

test('workstations name their person, or say that none is assigned', async (t) => {
  const { card } = await agents(t, {
    data: {
      demo_mode: false,
      people_enabled: true,
      people: [{ id: 'p1', name: '<b>Dana</b>' }],
      agents: [
        agent({ id: 'w1', name: 'laptop-1', agent_type: 'workstation', person_id: 'p1' }),
        agent({ id: 'w2', name: 'laptop-2', agent_type: 'workstation', person_id: 'gone' }),
      ],
    },
  });
  const label = (id) => card(id).querySelector('.border-top p').textContent;
  assert.equal(label('w1'), 'Workstation · <b>Dana</b>');
  assert.equal(card('w1').querySelector('.border-top b'), null);
  assert.equal(label('w2'), 'Workstation · Person unassigned');
});

test('a device assignment needs a person, and an unchanged one sends nothing', async (t) => {
  const { ui, state, $, card, button } = await agents(t, {
    data: {
      demo_mode: false,
      people_enabled: true,
      people: [{ id: 'p1', name: 'Dana' }],
      agents: [agent({ id: 'w1', agent_type: 'workstation', person_id: 'p1' })],
    },
  });
  const type = ui.document.getElementById('agent-type-w1');
  const person = ui.document.getElementById('agent-person-w1');
  assert.equal(person.value, 'p1');
  person.value = '';
  button(card('w1'), 'Save assignment').click();
  await settle(() => $('status').textContent === 'Choose a person.');
  assert.match($('status').className, /alert-danger/);
  // Saving what is already assigned asks nothing and sends nothing.
  person.value = 'p1';
  button(card('w1'), 'Save assignment').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ui.confirmations.length, 0);
  assert.deepEqual(state.patched, []);
  // A server has no person.
  type.value = 'server';
  type.dispatchEvent(new ui.window.Event('change'));
  assert.equal(person.disabled, true);
  assert.equal(person.closest('.agent-person-field').hidden, true);
  button(card('w1'), 'Save assignment').click();
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/agents/w1',
    body: { agent_type: 'server', person_id: null },
  });
  assert.equal(ui.confirmations.at(-1).title, 'Change device assignment');
});

test('a declined assignment change sends nothing', async (t) => {
  const { ui, state, card, button } = await agents(t, { confirm: false });
  // A server saved as a server is already assigned.
  button(card('a1'), 'Save assignment').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ui.confirmations.length, 0);
  const type = ui.document.getElementById('agent-type-a1');
  type.value = 'workstation';
  type.dispatchEvent(new ui.window.Event('change'));
  ui.document.getElementById('agent-person-a1').value = 'p1';
  button(card('a1'), 'Save assignment').click();
  await settle(() => ui.confirmations.length === 1);
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(state.patched, []);
});

test('a reload already under way is not repeated, and the list refreshes while visible', async (t) => {
  const { ui, state, $ } = await agents(t);
  assert.equal(state.gets, 1);
  let release;
  state.gate = new Promise((resolve) => {
    release = resolve;
  });
  $('refresh').click();
  $('refresh').click();
  await settle(() => state.gets === 2);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.gets, 2);
  release();
  state.gate = null;
  await settle(() => ui.idle());
  const tick = state.intervals.find((i) => i.ms === 30000).fn;
  // A hidden page waits; a visible one reloads on each tick.
  Object.defineProperty(ui.document, 'hidden', { value: true, configurable: true });
  tick();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.gets, 2);
  Object.defineProperty(ui.document, 'hidden', { value: false, configurable: true });
  tick();
  await settle(() => state.gets === 3);
  await settle(() => ui.idle());
  await new Promise((r) => setTimeout(r, 10));
});
