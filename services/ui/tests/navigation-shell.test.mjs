import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// KEEN's additions to the design-system navbar: grouped risk and ISMS menus,
// Help, framework reconciliation and the account-aware notification bell.
const LINKS = [
  ['Controls', '/controls.html?framework=A'],
  ['Clauses', '/clauses.html?framework=A'],
  ['Events', '/events.html?framework=A'],
  ['Risks', '/risks.html?framework=A'],
  ['Audits', '/audits.html?framework=A'],
];

// The navbar markup the design system renders for these links.
function navbarHtml(links) {
  const items = links
    .map(
      ([label, href, attrs = 'data-nav-fw="1"']) =>
        `<li class="nav-item"><a class="nav-link" ${attrs} href="${href}">${label}</a></li>`,
    )
    .join('');
  return `<nav class="navbar"><ul class="navbar-nav me-auto">${items}</ul>
    <select id="navFrameworkSelect" disabled><option>Loading…</option></select>
    <button id="navLogout" type="button">Logout</button></nav>`;
}

const CATALOG = {
  items: [{ slug: 'A', name: 'Framework A', has_clauses: true }],
  default: 'A',
};

async function shell(
  t,
  {
    links = LINKS,
    catalog = CATALOG,
    catalogRequest,
    me = { is_admin: true, user: 'admin' },
    url = 'https://keen.example/controls.html?framework=A',
    globals = {},
    apiPost,
  } = {},
) {
  let win;
  const seen = { config: null, catalogRequests: [], events: [] };
  const ui = await loadUi('app.js', {
    html: '<div id="navbar"></div>',
    url,
    globals,
    setup(window) {
      win = window;
      window.addEventListener('keen:framework-catalog-changed', (event) =>
        seen.events.push(JSON.parse(JSON.stringify(event.detail))),
      );
    },
    vendor: {
      getUiConfig: (overrides) => ({
        nav: {},
        framework: {},
        auth: {},
        bell: {},
        storage: { frameworkKey: 'keen_framework' },
        ...(overrides || {}),
      }),
      initCollapsibleFilterSections: () => {},
      getCurrentFramework: () => new URL(win.location.href).searchParams.get('framework') || '',
      getLoadedFrameworkCatalog: () => catalog,
      apiGet: async (path) => {
        seen.catalogRequests.push(path);
        if (!catalogRequest) throw new Error('Unexpected GET ' + path);
        return catalogRequest(path);
      },
      apiPost: apiPost ?? (async () => ({})),
      initNavbar: async (config) => {
        seen.config = config;
        win.document.getElementById('navbar').innerHTML = navbarHtml(links);
        return me;
      },
    },
  });
  t.after(ui.close);
  const result = await ui.api.initNavbar();
  const nav = ui.document.getElementById('navbar');
  const topLevel = () =>
    [...nav.querySelectorAll('ul.navbar-nav > li')].map((li) =>
      li.querySelector('a').textContent.trim(),
    );
  const menu = (id) =>
    [...nav.querySelectorAll(`[aria-labelledby="${id}"] a`)].map((a) => [
      a.textContent.trim(),
      a.getAttribute('href'),
    ]);
  return { ...ui, me: result, nav, seen, topLevel, menu, window: win };
}

test('the design system receives the Stories link for accounts that may read them', async (t) => {
  const ui = await shell(t);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.seen.config.links)), [
    {
      key: 'stories',
      label: 'Stories',
      href: '/stories.html',
      requireAny: ['is_admin', 'can_view_stories'],
    },
  ]);
});

test('risk and ISMS pages are grouped into menus that keep the framework', async (t) => {
  const ui = await shell(t);

  assert.deepEqual(ui.topLevel(), [
    'Controls',
    'Clauses',
    'Events',
    'Risks',
    'ISMS',
    'Audits',
    'Help',
  ]);
  assert.deepEqual(ui.menu('riskAssessmentsDropdown'), [
    ['Risks dashboard', '/risks.html?framework=A'],
    ['Add a risk', '/risk-wizard.html?framework=A'],
    ['Register & heatmap', '/risks.html?tab=register&framework=A'],
    ['PESTLE(E) method', '/risks.html?tab=pestle&framework=A'],
    ['Interested Parties', '/interested_parties.html?framework=A'],
    ['Keen Mitigator', '/mitigator.html?framework=A'],
  ]);
  assert.deepEqual(ui.menu('ismsDropdown'), [
    ['ISMS Overview', '/isms.html?framework=A'],
    ['Objectives', '/isms.html?tab=objectives&framework=A'],
    ['SWOT analysis', '/swot.html?framework=A'],
    ['Effectiveness Measures', '/isms.html?tab=effectiveness&framework=A'],
    ['Policies & Processes', '/isms.html?tab=documents&framework=A'],
    ['Organisation Chart', '/isms.html?tab=org&framework=A'],
    ['Asset Matrix', '/isms.html?tab=assets&framework=A'],
    ['People & vendors', '/assurance.html?framework=A'],
    ['Access Control Matrix', '/isms.html?tab=access&framework=A'],
    ['Minutes of Meetings', '/isms.html?tab=meetings&framework=A'],
    ['Statement of Applicability', '/statement-of-applicability.html?framework=A'],
  ]);
  const help = ui.nav.querySelector('[data-keen-documentation]');
  assert.equal(help.closest('li').className, 'nav-item');
  assert.deepEqual(
    [help.getAttribute('href'), help.target, help.rel, help.getAttribute('aria-haspopup')],
    ['/help/', '_blank', 'noopener noreferrer', 'dialog'],
  );
  assert.equal(help.dataset.helpTopic, 'page');

  // A second page module on the same document shares the navbar.
  assert.equal(await ui.api.initNavbar(), ui.me);
  assert.equal(ui.nav.querySelectorAll('[data-keen-documentation]').length, 1);
  assert.equal(ui.nav.querySelectorAll('#ismsDropdown').length, 1);
});

