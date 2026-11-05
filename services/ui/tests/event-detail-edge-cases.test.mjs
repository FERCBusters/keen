// The event page with sparse records, addresses without a framework, tabs
// used in other orders, the diary editor's control search, and actions taken
// before an event has loaded.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

class FakeModal {
  static instances = new Map();
  constructor(el) {
    this.el = el;
    FakeModal.instances.set(el, this);
  }
  static getOrCreateInstance(el) {
    return FakeModal.instances.get(el) || new FakeModal(el);
  }
  show() {
    this.el.dataset.shown = '1';
  }
  hide() {
    this.el.dataset.shown = '0';
  }
}

// An event as the API sends it: its controls carry no framework of their own.
function apiEvent(overrides = {}) {
  return {
    id: 'e1',
    source: 'ci',
    summary: 'Deploy',
    timestamp: '2026-02-01T10:00:00Z',
    connection_id: null,
    connection_name: null,
    system: null,
    actor: null,
    action: null,
    outcome: null,
    severity: null,
    source_url: null,
    raw_pointer: {},
    normalized_payload: {},
    controls: [],
    artifacts: [],
    incidents: [],
    ueu: null,
    ...overrides,
  };
}

const DIARY = {
  source: 'diary',
  summary: 'diary: Weekly check',
  normalized_payload: { summary: 'Weekly check' },
  controls: [{ id: 'c1', ref: 'A.1', title: 'Access', type: 'control', rationale: 'manual' }],
};

