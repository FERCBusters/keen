import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

test('a disabled child hides its tab and link without hiding sibling navigation', async () => {
  const ui = await loadUi('pages/feature-availability.js', {
    url: 'https://keen.example/admin.html',
    html: '<div id="navbar"><li class="nav-item dropdown"><ul><li><a href="/swot.html">SWOT</a></li><li><a href="/isms.html?tab=objectives">Objectives</a></li></ul></li></div><button id="tab" data-feature="swot" data-bs-target="#pane">SWOT</button><section id="pane"></section>',
  });
  try {
    ui.api.applyFeatures({ features: { enabled: { swot: false } } });
    const doc = ui.window.document;
    assert.ok(
      doc.querySelector('a[href="/swot.html"]').parentElement.hasAttribute('data-feature-hidden'),
    );
    assert.ok(!doc.querySelector('.nav-item').hasAttribute('data-feature-hidden'));
    assert.ok(
      !doc.querySelector('a[href*="objectives"]').parentElement.hasAttribute('data-feature-hidden'),
    );
    assert.ok(doc.getElementById('pane').hasAttribute('data-feature-hidden'));
    const link = doc.createElement('a');
    link.href = '/swot.html';
    doc.body.append(link);
    await settle(() => link.hasAttribute('data-feature-hidden'));
    ui.api.applyFeatures({ features: { enabled: { swot: true } } });
    assert.ok(!link.hasAttribute('data-feature-hidden'));
  } finally {
    ui.close();
  }
});

test('direct disabled pages stop before feature page initialisation', async () => {
  const ui = await loadUi('pages/feature-availability.js', {
    url: 'https://keen.example/swot.html?id=1',
    html: '<main><form>Editor</form></main>',
  });
  try {
    assert.throws(
      () => ui.api.applyFeatures({ features: { enabled: { swot: false } } }),
      /Feature disabled/,
    );
    assert.equal(ui.window.document.querySelector('form'), null);
    assert.match(ui.window.document.querySelector('main').textContent, /records are retained/);
  } finally {
    ui.close();
  }
});

test('administrator switches save versioned booleans and render untrusted labels as text', async () => {
  let config = {
    version: 3,
    configured: { isms: true, swot: true },
    enabled: { isms: true, swot: true },
    items: [
      { key: 'isms', label: 'ISMS', parent: null },
      { key: 'swot', label: '<img src=x onerror=alert(1)>', parent: 'isms' },
    ],
  };
  const writes = [];
  const ui = await loadUi('pages/admin-features.js', {
    url: 'https://keen.example/admin.html#features',
    html: '<li><button id="tab-features"></button></li><div id="feature-settings"></div><div id="feature-status" hidden></div><span id="feature-version"></span>',
    vendor: {
      apiGet: async () => config,
      apiPut: async (url, payload) => {
        writes.push({ url, payload });
        config = {
          ...config,
          version: 4,
          configured: { isms: false, swot: true },
          enabled: { isms: false, swot: false },
        };
        return config;
      },
      toast: (el, message) => {
        el.textContent = message;
      },
    },
  });
  try {
    ui.api.mountFeatures({ is_admin: true });
    const doc = ui.window.document;
    await settle(() => doc.getElementById('feature-isms'));
    assert.equal(doc.querySelector('img'), null);
    doc.getElementById('feature-isms').click();
    await settle(() => doc.getElementById('feature-version').textContent.includes('4'));
    assert.equal(writes[0].url, '/api/v1/admin/features');
    assert.equal(writes[0].payload.version, 3);
    assert.equal(writes[0].payload.changes.isms, false);
    assert.equal(doc.getElementById('feature-swot').checked, true);
    assert.equal(doc.getElementById('feature-swot').disabled, true);
    assert.equal(doc.getElementById('feature-status').hidden, false);
  } finally {
    ui.close();
  }
});

test('non-administrators never fetch configuration or receive switches', async () => {
  const ui = await loadUi('pages/admin-features.js', {
    html: '<li><button id="tab-features"></button></li><div id="feature-settings"></div>',
  });
  try {
    ui.api.mountFeatures({ is_admin: false });
    assert.equal(ui.window.document.querySelector('li').hidden, true);
    assert.equal(ui.window.document.querySelector('input'), null);
  } finally {
    ui.close();
  }
});

