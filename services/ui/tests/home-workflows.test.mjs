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
  { routes = {}, me = { is_admin: true }, reduced = true, setup, globals, htmlName = 'index' } = {},
) {
  const intervals = [];
  const ui = await page(t, 'home', {
    htmlName,
    me,
    globals,
    setup(window) {
      window.matchMedia = () => ({
        matches: reduced,
        addEventListener() {},
        removeEventListener() {},
      });
      // Compress the ticker's ambient delays; record the polling interval for manual firing.
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
      if (key === '/api/v1/evidence/latest') return { events: [] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  ui.intervals = intervals;
  ui.canvas = () => ui.el('explorerCanvas');
  ui.zoomCard = (kind, id) =>
    ui.canvas().querySelector(`[data-kind="${kind}"][data-id="${id}"] .js-zoom`);
  return ui;
}

// Layer changes ignore clicks until the transition finishes, so wait one macrotask.
const tick = () => new Promise((r) => setTimeout(r, 10));
async function goHome(ui) {
  ui.el('explorerRoot').click();
  await settle(() => ui.el('homeExplorer').hidden);
  await tick();
}

const getsTo = (ui, path) =>
  ui.calls.filter(
    (c) => c.method === 'GET' && new URL(c.url, 'https://keen.example').pathname === path,
  );
const assertNoInjectedMarkup = (ui, scope) => {
  assert.equal(scope.querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
};

test('the control tree drills from sections into a control and its connected records', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/home/controls': (u) =>
        u.searchParams.get('prefix')
          ? listOf([control('c1', { title: HOSTILE + 'Policies' })])
          : { groups: [{ id: 'A.5', count: 1 }], items: [] },
      '/api/v1/controls/c1': control('c1', {
        title: HOSTILE + 'Policies',
        description: 'Management direction',
        clauses: [{ clause_id: 'cl1', ref: '4.1', title: 'Context' }],
      }),
      '/api/v1/controls/c1/evidence': listOf([
        {
          event_id: 'e1',
          summary: 'Okta sign-in',
          source: 'okta',
          timestamp: '2026-01-02T03:04:05Z',
        },
      ]),
      '/api/v1/controls/c1/risks': listOf([
        {
          id: 'r1',
          asset: 'Database',
          threat_summary: 'Disclosure',
          risk_types: ['Confidentiality'],
        },
      ]),
      '/api/v1/controls/c1/interested-parties': listOf([
        { id: 'i1', name: { name: 'Customers' }, nature: { name: 'External' }, controls: [{}, {}] },
      ]),
      '/api/v1/controls/c1/effectiveness-measures': listOf([
        {
          id: 'm1',
          summary: 'Patch latency',
          metric_key: 'patch_days',
          target_display: '< 7 days',
        },
      ]),
    },
  });
  ui.document.querySelector('[data-kind="controls"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('control_section', 'A.5'));
  assert.equal(ui.el('homeExplorer').hidden, false);
  assert.equal(ui.el('homeOverview').hidden, true);
  assert.match(ui.canvas().textContent, /Section A\.5/);
  assert.match(ui.canvas().textContent, /1 controls · explore this section/);
  assert.equal(
    ui.canvas().querySelector('[data-kind="control_section"] a').getAttribute('href'),
    '/controls.html?q=A.5&framework=A',
  );

  ui.zoomCard('control_section', 'A.5').click();
  await settle(() => ui.zoomCard('control', 'c1'));
  assert.equal(getsTo(ui, '/api/v1/home/controls').at(-1).url.includes('prefix=A.5'), true);
  assert.match(ui.el('crumbs').textContent, /Controls.*Section A\.5/);
  assertNoInjectedMarkup(ui, ui.canvas());

  ui.zoomCard('control', 'c1').click();
  await settle(() => /Related risks/.test(ui.canvas().textContent));
  const canvas = ui.canvas();
  assertNoInjectedMarkup(ui, canvas);
  assert.match(canvas.querySelector('.home-node-heading').textContent, /^A\.5\.1 <img/);
  assert.match(canvas.querySelector('.home-node-header').textContent, /In scope/);
  assert.equal(
    canvas.querySelector('.home-node-header a').getAttribute('href'),
    '/control.html?id=c1&framework=A',
  );
  // Each related record links to its own page and only zoomable kinds offer Zoom in.
  assert.ok(ui.zoomCard('clause', 'cl1'));
  assert.ok(ui.zoomCard('risk', 'r1'));
  assert.ok(ui.zoomCard('interested_party', 'i1'));
  assert.ok(ui.zoomCard('effectiveness_measure', 'm1'));
  const evidence = canvas.querySelector('[data-kind="evidence"][data-id="e1"]');
  assert.equal(evidence.querySelector('.js-zoom'), null);
  assert.equal(evidence.querySelector('a').getAttribute('href'), '/event.html?id=e1&framework=A');
  assert.equal(evidence.querySelector('a').getAttribute('rel'), 'noopener noreferrer');
  assert.match(evidence.textContent, /2026-01-02T03:04:05Z · okta/);
  const party = canvas.querySelector('[data-kind="interested_party"]');
  assert.match(party.textContent, /Customers – External/);
  assert.match(party.textContent, /2 linked controls • 0 communications/);
  assert.match(
    canvas.querySelector('[data-kind="effectiveness_measure"]').textContent,
    /key: patch_days · target < 7 days/,
  );
  assert.match(canvas.querySelector('[data-kind="risk"]').textContent, /Confidentiality/);
  assert.ok(getsTo(ui, '/api/v1/controls/c1/risks')[0].url.includes('framework=A'));
  assert.ok(getsTo(ui, '/api/v1/controls/c1/evidence')[0].url.endsWith('limit=100'));
});

test('zoom out returns through the stack and Home restores the overview', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/risks': listOf([{ id: 'r1', asset: 'Database', threat_summary: 'Disclosure' }]),
      '/api/v1/risks/r1': {
        id: 'r1',
        asset_name: 'Database',
        threat_summary: 'Disclosure',
        risk_types: [],
      },
      '/api/v1/risks/r1/controls': listOf([control('c1', { in_scope: false })]),
      '/api/v1/controls/c1': null,
      '/api/v1/controls/c1/evidence': listOf([]),
      '/api/v1/controls/c1/risks': listOf([]),
      '/api/v1/controls/c1/interested-parties': listOf([]),
      '/api/v1/controls/c1/effectiveness-measures': listOf([]),
    },
  });
  assert.equal(ui.el('explorerBack').disabled, true);
  ui.document.querySelector('[data-kind="risks"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('risk', 'r1'));
  ui.zoomCard('risk', 'r1').click();
  await settle(() => ui.zoomCard('control', 'c1'));
  assert.match(ui.canvas().textContent, /Risk scenario/);
  assert.match(ui.canvas().textContent, /Out of scope/);
  assert.match(ui.el('crumbs').textContent, /Risk register.*Database – Disclosure/);
  ui.zoomCard('control', 'c1').click();
  await settle(() => ui.canvas().textContent.includes('Control not found.'));

  ui.el('explorerBack').click();
  await settle(() => ui.zoomCard('control', 'c1'));
  assert.equal(getsTo(ui, '/api/v1/risks/r1').length, 2);
  ui.el('explorerBack').click();
  await settle(() => ui.zoomCard('risk', 'r1'));
  ui.el('explorerBack').click();
  await settle(() => ui.el('homeExplorer').hidden);
  assert.equal(ui.el('homeOverview').hidden, false);
  assert.equal(ui.el('explorerBack').disabled, true);
  // With an empty stack, zoom out does nothing.
  ui.el('explorerBack').click();
  assert.equal(ui.el('homeExplorer').hidden, true);

  await tick();
  ui.document.querySelector('[data-kind="risks"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('risk', 'r1'));
  await goHome(ui);
  assert.equal(ui.el('explorerBack').disabled, true);
  assert.equal(ui.el('crumbs').textContent, 'Framework A');
});

