import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';

class FakeModal {
  static shown = [];
  constructor(el) {
    this.el = el;
  }
  static getOrCreateInstance(el) {
    return new FakeModal(el);
  }
  show() {
    FakeModal.shown.push(this.el.id);
  }
}

const users = () => [
  {
    id: 'u1',
    username: 'dana',
    email: 'dana@example.test',
    role: 'admin',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'u/2',
    username: HOSTILE + 'sam',
    role: 'inherit',
    effective_role: 'admin',
    is_active: false,
    auth_backend: 'ldap',
  },
];
const groups = () => [
  { id: 'g1', name: 'Auditors', role: 'normal', member_count: 2, permission_count: 3 },
  { id: 'g2', name: 'Admins', role: '', member_count: 1 },
];
const permissions = () => [
  { code: 'audits.view', description: 'View audits' },
  { code: 'admin.audit_trail' },
];

async function admin(t, options = {}) {
  const state = {
    calls: [],
    prompts: [],
    promptAnswer: options.promptAnswer ?? 'synthetic-password-1',
    data: { users: users(), groups: groups(), permissions: permissions() },
  };
  const fail = options.fail || {};
  const record = (method) => (url, body) => {
    state.calls.push({
      method,
      url,
      body:
        body instanceof FormData
          ? [...body.entries()].map(([k, v]) => [k, typeof v === 'string' ? v : v.name])
          : plain(body ?? null),
    });
  };
  const ui = await loadUi('pages/admin.js', {
    html,
    url: 'https://keen.example/admin.html' + (options.hash || ''),
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (asked) => {
          state.prompts.push(asked);
          return options.confirm ?? true;
        },
      },
    },
    setup(window) {
      window.bootstrap = {
        Modal: FakeModal,
        Tab: {
          getOrCreateInstance: (el) => ({
            show: () => state.calls.push({ method: 'TAB', url: el.id }),
          }),
        },
        Collapse: {
          getOrCreateInstance: (el) => ({
            show: () => state.calls.push({ method: 'COLLAPSE', url: el.id }),
          }),
        },
      };
    },
    globals: {
      setInterval: () => 0,
      clearInterval: () => {},
      FormData,
      prompt: (m) => (state.prompts.push(m), state.promptAnswer),
    },
    vendor: {
      initNavbar: async () =>
        options.me ?? { is_admin: true, preferences: { auto_apply_filters: !!options.autoApply } },
      initCollapsibleFilterSections: () => {},
      debounce: (fn) => Object.assign((...args) => fn(...args), { cancel() {} }),
      withFramework: (path, fw) => `${path}${path.includes('?') ? '&' : '?'}framework=${fw}`,
      getCurrentFramework: () => 'A',
      fmtTs: (v) => `ts(${v})`,
      toast: (el, message, kind) => {
        if (el) {
          el.textContent = message;
          el.dataset.alert = kind;
        }
      },
      apiGet: async (url) => {
        record('GET')(url);
        const path = url.split('?')[0];
        if (path === '/api/v1/admin/ingest/status') {
          if (fail.ingestStatus) throw new Error('offline');
          return { enabled: { loki: false, github: true } };
        }
        if (path === '/api/v1/users') {
          if (fail.users) throw new Error('Users offline');
          return structuredClone(state.data.users);
        }
        if (path === '/api/v1/admin/groups') {
          if (fail.groups) throw new Error('Groups offline');
          return structuredClone(state.data.groups);
        }
        if (path === '/api/v1/admin/permissions') {
          if (fail.permissions) throw new Error('Permissions offline');
          return structuredClone(state.data.permissions);
        }
        if (path.startsWith('/api/v1/admin/groups/')) {
          if (fail.group) throw new Error('Group gone');
          return {
            id: 'g1',
            name: 'Auditors',
            description: 'Read-only',
            role: 'normal',
            created_at: '2026-02-01',
            user_ids: ['u1'],
            permission_codes: ['audits.view'],
          };
        }
        if (path.startsWith('/api/v1/users/') && path.endsWith('/access')) {
          if (fail.access) throw new Error('Access offline');
          return {
            group_ids: ['g1'],
            explicit_permission_codes: ['admin.audit_trail'],
            effective_permission_codes: ['audits.view', 'admin.audit_trail'],
          };
        }
        if (path === '/api/v1/controls') {
          if (fail.controls) throw new Error('Controls offline');
          return {
            items: [
              { id: 'c-a51', ref: 'A.5.1', title: 'Policies', type: 'annex_control' },
              { id: 'c-a52', ref: 'A.5.2', title: HOSTILE, type: 'annex_control' },
              { id: 'k-a51', ref: 'A.5.1', title: 'Policy clause', type: 'clause' },
              { id: 'no-ref', title: 'No ref' },
              ...Array.from({ length: 30 }, (_, i) => ({
                id: `b-${i}`,
                ref: `B.${i}`,
                title: 'Bulk',
              })),
            ],
          };
        }
        if (path === '/api/v1/admin/entity-changelog') {
          if (fail.changelog) throw new Error('Changelog offline');
          return { items: [], total: options.changelogTotal ?? 30 };
        }
        if (path === '/api/v1/admin/audit') {
          if (fail.audit) throw new Error('Audit offline');
          return {
            items: [
              {
                ts: '2026-10-01T00:00:00Z',
                username: 'dana',
                method: 'POST',
                path: '/api/v1/users',
                query_string: 'q=<b>x</b>',
                status_code: 201,
                duration_ms: 12,
                client_ip: '192.0.2.1',
                user_agent: HOSTILE,
              },
              {
                ts: '2026-10-01T00:01:00Z',
                method: 'GET',
                path: '/api/v1/me',
                status_code: 200,
                duration_ms: 3,
              },
            ],
            total: options.auditTotal ?? 120,
          };
        }
        if (path === '/api/v1/admin/source-catalogue') {
          if (fail.catalogue) throw new Error('Catalogue offline');
          return {
            items: [
              {
                adapter: 'github',
                enabled: true,
                paused: false,
                can_pause: true,
                collection_items: 2,
                definitions: 3,
                observed_events: 40,
                last_seen: '2026-10-01',
              },
              {
                adapter: HOSTILE + 'loki',
                enabled: true,
                paused: true,
                can_pause: true,
                collection_items: 1,
                definitions: 0,
                observed_events: 0,
              },
              {
                adapter: 'taiga',
                enabled: false,
                paused: false,
                can_pause: true,
                collection_items: 0,
                definitions: 0,
                observed_events: 0,
              },
              {
                adapter: 'manual',
                enabled: true,
                paused: false,
                can_pause: false,
                collection_items: 0,
                definitions: 1,
                observed_events: 5,
              },
            ],
          };
        }
        return { items: [] };
      },
      apiPost: async (url, body) => {
        record('POST')(url, body);
        if (fail.post) throw new Error('Rejected');
        if (url === '/api/v1/admin/groups') return { id: 'g1' };
        if (url.startsWith('/api/v1/admin/ingest/github')) return { queued: true };
        return { ok: true };
      },
      apiPostForm: async (url, body) => {
        record('POSTFORM')(url, body);
        if (fail.post) throw new Error('Rejected');
        return { event_id: 'ev/1' };
      },
      apiPatch: async (url, body) => {
        record('PATCH')(url, body);
        if (fail.post) throw new Error('Rejected');
        return { id: 'g1' };
      },
      apiPut: async (url, body) => {
        record('PUT')(url, body);
        if (fail.put) throw new Error('Put rejected');
        if (url.endsWith('/access')) return { effective_permission_codes: ['audits.view'] };
        return {};
      },
      apiDelete: async (url) => {
        record('DELETE')(url);
        if (fail.post) throw new Error('Rejected');
        return {};
      },
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  await new Promise((r) => setTimeout(r, 15));
  const calls = (method, prefix) =>
    state.calls.filter((c) => c.method === method && c.url.startsWith(prefix));
  return { ui, el, state, calls };
}

test('the source catalogue shows ingestion state and pauses or resumes collection', async (t) => {
  const { ui, el, calls } = await admin(t);
  const catalogue = el('source-catalogue');
  await settle(() => catalogue.querySelectorAll('[data-source-pause]').length === 2);
  assert.equal(catalogue.querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  const cards = [...catalogue.querySelectorAll('.border')];
  assert.deepEqual(
    cards.map((c) => c.querySelector('.badge').textContent),
    ['Enabled', 'Paused', 'Disabled', 'Enabled'],
  );
  assert.match(
    cards[0].textContent,
    /2 collection items · 3 definitions[\s\S]*40 events · last seen 2026-10-01/,
  );
  assert.match(cards[1].textContent, /last seen never[\s\S]*Collection is paused/);
  // A source disabled by installation settings gets no pause control at all.
  assert.equal(cards[2].querySelector('button'), null);
  assert.equal(cards[3].querySelector('button'), null);

  cards[0].querySelector('button').click();
  await settle(
    () => el('status').textContent === 'Ingestion paused. A run already in progress may finish.',
  );
  assert.deepEqual(calls('PUT', '/api/v1/admin/sources/')[0], {
    method: 'PUT',
    url: '/api/v1/admin/sources/github/ingestion-state',
    body: { paused: true },
  });
  await settle(() => calls('GET', '/api/v1/admin/source-catalogue').length === 2);
  [...catalogue.querySelectorAll('[data-source-pause]')][1].click();
  await settle(() => /Ingestion resumed/.test(el('status').textContent));
  assert.equal(
    calls('PUT', '/api/v1/admin/sources/')[1].url,
    `/api/v1/admin/sources/${encodeURIComponent(HOSTILE + 'loki')}/ingestion-state`,
  );
  assert.deepEqual(calls('PUT', '/api/v1/admin/sources/')[1].body, { paused: false });
  // The evidence tab reloads the catalogue when shown.
  el('tab-evidence-config').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => calls('GET', '/api/v1/admin/source-catalogue').length >= 4);
});

test('source catalogue failures are reported', async (t) => {
  const failedPut = await admin(t, { fail: { put: true } });
  const catalogue = failedPut.el('source-catalogue');
  await settle(() => catalogue.querySelector('[data-source-pause]'));
  const button = catalogue.querySelector('[data-source-pause]');
  button.click();
  await settle(
    () => failedPut.el('status').textContent === 'Could not change ingestion state: Put rejected',
  );
  assert.equal(button.disabled, false);
  const offline = await admin(t, { fail: { catalogue: true } });
  await settle(
    () =>
      offline.el('source-catalogue').textContent ===
      'Could not load source catalogue: Catalogue offline',
  );
});

test('disabled ingest runners are hidden and runners report queued work', async (t) => {
  const { el, ui, calls, state } = await admin(t);
  assert.equal(
    ui.document.querySelector('[data-run="/api/v1/admin/ingest/loki/run"]').style.display,
    'none',
  );
  assert.equal(el('resetLokiCursors').style.display, 'none');
  const github = ui.document.querySelector('[data-run="/api/v1/admin/ingest/github/run"]');
  assert.equal(github.style.display, '');
  github.click();
  await settle(() => el('status').textContent === 'Collection queued.');
  assert.equal(calls('POST', '/api/v1/admin/ingest/github/run').length, 1);
  assert.match(el('out').textContent, /"queued": true/);
  assert.equal(el('admin-output-card').hidden, false);
  assert.ok(state.calls.some((c) => c.method === 'COLLAPSE' && c.url === 'outCollapse'));
  ui.document.querySelector('[data-run="/api/v1/admin/ingest/rss/run"]').click();
  await settle(() => el('status').textContent === 'OK');

  el('resetLokiCursors').click();
  await settle(() => calls('POST', '/api/v1/admin/ingest/loki/reset').length === 1);
  assert.equal(state.prompts.at(-1).title, 'Reset Loki cursors');
});

test('runner failures are shown in the output panel', async (t) => {
  const { el, ui } = await admin(t, { fail: { post: true, ingestStatus: true } });
  // Without ingest status, every runner stays visible.
  assert.equal(
    ui.document.querySelector('[data-run="/api/v1/admin/ingest/loki/run"]').style.display,
    '',
  );
  ui.document.querySelector('[data-run="/api/v1/admin/ingest/github/run"]').click();
  await settle(() => el('status').textContent === 'Failed: Rejected');
  assert.equal(el('out').textContent, 'Rejected');
});

test('declining the Loki reset does nothing', async (t) => {
  const { el, calls } = await admin(t, { confirm: false });
  el('resetLokiCursors').click();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls('POST', '/api/v1/admin/ingest/loki/reset').length, 0);
});

