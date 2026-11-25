// MIDENA diagrams with a policy or process document: the captured drawings
// shown as images, finding a MIDENA diagram to add, refreshing and removing
// one, and the count beside each document in Policies & processes. The API
// answers as services/api/app/services/isms/diagrams.py does.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';
import { documentRecord, register } from './support/isms-register-fixture.mjs';

const hostile = '<img src=x onerror=alert(1)>';
const base = '/api/v1/isms/documents/d1';
const SVG = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>';

const shown = (id, fields = {}) => ({
  id,
  document_id: 'd1',
  diagram_uuid: `u-${id}`,
  name: `Flow ${id}`,
  diagram_type: 'bpmn',
  description: '',
  version_number: 3,
  source_updated_at: '2026-11-01T00:00:00',
  sha256: `sha-${id}`,
  captured_at: '2026-11-02T00:00:00Z',
  captured_by: { id: 'u1', username: 'alice' },
  midena_url: `https://midena.example/diagram.html?id=u-${id}`,
  ...fields,
});

const found = (uuid, fields = {}) => ({
  uuid,
  name: `Found ${uuid}`,
  diagram_type: 'flowchart',
  folder_name: 'Processes',
  version_number: 2,
  updated_at: '2026-11-03T00:00:00',
  ...fields,
});

const documentRow = (diagrams) => ({
  id: 'd1',
  title: 'Access policy',
  document_type: 'policy',
  description: '',
  tags: [],
  content_html: '',
  controls: [],
  clauses: [],
  updated_at: '2026-02-01T00:00:00',
  ...(diagrams === undefined ? {} : { diagrams }),
});

