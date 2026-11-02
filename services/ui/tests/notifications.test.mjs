import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const hostile = '<img src=x onerror=alert(1)>';

function inboxItems() {
  return [
    {
      id: 'n1',
      kind: 'audit_started',
      summary: 'New audit',
      title: 'Access review ' + hostile,
      href: '/audit.html?id=a1',
      date_label: 'Starts',
      date: '2026-11-09',
      actor: 'dana',
      created_at: '2026-11-02T01:00:00',
      read_at: null,
    },
    {
      id: 'n2',
      kind: 'quiz_round',
      summary: 'Quiz to take',
      title: 'Security basics',
      href: '/quiz-take.html?round=r1',
      date_label: 'Due',
      date: '2026-11-09',
      actor: null,
      created_at: '2026-11-01T01:00:00',
      read_at: null,
    },
    {
      id: 'n3',
      kind: 'meeting_minutes',
      summary: 'Minutes added',
      title: 'Management review',
      href: 'javascript:alert(1)',
      date_label: 'Meeting on',
      date: null,
      actor: 'erin',
      created_at: '2026-10-30T01:00:00',
      read_at: '2026-10-31T01:00:00',
    },
    {
      id: 'n5',
      kind: 'measure_outside',
      summary: 'Measure outside its threshold',
      title: 'Patched within 14 days',
      href: '/isms-effectiveness-measure.html?id=m1',
      date_label: 'Recorded',
      date: '2026-10-29',
      actor: null,
      created_at: '2026-10-29T02:00:00',
      read_at: '2026-10-31T01:00:00',
    },
    {
      id: 'n4',
      kind: 'other',
      summary: 'Something',
      title: 'Elsewhere',
      href: '//evil.example/x',
      date_label: '',
      date: null,
      actor: null,
      created_at: '2026-10-29T01:00:00',
      read_at: '2026-10-31T01:00:00',
    },
  ];
}

async function inbox(t, { fail = null, failRead = false, failAll = false } = {}) {
  const state = { items: inboxItems(), navigations: [], bell: 0 };
  const unread = () => state.items.filter((item) => !item.read_at).length;
  const ui = await page(t, 'notifications', {
    me: {},
    globals: { location: { assign: (href) => state.navigations.push(href) } },
    setup(window) {
      window.keenRefreshBell = () => state.bell++;
    },
    get: async (url) => {
      assert.equal(url.pathname, '/api/v1/me/inbox');
      if (fail) throw new Error(fail);
      const only = url.searchParams.get('unread') === 'true';
      return {
        items: structuredClone(state.items.filter((item) => !only || !item.read_at)),
        unread_count: unread(),
      };
    },
    post: async (url) => {
      if (url.pathname === '/api/v1/me/inbox/read-all') {
        if (failAll) throw new Error('Inbox offline');
        for (const item of state.items) item.read_at ||= '2026-11-02T02:00:00';
        return { marked: 2, unread_count: 0 };
      }
      const id = url.pathname.match(/^\/api\/v1\/me\/inbox\/([^/]+)\/read$/)[1];
      if (failRead) throw new Error('Inbox offline');
      state.items.find((item) => item.id === id).read_at = '2026-11-02T02:00:00';
      return { unread_count: unread() };
    },
  });
  await settle(() => ui.el('inbox-meta').textContent !== 'Loading notifications');
  return { ui, state };
}

const rows = (ui) => [...ui.el('inbox-list').querySelectorAll('a[data-item]')];

test('the inbox lists notifications newest first with unread ones marked', async (t) => {
  const { ui } = await inbox(t);
  const links = rows(ui);
  assert.deepEqual(
    links.map((link) => link.getAttribute('href')),
    [
      '/audit.html?id=a1',
      '/quiz-take.html?round=r1',
      '#',
      '/isms-effectiveness-measure.html?id=m1',
      '#',
    ],
  );
  assert.equal(ui.el('inbox-meta').textContent, '2 unread notifications');
  assert.equal(ui.el('inbox-read-all').disabled, false);
  const [audit, quiz, minutes, measure, other] = links;
  assert.match(audit.textContent, /^NewNew audit: Access review <img/);
  assert.equal(audit.querySelector('img'), null);
  assert.match(audit.textContent, /Starts 2026-11-09 · by dana/);
  assert.ok(audit.querySelector('.bi-clipboard-check'));
  assert.match(quiz.textContent, /Due 2026-11-09 · by Keen/);
  assert.ok(quiz.querySelector('.bi-patch-question'));
  // Read notifications lose the New badge; a missing date leaves only who acted.
  assert.equal(minutes.querySelector('.badge'), null);
  assert.equal(minutes.querySelector('.flex-grow-1 > .small-muted').textContent, 'by erin');
  assert.ok(minutes.querySelector('.bi-journal-text'));
  assert.ok(measure.querySelector('.bi-graph-down-arrow'));
  assert.match(measure.textContent, /Recorded 2026-10-29 · by Keen/);
  assert.ok(other.querySelector('.bi-bell'));
});

