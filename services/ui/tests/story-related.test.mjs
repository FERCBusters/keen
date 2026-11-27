// The stories that include a record, on the record's own page: a card of its
// Related items that loads when the tab is first shown, the forms that add the
// record to a story or start one with it, and what readers, other accounts and
// failures see.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));
// The page's Related items tab and, on it, where the card's list goes.
const PAGE =
  '<main><ul class="nav nav-tabs"><li><button id="detailsTab">Details</button></li>' +
  '<li><button id="storiesRelatedTab">Related items</button></li></ul>' +
  '<div class="tab-content"><section id="detailsPane"></section>' +
  '<section id="storiesRelatedPane"></section></div></main>';
const STORIES = [
  {
    id: 's1',
    title: 'Backup <b>failure</b>',
    status: 'open',
    item_count: 1,
    updated_at: '2026-05-01',
    created_by: 'alice',
    can_edit: true,
    version: 3,
  },
  {
    id: 's2',
    title: 'Restore test',
    status: 'closed',
    item_count: 2,
    updated_at: '2026-05-02',
    created_by: null,
    can_edit: false,
    version: 1,
  },
];
const OPEN = [
  STORIES[0],
  { id: 's3', title: 'Open <i>draft</i>', status: 'open', can_edit: true, version: 7 },
  { id: 's4', title: 'Someone else', status: 'open', can_edit: false, version: 2 },
];