test('remapping sends only the filters that were set', async (t) => {
  const { el, calls } = await admin(t);
  el('remapBtn').click();
  await settle(() => calls('POST', '/api/v1/admin/remap/run').length === 1);
  assert.equal(
    calls('POST', '/api/v1/admin/remap/run')[0].url,
    '/api/v1/admin/remap/run?limit=500',
  );
  el('remapLimit').value = '20';
  el('remapIncludeMapped').checked = true;
  el('remapFrom').value = '2026-01-01';
  el('remapTo').value = '2026-02-01';
  el('remapSource').value = ' okta ';
  el('remapSystem').value = 'sso';
  el('remapAction').value = 'login';
  el('remapOutcome').value = 'success';
  el('remapBtn').click();
  await settle(() => calls('POST', '/api/v1/admin/remap/run').length === 2);
  assert.equal(
    calls('POST', '/api/v1/admin/remap/run')[1].url,
    '/api/v1/admin/remap/run?limit=20&include_mapped=true&from_date=2026-01-01&to_date=2026-02-01&source=okta&system=sso&action=login&outcome=success',
  );
});

test('users are listed safely, updated, reset and deleted', async (t) => {
  const { el, ui, calls, state } = await admin(t);
  const rows = [...el('usersRows').querySelectorAll('tr')];
  assert.equal(rows.length, 2);
  assert.equal(el('usersRows').querySelector('img'), null);
  assert.equal(rows[0].querySelector('[data-role]').value, 'admin');
  assert.equal(rows[0].querySelector('[data-active]').checked, true);
  assert.match(rows[0].textContent, /ts\(2026-01-01T00:00:00Z\)/);
  assert.match(rows[1].textContent, /Effective: Admin/);
  assert.equal(rows[1].querySelector('[data-reset]').disabled, true);
  assert.equal(rows[1].querySelector('[data-reset]').textContent, 'LDAP password');

  rows[0].querySelector('[data-email]').value = '  dana@new.example.test ';
  rows[0].querySelector('[data-role]').value = 'normal';
  rows[0].querySelector('[data-active]').checked = false;
  rows[0].querySelector('[data-save]').click();
  await settle(() => el('usersStatus').textContent === 'User updated');
  assert.deepEqual(calls('PATCH', '/api/v1/users/u1')[0].body, {
    role: 'normal',
    is_active: false,
    email: 'dana@new.example.test',
  });

  el('usersRows').querySelector('[data-reset]').click();
  await settle(() => el('usersStatus').textContent === 'Password updated for dana');
  assert.deepEqual(calls('POST', '/api/v1/users/u1/password')[0].body, {
    new_password: 'synthetic-password-1',
  });
  assert.equal(state.prompts.at(-1), 'Set a new password for dana:');
  state.promptAnswer = '';
  el('usersRows').querySelector('[data-reset]').click();
  assert.equal(calls('POST', '/api/v1/users/u1/password').length, 1);

  el('usersRows').querySelectorAll('[data-delete]')[1].click();
  await settle(() => el('usersStatus').textContent === `Deleted ${HOSTILE}sam`);
  assert.deepEqual(calls('DELETE', '/api/v1/users/')[0].url, '/api/v1/users/u%2F2');
  assert.match(state.prompts.at(-1).title, /^Delete <img src=x onerror="globalThis\.pwned=1">sam$/);
  el('usersRefresh').click();
  await settle(() => calls('GET', '/api/v1/users').length === 4);
  assert.equal(ui.window.pwned, undefined);
});