test('without risk access the ISMS menu still joins the navigation', async (t) => {
  const ui = await shell(t, {
    links: LINKS.filter(([label]) => label !== 'Risks'),
  });
  assert.equal(ui.nav.querySelector('#riskAssessmentsDropdown'), null);
  assert.ok(ui.topLevel().includes('ISMS'));
  assert.equal(ui.menu('ismsDropdown').length, 11);
});

test('an ISMS link supplied by the design system is used as it is', async (t) => {
  const ui = await shell(t, { links: [...LINKS, ['ISMS', '/isms.html?framework=A']] });
  assert.equal(ui.nav.querySelector('#ismsDropdown'), null);
  assert.equal(ui.topLevel().filter((label) => label === 'ISMS').length, 1);
});

test('the Home link to the explorer is removed and other Home pages stay', async (t) => {
  const ui = await shell(t, {
    links: [['Home', '/index.html'], ['Home', '/home.html'], ...LINKS],
  });
  const homes = [...ui.nav.querySelectorAll('a')].filter((a) => a.textContent === 'Home');
  assert.deepEqual(
    homes.map((a) => a.getAttribute('href')),
    ['/home.html'],
  );
});

test('the account shell turns off account caching and asks the bell for the right summary', async (t) => {
  const ui = await shell(t);
  const config = ui.seen.config;
  assert.equal(config.auth.meCacheMs, 0);
  assert.equal(config.brandIcon, '/keen-mitigator.png');
  assert.equal(config.nav.expandClass, 'navbar-expand-lg');
  assert.deepEqual([config.framework.selectMinChars, config.framework.selectMaxChars], [14, 19]);
  const summary = config.bell.summaryEndpoint;
  assert.equal(summary({ is_admin: true }), '/api/v1/admin/questions/summary');
  assert.equal(summary({ is_admin: false }), '/api/v1/me/questions/summary');
  assert.equal(summary({ is_admin: true, features: { enabled: { questions: false } } }), '');
});

test('a framework no longer enabled gives way to the default, everywhere on the page', async (t) => {
  const stored = new Map();
  const ui = await shell(t, {
    url: 'https://keen.example/controls.html?framework=OLD#evidence',
    catalog: null,
    catalogRequest: async () => ({
      items: [
        { slug: 'A', name: 'Framework A', has_clauses: false },
        { slug: 'OLD', name: 'Retired framework', enabled: false },
      ],
      default: 'A',
    }),
    links: [
      ['Controls', '/controls.html?framework=OLD'],
      ['Clauses', '/clauses.html?framework=OLD'],
      ['Guide', 'https://docs.example/guide?framework=OLD', ''],
      ['Account', '/account.html', ''],
    ],
    globals: { localStorage: { setItem: (key, value) => stored.set(key, value) } },
  });

  assert.deepEqual(ui.seen.catalogRequests, ['/api/v1/frameworks']);
  assert.equal(ui.window.location.search, '?framework=A');
  assert.equal(ui.window.location.hash, '#evidence');
  assert.equal(ui.window.keenFramework, 'A');
  assert.equal(ui.window.mospFramework, 'A');
  assert.equal(ui.window.keenFrameworkDefault, 'A');
  assert.equal(stored.get('keen_framework'), 'A');
  const hrefs = Object.fromEntries(
    [...ui.nav.querySelectorAll('ul.navbar-nav a')].map((a) => [
      a.textContent.trim(),
      a.getAttribute('href'),
    ]),
  );
  assert.equal(hrefs.Controls, '/controls.html?framework=A');
  assert.equal(hrefs.Guide, 'https://docs.example/guide?framework=OLD');
  assert.equal(hrefs.Account, '/account.html');

  // Only enabled frameworks are offered, and the picker names its choice.
  const picker = ui.document.getElementById('navFrameworkSelect');
  assert.deepEqual(
    [...picker.options].map((o) => [o.value, o.textContent, o.title]),
    [['A', 'Framework A', 'Framework A (A)']],
  );
  assert.equal(picker.value, 'A');
  assert.equal(picker.disabled, false);
  assert.equal(picker.title, 'Framework A (A)');
  assert.equal(picker.getAttribute('aria-label'), 'Framework: Framework A (A)');
  // The default framework has no clauses, so Clauses is hidden.
  assert.equal(ui.nav.querySelector('a[href^="/clauses.html"]').closest('li').hidden, true);
  assert.deepEqual(ui.seen.events.at(-1), {
    items: [{ slug: 'A', name: 'Framework A', has_clauses: false }],
    default: 'A',
  });
});

