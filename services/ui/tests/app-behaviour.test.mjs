import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const ui = await loadUi('app.js');
const a = ui.api;
test.after(() => ui.close());

for (const [label, input, expected] of [
  ['empty', { total: 0 }, [0, 0, true, true]],
  ['first', { total: 101, limit: 50 }, [1, 50, true, false]],
  ['middle', { total: 101, limit: 50, offset: 50 }, [51, 100, false, false]],
  ['last', { total: 101, limit: 50, offset: 100 }, [101, 101, false, true]],
  ['exact', { total: 100, limit: 50, offset: 50 }, [51, 100, false, true]],
  ['negative offset', { total: 5, offset: -20 }, [1, 5, true, true]],
])
  test('pagination ' + label, () => {
    const s = a.offsetPagerState(input);
    assert.deepEqual([s.start, s.end, s.prevDisabled, s.nextDisabled], expected);
    assert.ok(s.prevOffset >= 0 && s.nextOffset > s.offset);
  });

test('both pagers reflect limits and disabled buttons cannot paginate', () => {
  const doc = ui.document;
  doc.body.innerHTML =
    '<button id="p"></button><button id="n"></button><button id="p2"></button><button id="n2"></button>';
  const pager = {
    prev: [doc.getElementById('p'), doc.getElementById('p2')],
    next: [doc.getElementById('n'), doc.getElementById('n2')],
  };
  const calls = [];
  a.wireOffsetPagerButtons(pager, (d) => calls.push(d));
  a.setOffsetPagerDisabled(pager, { total: 51, limit: 50, offset: 0 });
  pager.prev[0].click();
  pager.next[1].click();
  assert.deepEqual(calls, ['next']);
  a.setOffsetPagerDisabled(pager, { total: 51, limit: 50, offset: 50 });
  pager.next[0].click();
  pager.prev[1].click();
  assert.deepEqual(calls, ['next', 'prev']);
});

for (const status of ['open', 'in_progress', 'completed', 'archived', 'unknown'])
  test('audit status ' + status, () => {
    assert.equal(a.isAuditActive({ status }), ['open', 'in_progress'].includes(status));
    assert.equal(a.isAuditLocked(status), ['completed', 'archived'].includes(status));
  });
for (const [user, allowed] of [
  [null, false],
  [{}, false],
  [{ can_manage_audits: true }, true],
  [{ is_admin: true }, true],
  [{ is_admin: false }, false],
])
  test('audit sampling permission ' + JSON.stringify(user), () =>
    assert.equal(a.canSampleIntoAudit(user), allowed),
  );

for (const [operator, value, ok] of [
  ['lt', 9, true],
  ['lt', 10, false],
  ['lte', 10, true],
  ['eq', 10, true],
  ['eq', 11, false],
  ['gte', 10, true],
  ['gte', 9, false],
  ['gt', 11, true],
  ['gt', 10, false],
])
  test(`metric ${operator} ${value}`, () => {
    const result = a.effectivenessMetricThresholdState(
      { threshold_operator: operator, target_value: 10, target_unit: '%' },
      { metric_value: value },
    );
    assert.equal(result.ok, ok);
    assert.equal(result.state, ok ? 'ok' : 'bad');
    assert.match(result.title, /%/);
  });
for (const value of [null, undefined, '', 'bad', Infinity])
  test('invalid metric ' + String(value), () => {
    assert.equal(
      a.effectivenessMetricThresholdState(
        { threshold_operator: 'gte', target_value: 10 },
        { metric_value: value },
      ),
      null,
    );
  });
test('zero metric remains a valid value', () =>
  assert.equal(
    a.effectivenessMetricThresholdState(
      { threshold_operator: 'eq', target_value: 0 },
      { metric_value: 0 },
    ).ok,
    true,
  ));

for (const [input, expected] of [
  [null, 'Fallback'],
  ['  Miguel  ', 'Miguel'],
  [{ username: 'user', name: 'Name' }, 'user'],
  [{ user: { email: 'a@example.test' } }, 'a@example.test'],
  [{}, 'Fallback'],
])
  test('user label ' + JSON.stringify(input), () =>
    assert.equal(a.userDisplayName(input, 'Fallback'), expected),
  );
test('user labels and titles cannot introduce markup', () => {
  const html = a.userPillHtml({
    username: '<img src=x onerror=alert(1)>',
    email: '" onmouseover="alert(1)',
  });
  ui.document.body.innerHTML = html;
  assert.equal(ui.document.querySelector('img'), null);
  assert.equal(ui.document.querySelector('[onmouseover]'), null);
  assert.match(ui.document.body.textContent, /<img/);
});