test('clause layers show parent, children, controls and evidence', async (t) => {
  const clauses = [
    { id: 'cl1', ref: '4', title: 'Context' },
    { id: 'cl2', ref: '4.1', title: 'Understanding', parent_id: 'cl1', parent_ref: '4' },
    { id: 'cl3', ref: '4.1.1', title: 'Issues', parent_id: 'cl2', parent_ref: '4.1' },
  ];
  const ui = await home(t, {
    routes: {
      '/api/v1/clauses': (u) =>
        u.searchParams.get('limit') === '5000' ? listOf(clauses) : listOf([clauses[1]]),
      '/api/v1/clauses/cl2': clauses[1],
      '/api/v1/clauses/cl2/controls': listOf([control('c1')]),
      '/api/v1/events': (u) => {
        assert.equal(u.searchParams.get('clause'), 'cl2');
        return listOf([{ id: 'e1', summary: 'Register reviewed' }]);
      },
    },
  });
  ui.document.querySelector('[data-kind="clauses"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('clause', 'cl2'));
  assert.match(ui.canvas().textContent, /Under 4/);
  ui.zoomCard('clause', 'cl2').click();
  await settle(() => /Applicable controls/.test(ui.canvas().textContent));
  const sections = [...ui.canvas().querySelectorAll('.home-section')];
  const byKey = (key) => sections.find((s) => s.dataset.sectionKey === key);
  assert.ok(byKey('parent-clause').querySelector('[data-id="cl1"]'));
  assert.ok(byKey('clause-children').querySelector('[data-id="cl3"]'));
  assert.ok(byKey('clause-controls').querySelector('[data-id="c1"]'));
  assert.match(byKey('clause-evidence').textContent, /Register reviewed/);
  assert.match(ui.el('crumbs').textContent, /4\.1 Understanding/);
});

test('PESTLE(E), Interested Party and audit layers render their relationships', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/pestle/items': listOf([
        { id: 'p1', type: 'Legal', lens: 'External', item: 'Privacy law' },
      ]),
      '/api/v1/pestle/items/p1': {
        id: 'p1',
        type: 'Legal',
        lens: 'External',
        item: 'Privacy law',
        rationale: HOSTILE,
        overall_relevance: { label: 'High' },
        business_processes: [
          { business_process: { name: 'Payroll' }, relevance: { label: 'Medium' } },
          { business_process: { name: 'Sales' } },
        ],
        clauses: [
          { clause: { id: 'cl1', ref: '4.1', title: 'Context' }, relevance: { label: 'High' } },
        ],
        related_controls: [control('c1')],
      },
      '/api/v1/interested-parties': listOf([{ id: 'i1', name: 'Regulator', nature: 'Statutory' }]),
      '/api/v1/interested-parties/i1': {
        id: 'i1',
        name: { name: 'Regulator' },
        nature: { name: 'Statutory' },
        controls: [control('c2')],
        communications: [
          {
            event: 'Breach notice',
            with_whom: 'Commissioner',
            when: 'Within 72 hours',
            methods: ['Email', 'Portal'],
          },
        ],
      },
      '/api/v1/audits': listOf([{ id: 'a1', title: 'Annual audit', status: 'fieldwork' }]),
      '/api/v1/audits/a1': {
        id: 'a1',
        title: 'Annual audit',
        status: 'fieldwork',
        scoped_clauses: [{ id: 'cl1', ref: '4.1', title: 'Context' }],
        scoped_controls: [control('c3')],
        evidence: [
          { event_id: 'e1', title: 'Sampled login' },
          { id: 'manual-1', notes: 'Interview notes', added_at: '2026-03-01' },
        ],
      },
    },
  });
  ui.document.querySelector('[data-kind="pestle"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('pestle_item', 'p1'));
  ui.zoomCard('pestle_item', 'p1').click();
  await settle(() => /Business process relevance/.test(ui.canvas().textContent));
  assertNoInjectedMarkup(ui, ui.canvas());
  assert.match(
    ui.canvas().querySelector('.home-node-header').textContent,
    /Legal · External · High/,
  );
  const processes = [...ui.canvas().querySelectorAll('[data-kind="business_process"]')];
  assert.deepEqual(
    processes.map((p) => p.querySelector('.home-node-meta').textContent),
    ['Medium', '—'],
  );
  // Business processes are context-only leaves: no zoom and no link.
  assert.equal(processes[0].querySelector('.js-zoom, a'), null);
  assert.match(
    ui.canvas().querySelector('[data-kind="clause"]').textContent,
    /PESTLE\(E\) relevance: High/,
  );
  assert.ok(ui.zoomCard('control', 'c1'));

  await goHome(ui);
  ui.document.querySelector('[data-kind="interested_parties"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('interested_party', 'i1'));
  assert.match(ui.canvas().textContent, /Regulator – Statutory/);
  ui.zoomCard('interested_party', 'i1').click();
  await settle(() => /Communications/.test(ui.canvas().textContent));
  const comms = ui.canvas().querySelector('[data-kind="communication"]');
  assert.match(comms.textContent, /Breach notice \/ Commissioner/);
  assert.match(comms.textContent, /Within 72 hours · Email, Portal/);
  assert.equal(comms.querySelector('a'), null);
  assert.ok(ui.zoomCard('control', 'c2'));

  await goHome(ui);
  ui.document.querySelector('[data-kind="audits"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('audit', 'a1'));
  ui.zoomCard('audit', 'a1').click();
  await settle(() => /Sampled evidence/.test(ui.canvas().textContent));
  assert.ok(ui.zoomCard('clause', 'cl1'));
  assert.ok(ui.zoomCard('control', 'c3'));
  const sampled = [
    ...ui.canvas().querySelectorAll('[data-section-key="audit-evidence"] [data-kind="evidence"]'),
  ];
  assert.equal(sampled[0].querySelector('a').getAttribute('href'), '/event.html?id=e1&framework=A');
  // Manual audit evidence has no event page to open.
  assert.match(sampled[1].textContent, /Interview notes/);
  assert.equal(sampled[1].querySelector('a'), null);
});

test('effectiveness measures lead to metric sources and the source layer', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/isms/effectiveness-measures': listOf([{ id: 'm1', summary: 'Patch latency' }]),
      '/api/v1/isms/effectiveness-measures/m1': {
        id: 'm1',
        summary: 'Patch latency',
        target_display: '< 7 days',
        controls: [control('c1')],
        metric_entries: [
          {
            id: 'me1',
            value_display: '5 days',
            period: '2026-Q1',
            source_type: 'keen-agent',
            source_event_id: 'e1',
            notes: 'Within target',
          },
          // As the API sends it: value_display carries the qualitative value.
          {
            id: 'me2',
            metric_value: null,
            qualitative_value: 'Improving',
            value_display: 'Improving',
            source_type: 'other',
          },
          { id: 'me3', value_display: '6 days', source_type: 'keen-agent' },
        ],
      },
      '/api/v1/sources': listOf([{ source: 'keen-agent', label: 'KEEN agents', event_count: 3 }]),
      '/api/v1/events': (u) => {
        assert.equal(u.searchParams.get('source'), 'keen-agent');
        return listOf([{ id: 'e1', summary: 'Patch report', source: 'keen-agent' }]);
      },
      '/api/v1/sources/keen-agent/effectiveness-measures': listOf([
        { id: 'm1', summary: 'Patch latency' },
      ]),
    },
  });
  ui.document.querySelector('[data-kind="effectiveness_measures"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('effectiveness_measure', 'm1'));
  ui.zoomCard('effectiveness_measure', 'm1').click();
  await settle(() => /Recent metric entries/.test(ui.canvas().textContent));
  assert.match(ui.canvas().querySelector('.home-node-header').textContent, /Target < 7 days/);
  // Sources are de-duplicated and the generic "other" source type is not a KEEN source.
  const sources = [
    ...ui
      .canvas()
      .querySelectorAll('[data-section-key="effectiveness-sources"] [data-kind="source"]'),
  ];
  assert.deepEqual(
    sources.map((s) => s.dataset.id),
    ['keen-agent'],
  );
  assert.equal(
    sources[0].querySelector('a').getAttribute('href'),
    '/sources.html?source=keen-agent&framework=A',
  );
  const entries = [...ui.canvas().querySelectorAll('[data-kind="metric_entry"]')];
  assert.deepEqual(
    entries.map((e) => e.querySelector('.home-node-title').textContent),
    ['5 days', 'Improving', '6 days'],
  );
  assert.match(entries[0].textContent, /2026-Q1 · keen-agent/);
  assert.equal(
    entries[0].querySelector('a').getAttribute('href'),
    '/isms-effectiveness-metric.html?id=me1&framework=A',
  );
  assert.equal(entries[1].querySelector('a'), null);
  assert.equal(entries[0].querySelector('.js-zoom'), null);

  ui.zoomCard('source', 'keen-agent').click();
  await settle(() => /Events ingested from this source/.test(ui.canvas().textContent));
  assert.match(ui.el('crumbs').textContent, /Patch latency.*KEEN agents/);
  assert.match(ui.canvas().textContent, /Patch report/);
  assert.ok(ui.zoomCard('effectiveness_measure', 'm1'));
  // The source catalogue is global; the per-source queries carry the framework.
  assert.equal(getsTo(ui, '/api/v1/sources').at(-1).url.includes('framework='), false);
});

