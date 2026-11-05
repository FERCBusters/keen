// The account page where browsers, catalogues and lists give it little to go
// on: no known time zone, an empty framework catalogue, dark source colours,
// sparse ISMS records, refused address updates and the small-screen menu.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const pause = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

async function account(t, options = {}) {
  const state = { gets: [], patched: [], removed: [] };
  const ui = await page(t, 'account', {
    query: options.query || '',
    me: options.me || { is_admin: true, preferences: options.preferences || {} },
    confirm: options.confirm ?? true,
    globals: options.globals,
    vendor: options.vendor,
    setup(window) {
      if (options.tabs) window.bootstrap = options.tabs;
      options.setup?.(window);
    },
    get: async (u) => {
      state.gets.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/sources/meta') return { items: options.sources ?? [] };
      if (u.pathname === '/api/v1/me/saved-searches') return structuredClone(options.saved ?? []);
      if (u.pathname === '/api/v1/frameworks-empty') return { items: [], default: '' };
      if (options.lists?.[u.pathname]) return { items: structuredClone(options.lists[u.pathname]) };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, body) => {
      state.patched.push(plain(body));
      return plain(body);
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return {};
    },
  });
  await settle(
    () => state.gets.some((g) => g.startsWith('/api/v1/me/saved-searches')) && ui.idle(),
  );
  await pause();
  return { ui, state };
}
const save = (ui) =>
  ui.el('prefsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
const showTab = (ui, id) => ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));

test('a browser that cannot name its time zone is shown as unknown', async (t) => {
  // One browser reports no zone; another cannot read its locale at all.
  for (const Intl of [
    { DateTimeFormat: () => ({ resolvedOptions: () => ({}) }) },
    {
      DateTimeFormat: () => {
        throw new RangeError('Incorrect locale information provided');
      },
    },
  ]) {
    const { ui, state } = await account(t, { globals: { Intl } });
    assert.equal(ui.el('prefBrowserTz').textContent, '(unknown)');
    assert.equal(ui.el('prefTimezone').value, '');
    ui.el('prefUseLocalTz').checked = true;
    ui.el('prefUseLocalTz').dispatchEvent(new ui.window.Event('change'));
    assert.equal(ui.el('prefTimezone').value, '');
    save(ui);
    await settle(() => state.patched.length === 1);
    assert.equal(state.patched[0].timezone, '');
  }
});

test('local time with the zone field cleared saves the browser zone', async (t) => {
  const { ui, state } = await account(t, { preferences: { timezone: 'Europe/London' } });
  const browser = ui.el('prefBrowserTz').textContent;
  assert.ok(browser && browser !== '(unknown)');
  ui.el('prefUseLocalTz').checked = true;
  ui.el('prefTimezone').value = '   ';
  save(ui);
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].use_local_timezone, true);
  assert.equal(state.patched[0].timezone, browser);
});

test('a framework catalogue with nothing enabled offers the built-in default', async (t) => {
  const { ui } = await account(t, {
    preferences: { default_framework: 'LEGACY' },
    vendor: { getUiConfig: () => ({ framework: { endpoint: '/api/v1/frameworks-empty' } }) },
  });
  await settle(() => ui.el('prefDefaultFramework').disabled === false);
  assert.deepEqual(
    [...ui.el('prefDefaultFramework').options].map((o) => [o.value, o.textContent]),
    [
      ['', 'Use system default (KEEN-AF:1.0)'],
      ['LEGACY', 'LEGACY (0)'],
    ],
  );
  assert.equal(ui.el('prefDefaultFramework').value, 'LEGACY');
});

test('a chosen default framework is remembered by the browser after saving', async (t) => {
  const stored = new Map();
  const localStorage = {
    setItem: (key, value) => stored.set(key, value),
    removeItem: (key) => stored.delete(key),
  };
  const { ui, state } = await account(t, { globals: { localStorage } });
  ui.el('prefDefaultFramework').value = 'A';
  save(ui);
  await settle(() => ui.el('status').textContent === 'Preferences saved');
  assert.equal(state.patched[0].default_framework, 'A');
  assert.equal(stored.get('keen_framework'), 'A');
  assert.equal(ui.window.mospPreferredFramework, 'A');
  // Clearing it again forgets it.
  ui.el('prefDefaultFramework').value = '';
  save(ui);
  await settle(() => state.patched.length === 2 && !stored.has('keen_framework'));
});

