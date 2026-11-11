import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

// --- Account, Security: connected AI apps ---------------------------------------

async function connections(t, { data, remove, confirm = true, clipboard } = {}) {
  const state = { data: structuredClone(data), removed: [] };
  const ui = await page(t, 'mcp-connections', {
    htmlName: 'account',
    confirm,
    globals: { navigator: { clipboard } },
    get: async (u) => {
      if (u.pathname !== '/api/v1/me/mcp') throw new Error('Unexpected GET ' + u);
      if (state.data instanceof Error) throw state.data;
      return structuredClone(state.data);
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return remove(u, state);
    },
  });
  await settle(() => ui.idle() && ui.calls.length);
  return { ...ui, state };
}

const grant = (overrides = {}) => ({
  id: 'g/1',
  client_name: '<b>Claude</b>',
  redirect_host: '127.0.0.1:33418',
  scopes: ['isms.read', 'risk.read'],
  created_at: '2026-11-01T10:00:00',
  last_used_at: '2026-11-05T08:00:00',
  expires_at: '2027-01-30T10:00:00',
  ...overrides,
});

test('the security card stays hidden while MCP is off', async (t) => {
  const ui = await connections(t, { data: { enabled: false } });
  assert.equal(ui.el('mcpCard').hidden, true);
  assert.equal(ui.el('mcpGrants').children.length, 0);
});

test('connected apps are listed as text with their reach and dates', async (t) => {
  const ui = await connections(t, {
    data: {
      enabled: true,
      mcp_url: 'https://keen.example/api/v1/mcp',
      grants: [
        grant(),
        grant({ id: 'g2', scopes: [], last_used_at: null }),
        grant({
          id: 'g3',
          scopes: ['isms.read', 'isms.manage', 'events.read', 'rules.write'],
          changes: ['isms.manage', 'rules.write'],
        }),
      ],
    },
  });
  assert.equal(ui.el('mcpCard').hidden, false);
  assert.equal(ui.el('mcpUrl').value, 'https://keen.example/api/v1/mcp');
  assert.equal(ui.el('mcpStatus').textContent, 'Connected AI apps:');
  const [first, second, third] = ui.el('mcpGrants').children;
  assert.equal(first.querySelector('.fw-semibold').textContent, '<b>Claude</b>');
  assert.equal(first.querySelector('b'), null);
  assert.equal(
    first.querySelector('.small').textContent,
    'Returns to 127.0.0.1:33418. Reads: isms.read, risk.read. Approved 2026-11-01, last used 2026-11-05, ends 2027-01-30.',
  );
  assert.match(second.textContent, /Reads: frameworks and controls only\./);
  assert.match(second.textContent, /last used never/);
  assert.match(
    third.textContent,
    /Reads: isms\.read, events\.read\. Adds and updates: isms\.manage, rules\.write\. Approved/,
  );
});

test('no connected apps says so', async (t) => {
  const ui = await connections(t, { data: { enabled: true, mcp_url: 'u', grants: [] } });
  assert.equal(ui.el('mcpStatus').textContent, 'No AI apps are connected to your account.');
});

test('revoking asks first, removes the connection and reloads the list', async (t) => {
  let answer = false;
  const ui = await connections(t, {
    data: { enabled: true, mcp_url: 'u', grants: [grant()] },
    confirm: (options) => {
      assert.equal(options.title, 'Revoke AI connection');
      assert.match(options.message, /Revoke <b>Claude<\/b>\? It stops using Keen at once/);
      return answer;
    },
    remove: async (u, state) => {
      state.data.grants = [];
      return { ok: true };
    },
  });
  const button = () => ui.el('mcpGrants').querySelector('button');
  button().click();
  await settle(() => ui.confirmations.length === 1);
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(ui.state.removed, []);
  answer = true;
  button().click();
  await settle(() => ui.state.removed.length === 1 && ui.el('mcpGrants').children.length === 0);
  assert.deepEqual(ui.state.removed, ['/api/v1/me/mcp/grants/g%2F1']);
  assert.equal(ui.el('mcpStatus').textContent, 'No AI apps are connected to your account.');
});

test('a failed revocation reports the error and keeps the button usable', async (t) => {
  const ui = await connections(t, {
    data: { enabled: true, mcp_url: 'u', grants: [grant()] },
    remove: async () => {
      throw new Error('Connection not found');
    },
  });
  const button = ui.el('mcpGrants').querySelector('button');
  button.click();
  await settle(() => ui.el('mcpStatus').textContent === 'Connection not found');
  assert.equal(button.disabled, false);
});

