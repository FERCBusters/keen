// Suggested rules: wording for single counts, untitled controls and removed
// accounts, and a first check on a list never checked before.
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
      id: 'suggested_okta_login',
      description: 'Okta: login',
      when: { source: 'okta', action: 'login' },
      map_to: [],
    },
    sources: [{ source: 'okta', label: 'Okta' }],
    action: 'login',
    event_count: 1,
    first_seen: '2026-10-01T00:00:00',
    last_seen: '2026-10-01T00:00:00',
    controls: [{ ref: 'AC-2', title: null, kind: 'control', selected: true, reasons: [] }],
    samples: [],
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
      examined: 1,
      unmapped: 1,
      suggested: 1,
      error: null,
    },
    sample_size: 1,
    window_days: 30,
    min_events: 1,
    ...overrides,
  };
}

async function suggestions(t, { lists, globals = {} }) {
  const calls = [],
    bells = [];
  const ui = await loadUi('pages/rule-suggestions.js', {
    html,
    url: 'https://keen.example/admin.html#evidence-config',
    globals,
    setup(window) {
      window.keenRefreshBell = () => bells.push(true);
    },
    vendor: {
      fmtTs: (value) => `at ${value}`,
      errorText: (error) => String(error?.message || error),
      apiGet: async (url) => {
        calls.push(['GET', url]);
        return structuredClone(lists(new URL(url, 'https://keen.example').searchParams));
      },
      apiPost: async (url) => {
        calls.push(['POST', url]);
        return { status: 'queued' };
      },
    },
  });
  t.after(() => ui.close());
  await settle();
  const $ = (id) => ui.document.getElementById(id);
  return { ui, $, calls, bells };
}

test('one unmapped event and an untitled control read naturally', async (t) => {
  const { $ } = await suggestions(t, { lists: () => listing() });
  const card = $('ec-suggestions-list').querySelector('[data-suggestion="s1"]');
  assert.equal(card.querySelector('.badge').textContent, '1 unmapped event');
  assert.match($('ec-suggestions-meta').textContent, /the latest 1 event of each source/);
  // A control without a title shows its reference alone.
  assert.equal(card.querySelector('label').textContent, 'AC-2 ');
  assert.doesNotMatch(card.textContent, /null|undefined/);
});

test('a suggestion dismissed by an account since removed names no one', async (t) => {
  const { $ } = await suggestions(t, {
    lists: () =>
      listing({
        items: [
          suggestion({ status: 'dismissed', decided_at: '2026-10-11T00:00:00', decided_by: null }),
        ],
      }),
  });
  const card = $('ec-suggestions-list').querySelector('[data-suggestion="s1"]');
  assert.match(card.textContent, /Dismissed at 2026-10-11T00:00:00\./);
  assert.doesNotMatch(card.textContent, /Dismissed by/);
});

test('the first check of a list never checked waits until a run has finished', async (t) => {
  let reads = 0;
  const finished = listing().last_run;
  const { $, calls, bells } = await suggestions(t, {
    // Polling waits run at once here.
    globals: { setTimeout: (fn) => Promise.resolve().then(fn) },
    lists: () => {
      reads++;
      // Not checked yet on the first two reads; the third has the finished run.
      return listing({ last_run: reads < 3 ? null : finished });
    },
  });
  assert.match($('ec-suggestions-meta').textContent, /^Not checked yet\./);
  $('ec-suggestions-refresh').click();
  await settle();
  assert.ok(calls.some(([method, url]) => method === 'POST' && url.endsWith('/refresh')));
  // The poll that still found no run kept waiting rather than stopping.
  assert.equal(reads, 3);
  assert.equal(bells.length, 1);
  assert.match($('ec-suggestions-meta').textContent, /^Checked at 2026-10-10T06:00:00/);
  assert.equal($('ec-suggestions-notice').hidden, true);
});
