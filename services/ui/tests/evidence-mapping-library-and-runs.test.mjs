// The mapping library, rule insights, revisions and historical runs for rules
// as the rules YAML can hold them: without a description, a source or a
// connection, and answers that arrive after the administrator has moved on.
import test from 'node:test';
import assert from 'node:assert/strict';
import { settle } from './support/page-fixture.mjs';
import {
  collection,
  collectorId,
  deferred,
  editor,
  rules,
} from './support/evidence-mapping-editor.mjs';

const library = (ui) =>
  [...ui.$('rules').children].map((row) =>
    row.tagName === 'P'
      ? [row.textContent]
      : [row.querySelector('strong').textContent, row.querySelector('.small-muted').textContent],
  );
const button = (root, text) =>
  [...root.querySelectorAll('button')].find((b) => b.textContent === text);
// Steps 2 and 3 with a mapping name and one control, then a predefined save.
async function publish(ui, description = 'Collected evidence') {
  await ui.click('next-step');
  ui.$('description').value = description;
  ui.$('control').value = 'A1';
  ui.$('add-target').click();
  await ui.click('next-step');
  ui.$('predefine').checked = true;
  await ui.click('save-rule');
}

test('a rule without a description is named by its ID in the library, insights and editor', async (t) => {
  const ui = await editor(t, {
    rules: [
      { id: 'yaml-rule', description: '', when: { source: 'keen-agent' }, map_to: [] },
      ...rules(1),
    ],
    insights: {
      version: 7,
      days: 90,
      rules: [{ id: 'yaml-rule', status: 'quiet', message: 'No matches for 3 days.' }],
      consolidation: [],
      overlaps: [],
    },
    confirm: false,
  });
  const card = ui.$('insights-attention').firstElementChild;
  assert.equal(card.querySelector('strong').textContent, 'yaml-rule');
  assert.equal(card.querySelector('.badge').title, 'No matches for 3 days.');
  assert.equal(button(card, 'Open rule').getAttribute('aria-label'), 'Open yaml-rule');
  // Searching reads the rule's ID when it has no description.
  await ui.type('rule-filter', 'yaml');
  assert.deepEqual(library(ui), [['yaml-rule', 'All keen-agent events · 0 framework targets']]);
  ui.$('rules').querySelector('.btn-outline-danger').click();
  await ui.idle();
  assert.equal(
    ui.confirmations.at(-1).message,
    'Delete evidence definition yaml-rule? Collection and existing evidence are retained.',
  );
  assert.equal(ui.writes.length, 0);
  await ui.open('yaml-rule');
  assert.equal(ui.$('editor-heading').textContent, 'Edit yaml-rule');
  assert.equal(ui.$('description').value, '');
});

test('a rule naming an environment collection without a connection opens on that collection', async (t) => {
  const ui = await editor(t, {
    sources: ['github', 'keen-agent'],
    collectors: [collection('github', 'repos', 'octo/keen', { owner: 'octo', repo: 'keen' })],
    // Written before connections existed: no source and no connection.
    rules: [
      {
        id: 'legacy-repo',
        description: 'Legacy repository rule',
        when: { collector: collectorId('github', 'repos', 'octo/keen') },
        map_to: [{ framework: 'A', ref: 'A1' }],
      },
      ...rules(1),
    ],
  });
  assert.deepEqual(library(ui)[0], [
    'Legacy repository rule',
    'github repository: octo/keen · 1 framework targets',
  ]);
  // The library's source filter finds it by its collection's source.
  ui.$('source-filter').value = 'github';
  ui.$('source-filter').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(
    library(ui).map(([title]) => title),
    ['Legacy repository rule'],
  );
  // Searching reads its collection, since it names no source of its own.
  await ui.type('rule-filter', 'octo');
  assert.deepEqual(
    library(ui).map(([title]) => title),
    ['Legacy repository rule'],
  );
  await ui.open('Legacy repository rule');
  assert.equal(ui.$('status').textContent, '');
  assert.deepEqual(
    ['definition-adapter', 'definition-connection', 'definition-section', 'definition-entry'].map(
      (id) => ui.$(id).value,
    ),
    ['github', 'env:github', 'repos', collectorId('github', 'repos', 'octo/keen')],
  );
  assert.equal(ui.field('owner').value, 'octo');
  assert.equal(
    ui.$('selected-input').querySelector('span').textContent,
    'Selected input: github repository “octo/keen” from Environment default.',
  );
});

