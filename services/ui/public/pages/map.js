// The Map: the records a person can open and how they relate, drawn as one
// graph. The page starts from an overview without events, and a record's
// related records are added when it is asked for, so a thread can be followed
// from any record to the controls, clauses and evidence sources around it.
import {
  apiGet,
  errorText,
  getCurrentFramework,
  initNavbar,
  node,
  toast,
  withFramework,
} from '/app.js';

const $ = (id) => document.getElementById(id);
await initNavbar();
const framework = getCurrentFramework();

const WIDTH = 1200;
const HEIGHT = 720;
const MATCH_LIMIT = 12;
// Names crowd a map, so few are on show: the chosen record's and its
// neighbours', the one pointed at, and those of the best linked records (at
// most HUB_LABELS of them, each with at least HUB_LINKS links). Zooming in past
// LABEL_ZOOM shows every name. On the map a name is cut to LABEL_LENGTH; the
// panel and the tooltip give it in full.
const HUB_LABELS = 10;
const HUB_LINKS = 3;
const LABEL_ZOOM = 2.5;
const LABEL_LENGTH = 30;
// How far the view zooms in on a record chosen from a list.
const FOCUS_ZOOM = 1.4;
// The kinds at the middle of the map; the others gather around them.
const CATALOGUE = ['control', 'clause'];
const COLOURS = {
  control: '#2563eb',
  clause: '#0f766e',
  risk: '#dc2626',
  pestle_item: '#d97706',
  document: '#7c3aed',
  objective: '#059669',
  asset: '#0891b2',
  person: '#db2777',
  vendor: '#9333ea',
  swot: '#65a30d',
  legal_item: '#b45309',
  effectiveness_measure: '#0284c7',
  meeting: '#4f46e5',
  org_node: '#64748b',
  story: '#e11d48',
  audit: '#ca8a04',
  source: '#475569',
};
// How a link reads from each end: [when the chosen record keeps the link,
// when the other record keeps it], each describing the other record.
const RELATIONS = {
  applies: ['Applicable control', 'Applies to clause'],
  mitigates: ['Mitigated by', 'Mitigates'],
  relevant: ['Relevant to', 'Relevant factor'],
  mapped: ['Mapped to', 'Mapped record'],
  affects: ['Affects', 'Affected by'],
  asset: ['Asset at risk', 'Risk to this asset'],
  related: ['Related', 'Related'],
  part: ['Part of', 'Includes'],
  supplied: ['Supplied by', 'Supplies'],
  owned: ['Owned by', 'Owns'],
  uses: ['Uses', 'Used by'],
  member: ['Member of', 'Member'],
  refers: ['Refers to', 'Referred to by'],
  attends: ['Attended by', 'Attends'],
  story: ['Includes', 'In story'],
  scope: ['In scope', 'In the scope of'],
  finding: ['Finding on', 'Audit finding'],
  evidence: ['Supplies evidence to', 'Evidence from'],
};

const state = {
  nodes: new Map(),
  links: new Map(),
  kinds: [],
  hidden: new Set(),
  expanded: new Set(),
  selected: null,
  truncated: false,
};
let svg = null;
let scene = null;
let zoom = null;
let shown = { nodes: [], links: [] };
// The drawing area in the map's own units: the canvas's size once it is known.
const view = { width: WIDTH, height: HEIGHT };

const linkKey = (link) => `${link.source}|${link.target}|${link.relation}`;
const kindLabel = (key) => state.kinds.find((kind) => kind.key === key).label;

// --- Data ---------------------------------------------------------------------

function merge(data, near = null) {
  state.kinds = data.kinds;
  state.truncated = state.truncated || data.truncated;
  for (const record of data.nodes) {
    const known = state.nodes.get(record.id);
    if (known) Object.assign(known, record);
    else
      state.nodes.set(record.id, {
        ...record,
        // A record added beside another starts next to it.
        x: near ? near.x + (Math.random() - 0.5) * 60 : undefined,
        y: near ? near.y + (Math.random() - 0.5) * 60 : undefined,
      });
  }
  // A link is kept once both of its records are on the map.
  for (const link of data.links)
    if (state.nodes.has(link.source) && state.nodes.has(link.target))
      state.links.set(linkKey(link), link);
  if (data.notes.length) toast($('status'), data.notes.join(' '), 'warning');
}

function linksOf(id) {
  return [...state.links.values()].filter((link) => link.source === id || link.target === id);
}