test('an unknown source still opens a layer keyed by its name', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/sources': (u) =>
        u.searchParams.get('limit')
          ? listOf([{ source: 'legacy-sync', event_count: 1 }])
          : listOf([]),
      '/api/v1/events': listOf([]),
      '/api/v1/sources/legacy-sync/effectiveness-measures': () => {
        throw new Error('Forbidden');
      },
    },
  });
  ui.document.querySelector('[data-kind="sources"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('source', 'legacy-sync'));
  ui.zoomCard('source', 'legacy-sync').click();
  await settle(() => /Recent evidence/.test(ui.canvas().textContent));
  assert.match(ui.el('crumbs').textContent, /legacy-sync$/);
  assert.equal(ui.canvas().querySelectorAll('.home-node-card').length, 0);
});

for (const [entry, kind, id, path, message] of [
  [
    'risks',
    'risk',
    'r9',
    '/api/v1/risks',
    'Risk not found, or you do not have permission to view it.',
  ],
  [
    'pestle',
    'pestle_item',
    'p9',
    '/api/v1/pestle/items',
    'PESTLE(E) item not found, or you do not have permission to view it.',
  ],
  [
    'interested_parties',
    'interested_party',
    'i9',
    '/api/v1/interested-parties',
    'Interested Party not found, or you do not have permission to view it.',
  ],
  [
    'audits',
    'audit',
    'a9',
    '/api/v1/audits',
    'Audit not found, or you do not have permission to view it.',
  ],
  [
    'effectiveness_measures',
    'effectiveness_measure',
    'm9',
    '/api/v1/isms/effectiveness-measures',
    'Effectiveness Measure not found, or you do not have permission to view it.',
  ],
  ['clauses', 'clause', 'cl9', '/api/v1/clauses', 'Clause not found.'],
])
  test(`a ${kind} the user cannot read shows a not-found layer`, async (t) => {
    const ui = await home(t, {
      routes: {
        [path]: listOf([{ id, title: 'Record ' + id }]),
        [`${path}/${id}`]: () => {
          throw new Error('403 Forbidden');
        },
        [`${path}/${id}/controls`]: listOf([]),
        '/api/v1/events': listOf([]),
      },
    });
    ui.document.querySelector(`[data-kind="${entry}"] .js-entry-zoom`).click();
    await settle(() => ui.zoomCard(kind, id));
    ui.zoomCard(kind, id).click();
    await settle(() => ui.canvas().textContent.includes(message));
  });

