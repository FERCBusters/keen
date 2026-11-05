// The mapping editor's source and collection step: BookStack catalogue
// entries without names, the field that names each kind of collection input,
// webhook providers and connections, and source patterns that name sources.
import test from 'node:test';
import assert from 'node:assert/strict';
import { collection, collectorId, editor, rules } from './support/evidence-mapping-editor.mjs';

const options = (select) => [...select.options].map((option) => [option.value, option.textContent]);
const step = (ui) => ui.$('step-number').textContent;
// Steps 2 and 3 with a mapping name and one control, then a predefined save.
async function publish(ui, description = 'Collected evidence') {
  await ui.click('next-step');
  ui.$('description').value = description;
  ui.$('control').value = 'A1';
  ui.$('add-target').click();
  await ui.click('next-step');
  ui.$('predefine').checked = true;
  await ui.click('save-rule');
  return ui.writes.filter((write) => write.method === 'PUT').at(-1);
}

test('BookStack books and pages without names are listed by slug or number, and blank choices load nothing', async (t) => {
  const chosen = { id: 12, name: 'Access policy' };
  const ui = await editor(t, {
    sources: ['bookstack'],
    connections: [
      {
        id: 'bs-one',
        source: 'bookstack',
        name: 'Policies wiki',
        configuration: {},
        inputs: { selected_pages: [chosen] },
        version: 4,
      },
    ],
    collectors: [collection('bookstack', 'selected_pages', '12', chosen, 'bs-one')],
    catalogueSize: 10,
    books: [
      { id: 7, name: '', slug: '' },
      { id: 8, name: '', slug: 'runbooks' },
    ],
    chapters: { 7: [] },
    pages: {
      7: [
        { id: 12, name: '', slug: 'access-policy', chapter_id: null, book_id: 7 },
        { id: 13, name: '', slug: '', chapter_id: null, book_id: 7 },
      ],
    },
  });
  await ui.openInput(
    'bookstack',
    'bs-one',
    'selected_pages',
    collectorId('bookstack', 'selected_pages', '12'),
  );
  assert.deepEqual(options(ui.$('bookstack-book')), [
    ['', 'Choose a book'],
    ['7', 'Book 7'],
    ['8', 'runbooks'],
  ]);
  await ui.choose('bookstack-book', '7');
  assert.deepEqual(options(ui.$('bookstack-page')), [
    ['', 'Choose a page'],
    ['12', 'access-policy'],
    ['13', 'Page 13'],
  ]);
  // A book without a name is described rather than left blank.
  assert.equal(ui.$('bookstack-status').textContent, '2 pages loaded from this book.');

  // The blank page choice leaves the saved input as it was.
  await ui.choose('bookstack-page', '');
  assert.equal(ui.field('id').value, '12');
  assert.equal(ui.field('name').value, 'Access policy');
  assert.equal(ui.$('bookstack-status').textContent, '2 pages loaded from this book.');
  await ui.choose('bookstack-page', '13');
  assert.deepEqual(
    ['id', 'book_id', 'book_slug', 'slug', 'name'].map((name) => ui.field(name).value),
    ['13', '7', '', '', ''],
  );

  // The blank book choice asks the catalogue for nothing.
  const asked = ui.calls.length;
  await ui.choose('bookstack-book', '');
  assert.equal(ui.calls.length, asked);
  assert.deepEqual(options(ui.$('bookstack-page')).at(-1), ['13', 'Page 13']);
});

test('each kind of collection input is named by its own field before Next', async (t) => {
  const inputs = [
    ['gitlab', 'groups', 'infra', { group: 'infra', label: 'Infrastructure' }, 'group'],
    ['gitlab', 'users', 'dana', { user: 'dana', label: 'Dana' }, 'user'],
    ['forgejo', 'organizations', 'keen', { org: 'keen', label: 'Keen' }, 'org'],
    ['riskledger', 'organizations', '*', { org: '*', label: 'Suppliers' }, 'org'],
    ['redmine', 'projects', 'ops', { project: 'ops', label: 'Operations' }, 'project'],
  ];
  const ui = await editor(t, {
    sources: ['gitlab', 'forgejo', 'riskledger', 'redmine', 'github', 'keen-agent'],
    collectors: [
      ...inputs.map(([adapter, section, key, entry]) => collection(adapter, section, key, entry)),
      collection('github', 'repos', 'octo/keen', { owner: 'octo', repo: 'keen' }),
    ],
  });
  for (const [adapter, section, key, , name] of inputs) {
    await ui.openInput(adapter, 'env:' + adapter, section, collectorId(adapter, section, key));
    // With its naming field filled, the input moves on to the mapping name.
    await ui.click('next-step');
    assert.equal(step(ui), '2', `${adapter} ${section} is named by ${name}`);
    await ui.click('prev-step');
    // Without it, the label alone does not name the input.
    ui.field(name).value = '';
    await ui.click('next-step');
    assert.equal(step(ui), '1');
    assert.equal(ui.$('status').textContent, 'Give the collection item a name, URL or ID.');
    await ui.click('close-editor');
  }
  // A repository is named by its owner and name together.
  await ui.openInput('github', 'env:github', 'repos', collectorId('github', 'repos', 'octo/keen'));
  ui.field('owner').value = '';
  ui.field('repo').value = '';
  await ui.click('next-step');
  assert.equal(step(ui), '1');
  assert.equal(ui.$('status').textContent, 'Give the collection item a name, URL or ID.');
});