async function eventPage(t, options = {}) {
  const state = { patched: [], posted: [], removed: [], gets: [] };
  const ui = await page(t, 'event', {
    query: options.query ?? '&id=e1',
    me: options.me ?? { is_admin: true, can_create_incidents: true, can_delete_incidents: true },
    vendor: options.vendor,
    confirm: options.confirm,
    setup(window) {
      window.bootstrap = { Modal: FakeModal, ...options.bootstrap };
      if (options.address) window.history.replaceState(null, '', options.address);
      options.setup?.(window);
    },
    get: async (u) => {
      state.gets.push(u.pathname + u.search);
      const p = u.pathname;
      if (p === '/api/v1/me') return structuredClone(options.me);
      if (p === '/api/v1/events/e1') {
        if (options.eventFail) throw new Error('Event offline');
        return apiEvent(options.event);
      }
      if (p === '/api/v1/events/e1/questions') return { threads: [] };
      if (p === '/api/v1/events/e1/control-suggestions')
        return { framework: 'A', items: options.suggestions ?? [] };
      if (p === '/api/v1/stories/related/event/e1') return { items: options.stories ?? [] };
      if (p === '/api/v1/stories') return { items: [] };
      if (p === '/api/v1/audits/by-event/e1') return { event_id: 'e1', items: [] };
      if (p === '/api/v1/audits')
        return { items: options.audits ?? [{ id: 'a1', title: 'Annual', status: 'open' }] };
      if (p === '/api/v1/controls') return { items: options.controls ?? [] };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      return { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      return {};
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(() => ui.el('normalized').textContent || ui.el('status').textContent);
  await settle(() => ui.idle());
  return { ui, state };
}

test('showing a tab writes it into the address, and the snapshot clears it', async (t) => {
  const { ui } = await eventPage(t);
  ui.el('eventAuditsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'audits');
  ui.el('eventIncidentsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'incidents');
  ui.el('eventSnapshotTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  const url = new URL(ui.window.location.href);
  assert.equal(url.searchParams.has('tab'), false);
  assert.equal(url.searchParams.get('id'), 'e1');
});

test('a tab named in the address stays closed while it is hidden or tabs cannot be switched', async (t) => {
  const shown = [];
  const Tab = { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) };
  // An event without incidents keeps the Incidents tab hidden.
  const { ui } = await eventPage(t, { query: '&id=e1&tab=incidents', bootstrap: { Tab } });
  assert.equal(ui.el('eventIncidentsTab').closest('li').hidden, true);
  assert.deepEqual(shown, []);
  // Without the tab script the page still loads the snapshot.
  const without = await eventPage(t, { query: '&id=e1&tab=questions' });
  assert.equal(without.ui.el('summary').textContent, 'Deploy');
  assert.equal(without.ui.el('status').textContent, '');
});

test('an event without a summary, or with an unnamed connection, is described plainly', async (t) => {
  const { ui, state } = await eventPage(t, {
    event: { summary: '', connection_id: 'conn-<9>' },
  });
  assert.equal(ui.el('summary').textContent, '(no summary)');
  const connection = ui.el('srcBadge').querySelector('.badge');
  assert.equal(connection.textContent, 'conn-<9>');
  assert.equal(connection.title, 'Connection: conn-<9>');
  // Sampling titles the evidence generically.
  ui.el('btnSampleAudit').click();
  await settle(() => ui.el('auditSampleSelect').value === 'a1');
  ui.el('auditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.title, 'Keen event evidence');
  // So does the incident form.
  ui.el('btnCreateIncident').click();
  assert.equal(ui.el('incidentTitle').value, 'Incident: Keen event incident');
});

test('sampling without the page address sends no evidence link', async (t) => {
  const { ui, state } = await eventPage(t);
  ui.el('btnSampleAudit').click();
  await settle(() => ui.el('auditSampleSelect').value === 'a1');
  ui.el('auditSampleUseCurrentUrl').checked = false;
  ui.el('auditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/audits/a1/evidence',
    body: { event_id: 'e1', evidence_url: null, notes: '', title: 'Deploy' },
  });
});

test('an address with an empty framework uses the current framework for links and requests', async (t) => {
  const { ui, state } = await eventPage(t, {
    address: '/event.html?id=e1&framework=',
    event: { controls: [{ id: 'c1', ref: 'A.1', title: 'Access', type: 'control' }] },
    stories: [
      {
        id: 's1',
        title: 'Q1',
        status: 'open',
        item_count: 1,
        updated_at: '2026-01-01',
        version: 1,
      },
    ],
  });
  assert.equal(ui.el('auditSamples').textContent, '-');
  ui.el('btnSampleAudit').click();
  await settle(() => ui.el('auditSampleSelect').value === 'a1');
  assert.equal(ui.el('auditSampleCreate').getAttribute('href'), '/audit-wizard.html?framework=A');
  assert.ok(state.gets.includes('/api/v1/audits?framework=A&limit=500'));
  ui.el('auditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.evidence_url, '/event.html?id=e1&framework=A');
  // The stories listing the event open in that framework.
  await settle(() => ui.el('eventStories').querySelector('a[href^="/story.html"]'));
  assert.equal(
    ui.el('eventStories').querySelector('a[href^="/story.html"]').getAttribute('href'),
    '/story.html?id=s1&framework=A',
  );
});

test('the diary editor searches the current framework when the address names none', async (t) => {
  const { ui, state } = await eventPage(t, {
    address: '/event.html?id=e1&framework=',
    event: DIARY,
  });
  ui.el('btnEditDiary').click();
  await settle(() => ui.el('editDiaryModal').dataset.shown === '1');
  assert.ok(state.gets.includes('/api/v1/controls?framework=A&limit=5000'));
});

test('without any framework the diary editor searches the default catalogue', async (t) => {
  // An installation with no framework enabled has no default to move to.
  const { ui, state } = await eventPage(t, {
    address: '/event.html?id=e1&framework=',
    vendor: {
      getCurrentFramework: () => '',
      getLoadedFrameworkCatalog: () => ({ items: [], default: '' }),
    },
    event: DIARY,
  });
  ui.el('btnEditDiary').click();
  await settle(() => ui.el('editDiaryModal').dataset.shown === '1');
  assert.ok(state.gets.includes('/api/v1/controls?framework=iso_27001_2022&limit=5000'));
});

test('the diary editor reads the catalogue once, finds at most 25 controls and names untitled ones by reference', async (t) => {
  const controls = [
    { id: 'u1', ref: 'Z.1', title: null, type: 'control' },
    ...Array.from({ length: 30 }, (_, i) => ({
      id: 'k' + i,
      ref: 'K.' + i,
      title: 'Key <b>' + i + '</b>',
      type: 'control',
    })),
  ];
  const { ui, state } = await eventPage(t, { event: DIARY, controls });
  const open = async () => {
    FakeModal.instances.get(ui.el('editDiaryModal'))?.hide();
    ui.el('btnEditDiary').click();
    await settle(() => ui.el('editDiaryModal').dataset.shown === '1');
  };
  await open();
  // A diary entry without details opens with an empty details field.
  assert.equal(ui.el('editDiaryDetails').value, '');
  const search = ui.el('editControlSearch');
  const results = () => [...ui.el('editControlResults').querySelectorAll('[data-add]')];
  search.value = 'key';
  search.dispatchEvent(new ui.window.Event('input'));
  assert.equal(results().length, 25);
  assert.equal(ui.el('editControlResults').querySelector('b'), null);
  // A control without a title is found by its reference only.
  for (const q of ['null', 'undefined']) {
    search.value = q;
    search.dispatchEvent(new ui.window.Event('input'));
    assert.equal(results().length, 0, q);
  }
  search.value = 'z.1';
  search.dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(
    results().map((b) => b.textContent.trim()),
    ['Z.1'],
  );
  assert.equal(results()[0].querySelector('.small-muted'), null);
  await open();
  assert.equal(state.gets.filter((g) => g.startsWith('/api/v1/controls?')).length, 1);
});

test('diary details written as a JSON list are saved as a list', async (t) => {
  const { ui, state } = await eventPage(t, { event: DIARY });
  ui.el('btnEditDiary').click();
  await settle(() => ui.el('editDiaryModal').dataset.shown === '1');
  ui.el('editDiaryDetails').value = '[1, "two"]';
  ui.el('editDiarySave').click();
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0].body.details, [1, 'two']);
  // The entry reloads once saved.
  await settle(
    () => state.gets.filter((g) => g.startsWith('/api/v1/events/e1?')).length === 2 && ui.idle(),
  );
  assert.equal(ui.el('editDiaryModal').dataset.shown, '0');
});

test('the diary editor does not open before the entry loads, for other events or for non-administrators', async (t) => {
  for (const options of [
    { eventFail: true },
    { event: { source: 'ci' } },
    { event: DIARY, me: { can_view_events: true } },
  ]) {
    const { ui, state } = await eventPage(t, options);
    // The button is hidden; activating it anyway opens nothing.
    assert.equal(ui.el('btnEditDiary').style.display, 'none');
    ui.el('btnEditDiary').click();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(ui.el('editDiaryModal').dataset.shown, undefined);
    assert.equal(state.gets.filter((g) => g.startsWith('/api/v1/controls?')).length, 0);
  }
});

test('a click on an incident outside its Delete button deletes nothing', async (t) => {
  const { ui, state } = await eventPage(t, {
    event: { incidents: [{ id: 'i1', title: 'Outage', text: 'Down for an hour' }] },
  });
  const incident = ui.el('incidents').querySelector('.incident-ref');
  incident.querySelector('.fw-bold').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(ui.confirmations, []);
  assert.deepEqual(state.removed, []);
  assert.ok(ui.el('incidents').querySelector('[data-incident-delete="i1"]'));
});

test('a suggestion without a title shows its reference, and one ticked control is named singly', async (t) => {
  const { ui } = await eventPage(t, {
    suggestions: [
      { id: 'c1', framework: 'A', ref: 'A.1', title: null, reason: 'Catalogue words: deploy.' },
      { id: 'c2', framework: 'A', ref: 'A.2', title: 'Change', reason: 'Catalogue words: change.' },
    ],
  });
  await settle(() => ui.el('suggestedControlsList').children.length === 2);
  const [untitled] = ui.el('suggestedControlsList').children;
  assert.equal(untitled.querySelector('a').textContent, 'A.1');
  assert.equal(untitled.querySelector('a + span').textContent, '');
  assert.doesNotMatch(untitled.textContent, /null/);
  const boxes = [...ui.el('suggestedControlsList').querySelectorAll('input[type="checkbox"]')];
  boxes[1].checked = false;
  boxes[1].dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.equal(
    ui.el('suggestedControlsPropose').textContent,
    'Propose a rule with 1 ticked control',
  );
});

test('with audits switched off the event lists no audit samples', async (t) => {
  const { ui, state } = await eventPage(t, {
    me: { is_admin: true, features: { enabled: { audits: false } } },
  });
  assert.equal(
    state.gets.some((g) => g.startsWith('/api/v1/audits')),
    false,
  );
  assert.equal(ui.el('auditSamples').textContent, '-');
});

test('the print view requests attachment thumbnails at once', async (t) => {
  let prints = 0;
  const loading = [];
  const { ui } = await eventPage(t, {
    query: '&id=e1&print=1',
    me: { is_admin: true },
    event: {
      artifacts: [
        { id: 'img1', kind: 'upload', content_type: 'image/png', size_bytes: 1 },
        { id: 'pdf1', kind: 'upload', content_type: 'application/pdf', size_bytes: 2 },
      ],
    },
    setup(window) {
      window.print = () => prints++;
      // Record each thumbnail's loading mode as the page inserts it.
      new window.MutationObserver((records) => {
        for (const record of records)
          for (const node of record.addedNodes)
            if (node.nodeType === 1)
              for (const img of node.querySelectorAll('img.evidence-img'))
                loading.push([img.getAttribute('src'), img.getAttribute('loading')]);
      }).observe(window.document, { childList: true, subtree: true });
    },
  });
  assert.deepEqual(loading, [
    ['/api/v1/artifacts/img1/download', 'eager'],
    ['/api/v1/artifacts/pdf1/preview', 'eager'],
  ]);
  // The thumbnails never arrive here; printing goes ahead once they fail.
  for (const img of ui.document.querySelectorAll('img.evidence-img'))
    img.dispatchEvent(new ui.window.Event('error'));
  await settle(() => prints === 1);
});
