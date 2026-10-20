// The story flow chart: boxes for the story's items, arranged by dragging, and
// labelled links drawn between them. The canvas keeps the layout in memory and
// reports every change so the page can offer to save it.
import { esc } from '/app.js';

export const NODE_WIDTH = 200;
export const NODE_HEIGHT = 90;
export const MAX_COORDINATE = 20000;
const SVG_NS = 'http://www.w3.org/2000/svg';

export function clampCoordinate(value) {
  const n = Math.round(Number(value) || 0);
  return Math.min(MAX_COORDINATE, Math.max(0, n));
}

// The straight line between two box centres, trimmed to the box edges so the
// arrow head ends at the border rather than under the box.
export function linkPath(from, to) {
  const ax = from.x + NODE_WIDTH / 2,
    ay = from.y + NODE_HEIGHT / 2,
    bx = to.x + NODE_WIDTH / 2,
    by = to.y + NODE_HEIGHT / 2;
  const dx = bx - ax,
    dy = by - ay;
  const length = Math.hypot(dx, dy) || 1;
  const trim = (x, y, sign) => {
    const scale = Math.min(
      Math.abs(dx) > 0 ? NODE_WIDTH / 2 / Math.abs(dx / length) : Infinity,
      Math.abs(dy) > 0 ? NODE_HEIGHT / 2 / Math.abs(dy / length) : Infinity,
    );
    const reach = Math.min(scale, length / 2);
    return [x + (sign * dx * reach) / length, y + (sign * dy * reach) / length];
  };
  const [x1, y1] = trim(ax, ay, 1);
  const [x2, y2] = trim(bx, by, -1);
  return { x1, y1, x2, y2, mx: (x1 + x2) / 2, my: (y1 + y2) / 2 };
}

// Places items on a grid in their given order, as the server does for new items.
export function gridPositions(
  items,
  { columns = 4, columnWidth = 240, rowHeight = 130, margin = 40 } = {},
) {
  return items.map((item, index) => ({
    item_id: item.id,
    x: margin + (index % columns) * columnWidth,
    y: margin + Math.floor(index / columns) * rowHeight,
  }));
}