test('Risk Ledger, Redmine and Jenkins inputs explain their identifying fields', async (t) => {
  const ui = await editor(t, {
    sources: ['riskledger', 'redmine', 'jenkins', 'keen-agent'],
    collectors: [
      collection('riskledger', 'organizations', '*', { org: '*' }),
      collection('redmine', 'projects', 'ops', { project: 'ops' }),
      collection('jenkins', 'jobs', 'deploy', { name: 'deploy', label: 'web', kind: 'deploy' }),
    ],
  });
  const help = (name) => ui.field(name).parentElement.querySelector('.form-text')?.textContent;
  await ui.openInput(
    'riskledger',
    'env:riskledger',
    'organizations',
    collectorId('riskledger', 'organizations', '*'),
  );
  assert.match(help('org'), /^Use \* for the authenticated organisation, or its UUID/);
  assert.equal(help('label'), undefined);
  await ui.click('close-editor');
  await ui.openInput(
    'redmine',
    'env:redmine',
    'projects',
    collectorId('redmine', 'projects', 'ops'),
  );
  assert.match(help('project'), /^Project ID or identifier\. Use \* as the sole collection/);
  await ui.click('close-editor');
  await ui.openInput('jenkins', 'env:jenkins', 'jobs', collectorId('jenkins', 'jobs', 'deploy'));
  assert.match(help('kind'), /^Action assigned to Jenkins events from this job/);
  assert.match(help('label'), /^System assigned to Jenkins events from this job/);
  assert.equal(help('name'), undefined);
});

test('a webhook provider input saves under its provider, without the provider in its settings', async (t) => {
  const provider = { ui_name: 'Pager', secret_header: 'X-Sig' };
  const ui = await editor(t, {
    sources: ['webhooks', 'keen-agent'],
    collectors: [collection('webhooks', 'providers', 'pager', provider)],
  });
  await ui.openInput(
    'webhooks',
    'env:webhooks',
    'providers',
    collectorId('webhooks', 'providers', 'pager'),
  );
  // An emptied Additional settings box reads as no additional settings.
  ui.$('definition-extra').value = '';
  const saved = await publish(ui, 'Pager alerts');
  assert.equal(ui.$('status').textContent, 'Mapping saved. Future evidence uses it immediately.');
  assert.equal(saved.body.entry_key, 'pager');
  assert.deepEqual(saved.body.entry, provider);
  assert.equal(saved.body.rule.when.source, 'webhook:pager');
  assert.equal(saved.body.rule.when.collector, collectorId('webhooks', 'providers', 'pager'));
});

test("a webhook connection's source-wide rule reads its provider's events", async (t) => {
  const ui = await editor(t, {
    sources: ['webhooks', 'keen-agent'],
    connections: [
      {
        id: 'wh-1',
        source: 'webhooks',
        name: 'GitHub hooks',
        configuration: { provider: 'github' },
        inputs: {},
        version: 3,
      },
    ],
  });
  await ui.openInput('webhooks', 'env:webhooks');
  assert.equal(ui.$('when-source').value, 'webhooks');
  await ui.choose('definition-connection', 'wh-1');
  assert.equal(ui.$('when-source').value, 'webhook:github');
  const saved = await publish(ui, 'GitHub reviews');
  assert.equal(saved.body.rule.when.source, 'webhook:github');
  assert.equal(saved.body.rule.when.connection_id, 'wh-1');
  assert.equal(saved.body.connection_id, 'wh-1');
});

