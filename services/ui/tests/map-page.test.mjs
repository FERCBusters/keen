// The Map: the overview graph, choosing and following a record, the filters
// that decide what is drawn, and what the page does when it cannot load.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const HOSTILE = '<img src=x onerror="alert(1)">';
const KINDS = [
  ['control', 'Controls'],
  ['clause', 'Clauses'],
  ['risk', 'Risks'],
  ['document', 'Policies and processes'],
  ['person', 'People'],
  ['source', 'Evidence sources'],
].map(([key, label]) => ({ key, label, count: 0 }));

const record = (kind, id, label, detail = '') => ({
  id: `${kind}:${id}`,
  kind,
  label,
  detail,
  url: kind === 'source' ? `/source.html?source=${id}` : `/${kind}.html?id=${id}`,
});
const link = (source, target, relation, value = 1) => ({ source, target, relation, value });

const A1 = record('control', 'c1', 'A.1', HOSTILE);
const A2 = record('control', 'c2', 'A.2', 'Change');
const C41 = record('clause', 'k1', '4.1', 'Context');
const OUTAGE = record('risk', 'r1', 'Outage', 'Payroll');
const POLICY = record('document', 'd1', 'Backup policy', 'policy');
const ADA = record('person', 'p1', 'Ada');
const ALONE = record('person', 'p2', 'Zed', 'Nobody links to Zed');
const JENKINS = record('source', 'jenkins', 'Jenkins', '2 mapped events');

function overview(extra = {}) {
  return {
    framework: 'A',
    nodes: [A1, C41, OUTAGE, POLICY, ADA, ALONE],
    links: [
      link(C41.id, A1.id, 'applies'),
      link(OUTAGE.id, A1.id, 'mitigates'),
      link(OUTAGE.id, ADA.id, 'affects'),
      link(POLICY.id, A1.id, 'mapped'),
      link(POLICY.id, C41.id, 'future'),
    ],
    kinds: KINDS,
    truncated: false,
    notes: [],
    ...extra,
  };
}

async function map(t, { data = overview(), neighbours, query = '', scripts, me } = {}) {
  const requests = [];
  const ui = await page(t, 'map', {
    query,
    me,
    scripts: scripts ?? [d3, 'window.d3 = d3;'],
    get: async (url) => {
      requests.push(url.pathname + url.search);
      if (url.pathname === '/api/v1/map/neighbours') {
        const answer = neighbours?.(url.searchParams.get('node'));
        if (!answer) throw new Error('Record not available');
        return answer;
      }
      if (url.pathname === '/api/v1/map') {
        const answer = typeof data === 'function' ? data(url) : data;
        if (!answer) throw new Error('The map is unavailable');
        return answer;
      }
      throw new Error('Unexpected GET ' + url);
    },
  });
  await settle(() => ui.el('mapMeta').textContent !== 'Loading');
  const group = (id) => ui.document.querySelector(`g.map-node[data-id="${id}"]`);
  const drawn = () =>
    [...ui.document.querySelectorAll('g.map-node')].map((g) => g.querySelector('text').textContent);
  const fire = (el, type, init = {}) =>
    el.dispatchEvent(new ui.window.MouseEvent(type, { bubbles: true, view: ui.window, ...init }));
  const kind = (key) => ui.document.querySelector(`#mapKinds [data-kind="${key}"]`);
  const panel = () => ui.el('mapPanel');
  const related = () =>
    [...panel().querySelectorAll('.map-related li')].map((li) => [
      li.querySelector('button').textContent,
      li.querySelector('.small-muted').textContent,
    ]);
  return { ui, requests, group, drawn, fire, kind, panel, related };
}

