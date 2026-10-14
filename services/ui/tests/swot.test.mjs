import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { loadUi } from './support/load-ui.mjs';
import { page, settle } from './support/page-fixture.mjs';

test('planning boards escape hostile text and offer keyboard status controls', async () => {
  const ui = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url) => url },
    html: '<main id="host"></main>',
  });
  const host = ui.window.document.getElementById('host');
  const moves = [];
  ui.api.renderBoard(
    host,
    [
      {
        id: 'safe-id',
        title: '<img src=x onerror=alert(1)>',
        kind: 'threat',
        significance: 5,
        status: 'planned',
      },
    ],
    { view: 'quadrant', framework: 'F1', onMove: (...args) => moves.push(args) },
  );
  assert.equal(host.querySelectorAll('img').length, 0);
  assert.match(host.textContent, /<img src=x/);
  const select = host.querySelector('select');
  select.value = 'in_progress';
  select.dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(moves, [['safe-id', 'in_progress']]);
  ui.window.close();
});

test('timeline shows dated items and hierarchy handles missing parents without HTML injection', async () => {
  const ui = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url) => url },
    html: '<main id="host"></main>',
    setup(window) {
      window.d3 = d3;
    },
  });
  const host = ui.window.document.getElementById('host');
  const rows = [
    {
      id: 'one',
      title: '<svg onload=alert(1)>',
      kind: 'opportunity',
      significance: 4,
      status: 'planned',
      start_date: '2026-10-01',
      target_date: '2026-11-01',
      parent_id: 'outside-page',
    },
    { id: 'two', title: 'Undated', kind: 'threat', significance: 2 },
  ];
  ui.api.renderTimeline(host, rows, 'F1');
  assert.equal(host.querySelectorAll('rect').length, 1);
  assert.match(host.textContent, /1 without a target date/);
  assert.equal(host.querySelectorAll('[onload]').length, 0);
  ui.api.renderHierarchy(host, rows, 'F1');
  assert.equal(host.querySelectorAll('a').length, 2);
  assert.equal(host.querySelectorAll('svg').length, 0);
  ui.window.close();
});

