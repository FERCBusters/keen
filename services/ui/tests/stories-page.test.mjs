// The story register: filters that follow the URL, paging and starting a story.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const STORIES = [
  {
    id: 's1',
    title: 'Payroll <export>',
    summary: 'Root sign-in then export',
    status: 'open',
    item_count: 4,
    updated_at: '2026-05-02T12:00:00',
    created_by: 'alice',
    can_edit: true,
  },
  {
    id: 's2',
    title: 'Phishing wave',
    summary: '',
    status: 'closed',
    item_count: 0,
    updated_at: '2026-04-02T12:00:00',
    created_by: 'bob',
    can_edit: false,
  },
];

async function register(t, { me = { can_view_stories: true }, query = '', post, globals } = {}) {
  const requests = [];
  const ui = await page(t, 'stories', {
    me,
    query,
    globals,
    get: async (url) => {
      requests.push(url.search);
      const status = url.searchParams.get('status');
      const q = url.searchParams.get('q') || '';
      const items = STORIES.filter(
        (s) => (!status || s.status === status) && s.title.toLowerCase().includes(q.toLowerCase()),
      );
      return {
        items,
        total: items.length,
        limit: 50,
        offset: Number(url.searchParams.get('offset')),
      };
    },
    post,
  });
  await settle(() => ui.el('meta').textContent !== 'Loading');
  const rows = () =>
    [...ui.document.querySelectorAll('#rows [data-story-id]')].map((tr) => [
      tr.dataset.storyId,
      tr.querySelector('a').textContent,
      tr.querySelector('a').getAttribute('href'),
      tr.querySelector('.badge').textContent,
    ]);
  return { ui, requests, rows };
}

test('stories list with their status and open with the framework', async (t) => {
  const { ui, requests, rows } = await register(t);
  assert.deepEqual(requests, ['?limit=50&offset=0']);
  assert.deepEqual(rows(), [
    ['s1', 'Payroll <export>', '/story.html?id=s1&framework=A', 'open'],
    ['s2', 'Phishing wave', '/story.html?id=s2&framework=A', 'closed'],
  ]);
  assert.equal(ui.el('meta').textContent, '1-2 of 2');
  assert.equal(ui.el('showCreate').hidden, true);
  assert.equal(
    ui.document.querySelector('#rows tr:first-child .small-muted').textContent,
    'Root sign-in then export',
  );
});

test('filters are read from and written to the URL', async (t) => {
  const { ui, requests, rows } = await register(t, { query: '&status=closed&q=phish' });
  assert.deepEqual(requests, ['?q=phish&status=closed&limit=50&offset=0']);
  assert.deepEqual(ui.el('statusFilter').value, 'closed');
  assert.deepEqual(
    rows().map((r) => r[0]),
    ['s2'],
  );
  ui.el('q').value = 'payroll';
  ui.el('statusFilter').value = '';
  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => requests.length === 2);
  assert.equal(requests[1], '?q=payroll&limit=50&offset=0');
  assert.equal(new URL(ui.window.location.href).search, '?framework=A&q=payroll');
  await settle(() => rows().length === 1 && rows()[0][0] === 's1');
  ui.el('reset').click();
  await settle(() => requests.length === 3);
  assert.equal(new URL(ui.window.location.href).search, '?framework=A');
  assert.equal(ui.el('q').value, '');
});

test('an unknown status in the address is ignored', async (t) => {
  const { ui, requests } = await register(t, { query: '&status=archived' });
  assert.deepEqual(requests, ['?limit=50&offset=0']);
  assert.equal(ui.el('statusFilter').value, '');
});

test('an author starts a story and lands on its page', async (t) => {
  const assigned = [];
  const posted = [];
  const { ui } = await register(t, {
    me: { can_manage_stories: true },
    globals: {
      location: {
        origin: 'https://keen.example',
        href: 'https://keen.example/stories.html?framework=A',
        search: '?framework=A',
        assign: (u) => assigned.push(u),
      },
    },
    post: async (url, body) => {
      posted.push([url.pathname + url.search, body]);
      return { id: 'new' };
    },
  });
  assert.equal(ui.el('showCreate').hidden, false);
  assert.equal(ui.el('createCard').hidden, true);
  ui.el('showCreate').click();
  assert.equal(ui.el('createCard').hidden, false);
  ui.el('createTitle').value = '  Incident 42 ';
  ui.el('createSummary').value = 'What we know';
  ui.el('createForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => assigned.length === 1);
  assert.deepEqual(JSON.parse(JSON.stringify(posted)), [
    ['/api/v1/stories?framework=A', { title: 'Incident 42', summary: 'What we know' }],
  ]);
  assert.deepEqual(assigned, ['/story.html?id=new&framework=A']);
  ui.el('cancelCreate').click();
  assert.equal(ui.el('createCard').hidden, true);
});

test('paging moves through the register and is written to the address', async (t) => {
  const requests = [];
  const ui = await page(t, 'stories', {
    me: { can_view_stories: true },
    get: async (url) => {
      requests.push(url.search);
      const offset = Number(url.searchParams.get('offset'));
      return {
        items: [{ ...STORIES[0], id: 's' + offset, title: 'Story ' + offset }],
        total: 120,
        limit: 50,
        offset,
      };
    },
  });
  await settle(() => ui.el('meta').textContent === '1-50 of 120');
  assert.equal(ui.el('prev').disabled, true);
  assert.equal(ui.el('next').disabled, false);

  ui.el('next').click();
  await settle(() => ui.el('meta').textContent === '51-100 of 120');
  assert.equal(new URL(ui.window.location.href).searchParams.get('offset'), '50');
  assert.equal(ui.el('prev').disabled, false);
  ui.el('next').click();
  await settle(() => ui.el('meta').textContent === '101-120 of 120');
  assert.equal(ui.el('next').disabled, true);
  ui.el('prev').click();
  await settle(() => ui.el('meta').textContent === '51-100 of 120');
  assert.deepEqual(requests, [
    '?limit=50&offset=0',
    '?limit=50&offset=50',
    '?limit=50&offset=100',
    '?limit=50&offset=50',
  ]);
  assert.match(ui.document.querySelector('#rows a').textContent, /Story 50/);
});

test('a register that cannot load says so, and a refused story is reported', async (t) => {
  const ui = await page(t, 'stories', {
    me: { can_manage_stories: true },
    get: async () => {
      throw new Error('Stories offline');
    },
    post: async () => {
      throw new Error('Title in use');
    },
  });
  await settle(() => ui.el('meta').textContent === '-');
  assert.equal(ui.el('status').textContent, 'Failed to load stories: Stories offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');

  ui.el('showCreate').click();
  ui.el('createTitle').value = 'Incident 42';
  ui.el('createForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Failed to create the story: Title in use');
  assert.equal(ui.el('createTitle').value, 'Incident 42');
  assert.equal(ui.el('createCard').hidden, false);
});