test('a failed framework structure request reports the error in place', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/home/controls': () => {
        throw new Error('Service unavailable');
      },
    },
  });
  ui.document.querySelector('[data-kind="controls"] .js-entry-zoom').click();
  await settle(() => ui.canvas().textContent.includes('Could not load this connection layer.'));
  assert.match(
    ui.el('status').textContent,
    /Could not load connection layer: Error: Service unavailable/,
  );
  assert.equal(ui.el('status').dataset.alert, 'danger');
});

test('empty collections and sections explain what is missing', async (t) => {
  const ui = await home(t, {
    routes: {
      '/api/v1/risks': listOf([]),
      '/api/v1/home/controls': { groups: [], items: [] },
    },
  });
  ui.document.querySelector('[data-kind="risks"] .js-entry-zoom').click();
  await settle(() =>
    /No risk register found, or you do not have permission/.test(ui.canvas().textContent),
  );
  await goHome(ui);
  ui.document.querySelector('[data-kind="controls"] .js-entry-zoom').click();
  await settle(() => ui.canvas().textContent.includes('No controls in this section.'));
});

test('large sections page locally and keep their page when revisited', async (t) => {
  const many = Array.from({ length: 150 }, (_, i) => control('c' + i, { ref: 'A.' + i }));
  const ui = await home(t, { routes: { '/api/v1/home/controls': { groups: [], items: many } } });
  ui.document.querySelector('[data-kind="controls"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('control', 'c0'));
  const summary = () => ui.canvas().querySelector('.home-pagination .small-muted').textContent;
  assert.equal(summary(), 'Showing 1–100 of 150');
  assert.equal(ui.canvas().querySelectorAll('.home-node-card').length, 100);
  const [prev, next] = ui.canvas().querySelectorAll('.js-section-page');
  assert.equal(prev.disabled, true);
  next.click();
  await settle(() => ui.zoomCard('control', 'c149'));
  assert.equal(summary(), 'Showing 101–150 of 150');
  assert.equal(ui.canvas().querySelectorAll('.home-node-card').length, 50);
  assert.equal(ui.canvas().querySelectorAll('.js-section-page')[1].disabled, true);
  ui.canvas().querySelectorAll('.js-section-page')[0].click();
  await settle(() => ui.zoomCard('control', 'c0'));
});

test('collections page through the server with offsets and step back from an empty page', async (t) => {
  const offsets = [];
  const ui = await home(t, {
    routes: {
      '/api/v1/risks': (u) => {
        const offset = Number(u.searchParams.get('offset'));
        offsets.push(offset);
        assert.equal(u.searchParams.get('limit'), '100');
        // The second page empties between the pager render and the click.
        if (offset === 100) return { items: [], total: 0 };
        return { items: [{ id: 'r' + offset, asset: 'Asset ' + offset }], total: 150 };
      },
    },
  });
  ui.document.querySelector('[data-kind="risks"] .js-entry-zoom').click();
  await settle(() => ui.zoomCard('risk', 'r0'));
  assert.match(ui.canvas().querySelector('.home-pagination').textContent, /Showing 1–100 of 150/);
  const next = ui.canvas().querySelector('.js-collection-page[data-pager="next"]');
  assert.equal(next.dataset.offset, '100');
  next.click();
  await settle(() => offsets.length === 3 && ui.zoomCard('risk', 'r0'));
  assert.deepEqual(offsets, [0, 100, 0]);
});

test('layer changes animate when reduced motion is not requested', async (t) => {
  const ui = await home(t, {
    reduced: false,
    routes: { '/api/v1/audits': listOf([{ id: 'a1', title: 'Annual audit', status: 'planned' }]) },
  });
  const entry = ui.document.querySelector('[data-kind="audits"]');
  assert.equal(entry.getAttribute('data-entering'), 'true');
  entry.querySelector('.js-entry-zoom').click();
  // A second click during the transition is ignored.
  entry.querySelector('.js-entry-zoom').click();
  await settle(() => ui.zoomCard('audit', 'a1'));
  assert.equal(
    ui.zoomCard('audit', 'a1').closest('.home-node-card').getAttribute('data-entering'),
    'true',
  );
  await settle(() => !ui.canvas().classList.contains('home-layer-incoming'));
  assert.equal(getsTo(ui, '/api/v1/audits').length, 1);
  ui.el('explorerBack').click();
  await settle(
    () =>
      ui.el('homeExplorer').hidden &&
      !ui.el('entryCards').classList.contains('home-layer-incoming'),
  );
});

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
  assert.equal(kpis.querySelector('[role="progressbar"]').getAttribute('aria-valuenow'), '70');
  assert.doesNotMatch(kpis.textContent, /Framework clauses|Events mapped/);
  const badges = [...ui.el('homeAttention').querySelectorAll('.badge')];
  assert.deepEqual(
    badges.map((b) => [
      b.textContent,
      b.classList.contains('text-bg-warning'),
      b.getAttribute('href'),
    ]),
    [
      ['3 control gaps', true, '/controls.html?gaps=1&framework=A'],
      ['0 overdue audits', false, '/audits.html?framework=A'],
      ['2 measures outside threshold', true, '/isms.html?tab=effectiveness&framework=A'],
      [
        '1 API collectors with failures · all frameworks',
        true,
        '/admin.html?framework=A#integrations',
      ],
    ],
  );
  assertNoInjectedMarkup(ui, ui.el('homeAttention'));
  assert.match(ui.el('homeAttention').textContent, /Audit overdue/);
  assert.match(ui.el('homeAttention').textContent, /Showing up to five items per category/);
});

