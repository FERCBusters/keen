import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';

const lists = {
  '/api/v1/me/questions': [
    {
      thread_id: 't1',
      target_label: HOSTILE + 'Policy review',
      target_type: 'pestle_item',
      target_ref: 'PESTLE-1',
      target_url: '/pestle_item.html?id=p1#questions',
      status: 'answered',
      updated_at: '2026-10-01T10:00:00Z',
    },
    {
      thread_id: 't2',
      event_summary: 'Login event',
      event_source: 'okta',
      event_timestamp: '2026-09-30T09:00:00Z',
      event_id: 'e/1',
      status: 'reviewing',
      unread: true,
    },
    { thread_id: 't3', target_url: 'javascript:alert(1)', status: 'open' },
  ],
  '/api/v1/me/audits': [
    {
      id: 'a1',
      title: HOSTILE + 'Annual audit',
      framework_slug: 'B',
      start_date: '2026-01-01',
      end_date: '2026-02-01',
      status: 'in_progress',
      my_attendee: { role: 'Lead auditor' },
      updated_at: '2026-01-15T00:00:00Z',
    },
    { id: 'a2', status: 'completed' },
  ],
  '/api/v1/me/risks': [
    {
      id: 'r1',
      asset: 'Payroll DB',
      threat_summary: 'Ransomware',
      category: { name: 'Information' },
      subcategory: { name: 'Databases' },
      risk_types: ['Availability'],
      risk_score: 16,
      residual_risk_score: 6,
      control_count: 1,
      updated_at: '2026-10-01T00:00:00Z',
    },
    {
      id: 'r2',
      asset_entity: { name: 'Laptops' },
      risk_score: 2,
      residual_risk_score: 0,
      control_count: 3,
    },
  ],
  '/api/v1/me/isms-meetings': [
    {
      id: 'm1',
      title: 'Management review',
      agenda_minutes_notes: HOSTILE,
      date: '2026-10-02',
      start_time: '09:00',
      end_time: '10:00',
      my_attendance_label: 'Attendee',
      attendees: [{ username: 'dana' }],
      apologies: [],
    },
    { id: 'm2', start_time: '14:00' },
  ],
  '/api/v1/me/isms-objectives': [
    {
      requirement: 'Reduce phishing',
      metric: 'Click rate',
      goal: 'Below 5%',
      owner_user: { username: 'dana' },
      resource_users: [{ username: 'sam' }],
      completion_target_date: '2026-12-31',
    },
    {},
  ],
  '/api/v1/me/isms-role': [
    {
      org_node: { name: 'Security lead', description: 'Owns the ISMS', node_type: 'role' },
      relationship_type: 'holder',
      path: [{ name: 'Board' }, 'CISO', { title: 'Security lead' }],
    },
    { node: { username: 'fallback' } },
  ],
  '/api/v1/me/isms-assets': [
    {
      asset: {
        asset: 'Laptop fleet',
        category: { name: 'Hardware' },
        subcategory_name: 'Laptops',
        license: 'Owned',
        owner_org_node: { name: 'IT' },
        register_held_by_org_node: [{ name: 'Org' }, { name: 'Finance' }],
      },
      is_owner: true,
      is_register_holder: true,
    },
    { name: 'Shared drive', description: 'Team files', relationship: 'Custodian' },
  ],
};