test('a rule without a source opens on the webhook source and is resumed in the rules YAML', async (t) => {
  const ui = await editor(t, {
    rules: [
      {
        id: 'any-login',
        description: 'Any login',
        enabled: false,
        when: { action: 'login' },
        map_to: [],
      },
    ],
  });
  ui.$('rules').querySelector('.btn-outline-secondary').click();
  await ui.idle();
  assert.equal(
    ui.$('status').textContent,
    'Any login cannot be resumed here: it has no source, or its ID uses characters the editor does not accept. Set enabled: true for it in the rules YAML instead.',
  );
  assert.equal(ui.confirmations.length, 0);
  await ui.open('Any login');
  assert.equal(ui.$('definition-adapter').value, 'webhook');
  assert.equal(ui.$('when-source').value, 'webhook');
  assert.equal(
    ui.$('selected-input').querySelector('span').textContent,
    'Selected input: All webhook events from all connections.',
  );
});

test('rule insights answering out of order keep the latest answer', async (t) => {
  const held = [],
    flags = { hold: false };
  const ui = await editor(t, {
    rules: rules(1),
    gate: (method, path) =>
      path === '/api/v1/admin/mapping-rules/insights' && flags.hold
        ? (held.push(deferred()), held.at(-1).promise)
        : null,
  });
  const insights = (days) => ({ version: 8, days, rules: [], consolidation: [], overlaps: [] });
  const reload = () => ui.window.dispatchEvent(new ui.window.Event('keen-rules-changed'));
  const asked = () => ui.calls.filter((call) => call.endsWith('/insights')).length;

  flags.hold = true;
  reload();
  await settle(() => held.length === 1);
  flags.hold = false;
  ui.state.insights = insights(30);
  reload();
  await settle(() => asked() === 3 && ui.$('insights-meta').textContent.includes('30 days'));
  // The earlier request answers last, with what the server held then.
  ui.state.insights = insights(90);
  held[0].resolve();
  await ui.idle();
  assert.match(
    ui.$('insights-meta').textContent,
    /^Based on the events each rule matched in the last 30 days/,
  );

  flags.hold = true;
  reload();
  await settle(() => held.length === 2);
  flags.hold = false;
  reload();
  await settle(() => asked() === 5 && ui.$('insights-meta').textContent.includes('90 days'));
  ui.state.failInsights = 'Timed out';
  held[1].resolve();
  await ui.idle();
  assert.match(
    ui.$('insights-meta').textContent,
    /^Based on the events each rule matched in the last 90 days/,
  );
});

test('revisions answering after another rule was opened are not listed for it', async (t) => {
  const slow = deferred();
  const ui = await editor(t, {
    rules: rules(2),
    revisions: {
      'rule-01': [{ revision: 1, version: 3, at: '2026-01-01T00:00:00' }],
      'rule-02': [{ revision: 1, version: 5, at: '2026-02-02T00:00:00' }],
    },
    gate: (method, path) =>
      path === '/api/v1/admin/mapping-rules/rule-01/revisions' ? slow.promise : null,
  });
  const [first, second] = ui.$('rules').querySelectorAll('.list-group-item');
  first.click();
  await settle(() => ui.calls.some((call) => call.includes('rule-01/revisions')));
  ui.$('close-editor').click();
  second.click();
  await settle(() => ui.$('rule-revision').options.length === 2);
  slow.resolve();
  await ui.idle();
  assert.equal(ui.$('rule-id').value, 'rule-02');
  assert.deepEqual(
    [...ui.$('rule-revision').options].map((option) => [option.value, option.textContent]),
    [
      ['', "Select this rule's revision"],
      ['5', '#1 · 2026-02-02T00:00:00'],
    ],
  );
});

test('a historical run answering after the editor closed is not shown or polled', async (t) => {
  const slow = deferred();
  const running = { id: 'job-9', status: 'running', examined: 1, matched: 0 };
  const ui = await editor(t, {
    rules: rules(1),
    backfills: { 'rule-01': [running] },
    jobs: { 'job-9': [running] },
    gate: (method, path) => (path === '/api/v1/admin/rule-backfills/job-9' ? slow.promise : null),
  });
  ui.$('rules').querySelector('.list-group-item').click();
  await settle(() => ui.calls.some((call) => call.endsWith('/rule-backfills/job-9')));
  ui.$('close-editor').click();
  slow.resolve();
  await ui.idle();
  assert.equal(ui.$('backfill').textContent, '');
  assert.equal(ui.delayed.filter((timer) => timer.ms === 5000).length, 0);
});