async function documentPage(
  t,
  {
    me = { is_admin: true, midena_enabled: true },
    items = [],
    diagrams,
    answers = {},
    confirm = true,
  } = {},
) {
  const state = { items: structuredClone(items), writes: [] };
  const summary = diagrams === undefined ? items.map((row) => ({ id: row.id })) : diagrams;
  const ui = await page(t, 'isms-document', {
    me,
    confirm,
    query: '&id=d1',
    get: async (u) => {
      const p = u.pathname;
      if (p === base) return documentRow(summary);
      if (p === `${base}/diagrams`) {
        if (answers.list instanceof Error) throw answers.list;
        return { items: structuredClone(state.items), enabled: true };
      }
      if (p.startsWith(`${base}/diagrams/`)) {
        const id = p.split('/').pop();
        if (answers.drawing?.[id]) throw answers.drawing[id];
        return { ...state.items.find((row) => row.id === id), svg: SVG };
      }
      if (p === '/api/v1/isms/midena-diagrams') {
        const next = answers.catalog.shift();
        if (next instanceof Error) throw next;
        return next;
      }
      if (p === `${base}/revisions`) return [];
      if (p === `${base}/comments`) return [];
      if (p === '/api/v1/isms/document/d1/changelog') return { items: [] };
      if (p === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (p === '/api/v1/stories') return { items: [], total: 0 };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      state.writes.push(['POST', u.pathname + u.search, JSON.parse(JSON.stringify(body))]);
      if (u.pathname.endsWith('/refresh')) {
        if (answers.refresh) throw answers.refresh;
        const id = u.pathname.split('/').at(-2);
        const row = state.items.find((item) => item.id === id);
        row.version_number = 4;
        return structuredClone(row);
      }
      if (answers.attach) throw answers.attach;
      const added = shown('n1', { diagram_uuid: body.diagram_uuid, name: 'Added flow' });
      state.items.push(added);
      return structuredClone(added);
    },
    remove: async (u) => {
      state.writes.push(['DELETE', u.pathname + u.search]);
      if (answers.remove) throw answers.remove;
      state.items = state.items.filter((row) => row.id !== u.pathname.split('/').pop());
      return { ok: true };
    },
  });
  await settle(() => !/^Loading/.test(ui.el('revisionList').textContent));
  await settle(() => !/^Loading diagrams/.test(ui.el('documentDiagrams').textContent));
  return { ui, state };
}

const figures = (ui) =>
  [...ui.el('documentDiagrams').querySelectorAll('figure')].map((figure) => ({
    id: figure.dataset.diagramId,
    text: figure.querySelector('.fw-semibold').textContent,
    meta: figure.querySelector('.small-muted').textContent,
    image: figure.querySelector('img')?.getAttribute('src') || null,
    alt: figure.querySelector('img')?.alt || null,
    caption: figure.querySelector('figcaption')?.textContent || null,
    actions: [...figure.querySelectorAll('a, button')].map((el) => el.textContent),
    link: figure.querySelector('a')?.getAttribute('href') || null,
  }));

test('a document shows each captured drawing as an image, with its version', async (t) => {
  const { ui } = await documentPage(t, {
    items: [
      shown('a', { name: `${hostile}Flow`, description: `Why ${hostile}` }),
      shown('b', { version_number: null, captured_by: null, midena_url: 'javascript:alert(1)' }),
    ],
  });
  await settle(() => figures(ui).every((f) => f.image));
  const [first, second] = figures(ui);
  assert.equal(first.text, `${hostile}Flow`);
  assert.equal(first.alt, `${hostile}Flow`);
  assert.equal(first.meta, 'MIDENA version 3 · captured 2026-11-02T00:00:00Z · by alice');
  assert.equal(first.caption, `Why ${hostile}`);
  assert.equal(first.image, `data:image/svg+xml;charset=utf-8,${encodeURIComponent(SVG)}`);
  assert.deepEqual(first.actions, ['Open in MIDENA', 'Refresh', 'Remove']);
  assert.equal(first.link, 'https://midena.example/diagram.html?id=u-a');
  assert.equal(second.meta, 'MIDENA version unknown · captured 2026-11-02T00:00:00Z');
  assert.equal(second.caption, null);
  // A link that is not a web address is not offered.
  assert.deepEqual(second.actions, ['Refresh', 'Remove']);
  // Names and descriptions are text: nothing hostile became markup.
  assert.equal(ui.el('documentDiagrams').querySelector('img[onerror]'), null);
  assert.equal(ui.el('documentDiagramsCard').hidden, false);
  assert.equal(ui.el('addDiagramButton').hidden, false);
});

test('readers see the drawings without changing them', async (t) => {
  const { ui } = await documentPage(t, {
    me: { is_admin: false, can_read_isms: true, midena_enabled: true },
    items: [shown('a'), shown('b', { midena_url: null })],
  });
  await settle(() => figures(ui)[0]?.image);
  assert.deepEqual(figures(ui)[0].actions, ['Open in MIDENA']);
  // Without MIDENA configured any longer, a drawing has no link to it.
  assert.deepEqual(figures(ui)[1].actions, []);
  assert.equal(ui.el('addDiagramButton').hidden, true);
});

test('a document without diagrams asks for none, and readers see no card', async (t) => {
  const reader = await documentPage(t, {
    me: { is_admin: false, can_read_isms: true, midena_enabled: true },
  });
  assert.equal(reader.ui.el('documentDiagramsCard').hidden, true);
  assert.ok(!reader.ui.calls.some((c) => c.url.includes('/diagrams')));
  // A document served before diagrams existed is read the same way.
  const older = await documentPage(t, {
    me: { is_admin: true, midena_enabled: false },
    diagrams: null,
  });
  assert.equal(older.ui.el('documentDiagramsCard').hidden, true);
  // A manager, with MIDENA configured, is offered the picker.
  const manager = await documentPage(t);
  assert.equal(manager.ui.el('documentDiagramsCard').hidden, false);
  assert.match(
    manager.ui.el('documentDiagrams').textContent,
    /No MIDENA diagrams are shown with this document/,
  );
});

test('a drawing or the list that cannot load says so in place', async (t) => {
  const { ui } = await documentPage(t, {
    items: [shown('a'), shown('b')],
    answers: { drawing: { b: new Error('Gone') } },
  });
  await settle(() => /could not be loaded: Gone/.test(ui.el('documentDiagrams').textContent));
  assert.ok(figures(ui)[0].image);
  const failed = await documentPage(t, {
    items: [],
    diagrams: [{ id: 'a' }],
    answers: { list: new Error('Offline') },
  });
  assert.equal(
    failed.ui.el('documentDiagrams').textContent,
    'Diagrams could not be loaded: Offline',
  );
  assert.equal(failed.ui.el('documentDiagramsCard').hidden, false);
});

test('finding a MIDENA diagram, paging and adding it', async (t) => {
  const { ui, state } = await documentPage(t, {
    items: [shown('a')],
    answers: {
      catalog: [
        {
          items: [found('u-a'), found('u-x', { folder_name: null, updated_at: '' })],
          total: 3,
          offset: 0,
          limit: 2,
        },
        { items: [found('u-y', { diagram_type: '' })], total: 3, offset: 2, limit: 2 },
        { items: [found(`u-z`, { name: hostile })], total: 1, offset: 0, limit: 2 },
      ],
    },
  });
  const add = ui.el('addDiagramButton');
  add.click();
  assert.equal(ui.el('diagramPicker').hidden, false);
  assert.equal(add.getAttribute('aria-expanded'), 'true');
  await settle(() => ui.el('diagramResults').querySelectorAll('.list-group-item').length === 2);
  const results = () =>
    [...ui.el('diagramResults').querySelectorAll('.list-group-item')].map((row) => [
      row.querySelector('.fw-semibold').textContent,
      row.querySelector('.small-muted').textContent,
      row.querySelector('button').textContent,
      row.querySelector('button').disabled,
    ]);
  assert.deepEqual(results(), [
    [
      'Found u-a',
      'flowchart · Processes · MIDENA version 2 · updated 2026-11-03T00:00:00',
      'Shown',
      true,
    ],
    ['Found u-x', 'flowchart · MIDENA version 2', 'Add', false],
  ]);
  assert.equal(ui.el('diagramMore').hidden, false);
  ui.el('diagramMore').click();
  await settle(() => results().length === 3);
  assert.equal(results()[2][1], 'Processes · MIDENA version 2 · updated 2026-11-03T00:00:00');
  assert.equal(ui.el('diagramMore').hidden, true);

  ui.el('diagramSearch').value = '  access  ';
  ui.el('diagramPicker').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => results().length === 1);
  const catalogUrls = ui.calls
    .filter((c) => c.url.includes('midena-diagrams'))
    .map((c) => new URL(c.url, 'https://keen.example').search);
  assert.deepEqual(catalogUrls, ['?q=&offset=0', '?q=&offset=2', '?q=access&offset=0']);
  assert.equal(results()[0][0], hostile);

  ui.el('diagramResults').querySelector('button').click();
  await settle(() => figures(ui).length === 2);
  assert.deepEqual(state.writes[0], [
    'POST',
    `${base}/diagrams?framework=A`,
    { diagram_uuid: 'u-z' },
  ]);
  assert.equal(ui.el('status').textContent, 'Added flow added to this document.');
  assert.equal(results()[0][2], 'Shown');

  add.click();
  assert.equal(ui.el('diagramPicker').hidden, true);
  assert.equal(add.getAttribute('aria-expanded'), 'false');
});

