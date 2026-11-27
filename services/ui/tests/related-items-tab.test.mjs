// The Related items tab of a record that other records point at: one card for
// each kind of record related to it, which cards a reader gets, when their
// lists load, and what a failure leaves.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const HOSTILE = '<img src=x onerror=alert(1)>';
const PAGE =
  '<main><ul class="nav nav-tabs"><li><button id="detailsTab">Details</button></li>' +
  '<li><button id="documentChangelogTab">Changelog</button></li></ul>' +
  '<div class="tab-content"><section id="detailsPane"></section></div>' +
  '<div id="host">old</div></main>';
const SWOT = {
  items: [
    { id: 's 1', title: HOSTILE, kind: 'weakness', significance: 4 },
    { id: 's2', title: 'New market', kind: 'opportunity', significance: 2 },
  ],
  total: 7,
};
const LEGAL = {
  items: [
    { id: 'l1', title: HOSTILE, in_scope: true },
    { id: 'l2', title: 'Bribery Act 2010', in_scope: false },
  ],
  total: 2,
};

async function mount(
  t,
  { html = PAGE, query = '', setup, answers = {}, me = { is_admin: true }, ...options } = {},
) {
  const calls = [];
  const world = {
    swot: () => SWOT,
    legal: () => LEGAL,
    stories: () => ({ items: [] }),
    ...answers,
  };
  const ui = await loadUi('pages/related-items-tab.js', {
    html,
    url: `https://keen.example/isms-document.html?framework=A&id=d1${query}`,
    setup,
    vendor: {
      apiGet: async (url) => {
        calls.push(url);
        if (url.startsWith('/api/v1/isms/swot?')) return world.swot();
        if (url.startsWith('/api/v1/isms/legal-register?')) return world.legal();
        if (url.startsWith('/api/v1/stories/related/')) return world.stories();
        if (url === '/api/v1/stories?status=open&limit=200') return { items: [] };
        throw new Error('Unexpected GET ' + url);
      },
      withFramework: (path, fw) => `${path}&framework=${fw}`,
      getCurrentFramework: () => 'A',
      fmtTs: (value) => `on ${value}`,
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  const mounted = ui.api.mountRelatedItems({ me, kind: 'document', id: 'd1', ...options });
  const show = () => el('relatedItemsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  const titles = () =>
    [...ui.document.querySelectorAll('.mosp-related-card-title')].map((title) => title.textContent);
  const links = (card) =>
    [...el(card).querySelectorAll('.mosp-related-list-item')].map((item) => [
      item.querySelector('.mosp-related-link').textContent,
      item.querySelector('.mosp-related-link').getAttribute('href'),
      item.querySelector('.mosp-related-detail').textContent,
    ]);
  const count = (card) => el(card).querySelector('.mosp-related-card-count').textContent;
  return { ui, world, calls, el, mounted, show, titles, links, count };
}

test('a record gains one Related items tab, with a card for each kind related to it', async (t) => {
  const view = await mount(t);
  const { el, calls } = view;
  // One tab, ahead of the Changelog, and none for a kind of its own.
  assert.deepEqual(
    [...view.ui.document.querySelectorAll('.nav-tabs button')].map((b) => [b.id, b.textContent]),
    [
      ['detailsTab', 'Details'],
      ['relatedItemsTab', 'Related items'],
      ['documentChangelogTab', 'Changelog'],
    ],
  );
  const tab = el('relatedItemsTab');
  assert.equal(view.mounted.tab, tab);
  assert.equal(tab.dataset.bsTarget, '#relatedItemsPane');
  assert.equal(tab.closest('li').getAttribute('role'), 'presentation');
  const pane = el('relatedItemsPane');
  assert.equal(view.mounted.pane, pane);
  assert.equal(pane.getAttribute('aria-labelledby'), 'relatedItemsTab');
  assert.equal(pane.parentElement.className, 'tab-content');
  assert.deepEqual(view.titles(), ['SWOT analysis', 'Legal register', 'Stories']);
  for (const card of pane.querySelectorAll('.mosp-related-card'))
    assert.equal(card.closest('#relatedItemsPane'), pane);
  assert.match(el('relatedSwotCard').textContent, /SWOT items related to this document\./);
  assert.match(el('relatedSwotCard').textContent, /Loading related SWOT items/);
  assert.match(el('relatedLegalCard').textContent, /Loading related legal register items/);
  assert.match(el('relatedStoriesCard').textContent, /Stories that include this document/);
  assert.deepEqual(calls, [], 'nothing is read until the tab is shown');

  view.show();
  await settle(() => view.links('relatedLegalCard').length === 2 && calls.length === 4);
  assert.deepEqual(calls.slice().sort(), [
    '/api/v1/isms/legal-register?target_type=document&target_id=d1&limit=100',
    '/api/v1/isms/swot?target_type=document&target_id=d1&limit=100',
    '/api/v1/stories/related/document/d1',
    '/api/v1/stories?status=open&limit=200',
  ]);
  // Titles are text, each opens its record, and the count is the whole number.
  assert.equal(pane.querySelector('img'), null);
  assert.deepEqual(view.links('relatedSwotCard'), [
    [HOSTILE, '/swot.html?id=s%201&tab=related&framework=A', 'weakness · 4/5'],
    ['New market', '/swot.html?id=s2&tab=related&framework=A', 'opportunity · 2/5'],
  ]);
  assert.equal(view.count('relatedSwotCard'), '7');
  assert.deepEqual(view.links('relatedLegalCard'), [
    [HOSTILE, '/legal-register.html?id=l1&tab=related&framework=A', 'In scope'],
    ['Bribery Act 2010', '/legal-register.html?id=l2&tab=related&framework=A', 'Out of scope'],
  ]);
  assert.equal(view.count('relatedLegalCard'), '2');
  assert.equal(view.count('relatedStoriesCard'), '0');
  assert.deepEqual(
    [...pane.querySelectorAll('.mosp-related-card-tools a')].map((a) => [
      a.textContent,
      a.getAttribute('href'),
    ]),
    [
      ['Open in SWOT analysis', '/swot.html?target_type=document&target_id=d1&framework=A'],
      [
        'Open in the legal register',
        '/legal-register.html?target_type=document&target_id=d1&framework=A',
      ],
    ],
  );
  // Showing the tab again reads nothing more.
  view.show();
  await view.mounted.load();
  assert.equal(calls.length, 4);
});

test('a record with nothing related says so in each card', async (t) => {
  const view = await mount(t, {
    answers: { swot: () => ({ items: [], total: 0 }), legal: () => ({ items: [], total: 0 }) },
  });
  view.show();
  await view.mounted.load();
  assert.match(view.el('relatedSwotCard').textContent, /No SWOT items are related\.$/);
  assert.match(view.el('relatedLegalCard').textContent, /No legal register items are related\.$/);
  assert.equal(view.count('relatedSwotCard'), '0');
});

test('a list that cannot be read says why, and the next showing tries again', async (t) => {
  const view = await mount(t, {
    answers: {
      swot: () => {
        throw new Error('SWOT <b>offline</b>');
      },
    },
  });
  view.show();
  await settle(() => /could not be loaded/.test(view.el('relatedSwotCard').textContent));
  const body = view.el('relatedSwotCard').querySelector('.mosp-related-card-body');
  assert.equal(body.textContent, 'SWOT analysis could not be loaded: SWOT <b>offline</b>');
  assert.equal(body.querySelector('b'), null);
  // The other cards are not held up by it.
  await settle(() => view.links('relatedLegalCard').length === 2);
  view.world.swot = () => SWOT;
  view.show();
  await settle(() => view.links('relatedSwotCard').length === 2);
  // Only the card that failed is read again.
  assert.equal(view.calls.filter((url) => url.includes('/legal-register?')).length, 1);
  assert.equal(view.calls.filter((url) => url.includes('/swot?')).length, 2);
});

test('the cards are those the kind of record and the reader allow', async (t) => {
  const off = (feature) => ({ is_admin: true, features: { enabled: { [feature]: false } } });
  for (const [options, titles] of [
    [{ kind: 'asset' }, ['SWOT analysis', 'Legal register', 'Stories']],
    [{ kind: 'risk' }, ['SWOT analysis', 'Legal register', 'Stories']],
    [{ kind: 'objective' }, ['SWOT analysis', 'Stories']],
    [{ kind: 'person' }, ['SWOT analysis', 'Stories']],
    [{ kind: 'vendor' }, ['SWOT analysis', 'Stories']],
    [{ kind: 'meeting' }, ['Stories']],
    [{ kind: 'effectiveness_measure' }, ['Stories']],
    [{ me: { can_view_isms: true } }, ['SWOT analysis', 'Legal register']],
    [{ me: { can_view_stories: true } }, ['Stories']],
    [{ me: off('swot') }, ['Legal register', 'Stories']],
    [{ me: off('legal_register') }, ['SWOT analysis', 'Stories']],
    [{ me: off('stories') }, ['SWOT analysis', 'Legal register']],
  ]) {
    const view = await mount(t, options);
    assert.deepEqual(view.titles(), titles, JSON.stringify(options));
  }
  // A reader with no card to see gets no tab, and neither does no record.
  for (const options of [{ me: { can_view_risks: true } }, { me: null }, { id: '' }]) {
    const view = await mount(t, options);
    assert.equal(view.mounted, null);
    assert.equal(view.el('relatedItemsTab'), null);
    assert.equal(view.el('relatedItemsPane'), null);
    assert.equal(view.ui.document.querySelectorAll('.nav-tabs li').length, 2);
    assert.deepEqual(view.calls, []);
  }
});

test("a page's own cards come first and load with the rest", async (t) => {
  let reads = 0;
  const view = await mount(t, { me: { can_view_isms: true } });
  const { api } = view.ui;
  const own = api.fetchedCard({
    id: 'ownCard',
    title: 'Risks',
    description: 'Risks recorded against this asset.',
    what: 'risks',
    empty: 'No risks are recorded.',
    read: async () => {
      reads += 1;
      return { count: 1, table: { columns: ['Risk'], rows: [[HOSTILE]] } };
    },
  });
  assert.match(own.card.textContent, /Loading risks/);
  view.el('host').replaceChildren();
  const hosted = api.mountRelatedItems({
    me: { can_view_isms: true },
    kind: 'asset',
    id: 'a1',
    host: view.el('host'),
    first: [own],
  });
  // In a host the lists load straight away, and there is no tab to wait for.
  assert.equal(hosted.tab, null);
  assert.equal(hosted.pane, view.el('host'));
  await hosted.load();
  assert.equal(reads, 1);
  const cards = [...view.el('host').querySelectorAll('.mosp-related-card')];
  assert.deepEqual(
    cards.map((card) => card.querySelector('.mosp-related-card-title').textContent),
    ['Risks', 'SWOT analysis', 'Legal register'],
  );
  assert.equal(view.el('ownCard').querySelector('td').textContent, HOSTILE);
  assert.equal(view.el('ownCard').querySelector('img'), null);
  assert.equal(view.el('ownCard').querySelector('.mosp-related-card-count').textContent, '1');
  assert.ok(view.calls.includes('/api/v1/isms/swot?target_type=asset&target_id=a1&limit=100'));
});

test('a page without a Changelog tab gains the tab last, and one without tabs gains none', async (t) => {
  const last = await mount(t, { html: PAGE.replace('documentChangelogTab', 'historyTab') });
  assert.deepEqual(
    [...last.ui.document.querySelectorAll('.nav-tabs button')].map((b) => b.id),
    ['detailsTab', 'historyTab', 'relatedItemsTab'],
  );
  for (const html of [
    '<main><div class="tab-content"></div></main>',
    '<main><ul class="nav-tabs"></ul></main>',
  ]) {
    const none = await mount(t, { html });
    assert.equal(none.mounted, null);
    assert.equal(none.el('relatedItemsTab'), null);
  }
});

test('an address naming Related items, or a tab a kind once had, opens the tab', async (t) => {
  for (const name of ['related', 'stories', 'swot', 'legal']) {
    const shown = [];
    const view = await mount(t, {
      query: `&tab=${name}`,
      setup(window) {
        window.bootstrap = {
          Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
        };
      },
    });
    assert.deepEqual(shown, ['relatedItemsTab'], name);
    await settle(() => view.links('relatedSwotCard').length === 2);
  }
  // Without the tab script the lists are still read.
  const bare = await mount(t, { query: '&tab=related' });
  await settle(() => bare.links('relatedSwotCard').length === 2);
  // Any other tab is the page's own business.
  const other = await mount(t, { query: '&tab=changelog' });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(other.calls, []);
});