async function mount(
  t,
  {
    me = { is_admin: true },
    kind = 'meeting',
    id = 'm1',
    html = PAGE,
    query = '',
    related = () => ({ items: STORIES }),
    open = () => ({ items: OPEN }),
    post = async () => ({ id: 'new', title: 'New story' }),
    setup,
    options = {},
  } = {},
) {
  const calls = [];
  const answer = (handler) => async (url, body) => {
    calls.push({ url, body: body === undefined ? undefined : plain(body) });
    return handler(url, body);
  };
  const ui = await loadUi('pages/story-related.js', {
    html,
    url: `https://keen.example/isms-meeting.html?framework=A&id=m1${query}`,
    setup,
    vendor: {
      apiGet: answer(async (url) => {
        if (url.startsWith('/api/v1/stories/related/')) return related(url);
        if (url === '/api/v1/stories?status=open&limit=200') return open(url);
        throw new Error('Unexpected GET ' + url);
      }),
      apiPost: answer(post),
      withFramework: (path, fw) => {
        const u = new URL(path, 'https://keen.example');
        u.searchParams.set('framework', fw);
        return u.pathname + u.search;
      },
      getCurrentFramework: () => 'A',
      fmtTs: (value) => `on ${value}`,
    },
  });
  t.after(ui.close);
  const el = (name) => ui.document.getElementById(name);
  const mounted = ui.api.mountStoryRelated({
    me,
    kind,
    id,
    host: el('storiesRelatedPane'),
    trigger: el('storiesRelatedTab'),
    ...options,
  });
  const show = () => el('storiesRelatedTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  const submit = (selector) =>
    ui.document
      .querySelector(selector)
      .dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  return { ui, calls, mounted, el, show, submit };
}

test('the stories load when the tab they are on is first shown', async (t) => {
  const view = await mount(t);
  const { el, calls } = view;
  // The card that holds the list carries the heading; the list has none.
  assert.equal(el('storiesRelatedPane').querySelector('h2'), null);
  assert.match(el('storiesRelatedPane').textContent, /Loading stories/);
  assert.deepEqual(calls, [], 'nothing loads until the tab is shown');

  view.show();
  await settle(() => el('storyRelatedChoice').options.length === 2);
  const pane = el('storiesRelatedPane');
  assert.equal(calls[0].url, '/api/v1/stories/related/meeting/m1');
  // The tab is the page's own and keeps its name.
  assert.equal(el('storiesRelatedTab').textContent, 'Related items');
  const links = [...pane.querySelectorAll('.list-group a')].map((a) => [
    a.textContent,
    a.getAttribute('href'),
  ]);
  assert.deepEqual(links, [
    ['Backup <b>failure</b>', '/story.html?id=s1&framework=A'],
    ['Flow chart', '/story.html?id=s1&tab=flow&framework=A'],
    ['Restore test', '/story.html?id=s2&framework=A'],
    ['Flow chart', '/story.html?id=s2&tab=flow&framework=A'],
  ]);
  assert.equal(pane.querySelector('.list-group b'), null);
  const meta = [...pane.querySelectorAll('.list-group .small-muted')].map((m) => m.textContent);
  assert.deepEqual(meta, [
    '1 item · updated on 2026-05-01 · by alice',
    '2 items · updated on 2026-05-02',
  ]);
  assert.deepEqual(
    [...pane.querySelectorAll('.list-group .badge')].map((b) => [b.textContent, b.className]),
    [
      ['open', 'badge text-bg-success ms-1'],
      ['closed', 'badge text-bg-secondary ms-1'],
    ],
  );
  // Only open stories the account can edit, and not those that already include it.
  assert.deepEqual(
    [...el('storyRelatedChoice').options].map((o) => [o.value, o.textContent, o.dataset.version]),
    [
      ['', 'Choose one of your open stories', undefined],
      ['s3', 'Open <i>draft</i>', '7'],
    ],
  );

  // Showing the tab again keeps the list it has.
  view.show();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(calls.length, 2);
});

test('authors add the record to an open story or start a new one with it', async (t) => {
  let included = STORIES;
  const posted = [];
  const view = await mount(t, {
    related: () => ({ items: included }),
    post: async (url, body) => {
      posted.push([url, plain(body)]);
      if (url.endsWith('/items?framework=A')) included = [...STORIES, OPEN[1]];
      return { id: 's9', title: plain(body).title };
    },
  });
  const { el, submit } = view;
  view.show();
  await settle(() => el('storyRelatedChoice').options.length === 2);

  // Nothing is chosen and nothing is typed: nothing is sent.
  submit('[data-story-add]');
  el('storyRelatedTitle').value = '   ';
  submit('[data-story-start]');
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(posted, []);

  el('storyRelatedChoice').value = 's3';
  submit('[data-story-add]');
  await settle(() => /Added to/.test(el('storiesRelatedPane').textContent));
  assert.deepEqual(posted[0], [
    '/api/v1/stories/s3/items?framework=A',
    { items: [{ kind: 'meeting', entity_id: 'm1' }], expected_version: 7 },
  ]);
  const status = el('storiesRelatedPane').querySelector('[data-story-related-status]');
  assert.equal(status.textContent, 'Added to Open <i>draft</i>.');
  assert.equal(status.className, 'small mt-2 text-success');
  assert.equal(el('storiesRelatedPane').querySelectorAll('.list-group li').length, 3);
  assert.equal(
    el('storyRelatedChoice').options[0].textContent,
    'No other open stories you can edit',
  );

  el('storyRelatedTitle').value = '  Supplier outage  ';
  submit('[data-story-start]');
  await settle(() => /Started the story/.test(status.textContent));
  assert.deepEqual(posted[1], [
    '/api/v1/stories?framework=A',
    { title: 'Supplier outage', items: [{ kind: 'meeting', entity_id: 'm1' }] },
  ]);
  assert.equal(status.textContent, 'Started the story Supplier outage.');
  assert.equal(el('storyRelatedTitle').value, '');
});

test('failures are explained where they happen', async (t) => {
  const unavailable = await mount(t, {
    related: () => {
      throw new Error('Story <b>offline</b>');
    },
  });
  unavailable.show();
  await settle(() => /unavailable/.test(unavailable.el('storiesRelatedPane').textContent));
  assert.match(
    unavailable.el('storiesRelatedPane').textContent,
    /Stories are unavailable with your current access/,
  );
  assert.equal(unavailable.calls.length, 1, 'no story choices without the list');
  assert.equal(unavailable.el('storiesRelatedTab').textContent, 'Related items');

  let failOpen = true;
  const view = await mount(t, {
    open: () => {
      if (failOpen) throw new Error('List <b>down</b>');
      return { items: OPEN };
    },
    post: async (url) => {
      throw new Error(url.includes('/items') ? 'This story changed.' : 'Title taken');
    },
  });
  const { el, submit } = view;
  view.show();
  const status = () => el('storiesRelatedPane').querySelector('[data-story-related-status]');
  await settle(() => /Could not list your stories/.test(status().textContent));
  assert.equal(status().textContent, 'Could not list your stories: List <b>down</b>');
  assert.equal(status().className, 'small mt-2 text-danger');
  assert.equal(el('storyRelatedChoice').options[0].textContent, 'Your stories could not be listed');

  failOpen = false;
  el('storyRelatedChoice').innerHTML = '<option value="s3" data-version="7">Open draft</option>';
  submit('[data-story-add]');
  await settle(() => /Could not add it/.test(status().textContent));
  assert.equal(status().textContent, 'Could not add it to the story: This story changed.');
  // The choices are listed afresh so the next attempt uses the current version.
  await settle(() => el('storyRelatedChoice').options.length === 2);

  el('storyRelatedTitle').value = 'Duplicate';
  submit('[data-story-start]');
  await settle(() => /Could not start/.test(status().textContent));
  assert.equal(status().textContent, 'Could not start the story: Title taken');
  assert.equal(el('storyRelatedTitle').value, 'Duplicate');
});

test('readers see the list alone; others see nothing', async (t) => {
  const reader = await mount(t, {
    me: { can_view_stories: true },
    related: () => ({ items: [] }),
    kind: 'swot',
  });
  reader.show();
  await settle(() => /not in a story yet/.test(reader.el('storiesRelatedPane').textContent));
  assert.match(reader.el('storiesRelatedPane').textContent, /This SWOT item is not in a story yet/);
  assert.equal(reader.ui.document.querySelector('[data-story-add], [data-story-start]'), null);
  assert.deepEqual(
    reader.calls.map((c) => c.url),
    ['/api/v1/stories/related/swot/m1'],
  );

  for (const [me, options] of [
    [{ can_view_isms: true }, {}],
    [{ is_admin: true, features: { enabled: { stories: false } } }, {}],
    [{ is_admin: true }, { kind: 'note' }],
    [{ is_admin: true }, { id: '' }],
  ]) {
    const view = await mount(t, { me, ...options });
    assert.equal(view.mounted, null);
    assert.equal(view.el('storiesRelatedPane').childNodes.length, 0);
    assert.deepEqual(view.calls, []);
  }
});

test('a list with no tab to wait for loads straight away', async (t) => {
  const loads = [];
  const view = await mount(t, {
    me: { can_view_stories: true },
    kind: 'event',
    id: 'e1',
    related: () => ({ items: [STORIES[1]] }),
    options: { trigger: null, onLoad: (stories) => loads.push(stories.map((s) => s.id)) },
  });
  await settle(() => loads.length === 1);
  assert.deepEqual(plain(loads), [['s2']]);
  assert.equal(view.mounted.tab, null);
  assert.equal(view.calls[0].url, '/api/v1/stories/related/event/e1');
  assert.match(view.el('storiesRelatedPane').textContent, /Restore test/);
  const { api } = view.ui;
  assert.equal(api.KIND_NAMES.effectiveness_measure, 'effectiveness measure');
  assert.equal(api.canManageStories({ can_view_stories: true }), false);
  assert.equal(api.canManageStories({ can_view_stories: true, can_manage_stories: true }), true);
});

test('meeting minutes offer to join a story from their own page', async (t) => {
  const posted = [];
  let included = [];
  const ui = await page(t, 'isms-meeting', {
    query: '&id=m1',
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/meetings/m1')
        return { id: 'm1', title: 'Monthly review', date: '2026-05-01', links: [] };
      if (u.pathname.endsWith('/changelog')) return { items: [] };
      if (u.pathname === '/api/v1/stories/related/meeting/m1') return { items: included };
      if (u.pathname === '/api/v1/stories') return { items: [OPEN[1]] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      posted.push([u.pathname, plain(body)]);
      included = [OPEN[1]];
      return { ok: true };
    },
  });
  await settle(() => ui.el('meetingTitle').textContent === 'Monthly review');
  // The minutes' stories are a card of their Related items, ahead of the
  // Changelog.
  const tab = ui.el('relatedItemsTab');
  assert.equal(tab.textContent, 'Related items');
  assert.deepEqual(
    [...ui.el('meetingTabs').querySelectorAll('button')].map((button) => button.id),
    ['meetingDetailsTab', 'relatedItemsTab', 'meetingChangelogTab'],
  );
  const card = ui.el('relatedStoriesCard');
  assert.equal(card.closest('#relatedItemsPane') !== null, true);
  assert.deepEqual(
    [...ui.el('relatedItemsPane').querySelectorAll('.mosp-related-card-title')].map(
      (title) => title.textContent,
    ),
    ['Stories'],
  );
  tab.dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('storyRelatedChoice')?.options.length === 2);
  assert.match(card.textContent, /This set of minutes is not in a story yet/);
  ui.el('storyRelatedChoice').value = 's3';
  ui.document
    .querySelector('[data-story-add]')
    .dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => posted.length === 1 && /Open <i>draft<\/i>/.test(card.textContent));
  assert.deepEqual(posted[0], [
    '/api/v1/stories/s3/items',
    { items: [{ kind: 'meeting', entity_id: 'm1' }], expected_version: 7 },
  ]);
  await settle(() => card.querySelector('.mosp-related-card-count').textContent === '1');
  assert.equal(tab.textContent, 'Related items');
});