test('overview without coverage or structure points to the next step', async (t) => {
  const noCoverage = await home(t, {
    routes: { '/api/v1/home': { control_count: 4, clause_count: 0, attention: [] } },
  });
  assert.match(noCoverage.el('homeKpis').textContent, /4 controls/);
  assert.match(noCoverage.el('homeKpis').textContent, /Explore this framework’s controls/);
  assert.equal(noCoverage.el('homeKpis').querySelector('[role="progressbar"]'), null);
  assert.match(noCoverage.el('homeAttention').textContent, /No attention items found/);

  const admin = await home(t, {
    routes: { '/api/v1/home': { control_count: 0, clause_count: 0 } },
  });
  assert.match(admin.el('homeKpis').textContent, /No entries yet/);
  assert.equal(
    admin.el('homeKpis').querySelector('a').getAttribute('href'),
    '/admin.html?framework=A#framework-editor',
  );
  // Without controls or clauses the explorer offers no framework entry points.
  assert.equal(admin.document.querySelector('[data-kind="controls"]'), null);
  assert.equal(admin.document.querySelector('[data-kind="clauses"]'), null);

  const reader = await home(t, {
    me: { can_view_audits: true },
    routes: { '/api/v1/home': { control_count: 0, clause_count: 0 } },
  });
  assert.equal(
    reader.el('homeKpis').querySelector('a').getAttribute('href'),
    '/help/using.html?framework=A',
  );
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
  fail = false;
  ui.el('homeRefresh').click();
  await settle(() => /5 events awaiting mapping/.test(ui.el('homeAttention').textContent));
  assert.equal(ui.el('homeRefresh').disabled, false);
  assert.equal(
    ui.el('homeAttention').querySelector('.home-attention-item').getAttribute('href'),
    '/events.html?unmapped=true&framework=A',
  );
  await settle(() => ui.document.querySelectorAll('[data-kind="controls"]').length === 1);
});