test('opening an unread notification marks it read before going to its record', async (t) => {
  const { ui, state } = await inbox(t);
  rows(ui)[0].click();
  await settle(() => state.navigations.length === 1);
  assert.deepEqual(state.navigations, ['/audit.html?id=a1']);
  assert.ok(ui.calls.some((call) => call.url === '/api/v1/me/inbox/n1/read'));
  assert.equal(state.bell, 1);

  // A read notification opens as an ordinary link, without marking.
  const before = ui.calls.length;
  const read = rows(ui)[2];
  const event = new ui.window.MouseEvent('click', { bubbles: true, cancelable: true });
  read.dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
  assert.equal(ui.calls.length, before);
});

test('a notification opened in a new tab is marked and stays listed', async (t) => {
  const { ui, state } = await inbox(t);
  const quiz = rows(ui)[1];
  const event = new ui.window.MouseEvent('click', {
    bubbles: true,
    cancelable: true,
    ctrlKey: true,
  });
  quiz.dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
  await settle(() => ui.el('inbox-meta').textContent === '1 unread notification');
  assert.equal(rows(ui)[1].querySelector('.badge'), null);
  assert.deepEqual(state.navigations, []);

  // A middle click opens a new tab too; other buttons are left alone.
  const right = new ui.window.MouseEvent('auxclick', { bubbles: true, button: 2 });
  rows(ui)[0].dispatchEvent(right);
  const middle = new ui.window.MouseEvent('auxclick', { bubbles: true, button: 1 });
  rows(ui)[0].dispatchEvent(middle);
  await settle(() => ui.el('inbox-meta').textContent === 'Everything is read.');
  assert.equal(ui.el('inbox-read-all').disabled, true);
  assert.equal(
    ui.calls.filter((call) => call.method === 'POST').length,
    2,
    'each notification is marked once',
  );
});

test('a failed mark still opens the record', async (t) => {
  const { ui, state } = await inbox(t, { failRead: true });
  rows(ui)[1].click();
  await settle(() => state.navigations.length === 1);
  assert.deepEqual(state.navigations, ['/quiz-take.html?round=r1']);
  assert.equal(state.bell, 0);
});

test('mark all as read clears every notification without opening any', async (t) => {
  const { ui, state } = await inbox(t);
  ui.el('inbox-read-all').click();
  await settle(() => ui.el('inbox-meta').textContent === 'Everything is read.');
  assert.ok(ui.calls.some((call) => call.url === '/api/v1/me/inbox/read-all'));
  assert.equal(ui.el('inbox-read-all').disabled, true);
  assert.equal(ui.el('inbox-list').querySelector('.badge'), null);
  assert.equal(state.bell, 1);
  assert.deepEqual(state.navigations, []);

  // Unread only now shows that nothing is left.
  ui.el('inbox-unread').checked = true;
  ui.el('inbox-unread').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('inbox-list').textContent === 'No unread notifications.');
  assert.ok(ui.calls.some((call) => call.url === '/api/v1/me/inbox?unread=true'));
  ui.el('inbox-unread').checked = false;
  ui.el('inbox-unread').dispatchEvent(new ui.window.Event('change'));
  await settle(() => rows(ui).length === 5);
});

test('an inbox that cannot be read or marked says so', async (t) => {
  const { ui } = await inbox(t, { failAll: true });
  ui.el('inbox-read-all').click();
  await settle(() => !ui.el('inbox-status').hidden);
  assert.equal(
    ui.el('inbox-status').textContent,
    'Notifications could not be marked as read: Inbox offline',
  );
  assert.equal(ui.el('inbox-status').getAttribute('role'), 'alert');
  assert.equal(ui.el('inbox-read-all').disabled, false);

  const offline = await inbox(t, { fail: hostile });
  assert.equal(offline.ui.el('inbox-meta').textContent, 'Notifications are unavailable.');
  assert.equal(offline.ui.el('inbox-list').textContent, 'No notifications yet.');
  assert.match(offline.ui.el('inbox-status').textContent, /could not be loaded: <img/);
  assert.equal(offline.ui.el('inbox-status').querySelector('img'), null);
  assert.equal(offline.ui.el('inbox-read-all').disabled, true);
});