test('a historical run without counts reports zeros, and its notice expires', async (t) => {
  const ui = await editor(t, {
    saveBackfill: { id: 'job-5', status: 'queued' },
    jobs: {
      'job-5': [
        { id: 'job-5', status: 'running' },
        { id: 'job-5', status: 'completed' },
      ],
    },
  });
  await ui.openInput('keen-agent');
  await publish(ui, 'Agent evidence');
  assert.equal(
    ui.$('backfill').querySelector('span').textContent,
    'Historical evidence: running. Checked 0 of an estimated total being calculated events; 0 matched, 0 new mappings.',
  );
  assert.equal(await ui.runTimers(5000), 1);
  assert.equal(
    ui.$('status').textContent,
    'Historical mapping complete: checked 0 events; 0 matched, 0 new mappings.',
  );
  assert.equal(ui.$('status').hidden, false);
  // A success notice leaves after six seconds.
  assert.ok((await ui.runTimers(6000)) >= 1);
  assert.equal(ui.$('status').hidden, true);
});

test('a save that finds no earlier evidence says the rule applies to future events', async (t) => {
  const ui = await editor(t, { saveBackfill: { status: 'no_previous_evidence' } });
  await ui.openInput('keen-agent');
  await publish(ui, 'Agent evidence');
  assert.equal(ui.$('status').textContent, 'Mapping saved. Future evidence uses it immediately.');
  assert.equal(
    ui.$('backfill').textContent,
    'No previously collected evidence to evaluate. The rule will apply to future events.',
  );
});

test('backfilling a rule for several sources names them, and declining starts nothing', async (t) => {
  const ui = await editor(t, {
    sources: ['keen-agent', 'rss'],
    rules: [
      {
        id: 'pair',
        description: 'Agent and feed evidence',
        when: { source_regex: '\\A(?:keen-agent|rss)\\Z' },
        map_to: [{ framework: 'A', ref: 'A1' }],
      },
    ],
    confirm: false,
  });
  await ui.open('Agent and feed evidence');
  await ui.click('next-step');
  await ui.click('next-step');
  assert.equal(ui.$('step-number').textContent, '3');
  await ui.click('backfill-start');
  assert.equal(
    ui.confirmations.at(-1).message,
    'Evaluate all previous evidence from keen-agent or rss against saved rule pair? This runs in the background.',
  );
  assert.ok(!ui.writes.some((write) => write.path.endsWith('/backfill')));
  assert.equal(ui.$('backfill').textContent, '');
});

test('warnings about the stored configuration are shown when the library loads', async (t) => {
  const warning = 'github contains sensitive settings that require server administrator review';
  const ui = await editor(t, {
    warnings: [warning, 'rss has a feed with a credential in its URL'],
  });
  assert.equal(
    ui.$('status').textContent,
    `${warning} · rss has a feed with a credential in its URL`,
  );
  assert.equal(ui.$('status').className, 'alert alert-warning mb-3');
  assert.equal(ui.$('status').getAttribute('role'), 'status');
});

test('a saved definition is deleted from the editor only after confirmation', async (t) => {
  const ui = await editor(t, { rules: rules(1), confirm: false });
  await ui.open('Definition 01');
  await ui.click('next-step');
  await ui.click('next-step');
  await ui.click('delete-rule');
  assert.equal(
    ui.confirmations.at(-1).message,
    'Delete evidence definition rule-01? Collected evidence and existing mappings are retained.',
  );
  assert.ok(!ui.writes.some((write) => write.method === 'DELETE'));
  assert.equal(ui.$('editor').hidden, false);
  ui.state.confirm = true;
  await ui.click('delete-rule');
  assert.equal(ui.writes.at(-1).method, 'DELETE');
  assert.equal(ui.writes.at(-1).path, '/api/v1/admin/mapping-rules/rule-01?version=7');
  assert.equal(
    ui.$('status').textContent,
    'Definition removed. Existing evidence and mappings are retained.',
  );
});

test('suggested controls without a title are offered as controls with their reasons', async (t) => {
  const ui = await editor(t, {
    suggestions: [
      { ref: 'A1', title: '', type: 'control', reason: 'Catalogue words: access.' },
      { ref: 'C5', title: 'Policy clause', type: 'clause', reason: 'Related concepts: policy.' },
    ],
  });
  await ui.openInput('keen-agent');
  await ui.click('next-step');
  await ui.type('description', 'Access reviews');
  await ui.click('suggest-controls');
  const offered = [...ui.$('control-suggestions').querySelectorAll('button')];
  assert.deepEqual(
    offered.map((b) => [b.querySelector('strong').textContent, b.querySelector('div').textContent]),
    [
      [
        'A1 · Control',
        'Catalogue words: access. Click to add; review suitability before publishing.',
      ],
      [
        'Clause C5 · Policy clause',
        'Related concepts: policy. Click to add; review suitability before publishing.',
      ],
    ],
  );
  offered[0].click();
  assert.equal(offered[0].disabled, true);
  assert.equal(ui.$('targets').querySelector('.fw-semibold').textContent, 'Framework A / A1');
});