async function account(t, options = {}) {
  const state = {
    lists: structuredClone(options.lists ?? lists),
    removed: [],
    patched: [],
    posted: [],
    prompts: [],
    gets: [],
    bell: 0,
  };
  const fail = options.fail || {};
  const ui = await page(t, 'account', {
    query: options.query || '',
    me: options.me || {
      is_admin: true,
      preferences: options.preferences || {},
      available_themes: [
        { id: 'purple', name: 'Purple' },
        { id: 'dark', name: '<b>Dark</b>' },
      ],
      default_timezone: 'Australia/Melbourne',
      default_date_format: 'dmy',
    },
    globals: {
      confirm: (m) => (state.prompts.push(m), options.confirm ?? true),
      ...options.globals,
    },
    vendor: options.vendor,
    setup(window) {
      // The design system's navbar exposes this hook for the question bell.
      window.keenRefreshBell = () => (state.bell += 1);
      if (options.tabs) window.bootstrap = options.tabs;
    },
    get: async (u) => {
      state.gets.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/sources/meta') {
        if (fail.sources) throw new Error('No source meta');
        return {
          items: options.sources ?? [
            { source: 'keen-agent', label: 'Agents', default_color: '#123' },
            { source: 'okta', color: 'not-a-colour' },
            { source: '' },
          ],
        };
      }
      if (u.pathname === '/api/v1/me/saved-searches') {
        if (fail.saved) throw new Error('Unsupported');
        return structuredClone(
          options.saved ?? [{ id: 's1', name: 'Unmapped', url: '/events.html?unmapped=true' }],
        );
      }
      if (u.pathname === '/api/v1/frameworks-down') throw new Error('Catalogue offline');
      if (state.lists[u.pathname]) {
        if (fail[u.pathname]) throw new Error('Offline');
        return { items: structuredClone(state.lists[u.pathname]) };
      }
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, body) => {
      state.patched.push({ path: u.pathname, body: plain(body) });
      if (fail.patch) throw new Error('Rejected');
      return { ...plain(body), theme: body.theme };
    },
    post: async (u, body) => {
      state.posted.push({ path: u.pathname, body: plain(body) });
      if (fail.post) throw new Error('Rejected');
      return options.postResult ?? {};
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (fail.remove) throw new Error('Not allowed');
      const id = decodeURIComponent(u.pathname.split('/').pop());
      state.lists['/api/v1/me/questions'] = state.lists['/api/v1/me/questions'].filter(
        (q) => q.thread_id !== id,
      );
      return {};
    },
  });
  await settle(() => state.gets.some((g) => g.startsWith('/api/v1/me/saved-searches')));
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
}

function fakeTabs() {
  const shown = [];
  return {
    shown,
    Tab: {
      getOrCreateInstance: (el) => ({
        show() {
          shown.push(el.id);
          el.dispatchEvent(new el.ownerDocument.defaultView.Event('shown.bs.tab'));
        },
      }),
    },
  };
}
const showTab = (ui, id) => ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));

