import test from 'node:test';
import * as d3 from 'd3';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';
const records = {
  objectives: [
    {
      id: 'o1',
      goal: 'Protect records',
      requirement: '<img src=x>',
      metric: '100%',
      resource_users: [],
      controls: [],
      clauses: [],
    },
  ],
  documents: [
    {
      id: 'd1',
      title: 'Security policy',
      tags: ['Security'],
      document_type: 'policy',
      content_html: '<p>Policy</p>',
      controls: [],
      clauses: [],
    },
    {
      id: 'd2',
      title: 'Operations guide',
      external_url: 'javascript:alert(1)',
      tags: ['Operations'],
      controls: [],
      clauses: [],
    },
  ],
  org_nodes: [
    { id: 'n1', name: 'Security team', node_type: 'role', people: [], controls: [], clauses: [] },
  ],
  assets: [{ id: 'a1', asset: 'Database', name: 'Database', controls: [], clauses: [] }],
  licenses: [{ id: 'l1', name: 'License' }],
  access_control_matrix: [],
  effectiveness_measures: [
    {
      id: 'e1',
      summary: 'Agent coverage',
      controls: [],
      clauses: [],
      metric_entries: [],
    },
  ],
  meetings: [
    {
      id: 'm1',
      title: 'Annual review',
      date: '2026-01-01',
      attendees: [],
      apologies: [],
      links: [{ url: 'javascript:alert(1)', title: 'Unsafe legacy link' }],
      controls: [],
      clauses: [],
    },
  ],
};
async function fixture(t, options = {}) {
  const ui = await page(t, 'isms', {
    ...options,
    get: async (u) => {
      if (options.fail) throw new Error('Offline');
      if (u.pathname === '/api/v1/isms/meta')
        return {
          framework: 'A',
          users: [],
          org_nodes: [],
          documents: [],
          assets: [],
          asset_categories: [],
          licenses: [],
          controls: [],
          clauses: [],
        };
      if (u.pathname === '/api/v1/isms/summary')
        return { framework: 'A', counts: { objectives: 1, documents: 2 }, ...records };
      if (u.pathname === '/api/v1/isms/document-folders') return [];
      if (u.pathname === '/api/v1/isms/people') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      assert.equal(u.pathname, '/api/v1/isms/objectives');
      return { id: 'o2', ...b };
    },
  });
  await settle(() =>
    options.fail
      ? ui.el('status').textContent.includes('Offline')
      : ui
          .el('ismsMeta')
          .textContent.includes(
            options.me?.features?.enabled?.objectives === false ? 'A' : '1 objectives',
          ),
  );
  return ui;
}
for (const [tab, id, text] of [
  ['objectives', 'objectivesRows', 'Protect records'],
  ['documents', 'documentsRows', 'Security policy'],
  ['org', 'orgRows', 'Security team'],
  ['assets', 'assetsRows', 'Database'],
  ['access', 'accessMatrixRows', 'No access control'],
  ['effectiveness', 'effectivenessRows', 'Agent coverage'],
  ['meetings', 'meetingsRows', 'Annual review'],
]) {
  test('ISMS ' + tab + ' tab loads and renders its section', async (t) => {
    const ui = await fixture(t, { query: '&tab=' + tab });
    await settle(() => ui.el(id).textContent.includes(text));
    assert.equal(ui.el(id).querySelector('img'), null);
    assert.ok(
      ui.calls.some((c) => c.url.includes('/isms/summary?') && c.url.includes('section=' + tab)),
    );
  });
}
test("a quiz's measure links to its quiz and is changed only there", async (t) => {
  const quizMeasure = {
    id: 'e2',
    summary: 'Quiz: Phishing',
    controls: [],
    clauses: [],
    metric_entries: [],
    quiz: { id: 'q1', title: 'Phishing <basics>' },
  };
  records.effectiveness_measures.push(quizMeasure);
  t.after(() => records.effectiveness_measures.pop());
  const ui = await fixture(t, { query: '&tab=effectiveness', me: { can_manage_isms: true } });
  await settle(() => ui.el('effectivenessRows').textContent.includes('Quiz: Phishing'));
  const rows = [...ui.el('effectivenessRows').querySelectorAll('tr')];
  const quizRow = rows.find((row) => row.dataset.ismsId === 'e2');
  assert.match(quizRow.textContent, /Results of the quiz Phishing <basics>/);
  assert.equal(
    quizRow.querySelector('a[href^="/quiz.html"]').getAttribute('href'),
    '/quiz.html?id=q1&framework=A',
  );
  assert.equal(quizRow.querySelector('[data-isms-edit], [data-effectiveness-record]'), null);
  const ownRow = rows.find((row) => row.dataset.ismsId === 'e1');
  assert.ok(ownRow.querySelector('[data-isms-edit="effectiveness"]'));
});