test('an empty search, a failed search and a refused addition are reported', async (t) => {
  const { ui } = await documentPage(t, {
    answers: {
      catalog: [
        { items: [], total: 0, offset: 0, limit: 50 },
        new Error('MIDENA refused'),
        { items: [found('u-x')], total: 1, offset: 0, limit: 50 },
      ],
      attach: new Error('Already shown'),
    },
  });
  ui.el('addDiagramButton').click();
  await settle(() => /No MIDENA diagrams match/.test(ui.el('diagramResults').textContent));
  ui.el('diagramPicker').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() =>
    /could not be searched: MIDENA refused/.test(ui.el('diagramResults').textContent),
  );
  assert.equal(ui.el('diagramMore').hidden, true);
  ui.el('diagramPicker').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('diagramResults').querySelector('button'));
  const choose = ui.el('diagramResults').querySelector('button');
  choose.click();
  await settle(() => /Could not add the diagram: Already shown/.test(ui.el('status').textContent));
  assert.equal(choose.disabled, false);
  assert.equal(choose.textContent, 'Add');
});

test('refreshing captures MIDENA’s current version, or reports why not', async (t) => {
  const { ui, state } = await documentPage(t, { items: [shown('a')] });
  const refresh = () =>
    [...ui.el('documentDiagrams').querySelectorAll('button')].find(
      (b) => b.textContent === 'Refresh',
    );
  refresh().click();
  await settle(() => /MIDENA version 4 captured/.test(ui.el('status').textContent));
  assert.deepEqual(state.writes[0], ['POST', `${base}/diagrams/a/refresh?framework=A`, {}]);
  assert.match(figures(ui)[0].meta, /^MIDENA version 4/);

  const refused = await documentPage(t, {
    items: [shown('a')],
    answers: { refresh: new Error('MIDENA has no such diagram') },
  });
  const button = [...refused.ui.el('documentDiagrams').querySelectorAll('button')].find(
    (b) => b.textContent === 'Refresh',
  );
  button.click();
  await settle(() =>
    /Could not refresh the diagram: MIDENA has no such diagram/.test(
      refused.ui.el('status').textContent,
    ),
  );
  assert.equal(button.disabled, false);
});