// --- Stories assigned to a control or clause ---------------------------------------

const ASSIGNED = [
  {
    id: 's1',
    title: 'Backup <b>failure</b>',
    status: 'open',
    updated_at: '2026-05-01',
    created_by: 'alice',
  },
  {
    id: 's/2',
    title: 'Restore test',
    status: 'closed',
    updated_at: '2026-05-02',
    created_by: null,
  },
];
const HOSTED =
  '<main><ul class="nav nav-tabs"><li><button id="govTab">Related items</button></li></ul>' +
  '<div class="tab-content"><section><div id="host">old</div></section></div></main>';

async function assigned(
  t,
  { html = HOSTED, items = ASSIGNED, me = { is_admin: true }, fail } = {},
) {
  const calls = [];
  const world = { fail };
  const ui = await loadUi('pages/story-related.js', {
    html,
    url: 'https://keen.example/control.html?framework=A&id=c1',
    vendor: {
      apiGet: async (url) => {
        calls.push(url);
        if (world.fail) throw new Error('Stories offline');
        return { items };
      },
      withFramework: (path, fw) => `${path}&framework=${fw}`,
      getCurrentFramework: () => 'A',
      fmtTs: (value) => `on ${value}`,
    },
  });
  t.after(ui.close);
  const el = (name) => ui.document.getElementById(name);
  const show = (name) => el(name).dispatchEvent(new ui.window.Event('shown.bs.tab'));
  return {
    ui,
    calls,
    world,
    el,
    show,
    me,
    mount: (options) =>
      ui.api.mountAssignedStories({ me, host: el('host'), trigger: el('govTab'), ...options }),
  };
}