test('inherited object names in the tab parameter open the overview', async (t) => {
  for (const name of ['constructor', '__proto__', 'toString', 'valueOf']) {
    const ui = await fixture(t, { query: '&tab=' + name });
    const sections = ui.calls
      .filter((c) => c.url.includes('/isms/summary?'))
      .map((c) => new URL(c.url, 'https://keen.example').searchParams.get('section'));
    assert.deepEqual(sections, ['overview'], name);
  }
});
test('tab and edit parameters never resolve through Object.prototype', async (t) => {
  // Inherited names such as "constructor" happen to match no section; a
  // property another script adds to Object.prototype would, without the
  // own-key checks.
  const scripts = [
    "Object.defineProperty(Object.prototype, 'polluted', { value: 'documents', configurable: true });",
  ];
  for (const query of ['&tab=polluted', '&edit=polluted:d1']) {
    const ui = await fixture(t, { query, scripts });
    const sections = ui.calls
      .filter((c) => c.url.includes('/isms/summary?'))
      .map((c) => new URL(c.url, 'https://keen.example').searchParams.get('section'));
    assert.deepEqual(sections, ['overview'], query);
  }
});
test('ISMS overview does not eagerly fetch all sections', async (t) => {
  const ui = await fixture(t);
  assert.equal(ui.calls.filter((c) => c.url.includes('/isms/summary?')).length, 1);
  assert.match(ui.calls.find((c) => c.url.includes('/isms/summary?')).url, /section=overview/);
});
test('ISMS document tag filtering is local and reversible', async (t) => {
  const ui = await fixture(t, { query: '&tab=documents' });
  await settle(() => ui.el('documentsRows').textContent.includes('Operations guide'));
  const before = ui.calls.length;
  ui.el('docTagCloud').querySelector('[data-filter-doc-tag="Security"]').click();
  assert.match(ui.el('documentsRows').textContent, /Security policy/);
  assert.doesNotMatch(ui.el('documentsRows').textContent, /Operations guide/);
  ui.el('docTagCloud').querySelector('[data-filter-doc-tag=""]').click();
  assert.match(ui.el('documentsRows').textContent, /Operations guide/);
  assert.equal(ui.calls.length, before);
});
test('ISMS objective form sends entered values and refreshes after save', async (t) => {
  const ui = await fixture(t, { query: '&tab=objectives' });
  await settle(() => ui.el('objectivesRows').textContent.includes('Protect records'));
  ui.el('objRequirement').value = 'Training';
  ui.el('objGoal').value = 'All staff trained';
  ui.el('objMetric').value = '100%';
  ui.el('objectiveForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'ISMS objective created');
  const call = ui.calls.find((c) => c.method === 'POST');
  assert.equal(call.body.goal, 'All staff trained');
  assert.equal(call.body.requirement, 'Training');
  assert.equal(ui.el('objGoal').value, '');
});
test('ISMS read-only users cannot see management forms', async (t) => {
  const ui = await fixture(t, { me: { can_view_isms: true } });
  assert.equal(ui.el('objectiveFormCard').style.display, 'none');
});
test('ISMS request failures remain visible', async (t) => {
  const ui = await fixture(t, { fail: true });
  assert.match(ui.el('status').textContent, /Failed to load ISMS.*Offline/);
});

test('the small-screen ISMS menu opens the sections and closes on selection', async (t) => {
  const ui = await fixture(t);
  const menu = ui.el('isms-navigation-toggle');
  assert.equal(menu.getAttribute('aria-expanded'), 'false');
  menu.click();
  assert.equal(ui.el('ismsTabs').classList.contains('admin-nav-open'), true);
  assert.equal(menu.getAttribute('aria-expanded'), 'true');
  ui.el('ismsTabs').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.el('ismsTabs').classList.contains('admin-nav-open'), false);
  assert.equal(menu.getAttribute('aria-expanded'), 'false');
});