test('removing asks first and keeps the diagram in MIDENA', async (t) => {
  let answer = false;
  const { ui, state } = await documentPage(t, {
    items: [shown('a')],
    confirm: () => answer,
  });
  const remove = () =>
    [...ui.el('documentDiagrams').querySelectorAll('button')].find(
      (b) => b.textContent === 'Remove',
    );
  remove().click();
  await settle(() => ui.confirmations.length === 1);
  assert.deepEqual(state.writes, []);
  assert.match(ui.confirmations[0].message, /The diagram stays in MIDENA/);
  answer = true;
  remove().click();
  await settle(() => figures(ui).length === 0);
  assert.deepEqual(state.writes, [['DELETE', `${base}/diagrams/a?framework=A`]]);
  assert.equal(ui.el('status').textContent, 'Flow a removed from this document.');

  const refused = await documentPage(t, {
    items: [shown('a')],
    answers: { remove: new Error('Locked') },
  });
  [...refused.ui.el('documentDiagrams').querySelectorAll('button')]
    .find((b) => b.textContent === 'Remove')
    .click();
  await settle(() =>
    /Could not remove the diagram: Locked/.test(refused.ui.el('status').textContent),
  );
});

test('Policies & processes counts the diagrams each document shows', async (t) => {
  const ui = await register(t, {
    tab: 'documents',
    records: {
      documents: [
        documentRecord('d1', 'None shown'),
        documentRecord('d2', 'One shown', { diagrams: [{ id: 'x' }] }),
        documentRecord('d3', 'Two shown', { diagrams: [{ id: 'x' }, { id: 'y' }] }),
        { ...documentRecord('d4', 'Served before diagrams'), diagrams: undefined },
      ],
    },
  });
  await settle(() => ui.el('documentsRows').querySelector('[data-isms-row="document"]'));
  const badges = [...ui.el('documentsRows').querySelectorAll('[data-isms-row="document"]')].map(
    (row) => row.querySelector('.text-bg-info')?.textContent || '',
  );
  assert.deepEqual(badges, ['', '1 diagram', '2 diagrams', '']);
});