test('dark source colours get a light border and text, and the default colour clears an override', async (t) => {
  const { ui, state } = await account(t, {
    preferences: { source_colors: { okta: '#00ff00' } },
    sources: [
      { source: 'okta', label: 'Okta', default_color: '#000080' },
      { source: 'rss', label: '<b>RSS</b>', default_color: '#f5f5f5' },
    ],
  });
  const body = ui.el('sourceColorsBody');
  const chip = (row) =>
    body.querySelectorAll('tr')[row].querySelectorAll('td')[2].querySelector('.badge');
  assert.match(
    chip(0).getAttribute('style'),
    /border:1px solid rgba\(255,255,255,0\.28\); color:#f8fafc/,
  );
  assert.match(chip(1).getAttribute('style'), /border:1px solid rgba\(0,0,0,0\.14\)/);
  assert.equal(body.querySelector('b'), null);

  // A click on a label cell or a colour field resets nothing.
  body.querySelectorAll('tr')[0].querySelectorAll('td')[1].click();
  body.querySelector('[data-src-color="okta"]').click();
  assert.equal(body.querySelector('[data-src-hex="okta"]').value, '#00FF00');

  const hex = body.querySelector('[data-src-hex="okta"]');
  hex.value = '#000080';
  hex.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.equal(hex.value, '#000080'.toUpperCase());
  save(ui);
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0].source_colors, {});
});

test('a saved search without a name is deleted under that description', async (t) => {
  const { ui, state } = await account(t, {
    saved: [{ id: 's9', name: '', url: '/events.html?q=x' }],
  });
  ui.el('savedSearchesList').querySelector('button').click();
  await settle(() => state.removed.length === 1);
  assert.equal(ui.confirmations[0].message, 'Delete the saved search (without a name)?');
  assert.deepEqual(state.removed, ['/api/v1/me/saved-searches/s9']);
});

test('the account menu opens on small screens and closes when a tab is chosen', async (t) => {
  const { ui } = await account(t);
  const toggle = ui.el('account-navigation-toggle');
  const tabs = ui.el('accountTabs');
  toggle.click();
  assert.equal(tabs.classList.contains('admin-nav-open'), true);
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  toggle.click();
  assert.equal(tabs.classList.contains('admin-nav-open'), false);
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  toggle.click();
  ui.el('security-tab').dispatchEvent(new ui.window.Event('shown.bs.tab', { bubbles: true }));
  assert.equal(tabs.classList.contains('admin-nav-open'), false);
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(ui.window.location.hash, '#security');
});

test('a browser refusing to update the address still opens the tab and loads its list', async (t) => {
  const { ui, state } = await account(t, {
    lists: { '/api/v1/me/audits': [] },
    setup(window) {
      window.history.replaceState = () => {
        throw new window.DOMException('Too many history updates', 'SecurityError');
      };
    },
  });
  showTab(ui, 'my-audits-tab');
  await settle(() => state.gets.includes('/api/v1/me/audits'));
  await settle(() => ui.el('myAuditsEmpty').style.display === '');
  assert.equal(ui.window.location.hash, '');
});

test('a click in My questions away from a delete button deletes nothing', async (t) => {
  const { ui, state } = await account(t, {
    lists: {
      '/api/v1/me/questions': [
        { thread_id: 't1', target_label: 'Policy', target_url: '/x.html', status: 'open' },
      ],
    },
  });
  showTab(ui, 'my-questions-tab');
  await settle(() => ui.el('myQuestionsTbody').querySelector('[data-delete-question]'));
  ui.el('myQuestionsTbody').querySelector('td').click();
  await pause();
  assert.deepEqual(state.removed, []);
  assert.equal(ui.confirmations.length, 0);
});

test('controls without a reference, meetings without times and assets with a subcategory record', async (t) => {
  const { ui } = await account(t, {
    lists: {
      '/api/v1/me/controls': [
        {
          kind: 'control',
          id: 'c1',
          framework: 'A',
          framework_name: 'Framework A',
          ref: '',
          title: 'Untitled',
          in_scope: true,
        },
      ],
      '/api/v1/me/isms-meetings': [
        { id: 'm1', title: 'Stand-up', end_time: '10:00', attendees: [], apologies: [] },
        { id: 'm2', title: 'Review', attendees: [], apologies: [] },
      ],
      '/api/v1/me/isms-assets': [
        {
          asset: 'Laptops',
          name: 'Laptops',
          category: { name: 'Hardware' },
          subcategory: { name: 'Portable' },
          description: '',
          is_owner: true,
        },
      ],
    },
  });
  showTab(ui, 'my-controls-tab');
  await settle(() => ui.el('myControlsTbody').querySelector('a'));
  assert.equal(ui.el('myControlsTbody').querySelector('a').textContent, '-');

  showTab(ui, 'my-meetings-tab');
  await settle(() => ui.el('myMeetingsTbody').querySelectorAll('a').length === 2);
  assert.deepEqual(
    [...ui.el('myMeetingsTbody').querySelectorAll('tr')].map((tr) => tr.cells[2].textContent),
    ['10:00', '-'],
  );

  showTab(ui, 'my-isms-assets-tab');
  await settle(() => ui.el('myIsmsAssetsTbody').querySelector('a'));
  assert.equal(
    ui.el('myIsmsAssetsTbody').querySelector('td .small-muted').textContent,
    'Hardware / Portable',
  );
});