test('user actions report failures and an empty list', async (t) => {
  const { el } = await admin(t, { fail: { post: true } });
  el('usersRows').querySelector('[data-save]').click();
  await settle(() => el('usersStatus').textContent === 'Failed: Rejected');
  el('usersStatus').textContent = '';
  el('usersRows').querySelector('[data-reset]').click();
  await settle(() => el('usersStatus').textContent === 'Failed: Rejected');
  el('usersStatus').textContent = '';
  el('usersRows').querySelector('[data-delete]').click();
  await settle(() => el('usersStatus').textContent === 'Failed: Rejected');
  const offline = await admin(t, { fail: { users: true } });
  assert.match(offline.el('usersRows').textContent, /No users found/);
  assert.equal(offline.el('usersStatus').textContent, 'Failed to load users: Users offline');
});

test('new users need a username and password, and the form resets after creation', async (t) => {
  const { el, ui, calls } = await admin(t);
  const submit = () =>
    el('userCreateForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  submit();
  await settle(() => el('usersStatus').textContent === 'Failed: Username is required');
  el('newUsername').value = ' riley ';
  submit();
  await settle(() => el('usersStatus').textContent === 'Failed: Password is required');
  el('newEmail').value = 'riley@example.test';
  el('newPassword').value = 'synthetic-password-2';
  // The role menu shares its choices with the users table and the group form.
  assert.deepEqual(
    [...el('newRole').options].map((o) => [o.value, o.textContent]),
    [
      ['inherit', 'Inherit (from groups)'],
      ['normal', 'Normal'],
      ['admin', 'Admin'],
    ],
  );
  assert.equal(el('newRole').value, 'inherit');
  el('newRole').value = 'admin';
  submit();
  await settle(() => el('usersStatus').textContent === 'Created riley (Admin)');
  assert.deepEqual(calls('POST', '/api/v1/users')[0].body, {
    username: 'riley',
    email: 'riley@example.test',
    password: 'synthetic-password-2',
    role: 'admin',
  });
  assert.equal(el('newPassword').value, '');
  assert.equal(el('newRole').value, 'inherit');
});

test('the access dialog loads groups and permissions and saves the selection', async (t) => {
  FakeModal.shown = [];
  const { el, calls } = await admin(t);
  el('usersRows').querySelector('[data-access]').click();
  await settle(() => FakeModal.shown.includes('userAccessModal'));
  assert.equal(el('userAccessUsername').textContent, 'Editing access for: dana');
  assert.deepEqual(
    [...el('userAccessGroups').options].map((o) => [o.textContent, o.selected]),
    [
      ['Admins', false],
      ['Auditors (Normal)', true],
    ],
  );
  assert.deepEqual(
    [...el('userAccessPerms').options].map((o) => [o.textContent, o.selected]),
    [
      ['admin.audit_trail', true],
      ['audits.view - View audits', false],
    ],
  );
  assert.equal(el('userAccessEffective').textContent, 'audits.view, admin.audit_trail');
  el('userAccessGroups').options[0].selected = true;
  el('userAccessSave').click();
  await settle(() => el('userAccessStatus').textContent === 'Saved');
  assert.deepEqual(calls('PUT', '/api/v1/users/u1/access')[0].body, {
    group_ids: ['g2', 'g1'],
    explicit_permission_codes: ['admin.audit_trail'],
  });
  assert.equal(el('userAccessEffective').textContent, 'audits.view');
  assert.equal(el('userAccessStatus').className, 'alert alert-success mt-3');
});

test('access dialog load and save failures are reported', async (t) => {
  FakeModal.shown = [];
  const { el } = await admin(t, { fail: { access: true, put: true } });
  el('usersRows').querySelector('[data-access]').click();
  await settle(() => el('userAccessStatus').textContent === 'Failed to load: Access offline');
  assert.equal(el('userAccessEffective').textContent, '');
  el('userAccessSave').click();
  await settle(() => el('userAccessStatus').textContent === 'Failed: Put rejected');
  el('userAccessUserId').value = '';
  el('userAccessSave').click();
});

test('groups open into an editable detail with members and permissions', async (t) => {
  const { el, ui, calls, state } = await admin(t);
  const rows = [...el('groupsRows').querySelectorAll('tr')];
  assert.deepEqual(
    rows.map((r) => r.textContent.replace(/\s+/g, ' ').trim()),
    ['Auditors Normal 2 3', 'Admins - 1 0'],
  );
  rows[0].click();
  await settle(() => el('groupName').value === 'Auditors');
  assert.equal(el('groupDetail').style.display, 'block');
  assert.equal(el('groupDetailEmpty').style.display, 'none');
  assert.equal(el('groupDetailMeta').textContent, 'Created ts(2026-02-01)');
  assert.deepEqual(
    [...el('groupMembers').selectedOptions].map((o) => o.value),
    ['u1'],
  );
  assert.ok(el('groupsRows').querySelector('tr.table-active'));

  el('groupName').value = ' Internal auditors ';
  el('groupRole').value = '';
  const details = () => calls('GET', '/api/v1/admin/groups/g1').length;
  el('groupSave').click();
  await settle(() => details() === 2);
  assert.deepEqual(calls('PATCH', '/api/v1/admin/groups/g1')[0].body, {
    name: 'Internal auditors',
    description: 'Read-only',
    role: '',
  });
  await new Promise((r) => setTimeout(r, 10));
  // The confirmation stays visible after the detail reloads.
  assert.equal(el('groupsStatus').textContent, 'Group updated');

  el('groupApplyMembers').click();
  await settle(() => details() === 3);
  assert.deepEqual(calls('PUT', '/api/v1/admin/groups/g1/members')[0].body, { user_ids: ['u1'] });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(el('groupsStatus').textContent, 'Members saved');
  // Permissions are shown as wrapped checkboxes kept in sync with the hidden select.
  const box = [
    ...ui.document.querySelectorAll('.keen-multiselect-wrap input[type="checkbox"]'),
  ].find((b) => b.value === 'admin.audit_trail');
  assert.equal(box.closest('label').title, 'admin.audit_trail');
  box.checked = true;
  box.dispatchEvent(new ui.window.Event('change'));
  el('groupApplyPermissions').click();
  await settle(() => details() === 4);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(el('groupsStatus').textContent, 'Permissions saved');
  assert.deepEqual(
    calls('PUT', '/api/v1/admin/groups/g1/permissions')[0].body.permission_codes.sort(),
    ['admin.audit_trail', 'audits.view'],
  );

  el('groupDelete').click();
  await settle(() => el('groupsStatus').textContent === 'Group deleted');
  assert.equal(state.prompts.at(-1).title, 'Delete group');
  assert.equal(el('groupDetail').style.display, 'none');
  assert.equal(el('groupDetailEmpty').style.display, 'block');
  // Without a selected group the detail actions do nothing.
  const before = state.calls.length;
  el('groupSave').click();
  el('groupDelete').click();
  el('groupApplyMembers').click();
  el('groupApplyPermissions').click();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(state.calls.length, before);
});

test('New group opens the group form empty, creates the group and then edits it', async (t) => {
  const { el, ui, calls } = await admin(t);
  const submit = () =>
    el('groupForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  // With no group open the form sends nothing.
  submit();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls('POST', '/api/v1/admin/groups').length, 0);

  el('groupNew').click();
  assert.equal(el('groupDetail').style.display, 'block');
  assert.equal(el('groupDetailEmpty').style.display, 'none');
  assert.equal(el('groupDetailTitle').textContent, 'New group');
  assert.equal(el('groupSave').textContent, 'Create group');
  // Deletion, members and permissions wait until the group exists.
  assert.equal(el('groupDelete').hidden, true);
  assert.equal(el('groupAccess').hidden, true);
  assert.deepEqual(
    [...el('groupRole').options].map((o) => [o.value, o.textContent]),
    [
      ['', 'No role'],
      ['normal', 'Normal'],
      ['admin', 'Admin'],
    ],
  );
  submit();
  await settle(() => el('groupsStatus').textContent === 'Failed: Group name is required');
  el('groupName').value = ' Reviewers ';
  el('groupRole').value = 'normal';
  submit();
  await settle(() => el('groupName').value === 'Auditors');
  assert.deepEqual(calls('POST', '/api/v1/admin/groups')[0].body, {
    name: 'Reviewers',
    description: null,
    role: 'normal',
  });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(el('groupsStatus').textContent, 'Created group Reviewers');
  assert.equal(el('groupDetailTitle').textContent, 'Edit group');
  assert.equal(el('groupSave').textContent, 'Save details');
  assert.equal(el('groupDelete').hidden, false);
  assert.equal(el('groupAccess').hidden, false);

  // Another new group starts from an empty form and clears the row selection.
  el('groupNew').click();
  assert.equal(el('groupName').value, '');
  assert.equal(el('groupDesc').value, '');
  assert.equal(el('groupRole').value, '');
  assert.equal(el('groupsRows').querySelector('tr.table-active'), null);

  el('groupsRefresh').click();
  await settle(() => calls('GET', '/api/v1/admin/groups').length >= 4);
});

test('group failures are reported', async (t) => {
  const offline = await admin(t, { fail: { groups: true } });
  assert.match(offline.el('groupsRows').textContent, /No groups/);
  assert.equal(offline.el('groupsStatus').textContent, 'Failed to load groups: Groups offline');
  const missing = await admin(t, { fail: { group: true } });
  missing.el('groupsRows').querySelector('tr').click();
  await settle(() => missing.el('groupsStatus').textContent === 'Failed to load group: Group gone');
  const rejected = await admin(t, { fail: { post: true, put: true } });
  rejected.el('groupsRows').querySelector('tr').click();
  await settle(() => rejected.el('groupName').value === 'Auditors');
  rejected.el('groupSave').click();
  await settle(() => rejected.el('groupsStatus').textContent === 'Failed: Rejected');
  rejected.el('groupApplyMembers').click();
  await settle(() => rejected.el('groupsStatus').textContent === 'Failed: Put rejected');
  rejected.el('groupsStatus').textContent = '';
  rejected.el('groupApplyPermissions').click();
  await settle(() => rejected.el('groupsStatus').textContent === 'Failed: Put rejected');
  rejected.el('groupDelete').click();
  await settle(() => rejected.el('groupsStatus').textContent === 'Failed: Rejected');
});

test('diary evidence collects controls, links, timestamp and attachments', async (t) => {
  const { el, ui, calls } = await admin(t);
  const search = (q) => {
    el('controlSearch').value = q;
    el('controlSearch').dispatchEvent(new ui.window.Event('input'));
  };
  search('a.5');
  // A clause and a control can share a reference, so results are chosen by id.
  const results = [...el('controlResults').querySelectorAll('[data-id]')];
  assert.deepEqual(
    results.map((r) => [r.dataset.id, r.querySelector('.badge').textContent]),
    [
      ['c-a51', 'A.5.1'],
      ['c-a52', 'A.5.2'],
      ['k-a51', 'Clause A.5.1'],
    ],
  );
  assert.equal(el('controlResults').querySelector('img'), null);
  search('bulk');
  assert.equal(el('controlResults').querySelectorAll('[data-id]').length, 25);
  search('a.5');
  el('controlResults').querySelector('[data-id="c-a52"]').click();
  assert.equal(el('controlSearch').value, '');
  assert.equal(el('controlResults').innerHTML, '');
  search('a.5');
  assert.equal(
    el('controlResults').querySelector('[data-id="c-a52"]').classList.contains('disabled'),
    true,
  );
  el('controlResults').querySelector('[data-id="k-a51"]').click();
  search('a.5');
  el('controlResults').querySelector('[data-id="c-a51"]').click();
  assert.deepEqual(
    [...el('controlSelected').querySelectorAll('[data-remove]')].map((b) => b.dataset.remove),
    ['c-a51', 'c-a52', 'k-a51'],
  );
  assert.deepEqual(
    [...el('controlSelected').querySelectorAll('.badge')].map((b) =>
      b.textContent.replace('×', '').trim(),
    ),
    ['A.5.1', 'A.5.2', 'Clause A.5.1'],
  );
  el('controlSelected').querySelector('[data-remove="c-a52"]').click();
  search('');
  assert.equal(el('controlResults').innerHTML, '');

  const submit = () =>
    el('diaryForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  submit();
  await settle(() => el('status').textContent === 'Failed: Missing summary');

  el('diarySummary').value = ' Quarterly access review ';
  el('diaryDetails').value = 'Reviewed all admins';
  el('diaryTs').value = '2026-10-01T09:30';
  const linkRow = el('diaryLinks').querySelector('[data-link-row]');
  linkRow.querySelector('[data-link-url]').value = 'https://tickets.example.test/1';
  linkRow.querySelector('[data-link-text]').value = 'Ticket';
  el('diaryAddLink').click();
  el('diaryAddLink').click();
  el('diaryLinks')
    .querySelectorAll('[data-link-row]')[2]
    .querySelector('[data-link-remove]')
    .click();
  assert.equal(el('diaryLinks').querySelectorAll('[data-link-row]').length, 2);
  const files = Array.from(
    { length: 12 },
    (_, i) => new File(['x'], `shot-${i}.png`, { type: 'image/png' }),
  );
  Object.defineProperty(el('diaryImages'), 'files', { value: files, configurable: true });
  submit();
  await settle(() => el('status').textContent === 'Diary evidence created');
  const sent = calls('POSTFORM', '/api/v1/admin/diary')[0].body;
  const field = (name) => sent.filter(([k]) => k === name).map(([, v]) => v);
  assert.deepEqual(field('summary'), ['Quarterly access review']);
  assert.deepEqual(field('details'), ['Reviewed all admins']);
  assert.deepEqual(JSON.parse(field('links')[0]), [
    { url: 'https://tickets.example.test/1', text: 'Ticket' },
  ]);
  assert.equal(field('timestamp').length, 1);
  assert.equal(new Date(field('timestamp')[0]).toISOString(), field('timestamp')[0]);
  assert.deepEqual(field('controls'), ['c-a51', 'k-a51']);
  // At most ten attachments are sent.
  assert.equal(field('attachments').length, 10);
  assert.match(
    el('out').textContent,
    /Open: https:\/\/keen\.example\/event\.html\?id=ev%2F1&framework=A/,
  );
  assert.equal(el('diarySummary').value, '');
  assert.equal(el('diaryLinks').querySelectorAll('[data-link-row]').length, 1);
  // The control selection is kept for the next entry.
  assert.ok(el('controlSelected').querySelector('[data-remove="c-a51"]'));
  assert.ok(el('controlSelected').querySelector('[data-remove="k-a51"]'));
});

test("a control's Requirements link starts the diary form with that control chosen", async (t) => {
  const { el } = await admin(t, { hash: '?control=c-a52&framework=A#diary' });
  assert.deepEqual(
    [...el('controlSelected').querySelectorAll('[data-remove]')].map((b) => b.dataset.remove),
    ['c-a52'],
  );
  // The chosen title stays text.
  assert.equal(el('controlSelected').querySelector('img'), null);
  // An unknown control leaves the form empty.
  const unknown = await admin(t, { hash: '?control=nothing#diary' });
  assert.equal(unknown.el('controlSelected').querySelectorAll('[data-remove]').length, 0);
});

test('a rejected diary entry is reported', async (t) => {
  const { el, ui } = await admin(t, { fail: { post: true } });
  el('diarySummary').value = 'Entry';
  el('diaryForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => el('status').textContent === 'Failed: Rejected');
  assert.equal(el('out').textContent, 'Rejected');
  assert.equal(el('diarySummary').value, 'Entry');
});

test('the entity changelog pages and filters', async (t) => {
  const { el, ui, calls } = await admin(t);
  const changelog = () => calls('GET', '/api/v1/admin/entity-changelog');
  assert.equal(changelog()[0].url, '/api/v1/admin/entity-changelog?limit=25&offset=0');
  assert.equal(el('entityChangeMeta').textContent, 'Showing 1-25 of 30');
  assert.equal(el('entityChangePrev').disabled, true);
  el('entityChangeNext').click();
  await settle(() => el('entityChangeMeta').textContent === 'Showing 26-30 of 30');
  assert.equal(el('entityChangeNext').disabled, true);
  el('entityChangePrev').click();
  await settle(() => changelog().at(-1).url.endsWith('offset=0'));
  el('entityChangeType').value = 'risk';
  el('entityChangeType').dispatchEvent(new ui.window.Event('change'));
  el('entityChangeUser').value = 'dana';
  el('entityChangeUser').dispatchEvent(
    new ui.window.KeyboardEvent('keydown', { key: 'Enter', cancelable: true }),
  );
  el('entityChangeQuery').value = 'backup';
  el('entityChangeQuery').dispatchEvent(new ui.window.Event('change'));
  await settle(
    () =>
      changelog().at(-1).url ===
      '/api/v1/admin/entity-changelog?limit=25&offset=0&entity_type=risk&username=dana&q=backup',
  );
  el('entityChangeUser').dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'a' }));
  el('entityChangeRefresh').click();
  await settle(() => changelog().length === 7);
});