test('a saved organisation catalogue updates the navigation without a reload', async (t) => {
  const ui = await shell(t);
  const clauses = ui.nav.querySelector('a[href^="/clauses.html"]').closest('li');
  assert.equal(clauses.hidden, false);

  const catalog = await ui.api.refreshFrameworkNavigation({
    items: [{ slug: 'A', name: 'Renamed A', has_clauses: false }],
    default: 'A',
  });

  assert.equal(catalog.items[0].name, 'Renamed A');
  assert.equal(clauses.hidden, true);
  assert.equal(ui.document.getElementById('navFrameworkSelect').title, 'Renamed A (A)');
  assert.deepEqual(ui.seen.catalogRequests, []);
});

test('while the catalogue loads, Clauses stays hidden until it applies', async (t) => {
  let release;
  const pending = new Promise((resolve) => (release = resolve));
  const ui = await shell(t, { catalogRequest: () => pending });
  const clauses = ui.nav.querySelector('a[href^="/clauses.html"]').closest('li');
  assert.equal(clauses.hidden, false);

  const refreshed = ui.api.refreshFrameworkNavigation();

  assert.equal(clauses.hidden, true);
  release(CATALOG);
  await refreshed;
  assert.equal(clauses.hidden, false);
  assert.deepEqual(ui.seen.catalogRequests, ['/api/v1/frameworks']);
});

test('with no frameworks available the picker explains why it is empty', async (t) => {
  const ui = await shell(t, { catalog: { items: [], default: '' } });
  const picker = ui.document.getElementById('navFrameworkSelect');
  assert.equal(picker.options.length, 0);
  assert.equal(picker.disabled, true);
  assert.equal(
    picker.title,
    'No frameworks available; ask an administrator to check organisation settings',
  );
  assert.equal(ui.nav.querySelector('a[href^="/clauses.html"]').closest('li').hidden, true);
  assert.equal(ui.window.location.search, '?framework=A');
});

test('when the catalogue cannot be checked the page still opens with Clauses hidden', async (t) => {
  const ui = await shell(t, {
    catalog: null,
    catalogRequest: async () => {
      throw new Error('Catalogue unavailable');
    },
  });
  assert.equal(ui.me.is_admin, true);
  assert.equal(ui.nav.querySelector('a[href^="/clauses.html"]').closest('li').hidden, true);
  assert.equal(ui.nav.querySelector('a[href^="/controls.html"]').closest('li').hidden, false);
});

test('single sign-on sign-out continues at the provider address the server returns', async (t) => {
  const posts = [];
  const location = { href: 'https://keen.example/controls.html' };
  const ui = await shell(t, {
    me: { is_admin: true, user: 'admin', logout_url: '/api/v1/auth/oidc/logout' },
    globals: { location },
    apiPost: async (path) => {
      posts.push(path);
      return { logout_url: 'https://idp.example/logout?client_id=keen' };
    },
  });

  ui.document.getElementById('navLogout').click();

  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(posts, ['/api/v1/auth/oidc/logout']);
  assert.equal(location.href, 'https://idp.example/logout?client_id=keen');
});

test('a local sign-out returns to the sign-in page', async (t) => {
  const posts = [];
  const location = { href: 'https://keen.example/controls.html' };
  const ui = await shell(t, {
    me: { is_admin: true, user: 'admin', logout_url: '' },
    globals: { location },
    // A completed local sign-out has no response body.
    apiPost: async (path) => {
      posts.push(path);
      return null;
    },
  });
  ui.document.getElementById('navLogout').click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(posts, ['/api/v1/auth/logout']);
  assert.equal(location.href, '/login.html');
});

test('a provider logout address the design system handles is left to it', async (t) => {
  const posts = [];
  const ui = await shell(t, {
    me: { is_admin: true, user: 'admin', logout_url: '/oauth2/sign_out' },
    apiPost: async (path) => {
      posts.push(path);
      return {};
    },
  });
  ui.document.getElementById('navLogout').click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(posts, []);
});