test('the server address copies to the clipboard, or is selected for copying', async (t) => {
  const copied = [];
  const ui = await connections(t, {
    data: { enabled: true, mcp_url: 'https://keen.example/api/v1/mcp', grants: [] },
    clipboard: { writeText: async (text) => copied.push(text) },
  });
  ui.el('mcpCopy').click();
  await settle(() => ui.el('mcpStatus').textContent === 'Server address copied.');
  assert.deepEqual(copied, ['https://keen.example/api/v1/mcp']);
  const failing = await connections(t, {
    data: { enabled: true, mcp_url: 'x', grants: [] },
    clipboard: {
      writeText: async () => {
        throw new Error('denied');
      },
    },
  });
  failing.el('mcpCopy').click();
  await settle(() => /Clipboard unavailable/.test(failing.el('mcpStatus').textContent));
});

test('a failed load shows its error in the card', async (t) => {
  const ui = await connections(t, { data: new Error('Session expired') });
  await settle(() => ui.el('mcpStatus').textContent === 'Session expired');
  assert.equal(ui.el('mcpCard').hidden, false);
});

// --- The consent page -------------------------------------------------------

async function consent(t, { query = '&request=kcr_key', view, answer } = {}) {
  const navigations = [];
  const posted = [];
  const ui = await page(t, 'mcp-authorize', {
    query,
    globals: {
      location: {
        search: query ? '?framework=A' + query : '',
        pathname: '/mcp-authorize.html',
        assign: (url) => navigations.push(url),
      },
    },
    post: async (u, body) => {
      posted.push({ path: u.pathname, body: structuredClone(body) });
      if (u.pathname === '/api/v1/me/mcp/consent/view') return view();
      if (u.pathname === '/api/v1/me/mcp/consent') return answer(body);
      throw new Error('Unexpected POST ' + u);
    },
  });
  if (view) await settle(() => posted.length && ui.idle());
  await new Promise((r) => setTimeout(r, 5));
  return { ...ui, navigations, posted };
}

const VIEW = {
  client_name: '<img src=x onerror=alert(1)>',
  redirect_host: '127.0.0.1:33418',
  scopes: [
    { scope: 'isms.read', description: 'ISMS records', requested: true },
    { scope: 'events.read', description: 'Evidence events', requested: false },
  ],
};

test('without a request the page explains how a connection starts', async (t) => {
  const ui = await consent(t, { query: '' });
  assert.match(ui.el('consentStatus').textContent, /Start the connection from your AI app/);
  assert.equal(ui.el('consentForm').hidden, true);
  assert.deepEqual(ui.posted, []);
});

test('the request is shown as text, with the scopes the client asked for chosen', async (t) => {
  const ui = await consent(t, { view: () => structuredClone(VIEW) });
  assert.deepEqual(ui.posted[0], {
    path: '/api/v1/me/mcp/consent/view',
    body: { request: 'kcr_key' },
  });
  // The key leaves the address bar before the page asks anything.
  assert.equal(ui.window.location.search, '');
  assert.equal(ui.el('consentForm').hidden, false);
  assert.equal(ui.el('consentClient').textContent, VIEW.client_name);
  assert.equal(ui.document.querySelector('img'), null);
  assert.equal(ui.el('consentRedirect').textContent, '127.0.0.1:33418');
  const boxes = [...ui.el('consentScopes').querySelectorAll('input')];
  assert.deepEqual(
    boxes.map((box) => [box.id, box.value, box.checked]),
    [
      ['scope-isms-read', 'isms.read', true],
      ['scope-events-read', 'events.read', false],
    ],
  );
  assert.equal(ui.el('consentScopes').querySelector('label').textContent, 'ISMS records');
  assert.equal(ui.el('consentNoScopes').hidden, true);
});

const CHANGING = {
  ...VIEW,
  scopes: [
    ...VIEW.scopes,
    { scope: 'risk.read', description: 'Risks', requested: false },
    {
      scope: 'isms.manage',
      description: 'Add ISMS records',
      requested: true,
      changes: true,
      includes: 'isms.read',
    },
    {
      scope: 'risk.manage',
      description: 'Add risks',
      requested: false,
      changes: true,
      includes: 'risk.read',
    },
    {
      scope: 'rules.write',
      description: 'Add mapping rules',
      requested: false,
      changes: true,
      includes: 'pestle.read',
    },
  ],
};