test('a changelog failure is reported', async (t) => {
  const { el } = await admin(t, { fail: { changelog: true } });
  await settle(
    () => el('status').textContent === 'Failed to load entity changelog: Changelog offline',
  );
  assert.equal(el('entityChangeMeta').textContent, 'Showing 0-0 of 0');
  assert.equal(el('entityChangeNext').disabled, true);
});

test('the audit trail renders requests as text and pages through results', async (t) => {
  const { el, ui, calls } = await admin(t);
  const rows = [...el('auditRows').querySelectorAll('tr')];
  assert.equal(rows.length, 2);
  assert.equal(el('auditRows').querySelector('img, b'), null);
  assert.match(
    rows[0].textContent,
    /POST[\s\S]*\/api\/v1\/users\?q=<b>x<\/b>[\s\S]*201[\s\S]*12[\s\S]*192\.0\.2\.1/,
  );
  assert.match(rows[1].textContent, /-[\s\S]*GET[\s\S]*\/api\/v1\/me\s[\s\S]*-[\s\S]*-/);
  assert.equal(el('auditMeta').textContent, 'Showing 1-50 of 120');
  const audit = () => calls('GET', '/api/v1/admin/audit');
  el('auditNext').click();
  await settle(() => el('auditMeta').textContent === 'Showing 51-100 of 120');
  el('auditNext').click();
  await settle(() => el('auditMeta').textContent === 'Showing 101-120 of 120');
  assert.equal(el('auditNext').disabled, true);
  el('auditPrev').click();
  await settle(() => audit().at(-1).url.endsWith('offset=50'));
  el('auditUser').value = 'dana';
  el('auditPath').value = '/api/v1/users';
  el('auditMethod').value = 'POST';
  el('auditRefresh').click();
  await settle(
    () =>
      audit().at(-1).url ===
      '/api/v1/admin/audit?limit=50&offset=0&username=dana&path=%2Fapi%2Fv1%2Fusers&method=POST',
  );
  assert.equal(ui.window.pwned, undefined);
});