test('a rejected save restores confirmed switches and exposes the conflict', async () => {
  const config = {
    version: 2,
    configured: { swot: true },
    enabled: { swot: true },
    items: [{ key: 'swot', label: 'SWOT', parent: null }],
  };
  const ui = await loadUi('pages/admin-features.js', {
    url: 'https://keen.example/admin.html#features',
    html: '<li><button id="tab-features"></button></li><div id="feature-settings"></div><div id="feature-status" hidden></div><span id="feature-version"></span>',
    vendor: {
      apiGet: async () => config,
      apiPut: async () => {
        throw new Error('409 configuration changed');
      },
      toast: (node, message) => {
        node.textContent = message;
      },
    },
  });
  try {
    ui.api.mountFeatures({ is_admin: true });
    const doc = ui.window.document;
    await settle(() => doc.getElementById('feature-swot'));
    doc.getElementById('feature-swot').click();
    await settle(() => doc.getElementById('feature-status').textContent.includes('409'));
    await settle(() => !doc.getElementById('feature-swot').disabled);
    assert.equal(doc.getElementById('feature-swot').checked, true);
  } finally {
    ui.close();
  }
});

// Bootstrap can activate the tab while admin.js is awaiting initNavbar(),
// before either the load listener or the URL-hash listener has been attached.
test('Features already active before mounting loads without relying on the URL hash', async () => {
  let reads = 0;
  const ui = await loadUi('pages/admin-features.js', {
    url: 'https://keen.example/admin.html#overview',
    html: '<li><button id="tab-features" class="active" aria-selected="true"></button></li><div id="feature-settings"></div><div id="feature-status" hidden></div><span id="feature-version"></span>',
    vendor: {
      apiGet: async () => {
        reads++;
        return {
          version: 1,
          configured: { swot: true },
          enabled: { swot: true },
          items: [{ key: 'swot', label: 'SWOT', parent: null }],
        };
      },
      toast: (node, message) => {
        node.textContent = message;
      },
    },
  });
  try {
    ui.api.mountFeatures({ is_admin: true });
    await settle(() => ui.window.document.getElementById('feature-swot'));
    assert.equal(reads, 1);
  } finally {
    ui.close();
  }
});

test('returning to Features refreshes once and keeps switches during loading', async () => {
  let reads = 0;
  let release;
  const config = {
    version: 1,
    configured: { swot: true },
    enabled: { swot: true },
    items: [{ key: 'swot', label: 'SWOT', parent: null }],
  };
  const ui = await loadUi('pages/admin-features.js', {
    url: 'https://keen.example/admin.html#features',
    html: '<li><button id="tab-features" class="active"></button></li><button id="features-reload"></button><div id="feature-settings"></div><div id="feature-status" hidden></div><span id="feature-version"></span>',
    vendor: {
      apiGet: async () => {
        reads++;
        if (reads > 1)
          await new Promise((resolve) => {
            release = resolve;
          });
        return config;
      },
      toast: (node, message) => {
        node.textContent = message;
      },
    },
  });
  try {
    const doc = ui.window.document;
    ui.api.mountFeatures({ is_admin: true });
    doc.getElementById('tab-features').dispatchEvent(new ui.window.Event('shown.bs.tab'));
    await settle(() => doc.getElementById('feature-swot'));
    assert.equal(reads, 1);
    doc.getElementById('tab-features').dispatchEvent(new ui.window.Event('shown.bs.tab'));
    assert.equal(doc.getElementById('feature-swot').disabled, true);
    assert.equal(doc.getElementById('features-reload').disabled, true);
    assert.match(doc.getElementById('feature-status').textContent, /Loading/);
    ui.window.dispatchEvent(new ui.window.PageTransitionEvent('pageshow', { persisted: true }));
    assert.equal(reads, 2);
    release();
    await settle(() => !doc.getElementById('feature-swot').disabled);
    ui.window.dispatchEvent(new ui.window.PageTransitionEvent('pageshow', { persisted: true }));
    assert.equal(reads, 3);
    release();
    await settle(() => !doc.getElementById('features-reload').disabled);
  } finally {
    ui.close();
  }
});