test('the map draws the linked records, coloured by kind, and counts them', async (t) => {
  const { ui, requests, group, drawn, kind } = await map(t);
  assert.deepEqual(requests, ['/api/v1/map?framework=A']);
  // A record nothing links to waits until it is asked for.
  assert.deepEqual(drawn().sort(), ['4.1', 'A.1', 'Ada', 'Backup policy', 'Outage']);
  assert.equal(ui.el('mapMeta').textContent, '5 records and 5 links shown');
  assert.equal(ui.document.querySelectorAll('line.map-link').length, 5);
  assert.equal(group(A1.id).querySelector('circle').getAttribute('fill'), '#2563eb');
  assert.equal(group(OUTAGE.id).querySelector('circle').getAttribute('fill'), '#dc2626');
  // Every record has a place, and a better linked one is drawn larger.
  assert.match(group(A1.id).getAttribute('transform'), /^translate\(-?[\d.]+,-?[\d.]+\)$/);
  const r = (id) => Number(group(id).querySelector('circle').getAttribute('r'));
  assert.ok(r(A1.id) > r(ADA.id));
  // Names and details are written as text.
  assert.equal(group(A1.id).querySelector('title').textContent, `Controls · A.1 · ${HOSTILE}`);
  assert.equal(group(ADA.id).querySelector('title').textContent, 'People · Ada');
  assert.equal(ui.document.querySelector('#mapCanvas img'), null);
  const titles = [...ui.document.querySelectorAll('line.map-link title')].map(
    (el) => el.textContent,
  );
  assert.ok(titles.includes('Outage · Mitigated by · A.1'));
  assert.ok(titles.includes('Backup policy · future · 4.1'));
  // The kinds on the map are listed with their counts; the others are not.
  assert.deepEqual(
    [...ui.el('mapKinds').querySelectorAll('button')].map((b) => [
      b.dataset.kind,
      b.textContent,
      b.getAttribute('aria-pressed'),
    ]),
    [
      ['control', 'Controls 1', 'true'],
      ['clause', 'Clauses 1', 'true'],
      ['risk', 'Risks 1', 'true'],
      ['document', 'Policies and processes 1', 'true'],
      ['person', 'People 2', 'true'],
    ],
  );
  assert.equal(kind('source'), null);

  // The records with no links join on request.
  ui.el('mapUnlinked').checked = true;
  ui.el('mapUnlinked').dispatchEvent(new ui.window.Event('change'));
  assert.ok(drawn().includes('Zed'));
  assert.equal(ui.el('mapMeta').textContent, '6 records and 5 links shown');
});

test('a kind of record is hidden and shown again from the legend', async (t) => {
  const { ui, drawn, kind } = await map(t);
  kind('risk').click();
  assert.deepEqual(drawn().sort(), ['4.1', 'A.1', 'Backup policy']);
  assert.equal(kind('risk').getAttribute('aria-pressed'), 'false');
  assert.equal(ui.el('mapMeta').textContent, '3 records and 3 links shown');
  kind('risk').click();
  assert.ok(drawn().includes('Outage') && drawn().includes('Ada'));

  // With every kind hidden the page says how to bring records back.
  for (const key of ['control', 'clause', 'risk', 'document', 'person']) kind(key).click();
  assert.equal(ui.document.querySelector('#mapCanvas svg'), null);
  assert.equal(
    ui.el('mapCanvas').textContent,
    'No record is shown. Show a kind of record, or the records with no links.',
  );
  assert.equal(ui.el('mapMeta').textContent, '0 records and 0 links shown');
  // Fitting an empty map does nothing.
  ui.el('mapFit').click();
  kind('person').click();
  assert.deepEqual(drawn(), []);
  kind('risk').click();
  assert.deepEqual(drawn().sort(), ['Ada', 'Outage']);
  assert.equal(ui.el('mapMeta').textContent, '2 records and 1 link shown');
});

