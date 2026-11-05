// The sources register: a single named source filter, coverage filters,
// an installation with no evidence yet, the filter form and tab deep links.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const items = () => [
  {
    source: 'github',
    label: 'GitHub',
    color: '#000000',
    total_events: 12,
    mapped_events: 10,
    unmapped_events: 2,
    last_seen: '2026-05-01T00:00:00Z',
  },
  {
    source: 'loki',
    label: 'Loki',
    color: '#ff0000',
    total_events: 4,
    mapped_events: 0,
    unmapped_events: 4,
    last_seen: null,
  },
];

async function sources(t, { query = '', data = items(), shown } = {}) {
  const ui = await page(t, 'sources', {
    query,
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown?.push(el.id) }) },
      };
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/sources') return { total: data.length, items: data };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('rows').querySelector('td'));
  const shownSources = () =>
    [...ui.el('rows').querySelectorAll('td[data-sort]:first-child')].map((td) => td.dataset.sort);
  return { ui, shownSources };
}

test('a filter naming one known source shows that source by its label', async (t) => {
  const { ui, shownSources } = await sources(t, { query: '&source=github' });
  assert.deepEqual(shownSources(), ['github']);
  assert.match(ui.el('sourcesFilter').textContent, /Filtered: GitHub/);
  assert.equal(
    ui.el('sourcesFilter').querySelector('a').getAttribute('href'),
    '/sources.html?framework=A',
  );
});

test('the mapped coverage filter leaves out sources with nothing mapped', async (t) => {
  const { ui, shownSources } = await sources(t);
  ui.el('sourcesCoverage').value = 'mapped';
  ui.el('sourcesCoverage').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(shownSources(), ['github']);
  assert.match(ui.el('sourcesFilter').textContent, /Filtered: with mapped events/);
  ui.el('sourcesCoverage').value = 'unmapped';
  ui.el('sourcesCoverage').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(shownSources(), ['github', 'loki']);
});

test('an installation without evidence explains where sources come from', async (t) => {
  const { ui } = await sources(t, { data: [] });
  assert.equal(ui.el('rows').textContent.trim(), 'No sources found. Ingest some data first.');
  assert.equal(ui.el('sourcesFilter').textContent, '');
});

test('submitting the filter form applies the typed name', async (t) => {
  const { ui, shownSources } = await sources(t);
  ui.el('sourcesSearch').value = ' LOK ';
  ui.el('sourcesFilters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.deepEqual(shownSources(), ['loki']);
  assert.equal(new URL(ui.window.location.href).searchParams.get('q'), 'LOK');
});

test('a link to the visualisations tab opens it, after each reload too', async (t) => {
  const shown = [];
  const { ui } = await sources(t, { query: '&tab=Visualizations', shown });
  await settle(() => shown.length === 1);
  assert.deepEqual(shown, ['sourcesVisualisationsTab']);
  ui.el('reload').click();
  await settle(() => shown.length === 2);
  assert.deepEqual(shown, ['sourcesVisualisationsTab', 'sourcesVisualisationsTab']);
  // Without a tab in the address the register stays open.
  const plain = [];
  await sources(t, { shown: plain });
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(plain, []);
});