test('my questions show status badges, safe links and deletion for administrators', async (t) => {
  const tabs = fakeTabs();
  const { ui, state } = await account(t, { query: '#my-questions', tabs });
  assert.deepEqual(tabs.shown, ['my-questions-tab']);
  await settle(() => ui.el('myQuestionsTbody').querySelectorAll('tr').length === 3);
  const rows = [...ui.el('myQuestionsTbody').querySelectorAll('tr')];
  assert.equal(ui.el('myQuestionsTbody').querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  assert.equal(rows[0].querySelector('.fw-semibold').textContent, HOSTILE + 'Policy review');
  assert.match(rows[0].textContent, /pestle item · PESTLE-1/);
  assert.equal(
    rows[0].querySelector('a').getAttribute('href'),
    '/pestle_item.html?id=p1&framework=A#questions',
  );
  assert.deepEqual(
    rows.map((r) => r.querySelector('.badge').textContent),
    ['answered', 'new reply', 'unanswered'],
  );
  assert.equal(rows[1].className.trim(), 'table-warning');
  assert.equal(
    rows[1].querySelector('a').getAttribute('href'),
    '/event.html?id=e%2F1&thread=t2&framework=A#questions',
  );
  assert.match(rows[1].textContent, /okta · 2026-09-30T09:00:00Z/);
  // A script URL is never made clickable.
  assert.equal(rows[2].querySelector('a').getAttribute('href'), '#');
  assert.equal(state.bell, 1);
  // The hash is kept in sync with the active tab.
  assert.equal(ui.window.location.hash, '#my-questions');

  rows[0].querySelector('[data-delete-question="t1"]').click();
  await settle(() => ui.el('status').textContent === 'Question deleted');
  assert.deepEqual(state.removed, ['/api/v1/questions/t1']);
  assert.equal(state.prompts.at(-1), 'Delete this question thread and all replies?');
  await settle(() => ui.el('myQuestionsTbody').querySelectorAll('tr').length === 2);
});

test('question deletion respects confirmation, permissions and failures', async (t) => {
  const declined = await account(t, { confirm: false, query: '#my-questions', tabs: fakeTabs() });
  await settle(() => declined.ui.el('myQuestionsTbody').querySelector('[data-delete-question]'));
  declined.ui.el('myQuestionsTbody').querySelector('[data-delete-question]').click();
  assert.deepEqual(declined.state.removed, []);

  const failing = await account(t, {
    fail: { remove: true },
    query: '#my-questions',
    tabs: fakeTabs(),
  });
  await settle(() => failing.ui.el('myQuestionsTbody').querySelector('[data-delete-question]'));
  const button = failing.ui.el('myQuestionsTbody').querySelector('[data-delete-question]');
  button.click();
  await settle(() => failing.ui.el('status').textContent === 'Error: Not allowed');
  assert.equal(button.disabled, false);

  const reader = await account(t, {
    me: { preferences: {} },
    query: '#my-questions',
    tabs: fakeTabs(),
  });
  await settle(() => reader.ui.el('myQuestionsTbody').querySelectorAll('tr').length === 3);
  assert.equal(reader.ui.el('myQuestionsTbody').querySelector('[data-delete-question]'), null);
});

test('my audits and risks show status, roles and score bands', async (t) => {
  const { ui, state } = await account(t);
  showTab(ui, 'my-audits-tab');
  await settle(() => ui.el('myAuditsTbody').querySelectorAll('tr').length === 2);
  const audits = [...ui.el('myAuditsTbody').querySelectorAll('tr')];
  assert.equal(audits[0].querySelector('a').textContent, HOSTILE + 'Annual audit');
  assert.equal(audits[0].querySelector('a').getAttribute('href'), '/audit.html?id=a1&framework=B');
  assert.match(
    audits[0].textContent,
    /2026-01-01 → 2026-02-01[\s\S]*Lead auditor[\s\S]*In progress/,
  );
  // Missing dates show a dash.
  assert.deepEqual(
    [...audits[1].cells].slice(0, 5).map((c) => c.textContent.replace(/\s+/g, ' ').trim()),
    ['Audit Updated -', '', '-', 'Attendee', 'Completed'],
  );
  assert.equal(audits[1].querySelector('a').getAttribute('href'), '/audit.html?id=a2&framework=A');
  // A second visit to the tab reuses the loaded list; Refresh reloads it.
  showTab(ui, 'my-audits-tab');
  ui.el('myAuditsRefresh').click();
  await settle(() => state.gets.filter((g) => g === '/api/v1/me/audits').length === 2);

  showTab(ui, 'my-risks-tab');
  await settle(() => ui.el('myRisksTbody').querySelectorAll('tr').length === 2);
  assert.ok(state.gets.includes('/api/v1/me/risks?framework=A'));
  const risks = [...ui.el('myRisksTbody').querySelectorAll('tr')];
  assert.deepEqual(
    [...risks[0].querySelectorAll('td.text-end .badge')].map((b) => [b.textContent, b.className]),
    [
      ['16', 'badge text-bg-danger'],
      ['6', 'badge text-bg-warning'],
    ],
  );
  assert.match(
    risks[0].textContent,
    /Information \/ Databases[\s\S]*Availability[\s\S]*1 control\b/,
  );
  assert.equal(risks[1].querySelector('a').textContent, 'Laptops');
  assert.match(risks[1].textContent, /No threat summary[\s\S]*3 controls/);
  assert.equal(risks[1].querySelector('td.text-end .badge').className, 'badge text-bg-success');
  ui.el('myRisksRefresh').click();
  await settle(() => state.gets.filter((g) => g.startsWith('/api/v1/me/risks')).length === 2);
});

test("ISMS meetings, objectives, roles and assets list the user's responsibilities", async (t) => {
  const { ui, state } = await account(t);
  showTab(ui, 'my-meetings-tab');
  await settle(() => ui.el('myMeetingsTbody').querySelectorAll('tr').length === 2);
  const meetings = [...ui.el('myMeetingsTbody').querySelectorAll('tr')];
  assert.equal(ui.el('myMeetingsTbody').querySelector('img'), null);
  assert.match(
    meetings[0].textContent,
    /Management review[\s\S]*2026-10-02[\s\S]*09:00-10:00[\s\S]*Attendee[\s\S]*dana/,
  );
  assert.equal(
    meetings[0].querySelector('a').getAttribute('href'),
    '/isms-meeting.html?id=m1&framework=A',
  );
  assert.deepEqual(
    [...meetings[1].cells].slice(0, 6).map((c) => c.textContent.replace(/\s+/g, ' ').trim()),
    ['Meeting', '-', '14:00', '-', '-', '-'],
  );
  ui.el('myMeetingsRefresh').click();
  await settle(
    () => state.gets.filter((g) => g.startsWith('/api/v1/me/isms-meetings')).length === 2,
  );

  showTab(ui, 'my-isms-objectives-tab');
  await settle(() => ui.el('myIsmsObjectivesTbody').querySelectorAll('tr').length === 2);
  const objectives = [...ui.el('myIsmsObjectivesTbody').querySelectorAll('tr')];
  assert.match(
    objectives[0].textContent,
    /Reduce phishing[\s\S]*Click rate[\s\S]*Below 5%[\s\S]*dana[\s\S]*sam[\s\S]*2026-12-31/,
  );
  assert.equal(
    objectives[0].querySelector('a').getAttribute('href'),
    '/isms.html?tab=objectives&framework=A',
  );
  assert.match(objectives[1].textContent, /Objective/);
  ui.el('myIsmsObjectivesRefresh').click();
  await settle(
    () => state.gets.filter((g) => g.startsWith('/api/v1/me/isms-objectives')).length === 2,
  );

  showTab(ui, 'my-isms-role-tab');
  await settle(() => ui.el('myIsmsRoleTbody').querySelectorAll('tr').length === 2);
  const roles = [...ui.el('myIsmsRoleTbody').querySelectorAll('tr')];
  assert.match(
    roles[0].textContent,
    /Security lead[\s\S]*Owns the ISMS[\s\S]*role[\s\S]*holder[\s\S]*Board → CISO → Security lead/,
  );
  assert.match(roles[1].textContent, /fallback[\s\S]*role[\s\S]*member[\s\S]*fallback/);
  ui.el('myIsmsRoleRefresh').click();
  await settle(() => state.gets.filter((g) => g === '/api/v1/me/isms-role').length === 2);

  showTab(ui, 'my-isms-assets-tab');
  await settle(() => ui.el('myIsmsAssetsTbody').querySelectorAll('tr').length === 2);
  const assets = [...ui.el('myIsmsAssetsTbody').querySelectorAll('tr')];
  assert.match(
    assets[0].textContent,
    /Laptop fleet[\s\S]*Hardware \/ Laptops[\s\S]*Owned[\s\S]*Owner, Register holder[\s\S]*IT[\s\S]*Org → Finance/,
  );
  assert.match(assets[1].textContent, /Shared drive[\s\S]*Team files[\s\S]*-[\s\S]*Custodian/);
  assert.equal(
    assets[0].querySelector('a').getAttribute('href'),
    '/isms.html?tab=assets&framework=A',
  );
  ui.el('myIsmsAssetsRefresh').click();
  await settle(() => state.gets.filter((g) => g.startsWith('/api/v1/me/isms-assets')).length === 2);
});

const emptyLists = Object.fromEntries(Object.keys(lists).map((k) => [k, []]));
for (const [tab, endpoint, tbody, empty, wrap, statusId, message] of [
  [
    'my-questions-tab',
    '/api/v1/me/questions',
    'myQuestionsTbody',
    'myQuestionsEmpty',
    'myQuestionsTableWrap',
    'myQuestionsStatus',
    'Failed to load questions: Error: Offline',
  ],
  [
    'my-audits-tab',
    '/api/v1/me/audits',
    'myAuditsTbody',
    'myAuditsEmpty',
    'myAuditsTableWrap',
    'myAuditsStatus',
    'Failed to load audits: Error: Offline',
  ],
  [
    'my-risks-tab',
    '/api/v1/me/risks',
    'myRisksTbody',
    'myRisksEmpty',
    'myRisksTableWrap',
    'myRisksStatus',
    'Failed to load risks: Error: Offline',
  ],
  [
    'my-meetings-tab',
    '/api/v1/me/isms-meetings',
    'myMeetingsTbody',
    'myMeetingsEmpty',
    'myMeetingsTableWrap',
    'myMeetingsStatus',
    'Failed to load ISMS meetings: Error: Offline',
  ],
  [
    'my-isms-objectives-tab',
    '/api/v1/me/isms-objectives',
    'myIsmsObjectivesTbody',
    'myIsmsObjectivesEmpty',
    'myIsmsObjectivesTableWrap',
    'myIsmsObjectivesStatus',
    'Failed to load ISMS objectives: Error: Offline',
  ],
  [
    'my-isms-role-tab',
    '/api/v1/me/isms-role',
    'myIsmsRoleTbody',
    'myIsmsRoleEmpty',
    'myIsmsRoleTableWrap',
    'myIsmsRoleStatus',
    'Failed to load ISMS roles: Error: Offline',
  ],
  [
    'my-isms-assets-tab',
    '/api/v1/me/isms-assets',
    'myIsmsAssetsTbody',
    'myIsmsAssetsEmpty',
    'myIsmsAssetsTableWrap',
    'myIsmsAssetsStatus',
    'Failed to load ISMS assets: Error: Offline',
  ],
])
  test(`${tab} explains empty lists and load failures`, async (t) => {
    const empty_ = await account(t, { lists: emptyLists });
    showTab(empty_.ui, tab);
    await settle(
      () =>
        empty_.ui.el(empty).style.display === '' &&
        empty_.state.gets.some((g) => g.startsWith(endpoint)),
    );
    assert.equal(empty_.ui.el(wrap).style.display, 'none');
    assert.equal(empty_.ui.el(tbody).innerHTML, '');

    const failed = await account(t, { fail: { [endpoint]: true } });
    showTab(failed.ui, tab);
    await settle(() => failed.ui.el(statusId).textContent === message);
    assert.equal(failed.ui.el(statusId).className, 'alert alert-danger');
    assert.equal(failed.ui.el(wrap).style.display, 'none');
  });

test('disabled features skip their account lists', async (t) => {
  const features = {
    enabled: {
      questions: false,
      audits: false,
      risk_cia: false,
      meetings: false,
      objectives: false,
      organisation: false,
      assets: false,
    },
  };
  const { ui, state } = await account(t, { me: { is_admin: true, preferences: {}, features } });
  for (const tab of [
    'my-questions-tab',
    'my-audits-tab',
    'my-risks-tab',
    'my-meetings-tab',
    'my-isms-objectives-tab',
    'my-isms-role-tab',
    'my-isms-assets-tab',
  ])
    showTab(ui, tab);
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(
    state.gets.filter((g) => Object.keys(lists).some((k) => g.startsWith(k))),
    [],
  );
});

test("each tab's own hash opens it, and an unknown hash opens the default tab", async (t) => {
  for (const [hash, id] of [
    ['#preferences', 'preferences-tab'],
    ['#security', 'security-tab'],
    ['#saved-searches', 'saved-searches-tab'],
    ['#my-questions', 'my-questions-tab'],
    ['#my-audits', 'my-audits-tab'],
    ['#my-meetings', 'my-meetings-tab'],
    ['#my-risks', 'my-risks-tab'],
    ['#my-isms-objectives', 'my-isms-objectives-tab'],
    ['#my-isms-role', 'my-isms-role-tab'],
    ['#MY-ISMS-ASSETS', 'my-isms-assets-tab'],
  ]) {
    const tabs = fakeTabs();
    await account(t, { query: hash, tabs, lists: emptyLists });
    assert.deepEqual(tabs.shown, [id], hash);
  }
  const unknown = fakeTabs();
  await account(t, { query: '#password', tabs: unknown, lists: emptyLists });
  assert.deepEqual(unknown.shown, []);
});

test('preferences load defaults, toggle the timezone and save the chosen settings', async (t) => {
  const { ui, state } = await account(t, {
    preferences: {
      use_local_timezone: false,
      theme: 'dark',
      date_format: 'dmy',
      landing_page: '/custom/landing',
      auto_apply_filters: true,
      default_framework: 'LEGACY',
    },
  });
  assert.equal(ui.el('prefDefaultTzHelp').textContent, 'Default timezone: Australia/Melbourne.');
  assert.equal(
    ui.el('prefDefaultDateFormatHelp').textContent,
    'App default: DD/MM/YYYY. API values remain YYYY-MM-DD.',
  );
  assert.deepEqual(
    [...ui.el('prefTheme').options].map((o) => o.textContent),
    ['Purple', '<b>Dark</b>'],
  );
  assert.equal(ui.el('prefTheme').value, 'dark');
  assert.equal(ui.el('prefAutoApplyFilters').checked, true);
  assert.equal(ui.el('prefTimezone').disabled, true);
  // A custom landing page stays selectable.
  assert.equal(ui.el('prefLandingPage').value, '/custom/landing');
  assert.ok(ui.el('prefLandingPage').querySelector('optgroup[label="Current (custom)"]'));
  assert.ok(ui.el('prefLandingPage').querySelector('option[value="/admin.html"]'));
  // An unknown saved framework stays selectable alongside the catalogue.
  const frameworks = [...ui.el('prefDefaultFramework').options].map((o) => [
    o.value,
    o.textContent,
  ]);
  assert.deepEqual(frameworks, [
    ['', 'Use system default (Framework A (2))'],
    ['LEGACY', 'LEGACY (0)'],
    ['A', 'Framework A (2)'],
  ]);
  assert.equal(ui.el('prefDefaultFramework').value, 'LEGACY');

  ui.el('prefUseLocalTz').checked = true;
  ui.el('prefTimezone').value = '';
  ui.el('prefUseLocalTz').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('prefTimezone').disabled, false);
  assert.equal(ui.el('prefTimezone').value, ui.el('prefBrowserTz').textContent);
  ui.el('prefTimezone').value = 'Europe/London';
  ui.el('prefUseBrowserTz').click();
  assert.equal(ui.el('prefTimezone').value, ui.el('prefBrowserTz').textContent);
  ui.el('prefTimezone').value = 'Pacific/Auckland';
  ui.el('prefTheme').value = 'purple';
  ui.el('prefTheme').dispatchEvent(new ui.window.Event('change'));
  ui.el('prefLandingPage').value = '/events.html?unmapped=true';
  ui.el('prefDefaultFramework').value = '';

  ui.el('prefsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  // The badge holds "Saved" in the markup, so wait for the page-level status.
  await settle(() => ui.el('status').textContent === 'Preferences saved');
  assert.equal(ui.el('prefsSaveToast').style.display, '');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/me/preferences',
    body: {
      use_local_timezone: true,
      theme: 'purple',
      date_format: 'dmy',
      auto_apply_filters: true,
      landing_page: '/events.html?unmapped=true',
      default_framework: null,
      source_colors: {},
      timezone: 'Pacific/Auckland',
    },
  });
  assert.equal(ui.el('prefsSaveToast').className, 'badge text-bg-success');
  assert.equal(ui.window.keenPreferences.theme, 'purple');
  assert.equal(ui.window.keenPreferredFramework, '');
});