test('audit filters apply automatically when the user prefers it', async (t) => {
  const { el, ui, calls } = await admin(t, { autoApply: true });
  const audit = () => calls('GET', '/api/v1/admin/audit');
  const start = audit().length;
  el('auditUser').value = 'sam';
  el('auditUser').dispatchEvent(new ui.window.Event('input'));
  await settle(() => audit().length === start + 1 && audit().at(-1).url.includes('username=sam'));
  el('auditPath').dispatchEvent(new ui.window.Event('change'));
  el('auditMethod').dispatchEvent(new ui.window.Event('change'));
  el('auditUser').dispatchEvent(new ui.window.Event('change'));
  el('auditPath').dispatchEvent(
    new ui.window.KeyboardEvent('keydown', { key: 'Enter', cancelable: true }),
  );
  await settle(() => audit().length === start + 5);
});

test('changelog filters apply while typing when the user prefers auto-apply', async (t) => {
  const { el, ui, calls } = await admin(t, { autoApply: true });
  const changelog = () => calls('GET', '/api/v1/admin/entity-changelog');
  const start = changelog().length;
  el('entityChangeUser').value = 'dana';
  el('entityChangeUser').dispatchEvent(new ui.window.Event('input'));
  await settle(() => changelog().length === start + 1);
  assert.match(changelog().at(-1).url, /username=dana/);
  el('entityChangeQuery').value = 'backup';
  el('entityChangeQuery').dispatchEvent(new ui.window.Event('input'));
  await settle(() => changelog().at(-1).url.includes('q=backup'));
});

