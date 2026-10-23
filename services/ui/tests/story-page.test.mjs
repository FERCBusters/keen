// One story's page: the timeline, the flow chart layout, comments, questions
// and the actions an author has that a reader does not.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));

function story(overrides = {}) {
  return {
    id: 's1',
    title: 'Payroll <export>',
    summary: 'Root sign-in\nthen export',
    status: 'open',
    version: 3,
    created_by: 'alice',
    created_at: '2026-05-01T12:00:00',
    updated_at: '2026-05-02T12:00:00',
    item_count: 4,
    comment_count: 2,
    can_edit: false,
    framework: 'A',
    controls: [{ id: 'c1', ref: 'A.5.15', title: 'Access' }],
    items: [
      {
        id: 'i-note',
        kind: 'note',
        kind_label: 'Note',
        position: 0,
        x: 40,
        y: 40,
        entity_id: null,
        label: 'Containment',
        note: 'Keys <rotated>',
        occurred_at: null,
        title: 'Containment',
        url: null,
        withheld: false,
      },
      {
        id: 'i-export',
        kind: 'event',
        kind_label: 'Event',
        position: 1,
        x: 280,
        y: 40,
        entity_id: 'e2',
        label: '',
        note: '',
        occurred_at: '2026-05-01T09:45:00',
        title: 'Bulk export started',
        url: '/event.html?id=e2',
        source: 'cloud-logs',
        controls: [{ id: 'c1', ref: 'A.5.15', title: 'Access' }],
        withheld: false,
      },
      {
        id: 'i-login',
        kind: 'event',
        kind_label: 'Event',
        position: 2,
        x: 520,
        y: 40,
        entity_id: 'e1',
        label: 'Attacker signs in',
        note: '',
        occurred_at: '2026-05-01T09:30:00',
        title: 'Root account sign-in',
        url: '/event.html?id=e1',
        source: 'cloud-logs',
        controls: [],
        withheld: false,
      },
      {
        id: 'i-risk',
        kind: 'risk',
        kind_label: 'Risk',
        position: 3,
        x: 760,
        y: 40,
        entity_id: null,
        label: '',
        note: '',
        occurred_at: null,
        title: 'Risk you cannot view',
        url: null,
        withheld: true,
      },
    ],
    links: [{ id: 'l1', from_item_id: 'i-login', to_item_id: 'i-export', label: 'then' }],
    ...overrides,
  };
}

async function open(
  t,
  {
    me,
    detail = story(),
    comments = [],
    threads = [],
    changelog = [],
    post,
    patch,
    put,
    remove,
    globals,
    get,
  } = {},
) {
  const ui = await page(t, 'story', {
    query: '&id=s1',
    me: { user_id: 'u1', can_view_stories: true, can_create_questions: true, ...me },
    globals,
    get:
      get ||
      (async (url) => {
        if (url.pathname === '/api/v1/stories/s1') return detail;
        if (url.pathname === '/api/v1/stories/s1/comments') return { items: comments };
        if (url.pathname === '/api/v1/stories/s1/changelog') return { items: changelog };
        if (url.pathname === '/api/v1/questions/entities/story/s1') return { threads };
        throw new Error('Unexpected GET ' + url);
      }),
    post,
    patch,
    put,
    remove,
  });
  await settle(() => ui.el('storyTitle').textContent !== 'Loading');
  return ui;
}

const timeline = (ui) =>
  [...ui.document.querySelectorAll('#timeline [data-item-id]')].map((li) => [
    li.dataset.itemId,
    li.querySelector('.story-timeline-time').textContent.trim(),
  ]);

