import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

function selection(overrides = {}) {
  return {
    items: [
      {
        slug: 'A',
        name: 'Alpha',
        enabled: true,
        allowed: true,
        version: '2022',
        description: 'Desc',
        upstream_url: 'https://a.example',
      },
      { slug: 'B', name: 'Beta', enabled: false, allowed: true },
      { slug: 'C', name: '<b>Gamma</b>', enabled: false, allowed: false },
    ],
    default: 'A',
    version: 3,
    environment_restricted: true,
    unknown_environment_slugs: ['zz'],
    ...overrides,
  };
}

const nodesA = [
  { kind: 'clause', ref: '4', title: 'Context', sort_order: 0, in_scope: true },
  { kind: 'clause', ref: '4.10', title: 'Later', parent_ref: '4', sort_order: 0, in_scope: true },
  { kind: 'clause', ref: '4.2', title: 'Earlier', parent_ref: '4', sort_order: 0, in_scope: true },
  { kind: 'annex_control', ref: 'A.5', title: 'Policies', in_scope: true },
  {
    kind: 'annex_control',
    ref: 'A.5.1',
    title: '<img src=x>Policy',
    parent_ref: 'A.5',
    clause_refs: ['4.2'],
    in_scope: false,
    sort_order: 2,
    description: 'D',
    upstream_url: 'https://u.example',
  },
  // A corrupt cycle must not hang the tree renderer.
  { kind: 'custom', ref: 'X', title: 'Loop X', parent_ref: 'Y' },
  { kind: 'custom', ref: 'Y', title: 'Loop Y', parent_ref: 'X' },
];

async function editor(t, options = {}) {
  const state = {
    selection: options.selection || selection(),
    nodes: { A: structuredClone([...nodesA, ...(options.extraNodes || [])]), B: [] },
    put: [],
    posted: [],
    removed: [],
  };
  const ui = await page(t, 'framework-editor', {
    htmlName: 'admin',
    globals: options.globals,
    get: async (u) => {
      if (u.pathname === '/api/v1/admin/framework-selection') {
        if (options.fail) throw new Error('Selection offline');
        return structuredClone(state.selection);
      }
      const m = u.pathname.match(/^\/api\/v1\/admin\/frameworks\/([^/]+)\/nodes$/);
      if (m) return { items: structuredClone(state.nodes[decodeURIComponent(m[1])] || []) };
      throw new Error('Unexpected GET ' + u);
    },
    put: async (u, b) => {
      state.put.push({ path: u.pathname, body: plain(b) });
      if (options.putFail) throw new Error('Node rejected');
      if (u.pathname === '/api/v1/admin/framework-selection') {
        state.selection = {
          ...state.selection,
          version: 4,
          default: b.default,
          items: state.selection.items.map((i) => ({ ...i, enabled: b.enabled.includes(i.slug) })),
        };
        return structuredClone(state.selection);
      }
      const existing = state.nodes.A.findIndex((n) => n.kind === b.kind && n.ref === b.ref);
      if (existing >= 0) state.nodes.A[existing] = b;
      else state.nodes.A.push(b);
      return b;
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      state.selection.items.push({ slug: b.slug, name: b.name, enabled: false, allowed: true });
      state.nodes[b.slug] = [];
      return b;
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.deleteFail) throw new Error('Node has children');
      state.nodes.A = state.nodes.A.filter(
        (n) => !(n.kind === 'annex_control' && n.ref === 'A.5.1'),
      );
      return { ok: true };
    },
  });
  const $ = (id) => ui.el('fe-' + id);
  await settle(() => $('tree').children.length || $('message').textContent);
  return { ui, state, $ };
}

test('the framework editor lists frameworks and renders the node hierarchy in order', async (t) => {
  const { ui, $ } = await editor(t);
  assert.deepEqual(
    [...$('frameworks').options].map((o) => o.textContent),
    ['Alpha (A)', 'Beta (B)', '<b>Gamma</b> (C)'],
  );
  assert.equal($('slug').value, 'A');
  assert.equal($('version').value, '2022');
  assert.equal($('url').value, 'https://a.example');
  const rows = [...$('tree').children].map((b) => b.textContent);
  // Children follow their parent, ordered numerically by reference.
  assert.deepEqual(rows.slice(0, 5), [
    '4  Context · clause',
    '4.2  Earlier · clause',
    '4.10  Later · clause',
    'A.5  Policies · annex_control',
    'A.5.1  <img src=x>Policy · annex_control',
  ]);
  assert.equal($('tree').querySelector('img'), null);
  assert.ok(rows.includes('X  Loop X · custom') && rows.includes('Y  Loop Y · custom'));
  assert.equal($('tree').children[1].style.paddingLeft, '30px');
  $('search').value = 'policy';
  $('search').dispatchEvent(new ui.window.Event('input'));
});