test('choosing a record shows what it is and what it is linked to', async (t) => {
  const { ui, group, fire, panel, related } = await map(t);
  assert.match(panel().textContent, /Choose a record/);
  fire(group(A1.id), 'click');
  assert.equal(panel().querySelector('h2').textContent, 'A.1');
  assert.equal(panel().querySelector('p').textContent, HOSTILE);
  assert.equal(panel().querySelector('img'), null);
  assert.equal(panel().querySelector('.small-muted').textContent, 'Controls');
  assert.equal(ui.el('mapOpen').getAttribute('href'), '/control.html?id=c1&framework=A');
  assert.equal(panel().querySelector('h3').textContent, 'Linked records (3)');
  // Each link reads from the chosen record's side.
  assert.deepEqual(related(), [
    ['4.1', 'Applies to clause · Clauses'],
    ['Backup policy', 'Mapped record · Policies and processes'],
    ['Outage', 'Mitigates · Risks'],
  ]);
  assert.equal(new URL(ui.window.location.href).searchParams.get('focus'), A1.id);
  assert.ok(group(A1.id).classList.contains('map-selected'));
  // The chosen record and its neighbours stand out from the rest.
  const svg = ui.document.querySelector('#mapCanvas svg');
  assert.ok(svg.classList.contains('map-dim'));
  assert.ok(group(OUTAGE.id).classList.contains('map-near'));
  assert.ok(!group(ADA.id).classList.contains('map-near'));
  assert.equal(ui.document.querySelectorAll('line.map-link.map-near').length, 3);

  // Pointing at another record shows its neighbours, then the chosen one's again.
  fire(group(ADA.id), 'mouseenter');
  assert.ok(group(ADA.id).classList.contains('map-near'));
  assert.ok(!group(C41.id).classList.contains('map-near'));
  fire(group(ADA.id), 'mouseleave');
  assert.ok(group(C41.id).classList.contains('map-near'));

  // A record with nothing more to say shows its name alone.
  fire(group(ADA.id), 'click');
  assert.equal(panel().querySelector('h2').textContent, 'Ada');
  assert.equal(panel().querySelector('p'), null);
  fire(group(A1.id), 'click');

  // A linked record is chosen from the list, and the view moves to it.
  panel().querySelector('.map-related button').click();
  assert.equal(panel().querySelector('h2').textContent, '4.1');
  assert.deepEqual(related(), [
    ['A.1', 'Applicable control · Controls'],
    ['Backup policy', 'future · Policies and processes'],
  ]);
  assert.match(ui.document.querySelector('g.map-scene').getAttribute('transform'), /scale\(1\.4\)/);
  // Names stay out of the way at that distance.
  assert.ok(!svg.classList.contains('map-labels'));
  // Fitting the map moves the view back to all of it.
  const centred = ui.document.querySelector('g.map-scene').getAttribute('transform');
  ui.el('mapFit').click();
  assert.notEqual(ui.document.querySelector('g.map-scene').getAttribute('transform'), centred);
});

test('a record is dragged to where it reads best', async (t) => {
  const { ui, group, fire } = await map(t);
  const before = group(ADA.id).getAttribute('transform');
  fire(group(ADA.id), 'mousedown', { clientX: 10, clientY: 10 });
  fire(ui.window, 'mousemove', { clientX: 90, clientY: 70 });
  fire(ui.window, 'mouseup', { clientX: 90, clientY: 70 });
  assert.notEqual(group(ADA.id).getAttribute('transform'), before);
});

test("a record's related records are added beside it, once", async (t) => {
  const { ui, requests, group, drawn, fire, panel, related } = await map(t, {
    neighbours: (id) =>
      id === A1.id
        ? {
            ...overview(),
            // The record again, with a new name; a control; a source.
            nodes: [{ ...A1, label: 'A.1 renamed' }, A2, JENKINS],
            links: [
              link(JENKINS.id, A1.id, 'evidence', 2),
              link(JENKINS.id, A2.id, 'evidence', 1),
              // To a record that is not on the map: left out.
              link(JENKINS.id, 'control:elsewhere', 'evidence', 9),
            ],
            notes: ['Evidence sources are not shown: too slow'],
          }
        : null,
  });
  fire(group(A1.id), 'dblclick');
  await settle(() => drawn().includes('Jenkins'));
  assert.equal(
    requests.at(-1),
    `/api/v1/map/neighbours?framework=A&node=${encodeURIComponent(A1.id)}`,
  );
  assert.ok(drawn().includes('A.1 renamed') && drawn().includes('A.2'));
  assert.equal(ui.document.querySelectorAll('line.map-link').length, 7);
  assert.equal(ui.el('status').textContent, 'Evidence sources are not shown: too slow');
  assert.equal(ui.el('status').dataset.alert, 'warning');
  // The record is chosen, and says its related records are on the map.
  assert.equal(panel().querySelector('h2').textContent, 'A.1 renamed');
  assert.equal(ui.el('mapExpand').textContent, 'Related records shown');
  assert.equal(ui.el('mapExpand').disabled, true);
  // Evidence says how many events and opens them.
  assert.deepEqual(related().at(1), [
    'Jenkins',
    'Evidence from · Evidence sources · 2 events · Open events',
  ]);
  assert.equal(
    panel().querySelector('.map-related a').getAttribute('href'),
    '/events.html?source=jenkins&control=A.1+renamed&framework=A',
  );
  const evidence = ui.document.querySelectorAll('line.map-link-evidence');
  assert.equal(evidence.length, 2);
  // Asking again asks nothing.
  const asked = requests.length;
  fire(group(A1.id), 'dblclick');
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(requests.length, asked);

  // A source reads its evidence from its own side, and its button asks.
  fire(group(JENKINS.id), 'click');
  assert.deepEqual(related(), [
    ['A.1 renamed', 'Supplies evidence to · Controls · 2 events · Open events'],
    ['A.2', 'Supplies evidence to · Controls · 1 events · Open events'],
  ]);
  assert.equal(ui.el('mapOpen').getAttribute('href'), '/source.html?source=jenkins&framework=A');
  assert.equal(ui.el('mapExpand').textContent, 'Show related records');
  ui.el('mapExpand').click();
  await settle(() => ui.el('status').dataset.alert === 'danger');
  assert.equal(
    ui.el('status').textContent,
    'Could not show the related records: Record not available',
  );
  assert.equal(ui.el('mapExpand').disabled, false);
});