test('a reader sees the timeline in time order with withheld items marked', async (t) => {
  const ui = await open(t, {
    comments: [
      {
        id: 'k1',
        body: 'Looks <complete>',
        created_by: 'bob',
        created_at: '2026-05-03T10:00:00',
        can_delete: false,
      },
    ],
  });
  assert.equal(ui.el('storyTitle').textContent, 'Payroll <export>');
  assert.equal(ui.document.title, 'Keen - Payroll <export>');
  assert.equal(ui.el('storySummary').textContent, 'Root sign-in\nthen export');
  assert.equal(
    ui.el('storyMeta').textContent,
    '4 items · created by alice · updated 2026-05-02T12:00:00',
  );
  assert.deepEqual(timeline(ui), [
    ['i-login', '2026-05-01T09:30:00'],
    ['i-export', '2026-05-01T09:45:00'],
    ['i-note', 'no time'],
    ['i-risk', 'no time'],
  ]);
  const login = ui.document.querySelector('[data-item-id="i-login"]');
  assert.equal(login.querySelector('a').getAttribute('href'), '/event.html?id=e1&framework=A');
  assert.equal(login.querySelector('.fw-semibold').textContent, 'Attacker signs in');
  const note = ui.document.querySelector('[data-item-id="i-note"] .story-note');
  assert.equal(note.textContent, 'Keys <rotated>');
  // A note's title is its label, so the heading shows it once.
  const noteHeading = ui.document.querySelector('[data-item-id="i-note"]');
  assert.equal(noteHeading.querySelectorAll('.fw-semibold').length, 1);
  assert.equal(noteHeading.textContent.includes(' · '), false);
  const withheld = ui.document.querySelector('[data-item-id="i-risk"]');
  assert.equal(withheld.classList.contains('story-timeline-withheld'), true);
  assert.equal(
    withheld.querySelector('.story-timeline-body .fst-italic').textContent,
    'Risk you cannot view',
  );
  assert.equal(withheld.querySelector('a'), null);
  assert.deepEqual(
    [...ui.el('controlsList').querySelectorAll('a')].map((a) => [
      a.textContent,
      a.getAttribute('href'),
    ]),
    [['A.5.15', '/control.html?id=c1&framework=A']],
  );
  // Readers comment and ask; they neither edit nor arrange nor sample.
  for (const id of ['edit', 'deleteStory', 'sample', 'addItems', 'flowToolbar'])
    assert.equal(ui.el(id).hidden, true, id);
  assert.equal(ui.el('commentForm').hidden, false);
  assert.equal(ui.el('questionForm').hidden, false);
  assert.equal(ui.document.querySelector('#timeline [data-edit-item]'), null);
  assert.equal(ui.el('commentCount').textContent, '1');
  assert.equal(ui.el('comments').querySelector('.story-note').textContent, 'Looks <complete>');
  assert.equal(ui.el('comments').querySelector('[data-delete-comment]'), null);
  assert.equal(ui.document.querySelectorAll('#flow [data-item-id]').length, 4);
  assert.equal(
    ui.document.querySelector('#flow [data-link-from="i-login"] text').textContent,
    'then',
  );
});