test('organisation framework selection respects environment restrictions and saves the version', async (t) => {
  const { ui, state, $ } = await editor(t);
  const boxes = [...$('organisation-frameworks').querySelectorAll('input')];
  assert.deepEqual(
    boxes.map((b) => [b.value, b.checked, b.disabled]),
    [
      ['A', true, false],
      ['B', false, false],
      ['C', false, true],
    ],
  );
  assert.match(
    $('organisation-frameworks').textContent,
    /<b>Gamma<\/b> \(C\) · excluded by environment/,
  );
  assert.equal($('organisation-frameworks').querySelector('b'), null);
  assert.equal(
    $('organisation-note').textContent,
    'KEEN_ENABLED_FRAMEWORKS limits the available choices. Unknown environment slugs: zz',
  );
  assert.deepEqual(
    [...$('organisation-default').options].map((o) => o.value),
    ['A'],
  );

  $('select-all').click();
  assert.deepEqual(
    boxes.map((b) => b.checked),
    [true, true, false],
  );
  assert.deepEqual(
    [...$('organisation-default').options].map((o) => o.value),
    ['A', 'B'],
  );
  assert.equal($('organisation-default').value, 'A');
  $('organisation-default').value = 'B';
  $('save-selection').click();
  await settle(() => /Organisation selection saved/.test($('organisation-note').textContent));
  assert.deepEqual(state.put[0], {
    path: '/api/v1/admin/framework-selection',
    body: { enabled: ['A', 'B'], default: 'B', version: 3 },
  });
  // The saved catalogue's version is used for the next save.
  $('save-selection').click();
  await settle(() => state.put.length === 2);
  assert.equal(state.put[1].body.version, 4);
  await settle(() => $('save-selection').disabled === false);
  assert.equal(ui.window.keenFrameworkDefault, 'B');
});

test('saving an empty organisation selection is refused locally', async (t) => {
  const { ui, state, $ } = await editor(t, {
    selection: selection({ environment_restricted: false, unknown_environment_slugs: [] }),
  });
  assert.equal(
    $('organisation-note').textContent,
    'Choose at least one framework. Saving also updates the navigation.',
  );
  $('deselect-all').click();
  assert.equal($('organisation-default').disabled, true);
  assert.match($('organisation-note').textContent, /All frameworks deselected/);
  $('save-selection').click();
  await settle(() => $('organisation-note').textContent === 'Choose at least one framework.');
  assert.equal(state.put.length, 0);
});

test('selecting a node fills the editor and saving sends the full node definition', async (t) => {
  const { ui, state, $ } = await editor(t);
  const child = [...$('tree').children].find((b) => b.textContent.startsWith('A.5.1'));
  child.click();
  assert.equal($('kind').value, 'annex_control');
  assert.equal($('ref').value, 'A.5.1');
  assert.equal($('parent').value, 'A.5');
  assert.equal($('scope').checked, false);
  assert.equal($('sort').value, '2');
  // A node cannot be its own parent; clause choices are offered for controls.
  assert.ok(![...$('parent').options].some((o) => o.value === 'A.5.1'));
  assert.deepEqual(
    [...$('clauses').selectedOptions].map((o) => o.value),
    ['4.2'],
  );
  assert.equal($('clauses').disabled, false);
  $('title').value = 'Information security policy';
  for (const o of $('clauses').options) o.selected = o.value === '4' || o.value === '4.10';
  $('save-node').click();
  await settle(() => $('message').textContent === 'Node saved');
  assert.deepEqual(state.put[0], {
    path: '/api/v1/admin/frameworks/A/nodes/annex_control/A.5.1',
    body: {
      kind: 'annex_control',
      ref: 'A.5.1',
      title: 'Information security policy',
      description: 'D',
      upstream_url: 'https://u.example',
      parent_ref: 'A.5',
      clause_refs: ['4', '4.10'],
      in_scope: false,
      sort_order: 2,
    },
  });
  // The saved node stays selected after reloading.
  assert.equal($('title').value, 'Information security policy');
});