test('a source without collection inputs offers all of its events on the environment connection', async (t) => {
  const ui = await editor(t);
  await ui.openInput('keen-agent', 'env:keen-agent');
  assert.equal(ui.$('status').textContent, '');
  assert.deepEqual(options(ui.$('definition-section')), [
    ['source', 'All collected events (any collection)'],
  ]);
  // Its recent events are read from that connection.
  assert.equal(
    ui.calls.at(-1),
    '/api/v1/admin/evidence-event-samples?source=keen-agent&connection_id=env%3Akeen-agent',
  );
});

test('Next asks for a collection input in the chosen source and scope', async (t) => {
  const bare = await editor(t, { sources: ['forgejo', 'keen-agent'] });
  await bare.openInput('forgejo');
  await bare.click('next-step');
  assert.equal(step(bare), '1');
  assert.equal(
    bare.$('status').textContent,
    'Add an input under Sources before creating a mapping rule.',
  );

  const other = await editor(t, {
    sources: ['forgejo', 'keen-agent'],
    collectors: [collection('forgejo', 'users', 'dana', { user: 'dana' })],
  });
  await other.openInput('forgejo', 'env:forgejo', 'organizations');
  assert.deepEqual(options(other.$('definition-entry')), []);
  await other.click('next-step');
  assert.equal(step(other), '1');
  assert.equal(
    other.$('status').textContent,
    'Add an input under Sources before creating its mapping rule.',
  );
});

test('source patterns that do not list sources exactly are described as patterns', async (t) => {
  const patterns = [
    '\\A(?:git\\w)\\Z',
    '\\A(?:git.*)\\Z',
    '\\A(?:github|)\\Z',
    '\\A(?:github|rss)\\Z',
  ];
  const ui = await editor(t, {
    rules: patterns.map((source_regex, index) => ({
      id: 'pattern-' + index,
      description: 'Pattern ' + index,
      when: { source_regex },
      map_to: [],
    })),
  });
  const meta = [...ui.$('rules').querySelectorAll('.small-muted')].map((m) => m.textContent);
  assert.deepEqual(meta, [
    'Events from sources matching “\\A(?:git\\w)\\Z” · 0 framework targets',
    'Events from sources matching “\\A(?:git.*)\\Z” · 0 framework targets',
    'Events from sources matching “\\A(?:github|)\\Z” · 0 framework targets',
    'Events from github or rss · 0 framework targets',
  ]);
  // Only the exact list adds its sources to the library's source filter.
  assert.deepEqual(
    options(ui.$('source-filter')).map(([value]) => value),
    ['', 'github', 'rss'],
  );
});

test('unchoosing every source clears the source pattern of a rule for several sources', async (t) => {
  const ui = await editor(t, { sources: ['keen-agent', 'rss'] });
  const chip = (name) =>
    ui.document
      .getElementById('ec-sourcesFilterPillModal')
      .querySelector(`.keen-filter-pill-grid [data-value="${name}"]`);
  await ui.click('new-rule');
  await ui.choose('definition-adapter', '__several__');
  chip('rss').click();
  await ui.idle();
  assert.equal(ui.$('when-source_regex').value, '\\A(?:rss)\\Z');
  chip('rss').click();
  await ui.idle();
  assert.equal(ui.$('when-source_regex').value, '');
  assert.match(ui.$('rule-sentence').textContent, /^From the chosen sources\./);
});

test('frameworks without names and controls without titles are listed by reference', async (t) => {
  const ui = await editor(t, {
    frameworks: [
      { slug: 'A', name: 'Framework A' },
      { slug: 'B', name: null },
    ],
    controls: (framework) =>
      framework === 'B'
        ? [
            { ref: 'B1', title: '', type: 'control' },
            { ref: 'B2', title: '', type: 'clause' },
          ]
        : [{ ref: 'A1', title: 'Control A' }],
    rules: rules(1),
  });
  assert.deepEqual(options(ui.$('framework')), [
    ['A', 'Framework A'],
    ['B', 'B'],
  ]);
  await ui.click('new-rule');
  await ui.click('next-step');
  await ui.choose('framework', 'B');
  assert.deepEqual(
    [...ui.$('control').options].map((option) => option.textContent),
    ['B1 - control', 'Clause B2 - clause'],
  );
  ui.$('control').value = 'B1';
  ui.$('add-target').click();
  await ui.idle();
  const target = ui.$('targets').firstElementChild;
  assert.equal(target.querySelector('.text-break').textContent, 'B / B1');
  assert.equal(target.querySelector('button').getAttribute('aria-label'), 'Remove B / B1');
});