test('an author edits the story, adds notes and records, and saves the layout', async (t) => {
  const calls = { post: [], patch: [], put: [] };
  const detail = story({ can_edit: true });
  const ui = await open(t, {
    me: {
      can_manage_stories: true,
      can_manage_audits: true,
      features: { enabled: { sampling: true } },
    },
    detail,
    get: async (url) => {
      if (url.pathname === '/api/v1/stories/s1') return detail;
      if (url.pathname === '/api/v1/stories/s1/comments') return { items: [] };
      if (url.pathname === '/api/v1/stories/s1/changelog') return { items: [] };
      if (url.pathname === '/api/v1/questions/entities/story/s1') return { threads: [] };
      if (url.pathname === '/api/v1/events')
        return {
          items: [
            { id: 'e9', summary: 'Alert <raised>', timestamp: '2026-05-01T09:50:00' },
            { id: 'e2', summary: 'Bulk export started', timestamp: '2026-05-01T09:45:00' },
          ],
        };
      if (url.pathname === '/api/v1/stories/choices/document')
        return {
          items: [{ target_id: 'd1', title: 'Access policy', url: '/isms-document.html?id=d1' }],
        };
      throw new Error('Unexpected GET ' + url);
    },
    post: async (url, body) => {
      calls.post.push([url.pathname + url.search, body]);
      return { ...detail, version: detail.version + 1, title: 'Saved' };
    },
    patch: async (url, body) => {
      calls.patch.push([url.pathname + url.search, body]);
      return { ...detail, version: 4, title: 'Renamed', status: 'closed' };
    },
    put: async (url, body) => {
      calls.put.push([url.pathname + url.search, body]);
      return { ...detail, version: 5 };
    },
  });
  for (const id of ['edit', 'deleteStory', 'sample', 'addItems', 'flowToolbar'])
    assert.equal(ui.el(id).hidden, false, id);

  // Story details.
  ui.el('edit').click();
  assert.equal(ui.el('storyForm').hidden, false);
  assert.deepEqual(
    [ui.el('title').value, ui.el('statusSelect').value],
    ['Payroll <export>', 'open'],
  );
  ui.el('title').value = ' Renamed ';
  ui.el('statusSelect').value = 'closed';
  ui.el('storyForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('storyTitle').textContent === 'Renamed');
  assert.deepEqual(plain(calls.patch), [
    [
      '/api/v1/stories/s1?framework=A',
      {
        title: 'Renamed',
        summary: 'Root sign-in\nthen export',
        status: 'closed',
        expected_version: 3,
      },
    ],
  ]);
  assert.equal(ui.el('storyStatus').textContent, 'closed');
  assert.equal(ui.el('storyForm').hidden, true);

  // A note.
  ui.el('addKind').value = 'note';
  ui.el('addKind').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('searchField').hidden, true);
  assert.equal(ui.el('noteTextField').hidden, false);
  ui.el('noteLabel').value = 'Decision';
  ui.el('noteText').value = 'Rotate keys';
  ui.el('addNote').click();
  await settle(() => calls.post.length === 1);
  assert.deepEqual(plain(calls.post[0]), [
    '/api/v1/stories/s1/items?framework=A',
    { items: [{ kind: 'note', label: 'Decision', note: 'Rotate keys' }], expected_version: 4 },
  ]);
  await settle(() => ui.el('noteText').value === '');

  // Events found by search; those already in the story are left out.
  ui.el('addKind').value = 'event';
  ui.el('addKind').dispatchEvent(new ui.window.Event('change'));
  ui.el('addSearch').value = 'alert';
  ui.el('findItems').click();
  await settle(() => ui.el('candidates').querySelector('[data-add-id]'));
  assert.deepEqual(
    [...ui.el('candidates').querySelectorAll('[data-add-id]')].map((b) => b.dataset.addId),
    ['e9'],
  );
  assert.equal(ui.el('candidates').textContent.includes('Alert <raised>'), true);
  ui.el('candidates').querySelector('[data-add-id="e9"]').click();
  await settle(() => calls.post.length === 2);
  assert.deepEqual(plain(calls.post[1][1]), {
    items: [{ kind: 'event', entity_id: 'e9' }],
    expected_version: 4,
  });

  // Records through the choices endpoint.
  ui.el('addKind').value = 'document';
  ui.el('addSearch').value = 'acc';
  ui.el('addSearch').dispatchEvent(
    new ui.window.KeyboardEvent('keydown', { key: 'Enter', cancelable: true }),
  );
  await settle(() => ui.el('candidates').querySelector('[data-add-id="d1"]'));
  assert.equal(ui.el('candidates').querySelector('[data-add-id="d1"]').dataset.addKind, 'document');

  // Layout: drag a box, link two boxes, save.
  const flowNode = (id) => ui.document.querySelector(`#flow [data-item-id="${id}"]`);
  const pointer = (type, target, x, y) =>
    target.dispatchEvent(
      new ui.window.MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 }),
    );
  assert.equal(ui.el('saveLayout').disabled, true);
  // Arrange by time lays the boxes out in timeline order, not insertion order.
  ui.el('arrange').click();
  assert.deepEqual(
    ['i-login', 'i-export', 'i-note', 'i-risk'].map((id) => flowNode(id).style.left),
    ['40px', '280px', '520px', '760px'],
  );
  pointer('pointerdown', flowNode('i-note'), 0, 0);
  pointer('pointermove', ui.el('flow'), 15, 25);
  pointer('pointerup', ui.el('flow'), 15, 25);
  assert.equal(ui.el('saveLayout').disabled, false);
  ui.el('linkMode').click();
  assert.equal(ui.el('linkMode').getAttribute('aria-pressed'), 'true');
  pointer('pointerdown', flowNode('i-export'), 0, 0);
  pointer('pointerdown', flowNode('i-note'), 0, 0);
  assert.equal(ui.el('linkEditor').hidden, false);
  ui.el('linkLabel').value = 'so';
  ui.el('linkLabel').dispatchEvent(new ui.window.Event('input'));
  ui.el('saveLayout').click();
  await settle(() => calls.put.length === 1);
  const [path, layout] = plain(calls.put[0]);
  assert.equal(path, '/api/v1/stories/s1/layout?framework=A');
  assert.equal(layout.expected_version, 4);
  assert.deepEqual(
    layout.positions.find((p) => p.item_id === 'i-note'),
    { item_id: 'i-note', x: 535, y: 65 },
  );
  assert.deepEqual(layout.links, [
    { from_item_id: 'i-login', to_item_id: 'i-export', label: 'then' },
    { from_item_id: 'i-export', to_item_id: 'i-note', label: 'so' },
  ]);
  await settle(() => ui.el('saveLayout').disabled);
});