for (const url of [
  '/home/user',
  '//outside.test',
  'javascript:alert(1)',
  'data:text/html,x',
  'ftp://host/a',
  'https://',
  'https://a.test/' + 'x'.repeat(2049),
])
  test('upstream rejects ' + url.slice(0, 40), () => assert.equal(a.safeAbsoluteHttpHref(url), ''));
for (const url of ['https://example.test/a?x=1', 'http://example.test', 'HTTPS://example.test/a'])
  test('upstream permits ' + url, () => assert.equal(a.safeAbsoluteHttpHref(url), url));

// Browsers read "\\" as "/" and drop tabs and newlines, so these leave the origin.
for (const url of [
  '/\\outside.test/login',
  '/\t/outside.test/login',
  '/\n/outside.test',
  '//outside.test',
  'javascript:alert(1)',
  ' javascript:alert(1)',
  'data:text/html,x',
  'https://a.test/' + 'x'.repeat(2049),
])
  test('link rejects ' + JSON.stringify(url.slice(0, 40)), () =>
    assert.equal(a.safeExternalHref(url), ''),
  );
for (const [url, expected] of [
  ['/event.html?id=1#questions', '/event.html?id=1#questions'],
  ['https://example.test/a?x=1', 'https://example.test/a?x=1'],
  ['http://example.test', 'http://example.test/'],
])
  test('link permits ' + url, () => assert.equal(a.safeExternalHref(url), expected));

test('arrow links use the hardened link check', () => {
  assert.equal(a.arrowLinkHtml('/\\outside.test/login'), '');
  assert.match(a.arrowLinkHtml('https://example.test/a'), /href="https:\/\/example\.test\/a"/);
});

for (const color of ['#fff', '#ffffff'])
  test('light badge ' + color, () => assert.equal(a.idealTextColor(color), '#111827'));
for (const color of ['#000', '#000000'])
  test('dark badge ' + color, () => assert.equal(a.idealTextColor(color), '#f8fafc'));

test('changelog loader propagates API failure without rendering error markup', async () => {
  const u = await loadUi('app.js', {
    vendor: {
      apiGet: async () => {
        throw new Error('<img src=x onerror=alert(1)>');
      },
    },
  });
  try {
    const target = u.document.createElement('div');
    await assert.rejects(u.api.loadEntityChangelog(target, '/api/fail'), /onerror/);
    assert.equal(target.querySelector('img'), null);
    assert.match(target.textContent, /Loading/);
  } finally {
    u.close();
  }
});

test('navbar native logout uses POST and stops the vendor GET handler', async () => {
  const calls = [];
  let vendorClicked = false;
  let u;
  u = await loadUi('app.js', {
    html: '<div id="navbar"><button id="navLogout">Logout</button></div>',
    vendor: {
      getUiConfig: () => ({ nav: {}, framework: {} }),
      initCollapsibleFilterSections: () => {},
      initNavbar: async () => {
        u.document.getElementById('navLogout').addEventListener('click', () => {
          vendorClicked = true;
        });
        return { logout_url: '/api/v1/auth/oidc/logout' };
      },
      apiGet: async () => ({ items: [] }),
      apiPost: async (url) => {
        calls.push(url);
        throw new Error('CSRF rejected');
      },
    },
  });
  try {
    await u.api.initNavbar();
    const button = u.document.getElementById('navLogout');
    button.click();
    await new Promise((r) => setTimeout(r, 10));
    assert.deepEqual(calls, ['/api/v1/auth/oidc/logout']);
    assert.equal(vendorClicked, false);
    assert.equal(button.textContent, 'Retry logout');
    assert.equal(button.disabled, false);
  } finally {
    u.close();
  }
});

for (const [user, audit, allowed] of [
  [{ can_manage_audits: true, user_id: 'one' }, { created_by_user_id: 'one' }, true],
  [{ can_manage_audits: true, user_id: 'one' }, { created_by_user_id: 'two' }, false],
  [{ can_manage_audits: true }, {}, false],
  [{ can_view_audits: true, user_id: 'one' }, { created_by_user_id: 'one' }, false],
  [{ can_admin_audits: true }, { created_by_user_id: 'two' }, true],
  [{ is_admin: true }, { created_by_user_id: null }, true],
])
  test(`audit editing ownership ${JSON.stringify(user)} ${JSON.stringify(audit)}`, () => {
    assert.equal(a.canEditAuditForUser(user, audit), allowed);
  });

