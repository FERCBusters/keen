import { esc, withFramework } from '/app.js';

export const statuses = [
  'defined',
  'planned',
  'in_progress',
  'under_review',
  'completed',
  'deferred',
  'superseded',
];
export const kinds = ['strength', 'weakness', 'opportunity', 'threat'];
export const label = (value) =>
  String(value || '')
    .replaceAll('_', ' ')
    .replace(/^./, (c) => c.toUpperCase());
export function normalise(row, objective = false) {
  return {
    ...row,
    title: row.title || row.goal || row.requirement,
    status: row.status === 'not_started' ? 'defined' : row.status,
    target_date: row.target_date || row.completion_target_date,
    objective,
  };
}
export function href(row, framework) {
  return withFramework(
    `/${row.objective ? 'isms-objective' : 'swot'}.html?id=${encodeURIComponent(row.id)}`,
    framework,
  );
}
function card(row, framework, editable) {
  return `<article class="card planning-card mb-2" ${editable ? `draggable="true" data-drag-id="${esc(row.id)}"` : ''}>
    <div class="card-body p-3"><div class="small-muted">${esc(label(row.kind || 'objective'))}${row.significance ? ` · ${row.significance}/5` : ''}</div>
    <a class="fw-bold" href="${esc(href(row, framework))}">${esc(row.title)}</a>
    <div class="small-muted mt-2">${esc(row.target_date || 'No target date')}</div>
    ${editable ? `<label class="small mt-2">Status<select class="form-select form-select-sm" data-status-id="${esc(row.id)}">${statuses.map((s) => `<option value="${s}" ${s === row.status ? 'selected' : ''}>${label(s)}</option>`).join('')}</select></label>` : `<div class="small">${esc(label(row.status))}</div>`}
    </div></article>`;
}
function quadrantCards(items, framework, editable) {
  const ids = new Set(items.map((row) => row.id));
  const children = new Map();
  const seen = new Set();
  for (const row of items) {
    const parent = ids.has(row.parent_id) ? row.parent_id : '';
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(row);
  }
  function branch(row, depth = 0) {
    if (seen.has(row.id)) return '';
    seen.add(row.id);
    const descendants =
      depth < 12
        ? (children.get(row.id) || []).map((child) => branch(child, depth + 1)).join('')
        : '';
    const parentHint =
      row.parent_id && !ids.has(row.parent_id)
        ? `<p class="small-muted mb-1"><a href="${esc(withFramework(`/swot.html?id=${encodeURIComponent(row.parent_id)}`, framework))}">View parent outside these results</a></p>`
        : '';
    return `<li>${parentHint}${card(row, framework, editable)}${descendants ? `<ul aria-label="Child SWOT items">${descendants}</ul>` : ''}</li>`;
  }
  let content = (children.get('') || []).map((row) => branch(row)).join('');
  // Keep all supplied items visible even if malformed legacy data contains a cycle.
  for (const row of items) if (!seen.has(row.id)) content += branch(row);
  return content ? `<ul class="planning-quadrant-tree">${content}</ul>` : '';
}
export function renderBoard(host, rows, { view, framework, onMove, canCreate = false } = {}) {
  const groups = view === 'quadrant' ? kinds : statuses;
  host.className = view === 'quadrant' ? 'planning-quadrants' : 'planning-kanban';
  host.innerHTML = groups
    .map((group) => {
      const items = rows.filter((row) => (view === 'quadrant' ? row.kind : row.status) === group);
      return `<section class="planning-column quadrant-${group}" data-column="${group}"><div class="d-flex justify-content-between align-items-start gap-2 mb-2"><h2 class="h5">${label(group)} <span class="badge text-bg-light">${items.length}</span></h2>${view === 'quadrant' && canCreate ? `<a class="btn btn-sm btn-outline-primary" aria-label="New ${label(group).toLowerCase()} item" href="${esc(withFramework(`/swot.html?new=1&kind=${group}`, framework))}">New item</a>` : ''}</div>${(view === 'quadrant' ? quadrantCards(items, framework, !!onMove) : items.map((row) => card(row, framework, !!onMove)).join('')) || '<p class="small-muted">No items in this view.</p>'}</section>`;
    })
    .join('');
  if (!onMove) return;
  host
    .querySelectorAll('[data-status-id]')
    .forEach((select) =>
      select.addEventListener('change', () => onMove(select.dataset.statusId, select.value)),
    );
  if (view !== 'kanban') return;
  let draggedId = null;
  const clearDrag = () => {
    draggedId = null;
    host
      .querySelectorAll('.planning-dragging, .planning-drop-target')
      .forEach((el) => el.classList.remove('planning-dragging', 'planning-drop-target'));
  };
  host.querySelectorAll('[data-drag-id]').forEach((el) => {
    el.addEventListener('dragstart', (event) => {
      if (event.target.closest('select, input, textarea, button')) {
        event.preventDefault();
        return;
      }
      draggedId = el.dataset.dragId;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', draggedId);
      el.classList.add('planning-dragging');
    });
    el.addEventListener('dragend', clearDrag);
  });
  host.querySelectorAll('[data-column]').forEach((el) => {
    el.addEventListener('dragover', (event) => {
      if (!draggedId) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      el.classList.add('planning-drop-target');
    });
    el.addEventListener('dragleave', (event) => {
      if (!el.contains(event.relatedTarget)) el.classList.remove('planning-drop-target');
    });
    el.addEventListener('drop', (event) => {
      event.preventDefault();
      const item = rows.find((row) => row.id === draggedId);
      clearDrag();
      if (item && item.status !== el.dataset.column) onMove(item.id, el.dataset.column);
    });
  });
}
export function renderTimeline(host, rows, framework) {
  host.className = 'planning-timeline';
  host.replaceChildren();
  const d3 = window.d3;
  if (!d3) {
    host.textContent = 'Timeline unavailable. Use the board to view dates.';
    return;
  }
  const dated = rows.filter((r) => /^\d{4}-\d{2}-\d{2}$/.test(r.target_date || ''));
  const note = document.createElement('p');
  note.className = 'small-muted';
  note.textContent = `${dated.length} items with target dates are plotted; ${rows.length - dated.length} without a target date are not plotted. Bars show planned start to target completion; diamonds show target completion only.`;
  host.append(note);
  if (!dated.length) return;
  const parse = d3.utcParse('%Y-%m-%d');
  const dates = dated
    .flatMap((r) => [parse(r.start_date || r.target_date), parse(r.target_date)])
    .filter(Boolean);
  const domain = d3.extent(dates);
  domain[0] = d3.utcDay.offset(domain[0], -2);
  domain[1] = d3.utcDay.offset(domain[1], 2);
  const width = 1100,
    height = 65 + dated.length * 38;
  const x = d3
    .scaleUtc()
    .domain(domain)
    .range([340, width - 65]);
  const svg = d3
    .select(host)
    .append('svg')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('role', 'img')
    .attr('aria-label', 'Planning timeline');
  // D3's tick count is advisory. Enforce enough pixel space for full date labels.
  const ticks = [];
  for (const tick of x.ticks(12)) {
    if (!ticks.length || x(tick) - x(ticks[ticks.length - 1]) >= 110) ticks.push(tick);
  }
  svg
    .append('g')
    .attr('class', 'planning-date-axis')
    .attr('transform', 'translate(0,30)')
    .call(d3.axisTop(x).tickValues(ticks).tickFormat(d3.utcFormat('%d %b %Y')));
  dated.forEach((row, i) => {
    const y = 52 + i * 38,
      end = x(parse(row.target_date)),
      start = x(parse(row.start_date || row.target_date));
    svg
      .append('a')
      .attr('href', href(row, framework))
      .append('text')
      .attr('x', 5)
      .attr('y', y + 5)
      .text(row.title.slice(0, 42))
      .append('title')
      .text(row.title);
    if (row.start_date)
      svg
        .append('rect')
        .attr('x', start)
        .attr('y', y - 9)
        .attr('width', Math.max(3, end - start))
        .attr('height', 18)
        .attr('rx', 4)
        .attr('fill', row.status === 'completed' ? '#198754' : '#6f42c1')
        .append('title')
        .text(`${row.start_date} → ${row.target_date}`);
    else
      svg
        .append('path')
        .attr('d', d3.symbol().type(d3.symbolDiamond).size(90)())
        .attr('transform', `translate(${end},${y})`)
        .attr('fill', '#6f42c1');
  });
}
export function renderHierarchy(host, rows, framework) {
  host.className = '';
  host.replaceChildren();
  const byParent = new Map();
  const ids = new Set(rows.map((r) => r.id));
  for (const row of rows) {
    const key = ids.has(row.parent_id) ? row.parent_id : '';
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(row);
  }
  function branch(parent, depth, seen) {
    const ul = document.createElement('ul');
    ul.className = 'planning-tree';
    for (const row of byParent.get(parent) || []) {
      if (seen.has(row.id) || depth > 12) continue;
      const li = document.createElement('li'),
        a = document.createElement('a');
      a.href = href(row, framework);
      a.textContent = `${row.title} · ${label(row.kind)} · ${row.significance}/5`;
      li.append(a);
      if (byParent.has(row.id)) li.append(branch(row.id, depth + 1, new Set([...seen, row.id])));
      ul.append(li);
    }
    return ul;
  }
  host.append(branch('', 0, new Set()));
}
export function renderGraph(host, row, framework) {
  host.replaceChildren();
  const d3 = window.d3;
  if (!d3) {
    host.textContent =
      'Visualisation unavailable. Relationships are listed in the Related items tab.';
    return;
  }
  const nodes = [
    ...(row.links || []).map((l) => ({
      title: l.title,
      type: l.target_type,
      url: withFramework(l.url, framework),
    })),
    ...(row.controls || []).map((c) => ({
      title: `${c.ref} ${c.title || ''}`,
      type: 'control',
      url: withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework),
    })),
    ...(row.clauses || []).map((c) => ({
      title: `${c.ref} ${c.title || ''}`,
      type: 'clause',
      url: withFramework(`/clause.html?id=${encodeURIComponent(c.id)}`, framework),
    })),
  ];
  const note = document.createElement('p');
  note.textContent = 'Direct relationships visible to you. Select a node to open its record.';
  host.append(note);
  const wrap = (value, columns) => {
    const lines = [];
    let line = '';
    for (const word of String(value || '')
      .trim()
      .split(/\s+/)) {
      let remaining = word;
      if (line && line.length + remaining.length + 1 > columns) {
        lines.push(line);
        line = '';
      }
      while (remaining.length > columns) {
        lines.push(remaining.slice(0, columns));
        remaining = remaining.slice(columns);
      }
      line = line ? line + ' ' + remaining : remaining;
    }
    if (line) lines.push(line);
    return lines.length ? lines : [''];
  };
  const rootLines = wrap(row.title, 38);
  let nextY = 30;
  for (const node of nodes) {
    node.lines = wrap(`${label(node.type)}: ${node.title}`, 52);
    node.y = nextY;
    nextY += Math.max(42, node.lines.length * 19 + 20);
  }
  const height = Math.max(380, nextY + 20, rootLines.length * 38 + 180),
    width = 950;
  const svg = d3
    .select(host)
    .append('svg')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('role', 'img')
    .attr('aria-label', 'SWOT relationships');
  const textLines = (parent, lines, x, y) => {
    const text = parent.append('text').attr('x', x).attr('y', y);
    lines.forEach((line, i) =>
      text
        .append('tspan')
        .attr('x', x)
        .attr('dy', i ? 19 : 0)
        .text(line),
    );
  };
  svg
    .append('circle')
    .attr('cx', 170)
    .attr('cy', height / 2)
    .attr('r', 22 + row.significance * 4)
    .attr('fill', '#6f42c1');
  textLines(svg, rootLines, 15, height / 2 + 65);
  nodes.forEach((node) => {
    svg
      .append('path')
      .attr('d', `M210,${height / 2} C370,${height / 2} 380,${node.y} 490,${node.y}`)
      .attr('fill', 'none')
      .attr('stroke', '#888')
      .attr('opacity', 0.55);
    const a = svg
      .append('a')
      .attr('href', node.url)
      .attr('aria-label', `${label(node.type)}: ${node.title}`);
    a.append('circle').attr('cx', 490).attr('cy', node.y).attr('r', 6).attr('fill', '#198754');
    textLines(a, node.lines, 505, node.y + 5);
  });
}
