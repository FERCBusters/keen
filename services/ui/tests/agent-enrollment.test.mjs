import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

test('enrollment profile creation displays a one-time key and clears it on navigation', async (t) => {
  let saved;
  const p = {
    id: 'profile',
    name: 'Web fleet',
    state: 'active',
    token_days: 30,
    enrollment_count: 0,
    max_enrollments: 5,
    allowed_cidrs: [],
    expires_at: null,
  };
  const ui = await loadUi('pages/agent-enrollment.js', {
    html: '<section id="host"></section>',
    vendor: {
      apiGet: async () => ({ profiles: [p] }),
      apiPost: async (url, body) => {
        saved = { url, body };
        return { profile: p, bootstrap_key: 'ke_test-secret' };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host'),
    controller = ui.api.enrollmentProfiles(host, async () => {});
  await controller.refresh(false);
  const badge = host.querySelector('[data-profiles] .badge');
  assert.equal(
    badge.className,
    'badge d-inline-flex align-items-center justify-content-center py-2 px-3 lh-sm text-bg-success',
  );
  assert.equal(badge.closest('h3'), null);
  host.querySelector('#ep-name').value = 'Web fleet';
  host.querySelector('#ep-days').value = '30';
  host.querySelector('#ep-limit').value = '5';
  host.querySelector('#ep-labels').value = 'environment=production';
  await host.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved.body.name, 'Web fleet');
  assert.equal(saved.body.labels.environment, 'production');
  assert.equal(host.querySelector('#ep-secret').value, 'ke_test-secret');
  assert.equal(host.querySelector('#ep-secret').type, 'password');
  ui.window.dispatchEvent(new ui.window.Event('pagehide'));
  assert.equal(host.querySelector('#ep-secret').value, '');
  assert.equal(host.querySelector('[data-secret-box]').hidden, true);
});

test('profile revocation is explicit and revoked profiles have no reactivation controls', async (t) => {
  const calls = [];
  const confirms = [];
  let p = {
    id: 'profile',
    name: 'Fleet <x>',
    state: 'active',
    token_days: 90,
    enrollment_count: 3,
    allowed_cidrs: [],
  };
  const ui = await loadUi('pages/agent-enrollment.js', {
    html: '<section id="host"></section>',
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (options) => {
          confirms.push(options);
          return true;
        },
      },
    },
    vendor: {
      apiGet: async () => ({ profiles: [p] }),
      apiPost: async (url) => {
        calls.push(url);
        p = { ...p, state: 'revoked' };
        return { profile: p };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host'),
    controller = ui.api.enrollmentProfiles(host, async () => {});
  await controller.refresh(false);
  assert.equal(host.querySelector('x'), null);
  await [...host.querySelectorAll('[data-actions] button')]
    .find((b) => b.textContent === 'Revoke profile and agents')
    .onclick();
  assert.equal(calls[0], '/api/v1/admin/agent-enrollment-profiles/profile/revoke');
  assert.equal(confirms[0].danger, true, 'revocation asks through the danger confirm dialog');
  assert.equal(confirms[0].typed, undefined, 'revocation does not ask for typed confirmation');
  assert.equal(host.querySelectorAll('[data-actions] button').length, 0);
  await controller.refresh(true);
  assert.equal(host.querySelector('[data-create]').disabled, true);
});

test("creation errors are shown as the server's detail, and the key can be revealed or copied", async (t) => {
  let failure = null;
  const copied = [];
  const ui = await loadUi('pages/agent-enrollment.js', {
    html: '<section id="host"></section>',
    setup(window) {
      Object.defineProperty(window.navigator, 'clipboard', {
        configurable: true,
        value: {
          writeText: async (text) => {
            if (failure === 'clipboard') throw new Error('denied');
            copied.push(text);
          },
        },
      });
    },
    vendor: {
      apiGet: async () => ({ profiles: [] }),
      apiPost: async () => {
        if (failure) throw new Error(failure);
        return {
          profile: { id: 'p', name: 'Fleet', state: 'active', token_days: 1, allowed_cidrs: [] },
          bootstrap_key: 'ke_secret',
        };
      },
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const controller = ui.api.enrollmentProfiles(host, async () => {});
  await controller.refresh(false);
  assert.match(
    host.textContent,
    /No enrollment profiles\. Individual agents can still be registered separately\./,
  );
  const status = () => host.querySelector('[data-status]').textContent;
  const submit = () => host.querySelector('form').onsubmit({ preventDefault() {} });
  host.querySelector('#ep-name').value = 'Fleet';
  host.querySelector('#ep-days').value = '1';

  // A malformed label is refused before any request.
  host.querySelector('#ep-labels').value = 'environment';
  await submit();
  assert.equal(status(), 'Use key=value for each label.');
  host.querySelector('#ep-labels').value = '';

  failure = '{"detail":"Name already in use"}';
  await submit();
  assert.equal(status(), 'Name already in use');
  failure = '{"detail":[{"msg":"token_days too large"}]}';
  await submit();
  assert.equal(status(), 'Check the profile settings.');
  failure = 'Network down';
  await submit();
  assert.equal(status(), 'Network down');
  assert.equal(host.querySelector('[data-create]').disabled, false);

  failure = null;
  await submit();
  const secret = host.querySelector('#ep-secret');
  assert.equal(secret.value, 'ke_secret');
  host.querySelector('[data-reveal]').onclick();
  assert.equal(secret.type, 'text');
  assert.equal(host.querySelector('[data-reveal]').textContent, 'Hide key');
  host.querySelector('[data-reveal]').onclick();
  assert.equal(secret.type, 'password');
  assert.equal(host.querySelector('[data-reveal]').textContent, 'Show key');
  await host.querySelector('[data-copy]').onclick();
  assert.deepEqual(copied, ['ke_secret']);
  assert.equal(status(), 'Bootstrap key copied.');
  failure = 'clipboard';
  await host.querySelector('[data-copy]').onclick();
  assert.equal(status(), 'Use Show key to copy it manually.');
});
