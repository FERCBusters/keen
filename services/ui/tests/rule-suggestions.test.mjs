// Suggested rules on the Mapping rules page: review, create, edit first,
// dismiss and restore, and checking now.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle() {
  for (let i = 0; i < 20; i++) await tick();
}

function suggestion(overrides = {}) {
  return {
    id: 's1',
    status: 'open',
    rule: {
      id: 'suggested_github_pull_request_event',
      description: 'GitHub: PullRequestEvent',
      when: { source: 'github', action: 'PullRequestEvent' },
      map_to: [{ framework: 'F1', ref: 'CM-1', kind: 'control' }],
    },
    sources: [{ source: 'github', label: 'GitHub' }],
    action: 'PullRequestEvent',
    event_count: 42,
    first_seen: '2026-10-01T00:00:00',
    last_seen: '2026-10-09T00:00:00',
    controls: [
      {
        ref: 'CM-1',
        title: 'Change management',
        kind: 'control',
        selected: true,
        reasons: ['Related concept: pull request → change management (activity)'],
      },
      { ref: 'LG-1', title: 'Logging', kind: 'control', selected: false, reasons: [] },
    ],
    samples: [{ id: 'e1', timestamp: '2026-10-09T00:00:00', summary: 'alice opened #3' }],
    rule_id: null,
    decided_at: null,
    decided_by: null,
    ...overrides,
  };
}

function listing(overrides = {}) {
  return {
    enabled: true,
    framework: 'F1',
    framework_name: 'Framework One',
    open_count: 1,
    items: [suggestion()],
    last_run: {
      finished_at: '2026-10-10T06:00:00',
      status: 'completed',
      examined: 2000,
      unmapped: 300,
      suggested: 1,
      error: null,
    },
    sample_size: 1000,
    window_days: 30,
    min_events: 5,
    ...overrides,
  };
}

async function page(t, server = {}) {
  const state = { lists: { open: listing(), dismissed: listing({ items: [] }) }, ...server };
  const calls = [],
    events = [],
    bells = [];
  const ui = await loadUi('pages/rule-suggestions.js', {
    html,
    url: 'https://keen.example/admin.html' + (state.hash || '#evidence-config'),
    globals: state.globals || {},
    setup(window) {
      window.keenRefreshBell = () => bells.push(true);
      for (const name of [
        'keen-rules-changed',
        'keen-suggested-rule',
        'keen-show-evidence-mapping',
      ])
        window.addEventListener(name, (event) =>
          events.push({ name, detail: JSON.parse(JSON.stringify(event.detail ?? null)) }),
        );
      window.HTMLElement.prototype.scrollIntoView = function () {
        this.dataset.scrolled = 'yes';
      };
      // jsdom lays nothing out; a test says when the panel is shown.
      window.HTMLElement.prototype.getClientRects = function () {
        return state.shown === false ? [] : [{}];
      };
    },
    vendor: {
      fmtTs: (value) => `at ${value}`,
      errorText: (error) => String(error?.message || error),
      apiGet: async (url) => {
        calls.push(['GET', url]);
        if (state.failGet) throw new Error(state.failGet);
        if (url === '/api/v1/admin/mapping-rules') return { version: 7, items: [] };
        const status = new URL(url, 'https://keen.example').searchParams.get('status');
        if (state.respond) return structuredClone(await state.respond(status));
        return structuredClone(state.lists[status]);
      },
      apiPost: async (url, body) => {
        calls.push(['POST', url, JSON.parse(JSON.stringify(body))]);
        if (state.failPost) throw new Error(state.failPost);
        if (url.endsWith('/apply'))
          return {
            rule: { description: 'GitHub: PullRequestEvent' },
            backfill: state.backfill === undefined ? { status: 'queued' } : state.backfill,
          };
        if (url.endsWith('/refresh')) return state.refresh?.() || { status: 'queued' };
        return {};
      },
    },
  });
  t.after(() => ui.close());
  await settle();
  const $ = (id) => ui.document.getElementById(id);
  const card = () => ui.document.querySelector('[data-suggestion="s1"]');
  const buttonNamed = (name) =>
    [...ui.document.querySelectorAll('#ec-suggestions-list button')].find(
      (b) => b.textContent === name,
    );
  return { ui, state, calls, events, bells, $, card, buttonNamed };
}