// The records and links on show: a kind can be hidden, and a record with no
// link to another shown record waits until it is asked for.
function visible() {
  const kept = [...state.nodes.values()].filter(
    (record) => !state.hidden.has(record.kind) || record.id === state.selected,
  );
  const ids = new Set(kept.map((record) => record.id));
  const links = [...state.links.values()].filter(
    (link) => ids.has(link.source) && ids.has(link.target),
  );
  const degree = new Map();
  for (const link of links)
    for (const end of [link.source, link.target]) degree.set(end, (degree.get(end) || 0) + 1);
  const nodes = kept.filter(
    (record) => $('mapUnlinked').checked || degree.has(record.id) || record.id === state.selected,
  );
  for (const record of nodes) record.degree = degree.get(record.id) || 0;
  return { nodes, links };
}

// --- Drawing ------------------------------------------------------------------

const radius = (record) => Math.min(18, 5 + Math.sqrt(record.degree) * 2);
const shortLabel = (label) =>
  label.length > LABEL_LENGTH ? `${label.slice(0, LABEL_LENGTH - 1)}…` : label;

// Where each kind of record gathers: controls and clauses in the middle and
// every other kind in its own direction around them, so that risks, documents
// and the rest form loose groups instead of one mixed ball.
function kindAnchors(nodes, spread) {
  const around = [...new Set(nodes.map((record) => record.kind))].filter(
    (kind) => !CATALOGUE.includes(kind),
  );
  const anchors = new Map(CATALOGUE.map((kind) => [kind, { x: WIDTH / 2, y: HEIGHT / 2 }]));
  around.forEach((kind, index) => {
    const angle = (index / around.length) * 2 * Math.PI - Math.PI / 2;
    anchors.set(kind, {
      x: WIDTH / 2 + Math.cos(angle) * 260 * spread,
      y: HEIGHT / 2 + Math.sin(angle) * 260 * spread,
    });
  });
  return anchors;
}

// Places the records. The layout is computed in one go rather than animated,
// so the map is still while it is read; `settled` keeps the records already
// placed close to where they were.
function layout(nodes, links, settled) {
  // A map of more records is given more room, so it stays as open as a small one.
  const spread = Math.min(3, Math.max(1, Math.sqrt(nodes.length / 60)));
  const anchors = kindAnchors(nodes, spread);
  const simulation = window.d3
    .forceSimulation(nodes)
    .force(
      'link',
      window.d3
        .forceLink(
          links.map((link) => ({
            source: state.nodes.get(link.source),
            target: state.nodes.get(link.target),
          })),
        )
        .distance(60 + 30 * spread)
        .strength(0.3),
    )
    .force(
      'charge',
      window.d3
        .forceManyBody()
        .strength(-260 * spread)
        .distanceMax(700 * spread),
    )
    .force('x', window.d3.forceX((record) => anchors.get(record.kind).x).strength(0.12))
    .force('y', window.d3.forceY((record) => anchors.get(record.kind).y).strength(0.12))
    .force(
      'collide',
      window.d3.forceCollide((record) => radius(record) + 8),
    )
    .alpha(settled ? 0.25 : 1)
    .stop();
  const steps = settled ? 60 : nodes.length > 800 ? 120 : 300;
  for (let step = 0; step < steps; step += 1) simulation.tick();
}

function place() {
  scene
    .selectAll('line.map-link')
    .attr('x1', (link) => state.nodes.get(link.source).x)
    .attr('y1', (link) => state.nodes.get(link.source).y)
    .attr('x2', (link) => state.nodes.get(link.target).x)
    .attr('y2', (link) => state.nodes.get(link.target).y);
  scene.selectAll('g.map-node').attr('transform', (record) => `translate(${record.x},${record.y})`);
}

// Dims everything but one record and the records it is linked to.
function spotlight(id) {
  const near = new Set([id]);
  for (const link of shown.links)
    if (link.source === id || link.target === id) {
      near.add(link.source);
      near.add(link.target);
    }
  svg.classed('map-dim', Boolean(id));
  scene.selectAll('g.map-node').classed('map-near', (record) => near.has(record.id));
  scene
    .selectAll('line.map-link')
    .classed('map-near', (link) => link.source === id || link.target === id);
}