test('a record is found by name and shown even when its kind is hidden', async (t) => {
  const { ui, drawn, kind, panel, related } = await map(t);
  const search = (value) => {
    ui.el('mapSearch').value = value;
    ui.el('mapSearch').dispatchEvent(new ui.window.Event('input'));
    return [...ui.el('mapMatches').children].map((el) => el.textContent);
  };
  assert.equal(ui.el('mapMatches').hidden, true);
  // Names and details are searched, whatever their case.
  assert.deepEqual(search('PAYROLL'), ['OutageRisks']);
  assert.deepEqual(search('a'), [
    'A.1Controls',
    'AdaPeople',
    'Backup policyPolicies and processes',
    'OutageRisks',
  ]);
  assert.equal(ui.el('mapMatches').hidden, false);
  assert.match(search('nothing like this')[0], /^No record on the map matches/);
  assert.deepEqual(search(''), []);
  assert.equal(ui.el('mapMatches').hidden, true);

  // A record with no links, of a hidden kind, is drawn once it is chosen.
  kind('person').click();
  assert.ok(!drawn().includes('Zed'));
  search('zed');
  ui.el('mapMatches').querySelector('button').click();
  assert.equal(ui.el('mapSearch').value, '');
  assert.equal(ui.el('mapMatches').hidden, true);
  assert.equal(panel().querySelector('h2').textContent, 'Zed');
  assert.ok(drawn().includes('Zed') && !drawn().includes('Ada'));
  assert.deepEqual(related(), []);
  assert.match(panel().textContent, /Nothing on the map is linked to this record/);
  assert.equal(panel().querySelector('h3').textContent, 'Linked records (0)');
  // Alone on the map, it is still counted properly.
  for (const key of ['control', 'clause', 'risk', 'document']) kind(key).click();
  assert.deepEqual(drawn(), ['Zed']);
  assert.equal(ui.el('mapMeta').textContent, '1 record and 0 links shown');
});

