// A story's page for sparse stories, unnamed authors and actions the author
// declines or that miss their buttons, with responses shaped as
// services/api/app/services/stories.py sends them.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const note = {
  id: 'i-note',
  kind: 'note',
  kind_label: 'Note',
  position: 0,
  x: 40,
  y: 40,
  entity_id: null,
  label: 'Containment',
  note: 'Keys rotated',
  occurred_at: null,
  title: 'Containment',
  url: null,
  withheld: false,
};

function story(overrides = {}) {
  return {
    id: 's1',
    title: 'Payroll export',
    summary: '',
    status: 'open',
    version: 3,
    created_by_user_id: null,
    // The author's account has been deleted.
    created_by: null,
    created_at: '2026-05-01T12:00:00',
    updated_at: '2026-05-02T12:00:00',
    item_count: 1,
    framework: 'A',
    can_edit: true,
    items: [note],
    links: [],
    controls: [],
    comment_count: 0,
    ...overrides,
  };
}

async function open(
  t,
  { query = '&id=s1', detail = story(), comments = [], confirm = true, get } = {},
) {
  const requests = [];
  const ui = await page(t, 'story', {
    query,
    me: { user_id: 'u1', is_admin: true },
    confirm,
    get: async (url) => {
      if (get) {
        const answer = await get(url);
        if (answer !== undefined) return answer;
      }
      if (url.pathname === '/api/v1/stories/s1') return structuredClone(detail);
      if (url.pathname === '/api/v1/stories/s1/comments') return { items: comments };
      if (url.pathname === '/api/v1/stories/s1/changelog') return { items: [] };
      if (url.pathname === '/api/v1/questions/entities/story/s1') return { threads: [] };
      throw new Error('Unexpected GET ' + url);
    },
    patch: async (url, body) => {
      requests.push(['PATCH', url.pathname, body]);
      return structuredClone({ ...detail, version: detail.version + 1, title: body.title });
    },
    post: async (url, body) => {
      requests.push(['POST', url.pathname, body]);
      return structuredClone(detail);
    },
    remove: async (url) => {
      requests.push(['DELETE', url.pathname]);
      return structuredClone(detail);
    },
  });
  await settle(() => ui.el('storyTitle').textContent !== 'Loading');
  await settle(() => ui.idle());
  return { ui, requests };
}

const pause = () => new Promise((r) => setTimeout(r, 20));

test('a page opened without a story named says so and asks for nothing', async (t) => {
  const { ui } = await open(t, { query: '' });
  assert.equal(ui.el('storyTitle').textContent, 'Story unavailable');
  assert.equal(ui.el('status').textContent, 'No story was named.');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.deepEqual(
    ui.calls.filter((c) => c.url.startsWith('/api/v1/') && !c.url.startsWith('/api/v1/frameworks')),
    [],
  );
});

test('a one-item story by a deleted account reads in the singular with an unknown author', async (t) => {
  const { ui } = await open(t);
  assert.equal(
    ui.el('storyMeta').textContent,
    '1 item · created by unknown · updated 2026-05-02T12:00:00',
  );
  assert.equal(ui.el('storySummary').hidden, true);
  assert.equal(ui.el('commentCount').textContent, '');
  // Saving the details redraws the page with the story's own comment count.
  ui.el('edit').click();
  assert.equal(ui.el('summary').value, '');
  ui.el('title').value = 'Payroll export (closed)';
  ui.el('storyForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Story saved.');
  assert.equal(ui.el('storyTitle').textContent, 'Payroll export (closed)');
  assert.equal(ui.el('commentCount').textContent, '');
});

test('a story with no items yet points to where items come from', async (t) => {
  const { ui } = await open(t, { detail: story({ items: [], item_count: 0 }) });
  assert.equal(
    ui.el('timeline').textContent.trim(),
    'No items yet. Add events from the Events screen or records below.',
  );
  assert.match(ui.el('storyMeta').textContent, /^0 items ·/);
});

test('declining to delete the story keeps it', async (t) => {
  const { ui, requests } = await open(t, { confirm: false });
  ui.el('deleteStory').click();
  await settle(() => ui.confirmations.length === 1);
  await pause();
  assert.deepEqual(requests, []);
  assert.equal(ui.window.location.pathname, '/story.html');
});

test('event search names untitled events, says when nothing is new and ignores clicks beside Add', async (t) => {
  let events = [{ id: 'e1', summary: '', timestamp: '2026-05-01T09:30:00' }];
  const { ui, requests } = await open(t, {
    get: async (url) =>
      url.pathname === '/api/v1/events' ? { items: events, total: events.length } : undefined,
  });
  ui.el('addSearch').value = 'root';
  ui.el('findItems').click();
  await settle(() => ui.el('candidates').querySelector('[data-add-id]'));
  const candidate = ui.el('candidates').querySelector('li');
  assert.match(candidate.textContent, /Event\s*\(no summary\)/);
  // A click on the candidate's text adds nothing.
  candidate.querySelector('.text-break').click();
  await pause();
  assert.deepEqual(requests, []);
  assert.equal(ui.el('status').textContent, '');
  assert.ok(ui.el('candidates').querySelector('[data-add-id="e1"]'));

  events = [];
  ui.el('findItems').click();
  await settle(() => /Nothing new matches/.test(ui.el('candidates').textContent));
  assert.equal(ui.el('candidates').querySelector('[data-add-id]'), null);
});

test('declining to remove an item keeps it, and clicks elsewhere on the timeline do nothing', async (t) => {
  const { ui, requests } = await open(t, { confirm: false });
  ui.el('timeline').querySelector('.story-note').click();
  await pause();
  assert.deepEqual(ui.confirmations, []);
  ui.el('timeline').querySelector('[data-remove-item="i-note"]').click();
  await settle(() => ui.confirmations.length === 1);
  await pause();
  assert.equal(ui.confirmations[0].title, 'Remove item from story');
  assert.deepEqual(requests, []);
  assert.ok(ui.el('timeline').querySelector('[data-item-id="i-note"]'));
});

test('comments by deleted accounts are unattributed, and declining or missing Delete keeps them', async (t) => {
  const { ui, requests } = await open(t, {
    confirm: false,
    comments: [
      {
        id: 'k1',
        body: 'Rotated <b>keys</b>',
        created_by_user_id: null,
        created_by: null,
        created_at: '2026-05-01T13:00:00',
        can_delete: true,
      },
    ],
  });
  await settle(() => ui.el('comments').querySelector('[data-comment-id]'));
  const comment = ui.el('comments').querySelector('[data-comment-id="k1"]');
  assert.match(comment.querySelector('.small-muted').textContent, /^unknown · 2026-05-01T13:00:00/);
  assert.equal(comment.querySelector('b'), null);
  comment.querySelector('.story-note').click();
  await pause();
  assert.deepEqual(ui.confirmations, []);
  comment.querySelector('[data-delete-comment]').click();
  await settle(() => ui.confirmations.length === 1);
  await pause();
  assert.equal(ui.confirmations[0].title, 'Delete comment');
  assert.deepEqual(requests, []);
  assert.ok(ui.el('comments').querySelector('[data-comment-id="k1"]'));
});