for (const [tab, id] of [
  ['documents', 'documentsRows'],
  ['meetings', 'meetingsRows'],
]) {
  test('ISMS ' + tab + ' neutralises unsafe links already stored in the database', async (t) => {
    const ui = await fixture(t, { query: '&tab=' + tab });
    await settle(() => ui.el(id).querySelector('a[href="#"]'));
    for (const link of ui.el(id).querySelectorAll('a[href]'))
      assert.doesNotMatch(link.href, /^(javascript|data|vbscript):/i);
  });
}

test('SWOT overview is paginated and read only even for managers', async (t) => {
  const ui = await page(t, 'isms', {
    get(url) {
      if (url.pathname === '/api/v1/isms/meta') return { users: [] };
      if (url.pathname === '/api/v1/isms/summary') return { counts: {} };
      if (url.pathname === '/api/v1/isms/document-folders') return [];
      if (url.pathname === '/api/v1/isms/swot') {
        assert.equal(url.searchParams.get('framework'), 'A');
        assert.equal(url.searchParams.get('limit'), '100');
        return {
          items: [{ id: 's1', title: 'Stable team', kind: 'strength', status: 'defined' }],
          total: 101,
        };
      }
      throw Error('Unexpected GET ' + url);
    },
  });
  ui.el('swotTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('swotQuadrants').textContent.includes('Stable team'));
  assert.equal(
    ui.el('swotQuadrants').querySelectorAll('select, button, [draggable], .btn').length,
    0,
  );
  assert.equal(ui.el('swotEdit').hidden, false);
  assert.equal(new URL(ui.el('swotEdit').href).pathname, '/swot.html');
  ui.el('swotNext').click();
  await settle(() => ui.calls.some((call) => call.url.includes('offset=100')));
  assert.equal(
    ui.calls.some((call) => call.method !== 'GET'),
    false,
  );
});

for (const editable of [true, false]) {
  test(`Objectives share table, board, timeline and filters (${editable ? 'manager' : 'reader'})`, async (t) => {
    let state = 'defined';
    const ui = await page(t, 'isms', {
      query: '&tab=objectives&view=kanban',
      me: editable ? { is_admin: true } : { can_read_isms: true },
      setup(window) {
        window.d3 = d3;
      },
      get(url) {
        if (url.pathname === '/api/v1/isms/meta') return { users: [] };
        if (url.pathname === '/api/v1/isms/summary')
          return {
            counts: {},
            objectives: [
              {
                id: 'o1',
                goal: 'Train operators',
                status: state,
                start_date: '2026-10-01',
                completion_target_date: '2026-12-01',
                resource_users: [],
                controls: [],
                clauses: [],
              },
              {
                id: 'o2',
                goal: 'Review suppliers',
                status: 'completed',
                resource_users: [],
                controls: [],
                clauses: [],
              },
            ],
          };
        if (url.pathname === '/api/v1/isms/document-folders') return [];
        throw Error('Unexpected GET ' + url);
      },
      patch(url, body) {
        assert.equal(url.pathname, '/api/v1/isms/objectives/o1');
        state = body.status;
        return {};
      },
    });
    await settle(() => ui.el('objectivesPlanningView').textContent.includes('Train operators'));
    assert.equal(ui.el('objectivesTable').hidden, true);
    assert.equal(ui.el('objectivesPlanningView').querySelectorAll('.planning-card').length, 2);
    const select = ui.el('objectivesPlanningView').querySelector('[data-status-id="o1"]');
    if (editable) {
      select.value = 'planned';
      select.dispatchEvent(new ui.window.Event('change'));
      await settle(
        () => state === 'planned' && !ui.el('objectivesPlanningView').hasAttribute('aria-busy'),
      );
      assert.equal(
        ui.el('objectivesRows').querySelector('[data-isms-id="o1"]').dataset.objectiveStatus,
        'planned',
      );
    } else assert.equal(select, null);
    ui.el('objectivesTableFilter').value = 'Train';
    ui.el('objectivesTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
    assert.equal(ui.el('objectivesPlanningView').querySelectorAll('.planning-card').length, 1);
    ui.el('objectivesView').value = 'timeline';
    ui.el('objectivesView').dispatchEvent(new ui.window.Event('change'));
    assert.equal(ui.el('objectivesPlanningView').querySelectorAll('rect').length, 1);
    ui.el('objectivesView').value = 'table';
    ui.el('objectivesView').dispatchEvent(new ui.window.Event('change'));
    assert.equal(ui.el('objectivesTable').hidden, false);
    assert.equal(ui.el('objectivesPlanning').hidden, true);
    assert.equal(
      ui.el('objectivesRows').querySelector('[data-isms-id="o2"]').style.display,
      'none',
    );
    assert.equal(
      ui.calls.some((call) => call.url.includes('/api/v1/isms/swot')),
      false,
    );
  });
}

test('a disabled Objectives deep link loads Overview without requesting objective data', async (t) => {
  const ui = await fixture(t, {
    query: '&tab=objectives',
    me: { is_admin: true, features: { enabled: { objectives: false } } },
  });
  assert.ok(ui.el('objectivesTab').closest('li').hasAttribute('data-feature-hidden'));
  assert.ok(!ui.calls.some((call) => call.url.includes('section=objectives')));
  assert.ok(ui.calls.some((call) => call.url.includes('section=overview')));
});

test('ISMS flow places SWOT beside measures and respects feature availability', async (t) => {
  const ui = await fixture(t);
  const link = ui.el('overviewCards').querySelector('a[aria-label="Open SWOT"]');
  assert.match(link.getAttribute('href'), /tab=swot/);
  assert.match(link.getAttribute('href'), /framework=A/);
  assert.equal(link.querySelector('rect').getAttribute('y'), '238');
});
test('ISMS flow opens the legal register beside SWOT, unless it is switched off', async (t) => {
  const ui = await fixture(t);
  const cards = ui.el('overviewCards');
  const link = cards.querySelector('a[aria-label="Open Legal register"]');
  assert.match(link.getAttribute('href'), /^\/legal-register\.html\?framework=A$/);
  // The three buttons share the row without overlapping.
  const box = (label) => {
    const rect = cards.querySelector(`a[aria-label="Open ${label}"] rect`);
    return ['x', 'y', 'width'].map((name) => Number(rect.getAttribute(name)));
  };
  const row = ['Effectiveness measures', 'SWOT', 'Legal register'].map(box);
  assert.deepEqual(row, [
    [384, 238, 140],
    [536, 238, 70],
    [618, 238, 134],
  ]);
  for (let i = 1; i < row.length; i++) assert.ok(row[i - 1][0] + row[i - 1][2] < row[i][0]);

  const off = await fixture(t, {
    me: { is_admin: true, features: { enabled: { legal_register: false } } },
  });
  assert.equal(off.el('overviewCards').querySelector('a[aria-label="Open Legal register"]'), null);
  assert.ok(off.el('overviewCards').querySelector('a[aria-label="Open SWOT"]'));
});
test('ISMS flow omits disabled SWOT', async (t) => {
  const ui = await fixture(t, { me: { is_admin: true, features: { enabled: { swot: false } } } });
  assert.equal(ui.el('overviewCards').querySelector('a[aria-label="Open SWOT"]'), null);
});

test('the ISMS menu folds down to its icons, and names each entry on hover', async (t) => {
  // Without browser storage the menu starts open and still folds.
  const ui = await fixture(t);
  const menu = ui.el('ismsTabs');
  const toggle = ui.el('toggleIsmsRail');
  const titles = () => [...menu.querySelectorAll('.nav-link')].map((link) => link.title);
  assert.equal(menu.classList.contains('isms-rail-collapsed'), false);
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(toggle.getAttribute('aria-controls'), 'ismsTabs');
  assert.equal(toggle.textContent, 'Collapse menu');
  assert.equal(toggle.title, 'Collapse the menu to icons');
  assert.ok(titles().every((title) => title === ''));
  // Every entry keeps its name as text beside its icon, for the folded menu to hide.
  assert.deepEqual(
    [...menu.querySelectorAll('.nav-link')].map((link) => [
      link.querySelector('.isms-rail-label').textContent,
      Boolean(link.querySelector('i.bi')),
    ]),
    [
      ['Overview', true],
      ['SWOT analysis', true],
      ['Objectives', true],
      ['Policies & processes', true],
      ['Organisation chart', true],
      ['Asset matrix', true],
      ['Access control matrix', true],
      ['Effectiveness measures', true],
      ['Minutes of meetings', true],
      ['SoA', true],
    ],
  );
  toggle.click();
  assert.equal(menu.classList.contains('isms-rail-collapsed'), true);
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(toggle.textContent, 'Expand menu');
  assert.equal(toggle.title, 'Expand the menu');
  assert.equal(toggle.querySelector('i').className, 'bi bi-chevron-double-right');
  assert.equal(titles()[3], 'Policies & processes');
  toggle.click();
  assert.equal(menu.classList.contains('isms-rail-collapsed'), false);
  assert.equal(toggle.querySelector('i').className, 'bi bi-chevron-double-left');
  assert.ok(titles().every((title) => title === ''));

  // A browser that folded the menu before finds it folded, and remembers changes.
  const stored = { 'keen.isms.menu': 'collapsed' };
  const storage = {
    getItem: (key) => stored[key] ?? null,
    setItem: (key, value) => {
      stored[key] = value;
    },
  };
  const again = await fixture(t, { globals: { localStorage: storage } });
  assert.equal(again.el('ismsTabs').classList.contains('isms-rail-collapsed'), true);
  assert.equal(again.el('toggleIsmsRail').getAttribute('aria-expanded'), 'false');
  again.el('toggleIsmsRail').click();
  assert.equal(stored['keen.isms.menu'], 'open');
  again.el('toggleIsmsRail').click();
  assert.equal(stored['keen.isms.menu'], 'collapsed');
});

test('the folders fold away so the policies take the whole row', async (t) => {
  const ui = await fixture(t);
  const folders = ui.el('docFolderColumn');
  const list = ui.el('docListColumn');
  assert.equal(folders.hidden, false);
  assert.equal(ui.el('showDocFolders').hidden, true);
  assert.ok(list.classList.contains('col-xl-9'));
  ui.el('hideDocFolders').click();
  assert.equal(folders.hidden, true);
  assert.equal(ui.el('showDocFolders').hidden, false);
  assert.equal(ui.el('showDocFolders').textContent.trim(), 'Folders');
  assert.ok(!list.classList.contains('col-xl-9'));
  assert.ok(list.classList.contains('col-12'));
  // The keyboard follows to the button that brings them back.
  assert.equal(ui.document.activeElement, ui.el('showDocFolders'));
  ui.el('showDocFolders').click();
  assert.equal(folders.hidden, false);
  assert.equal(ui.el('showDocFolders').hidden, true);
  assert.ok(list.classList.contains('col-xl-9'));
  assert.equal(ui.document.activeElement, ui.el('hideDocFolders'));

  const stored = { 'keen.isms.folders': 'collapsed' };
  const storage = {
    getItem: (key) => stored[key] ?? null,
    setItem: (key, value) => {
      stored[key] = value;
    },
  };
  const again = await fixture(t, { globals: { localStorage: storage } });
  assert.equal(again.el('docFolderColumn').hidden, true);
  assert.equal(again.el('showDocFolders').hidden, false);
  // The menu's own choice is kept apart.
  assert.equal(again.el('ismsTabs').classList.contains('isms-rail-collapsed'), false);
  again.el('showDocFolders').click();
  assert.equal(stored['keen.isms.folders'], 'open');
  again.el('hideDocFolders').click();
  assert.equal(stored['keen.isms.folders'], 'collapsed');
});

test('document tags sit with the other policy filters', async (t) => {
  const ui = await fixture(t);
  const filters = ui.document.querySelector('[data-mosp-filter-section="documents-isms-filters"]');
  const tags = ui.el('docTagCloud');
  assert.ok(filters.contains(tags), 'the tags fold away with the filter section');
  assert.equal(tags.previousElementSibling.textContent, 'Tags');
  assert.equal(tags.getAttribute('aria-label'), 'Filter documents by tag');
  // The search box comes first, then the tags.
  assert.ok(
    filters.querySelector('#documentsTableFilter').compareDocumentPosition(tags) &
      ui.window.Node.DOCUMENT_POSITION_FOLLOWING,
  );
});

test('the folded menu gives up the width its labels needed', async () => {
  // The design system gives the rail a minimum width and Keen a fixed basis;
  // folded to icons, both are released so the page gains the room.
  const css = await readFile(new URL('../public/styles.css', import.meta.url), 'utf8');
  const rule = /\.isms-tab-nav\.isms-rail-collapsed\s*\{([^}]*)\}/.exec(css)[1];
  for (const declaration of ['flex: 0 0 auto', 'width: auto', 'min-width: 0'])
    assert.ok(rule.includes(declaration + ';'), declaration);
  // Only on a wide screen: a narrow one has the menu button instead.
  const before = css.slice(0, css.indexOf('.isms-tab-nav.isms-rail-collapsed'));
  assert.match(before.slice(before.lastIndexOf('@media')), /^@media \(min-width: 992px\) \{/);
});
