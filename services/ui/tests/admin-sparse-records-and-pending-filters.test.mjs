// The administration page with sparse records, plain-text runner answers,
// declined deletions, lists that load on a second attempt, and typed filters
// still waiting to apply when the user pages or refreshes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));

class FakeModal {
  static getOrCreateInstance() {
    return { show() {} };
  }
}

async function admin(t, options = {}) {
  const state = {
    calls: [],
    fail: { ...options.fail },
    debounced: [],
    users: options.users ?? [
      {
        id: 'u1',
        username: 'dana',
        email: 'dana@example.test',
        role: 'admin',
        effective_role: 'admin',
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-03-01T00:00:00Z',
      },
    ],
    groups: options.groups ?? [
      { id: 'g1', name: 'Auditors', role: null, member_count: 0, permission_count: 0 },
    ],
    group: options.group,
    controls: options.controls ?? [],
  };
  const record = (method, url, body) =>
    state.calls.push({ method, url, body: body === undefined ? null : plain(body) });
  const ui = await loadUi('pages/admin.js', {
    html,
    url: 'https://keen.example/admin.html' + (options.hash || ''),
    modules: {
      'pages/confirm-modal.js': { confirmAction: async () => options.confirm ?? true },
    },
    setup(window) {
      window.bootstrap = {
        Modal: FakeModal,
        Tab: { getOrCreateInstance: () => ({ show() {} }) },
        Collapse: { getOrCreateInstance: () => ({ show() {} }) },
      };
    },
    globals: { setInterval: () => 0, clearInterval: () => {}, FormData, prompt: () => '' },
    vendor: {
      initNavbar: async () => ({
        is_admin: true,
        preferences: { auto_apply_filters: !!options.autoApply },
      }),
      initCollapsibleFilterSections: () => {},
      // Like the design system's debounce: a call waits, and cancel drops it.
      debounce: (fn) => {
        let pending = null;
        const wrapped = (...args) => {
          pending = args;
        };
        wrapped.cancel = () => {
          pending = null;
        };
        wrapped.flush = () => {
          if (pending) fn(...pending);
          pending = null;
        };
        state.debounced.push(wrapped);
        return wrapped;
      },
      withFramework: (path, fw) => `${path}${path.includes('?') ? '&' : '?'}framework=${fw}`,
      getCurrentFramework: () => options.framework ?? 'A',
      fmtTs: (v) => `ts(${v})`,
      toast: (el, message, kind) => {
        if (el) {
          el.textContent = message;
          el.dataset.alert = kind;
        }
      },
      apiGet: async (url) => {
        record('GET', url);
        const path = url.split('?')[0];
        if (path === '/api/v1/admin/ingest/status') return { enabled: {}, cloud: {} };
        if (path === '/api/v1/users') return structuredClone(state.users);
        if (path === '/api/v1/admin/groups') {
          if (state.fail.groups) throw new Error('Groups offline');
          return structuredClone(state.groups);
        }
        if (path === '/api/v1/admin/permissions') {
          if (state.fail.permissions) throw new Error('Permissions offline');
          return [{ code: 'audits.view', description: 'View audits' }];
        }
        if (path.startsWith('/api/v1/admin/groups/'))
          return (
            state.group ?? {
              id: 'g1',
              name: 'Auditors',
              description: null,
              role: null,
              created_at: '2026-02-01',
              user_ids: [],
              permission_codes: [],
            }
          );
        if (path.startsWith('/api/v1/users/') && path.endsWith('/access'))
          return {
            user_id: 'u1',
            group_ids: ['g1'],
            explicit_permission_codes: [],
            effective_permission_codes: ['audits.view'],
          };
        if (path === '/api/v1/controls') return { items: structuredClone(state.controls) };
        if (path === '/api/v1/admin/entity-changelog') return { items: [], total: 60 };
        if (path === '/api/v1/admin/audit') return { items: [], total: 160 };
        if (path === '/api/v1/admin/source-catalogue') return { items: [] };
        return { items: [] };
      },
      apiPost: async (url, body) => {
        record('POST', url, body);
        if (options.post) return options.post(url, body);
        if (url === '/api/v1/admin/groups') return { id: 'g9' };
        return { ok: true };
      },
      apiPostForm: async (url, body) => {
        record(
          'POSTFORM',
          url,
          [...body.entries()].map(([k, v]) => [k, String(v)]),
        );
        return { event_id: 'ev1' };
      },
      apiPatch: async (url, body) => {
        record('PATCH', url, body);
        return { id: 'g1' };
      },
      apiPut: async (url, body) => {
        record('PUT', url, body);
        return {};
      },
      apiDelete: async (url) => {
        record('DELETE', url);
        return {};
      },
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  await new Promise((r) => setTimeout(r, 15));
  const calls = (method, prefix) =>
    state.calls.filter((c) => c.method === method && c.url.startsWith(prefix));
  const flush = () => state.debounced.forEach((d) => d.flush());
  return { ui, el, state, calls, flush };
}

test('without a current framework the diary uses the default catalogue', async (t) => {
  const { ui, el, calls } = await admin(t, {
    framework: '',
    controls: [{ id: 'c1', ref: 'A.5.1', title: 'Policies', type: 'annex_control' }],
  });
  assert.equal(
    calls('GET', '/api/v1/controls')[0].url,
    '/api/v1/controls?framework=iso_27001_2022&limit=5000',
  );
  el('diarySummary').value = 'Review';
  el('diaryForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Open:/.test(el('out').textContent));
  assert.match(el('out').textContent, /event\.html\?id=ev1&framework=iso_27001_2022/);
});

test('a runner answering with plain text shows that text as sent', async (t) => {
  const { ui, el } = await admin(t, { post: () => 'Queued for the next worker.' });
  ui.document.querySelector('[data-run="/api/v1/admin/ingest/rss/run"]').click();
  await settle(() => el('status').textContent === 'OK');
  assert.equal(el('out').textContent, 'Queued for the next worker.');
});

test('each user shows when the account was last updated', async (t) => {
  const { el } = await admin(t);
  assert.match(el('usersRows').textContent, /Updated ts\(2026-03-01T00:00:00Z\)/);
});

test('declining a user or group deletion keeps it', async (t) => {
  const { el, calls } = await admin(t, { confirm: false });
  el('usersRows').querySelector('[data-delete]').click();
  el('groupsRows').querySelector('tr').click();
  await settle(() => el('groupName').value === 'Auditors');
  el('groupDelete').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(calls('DELETE', '/'), []);
  assert.equal(el('groupDetail').style.display, 'block');
  assert.equal(el('usersRows').querySelectorAll('tr[data-user-id]').length, 1);
});

test('the access dialog loads groups and permissions that failed to load earlier', async (t) => {
  const { el, state, calls } = await admin(t, { fail: { groups: true, permissions: true } });
  assert.match(el('groupsStatus').textContent, /Groups offline/);
  state.fail = {};
  el('usersRows').querySelector('[data-access]').click();
  await settle(() => el('userAccessEffective').textContent === 'audits.view');
  assert.deepEqual(
    [...el('userAccessGroups').options].map((o) => [o.textContent, o.selected]),
    [['Auditors', true]],
  );
  assert.deepEqual(
    [...el('userAccessPerms').options].map((o) => o.textContent),
    ['audits.view - View audits'],
  );
  assert.equal(calls('GET', '/api/v1/admin/groups').length, 2);
});

test('a group without a role or description opens with both blank', async (t) => {
  const { el } = await admin(t);
  el('groupDesc').value = 'left over';
  el('groupsRows').querySelector('tr').click();
  await settle(() => el('groupName').value === 'Auditors');
  assert.equal(el('groupRole').value, '');
  assert.equal(el('groupRole').selectedOptions[0].textContent, 'No role');
  assert.equal(el('groupDesc').value, '');
  assert.equal(el('groupDetailMeta').textContent, 'Created ts(2026-02-01)');
});

test('a new group without a role is created with none', async (t) => {
  const { ui, el, calls } = await admin(t);
  el('groupNew').click();
  el('groupName').value = 'Readers';
  el('groupForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => calls('POST', '/api/v1/admin/groups').length === 1);
  assert.deepEqual(calls('POST', '/api/v1/admin/groups')[0].body, {
    name: 'Readers',
    description: null,
    role: null,
  });
});

test('a cleared remap limit sends the default of 500', async (t) => {
  const { el, calls } = await admin(t);
  el('remapLimit').value = '';
  el('remapBtn').click();
  await settle(() => calls('POST', '/api/v1/admin/remap/run').length === 1);
  assert.equal(
    calls('POST', '/api/v1/admin/remap/run')[0].url,
    '/api/v1/admin/remap/run?limit=500',
  );
});

test('untitled controls are found by reference alone and chosen only once', async (t) => {
  const { ui, el } = await admin(t, {
    controls: [{ id: 'c1', ref: 'A.8.1', title: null, type: 'annex_control' }],
  });
  const search = (q) => {
    el('controlSearch').value = q;
    el('controlSearch').dispatchEvent(new ui.window.Event('input'));
  };
  search('null');
  assert.equal(el('controlResults').querySelectorAll('[data-id]').length, 0);
  search('a.8');
  const result = el('controlResults').querySelector('[data-id="c1"]');
  assert.equal(result.textContent, 'A.8.1');
  assert.equal(result.querySelector('.small-muted'), null);
  result.click();
  search('a.8');
  const again = el('controlResults').querySelector('[data-id="c1"]');
  assert.ok(again.classList.contains('disabled'));
  again.click();
  assert.deepEqual(
    [...el('controlSelected').querySelectorAll('[data-remove]')].map((b) => b.dataset.remove),
    ['c1'],
  );
});

test('paging or refreshing the changelog drops a typed filter still waiting to apply', async (t) => {
  const { ui, el, calls, flush } = await admin(t, { autoApply: true });
  const changelog = () => calls('GET', '/api/v1/admin/entity-changelog');
  const type = (value) => {
    el('entityChangeUser').value = value;
    el('entityChangeUser').dispatchEvent(new ui.window.Event('input'));
  };
  for (const act of [
    () => el('entityChangeNext').click(),
    () => el('entityChangePrev').click(),
    () => el('entityChangeRefresh').click(),
    () =>
      el('entityChangeQuery').dispatchEvent(
        new ui.window.KeyboardEvent('keydown', { key: 'Enter', cancelable: true }),
      ),
  ]) {
    type('dana');
    const before = changelog().length;
    act();
    await settle(() => changelog().length === before + 1);
    // The waiting apply was dropped, so nothing more is requested.
    flush();
    await new Promise((r) => setTimeout(r, 5));
    assert.equal(changelog().length, before + 1);
  }
  assert.match(changelog().at(-1).url, /username=dana/);
});

test('paging or refreshing the audit trail drops a typed filter still waiting to apply', async (t) => {
  const { ui, el, calls, flush } = await admin(t, { autoApply: true });
  const audit = () => calls('GET', '/api/v1/admin/audit');
  for (const button of ['auditNext', 'auditPrev', 'auditRefresh']) {
    el('auditPath').value = '/api/v1/users';
    el('auditPath').dispatchEvent(new ui.window.Event('input'));
    const before = audit().length;
    el(button).click();
    await settle(() => audit().length === before + 1);
    flush();
    await new Promise((r) => setTimeout(r, 5));
    assert.equal(audit().length, before + 1, button);
  }
  assert.match(audit().at(-1).url, /offset=0&path=%2Fapi%2Fv1%2Fusers/);
});