test('the stories assigned to a clause are listed when the tab they are on is shown', async (t) => {
  const view = await assigned(t);
  const mounted = view.mount({ kind: 'clause', id: 'k/1' });
  const pane = view.el('host');
  assert.equal(mounted.pane, pane);
  assert.equal(mounted.tab, view.el('govTab'));
  // The card that holds the list carries the heading; the list has none.
  assert.equal(pane.querySelector('h2'), null);
  assert.equal(pane.textContent, 'Loading stories');
  assert.deepEqual(view.calls, [], 'nothing loads until the tab is shown');

  view.show('govTab');
  await settle(() => pane.querySelector('.list-group'));
  view.show('govTab');
  assert.deepEqual(view.calls, ['/api/v1/stories/related/clause/k%2F1'], 'read once');
  assert.equal(view.el('govTab').textContent, 'Related items', 'the tab keeps its name');
  assert.deepEqual(
    [...pane.querySelectorAll('.list-group a')].map((a) => [a.textContent, a.getAttribute('href')]),
    [
      ['Backup <b>failure</b>', '/story.html?id=s1&framework=A'],
      ['Controls and clauses', '/story.html?id=s1&tab=framework&framework=A'],
      ['Restore test', '/story.html?id=s%2F2&framework=A'],
      ['Controls and clauses', '/story.html?id=s%2F2&tab=framework&framework=A'],
    ],
  );
  assert.equal(pane.querySelector('.list-group b'), null, 'titles are text');
  assert.deepEqual(
    [...pane.querySelectorAll('.list-group .small-muted')].map((m) => m.textContent),
    ['updated on 2026-05-01 · by alice', 'updated on 2026-05-02'],
  );
  assert.deepEqual(
    [...pane.querySelectorAll('.badge')].map((b) => [b.textContent, b.className]),
    [
      ['open', 'badge text-bg-success ms-1'],
      ['closed', 'badge text-bg-secondary ms-1'],
    ],
  );
  // No forms: stories are assigned on the story's own page.
  assert.equal(pane.querySelector('form'), null);
});