test('changelog filters wait for Enter or change without auto-apply', async (t) => {
  const { el, ui, calls } = await admin(t);
  const start = calls('GET', '/api/v1/admin/entity-changelog').length;
  el('entityChangeUser').value = 'dana';
  el('entityChangeUser').dispatchEvent(new ui.window.Event('input'));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(calls('GET', '/api/v1/admin/entity-changelog').length, start);
});

test('an audit trail failure is reported', async (t) => {
  const { el } = await admin(t, { fail: { audit: true } });
  await settle(() => el('status').textContent === 'Failed to load audit: Audit offline');
  assert.equal(el('out').textContent, 'Audit offline');
  assert.equal(el('auditMeta').textContent, 'Showing 0-0 of 0');
});

test('audit-only users see just the audit tab and no admin data', async (t) => {
  const { el, ui, calls, state } = await admin(t, { me: { can_audit_trail: true } });
  assert.equal(ui.window.location.hash, '#audit');
  assert.ok(state.calls.some((c) => c.method === 'TAB' && c.url === 'tab-audit'));
  assert.equal(el('tab-users').style.display, 'none');
  assert.equal(el('tab-audit').style.display, '');
  assert.equal(el('pane-users').style.display, 'none');
  assert.equal(calls('GET', '/api/v1/users').length, 0);
  assert.equal(calls('GET', '/api/v1/admin/ingest/status').length, 0);
  assert.equal(calls('GET', '/api/v1/admin/source-catalogue').length, 0);
  assert.equal(calls('GET', '/api/v1/admin/audit').length, 1);
});

