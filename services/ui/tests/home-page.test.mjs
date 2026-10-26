import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';
const overview = {
  framework: 'A',
  control_count: 2,
  clause_count: 1,
  events: { mapped: 8, unmapped: 3 },
  attention: [{ href: '/audits.html', label: 'Audit due', detail: 'Review soon' }],
  counts: { risks: 1 },
};
const timeline = {
  days: 30,
  interval: 'day',
  items: [
    { bucket: '2026-10-01', total: 4, mapped: 3, unmapped: 1 },
    { bucket: '2026-10-02', total: 0, mapped: 0, unmapped: 0 },
    { bucket: '2026-10-03', total: 9, mapped: 9, unmapped: 0 },
  ],
  sources: [
    { source: 'forgejo', label: 'Forgejo', color: '#c73e1d', total: 40 },
    { source: HOSTILE_KEY(), label: HOSTILE(), color: 'not-a-hex', total: 12 },
    { source: 'legacy', label: 'Legacy collector', color: '#5b8def', total: 7 },
  ],
};
function HOSTILE() {
  return '<img src=x onerror="globalThis.pwned=1">';
}
function HOSTILE_KEY() {
  return 'file:"src';
}
async function fixture(t, options = {}) {
  return page(t, 'home', {
    htmlName: 'index',
    ...options,
    setup(window) {
      window.matchMedia = () => ({
        matches: true,
        addEventListener() {},
        removeEventListener() {},
      });
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/frameworks')
        return { items: [{ slug: 'A', name: 'Framework A', control_count: 2, has_clauses: true }] };
      if (u.pathname === '/api/v1/home') {
        if (options.homeFail) throw new Error('Offline');
        return overview;
      }
      if (u.pathname === '/api/v1/charts/event_volume_timeseries') {
        if (options.timelineFail === '403') throw new Error('403 events.read permission required');
        if (options.timelineFail) throw new Error('Offline');
        return options.timeline || timeline;
      }
      if (u.pathname === '/api/v1/home/controls') return { items: [], sections: [], total: 0 };
      throw new Error('Unexpected GET ' + u);
    },
  });
}
test('home overview renders evidence counts and attention links', async (t) => {
  const ui = await fixture(t);
  assert.match(ui.el('homeKpis').textContent, /8/);
  assert.match(ui.el('homeKpis').textContent, /Unmapped events/);
  assert.match(ui.el('homeAttention').textContent, /3 unmapped events/);
  assert.match(ui.el('homeAttention').textContent, /Audit due/);
  assert.ok(ui.document.querySelector('.home-task'));
});
test('the activity band charts the 30-day overview and the busiest sources', async (t) => {
  const ui = await fixture(t);
  assert.equal(ui.el('homeActivity').hidden, false);
  // The sparkline is an SVG over the existing event volume endpoint's buckets.
  assert.ok(ui.document.querySelector('#homeSpark .home-spark-svg'));
  assert.match(
    ui.el('homeSpark').getAttribute('aria-label'),
    /peaking at 9 on 2026-10-03; 13 collected and 12 mapped/,
  );
  assert.match(
    ui.el('homeSparkNote').textContent,
    /13 events collected · 12 mapped to this framework/,
  );
  // Busiest sources reuse the chart payload's own labels, tints and totals.
  const rows = [...ui.el('homeSources').querySelectorAll('a')];
  assert.deepEqual(
    rows.map((a) => [a.getAttribute('href'), a.textContent]),
    [
      ['/events.html?source=forgejo&framework=A', 'Forgejo40'],
      ['/events.html?source=file%3A%22src&framework=A', HOSTILE() + '12'],
      ['/events.html?source=legacy&framework=A', 'Legacy collector7'],
    ],
  );
  // A tint is only inlined when it is a plain sRGB hex colour.
  assert.equal(rows[1].querySelector('.home-source-dot').getAttribute('style'), null);
  assert.equal(
    rows[0].querySelector('.home-source-dot').getAttribute('style'),
    'background:#c73e1d',
  );
  // No markup from hostile label text reaches the document.
  assert.equal(ui.el('homeSources').querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  assert.equal(
    ui.el('homeSourcesMore').querySelector('a').getAttribute('href'),
    '/sources.html?framework=A',
  );
});
test('the activity band is absent without evidence access and quiet when empty', async (t) => {
  const reader = await fixture(t, { me: { can_view_audits: true } });
  assert.equal(reader.el('homeActivity').hidden, true);
  // No activity request is made for an account that cannot read events.
  assert.equal(
    reader.calls.filter((c) => new URL(c.url, 'https://keen.example').pathname.includes('charts'))
      .length,
    0,
  );
  const empty = await fixture(t, {
    timeline: { interval: 'day', items: [], sources: [] },
  });
  assert.equal(empty.el('homeActivity').hidden, false);
  assert.match(empty.el('homeSparkNote').textContent, /No events collected in the last 30 days/);
  assert.equal(empty.el('homeSpark').childElementCount, 0);
  assert.equal(empty.el('homeActivitySources').hidden, true);
});
test('the activity band fails quietly without hiding the workspace', async (t) => {
  const offline = await fixture(t, { timelineFail: true });
  assert.equal(offline.el('homeActivity').hidden, false);
  assert.match(offline.el('homeSparkNote').textContent, /could not be loaded/);
  assert.equal(offline.el('homeSpark').childElementCount, 0);
  assert.ok(offline.el('homeTasks').querySelector('a'));
  assert.match(offline.el('homeAttention').textContent, /Audit due/);

  const forbidden = await fixture(t, { timelineFail: '403' });
  assert.equal(forbidden.el('homeActivity').hidden, true);
});
test('shortcuts render every role-gated destination in the compact grid', async (t) => {
  const ui = await fixture(t);
  const list = ui.el('homeTasks');
  const labels = [...list.querySelectorAll(':scope > .home-task span')].map((s) => s.textContent);
  assert.deepEqual(labels, [
    'Review evidence',
    'Create an evidence mapping',
    'Plan or run an audit',
    'Review the risk register',
    'Record an effectiveness measurement',
    'Manage documents and assets',
  ]);
  // No gradation, no discloser: the card exposes the full advertised list.
  assert.equal(list.querySelector('details.home-task-more'), null);
  for (const link of list.querySelectorAll('a.home-task'))
    assert.match(link.getAttribute('href'), /\bframework=A\b/);
});