test('a rejected preference save is reported beside the button', async (t) => {
  const { ui } = await account(t, { fail: { patch: true, sources: true } });
  assert.equal(
    ui.el('sourceColorsLoading').textContent,
    'Source colours are unavailable on this server.',
  );
  ui.el('prefsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('prefsSaveToast').textContent === 'Failed');
  assert.equal(ui.el('prefsSaveToast').className, 'badge text-bg-danger');
  assert.equal(ui.el('status').textContent, 'Failed: Error: Rejected');
});

test('source colour overrides validate hex values and can be reset', async (t) => {
  const { ui, state } = await account(t, { preferences: { source_colors: { okta: '#00ff00' } } });
  const body = ui.el('sourceColorsBody');
  assert.equal(ui.el('sourceColorsWrap').style.display, '');
  const rows = [...body.querySelectorAll('tr')];
  assert.equal(rows.length, 2);
  // Three-digit defaults are expanded; invalid defaults show no chip.
  assert.equal(rows[0].querySelector('.badge').textContent, '#112233');
  assert.equal(rows[1].querySelectorAll('td')[2].textContent.trim(), '-');
  const picker = (src) => body.querySelector(`[data-src-color="${src}"]`);
  const hex = (src) => body.querySelector(`[data-src-hex="${src}"]`);
  assert.equal(hex('okta').value, '#00FF00');
  const change = (el, value) => {
    el.value = value;
    el.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  };
  change(picker('keen-agent'), '#abcdef');
  assert.equal(hex('keen-agent').value, '#ABCDEF');
  change(hex('keen-agent'), 'zzz');
  assert.equal(ui.el('status').textContent, 'Invalid hex colour (use #RRGGBB)');
  assert.equal(hex('keen-agent').value, '#ABCDEF');
  change(hex('okta'), 'f00');
  // Choosing the default colour removes the override.
  change(picker('keen-agent'), '#112233');
  body.querySelector('[data-src-reset="okta"]').click();
  assert.equal(hex('okta').value, '');
  change(hex('okta'), '#123456');
  ui.el('prefsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0].body.source_colors, { okta: '#123456' });

  ui.el('sourceColorsResetAll').click();
  await settle(() => state.gets.filter((g) => g === '/api/v1/sources/meta').length === 2);
  assert.equal(state.prompts.at(-1), 'Reset all your source colour overrides?');
  await settle(() => body.querySelector('[data-src-hex="okta"]')?.value === '');
  ui.el('prefsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 2);
  assert.deepEqual(state.patched[1].body.source_colors, {});
});

test('declining the reset keeps source colour overrides', async (t) => {
  const { ui, state } = await account(t, {
    confirm: false,
    preferences: { source_colors: { okta: '#00ff00' } },
  });
  ui.el('sourceColorsResetAll').click();
  ui.el('prefsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0].body.source_colors, { okta: '#00ff00' });
});

test('saved searches can be deleted and duplicate names are disambiguated for the landing page', async (t) => {
  const saved = [
    { id: 's1', name: 'Unmapped', url: '/events.html?unmapped=true' },
    { id: 's2', name: 'unmapped', url: '/events.html?source=okta&q=<b>x</b>' },
    { id: 's5', name: 'R&D <b>reviews</b>', url: '/events.html?q=r%26d' },
    { id: 's3', name: 'Danger', url: 'javascript:alert(1)' },
    { id: 's4', name: '', url: '/events.html' },
  ];
  const { ui, state } = await account(t, { saved });
  const links = [...ui.el('savedSearchesList').querySelectorAll('a')];
  assert.equal(links[0].getAttribute('href'), '/events.html?unmapped=true');
  assert.equal(links[2].textContent, 'R&D <b>reviews</b>');
  assert.equal(links[3].getAttribute('href'), '#');
  assert.equal(links[4].textContent, '(untitled)');
  assert.equal(ui.el('savedSearchesEmpty').style.display, 'none');
  const group = ui.el('prefLandingPage').querySelector('optgroup[label="Saved searches"]');
  assert.deepEqual(
    [...group.querySelectorAll('option')].map((o) => o.textContent),
    [
      'Unmapped - /events.html?unmapped=true',
      'unmapped - /events.html?source=okta&q=<b>x</b>',
      'R&D <b>reviews</b>',
      'Danger',
    ],
  );
  // Names are shown as text exactly once-escaped, never as markup.
  assert.equal(group.querySelector('b'), null);

  ui.el('savedSearchesList').querySelector('button').click();
  await settle(() => state.removed.length === 1);
  assert.deepEqual(state.removed, ['/api/v1/me/saved-searches/s1']);
  assert.equal(state.prompts[0], 'Delete saved search "Unmapped"?');
});

test('saved search failures are reported in the card', async (t) => {
  const failing = await account(t, { fail: { remove: true } });
  failing.ui.el('savedSearchesList').querySelector('button').click();
  await settle(
    () => failing.ui.el('status').textContent === 'Failed to delete: Error: Not allowed',
  );
  const declined = await account(t, { confirm: false });
  declined.ui.el('savedSearchesList').querySelector('button').click();
  assert.deepEqual(declined.state.removed, []);
  const unavailable = await account(t, { fail: { saved: true } });
  const message = unavailable.ui.el('savedSearchesEmpty');
  await settle(() => message.textContent === 'Failed to load saved searches: Error: Unsupported');
  assert.equal(message.className, 'alert alert-danger mb-0');
  assert.equal(message.style.display, '');
  assert.equal(unavailable.ui.el('savedSearchesCard').style.display, '');
  const none = await account(t, { saved: [] });
  assert.equal(none.ui.el('savedSearchesEmpty').style.display, '');
});

test('sign-out follows the configured logout route', async (t) => {
  const make = () => ({
    origin: 'https://keen.example',
    href: 'https://keen.example/account.html',
    assign() {},
  });
  const upstream = make();
  const proxy = await account(t, {
    me: { preferences: {}, logout_url: 'https://sso.example.test/logout' },
    globals: { location: upstream },
  });
  proxy.ui.el('logoutBtn').click();
  assert.equal(upstream.href, 'https://sso.example.test/logout');
  assert.equal(proxy.state.posted.length, 0);

  const oidc = make();
  const provider = await account(t, {
    me: { preferences: {}, logout_url: '/api/v1/auth/oidc/logout' },
    postResult: { logout_url: 'https://idp.example.test/end-session' },
    globals: { location: oidc },
  });
  provider.ui.el('logoutBtn').click();
  await settle(() => oidc.href === 'https://idp.example.test/end-session');
  assert.deepEqual(provider.state.posted, [{ path: '/api/v1/auth/oidc/logout', body: {} }]);

  const local = make();
  const session = await account(t, { me: { preferences: {} }, globals: { location: local } });
  session.ui.el('logoutBtn').click();
  await settle(() => local.href === '/login.html');
  assert.deepEqual(session.state.posted, [{ path: '/api/v1/auth/logout', body: {} }]);
});

test('a password change signs the user out and clears the fields', async (t) => {
  const assigned = [];
  const location = {
    origin: 'https://keen.example',
    href: 'https://keen.example/account.html',
    assign: (u) => assigned.push(u),
  };
  const { ui, state } = await account(t, { globals: { location } });
  ui.el('curPw').value = 'synthetic-current';
  ui.el('newPw').value = 'synthetic-new-passphrase';
  ui.el('pwForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => assigned.length === 1);
  assert.deepEqual(assigned, ['/login.html']);
  assert.deepEqual(state.posted, [
    {
      path: '/api/v1/me/password',
      body: { current_password: 'synthetic-current', new_password: 'synthetic-new-passphrase' },
    },
  ]);
  assert.equal(ui.el('curPw').value, '');
  assert.equal(ui.el('newPw').value, '');
  assert.equal(ui.el('status').textContent, 'Password updated. Sign in again to continue.');
});

test('the default landing page is offered as Home', async (t) => {
  const { ui, state } = await account(t, { preferences: {} });
  assert.equal(ui.el('prefLandingPage').value, '/');
  assert.equal(ui.el('prefLandingPage').selectedOptions[0].textContent, 'Home');
  assert.equal(ui.el('prefLandingPage').querySelector('optgroup[label="Current (custom)"]'), null);
  ui.el('prefsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].body.landing_page, '/');
});

test('a framework catalogue that cannot load still offers the saved default framework', async (t) => {
  const { ui } = await account(t, {
    preferences: { default_framework: '<b>LEGACY</b>' },
    // The catalogue endpoint comes from the UI configuration; this one is down.
    vendor: { getUiConfig: () => ({ framework: { endpoint: '/api/v1/frameworks-down' } }) },
  });
  await settle(() => ui.el('prefDefaultFramework').disabled === false);
  assert.deepEqual(
    [...ui.el('prefDefaultFramework').options].map((o) => [o.value, o.textContent]),
    [
      ['', 'Use system default'],
      ['<b>LEGACY</b>', '<b>LEGACY</b>'],
    ],
  );
  assert.equal(ui.el('prefDefaultFramework').querySelector('b'), null);
  assert.equal(ui.el('prefDefaultFramework').value, '<b>LEGACY</b>');

  const unsaved = await account(t, {
    vendor: { getUiConfig: () => ({ framework: { endpoint: '/api/v1/frameworks-down' } }) },
  });
  await settle(() => unsaved.ui.el('prefDefaultFramework').disabled === false);
  assert.deepEqual(
    [...unsaved.ui.el('prefDefaultFramework').options].map((o) => o.value),
    [''],
  );
});

test('Refresh fetches my questions again and shows a later failure in the card', async (t) => {
  const fail = {};
  const { ui, state } = await account(t, { query: '#my-questions', tabs: fakeTabs(), fail });
  await settle(() => ui.el('myQuestionsTbody').querySelectorAll('tr').length === 3);
  const requests = () => state.gets.filter((g) => g.startsWith('/api/v1/me/questions')).length;
  assert.equal(requests(), 1);

  // Re-showing the tab reuses the loaded list; Refresh asks again.
  showTab(ui, 'my-questions-tab');
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(requests(), 1);
  ui.el('myQuestionsRefresh').click();
  await settle(() => requests() === 2);
  await settle(() => ui.el('myQuestionsTbody').querySelectorAll('tr').length === 3);
  assert.equal(state.bell, 2);

  fail['/api/v1/me/questions'] = true;
  ui.el('myQuestionsRefresh').click();
  await settle(() => ui.el('myQuestionsStatus').textContent.includes('Offline'));
  assert.equal(ui.el('myQuestionsStatus').textContent, 'Failed to load questions: Error: Offline');
  assert.equal(ui.el('myQuestionsTableWrap').style.display, 'none');
});