// Draws in the canvas's own proportions, so a wide canvas is used in full.
function measure() {
  const width = $('mapCanvas').clientWidth;
  const height = svg.node().clientHeight;
  if (!width || !height) return;
  view.width = width;
  view.height = height;
  svg.attr('viewBox', `0 0 ${width} ${height}`);
  zoom.extent([
    [0, 0],
    [width, height],
  ]);
}

function ensureSvg() {
  if (svg) return;
  const host = $('mapCanvas');
  host.replaceChildren();
  svg = window.d3
    .select(host)
    .append('svg')
    .attr('viewBox', `0 0 ${view.width} ${view.height}`)
    .attr('preserveAspectRatio', 'xMidYMid meet')
    .attr('role', 'img')
    .attr('aria-label', 'Records and the links between them');
  scene = svg.append('g').attr('class', 'map-scene');
  scene.append('g').attr('class', 'map-links');
  scene.append('g').attr('class', 'map-nodes');
  zoom = window.d3
    .zoom()
    .extent([
      [0, 0],
      [view.width, view.height],
    ])
    .scaleExtent([0.1, 6])
    .on('zoom', (event) => {
      scene.attr('transform', event.transform);
      svg.classed('map-labels', event.transform.k >= LABEL_ZOOM);
    });
  svg.call(zoom).on('dblclick.zoom', null);
  measure();
}

function draw({ settled = true } = {}) {
  shown = visible();
  renderKinds();
  const records = shown.nodes.length;
  $('mapMeta').textContent =
    `${records} record${records === 1 ? '' : 's'} and ${shown.links.length} link${shown.links.length === 1 ? '' : 's'} shown` +
    (state.truncated
      ? '. The map shows part of what there is: the best connected records, within the most Keen reads at once.'
      : '');
  if (!records) {
    svg = null;
    $('mapCanvas').replaceChildren(
      node(
        'p',
        { class: 'small-muted p-4 mb-0' },
        state.nodes.size
          ? 'No record is shown. Show a kind of record, or the records with no links.'
          : 'There are no records to show yet.',
      ),
    );
    return;
  }
  ensureSvg();
  layout(shown.nodes, shown.links, settled);
  const hubs = new Set(
    shown.nodes
      .filter((record) => record.degree >= HUB_LINKS)
      .sort((a, b) => b.degree - a.degree || a.id.localeCompare(b.id))
      .slice(0, HUB_LABELS)
      .map((record) => record.id),
  );
  scene
    .select('g.map-links')
    .selectAll('line')
    .data(shown.links, linkKey)
    .join((enter) => {
      const line = enter.append('line').attr('class', 'map-link');
      line.append('title');
      return line;
    })
    .classed('map-link-evidence', (link) => link.relation === 'evidence')
    .attr('stroke-width', (link) => 1 + Math.min(5, Math.log10(link.value)))
    .select('title')
    .text((link) => {
      const [from, to] = [state.nodes.get(link.source), state.nodes.get(link.target)];
      return `${from.label} · ${RELATIONS[link.relation]?.[0] || link.relation} · ${to.label}`;
    });
  const groups = scene
    .select('g.map-nodes')
    .selectAll('g.map-node')
    .data(shown.nodes, (record) => record.id)
    .join((enter) => {
      const group = enter.append('g').attr('class', 'map-node');
      group.append('circle');
      group.append('text').attr('class', 'map-label').attr('dy', '0.32em');
      group.append('title');
      group
        .on('click', (event, record) => select(record.id))
        .on('dblclick', (event, record) => expand(record.id))
        .on('mouseenter', (event, record) => spotlight(record.id))
        .on('mouseleave', () => spotlight(state.selected))
        .call(
          window.d3.drag().on('drag', (event, record) => {
            record.x = event.x;
            record.y = event.y;
            place();
          }),
        );
      return group;
    })
    .attr('data-id', (record) => record.id)
    .classed('map-hub', (record) => hubs.has(record.id))
    .classed('map-selected', (record) => record.id === state.selected);
  groups
    .select('circle')
    .attr('r', radius)
    .attr('fill', (record) => COLOURS[record.kind]);
  groups
    .select('text')
    .attr('x', (record) => radius(record) + 4)
    .text((record) => shortLabel(record.label));
  groups
    .select('title')
    .text((record) =>
      [kindLabel(record.kind), record.label, record.detail].filter(Boolean).join(' · '),
    );
  place();
  spotlight(state.selected);
}

