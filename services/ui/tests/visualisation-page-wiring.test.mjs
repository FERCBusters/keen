// How the charts fit their host page: a browser that refuses address updates,
// a tab plugin that fails, addresses with nothing else in them, a charts pane
// with its own name, a page's default chart and extra filters, shared Days,
// auto-apply with a cleared date or a pending change, charts turned off, and
// window resizes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { charts, pause, settle, twoSources } from './support/visualisation-charts.mjs';

test('when the browser refuses address updates, the charts still load, reload and reset', async (t) => {
  // Browsers refuse rapid history updates with a SecurityError for a while.
  let refusing = true;
  const ui = await charts(t, {
    setup(window) {
      window.history.replaceState(null, '', '/sources.html?mode=graph');
      const replace = window.history.replaceState.bind(window.history);
      window.history.replaceState = (...args) => {
        if (refusing)
          throw new window.DOMException('Too many calls to the History API', 'SecurityError');
        return replace(...args);
      };
    },
  });
  assert.equal(ui.el('status').textContent, 'Loaded.');
  assert.equal(ui.window.location.href, 'https://keen.example/sources.html?mode=graph');

  ui.input('vizBreakdown', 'system', 'change');
  await settle(() => ui.graphRequests().length === 2);
  assert.match(ui.graphRequests()[1].url, /&breakdown=system$/);

  ui.el('vizReset').click();
  await settle(() => ui.graphRequests().length === 3);
  assert.doesNotMatch(ui.graphRequests()[2].url, /breakdown/);
  assert.equal(ui.el('vizBreakdown').value, '');
  assert.equal(ui.window.location.href, 'https://keen.example/sources.html?mode=graph');

  // Once the browser accepts updates again, Reset clears the shared view.
  refusing = false;
  ui.el('vizReset').click();
  await settle(() => ui.graphRequests().length === 4);
  assert.equal(ui.window.location.href, 'https://keen.example/sources.html');
});

test('a mount outside any tab starts its charts at once and keeps the address', async (t) => {
  const shown = [];
  const ui = await charts(t, {
    active: false,
    query: '&tab=visualisations',
    setup(window) {
      const mount = window.document.querySelector('[data-keen-visualisation-mount]');
      window.document.getElementById('embeddedVisualisationsPane').replaceWith(mount);
      window.bootstrap = {
        Tab: { getOrCreateInstance: (button) => ({ show: () => shown.push(button.id) }) },
      };
    },
  });
  await settle(() => ui.el('status').textContent === 'Loaded.');
  assert.equal(ui.graphRequests().length, 1);
  assert.deepEqual(shown, []);
  // The address keeps what it had; there is no tab to mark.
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'visualisations');
  ui.el('sourcesRegisterTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'visualisations');
});

