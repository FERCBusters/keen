// Enrollment profiles: rotating a fleet's bootstrap key, the settings a
// profile card describes and the expiry a new profile is created with.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

function profile(overrides = {}) {
  return {
    id: 'p1',
    name: 'Web fleet',
    state: 'active',
    token_days: 30,
    enrollment_count: 2,
    max_enrollments: null,
    evidence_mode_ceiling: 'events',
    subject_type: 'pairwise',
    allowed_cidrs: [],
    expires_at: null,
    ...overrides,
  };
}

async function profiles(t, { items = [profile()], confirm = true, post } = {}) {
  const state = { posted: [], confirms: [], changed: 0, items };
  const ui = await loadUi('pages/agent-enrollment.js', {
    html: '<section id="host"></section>',
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (options) => {
          state.confirms.push(options);
          return confirm;
        },
      },
    },
    vendor: {
      fmtTs: (value) => `ts(${value})`,
      apiGet: async () => ({ profiles: structuredClone(state.items) }),
      apiPost: async (url, body) => {
        state.posted.push({ url, body: JSON.parse(JSON.stringify(body)) });
        return post ? post(url, body) : { profile: profile() };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const controller = ui.api.enrollmentProfiles(host, async () => {
    state.changed++;
  });
  await controller.refresh(false);
  const action = (label) =>
    [...host.querySelectorAll('[data-actions] button')].find((b) => b.textContent === label);
  return { ui, host, state, controller, action };
}

test('rotating a bootstrap key asks for typed confirmation and shows the new key once', async (t) => {
  const { host, state, action } = await profiles(t, {
    post: () => ({ profile: profile(), bootstrap_key: 'ke_rotated-<b>key</b>' }),
  });
  await action('Rotate bootstrap key').onclick();
  assert.equal(state.confirms[0].title, 'Rotate bootstrap key');
  assert.equal(state.confirms[0].typed, 'Rotate');
  assert.deepEqual(state.posted, [
    { url: '/api/v1/admin/agent-enrollment-profiles/p1/rotate', body: {} },
  ]);
  const secret = host.querySelector('#ep-secret');
  assert.equal(secret.value, 'ke_rotated-<b>key</b>');
  assert.equal(secret.type, 'password');
  assert.equal(host.querySelector('[data-secret-box]').hidden, false);
  assert.equal(state.changed, 1);
  // The rebuilt card offers the same actions again.
  assert.equal(action('Rotate bootstrap key').disabled, false);
});

test('a declined rotation keeps the current key and sends nothing', async (t) => {
  const { host, state, action } = await profiles(t, { confirm: false });
  host.querySelector('#ep-secret').value = 'ke_shown-earlier';
  host.querySelector('[data-secret-box]').hidden = false;
  await action('Rotate bootstrap key').onclick();
  assert.equal(state.confirms.length, 1);
  assert.deepEqual(state.posted, []);
  assert.equal(host.querySelector('#ep-secret').value, 'ke_shown-earlier');
  assert.equal(state.changed, 0);
});

test('pausing enrollment needs no confirmation and reveals no key', async (t) => {
  const { host, state, action } = await profiles(t);
  await action('Pause enrollment').onclick();
  assert.deepEqual(state.confirms, []);
  assert.deepEqual(state.posted, [
    { url: '/api/v1/admin/agent-enrollment-profiles/p1/pause', body: {} },
  ]);
  assert.equal(host.querySelector('[data-secret-box]').hidden, true);
  assert.equal(state.changed, 1);
});

test('a profile card names its pseudonyms, expiry, networks and enrollment limit', async (t) => {
  const { host } = await profiles(t, {
    items: [
      profile({
        name: '<img src=x>Fleet',
        subject_type: 'public',
        expires_at: '2026-12-01T00:00:00Z',
        allowed_cidrs: ['192.0.2.0/24', '2001:db8::/32'],
        max_enrollments: 10,
        evidence_mode_ceiling: 'all_lines',
      }),
      profile({ id: 'p2', name: 'Open fleet', evidence_mode_ceiling: 'future_mode' }),
    ],
  });
  const [limited, open] = host.querySelectorAll('[data-profiles] article');
  assert.equal(host.querySelector('img'), null);
  assert.match(
    limited.textContent,
    /2 enrolled \/ 10 maximum · Keys last 30 days · All log lines · Public pseudonyms/,
  );
  assert.match(
    limited.textContent,
    /Enrollment expires: ts\(2026-12-01T00:00:00Z\) · Networks: 192\.0\.2\.0\/24, 2001:db8::\/32/,
  );
  assert.match(
    open.textContent,
    /2 enrolled · Keys last 30 days · future_mode · Pairwise pseudonyms/,
  );
  assert.match(
    open.textContent,
    /Enrollment expires: No expiry · Networks: Any address with a valid key/,
  );
});

test('a new profile sends its local expiry as a UTC timestamp', async (t) => {
  const { host, state } = await profiles(t, {
    items: [],
    post: () => ({ profile: profile(), bootstrap_key: 'ke_new' }),
  });
  host.querySelector('#ep-name').value = 'Batch fleet';
  host.querySelector('#ep-expires').value = '2026-12-01T09:30';
  host.querySelector('#ep-cidrs').value = ' 192.0.2.0/24 , ,198.51.100.0/24';
  await host.querySelector('form').onsubmit({ preventDefault() {} });
  const sent = state.posted[0].body;
  assert.equal(sent.expires_at, new Date('2026-12-01T09:30').toISOString());
  assert.deepEqual(sent.allowed_cidrs, ['192.0.2.0/24', '198.51.100.0/24']);
  assert.equal(sent.max_enrollments, null);
  assert.equal(
    host.querySelector('[data-status]').textContent,
    'Enrollment profile created. Save its bootstrap key.',
  );
  // Without an expiry the profile never expires.
  host.querySelector('#ep-expires').value = '';
  await host.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(state.posted[1].body.expires_at, null);
});