test('item edits, removals, comments and questions carry the current version', async (t) => {
  const calls = { post: [], patch: [], remove: [] };
  const detail = story({ can_edit: true });
  const ui = await open(t, {
    me: { can_manage_stories: true, can_admin_stories: true },
    detail,
    comments: [
      {
        id: 'k1',
        body: 'Mine',
        created_by: 'me',
        created_at: '2026-05-03T10:00:00',
        can_delete: true,
      },
    ],
    post: async (url, body) => {
      calls.post.push([url.pathname, body]);
      return {};
    },
    patch: async (url, body) => {
      calls.patch.push([url.pathname + url.search, body]);
      return { ...detail, version: 4 };
    },
    remove: async (url) => {
      calls.remove.push(url.pathname + url.search);
      return { ...detail, version: 4, items: detail.items.slice(1) };
    },
  });
  const login = ui.document.querySelector('[data-item-id="i-login"]');
  login.querySelector('[data-edit-item]').click();
  const form = login.querySelector('[data-item-form]');
  assert.equal(form.hidden, false);
  assert.deepEqual(
    [form.elements.label.value, form.elements.occurred_at.value],
    ['Attacker signs in', '2026-05-01T09:30'],
  );
  form.elements.note.value = 'First sign of trouble';
  form.elements.clear_occurred_at.checked = true;
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await settle(() => calls.patch.length === 1);
  assert.deepEqual(plain(calls.patch[0]), [
    '/api/v1/stories/s1/items/i-login?framework=A',
    {
      label: 'Attacker signs in',
      note: 'First sign of trouble',
      expected_version: 3,
      clear_occurred_at: true,
    },
  ]);
  // The saved story re-renders the timeline with its new version.
  await settle(() => ui.document.querySelector('[data-item-id="i-login"] [data-item-form]').hidden);
  const exportItem = ui.document.querySelector('[data-item-id="i-export"]');
  exportItem.querySelector('[data-edit-item]').click();
  const exportForm = exportItem.querySelector('[data-item-form]');
  exportForm.elements.occurred_at.value = '2026-05-01T11:00';
  exportForm.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await settle(() => calls.patch.length === 2);
  // The author's local time is sent as an instant.
  assert.equal(plain(calls.patch[1][1]).occurred_at, new Date('2026-05-01T11:00').toISOString());
  assert.equal(plain(calls.patch[1][1]).expected_version, 4);

  ui.document.querySelector('[data-item-id="i-note"] [data-remove-item]').click();
  await settle(() => calls.remove.length === 1);
  assert.deepEqual(calls.remove, [
    '/api/v1/stories/s1/items/i-note?expected_version=4&framework=A',
  ]);
  await settle(() => !ui.document.querySelector('#timeline [data-item-id="i-note"]'));

  ui.el('commentBody').value = 'Agreed';
  ui.el('commentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => calls.post.length === 1);
  assert.deepEqual(plain(calls.post[0]), ['/api/v1/stories/s1/comments', { body: 'Agreed' }]);
  ui.el('comments').querySelector('[data-delete-comment="k1"]').click();
  await settle(() => calls.remove.length === 2);
  assert.equal(calls.remove[1], '/api/v1/stories/s1/comments/k1');

  ui.el('questionBody').value = 'Why?';
  ui.el('questionForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => calls.post.length === 2);
  assert.deepEqual(plain(calls.post[1]), ['/api/v1/questions/entities/story/s1', { body: 'Why?' }]);
});

test('deleting the story returns to the register and a missing story is reported', async (t) => {
  const assigned = [];
  const location = {
    origin: 'https://keen.example',
    href: 'https://keen.example/story.html?framework=A&id=s1',
    search: '?framework=A&id=s1',
    assign: (u) => assigned.push(u),
  };
  const removed = [];
  const ui = await open(t, {
    me: { can_manage_stories: true },
    detail: story({ can_edit: true }),
    globals: { location },
    remove: async (url) => {
      removed.push(url.pathname + url.search);
      return { ok: true };
    },
  });
  ui.el('deleteStory').click();
  await settle(() => assigned.length === 1);
  assert.deepEqual(removed, ['/api/v1/stories/s1?expected_version=3']);
  assert.deepEqual(assigned, ['/stories.html?framework=A']);

  const gone = await page(t, 'story', {
    query: '&id=missing',
    me: { can_view_stories: true },
    get: async () => {
      throw new Error('404 Story not found');
    },
  });
  await settle(() => gone.el('storyTitle').textContent === 'Story unavailable');
  assert.equal(gone.el('status').textContent, '404 Story not found');
});

test('question threads show replies the author may send, and failures are reported', async (t) => {
  const posted = [];
  const ui = await open(t, {
    me: { user_id: 'u1', can_manage_stories: true },
    detail: story({ can_edit: true }),
    threads: [
      {
        id: 't1',
        status: 'unanswered',
        created_by_user_id: 'u1',
        posts: [{ author_username: 'me', body: 'Why <was> it allowed?' }],
      },
      { id: 't2', status: 'answered', created_by_user_id: 'u2', posts: [] },
    ],
    post: async (url, body) => {
      posted.push([url.pathname, body]);
      return {};
    },
    patch: async () => {
      throw new Error('409 This story changed. Reload before saving.');
    },
  });
  const forms = [...ui.el('questionList').querySelectorAll('[data-reply-thread]')];
  assert.deepEqual(
    forms.map((f) => f.dataset.replyThread),
    ['t1'],
  );
  assert.equal(ui.el('questionList').textContent.includes('Why <was> it allowed?'), true);
  forms[0].elements.body.value = 'Because of the change window';
  forms[0].dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await settle(() => posted.length === 1);
  assert.deepEqual(plain(posted[0]), [
    '/api/v1/questions/t1/posts',
    { body: 'Because of the change window' },
  ]);

  // A failed save reports the server's reason and keeps the form open.
  ui.el('edit').click();
  ui.el('storyForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent.includes('Reload before saving'));
  assert.equal(ui.el('storyForm').hidden, false);
  // An item editor opens and closes without a request.
  const login = ui.document.querySelector('[data-item-id="i-login"]');
  login.querySelector('[data-edit-item]').click();
  login.querySelector('[data-cancel-item]').click();
  assert.equal(login.querySelector('[data-item-form]').hidden, true);
});

test('every refused change is reported and leaves the page as it was', async (t) => {
  const refuse = async () => {
    throw new Error('409 This story changed. Reload before saving.');
  };
  const detail = story({ can_edit: true });
  const ui = await open(t, {
    me: { can_manage_stories: true, can_admin_stories: true },
    detail,
    comments: [
      {
        id: 'k1',
        body: 'Mine',
        created_by: 'me',
        created_at: '2026-05-03T10:00:00',
        can_delete: true,
      },
    ],
    globals: { confirm: () => true },
    get: async (url) => {
      if (url.pathname === '/api/v1/stories/s1') return detail;
      if (url.pathname === '/api/v1/stories/s1/comments')
        return {
          items: [
            {
              id: 'k1',
              body: 'Mine',
              created_by: 'me',
              created_at: '2026-05-03T10:00:00',
              can_delete: true,
            },
          ],
        };
      if (url.pathname === '/api/v1/stories/s1/changelog') return { items: [] };
      if (url.pathname === '/api/v1/questions/entities/story/s1') return { threads: [] };
      if (url.pathname === '/api/v1/events') throw new Error('Search index offline');
      throw new Error('Unexpected GET ' + url);
    },
    post: refuse,
    patch: refuse,
    put: refuse,
    remove: refuse,
  });
  const reported = () =>
    ui.el('status').textContent === '409 This story changed. Reload before saving.';
  const reset = () => (ui.el('status').textContent = '');

  // Cancelling an edit closes the form without a request.
  ui.el('edit').click();
  ui.el('cancelEdit').click();
  assert.equal(ui.el('storyForm').hidden, true);

  ui.el('addKind').value = 'note';
  ui.el('addKind').dispatchEvent(new ui.window.Event('change'));
  ui.el('noteLabel').value = 'Decision';
  ui.el('noteText').value = 'Rotate keys';
  ui.el('addNote').click();
  await settle(reported);
  assert.equal(ui.el('noteText').value, 'Rotate keys');
  reset();

  ui.el('addKind').value = 'event';
  ui.el('addKind').dispatchEvent(new ui.window.Event('change'));
  ui.el('addSearch').value = 'alert';
  ui.el('findItems').click();
  await settle(() => /Search failed/.test(ui.el('candidates').textContent));
  assert.equal(
    ui.el('candidates').textContent.trim(),
    'Search failed: Error: Search index offline',
  );
  // A candidate the API refuses stays in the list.
  ui.el('candidates').innerHTML =
    '<li class="list-group-item"><button type="button" data-add-kind="event" data-add-id="e9">Add</button></li>';
  ui.el('candidates').querySelector('[data-add-id="e9"]').click();
  await settle(reported);
  assert.ok(ui.el('candidates').querySelector('[data-add-id="e9"]'));
  reset();

  const login = ui.document.querySelector('[data-item-id="i-login"]');
  login.querySelector('[data-edit-item]').click();
  const form = login.querySelector('[data-item-form]');
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await settle(reported);
  assert.equal(form.hidden, false);
  reset();
  login.querySelector('[data-remove-item]').click();
  await settle(reported);
  assert.ok(ui.document.querySelector('#timeline [data-item-id="i-login"]'));
  reset();

  ui.el('commentBody').value = 'Agreed';
  ui.el('commentForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(reported);
  assert.equal(ui.el('commentBody').value, 'Agreed');
  reset();
  ui.el('comments').querySelector('[data-delete-comment="k1"]').click();
  await settle(reported);
  assert.ok(ui.el('comments').querySelector('[data-delete-comment="k1"]'));
  reset();

  ui.el('questionBody').value = 'Why?';
  ui.el('questionForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(reported);
  assert.equal(ui.el('questionBody').value, 'Why?');
  reset();

  ui.el('saveLayout').disabled = false;
  ui.el('saveLayout').click();
  await settle(reported);
  reset();

  ui.el('deleteStory').click();
  await settle(reported);
  assert.equal(ui.el('storyTitle').textContent, 'Payroll <export>');
});

test('an author samples the story into an audit with a link back to it', async (t) => {
  const posted = [];
  const ui = await open(t, {
    me: {
      can_manage_stories: true,
      can_manage_audits: true,
      features: { enabled: { sampling: true } },
    },
    detail: story({ can_edit: true }),
    get: async (url) => {
      if (url.pathname === '/api/v1/stories/s1') return story({ can_edit: true });
      if (url.pathname === '/api/v1/stories/s1/comments') return { items: [] };
      if (url.pathname === '/api/v1/stories/s1/changelog') return { items: [] };
      if (url.pathname === '/api/v1/questions/entities/story/s1') return { threads: [] };
      if (url.pathname === '/api/v1/audits')
        return {
          items: [{ id: 'au1', title: 'Annual audit', status: 'open', created_by_user_id: 'u1' }],
        };
      throw new Error('Unexpected GET ' + url);
    },
    post: async (url, body) => {
      posted.push([url.pathname, plain(body)]);
      return {};
    },
  });
  ui.el('sample').click();
  await settle(() => ui.el('entityAuditSampleSelect')?.value === 'au1');
  assert.equal(ui.el('entityAuditSampleTitle').textContent, 'Sample “Payroll <export>” into audit');
  ui.el('entityAuditSampleSubmit').click();
  await settle(() => posted.length === 1);
  assert.deepEqual(posted[0], [
    '/api/v1/audits/au1/evidence',
    {
      entity_type: 'story',
      entity_id: 's1',
      evidence_url: '/story.html?id=s1&framework=A',
      title: 'Payroll <export>',
      notes: '',
    },
  ]);
});

test('a selected link can be removed, unsaved layout changes hold the page, and a tab opens from the address', async (t) => {
  const shown = [];
  const ui = await page(t, 'story', {
    query: '&id=s1&tab=comments',
    me: { user_id: 'u1', can_view_stories: true, can_manage_stories: true },
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
    get: async (url) => {
      if (url.pathname === '/api/v1/stories/s1') return story({ can_edit: true });
      if (url.pathname === '/api/v1/stories/s1/comments') return { items: [] };
      if (url.pathname === '/api/v1/stories/s1/changelog') return { items: [] };
      if (url.pathname === '/api/v1/questions/entities/story/s1') return { threads: [] };
      throw new Error('Unexpected GET ' + url);
    },
  });
  await settle(() => ui.el('storyTitle').textContent !== 'Loading');
  assert.deepEqual(shown, ['commentsTab']);

  // Leaving with a clean layout is allowed.
  const clean = new ui.window.Event('beforeunload', { cancelable: true });
  ui.window.dispatchEvent(clean);
  assert.equal(clean.defaultPrevented, false);

  const flowNode = (id) => ui.document.querySelector(`#flow [data-item-id="${id}"]`);
  const pointer = (type, target) =>
    target.dispatchEvent(
      new ui.window.MouseEvent(type, { bubbles: true, clientX: 0, clientY: 0, button: 0 }),
    );
  ui.el('linkMode').click();
  pointer('pointerdown', flowNode('i-export'));
  pointer('pointerdown', flowNode('i-note'));
  assert.equal(ui.el('linkEditor').hidden, false);
  assert.equal(ui.el('flow').querySelectorAll('.story-link').length, 2);
  ui.el('removeLink').click();
  assert.equal(ui.el('linkEditor').hidden, true);
  assert.equal(ui.el('flow').querySelectorAll('.story-link').length, 1);

  // A changed layout asks before the page is left.
  const dirty = new ui.window.Event('beforeunload', { cancelable: true });
  ui.window.dispatchEvent(dirty);
  assert.equal(dirty.defaultPrevented, true);
  assert.equal(ui.el('saveLayout').disabled, false);
});

test('flow chart titles open records in a new tab and authors add any kind of record', async (t) => {
  const posted = [];
  const detail = story({ can_edit: true });
  const ui = await open(t, {
    me: { can_manage_stories: true },
    detail,
    get: async (url) => {
      if (url.pathname === '/api/v1/stories/s1') return detail;
      if (url.pathname === '/api/v1/stories/s1/comments') return { items: [] };
      if (url.pathname === '/api/v1/stories/s1/changelog') return { items: [] };
      if (url.pathname === '/api/v1/questions/entities/story/s1') return { threads: [] };
      if (url.pathname === '/api/v1/stories/choices/meeting')
        return {
          items: [{ target_id: 'm1', title: 'Monthly <review>', url: '/isms-meeting.html?id=m1' }],
        };
      throw new Error('Unexpected GET ' + url);
    },
    post: async (url, body) => {
      posted.push([url.pathname, plain(body)]);
      return detail;
    },
  });
  const title = (id) =>
    ui.document.querySelector(`#flow [data-item-id="${id}"] .story-node-title a`);
  assert.equal(title('i-login').getAttribute('href'), '/event.html?id=e1&framework=A');
  assert.equal(title('i-login').target, '_blank');
  assert.equal(title('i-login').textContent, 'Attacker signs in');
  assert.equal(title('i-note'), null);
  assert.equal(title('i-risk'), null, 'a withheld item has no link');

  assert.deepEqual(
    [...ui.el('addKind').options].map((o) => [o.value, o.textContent, o.dataset.feature || '']),
    [
      ['event', 'Event', ''],
      ['risk', 'Risk', 'risk_cia'],
      ['document', 'Document', 'documents'],
      ['asset', 'Asset', 'assets'],
      ['objective', 'Objective', 'objectives'],
      ['swot', 'SWOT item', 'swot'],
      ['person', 'Person', 'people'],
      ['vendor', 'Vendor', 'vendors'],
      ['effectiveness_measure', 'Effectiveness measure', 'effectiveness'],
      ['meeting', 'Meeting minutes', 'meetings'],
      ['note', 'Note', ''],
    ],
  );
  ui.el('addKind').value = 'meeting';
  ui.el('addKind').dispatchEvent(new ui.window.Event('change'));
  ui.el('addSearch').value = 'monthly';
  ui.el('findItems').click();
  await settle(() => ui.el('candidates').querySelector('[data-add-id="m1"]'));
  const candidate = ui.el('candidates').querySelector('li');
  assert.equal(candidate.querySelector('.badge').textContent, 'Meeting minutes');
  assert.match(candidate.textContent, /Monthly <review>/);
  candidate.querySelector('[data-add-id="m1"]').click();
  await settle(() => posted.length === 1);
  assert.deepEqual(posted[0], [
    '/api/v1/stories/s1/items',
    { items: [{ kind: 'meeting', entity_id: 'm1' }], expected_version: 3 },
  ]);
});
