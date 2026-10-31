// The event details panel beside the Events list: a click on a row shows the
// essentials of that event and links to the full page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const EVENTS = [
  {
    id: 'e1',
    source: 'ci',
    summary: 'Deploy <b>v2</b>',
    timestamp: '2026-01-01T10:00:00Z',
    controls: [{ id: 'c1', ref: 'A.1', title: 'Access' }],
  },
  { id: 'e2', source: 'ci', summary: 'Rollback', timestamp: '2026-01-02T10:00:00Z', controls: [] },
];

function detail(id, overrides = {}) {
  return {
    id,
    source: 'ci',
    summary: 'Deploy <b>v2</b>',
    timestamp: '2026-01-01T10:00:00Z',
    system: 'build-7',
    actor: 'alice',
    action: 'deploy',
    outcome: 'success',
    severity: 2,
    connection_name: 'Main CI',
    source_url: 'https://ci.example/run/7',
    artifacts: [{ id: 'a1' }, { id: 'a2' }],
    normalized_payload: {
      repo: 'keen',
      branch: 'main',
      nested: { commit: 'abc123', author: { email: 'alice@example.test' } },
      skipped: ['list'],
    },
    controls: [
      {
        id: 'c1',
        ref: 'A.1',
        title: 'Access <i>x</i>',
        method: 'rule',
        confidence: 0.8,
        rationale: 'auto by rule deploys (A)',
        rule_id: 'deploys',
      },
      {
        id: 'c2',
        ref: 'B.2',
        title: 'Backups',
        method: 'cross_framework',
        confidence: 0.5,
        rationale: 'Shared <u>control</u>',
        inherited_from: { framework: 'ISO', ref: 'A.8.13' },
      },
    ],
    ...overrides,
  };
}