// Moves the view so that every shown record is in it.
function fit() {
  if (!svg) return;
  measure();
  const xs = shown.nodes.map((record) => record.x);
  const ys = shown.nodes.map((record) => record.y);
  const [left, right] = [Math.min(...xs) - 60, Math.max(...xs) + 160];
  const [top, bottom] = [Math.min(...ys) - 40, Math.max(...ys) + 40];
  const scale = Math.min(2, view.width / (right - left), view.height / (bottom - top));
  svg.call(
    zoom.transform,
    window.d3.zoomIdentity
      .translate(view.width / 2, view.height / 2)
      .scale(scale)
      .translate(-(left + right) / 2, -(top + bottom) / 2),
  );
}

function centreOn(record) {
  svg.call(
    zoom.transform,
    window.d3.zoomIdentity
      .translate(view.width / 2, view.height / 2)
      .scale(FOCUS_ZOOM)
      .translate(-record.x, -record.y),
  );
}

// --- Kinds --------------------------------------------------------------------

function renderKinds() {
  const counts = new Map();
  for (const record of state.nodes.values())
    counts.set(record.kind, (counts.get(record.kind) || 0) + 1);
  $('mapKinds').replaceChildren(
    ...state.kinds
      .filter((kind) => counts.has(kind.key))
      .map((kind) => {
        const on = !state.hidden.has(kind.key);
        const button = node(
          'button',
          {
            type: 'button',
            class: `btn btn-sm map-kind ${on ? 'btn-outline-secondary' : 'btn-light text-muted'}`,
            'aria-pressed': String(on),
            'data-kind': kind.key,
          },
          node('span', {
            class: 'map-dot',
            style: `background:${on ? COLOURS[kind.key] : 'transparent'};border-color:${COLOURS[kind.key]}`,
          }),
          `${kind.label} `,
          node('span', { class: 'small-muted' }, String(counts.get(kind.key))),
        );
        button.addEventListener('click', () => {
          if (on) state.hidden.add(kind.key);
          else state.hidden.delete(kind.key);
          draw();
        });
        return button;
      }),
  );
}

// --- The chosen record --------------------------------------------------------

function eventsLink(link) {
  const control = state.nodes.get(link.target);
  const source = link.source.slice('source:'.length);
  return withFramework(
    `/events.html?source=${encodeURIComponent(source)}&control=${encodeURIComponent(control.label)}`,
    framework,
  );
}

function renderPanel() {
  const record = state.nodes.get(state.selected);
  if (!record) {
    $('mapPanel').replaceChildren(
      node(
        'p',
        { class: 'small-muted mb-0' },
        'Choose a record to see what it is and what it is linked to.',
      ),
    );
    return;
  }
  const related = linksOf(record.id)
    .map((link) => {
      const kept = link.source === record.id;
      const other = state.nodes.get(kept ? link.target : link.source);
      return { link, other, phrase: RELATIONS[link.relation]?.[kept ? 0 : 1] || link.relation };
    })
    .sort((a, b) => a.phrase.localeCompare(b.phrase) || a.other.label.localeCompare(b.other.label));
  const expandButton = node(
    'button',
    { type: 'button', class: 'btn btn-sm btn-primary', id: 'mapExpand' },
    state.expanded.has(record.id) ? 'Related records shown' : 'Show related records',
  );
  expandButton.disabled = state.expanded.has(record.id);
  expandButton.addEventListener('click', () => expand(record.id));
  $('mapPanel').replaceChildren(
    node(
      'div',
      { class: 'small-muted d-flex align-items-center gap-2' },
      node('span', { class: 'map-dot', style: `background:${COLOURS[record.kind]}` }),
      kindLabel(record.kind),
    ),
    node('h2', { class: 'h5 mt-1 mb-1 text-break' }, record.label),
    record.detail ? node('p', { class: 'small-muted text-break' }, record.detail) : null,
    node(
      'div',
      { class: 'd-flex flex-wrap gap-2 mb-3' },
      node(
        'a',
        {
          class: 'btn btn-sm btn-outline-primary',
          href: withFramework(record.url, framework),
          id: 'mapOpen',
        },
        'Open record',
      ),
      expandButton,
    ),
    node('h3', { class: 'h6' }, `Linked records (${related.length})`),
    related.length
      ? node(
          'ul',
          { class: 'list-unstyled mb-0 map-related' },
          ...related.map(({ link, other, phrase }) => {
            const open = node(
              'button',
              { type: 'button', class: 'btn btn-link p-0 text-start text-break' },
              other.label,
            );
            open.addEventListener('click', () => select(other.id, { centre: true }));
            return node(
              'li',
              { class: 'mb-2' },
              node('span', {
                class: 'map-dot me-1',
                style: `background:${COLOURS[other.kind]}`,
              }),
              open,
              node(
                'div',
                { class: 'small-muted' },
                `${phrase} · ${kindLabel(other.kind)}`,
                link.relation === 'evidence' ? ` · ${link.value} events · ` : null,
                link.relation === 'evidence'
                  ? node('a', { href: eventsLink(link) }, 'Open events')
                  : null,
              ),
            );
          }),
        )
      : node(
          'p',
          { class: 'small-muted mb-0' },
          'Nothing on the map is linked to this record. Show its related records to look further.',
        ),
  );
}