test('a tab plugin that fails to open the saved charts tab leaves the tab working', async (t) => {
  const ui = await charts(t, {
    htmlName: 'events',
    modes: null,
    active: false,
    query: '&tab=visualisations',
    setup(window) {
      window.bootstrap = {
        Tab: {
          getOrCreateInstance: () => {
            throw new Error('Tab plugin unavailable');
          },
        },
      };
    },
  });
  assert.equal(ui.series.length, 0);
  ui.el('eventsVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('status').textContent === 'Loaded.');
  assert.equal(ui.series.length, 1);
});

test('leaving the charts tab of an address with nothing else leaves a bare address', async (t) => {
  const ui = await charts(t, {
    htmlName: 'events',
    modes: null,
    active: false,
    setup(window) {
      window.history.replaceState(null, '', '/events.html');
    },
  });
  ui.el('eventsVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.window.location.href, 'https://keen.example/events.html?tab=visualisations');
  await settle(() => ui.el('status').textContent === 'Loaded.');
  ui.el('eventsRegisterTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.window.location.href, 'https://keen.example/events.html');
});

test('a charts pane with its own name waits for its tab, and keeps the source scope', async (t) => {
  const ui = await charts(t, {
    htmlName: 'source',
    modes: null,
    active: false,
    query: '&source=github',
  });
  await pause();
  assert.equal(ui.graphRequests().length, 0);
  ui.el('sourceVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('status').textContent === 'Loaded.');
  assert.match(ui.graphRequests()[0].url, /^\/api\/v1\/graph\/control_source\?.*&source=github$/);
  assert.equal(ui.el('vizScope').textContent, 'Scoped to the page filters: source github.');
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'visualisations');
});

test('a page can name the chart it opens with', async (t) => {
  const ui = await charts(t, { mount: { vizDefaultMode: 'heatmap' } });
  assert.equal(ui.el('vizSelect').value, 'heatmap');
  assert.equal(ui.document.querySelectorAll('#viz rect.heat-cell').length, 4);
});

test('a page filter without a label of its own is described by its parameter', async (t) => {
  const ui = await charts(t, {
    mode: 'graph',
    query: '&source=github&team=blue,red',
    mount: { vizFilterParams: 'source,team' },
  });
  assert.equal(
    ui.el('vizScope').textContent,
    'Scoped to the page filters: source github; teams blue, red.',
  );
  assert.match(ui.graphRequests()[0].url, /&source=github&team=blue%2Cred$/);
});

test('a shared link fills in its Days value', async (t) => {
  const ui = await charts(t, { mode: 'graph', query: '&days=30' });
  assert.equal(ui.el('histDays').value, '30');
});

test('with auto-apply, a cleared date waits for a whole range before reloading', async (t) => {
  const ui = await charts(t, {
    mode: 'graph',
    me: { is_admin: true, preferences: { auto_apply_filters: true } },
  });
  const change = (dmy, value) => {
    ui.el(dmy).value = value;
    ui.el(dmy.replace('Dmy', '')).dispatchEvent(new ui.window.Event('change'));
  };
  change('dateFromDmy', '');
  await pause();
  assert.equal(ui.graphRequests().length, 1);
  change('dateFromDmy', '2026-09-01');
  await settle(() => ui.graphRequests().length === 2);
  assert.match(ui.graphRequests()[1].url, /start_date=2026-09-01&/);
});

test('Reset cancels an automatic reload still waiting to run', async (t) => {
  const pending = [];
  const ui = await charts(t, {
    mode: 'graph',
    me: { is_admin: true, preferences: { auto_apply_filters: true } },
    vendor: {
      debounce: (fn, ms) => {
        let id = null;
        const run = (...args) => {
          clearTimeout(id);
          id = setTimeout(() => fn(...args), ms);
          pending.push(ms);
        };
        run.cancel = () => clearTimeout(id);
        return run;
      },
    },
  });
  ui.el('dateFromDmy').value = '2026-09-01';
  ui.el('dateFrom').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(pending, [500]);
  ui.el('vizReset').click();
  await settle(() => ui.graphRequests().length === 2);
  // The reload the date change scheduled would have run after 500ms.
  await pause(650);
  assert.equal(ui.graphRequests().length, 2);
  assert.doesNotMatch(ui.graphRequests()[1].url, /2026-09-01/);
});

test('with the charts turned off, their hidden controls load nothing', async (t) => {
  const ui = await charts(t, {
    mode: 'graph',
    wait: false,
    me: { is_admin: true, features: { enabled: { visualisations: false } } },
  });
  assert.ok(ui.el('embeddedVisualisationsPane').hasAttribute('data-feature-hidden'));
  ui.el('dateApply').click();
  ui.el('vizReset').click();
  ui.el('histApply').click();
  ui.input('vizBreakdown', 'actor', 'change');
  await pause();
  assert.equal(ui.graphRequests().length, 0);
});

test('two resizes within one frame redraw the chart once', async (t) => {
  const cancelled = [];
  let frames = 0;
  const ui = await charts(t, {
    mode: 'graph',
    graph: twoSources,
    globals: {
      requestAnimationFrame: (fn) => {
        frames += 1;
        return setTimeout(fn, 0);
      },
      cancelAnimationFrame: (id) => {
        cancelled.push(id);
        clearTimeout(id);
      },
    },
  });
  let draws = 0;
  new ui.window.MutationObserver((records) => {
    for (const r of records)
      for (const n of r.addedNodes) if (n.classList?.contains('links')) draws += 1;
  }).observe(ui.el('viz'), { childList: true });
  ui.window.dispatchEvent(new ui.window.Event('resize'));
  ui.window.dispatchEvent(new ui.window.Event('resize'));
  await pause();
  assert.equal(frames, 2);
  assert.equal(cancelled.length, 1);
  assert.equal(draws, 1);
});
