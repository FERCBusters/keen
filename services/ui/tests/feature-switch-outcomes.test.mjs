// Feature switches on the administration page: the outcome of each save or
// load, and the navigation a disabled feature leaves behind.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const switchesHtml =
  '<li><button id="tab-features" class="active"></button></li><button id="features-reload"></button><div id="feature-settings"></div><div id="feature-status" hidden></div><span id="feature-version"></span>';

async function features(t, { get, put }) {
  const calls = [];
  const ui = await loadUi('pages/admin-features.js', {
    url: 'https://keen.example/admin.html#features',
    html: switchesHtml,
    vendor: {
      apiGet: async (url) => {
        calls.push(['GET', url]);
        return get(calls.filter(([method]) => method === 'GET').length);
      },
      apiPut: async (url, body) => {
        calls.push(['PUT', url, JSON.parse(JSON.stringify(body))]);
        return put(body);
      },
      toast: (node, message, kind) => {
        node.textContent = message;
        node.dataset.kind = kind;
      },
    },
  });
  t.after(ui.close);
  ui.api.mountFeatures({ is_admin: true });
  const el = (id) => ui.document.getElementById(id);
  return { ui, el, calls };
}

const swotOff = () => ({
  version: 5,
  configured: { swot: false },
  enabled: { swot: false },
  items: [{ key: 'swot', label: 'SWOT', parent: null }],
});

test('switching a feature on confirms that it was enabled', async (t) => {
  const { el, calls } = await features(t, {
    get: swotOff,
    put: () => ({ ...swotOff(), version: 6, configured: { swot: true }, enabled: { swot: true } }),
  });
  await settle(() => el('feature-swot'));
  assert.equal(el('feature-swot').checked, false);
  el('feature-swot').click();
  await settle(() => el('feature-status').dataset.kind === 'success');
  assert.deepEqual(calls.at(-1), [
    'PUT',
    '/api/v1/admin/features',
    { version: 5, changes: { swot: true } },
  ]);
  assert.equal(
    el('feature-status').textContent,
    'SWOT enabled. Settings saved. Reload the page to refresh navigation.',
  );
  assert.equal(el('feature-swot').checked, true);
  assert.equal(el('feature-version').textContent, 'Configuration revision 6');
});

test('a refused save whose reload also fails keeps the last confirmed switch', async (t) => {
  const { el, calls } = await features(t, {
    get: (reads) => {
      if (reads > 1) throw new Error('Features offline');
      return swotOff();
    },
    put: () => {
      throw new Error('409 configuration changed');
    },
  });
  await settle(() => el('feature-swot'));
  el('feature-swot').click();
  await settle(() => !el('feature-swot').disabled);
  // The save and the reload after it were both attempted.
  assert.deepEqual(
    calls.map(([method]) => method),
    ['GET', 'PUT', 'GET'],
  );
  assert.equal(
    el('feature-status').textContent,
    'Could not save: 409 configuration changed. Reload settings before trying again.',
  );
  assert.equal(el('feature-status').dataset.kind, 'danger');
  // The switch shows what the server last confirmed, not the refused change.
  assert.equal(el('feature-swot').checked, false);
  assert.equal(el('feature-version').textContent, 'Configuration revision 5');
  assert.equal(el('feature-settings').getAttribute('aria-busy'), 'false');
});

test('settings that cannot load say so and can be reloaded', async (t) => {
  const { el } = await features(t, {
    get: (reads) => {
      if (reads === 1) throw new Error('Features offline');
      return swotOff();
    },
    put: swotOff,
  });
  await settle(() => el('feature-status').dataset.kind === 'danger');
  assert.equal(
    el('feature-status').textContent,
    'Could not load features: Features offline. Use Reload settings to retry.',
  );
  assert.equal(el('feature-status').hidden, false);
  assert.equal(el('feature-settings').children.length, 0);
  assert.equal(el('features-reload').disabled, false);
  el('features-reload').click();
  await settle(() => el('feature-swot'));
  assert.equal(el('feature-status').hidden, true);
});

test('the question bell is hidden while questions are switched off', async () => {
  // The navigation bar's bell opens a menu and links nowhere itself.
  const ui = await loadUi('pages/feature-availability.js', {
    url: 'https://keen.example/admin.html',
    html: '<div id="navBellWrap"><button id="navBell" type="button" data-bs-toggle="dropdown">Questions</button></div>',
  });
  try {
    const bell = ui.document.getElementById('navBell');
    ui.api.applyFeatures({ features: { enabled: { questions: false } } });
    assert.equal(bell.dataset.feature, 'questions');
    assert.ok(bell.hasAttribute('data-feature-hidden'));
    ui.api.applyFeatures({ features: { enabled: { questions: true } } });
    assert.ok(!bell.hasAttribute('data-feature-hidden'));
  } finally {
    ui.close();
  }
});

async function tabs(html) {
  const shown = [];
  const ui = await loadUi('pages/feature-availability.js', {
    url: 'https://keen.example/events.html?framework=A',
    html,
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  return { ui, shown };
}

test('an open visualisations tab gives way to the first available tab beside it', async () => {
  const { ui, shown } = await tabs(
    '<ul class="nav nav-tabs" role="tablist"><li><button id="charts" class="active" data-feature="visualisations" data-bs-toggle="tab">Visualisations</button></li><li><button id="questions" data-feature="questions" data-bs-toggle="tab">Questions</button></li><li><button id="events" data-bs-toggle="tab">Events</button></li></ul>',
  );
  try {
    ui.api.applyFeatures({ features: { enabled: { visualisations: false, questions: false } } });
    // Hidden tabs are passed over.
    assert.deepEqual(shown, ['events']);
  } finally {
    ui.close();
  }
});

test('an open visualisations tab stays put when every tab beside it is switched off', async () => {
  const { ui, shown } = await tabs(
    '<ul class="nav nav-tabs" role="tablist"><li><button id="charts" class="active" data-feature="visualisations" data-bs-toggle="tab">Visualisations</button></li><li><button id="questions" data-feature="questions" data-bs-toggle="tab">Questions</button></li></ul>',
  );
  try {
    ui.api.applyFeatures({ features: { enabled: { visualisations: false, questions: false } } });
    assert.deepEqual(shown, []);
    assert.ok(
      ui.document.getElementById('charts').parentElement.hasAttribute('data-feature-hidden'),
    );
  } finally {
    ui.close();
  }
});