const taskLabels = (ui) =>
  [...ui.el('homeTasks').querySelectorAll('.home-task span')].map((s) => s.textContent);

test('home tasks follow the signed-in role', async (t) => {
  const admin = await home(t);
  assert.deepEqual(taskLabels(admin), [
    'Review evidence',
    'Create an evidence mapping',
    'Plan or run an audit',
    'Review the risk register',
    'Record an effectiveness measurement',
    'Manage documents and assets',
  ]);
  assert.equal(admin.el('homeSearch').hidden, false);

  const reader = await home(t, { me: { can_view_audits: true, can_view_isms: true } });
  assert.deepEqual(taskLabels(reader), [
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
  assert.deepEqual(taskLabels(manager), [
    'Plan or run an audit',
    'Record an effectiveness measurement',
    'Manage documents and assets',
  ]);

  const nobody = await home(t, { me: {} });
  assert.deepEqual(taskLabels(nobody), []);
  assert.match(nobody.el('homeTasks').textContent, /Explore the framework cards below/);
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

test('the latest-evidence ticker shows new events as text and polls after the newest one', async (t) => {
  const responses = [
    {
      events: [
        {
          id: 'e1',
          created_at: '2026-10-01T10:00:00Z',
          source: 'github',
          summary: 'Push to main',
          url: '/event.html?id=e1',
        },
        {
          id: 'e2',
          created_at: '2026-10-01T10:00:05Z',
          timestamp: '2026-10-01T09:59:00Z',
          source: 'okta',
          system: 'sso',
          action: 'login',
          summary: HOSTILE + ' signed in',
          url: '/event.html?id=e2',
        },
      ],
    },
    // A response repeating an event already shown is ignored.
    { events: [{ id: 'e2', source: 'okta', summary: 'Repeat' }] },
    { events: [{ event_id: 'e3', created_at: '2026-10-01T10:01:00Z', source: 'aws' }] },
  ];
  const requests = [];
  const ui = await home(t, {
    reduced: false,
    me: { can_view_events: true },
    routes: {
      '/api/v1/events': listOf([]),
      '/api/v1/sources': listOf([]),
      '/api/v1/evidence/latest': (u) => {
        requests.push(Object.fromEntries(u.searchParams));
        const next = responses.shift();
        if (!next) throw new Error('Polling failed');
        return next;
      },
    },
  });
  const text = () => ui.el('latestEvidenceTickerText').textContent;
  const link = () => ui.el('latestEvidenceTickerLink');
  await settle(() => text().startsWith('github'));
  assert.equal(ui.el('latestEvidenceTicker').classList.contains('d-none'), false);
  assert.equal(text(), 'github :: Push to main');
  assert.equal(link().getAttribute('href'), '/event.html?id=e1&framework=A');
  assert.equal(link().title, 'Open latest evidence');
  await settle(() => text().startsWith('okta'));
  assert.equal(text(), 'okta :: sso :: login :: ' + HOSTILE + ' signed in');
  assertNoInjectedMarkup(ui, ui.el('latestEvidenceTicker'));
  assert.equal(link().title, 'Open latest evidence from 2026-10-01T09:59:00Z');
  assert.deepEqual(requests[0], { limit: '5' });

  assert.deepEqual(
    ui.intervals.map((i) => i.ms),
    [30000],
  );
  await ui.intervals[0].fn();
  assert.deepEqual(requests[1], { limit: '5', after: '2026-10-01T10:00:05Z' });
  await ui.intervals[0].fn();
  assert.equal(requests[2].after, '2026-10-01T10:00:05Z');
  await settle(() => text().startsWith('aws'));
  assert.equal(text(), 'aws :: New evidence received');
  assert.equal(link().getAttribute('href'), '/event.html?id=e3&framework=A');
  // A failed poll leaves the last item in place.
  await ui.intervals[0].fn();
  assert.equal(requests[3].after, '2026-10-01T10:01:00Z');
  assert.equal(text(), 'aws :: New evidence received');
});

test('the ticker does not poll for users who cannot view events', async (t) => {
  const ui = await home(t, { me: { can_view_audits: true } });
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(getsTo(ui, '/api/v1/evidence/latest').length, 0);
  assert.equal(ui.intervals.length, 0);
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
  assert.deepEqual(replaced, ['/?framework=B&__keen_home=20261007-home-2#explore']);
  assert.equal(stale.calls.length, 0);

  const looping = await home(t, {
    htmlName: 'events',
    globals: {
      location: {
        href: 'https://keen.example/?__keen_home=20261007-home-2',
        replace: (u) => replaced.push(u),
      },
    },
  });
  assert.equal(replaced.length, 1);
  const alert = looping.document.querySelector('[role="alert"].alert-warning');
  assert.match(alert.textContent, /from different UI versions/);
});
