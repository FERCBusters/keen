// Keen's changes to the design-system navbar when the navbar is laid out
// differently, links are malformed or the catalogue cannot be read.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

async function shell(
  t,
  {
    navbar = '',
    html = '<div id="navbar"></div>',
    url = 'https://keen.example/controls.html?framework=A',
    catalog = { items: [{ slug: 'A', name: 'Framework A', has_clauses: true }], default: 'A' },
    catalogFails = false,
    catalogRequest = null,
  } = {},
) {
  let win;
  const ui = await loadUi('app.js', {
    html,
    url,
    setup(window) {
      win = window;
    },
    vendor: {
      getUiConfig: () => ({
        nav: {},
        framework: {},
        auth: {},
        bell: {},
        storage: { frameworkKey: 'keen_framework' },
      }),
      initCollapsibleFilterSections: () => {},
      getCurrentFramework: () => new URL(win.location.href).searchParams.get('framework') || '',
      getLoadedFrameworkCatalog: () => (catalogFails ? null : catalog),
      apiGet: async (path) => {
        if (catalogFails) throw new Error('Catalogue unavailable');
        if (catalogRequest && path === '/api/v1/frameworks') return catalogRequest();
        throw new Error('Unexpected GET ' + path);
      },
      initNavbar: async () => {
        const nav = win.document.getElementById('navbar');
        if (nav) nav.innerHTML = navbar;
        return { is_admin: true };
      },
    },
  });
  t.after(ui.close);
  const me = await ui.api.initNavbar();
  return { ...ui, me, nav: ui.document.getElementById('navbar') };
}

test('a Risks link outside a list item turns its container into the risk menu', async (t) => {
  const ui = await shell(t, {
    navbar:
      '<ul class="navbar-nav"><li class="nav-item"><a href="/events.html">Events</a></li></ul>' +
      '<div class="nav-extra"><a href="/risks.html?framework=A">Risks</a></div>',
  });
  const holder = ui.nav.querySelector('.nav-extra');
  assert.ok(holder.classList.contains('nav-item'));
  assert.ok(holder.classList.contains('dropdown'));
  assert.deepEqual(
    [...holder.querySelectorAll('.dropdown-item')].map((a) => [
      a.textContent.trim(),
      a.getAttribute('href'),
    ]),
    [
      ['Risks dashboard', '/risks.html?framework=A'],
      ['Add a risk', '/risk-wizard.html?framework=A'],
      ['PESTLE(E) method', '/risks.html?tab=pestle&framework=A'],
      ['Interested parties', '/interested_parties.html?framework=A'],
      ['Keen Mitigator', '/mitigator.html?framework=A'],
    ],
  );
  // The ISMS menu joins the link list.
  assert.ok(ui.nav.querySelector('ul.navbar-nav > li.dropdown #ismsDropdown'));
});

test('Home links to the home page are removed wherever they sit; other Home links stay', async (t) => {
  const ui = await shell(t, {
    navbar:
      '<ul class="navbar-nav"><li class="nav-item"><a href="/">Home</a></li></ul>' +
      '<a id="bare" href="/index.html">Home</a><a id="empty" href="">Home</a>' +
      '<a id="about" href="/about.html">Home</a>',
  });
  const homes = [...ui.nav.querySelectorAll('a')]
    .filter((a) => a.textContent.trim() === 'Home')
    .map((a) => a.getAttribute('href'));
  assert.deepEqual(homes, ['/about.html']);
  assert.equal(ui.document.getElementById('bare'), null);
  assert.equal(ui.document.getElementById('empty'), null);
  // The list item that held the home link went with it.
  assert.equal(ui.nav.querySelectorAll('ul.navbar-nav > li.nav-item a[href="/"]').length, 0);
});

for (const has_clauses of [true, false])
  test(`a Clauses link outside a list item is ${has_clauses ? 'shown' : 'hidden'} for a framework ${has_clauses ? 'with' : 'without'} clauses`, async (t) => {
    const ui = await shell(t, {
      navbar: '<a id="clauses" href="/clauses.html?framework=A">Clauses</a>',
      catalog: { items: [{ slug: 'A', name: 'Framework A', has_clauses }], default: 'A' },
    });
    assert.equal(ui.document.getElementById('clauses').hidden, !has_clauses);
  });

test('a Clauses link outside a list item is hidden while the catalogue is read', async (t) => {
  let release;
  const ui = await shell(t, {
    navbar: '<a id="clauses" href="/clauses.html?framework=A">Clauses</a>',
    catalogRequest: () => new Promise((resolve) => (release = resolve)),
  });
  const clauses = ui.document.getElementById('clauses');
  assert.equal(clauses.hidden, false);
  const refreshed = ui.api.refreshFrameworkNavigation();
  assert.equal(clauses.hidden, true);
  release({ items: [{ slug: 'A', name: 'Framework A', has_clauses: true }], default: 'A' });
  await refreshed;
  assert.equal(clauses.hidden, false);
});

test('a Clauses link outside a list item stays hidden when the catalogue cannot be read', async (t) => {
  const ui = await shell(t, {
    navbar:
      '<a id="clauses" href="/clauses.html?framework=A">Clauses</a><a id="events" href="/events.html">Events</a>',
    catalogFails: true,
  });
  assert.equal(ui.document.getElementById('clauses').hidden, true);
  assert.equal(ui.document.getElementById('events').hidden, false);
});

test('a page without a navbar still starts when the catalogue cannot be read', async (t) => {
  const ui = await shell(t, { html: '<main></main>', catalogFails: true });
  assert.deepEqual(JSON.parse(JSON.stringify(ui.me)), { is_admin: true });
  assert.equal(ui.document.querySelector('[data-keen-documentation]'), null);
});

test('a page without a navbar moves to the default framework when its own is not offered', async (t) => {
  const ui = await shell(t, { html: '<main></main>' });
  const catalog = await ui.api.refreshFrameworkNavigation({
    items: [{ slug: 'B', name: 'Framework B' }],
    default: 'B',
  });
  assert.deepEqual([...catalog.items.map((item) => item.slug)], ['B']);
  assert.equal(new URL(ui.window.location.href).searchParams.get('framework'), 'B');
  assert.equal(ui.window.mospFramework, 'B');
});