function select(id, { centre = false } = {}) {
  state.selected = id;
  const url = new URL(String(location.href), document.baseURI);
  url.searchParams.set('focus', id);
  history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  // A record of a hidden kind, or one with no links, is shown once chosen.
  draw();
  renderPanel();
  if (centre) centreOn(state.nodes.get(id));
}

async function expand(id) {
  if (state.expanded.has(id)) return;
  try {
    const data = await apiGet(
      `/api/v1/map/neighbours?framework=${encodeURIComponent(framework)}&node=${encodeURIComponent(id)}`,
    );
    state.expanded.add(id);
    merge(data, state.nodes.get(id) || null);
    select(id);
  } catch (error) {
    toast($('status'), `Could not show the related records: ${errorText(error)}`, 'danger');
  }
}

// --- Search -------------------------------------------------------------------

function renderMatches() {
  const q = $('mapSearch').value.trim().toLowerCase();
  const matches = q
    ? [...state.nodes.values()]
        .filter((record) => `${record.label} ${record.detail}`.toLowerCase().includes(q))
        .sort((a, b) => a.label.localeCompare(b.label))
        .slice(0, MATCH_LIMIT)
    : [];
  $('mapMatches').hidden = !q;
  $('mapMatches').replaceChildren(
    ...(!q
      ? []
      : matches.length
        ? matches.map((record) => {
            const button = node(
              'button',
              {
                type: 'button',
                class: 'list-group-item list-group-item-action',
                'data-id': record.id,
              },
              node('span', {
                class: 'map-dot me-2',
                style: `background:${COLOURS[record.kind]}`,
              }),
              record.label,
              node('span', { class: 'small-muted ms-2' }, kindLabel(record.kind)),
            );
            button.addEventListener('click', () => {
              $('mapSearch').value = '';
              renderMatches();
              select(record.id, { centre: true });
            });
            return button;
          })
        : [
            node(
              'div',
              { class: 'list-group-item small-muted' },
              'No record on the map matches. A control or clause nothing refers to appears once a record related to it is shown.',
            ),
          ]),
  );
}

// --- Page ---------------------------------------------------------------------

async function load() {
  $('mapMeta').textContent = 'Loading';
  try {
    const data = await apiGet(
      `/api/v1/map?framework=${encodeURIComponent(framework)}${$('mapEvidence').checked ? '&evidence=true' : ''}`,
    );
    state.nodes.clear();
    state.links.clear();
    state.expanded.clear();
    state.truncated = false;
    merge(data);
    if (state.selected && !state.nodes.has(state.selected)) state.selected = null;
    draw({ settled: false });
    renderPanel();
    fit();
  } catch (error) {
    $('mapMeta').textContent = 'The map could not be loaded.';
    toast($('status'), `Could not load the map: ${errorText(error)}`, 'danger');
  }
}

$('mapSearch').addEventListener('input', renderMatches);
$('mapEvidence').addEventListener('change', load);
$('mapUnlinked').addEventListener('change', () => draw());
$('mapFit').addEventListener('click', fit);
$('mapReset').addEventListener('click', () => {
  state.hidden.clear();
  state.selected = null;
  const url = new URL(String(location.href), document.baseURI);
  url.searchParams.delete('focus');
  history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  load();
});

if (!window.d3) {
  $('mapCanvas').textContent = 'D3 is not available.';
  $('mapMeta').textContent = '';
} else {
  await load();
  // A link to the map can name the record to start from, as `kind:id`.
  const focus = new URLSearchParams(location.search).get('focus');
  if (focus) {
    if (state.nodes.has(focus)) select(focus, { centre: true });
    else await expand(focus);
  }
}