test('evidence sources are loaded on request and starting again clears the map', async (t) => {
  let fail = false;
  const { ui, requests, group, drawn, fire, kind, panel } = await map(t, {
    data: (url) =>
      fail
        ? null
        : url.searchParams.get('evidence') === 'true'
          ? overview({
              nodes: [...overview().nodes, JENKINS],
              links: [...overview().links, link(JENKINS.id, A1.id, 'evidence', 250)],
              truncated: true,
            })
          : overview(),
    neighbours: () => ({ ...overview(), nodes: [A2], links: [link(A2.id, C41.id, 'applies')] }),
  });
  // A record that only an expansion brought is forgotten when the map reloads.
  fire(group(C41.id), 'dblclick');
  await settle(() => drawn().includes('A.2'));
  fire(group(A2.id), 'click');
  assert.equal(panel().querySelector('h2').textContent, 'A.2');
  ui.el('mapEvidence').checked = true;
  ui.el('mapEvidence').dispatchEvent(new ui.window.Event('change'));
  await settle(() => drawn().includes('Jenkins'));
  assert.equal(requests.at(-1), '/api/v1/map?framework=A&evidence=true');
  assert.ok(!drawn().includes('A.2'));
  assert.match(panel().textContent, /Choose a record/);
  assert.equal(
    ui.el('mapMeta').textContent,
    '6 records and 6 links shown. The map shows part of what there is: the best connected records, within the most Keen reads at once.',
  );
  // A link is drawn thicker the more events it stands for.
  const width = (el) => Number(el.getAttribute('stroke-width'));
  const lines = [...ui.document.querySelectorAll('line.map-link')];
  assert.ok(width(ui.document.querySelector('line.map-link-evidence')) > width(lines[0]));
  assert.equal(kind('source').textContent, 'Evidence sources 1');

  // Starting again shows every kind, forgets the chosen record and reloads.
  kind('risk').click();
  fire(group(A1.id), 'click');
  ui.el('mapReset').click();
  await settle(() => drawn().includes('Outage'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('focus'), null);
  assert.equal(kind('risk').getAttribute('aria-pressed'), 'true');
  assert.match(panel().textContent, /Choose a record/);

  // A map that cannot be loaded says so and keeps what was drawn.
  fail = true;
  ui.el('mapReset').click();
  await settle(() => ui.el('status').dataset.alert === 'danger');
  assert.equal(ui.el('status').textContent, 'Could not load the map: The map is unavailable');
  assert.equal(ui.el('mapMeta').textContent, 'The map could not be loaded.');
});

test('a link to the map starts from the record it names', async (t) => {
  const onMap = await map(t, { query: `&focus=${encodeURIComponent(OUTAGE.id)}` });
  await settle(() => onMap.panel().querySelector('h2'));
  assert.equal(onMap.panel().querySelector('h2').textContent, 'Outage');
  assert.deepEqual(onMap.requests, ['/api/v1/map?framework=A']);
  assert.deepEqual(onMap.related(), [
    ['Ada', 'Affects · People'],
    ['A.1', 'Mitigated by · Controls'],
  ]);

  // A record the overview leaves out is fetched with its related records.
  const fetched = await map(t, {
    query: `&focus=${encodeURIComponent(A2.id)}`,
    neighbours: () => ({ ...overview(), nodes: [A2], links: [link(C41.id, A2.id, 'applies')] }),
  });
  await settle(() => fetched.panel().querySelector('h2'));
  assert.equal(fetched.panel().querySelector('h2').textContent, 'A.2');
  assert.deepEqual(fetched.related(), [['4.1', 'Applies to clause · Clauses']]);

  // One that is not available leaves the overview, with the reason.
  const missing = await map(t, { query: '&focus=risk:gone' });
  await settle(() => missing.ui.el('status').dataset.alert === 'danger');
  assert.match(missing.panel().textContent, /Choose a record/);
});

test('an empty map, a large one and a page without D3 each say or draw what they can', async (t) => {
  const empty = await map(t, { data: overview({ nodes: [], links: [] }) });
  assert.equal(empty.ui.el('mapCanvas').textContent, 'There are no records to show yet.');
  assert.equal(empty.ui.el('mapKinds').children.length, 0);

  const single = await map(t, {
    data: overview({ nodes: [A1, C41], links: [link(C41.id, A1.id, 'applies')] }),
  });
  assert.equal(single.ui.el('mapMeta').textContent, '2 records and 1 link shown');

  // A large map is laid out in fewer steps and still places every record.
  const many = Array.from({ length: 801 }, (_, n) => record('person', `m${n}`, `Person ${n}`));
  const large = await map(t, {
    data: overview({
      nodes: [A1, ...many],
      links: many.map((person) => link(A1.id, person.id, 'related')),
    }),
  });
  assert.equal(large.ui.el('mapMeta').textContent, '802 records and 801 links shown');
  assert.ok(large.group(A1.id).classList.contains('map-hub'));
  assert.ok(!large.group(many[0].id).classList.contains('map-hub'));
  assert.match(
    large.group(many[800].id).getAttribute('transform'),
    /^translate\(-?[\d.]+,-?[\d.]+\)$/,
  );

  const bare = await map(t, { scripts: [] });
  assert.equal(bare.ui.el('mapCanvas').textContent, 'D3 is not available.');
  assert.deepEqual(bare.requests, []);
});

test('few names are on show until the map is zoomed in, and long ones are cut', async (t) => {
  // Twelve well linked controls and one risk with a long name.
  const long = 'Customer servers are hosted in cloud accounts the operator alone can reach';
  const risk = record('risk', 'long', long);
  const controls = Array.from({ length: 12 }, (_, n) => record('control', `h${n}`, `H.${n}`));
  const people = Array.from({ length: 4 }, (_, n) => record('person', `q${n}`, `Person ${n}`));
  const { ui, group, fire } = await map(t, {
    data: overview({
      nodes: [risk, ...controls, ...people],
      links: [
        ...controls.flatMap((control) =>
          people.map((person) => link(control.id, person.id, 'related')),
        ),
        link(risk.id, controls[0].id, 'mitigates'),
      ],
    }),
  });
  // The name on the map is short; the tooltip has all of it.
  const text = group(risk.id).querySelector('text').textContent;
  assert.equal(text, 'Customer servers are hosted i…');
  assert.equal(text.length, 30);
  assert.equal(group(risk.id).querySelector('title').textContent, `Risks · ${long}`);
  // Only the ten best linked records keep their names on show: the people
  // (twelve links each) and the best linked controls, never the risk.
  const hubs = [...ui.document.querySelectorAll('g.map-node.map-hub')].map((g) => g.dataset.id);
  assert.equal(hubs.length, 10);
  for (const person of people) assert.ok(hubs.includes(person.id));
  assert.ok(hubs.includes(controls[0].id), 'the control with one more link');
  assert.ok(!hubs.includes(risk.id));

  // Zooming well in shows every name; zooming back out hides them again.
  const svg = ui.document.querySelector('#mapCanvas svg');
  assert.ok(!svg.classList.contains('map-labels'));
  const wheel = (deltaY) =>
    svg.dispatchEvent(
      new ui.window.WheelEvent('wheel', {
        deltaY,
        bubbles: true,
        cancelable: true,
        view: ui.window,
      }),
    );
  wheel(-2000);
  assert.ok(svg.classList.contains('map-labels'));
  wheel(2000);
  assert.ok(!svg.classList.contains('map-labels'));
  fire(group(risk.id), 'click');
  assert.ok(group(risk.id).classList.contains('map-selected'));
});

test('each kind gathers in its own part of the map around the controls', async (t) => {
  const risks = Array.from({ length: 15 }, (_, n) => record('risk', `g${n}`, `Risk ${n}`));
  const documents = Array.from({ length: 15 }, (_, n) => record('document', `g${n}`, `Doc ${n}`));
  const controls = Array.from({ length: 15 }, (_, n) => record('control', `g${n}`, `G.${n}`));
  const { ui, group } = await map(t, {
    data: overview({ nodes: [...risks, ...documents, ...controls], links: [] }),
  });
  ui.el('mapUnlinked').checked = true;
  ui.el('mapUnlinked').dispatchEvent(new ui.window.Event('change'));
  const centre = (records) => {
    const points = records.map((r) =>
      group(r.id)
        .getAttribute('transform')
        .match(/-?[\d.]+/g)
        .map(Number),
    );
    return [0, 1].map((axis) => points.reduce((sum, p) => sum + p[axis], 0) / points.length);
  };
  const apart = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const [r, d, c] = [centre(risks), centre(documents), centre(controls)];
  // The two kinds sit on opposite sides, with the controls between them.
  assert.ok(apart(r, d) > 250, `risks and documents are ${apart(r, d)} apart`);
  assert.ok(apart(r, c) < apart(r, d) && apart(d, c) < apart(r, d));
});

test('the map is drawn in the proportions of its canvas', async (t) => {
  const { ui } = await map(t);
  const svg = ui.document.querySelector('#mapCanvas svg');
  // Without a measured size the map keeps its own.
  assert.equal(svg.getAttribute('viewBox'), '0 0 1200 720');
  Object.defineProperty(ui.el('mapCanvas'), 'clientWidth', { value: 1600 });
  ui.el('mapFit').click();
  assert.equal(svg.getAttribute('viewBox'), '0 0 1200 720');
  Object.defineProperty(svg, 'clientHeight', { value: 500 });
  ui.el('mapFit').click();
  assert.equal(svg.getAttribute('viewBox'), '0 0 1600 500');
  // The view is centred on the canvas as measured.
  assert.match(
    ui.document.querySelector('g.map-scene').getAttribute('transform'),
    /^translate\(-?[\d.]+,-?[\d.]+\) scale\([\d.]+\)$/,
  );
});
