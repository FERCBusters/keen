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
  const ui = await page(t, 'swot', {
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
  const ui = await page(t, 'swot', {
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
    const ui = await page(t, 'swot', {
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
        if (url.pathname === '/api/v1/isms/swot-choices/objective') return { items: [] };
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
  const ui = await page(t, 'swot', {
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
  await settle(() => ui.el('questionPanel').querySelector('[data-thread] textarea'));
  const reply = ui.el('questionPanel').querySelector('[data-thread] textarea');
  assert.equal(reply.rows, ui.el('questionBody').rows);
  assert.equal(ui.el('questionBody').placeholder, 'Write a question about this SWOT item');
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
    const ui = await page(t, 'swot', {
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
  const ui = await page(t, 'swot', {
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

test('compact quadrant cards expose a five-bar rating and optional date beside an accessible status', async (t) => {
  const ui = await loadUi('pages/planning-views.js', {
    html: '<main id="host"></main>',
    vendor: { withFramework: (url) => url },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const rows = [
    {
      id: 's1',
      title: '<img src=x> Independent team',
      kind: 'strength',
      significance: 4,
      status: 'planned',
      target_date: '2026-12-31',
    },
  ];
  const changes = [];
  ui.api.renderBoard(host, rows, { view: 'quadrant', onMove: (...args) => changes.push(args) });
  const card = host.querySelector('.planning-card-compact');
  assert.equal(card.querySelectorAll('.planning-significance-bar').length, 5);
  assert.equal(card.querySelectorAll('.planning-significance-bar.is-filled').length, 4);
  assert.equal(
    card.querySelector('[role="img"]').getAttribute('aria-label'),
    'Significance: 4 out of 5',
  );
  assert.equal(card.querySelector('time').dateTime, '2026-12-31');
  assert.equal(card.querySelectorAll('img').length, 0);
  assert.equal(
    card.querySelector('select').getAttribute('aria-label'),
    'Status for <img src=x> Independent team',
  );
  assert.equal(card.querySelectorAll('.planning-compact-row > .planning-compact-meta').length, 1);
  const select = card.querySelector('select');
  select.value = 'completed';
  select.dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(changes, [['s1', 'completed']]);
  ui.api.renderBoard(host, [{ ...rows[0], target_date: null }], { view: 'quadrant' });
  assert.equal(host.querySelectorAll('time, select, [draggable]').length, 0);
  assert.equal(host.querySelector('.planning-compact-status-text').textContent, 'Planned');
  assert.ok(!host.textContent.includes('No target date'));
});

test('new SWOT relationships remain local, deduplicate and travel in the final create payload', async (t) => {
  let sent;
  const target = {
    target_type: 'objective',
    target_id: 'o1',
    title: '<img src=x onerror=alert(1)>',
    url: '/isms-objective.html?id=o1',
  };
  const ui = await page(t, 'swot', {
    query: '&new=1',
    get(url) {
      if (url.pathname === '/api/v1/isms/meta') return { users: [], controls: [], clauses: [] };
      if (url.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (url.pathname === '/api/v1/isms/swot-choices/objective') return { items: [target] };
      throw Error('Unexpected GET ' + url);
    },
    post(url, body) {
      assert.equal(url.pathname, '/api/v1/isms/swot');
      sent = body;
      throw Error('Save rejected');
    },
  });
  await settle(() => ui.el('linkItem').options.length === 2);
  assert.equal(ui.el('linkForm').hidden, false);
  assert.equal(ui.el('saveDraft').hidden, false);
  ui.el('title').value = 'Draft';
  ui.el('linkItem').value = 'o1';
  const submit = () =>
    ui.el('linkForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  submit();
  submit();
  assert.equal(ui.el('relatedList').querySelectorAll('[data-unlink-id]').length, 1);
  assert.equal(ui.el('relatedList').querySelector('img'), null);
  assert.equal(ui.calls.filter((call) => call.method === 'POST').length, 0);
  ui.el('relatedList').querySelector('[data-unlink-id]').click();
  assert.equal(ui.el('relatedList').querySelectorAll('[data-unlink-id]').length, 0);
  submit();
  ui.el('itemForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => sent);
  assert.equal(sent.links.length, 1);
  assert.equal(sent.links[0].target_type, 'objective');
  assert.equal(sent.links[0].target_id, 'o1');
  assert.deepEqual(Object.keys(sent.links[0]).sort(), ['target_id', 'target_type']);
  await settle(() => ui.el('status').textContent.includes('Save rejected'));
  assert.equal(ui.el('title').value, 'Draft');
  assert.equal(ui.el('relatedList').querySelectorAll('[data-unlink-id]').length, 1);
});

test('SWOT question authors can reply to their own threads only', async (t) => {
  const posted = [];
  const seen = [];
  const ui = await page(t, 'swot', {
    query: '&id=s1',
    // /me identifies the user by user_id; `user` is the username string.
    me: { user: 'erin', user_id: 'u7', can_view_isms: true, can_create_questions: true },
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
        return {
          threads: [
            {
              id: 'q1',
              status: 'answered',
              created_by_user_id: 'u7',
              posts: [{ author_username: 'erin', body: '<b>Why?</b>' }],
            },
            {
              id: 'q2',
              status: 'unanswered',
              created_by_user_id: 'u9',
              posts: [{ author_username: 'sam', body: 'Other' }],
            },
            { id: 'q3', status: 'unanswered', created_by_user_id: null, posts: [] },
          ],
        };
      throw Error('Unexpected GET ' + url);
    },
    post(url, body) {
      // Reading the threads marks their replies seen for the author.
      if (url.pathname.endsWith('/mark-seen')) seen.push(url.pathname);
      else posted.push({ path: url.pathname, body: JSON.parse(JSON.stringify(body)) });
      return {};
    },
  });
  await settle(() => seen.length === 1);
  assert.deepEqual(seen, ['/api/v1/questions/entities/isms_swot/s1/mark-seen']);
  const panel = ui.el('questionPanel');
  const replies = [...panel.querySelectorAll('[data-question-action="reply"]')];
  assert.deepEqual(
    replies.map((button) => button.closest('[data-thread]').dataset.thread),
    ['q1'],
  );
  assert.equal(panel.querySelector('b'), null);
  assert.match(panel.textContent, /<b>Why\?<\/b>/);
  ui.el('questionReply-q1').value = 'Thanks, resolved';
  replies[0].click();
  await settle(() => posted.length === 1);
  assert.deepEqual(posted[0], {
    path: '/api/v1/questions/q1/posts',
    body: { body: 'Thanks, resolved' },
  });
});

test('a kanban drag starts only from the card, not its controls, and drop targets clear when left', async (t) => {
  const ui = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url) => url },
    html: '<main id="host"></main>',
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const moves = [];
  ui.api.renderBoard(
    host,
    [{ id: 'one', title: 'Train operators', kind: 'threat', significance: 3, status: 'planned' }],
    { view: 'kanban', framework: 'F1', onMove: (...args) => moves.push(args) },
  );
  const card = host.querySelector('[data-drag-id="one"]');
  const select = card.querySelector('select');
  const transfer = { setData() {}, effectAllowed: '', dropEffect: '' };
  const drag = (type, target, extra = {}) => {
    const event = new ui.window.Event(type, { bubbles: true, cancelable: true });
    Object.assign(event, { dataTransfer: transfer }, extra);
    target.dispatchEvent(event);
    return event;
  };
  // Dragging the status menu is not a card move.
  const fromSelect = drag('dragstart', select);
  assert.equal(fromSelect.defaultPrevented, true);
  assert.equal(card.classList.contains('planning-dragging'), false);
  const column = host.querySelector('[data-column="in_progress"]');
  drag('dragover', column);
  assert.equal(column.classList.contains('planning-drop-target'), false);

  // Dragging the card itself highlights the column under it until the pointer leaves.
  drag('dragstart', card);
  assert.equal(card.classList.contains('planning-dragging'), true);
  drag('dragover', column);
  assert.equal(column.classList.contains('planning-drop-target'), true);
  drag('dragleave', column, { relatedTarget: column.querySelector('h2') });
  assert.equal(column.classList.contains('planning-drop-target'), true);
  drag('dragleave', column, { relatedTarget: host });
  assert.equal(column.classList.contains('planning-drop-target'), false);
  drag('dragover', column);
  drag('drop', column);
  assert.deepEqual(moves, [['one', 'in_progress']]);
  assert.equal(host.querySelector('.planning-dragging, .planning-drop-target'), null);
});

test('the timeline needs D3, and plots target-only items as diamonds', async (t) => {
  const without = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url) => url },
    html: '<main id="host"></main>',
  });
  t.after(without.close);
  const bare = without.document.getElementById('host');
  without.api.renderTimeline(
    bare,
    [{ id: 'one', title: 'Dated', kind: 'threat', target_date: '2026-11-01' }],
    'F1',
  );
  assert.equal(bare.textContent, 'Timeline unavailable. Use the board to view dates.');
  assert.equal(bare.querySelector('svg'), null);

  const ui = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url) => url },
    html: '<main id="host"></main>',
    setup(window) {
      window.d3 = d3;
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.renderTimeline(
    host,
    [
      {
        id: 'done',
        title: 'Completed work',
        kind: 'strength',
        status: 'completed',
        start_date: '2026-10-01',
        target_date: '2026-10-20',
      },
      { id: 'target', title: 'Target only', kind: 'threat', target_date: '2026-11-01' },
    ],
    'F1',
  );
  assert.equal(host.querySelectorAll('rect').length, 1);
  assert.equal(host.querySelector('rect').getAttribute('fill'), '#198754');
  const diamonds = [...host.querySelectorAll('path[fill="#6f42c1"]')];
  assert.equal(diamonds.length, 1);
  assert.match(diamonds[0].getAttribute('transform'), /^translate\(/);
  assert.match(host.textContent, /2 items with target dates are plotted; 0 without/);
});

test('the graph lists controls and clauses as records and breaks unbroken words', async (t) => {
  const ui = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url, fw) => `${url}&framework=${fw}` },
    html: '<main id="host"></main>',
    setup(window) {
      window.d3 = d3;
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const long = 'x'.repeat(120);
  ui.api.renderGraph(
    host,
    {
      title: long,
      significance: 2,
      controls: [{ id: 'c 1', ref: 'A.5.1', title: 'Policies' }],
      clauses: [{ id: 'k1', ref: '4.1' }],
    },
    'A',
  );
  const links = [...host.querySelectorAll('svg a')].map((a) => [
    a.getAttribute('aria-label'),
    a.getAttribute('href'),
  ]);
  assert.deepEqual(links, [
    ['Control: A.5.1 Policies', '/control.html?id=c%201&framework=A'],
    ['Clause: 4.1 ', '/clause.html?id=k1&framework=A'],
  ]);
  // A word longer than the column is split so every character stays visible.
  const rootLines = [...host.querySelectorAll('svg text')[0].querySelectorAll('tspan')].map(
    (x) => x.textContent,
  );
  assert.deepEqual(rootLines, ['x'.repeat(38), 'x'.repeat(38), 'x'.repeat(38), 'x'.repeat(6)]);
});

test('a second status change while one is saving is ignored, and a failed refresh is reported', async (t) => {
  let release;
  let listFails = false;
  const ui = await page(t, 'swot', {
    get(url) {
      if (url.pathname === '/api/v1/isms/meta') return { controls: [], clauses: [] };
      if (url.pathname === '/api/v1/isms/swot') {
        if (listFails) throw new Error('List offline');
        return {
          items: [
            {
              id: 's1',
              title: 'Continuity',
              kind: 'threat',
              significance: 4,
              status: 'defined',
              version: 3,
            },
          ],
          total: 1,
        };
      }
      throw Error('Unexpected GET ' + url);
    },
    patch: () => new Promise((resolve, reject) => (release = reject)),
  });
  const doc = ui.window.document;
  await settle(() => doc.querySelector('[data-status-id="s1"]'));
  const move = (status) => {
    const select = doc.querySelector('[data-status-id="s1"]');
    select.value = status;
    select.dispatchEvent(new ui.window.Event('change'));
  };
  move('planned');
  await settle(() => release);
  assert.equal(doc.getElementById('planningView').getAttribute('aria-busy'), 'true');
  move('completed');
  // The board is redrawn from the stored status and nothing else is sent.
  assert.equal(doc.querySelector('[data-status-id="s1"]').value, 'defined');
  assert.equal(ui.calls.filter((c) => c.method === 'PATCH').length, 1);

  listFails = true;
  release(new Error('Status refused'));
  await settle(() => ui.el('status').textContent === 'Error: List offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(doc.getElementById('planningView').hasAttribute('aria-busy'), false);
  assert.equal(doc.querySelector('[data-status-id="s1"]').value, 'defined');
});