for (const [user, type, allowed] of [
  [{ can_manage_audits: true }, 'event', false],
  [{ can_manage_audits: true, can_view_events: true }, 'event', true],
  [{ can_admin_audits: true }, 'event', false],
  [{ can_manage_audits: true, can_view_isms: true }, 'bookstack_section', false],
  [{ can_manage_audits: true, can_sample_isms: true }, 'isms_document', true],
  [{ can_manage_audits: true, can_view_events: true }, 'story', false],
  [{ can_manage_audits: true, can_view_stories: true }, 'story', true],
  [{ is_admin: true }, 'event', true],
])
  test(`audit sampling source ${type} ${JSON.stringify(user)}`, () => {
    assert.equal(a.canSampleIntoAudit(user, type), allowed);
  });

test('page modules share navbar setup and its initial framework response', async () => {
  let starts = 0;
  let release;
  const ready = new Promise((resolve) => {
    release = resolve;
  });
  const me = { is_admin: true };
  const u = await loadUi('app.js', {
    html: '<div id="navbar"></div>',
    vendor: {
      getUiConfig: () => ({ nav: {}, framework: {} }),
      initCollapsibleFilterSections: () => {},
      initNavbar: async () => {
        starts++;
        await ready;
        return me;
      },
      getLoadedFrameworkCatalog: () => ({ items: [] }),
      // Any additional catalogue request fails this test.
    },
  });
  try {
    const first = u.api.initNavbar();
    const second = u.api.initNavbar();
    assert.equal(first, second);
    release();
    assert.equal(await first, me);
    assert.equal(await u.api.initNavbar(), me);
    assert.equal(starts, 1);
  } finally {
    u.close();
  }
});

test('failed navbar account requests can be retried', async () => {
  let starts = 0;
  const u = await loadUi('app.js', {
    vendor: {
      getUiConfig: () => ({ nav: {}, framework: {} }),
      initCollapsibleFilterSections: () => {},
      initNavbar: async () => {
        if (++starts === 1) throw new Error('Account unavailable');
        return { is_admin: true };
      },
      getLoadedFrameworkCatalog: () => ({ items: [] }),
    },
  });
  try {
    await assert.rejects(u.api.initNavbar(), /Account unavailable/);
    assert.equal((await u.api.initNavbar()).is_admin, true);
    assert.equal(starts, 2);
  } finally {
    u.close();
  }
});

test('the Stories link follows Audits and is offered once', () => {
  const links = [{ key: 'events' }, { key: 'audits' }, { key: 'admin' }];
  const withStories = a.withStoriesLink(links);
  assert.deepEqual(
    [...withStories.map((link) => link.key)],
    ['events', 'audits', 'stories', 'admin'],
  );
  assert.deepEqual(JSON.parse(JSON.stringify(withStories[2])), {
    key: 'stories',
    label: 'Stories',
    href: '/stories.html',
    requireAny: ['is_admin', 'can_view_stories'],
  });
  assert.deepEqual([...links.map((link) => link.key)], ['events', 'audits', 'admin']);
  assert.equal(a.withStoriesLink(withStories).length, 4);
  assert.deepEqual(
    [...a.withStoriesLink([{ key: 'controls' }]).map((link) => link.key)],
    ['controls', 'stories'],
  );
  assert.deepEqual([...a.withStoriesLink(undefined).map((link) => link.key)], ['stories']);
});

test('changelog values show blanks, booleans and long objects as inert text', () => {
  const cyclic = { name: 'loop' };
  cyclic.self = cyclic;
  const big = { notes: 'x'.repeat(1300) };
  const html = a.entityChangelogHtml([
    {
      summary: 'Settings changed',
      changed_by_username: 'root',
      changes: [
        { field: 'enabled', before: false, after: true },
        { field: 'owner', before: null, after: '' },
        { field: 'config', before: { a: '<b>1</b>' }, after: big },
        { field: 'loop', before: cyclic, after: 'ok' },
      ],
    },
  ]);
  const doc = ui.document;
  doc.body.innerHTML = html;
  const cells = [...doc.querySelectorAll('tbody tr')].map((row) => [
    row.cells[1].textContent,
    row.cells[2].textContent,
  ]);
  assert.deepEqual(cells[0], ['No', 'Yes']);
  assert.deepEqual(cells[1], ['-', '-']);
  assert.match(cells[2][0], /"a": "<b>1<\/b>"/);
  assert.equal(doc.querySelector('b'), null);
  // Long objects are cut at 1200 characters; an unserialisable one falls back to its string form.
  assert.ok(cells[2][1].endsWith('…'));
  assert.equal(cells[2][1].length, 1201);
  assert.equal(cells[3][0], '[object Object]');
  assert.equal(doc.querySelectorAll('pre.changelog-json').length, 3);
});