test('changes are offered apart and start unticked, whatever the app asked', async (t) => {
  const ui = await consent(t, { view: () => structuredClone(VIEW) });
  assert.equal(ui.el('consentChangesSet').hidden, true);
  assert.equal(ui.el('consentWants').textContent, 'wants to read Keen as you.');

  const changing = await consent(t, {
    view: () => structuredClone(CHANGING),
    answer: () => ({ redirect: 'http://127.0.0.1:33418/callback?code=kac_x' }),
  });
  assert.equal(changing.el('consentChangesSet').hidden, false);
  assert.equal(changing.el('consentWants').textContent, 'wants to read and change Keen as you.');
  const reads = [...changing.el('consentScopes').querySelectorAll('input')];
  assert.deepEqual(
    reads.map((box) => box.value),
    ['isms.read', 'events.read', 'risk.read'],
  );
  const boxes = [...changing.el('consentChanges').querySelectorAll('input')];
  assert.deepEqual(
    boxes.map((box) => [box.value, box.checked]),
    [
      ['isms.manage', false],
      ['risk.manage', false],
      ['rules.write', false],
    ],
  );
  // Ticking a change ticks and holds its read scope; unticking releases it.
  const risks = changing.el('scope-risk-read');
  const manage = changing.el('scope-risk-manage');
  manage.checked = true;
  manage.dispatchEvent(new changing.window.Event('change'));
  assert.equal(risks.checked, true);
  assert.equal(risks.disabled, true);
  manage.checked = false;
  manage.dispatchEvent(new changing.window.Event('change'));
  assert.equal(risks.checked, true);
  assert.equal(risks.disabled, false);
  // A change whose read scope is not offered changes nothing else.
  const rules = changing.el('scope-rules-write');
  rules.checked = true;
  rules.dispatchEvent(new changing.window.Event('change'));
  manage.checked = true;
  manage.dispatchEvent(new changing.window.Event('change'));
  changing
    .el('consentForm')
    .dispatchEvent(new changing.window.Event('submit', { cancelable: true }));
  await settle(() => changing.navigations.length === 1);
  assert.deepEqual(changing.posted[1].body.scopes, [
    'isms.read',
    'risk.read',
    'risk.manage',
    'rules.write',
  ]);
});

test('an account without readable scopes is told so', async (t) => {
  const ui = await consent(t, { view: () => ({ ...VIEW, scopes: [] }) });
  assert.equal(ui.el('consentNoScopes').hidden, false);
});

test('a request that cannot be loaded shows why', async (t) => {
  const ui = await consent(t, {
    view: () => {
      throw new Error('This request has expired.');
    },
  });
  assert.equal(ui.el('consentStatus').textContent, 'This request has expired.');
  assert.equal(ui.el('consentForm').hidden, true);
});

test('approving sends the chosen scopes and returns to the app', async (t) => {
  const ui = await consent(t, {
    view: () => structuredClone(VIEW),
    answer: () => ({ redirect: 'http://127.0.0.1:33418/callback?code=kac_x&state=s' }),
  });
  ui.el('scope-events-read').checked = true;
  ui.el('consentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.navigations.length === 1);
  assert.deepEqual(ui.posted[1].body, {
    request: 'kcr_key',
    approve: true,
    scopes: ['isms.read', 'events.read'],
  });
  assert.deepEqual(ui.navigations, ['http://127.0.0.1:33418/callback?code=kac_x&state=s']);
  assert.equal(ui.el('consentForm').hidden, true);
  assert.equal(ui.el('consentStatus').textContent, 'Returning you to the app.');
});

test('refusing sends no scopes as approved and returns to the app', async (t) => {
  const ui = await consent(t, {
    view: () => structuredClone(VIEW),
    answer: () => ({ redirect: 'cursor://keen/callback?error=access_denied' }),
  });
  ui.el('consentRefuse').click();
  await settle(() => ui.navigations.length === 1);
  assert.equal(ui.posted[1].body.approve, false);
  assert.deepEqual(ui.navigations, ['cursor://keen/callback?error=access_denied']);
});

test('the page never navigates to a script or unparsable address', async (t) => {
  for (const redirect of ['javascript:alert(1)', 'not a url', 'data:text/html,x']) {
    const ui = await consent(t, {
      view: () => structuredClone(VIEW),
      answer: () => ({ redirect }),
    });
    ui.el('consentRefuse').click();
    await settle(
      () => ui.el('consentStatus').textContent === 'Keen could not return you to the app.',
    );
    assert.deepEqual(ui.navigations, []);
  }
});

test('a failed answer reports the error and re-enables the buttons', async (t) => {
  const ui = await consent(t, {
    view: () => structuredClone(VIEW),
    answer: () => {
      throw new Error('CSRF token missing or invalid');
    },
  });
  ui.el('consentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('consentStatus').textContent === 'CSRF token missing or invalid');
  assert.equal(ui.el('consentApprove').disabled, false);
  assert.equal(ui.el('consentRefuse').disabled, false);
  assert.equal(ui.el('consentForm').hidden, false);
});