async function events(t, options = {}) {
  const requests = [];
  const ui = await page(t, 'events', {
    me: options.me || { is_admin: false },
    get: async (u) => {
      requests.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/sources/meta') return { items: [{ source: 'ci', label: 'CI' }] };
      if (u.pathname === '/api/v1/controls') return { items: [] };
      if (u.pathname === '/api/v1/events/facets') return { sources: [], controls: [], rules: [] };
      if (u.pathname === '/api/v1/events') return { total: 2, items: EVENTS };
      const m = u.pathname.match(/^\/api\/v1\/events\/(e\d)$/);
      if (m) {
        if (options.detailFail) throw new Error('Event offline');
        await new Promise((resolve) => setTimeout(resolve, options.delay?.[m[1]] || 0));
        return detail(
          m[1],
          m[1] === 'e2'
            ? { summary: 'Rollback', controls: [], normalized_payload: {} }
            : options.payload
              ? { normalized_payload: options.payload }
              : {},
        );
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('rows').querySelectorAll('tr[data-event-id]').length === 2);
  const panel = ui.el('eventPanel');
  const click = (el, init = {}) =>
    el.dispatchEvent(
      new ui.window.MouseEvent('click', { bubbles: true, cancelable: true, ...init }),
    );
  return { ...ui, requests, panel, click };
}

test('clicking a row opens the panel with the essentials and a link to the full event', async (t) => {
  const ui = await events(t);
  const row = ui.el('rows').querySelector('tr[data-event-id="e1"]');

  ui.click(row.querySelector('td'));

  assert.equal(ui.panel.classList.contains('show'), true);
  assert.equal(ui.panel.style.visibility, 'visible');
  await settle(() => /Deploy/.test(ui.panel.querySelector('[data-panel-title]').textContent));
  assert.ok(ui.requests.includes('/api/v1/events/e1?framework=A'));
  assert.equal(ui.panel.querySelector('[data-panel-title]').textContent, 'Deploy <b>v2</b>');
  assert.equal(ui.panel.querySelector('b'), null);
  assert.equal(
    ui.panel.querySelector('[data-panel-full]').getAttribute('href'),
    '/event.html?id=e1&framework=A',
  );

  const details = [...ui.panel.querySelectorAll('[data-panel-details] dt')].map((dt) => [
    dt.textContent,
    dt.nextElementSibling.textContent,
  ]);
  assert.deepEqual(details, [
    ['System', 'build-7'],
    ['Actor', 'alice'],
    ['Action', 'deploy'],
    ['Outcome', 'success'],
    ['Severity', '2'],
    ['Connection', 'Main CI'],
    ['Artifacts', '2'],
  ]);

  // Mapped controls show the reference and title only, linked to their pages;
  // method, confidence and rationale belong to the full event page.
  const controls = [...ui.panel.querySelectorAll('[data-panel-control]')];
  assert.deepEqual(
    controls.map((li) => [
      li.dataset.panelControl,
      li.querySelector('a').getAttribute('href'),
      li.textContent.replace(/\s+/g, ' ').trim(),
    ]),
    [
      ['A.1', '/control.html?id=c1&framework=A', 'A.1 Access <i>x</i>'],
      ['B.2', '/control.html?id=c2&framework=A', 'B.2 Backups'],
    ],
  );
  assert.equal(controls[0].querySelectorAll('a').length, 1);
  assert.equal(controls[0].querySelector('i'), null);
  for (const li of controls) {
    assert.doesNotMatch(li.textContent, /rule|80%|auto by rule|cross_framework|inherited|Shared/);
  }

  // Structured fields are flattened with readable labels; arrays are left out.
  const fields = [...ui.panel.querySelectorAll('[data-panel-fields] dt')].map((dt) => [
    dt.textContent,
    dt.nextElementSibling.textContent,
  ]);
  assert.deepEqual(fields, [
    ['repo', 'keen'],
    ['branch', 'main'],
    ['nested › commit', 'abc123'],
    ['nested › author › email', 'alice@example.test'],
  ]);
  assert.equal(ui.panel.querySelector('[data-panel-more]'), null);
});

test('the summary link opens the panel, while a modified click keeps the full page', async (t) => {
  const ui = await events(t);
  const summary = ui.el('rows').querySelector('tr[data-event-id="e2"] [data-event-summary]');

  const plain = new ui.window.MouseEvent('click', { bubbles: true, cancelable: true });
  summary.dispatchEvent(plain);
  assert.equal(plain.defaultPrevented, true);
  await settle(() => ui.panel.querySelector('[data-panel-title]').textContent === 'Rollback');
  assert.match(ui.panel.querySelector('[data-panel-controls]').textContent, /No mapped controls/);
  assert.match(ui.panel.querySelector('[data-panel-fields]').textContent, /No structured fields/);

  const modified = new ui.window.MouseEvent('click', {
    bubbles: true,
    cancelable: true,
    ctrlKey: true,
  });
  summary.dispatchEvent(modified);
  assert.equal(modified.defaultPrevented, false);
  // Other links in the row keep their own destination.
  const control = ui.el('rows').querySelector('tr[data-event-id="e1"] a[href^="/control.html"]');
  const controlClick = new ui.window.MouseEvent('click', { bubbles: true, cancelable: true });
  control.dispatchEvent(controlClick);
  assert.equal(controlClick.defaultPrevented, false);
  assert.equal(ui.requests.filter((r) => r.startsWith('/api/v1/events/e1')).length, 0);

  // Close hides the panel without the design system's offcanvas script.
  ui.panel.querySelector('[data-panel-close]').click();
  assert.equal(ui.panel.classList.contains('show'), false);
});

test('administrators see the same compact controls, and a long payload is cut short', async (t) => {
  const payload = {};
  for (let i = 0; i < 15; i++) payload[`field_${i}`] = `value ${i}`;
  const ui = await events(t, { me: { is_admin: true }, payload });
  ui.click(ui.el('rows').querySelector('tr[data-event-id="e1"] td'));
  await settle(() => ui.panel.querySelector('[data-panel-control]'));
  assert.equal(ui.panel.querySelector('[data-panel-control="A.1"] a[href^="/admin.html"]'), null);
  assert.equal(ui.panel.querySelectorAll('[data-panel-fields] dt').length, 12);
  assert.equal(
    ui.panel.querySelector('[data-panel-more]').textContent,
    '3 more in the full event.',
  );
});

test('a slower earlier request cannot overwrite the event opened last', async (t) => {
  const ui = await events(t, { delay: { e1: 60 } });
  ui.click(ui.el('rows').querySelector('tr[data-event-id="e1"] td'));
  ui.click(ui.el('rows').querySelector('tr[data-event-id="e2"] td'));
  await settle(() => ui.panel.querySelector('[data-panel-title]').textContent === 'Rollback');
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(ui.panel.querySelector('[data-panel-title]').textContent, 'Rollback');
});

test('a failed event load is reported in the panel', async (t) => {
  const ui = await events(t, { detailFail: true });
  ui.click(ui.el('rows').querySelector('tr[data-event-id="e1"] td'));
  await settle(() => /unavailable/.test(ui.panel.querySelector('[data-panel-title]').textContent));
  assert.match(
    ui.panel.querySelector('[data-panel-body]').textContent,
    /Failed to load the event: Event offline/,
  );
  assert.equal(
    ui.panel.querySelector('[data-panel-full]').getAttribute('href'),
    '/event.html?id=e1&framework=A',
  );
});