test('nodes in catalogue-specific categories can be edited and saved', async (t) => {
  // Seeded catalogues use categories beyond the editor defaults, such as these.
  const extraNodes = [
    { kind: 'trust_framework_control', ref: 'CC1', title: 'Control environment', in_scope: true },
    {
      kind: 'trust_framework_control',
      ref: 'CC1.1',
      title: 'Integrity and ethics',
      parent_ref: 'CC1',
      in_scope: true,
    },
  ];
  const { state, $ } = await editor(t, { extraNodes });
  [...$('tree').children].find((b) => b.textContent.startsWith('CC1.1')).click();
  assert.equal($('kind').value, 'trust_framework_control');
  assert.equal($('parent').value, 'CC1');
  assert.deepEqual(
    [...$('parent').options].map((o) => o.value),
    ['', 'CC1'],
  );
  $('title').value = 'Integrity and ethical values';
  $('save-node').click();
  await settle(() => $('message').textContent === 'Node saved');
  assert.equal(state.put[0].path, '/api/v1/admin/frameworks/A/nodes/trust_framework_control/CC1.1');
  assert.equal(state.put[0].body.kind, 'trust_framework_control');
  assert.equal(state.put[0].body.parent_ref, 'CC1');
  // Switching to a default category still works, and the added category stays available.
  [...$('tree').children].find((b) => b.textContent.startsWith('A.5.1')).click();
  assert.equal($('kind').value, 'annex_control');
  [...$('tree').children].find((b) => b.textContent.startsWith('CC1 ')).click();
  assert.equal($('kind').value, 'trust_framework_control');
  assert.equal(
    [...$('kind').options].filter((o) => o.value === 'trust_framework_control').length,
    1,
  );
});

test('node references are stable and required fields are validated', async (t) => {
  const { ui, state, $ } = await editor(t);
  $('save-node').click();
  await settle(() => $('message').textContent === 'Reference and title are required');
  [...$('tree').children][0].click();
  assert.equal($('clauses').disabled, true);
  $('ref').value = '5';
  $('save-node').click();
  await settle(
    () => $('message').textContent === 'Create a new node to change its reference or category',
  );
  assert.equal(state.put.length, 0);
  // New nodes start blank with default scope and ordering.
  $('new').click();
  assert.equal($('ref').value, '');
  assert.equal($('sort').value, '0');
  assert.equal($('scope').checked, true);
  $('kind').value = 'clause';
  $('kind').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(
    [...$('parent').options].map((o) => o.value),
    ['', '4', '4.10', '4.2'],
  );
  $('ref').value = '5';
  $('title').value = 'Leadership';
  $('save-node').click();
  await settle(() => $('message').textContent === 'Node saved');
  assert.equal(state.put[0].path, '/api/v1/admin/frameworks/A/nodes/clause/5');
  assert.equal(state.put[0].body.parent_ref, null);
});

test('node deletion confirms and reports server refusals', async (t) => {
  const prompts = [];
  const { ui, state, $ } = await editor(t, {
    globals: { confirm: (m) => (prompts.push(m), true) },
  });
  $('delete-node').click();
  assert.equal(prompts.length, 0);
  [...$('tree').children].find((b) => b.textContent.startsWith('A.5.1')).click();
  $('delete-node').click();
  await settle(() => $('message').textContent === 'Node deleted');
  assert.deepEqual(state.removed, ['/api/v1/admin/frameworks/A/nodes/annex_control/A.5.1']);
  assert.equal(prompts[0], 'Delete annex_control A.5.1?');

  const refused = await editor(t, { deleteFail: true, globals: { confirm: () => true } });
  [...refused.$('tree').children][0].click();
  refused.$('delete-node').click();
  await settle(() => refused.$('message').textContent === 'Node has children');
});

test('frameworks are created through New framework and existing slugs cannot be renamed', async (t) => {
  const { ui, state, $ } = await editor(t);
  $('slug').value = 'A2';
  $('save-framework').click();
  await settle(
    () => $('message').textContent === 'Framework slugs cannot be renamed; use New framework',
  );
  $('new-framework').click();
  assert.equal($('slug').value, '');
  assert.match($('message').textContent, /Enter a new slug/);
  $('save-framework').click();
  await settle(() => $('message').textContent === 'A framework slug is required');
  $('slug').value = ' NEW ';
  $('name').value = 'New framework';
  $('save-framework').click();
  await settle(() => $('message').textContent === 'Framework saved');
  assert.deepEqual(state.posted[0].body, {
    slug: 'NEW',
    name: 'New framework',
    version: '',
    description: '',
    upstream_url: '',
  });
  assert.equal($('frameworks').value, 'NEW');
  assert.equal($('tree').children.length, 0);
});

test('changing framework loads its nodes and load failures are reported', async (t) => {
  const { ui, $ } = await editor(t);
  $('frameworks').value = 'B';
  $('frameworks').dispatchEvent(new ui.window.Event('change'));
  await settle(() => $('slug').value === 'B' && $('tree').children.length === 0);
  const failed = await editor(t, { fail: true });
  assert.equal(failed.$('message').textContent, 'Selection offline');
  const putFail = await editor(t, { putFail: true });
  putFail.$('ref').value = 'Z';
  putFail.$('title').value = 'Z';
  putFail.$('save-node').click();
  await settle(() => putFail.$('message').textContent === 'Node rejected');
  putFail.$('save-selection').click();
  await settle(() => putFail.$('organisation-note').textContent === 'Node rejected');
});
