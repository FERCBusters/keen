import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';
const overview = {
  framework: 'A',
  control_count: 2,
  clause_count: 1,
  events: { mapped: 8, unmapped: 0 },
  attention: [],
  counts: {},
};
const timeline = {
  days: 30,
  interval: 'day',
  items: [
    { bucket: '2026-10-01', total: 3, mapped: 3, unmapped: 0 },
    { bucket: '2026-10-02', total: 0, mapped: 0, unmapped: 0 },
    { bucket: '2026-10-03', total: 2, mapped: 1, unmapped: 1 },
  ],
  sources: [{ source: 'forgejo', label: 'Forgejo', color: '#c73e1d', total: 40 }],
};

const control = (id, extra = {}) => ({
  id,
  ref: 'A.5.1',
  title: 'Policies',
  in_scope: true,
  ...extra,
});
const listOf = (items, total = items.length) => ({ items, total });

// Every API route must be declared by the test; unknown paths fail the request.
async function home(
  t,
  {
    routes = {},
    me = { is_admin: true },
    reduced = true,
    setup,
    globals,
    vendor,
    htmlName = 'index',
  } = {},
) {
  const intervals = [];
  const ui = await page(t, 'home', {
    htmlName,
    me,
    globals,
    vendor,
    setup(window) {
      window.matchMedia = () => ({
        matches: reduced,
        addEventListener() {},
        removeEventListener() {},
      });
      // Record polling intervals for manual firing in tests that need them.
      const realTimeout = window.setTimeout.bind(window);
      window.setTimeout = (fn, ms) => realTimeout(fn, Math.min(Number(ms) || 0, 2));
      window.setInterval = (fn, ms) => (intervals.push({ fn, ms }), intervals.length);
      setup?.(window);
    },
    get: async (u) => {
      const key = u.pathname;
      const handler = routes[key];
      if (handler !== undefined)
        return typeof handler === 'function' ? handler(u) : structuredClone(handler);
      if (key === '/api/v1/home') return structuredClone(overview);
      if (key === '/api/v1/charts/event_volume_timeseries') return structuredClone(timeline);
      throw new Error('Unexpected GET ' + u);
    },
  });

  ui.intervals = intervals;
  return ui;
}

const getsTo = (ui, path) =>
  ui.calls.filter(
    (c) => c.method === 'GET' && new URL(c.url, 'https://keen.example').pathname === path,
  );
const assertNoInjectedMarkup = (ui, scope) => {
  assert.equal(scope.querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
};

test('overview reports coverage and highlight counts for the selected framework', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/home': {
        control_count: 10,
        clause_count: 0,
        coverage: { in_scope: 10, with_evidence: 7, without_evidence: 3 },
        overdue_audits: 0,
        measures_outside_threshold: 2,
        collectors_with_failures: 1,
        attention: [
          { href: '/audits.html', label: HOSTILE + 'Audit overdue', detail: 'Due last week' },
        ],
      },
    },
  });
  const kpis = ui.el('homeKpis');
  assert.equal(ui.el('homeFrameworkName').textContent, 'Framework A');
  assert.match(
    kpis.textContent,
    /Control evidence coverage70%7 of 10 in-scope controls have evidence/,
  );
  const ring = kpis.querySelector('.home-kpi-ring');
  assert.equal(ring.getAttribute('aria-label'), '70% of in-scope controls covered by evidence');
  assert.equal(ring.getAttribute('role'), 'img');
  assert.doesNotMatch(kpis.textContent, /Framework clauses|Events mapped/);
  const badges = [...ui.el('homeAttention').querySelectorAll('.badge')];
  assert.deepEqual(
    badges.map((b) => [b.textContent, b.getAttribute('href')]),
    [
      ['3 control gaps', '/controls.html?gaps=1&framework=A'],
      ['2 measures outside threshold', '/isms.html?tab=effectiveness&framework=A'],
      ['1 API collector with failures · all frameworks', '/admin.html?framework=A#integrations'],
    ],
  );
  // Zero counts are absence, not work: a grey zero pill is never rendered.
  assert.ok(badges.every((b) => b.classList.contains('text-bg-warning')));
  assert.ok(!ui.el('homeAttention').textContent.includes('0 overdue audits'));
  assertNoInjectedMarkup(ui, ui.el('homeAttention'));
  assert.match(ui.el('homeAttention').textContent, /Audit overdue/);
  assert.match(ui.el('homeAttention').textContent, /Showing up to five items per category/);
  // The activity band rides on the existing event volume endpoint exactly once.
  const chartCalls = getsTo(ui, '/api/v1/charts/event_volume_timeseries');
  assert.equal(chartCalls.length, 1);
  assert.match(chartCalls[0].url, /framework=A&(?:days=30&breakdown=source|.*breakdown=source)/);
  // This overview has no events payload, so the band stays out of the way.
  assert.equal(ui.el('homeActivity').hidden, true);
});