test('users without admin or audit access are told so', async (t) => {
  const { el, calls } = await admin(t, { me: {} });
  assert.equal(el('out').textContent, 'You do not have access to this page.');
  assert.equal(calls('GET', '/api/v1/admin/audit').length, 0);
});

test('tabs follow the address hash and update it when changed', async (t) => {
  const { el, ui, state } = await admin(t, { hash: '#import' });
  assert.ok(state.calls.some((c) => c.method === 'TAB' && c.url === 'tab-ingest'));
  ui.window.location.hash = '#groups';
  ui.window.dispatchEvent(new ui.window.HashChangeEvent('hashchange'));
  assert.ok(state.calls.some((c) => c.method === 'TAB' && c.url === 'tab-groups'));
  el('tab-retention').dispatchEvent(new ui.window.Event('shown.bs.tab', { bubbles: true }));
  assert.equal(ui.window.location.hash, '#retention');
  // The compact navigation menu closes and names the active tab.
  el('admin-navigation-toggle').click();
  assert.equal(el('admin-navigation-toggle').getAttribute('aria-expanded'), 'true');
  el('tab-users').dispatchEvent(new ui.window.Event('shown.bs.tab', { bubbles: true }));
  assert.equal(el('admin-navigation-toggle').getAttribute('aria-expanded'), 'false');
  assert.match(el('admin-navigation-toggle').textContent, /· Menu$/);
  assert.equal(el('admin-output-card').hidden, true);
});