export function createFlowCanvas({ host, editable = false, onChange = () => {} }) {
  let items = [];
  let canEdit = Boolean(editable);
  let links = [];
  let selectedLink = null;
  let linkFrom = null;
  let linkMode = false;
  let dragging = null;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'story-flow-links');
  svg.setAttribute('aria-hidden', 'true');
  const defs = document.createElementNS(SVG_NS, 'defs');
  const marker = document.createElementNS(SVG_NS, 'marker');
  for (const [name, value] of Object.entries({
    id: 'storyArrow',
    viewBox: '0 0 10 10',
    refX: '9',
    refY: '5',
    markerWidth: '8',
    markerHeight: '8',
    orient: 'auto-start-reverse',
  }))
    marker.setAttribute(name, value);
  const head = document.createElementNS(SVG_NS, 'path');
  head.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
  marker.append(head);
  defs.append(marker);
  svg.append(defs);
  const layer = document.createElement('div');
  layer.className = 'story-flow-nodes';
  host.classList.add('story-flow');
  host.replaceChildren(svg, layer);

  function byId(id) {
    return items.find((item) => item.id === id);
  }

  function extent() {
    let width = 0,
      height = 0;
    for (const item of items) {
      width = Math.max(width, item.x + NODE_WIDTH + 40);
      height = Math.max(height, item.y + NODE_HEIGHT + 40);
    }
    return { width: Math.max(width, 600), height: Math.max(height, 320) };
  }

  function drawLinks() {
    for (const node of [...svg.children]) if (node !== defs) node.remove();
    const size = extent();
    svg.setAttribute('width', String(size.width));
    svg.setAttribute('height', String(size.height));
    layer.style.width = `${size.width}px`;
    layer.style.height = `${size.height}px`;
    for (const link of links) {
      const from = byId(link.from_item_id),
        to = byId(link.to_item_id);
      if (!from || !to) continue;
      const path = linkPath(from, to);
      const group = document.createElementNS(SVG_NS, 'g');
      group.setAttribute('class', 'story-link' + (link === selectedLink ? ' selected' : ''));
      group.dataset.linkFrom = link.from_item_id;
      group.dataset.linkTo = link.to_item_id;
      const line = document.createElementNS(SVG_NS, 'line');
      line.setAttribute('x1', String(path.x1));
      line.setAttribute('y1', String(path.y1));
      line.setAttribute('x2', String(path.x2));
      line.setAttribute('y2', String(path.y2));
      line.setAttribute('marker-end', 'url(#storyArrow)');
      const hit = document.createElementNS(SVG_NS, 'line');
      hit.setAttribute('class', 'story-link-hit');
      hit.setAttribute('x1', String(path.x1));
      hit.setAttribute('y1', String(path.y1));
      hit.setAttribute('x2', String(path.x2));
      hit.setAttribute('y2', String(path.y2));
      group.append(line, hit);
      if (link.label) {
        const text = document.createElementNS(SVG_NS, 'text');
        text.setAttribute('x', String(path.mx));
        text.setAttribute('y', String(path.my - 6));
        text.setAttribute('text-anchor', 'middle');
        text.textContent = link.label;
        group.append(text);
      }
      svg.append(group);
    }
  }

  function nodeHtml(item) {
    // A withheld item arrives with no label or note, so its title stands alone.
    const title = item.label || item.title || item.kind_label;
    const subtitle = item.label && item.title && item.title !== item.label ? item.title : '';
    const when = item.occurred_at
      ? `<div class="small-muted story-node-time">${esc(item.occurred_at)}</div>`
      : '';
    return `<span class="badge badge-soft story-node-kind">${esc(item.kind_label || item.kind)}</span>
      <div class="story-node-title">${esc(title)}</div>
      ${subtitle ? `<div class="small-muted story-node-subtitle">${esc(subtitle)}</div>` : ''}${when}`;
  }

  function drawNodes() {
    layer.replaceChildren();
    for (const item of items) {
      const node = document.createElement('div');
      node.className = 'story-node' + (item.withheld ? ' story-node-withheld' : '');
      node.dataset.itemId = item.id;
      node.dataset.kind = item.kind;
      node.style.left = `${item.x}px`;
      node.style.top = `${item.y}px`;
      node.setAttribute('role', 'group');
      node.setAttribute('aria-label', `${item.kind_label || item.kind}: ${item.title || ''}`);
      node.innerHTML = nodeHtml(item);
      if (linkFrom === item.id) node.classList.add('story-node-linking');
      layer.append(node);
    }
  }

  function render() {
    drawNodes();
    drawLinks();
  }

  function setLinkMode(on) {
    linkMode = Boolean(on);
    linkFrom = null;
    host.classList.toggle('story-flow-link-mode', linkMode);
    drawNodes();
  }

  function selectLink(link) {
    selectedLink = link || null;
    drawLinks();
    onChange({ type: 'select', link: selectedLink });
  }

  function addLink(fromId, toId) {
    if (fromId === toId) return null;
    const existing = links.find((l) => l.from_item_id === fromId && l.to_item_id === toId);
    if (existing) return existing;
    const link = { from_item_id: fromId, to_item_id: toId, label: '' };
    links.push(link);
    drawLinks();
    onChange({ type: 'links' });
    return link;
  }

  function removeSelectedLink() {
    if (!selectedLink) return;
    links = links.filter((link) => link !== selectedLink);
    selectedLink = null;
    drawLinks();
    onChange({ type: 'links' });
  }

  function labelSelectedLink(label) {
    if (!selectedLink) return;
    selectedLink.label = String(label || '').slice(0, 128);
    drawLinks();
    onChange({ type: 'links' });
  }

  host.addEventListener('pointerdown', (ev) => {
    const node = ev.target.closest('[data-item-id]');
    if (!node || !canEdit || ev.button !== 0) return;
    const item = byId(node.dataset.itemId);
    if (!item) return;
    if (linkMode) {
      ev.preventDefault();
      if (!linkFrom) {
        linkFrom = item.id;
        drawNodes();
      } else {
        const link = addLink(linkFrom, item.id);
        linkFrom = null;
        drawNodes();
        if (link) selectLink(link);
      }
      return;
    }
    ev.preventDefault();
    dragging = {
      item,
      startX: ev.clientX,
      startY: ev.clientY,
      originX: item.x,
      originY: item.y,
      moved: false,
    };
    node.classList.add('story-node-dragging');
  });
  host.addEventListener('pointermove', (ev) => {
    if (!dragging) return;
    dragging.moved = true;
    dragging.item.x = clampCoordinate(dragging.originX + ev.clientX - dragging.startX);
    dragging.item.y = clampCoordinate(dragging.originY + ev.clientY - dragging.startY);
    const node = layer.querySelector(`[data-item-id="${CSS.escape(dragging.item.id)}"]`);
    if (node) {
      node.style.left = `${dragging.item.x}px`;
      node.style.top = `${dragging.item.y}px`;
    }
    drawLinks();
  });
  const finish = () => {
    if (!dragging) return;
    const moved = dragging.moved;
    layer.querySelector('.story-node-dragging')?.classList.remove('story-node-dragging');
    dragging = null;
    if (moved) onChange({ type: 'positions' });
  };
  host.addEventListener('pointerup', finish);
  host.addEventListener('pointercancel', finish);
  host.addEventListener('pointerleave', finish);
  host.addEventListener('click', (ev) => {
    const linkEl = ev.target.closest('[data-link-from]');
    if (linkEl) {
      selectLink(
        links.find(
          (l) =>
            l.from_item_id === linkEl.dataset.linkFrom && l.to_item_id === linkEl.dataset.linkTo,
        ),
      );
      return;
    }
    if (selectedLink && !ev.target.closest('[data-item-id]')) selectLink(null);
  });

  return {
    load(nextItems, nextLinks) {
      items = nextItems.map((item) => ({
        ...item,
        x: clampCoordinate(item.x),
        y: clampCoordinate(item.y),
      }));
      links = nextLinks.map((link) => ({ ...link }));
      selectedLink = null;
      linkFrom = null;
      render();
    },
    arrange() {
      for (const position of gridPositions(items)) {
        const item = byId(position.item_id);
        item.x = position.x;
        item.y = position.y;
      }
      render();
      onChange({ type: 'positions' });
    },
    layout() {
      return {
        positions: items.map((item) => ({ item_id: item.id, x: item.x, y: item.y })),
        links: links.map((link) => ({
          from_item_id: link.from_item_id,
          to_item_id: link.to_item_id,
          label: link.label || '',
        })),
      };
    },
    setEditable(flag) {
      canEdit = Boolean(flag);
      host.classList.toggle('story-flow-editable', canEdit);
      if (!canEdit) setLinkMode(false);
    },
    setLinkMode,
    get linkMode() {
      return linkMode;
    },
    get selectedLink() {
      return selectedLink;
    },
    removeSelectedLink,
    labelSelectedLink,
  };
}