test('a control or clause with no story says so', async (t) => {
  const view = await assigned(t, { items: [] });
  await view.mount({ kind: 'control', id: 'c1' }).load();
  assert.equal(view.el('host').textContent, 'No story is assigned to this control yet.');
});

test('a host whose tab is already open loads straight away', async (t) => {
  const view = await assigned(t, {
    html: HOSTED.replace('id="govTab"', 'id="govTab" class="nav-link active"'),
  });
  view.mount({ kind: 'control', id: 'c1', host: view.el('host'), trigger: view.el('govTab') });
  await settle(() => view.el('host').querySelector('.list-group'));
  assert.deepEqual(view.calls, ['/api/v1/stories/related/control/c1']);
});

test('stories that cannot be read are explained, and the next opening tries again', async (t) => {
  const view = await assigned(t, { fail: true });
  view.mount({ kind: 'clause', id: 'k1' });
  const pane = view.el('host');
  view.show('govTab');
  await settle(() => /unavailable/.test(pane.textContent));
  assert.equal(pane.textContent, 'Stories are unavailable with your current access.');
  view.world.fail = false;
  view.show('govTab');
  await settle(() => pane.querySelector('.list-group'));
  assert.equal(view.calls.length, 2);
  assert.equal(pane.querySelectorAll('li').length, 2);
});

test('nothing is mounted without a record or story access', async (t) => {
  const view = await assigned(t);
  assert.equal(view.mount({ kind: 'clause', id: '' }), null);
  assert.equal(view.mount({ me: {}, kind: 'clause', id: 'k1' }), null);
  assert.equal(view.el('host').textContent, 'old');
  view.show('govTab');
  assert.deepEqual(view.calls, []);
});

test('a legal register item is named in the forms of its stories', async (t) => {
  const view = await mount(t, { kind: 'legal_item', id: 'l1' });
  assert.equal(view.ui.api.KIND_NAMES.legal_item, 'legal register item');
  assert.match(
    view.el('storiesRelatedPane').querySelector('label').textContent,
    /Add this legal register item to a story/,
  );
});