test('open suggestions show their rule, events, controls and samples', async (t) => {
  const { $, card, calls } = await page(t);
  assert.deepEqual(calls[0], ['GET', '/api/v1/admin/rule-suggestions?status=open']);
  assert.equal($('ec-suggestions-count').textContent, ' · 1 to review');
  assert.match($('ec-suggestions-meta').textContent, /^Checked at 2026-10-10T06:00:00: the latest/);
  assert.match($('ec-suggestions-meta').textContent, /300 of 2,000 not mapped to Framework One/);
  assert.match($('ec-suggestions-meta').textContent, /needs 5 unmapped events/);
  const node = card();
  assert.equal(node.querySelector('strong').textContent, 'GitHub: PullRequestEvent');
  assert.equal(node.querySelector('.badge').textContent, '42 unmapped events');
  const link = [...node.querySelectorAll('a')].find((a) => a.textContent === 'View these events');
  assert.equal(
    link.getAttribute('href'),
    '/events.html?source=github&unmapped=true&f=action%3Aequals%3APullRequestEvent&framework=F1',
  );
  assert.equal(link.target, '_blank');
  const boxes = [...node.querySelectorAll('input[data-ref]')];
  assert.deepEqual(
    boxes.map((b) => [b.dataset.ref, b.checked, b.disabled]),
    [
      ['CM-1', true, false],
      ['LG-1', false, false],
    ],
  );
  assert.equal(
    node.querySelector(`label[for="${boxes[0].id}"]`).textContent,
    'CM-1 Change management',
  );
  assert.match(node.textContent, /pull request → change management \(activity\)\./);
  const sample = node.querySelector(':scope details a');
  assert.equal(sample.getAttribute('href'), '/event.html?id=e1');
  assert.equal(sample.rel, 'noopener');
  assert.match(sample.parentElement.textContent, /alice opened #3/);
});

test('creating a rule maps to the ticked controls and refreshes the library', async (t) => {
  const { card, calls, events, bells, $, buttonNamed } = await page(t);
  card().querySelector('input[data-ref="LG-1"]').checked = true;
  buttonNamed('Create rule').click();
  await settle();
  assert.deepEqual(calls.slice(1, 3), [
    ['GET', '/api/v1/admin/mapping-rules'],
    ['POST', '/api/v1/admin/rule-suggestions/s1/apply', { version: 7, refs: ['CM-1', 'LG-1'] }],
  ]);
  assert.equal(
    $('ec-suggestions-notice').textContent,
    'Rule “GitHub: PullRequestEvent” created. Past events are being mapped in the background.',
  );
  assert.equal($('ec-suggestions-notice').getAttribute('role'), 'status');
  assert.deepEqual(
    events.map((e) => e.name),
    ['keen-rules-changed'],
  );
  assert.equal(bells.length, 1);
  assert.deepEqual(calls.at(-1), ['GET', '/api/v1/admin/rule-suggestions?status=open']);
});

for (const [backfill, said] of [
  [{ status: 'no_previous_evidence' }, ' There were no past events to map.'],
  [null, ''],
])
  test(`creating a rule says what happened to past events (${said || 'nothing'})`, async (t) => {
    const { $, buttonNamed } = await page(t, { backfill });
    buttonNamed('Create rule').click();
    await settle();
    assert.equal(
      $('ec-suggestions-notice').textContent,
      `Rule “GitHub: PullRequestEvent” created.${said}`,
    );
  });

test('a rule needs a control, and a refused one keeps the suggestion', async (t) => {
  const { card, calls, $, buttonNamed, state } = await page(t);
  card().querySelector('input[data-ref="CM-1"]').checked = false;
  buttonNamed('Create rule').click();
  await settle();
  assert.equal($('ec-suggestions-notice').textContent, 'Choose at least one control to map to.');
  assert.equal($('ec-suggestions-notice').getAttribute('role'), 'alert');
  assert.equal(calls.length, 1);

  card().querySelector('input[data-ref="CM-1"]').checked = true;
  state.failPost = 'Unknown control: F1 / CM-1';
  buttonNamed('Create rule').click();
  await settle();
  assert.equal($('ec-suggestions-notice').textContent, 'Unknown control: F1 / CM-1');
  assert.ok([...card().querySelectorAll('button')].every((b) => !b.disabled));
});

test('edit first hands the rule with the ticked controls to the editor', async (t) => {
  const { card, events, buttonNamed } = await page(t);
  card().querySelector('input[data-ref="CM-1"]').checked = false;
  card().querySelector('input[data-ref="LG-1"]').checked = true;
  buttonNamed('Edit first').click();
  assert.deepEqual(events, [
    {
      name: 'keen-suggested-rule',
      detail: {
        id: 's1',
        eventCount: 42,
        rule: {
          id: 'suggested_github_pull_request_event',
          description: 'GitHub: PullRequestEvent',
          when: { source: 'github', action: 'PullRequestEvent' },
          map_to: [{ framework: 'F1', ref: 'LG-1', kind: 'control' }],
        },
      },
    },
  ]);
});

test('dismissed suggestions can be reviewed and restored', async (t) => {
  const dismissed = suggestion({
    status: 'dismissed',
    decided_by: 'alice',
    decided_at: '2026-10-10T07:00:00',
    samples: [],
  });
  const { $, calls, card, buttonNamed, bells, ui } = await page(t, {
    lists: { open: listing(), dismissed: listing({ items: [dismissed] }) },
  });
  buttonNamed('Dismiss').click();
  await settle();
  assert.deepEqual(calls[1], [
    'POST',
    '/api/v1/admin/rule-suggestions/s1/status',
    { status: 'dismissed' },
  ]);
  assert.equal(
    $('ec-suggestions-notice').textContent,
    'Dismissed “GitHub: PullRequestEvent”. It will not be suggested again.',
  );
  assert.equal(bells.length, 1);

  $('ec-suggestions-dismissed').checked = true;
  $('ec-suggestions-dismissed').dispatchEvent(new ui.window.Event('change'));
  await settle();
  assert.deepEqual(calls.at(-1), ['GET', '/api/v1/admin/rule-suggestions?status=dismissed']);
  assert.equal($('ec-suggestions-notice').hidden, true);
  assert.match(card().textContent, /Dismissed by alice at 2026-10-10T07:00:00\./);
  assert.match(card().textContent, /These events are no longer held\./);
  assert.ok([...card().querySelectorAll('input[data-ref]')].every((b) => b.disabled));
  assert.equal(buttonNamed('Create rule'), undefined);
  buttonNamed('Restore').click();
  await settle();
  assert.deepEqual(
    calls.find((c) => c[0] === 'POST' && c[2]?.status === 'open'),
    ['POST', '/api/v1/admin/rule-suggestions/s1/status', { status: 'open' }],
  );
  assert.equal($('ec-suggestions-notice').textContent, 'Restored “GitHub: PullRequestEvent”.');
});

test('empty lists, and runs not yet made, failed or switched off, are explained', async (t) => {
  for (const [list, meta, empty] of [
    [listing({ items: [], open_count: 0 }), /^Checked/, 'No suggestions to review.'],
    [listing({ items: [], last_run: null }), /^Not checked yet\./, 'No suggestions to review.'],
    [
      listing({
        items: [],
        last_run: { status: 'failed', finished_at: 'T', error: 'A query took too long' },
      }),
      /^The check at at T failed: A query took too long$/,
      'No suggestions to review.',
    ],
    [listing({ items: [], enabled: false }), /^Suggestions are off/, 'No suggestions to review.'],
  ]) {
    const { $ } = await page(t, { lists: { open: list } });
    assert.match($('ec-suggestions-meta').textContent, meta);
    assert.equal($('ec-suggestions-list').textContent, empty);
    assert.equal($('ec-suggestions-refresh').disabled, !list.enabled);
  }
  const { $, ui } = await page(t, {
    lists: { open: listing(), dismissed: listing({ items: [] }) },
  });
  $('ec-suggestions-dismissed').checked = true;
  $('ec-suggestions-dismissed').dispatchEvent(new ui.window.Event('change'));
  await settle();
  assert.equal($('ec-suggestions-list').textContent, 'No dismissed suggestions.');
});

test('a list that cannot load says so', async (t) => {
  const { $ } = await page(t, { failGet: 'Forbidden' });
  assert.equal($('ec-suggestions-meta').textContent, 'Suggested rules are unavailable: Forbidden');
  assert.equal($('ec-suggestions-count').textContent, '');
  assert.equal($('ec-suggestions-refresh').disabled, true);
});

test('a link to suggested rules scrolls once the tab is shown', async (t) => {
  const { $, ui, state } = await page(t, { hash: '#rule-suggestions', shown: false });
  assert.equal($('ec-suggestions').open, true);
  assert.equal($('ec-suggestions').dataset.scrolled, undefined);
  // Another tab first: still hidden, so the panel waits for the next one.
  ui.document.dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal($('ec-suggestions').dataset.scrolled, undefined);
  state.shown = true;
  ui.document.dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal($('ec-suggestions').dataset.scrolled, 'yes');
  assert.equal(ui.document.activeElement, $('ec-suggestions').querySelector('summary'));
});

test('a link to suggested rules opens the panel', async (t) => {
  const { $, ui, events } = await page(t, { hash: '#rule-suggestions' });
  // The Evidence mapping view of the tab shows at once, and again with the list.
  assert.deepEqual(
    events.map((e) => e.name),
    ['keen-show-evidence-mapping', 'keen-show-evidence-mapping'],
  );
  assert.equal(ui.document.activeElement, $('ec-suggestions').querySelector('summary'));
  assert.equal($('ec-suggestions').open, true);
  assert.equal($('ec-suggestions').dataset.scrolled, 'yes');
  $('ec-suggestions').open = false;
  ui.window.location.hash = '#evidence-config';
  ui.window.dispatchEvent(new ui.window.HashChangeEvent('hashchange'));
  assert.equal($('ec-suggestions').open, false);
  ui.window.location.hash = '#rule-suggestions';
  ui.window.dispatchEvent(new ui.window.HashChangeEvent('hashchange'));
  assert.equal($('ec-suggestions').open, true);
});

test('the editor saving a suggestion refreshes the list', async (t) => {
  const { calls, ui } = await page(t);
  ui.window.dispatchEvent(new ui.window.CustomEvent('keen-rule-suggestions-changed'));
  await settle();
  assert.deepEqual(
    calls.map((c) => c[1]),
    ['/api/v1/admin/rule-suggestions?status=open', '/api/v1/admin/rule-suggestions?status=open'],
  );
});

test('the events link reads every source, and every activity without one', async (t) => {
  const { ui } = await page(t);
  assert.equal(
    ui.api.eventsHref({ sources: [{ source: 'aws' }, { source: 'azure' }], action: '' }, ''),
    '/events.html?source=aws%2Cazure&unmapped=true',
  );
});

// Polling waits run at once here.
const instant = { setTimeout: (fn) => Promise.resolve().then(fn) };

test('checking now waits for the run and refreshes the bell', async (t) => {
  let reads = 0;
  const first = listing();
  const finished = listing({ last_run: { ...first.last_run, finished_at: '2026-10-10T08:00:00' } });
  const { $, calls, bells, state } = await page(t, {
    globals: instant,
    lists: {
      get open() {
        reads++;
        return reads < 3 ? first : finished;
      },
    },
  });
  $('ec-suggestions-refresh').click();
  assert.equal($('ec-suggestions-refresh').disabled, true);
  await settle();
  assert.ok(calls.some((c) => c[1] === '/api/v1/admin/rule-suggestions/refresh'));
  assert.equal(reads, 3);
  assert.equal(bells.length, 1);
  assert.match($('ec-suggestions-meta').textContent, /^Checked at 2026-10-10T08:00:00/);
  assert.equal($('ec-suggestions-refresh').disabled, false);

  state.refresh = () => ({ status: 'recent' });
  $('ec-suggestions-refresh').click();
  await settle();
  assert.equal(
    $('ec-suggestions-notice').textContent,
    'Checked less than a minute ago; the suggestions below are current.',
  );
});

test('a long check, or one that cannot start, says so', async (t) => {
  const { $, state } = await page(t, { globals: instant });
  $('ec-suggestions-refresh').click();
  await settle();
  await settle();
  assert.equal(
    $('ec-suggestions-notice').textContent,
    'The check is taking a while; the list updates when you return.',
  );
  state.failPost = 'The background worker could not be reached; try again shortly';
  $('ec-suggestions-refresh').click();
  await settle();
  assert.equal(
    $('ec-suggestions-notice').textContent,
    'The background worker could not be reached; try again shortly',
  );
  assert.equal($('ec-suggestions-refresh').disabled, false);
});

test('a newer list replaces one still loading', async (t) => {
  const { $, ui, state } = await page(t);
  const gates = [];
  state.respond = () => new Promise((resolve, reject) => gates.push({ resolve, reject }));
  const reload = () =>
    ui.window.dispatchEvent(new ui.window.CustomEvent('keen-rule-suggestions-changed'));
  reload();
  reload();
  await settle();
  gates[1].resolve(listing({ items: [], open_count: 2 }));
  await settle();
  gates[0].resolve(listing({ items: [], open_count: 9 }));
  await settle();
  assert.equal($('ec-suggestions-count').textContent, ' · 2 to review');
  reload();
  reload();
  await settle();
  gates[3].resolve(listing({ open_count: 3 }));
  await settle();
  gates[2].reject(new Error('late'));
  await settle();
  assert.equal($('ec-suggestions-count').textContent, ' · 3 to review');
  assert.match($('ec-suggestions-meta').textContent, /^Checked/);
});