test('the activity band renders the chart payload with its accessibility text', async (t) => {
  const ui = await home(t);
  assert.equal(ui.el('homeActivity').hidden, false);
  assert.ok(ui.document.querySelector('#homeSpark .home-spark-svg'));
  assert.match(
    ui.el('homeSpark').getAttribute('aria-label'),
    /peaking at 3 on 2026-10-01; 5 collected and 4 mapped/,
  );
  assert.match(
    ui.el('homeSparkNote').textContent,
    /5 events collected · 4 mapped to this framework/,
  );
  const sourceLink = ui.el('homeSources').querySelector('a');
  assert.equal(sourceLink.getAttribute('href'), '/events.html?source=forgejo&framework=A');
  assert.equal(ui.el('homeSourcesMore').textContent, 'All sources');
});

test('overview without coverage or structure points to the next step', async (t) => {
  const noCoverage = await home(t, {
    routes: { '/api/v1/home': { control_count: 4, clause_count: 0, attention: [] } },
  });
  assert.match(noCoverage.el('homeKpis').textContent, /4 controls/);
  assert.match(noCoverage.el('homeKpis').textContent, /Explore this framework's controls/);
  assert.equal(noCoverage.el('homeKpis').querySelector('.home-kpi-ring'), null);
  assert.match(noCoverage.el('homeAttention').textContent, /No attention items found/);

  const admin = await home(t, {
    routes: { '/api/v1/home': { control_count: 0, clause_count: 0 } },
  });
  assert.match(admin.el('homeKpis').textContent, /No entries yet/);
  assert.equal(
    admin.el('homeKpis').querySelector('a').getAttribute('href'),
    '/admin.html?framework=A#framework-editor',
  );

  const reader = await home(t, {
    me: { can_view_audits: true },
    routes: { '/api/v1/home': { control_count: 0, clause_count: 0 } },
  });
  assert.equal(
    reader.el('homeKpis').querySelector('a').getAttribute('href'),
    '/help/using.html?framework=A',
  );
  assert.equal(reader.el('homeActivity').hidden, true);
});

test('a failed overview leaves workspace links and recovers on refresh', async (t) => {
  let fail = true;
  const ui = await home(t, {
    routes: {
      '/api/v1/home': () => {
        if (fail) throw new Error('Offline');
        return { ...overview, events: { mapped: 1, unmapped: 5 } };
      },
    },
  });
  assert.equal(ui.el('homeKpis').innerHTML, '');
  assert.match(ui.el('homeAttention').textContent, /The overview could not be loaded/);
  assert.ok(ui.el('homeTasks').querySelector('a'));
  // Without a loaded overview the activity band stays hidden.
  assert.equal(ui.el('homeActivity').hidden, true);
  fail = false;
  ui.el('homeRefresh').click();
  await settle(() => /5 unmapped events/.test(ui.el('homeAttention').textContent));
  assert.equal(ui.el('homeRefresh').disabled, false);
  assert.equal(
    ui.el('homeAttention').querySelector('.home-attention-item').getAttribute('href'),
    '/events.html?unmapped=true&framework=A',
  );
  await settle(() => ui.document.querySelectorAll('.home-task').length >= 3);
  // The recovered render keeps the fresh overview and activity band together.
  assert.equal(ui.el('homeActivity').hidden, false);
});

const taskLabels = (scope) =>
  [...scope.querySelectorAll('.home-task span')].map((s) => s.textContent);

test('home tasks follow the signed-in role', async (t) => {
  const admin = await home(t);
  // Every permitted shortcut renders inline in the compact grid.
  assert.deepEqual(taskLabels(admin.el('homeTasks')), [
    'Review evidence',
    'Create an evidence mapping',
    'Plan or run an audit',
    'Review the risk register',
    'Record an effectiveness measurement',
    'Manage documents and assets',
  ]);
  assert.equal(admin.el('homeTasks').querySelector('details.home-task-more'), null);
  assert.deepEqual(
    [...admin.el('homeTasks').querySelectorAll('a.home-task')].map((a) => a.getAttribute('href')),
    [
      '/events.html?framework=A',
      '/admin.html?framework=A#evidence-config',
      '/audits.html?framework=A',
      '/risks.html?framework=A',
      '/isms.html?tab=effectiveness&framework=A',
      '/isms.html?framework=A',
    ],
  );
  assert.equal(admin.el('homeSearch').hidden, false);

  const reader = await home(t, { me: { can_view_audits: true, can_view_isms: true } });
  assert.deepEqual(taskLabels(reader.el('homeTasks')), [
    'Review audits',
    'Review effectiveness measures',
    'Review documents and assets',
  ]);
  assert.equal(reader.el('homeSearch').hidden, true);

  const manager = await home(t, {
    me: {
      can_manage_audits: true,
      can_view_audits: true,
      can_view_isms: true,
      can_manage_isms: true,
    },
  });
  assert.deepEqual(taskLabels(manager.el('homeTasks')), [
    'Plan or run an audit',
    'Record an effectiveness measurement',
    'Manage documents and assets',
  ]);

  const nobody = await home(t, { me: {} });
  assert.deepEqual(taskLabels(nobody.el('homeTasks')), []);
  assert.match(nobody.el('homeTasks').textContent, /No shortcuts are available for your role/);
});

test('evidence search opens the event list for the current framework', async (t) => {
  const location = {
    origin: 'https://keen.example',
    href: 'https://keen.example/index.html?framework=A',
  };
  const ui = await home(t, { me: { can_view_events: true }, globals: { location } });
  ui.el('homeQ').value = '  a&b <script>  ';
  ui.el('homeSearch').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.equal(location.href, '/events.html?q=a%26b+%3Cscript%3E&framework=A');
  ui.el('homeQ').value = '   ';
  ui.el('homeSearch').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.equal(location.href, '/events.html?framework=A');
});

test('a stale cached home page reloads once with a revision marker, then explains the mismatch', async (t) => {
  const replaced = [];
  const stale = await home(t, {
    htmlName: 'events',
    globals: {
      location: {
        href: 'https://keen.example/?framework=B#explore',
        replace: (u) => replaced.push(u),
      },
    },
  });
  assert.deepEqual(replaced, ['/?framework=B&__keen_home=20261026-home-5#explore']);
  assert.equal(stale.calls.length, 0);

  const looping = await home(t, {
    htmlName: 'events',
    globals: {
      location: {
        href: 'https://keen.example/?__keen_home=20261026-home-5',
        replace: (u) => replaced.push(u),
      },
    },
  });
  assert.equal(replaced.length, 1);
  const alert = looping.document.querySelector('[role="alert"].alert-warning');
  assert.match(alert.textContent, /from different UI versions/);
});

test('without a chosen framework the home page uses the catalogue default', async (t) => {
  const ui = await home(t, { vendor: { getCurrentFramework: () => '' } });
  assert.equal(getsTo(ui, '/api/v1/frameworks').length >= 1, true);
  // The catalog is fetched once and shared by the framework lookup and the
  // overview; the activity chart and shortcuts carry the resolved framework.
  const risks = [...ui.document.querySelectorAll('#homeTasks a.home-task')].find((a) =>
    a.getAttribute('href').includes('/risks.html'),
  );
  assert.match(risks.getAttribute('href'), /framework=A$/);
  assert.match(getsTo(ui, '/api/v1/charts/event_volume_timeseries')[0].url, /framework=A\&/);
});