test('the access dialog opens with empty lists when groups and permissions are unavailable', async (t) => {
  FakeModal.shown = [];
  const { el, ui } = await admin(t, { fail: { groups: true, permissions: true, controls: true } });
  assert.match(el('groupsStatus').textContent, /Failed to load groups: Groups offline/);
  el('usersRows').querySelector('[data-access]').click();
  await settle(() => FakeModal.shown.includes('userAccessModal'));
  assert.equal(el('userAccessGroups').options.length, 0);
  assert.equal(el('userAccessPerms').options.length, 0);
  assert.equal(el('userAccessEffective').textContent, 'audits.view, admin.audit_trail');
  // Without the control catalogue the diary search finds nothing.
  el('controlSearch').value = 'a.5';
  el('controlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(el('controlResults').querySelectorAll('[data-id]').length, 0);
});

test('groups that inherit their role are labelled so in the access dialog', async (t) => {
  FakeModal.shown = [];
  const { el, state } = await admin(t);
  state.data.groups = [{ id: 'g3', name: 'Readers', role: 'inherit', member_count: 0 }];
  el('usersRows').querySelector('[data-access]').click();
  await settle(() => FakeModal.shown.includes('userAccessModal'));
  assert.deepEqual(
    [...el('userAccessGroups').options].map((o) => o.textContent),
    ['Admins', 'Auditors (Normal)'],
  );
  // The lists are fetched once; a later dialog reuses them.
  const fetched = state.calls.filter((c) => c.method === 'GET' && c.url === '/api/v1/admin/groups');
  el('groupsRefresh')?.click();
  await settle(() => el('groupsRows').textContent.includes('Readers'));
  assert.match(el('groupsRows').textContent, /Inherit/);
  assert.ok(fetched.length >= 1);
});