test('SWOT workspace filters on the server and preserves version on status change', async (t) => {
  let state = 'defined';
  const ui = await page(t, 'isms-swot', {
    get(url) {
      if (url.pathname === '/api/v1/isms/meta') return { controls: [], clauses: [] };
      if (url.pathname === '/api/v1/isms/swot')
        return {
          items: [
            {
              id: 's1',
              title: 'Continuity',
              kind: 'threat',
              significance: 4,
              status: state,
              version: 3,
            },
          ],
          total: 1,
        };
      throw Error('Unexpected GET ' + url);
    },
    patch(url, body) {
      assert.equal(body.expected_version, 3);
      state = body.status;
      return { ok: true };
    },
  });
  const doc = ui.window.document;
  await settle(() => doc.querySelector('[data-status-id]'));
  const select = doc.querySelector('[data-status-id]');
  select.value = 'planned';
  select.dispatchEvent(new ui.window.Event('change'));
  await settle(() => state === 'planned');
  doc.getElementById('kindFilter').value = 'threat';
  doc.getElementById('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.calls.some((c) => c.url.includes('kind=threat')));
});

test('SWOT detail renders escaped relationships and submits bounded fields with a version', async (t) => {
  const item = {
    id: 's1',
    title: '<img src=x>',
    kind: 'threat',
    description: 'Context',
    response: 'Exercise continuity',
    significance: 4,
    status: 'planned',
    version: 2,
    links: [
      {
        target_type: 'document',
        target_id: 'd1',
        title: '<script>bad()</script>',
        url: '/isms-document.html?id=d1',
      },
    ],
    controls: [],
    clauses: [],
  };
  const ui = await page(t, 'isms-swot', {
    query: '&id=s1',
    get(url) {
      if (url.pathname === '/api/v1/isms/swot/s1') return item;
      if (url.pathname === '/api/v1/isms/meta') return { users: [] };
      if (url.pathname === '/api/v1/isms/swot') return { items: [item], total: 1 };
      if (url.pathname.includes('/changelog')) return { items: [] };
      if (url.pathname.startsWith('/api/v1/isms/swot-choices/')) return { items: [] };
      if (url.pathname.startsWith('/api/v1/questions/entities/')) return { threads: [] };
      throw Error('Unexpected GET ' + url);
    },
    patch(url, body) {
      assert.equal(body.expected_version, 2);
      assert.equal(body.title, 'Updated');
      return { ...item, ...body };
    },
  });
  await settle(() => ui.el('pageTitle').textContent === item.title);
  assert.equal(ui.el('relatedList').querySelectorAll('script').length, 0);
  assert.match(ui.el('relatedList').textContent, /<script>bad/);
  assert.equal(ui.el('editor').disabled, false);
  ui.el('title').value = 'Updated';
  ui.el('itemForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.calls.some((c) => c.method === 'PATCH'));
});

test('quadrant creation links preserve framework and type; overview is read only', async (t) => {
  const ui = await loadUi('pages/planning-views.js', {
    html: '<main id="host"></main>',
    vendor: { withFramework: (url, fw) => url + '&framework=' + fw },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.renderBoard(host, [], { view: 'quadrant', framework: 'A', canCreate: true });
  const links = [...host.querySelectorAll('a')];
  assert.equal(links.length, 4);
  for (const [i, kind] of ['strength', 'weakness', 'opportunity', 'threat'].entries()) {
    const url = new URL(links[i].href);
    assert.equal(url.pathname, '/swot.html');
    assert.equal(url.searchParams.get('kind'), kind);
    assert.equal(url.searchParams.get('framework'), 'A');
    assert.equal(url.searchParams.get('new'), '1');
  }
  ui.api.renderBoard(host, [{ id: 's1', kind: 'strength', title: 'Team', status: 'defined' }], {
    view: 'quadrant',
    framework: 'A',
  });
  assert.equal(host.querySelectorAll('button, select, [draggable], .btn').length, 0);
});

for (const kind of ['strength', 'weakness', 'opportunity', 'threat', '<img src=x>']) {
  test(`new SWOT preselects only valid quadrant: ${kind}`, async (t) => {
    let saved;
    const ui = await page(t, 'isms-swot', {
      htmlName: 'swot',
      query: '&new=1&kind=' + encodeURIComponent(kind),
      get(url) {
        if (url.pathname === '/api/v1/isms/meta') {
          assert.equal(url.searchParams.get('framework'), 'A');
          return {
            users: [],
            controls: [{ id: 'c1', ref: 'A.1', title: '<img src=x>' }],
            clauses: [{ id: 'cl1', ref: '6.1', title: 'Planning' }],
          };
        }
        if (url.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
        throw Error('Unexpected GET ' + url);
      },
      post(url, body) {
        saved = body;
        return { id: 's1' };
      },
    });
    await settle(() => !ui.el('editor').disabled);
    assert.equal(ui.el('kind').value, kind.startsWith('<') ? 'strength' : kind);
    for (const id of ['controls', 'clauses']) {
      assert.equal(ui.el(id).tagName, 'SELECT');
      assert.equal(ui.el(id).multiple, true);
      ui.el(id).options[0].selected = true;
    }
    assert.equal(ui.el('controls').querySelectorAll('img').length, 0);
    assert.equal(ui.el('startDate').required, false);
    assert.equal(ui.el('targetDate').required, false);
    ui.el('title').value = 'New item';
    ui.el('itemForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
    await settle(() => saved);
    assert.deepEqual(Array.from(saved.controls), ['c1']);
    assert.deepEqual(Array.from(saved.clauses), ['cl1']);
    assert.equal(saved.start_date, null);
    assert.equal(saved.target_date, null);
  });
}

test('graph wraps complete labels as text and allocates space for every line', async (t) => {
  const ui = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url) => url },
    html: '<main id="host"></main>',
    setup(window) {
      window.d3 = d3;
    },
  });
  t.after(ui.close);
  const title = 'Well established, financially stable and independent';
  const target = '<img src=x onerror=alert(1)> ' + 'Long label '.repeat(30);
  ui.api.renderGraph(
    ui.document.getElementById('host'),
    {
      title,
      significance: 3,
      links: [{ title: target, target_type: 'document', url: '/isms-document.html?id=d1' }],
    },
    'A',
  );
  const labels = ui.document.querySelectorAll('svg text');
  assert.equal([...labels[0].querySelectorAll('tspan')].map((x) => x.textContent).join(' '), title);
  assert.equal(
    [...labels[1].querySelectorAll('tspan')].map((x) => x.textContent).join(' '),
    'Document: ' + target.trim(),
  );
  assert.equal(ui.document.querySelectorAll('img, [onerror]').length, 0);
  const height = Number(ui.document.querySelector('svg').getAttribute('viewBox').split(' ')[3]);
  for (const text of labels) {
    assert.ok(Number(text.getAttribute('y')) + (text.children.length - 1) * 19 < height);
  }
});

test('SWOT filters default to collapsed and question composers share consistent sizing', async (t) => {
  const ui = await page(t, 'isms-swot', {
    htmlName: 'swot',
    query: '&id=s1',
    get(url) {
      if (url.pathname === '/api/v1/isms/swot/s1')
        return {
          id: 's1',
          title: 'Team',
          kind: 'strength',
          status: 'defined',
          significance: 3,
          version: 1,
          links: [],
        };
      if (url.pathname === '/api/v1/isms/meta') return { users: [] };
      if (url.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (url.pathname.includes('/changelog') || url.pathname.includes('swot-choices'))
        return { items: [] };
      if (url.pathname.includes('/questions/entities/'))
        return { threads: [{ id: 'q1', status: 'unanswered', posts: [] }] };
      throw Error('Unexpected GET ' + url);
    },
  });
  await settle(() => ui.el('questionList').querySelector('textarea'));
  const reply = ui.el('questionList').querySelector('textarea');
  assert.equal(reply.rows, ui.el('questionBody').rows);
  assert.ok(reply.closest('.planning-question-form'));
  assert.ok(ui.el('questionBody').closest('.planning-question-form'));
  assert.ok(reply.closest('label').classList.contains('d-block'));
  assert.equal(ui.el('filters').dataset.mospFilterDefault, 'collapsed');
  assert.ok(ui.el('filters').querySelector('[data-mosp-filter-body]').hidden);
  assert.equal(ui.el('view').closest('#filters'), null);
});

test('quadrants nest children beneath parents regardless of list order and retain missing-parent context', async (t) => {
  const ui = await loadUi('pages/planning-views.js', {
    html: '<main id="host"></main>',
    vendor: { withFramework: (url) => url },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const rows = [
    { id: 'child', title: 'AI (various applications)', kind: 'opportunity', parent_id: 'parent' },
    { id: 'parent', title: 'New technologies', kind: 'opportunity' },
    { id: 'orphan', title: 'Elsewhere', kind: 'opportunity', parent_id: 'outside' },
  ];
  ui.api.renderBoard(host, rows, { view: 'quadrant', framework: 'A' });
  const column = host.querySelector('[data-column="opportunity"]');
  assert.equal(column.querySelectorAll('.planning-card').length, 3);
  const root = column.querySelector('.planning-quadrant-tree > li');
  assert.equal(root.querySelector('article a').textContent, 'New technologies');
  assert.equal(
    root.querySelector(':scope > ul article a').textContent,
    'AI (various applications)',
  );
  assert.match(column.textContent, /View parent outside these results/);
  assert.equal(
    column.querySelector('a[href*="id=outside"]').textContent,
    'View parent outside these results',
  );
  assert.equal(host.querySelectorAll('select, [draggable]').length, 0);
});

for (const [start, end] of [
  ['2026-10-01', '2027-01-01'],
  ['2026-12-31', '2027-01-01'],
  ['2024-01-01', '2030-01-01'],
]) {
  test(`timeline date ticks stay spaced for ${start} to ${end}`, async (t) => {
    const ui = await loadUi('pages/planning-views.js', {
      html: '<main id="host"></main>',
      vendor: { withFramework: (url) => url },
      setup(window) {
        window.d3 = d3;
      },
    });
    t.after(ui.close);
    ui.api.renderTimeline(
      ui.document.getElementById('host'),
      [{ id: 'x', title: 'Dates', start_date: start, target_date: end }],
      'A',
    );
    const ticks = [...ui.document.querySelectorAll('.planning-date-axis .tick')];
    assert.ok(ticks.length > 0 && ticks.length <= 7);
    const positions = ticks.map((tick) =>
      Number(tick.getAttribute('transform').match(/translate\(([^,]+)/)[1]),
    );
    for (let i = 1; i < positions.length; i++) assert.ok(positions[i] - positions[i - 1] >= 110);
  });
}

for (const rejected of [false, true]) {
  test(`kanban drop ${rejected ? 'recovers saved state after rejection' : 'saves immediately and highlights the card'}`, async (t) => {
    let state = 'defined';
    const ui = await page(t, 'isms-swot', {
      htmlName: 'swot',
      get(url) {
        if (url.pathname === '/api/v1/isms/meta') return {};
        if (url.pathname === '/api/v1/isms/swot')
          return {
            items: [
              {
                id: 's1',
                title: 'Team',
                kind: 'strength',
                status: state,
                significance: 3,
                version: 4,
              },
            ],
            total: 1,
          };
        throw Error('Unexpected GET ' + url);
      },
      patch(url, body) {
        assert.equal(body.expected_version, 4);
        assert.equal(body.status, 'planned');
        if (rejected) throw Error('Changed by another user');
        state = body.status;
        return { ok: true };
      },
    });
    ui.el('view').value = 'kanban';
    ui.el('view').dispatchEvent(new ui.window.Event('change'));
    assert.equal(ui.el('planningHint').hidden, false);
    const card = ui.el('planningView').querySelector('[data-drag-id]');
    const column = ui.el('planningView').querySelector('[data-column="planned"]');
    const transfer = { setData() {} };
    const drag = (element, type) => {
      const event = new ui.window.Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'dataTransfer', { value: transfer });
      element.dispatchEvent(event);
    };
    drag(card, 'dragstart');
    assert.ok(card.classList.contains('planning-dragging'));
    drag(column, 'dragover');
    assert.ok(column.classList.contains('planning-drop-target'));
    drag(column, 'drop');
    await settle(() => ui.calls.some((call) => call.method === 'PATCH'));
    await settle(() => !ui.el('planningView').hasAttribute('aria-busy'));
    const saved = ui.el('planningView').querySelector('[data-drag-id]');
    assert.equal(saved.closest('[data-column]').dataset.column, rejected ? 'defined' : 'planned');
    assert.equal(saved.classList.contains('planning-saved'), !rejected);
    assert.equal(ui.el('planningView').querySelectorAll('.planning-drop-target').length, 0);
    assert.match(
      ui.el('status').textContent,
      rejected ? /Changed by another user/ : /Status changed to Planned/,
    );
  });
}

test('related filter appears only with usable choices and clears immediately; stale choices are ignored', async (t) => {
  let resolveChoices;
  const ui = await page(t, 'isms-swot', {
    htmlName: 'swot',
    get(url) {
      if (url.pathname === '/api/v1/isms/meta') return {};
      if (url.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (url.pathname === '/api/v1/isms/swot-choices/asset')
        return { items: [{ target_id: 'a1', title: 'Service' }] };
      if (url.pathname === '/api/v1/isms/swot-choices/vendor')
        return new Promise((resolve) => {
          resolveChoices = resolve;
        });
      throw Error('Unexpected GET ' + url);
    },
  });
  assert.equal(ui.el('relatedItemField').hidden, true);
  const change = (id, value) => {
    ui.el(id).value = value;
    ui.el(id).dispatchEvent(new ui.window.Event('change'));
  };
  change('relatedType', 'asset');
  await settle(() => !ui.el('relatedItemField').hidden);
  change('relatedItem', 'a1');
  await settle(() => ui.calls.some((call) => call.url.includes('target_id=a1')));
  change('relatedType', 'vendor');
  assert.equal(ui.el('relatedItemField').hidden, true);
  assert.equal(ui.el('relatedItem').value, '');
  change('relatedType', '');
  resolveChoices({ items: [{ target_id: 'v1', title: 'Late result' }] });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(ui.el('relatedItemField').hidden, true);
  assert.equal(ui.el('relatedItem').value, '');
  assert.equal(ui.el('relatedItem').disabled, true);
});

test('legacy objective planning URL forwards to the unified Objectives board in the same framework', async (t) => {
  let destination;
  await page(t, 'isms-swot', {
    query: '&mode=objectives',
    globals: {
      location: {
        search: '?framework=A&mode=objectives',
        replace(url) {
          destination = url;
        },
      },
    },
    get(url) {
      throw Error('Unexpected GET ' + url);
    },
  });
  const url = new URL(destination, 'https://keen.example');
  assert.equal(url.pathname, '/isms.html');
  assert.equal(url.searchParams.get('tab'), 'objectives');
  assert.equal(url.searchParams.get('view'), 'kanban');
  assert.equal(url.searchParams.get('framework'), 'A');
});
