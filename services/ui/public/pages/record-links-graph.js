// One record and what it is linked to, drawn as a small graph: the record on
// the left, its controls and clauses in groups on the right. The risk page and
// the PESTLE(E) item page pass their own record and groups, so a risk of any
// kind is pictured the same way.
import { addNodeLabel, addSvgText, addZoom, splitLabel } from '/pages/bipartite-graph.js';

const ROW_GAP = 46;
const TOP = 56;
const GROUP_COLOURS = ['#2563eb', '#0f766e'];
// A link with no weight of its own is drawn at this strength, from 0 to 1.
const PLAIN_STRENGTH = 0.4;

// `groups` is a list of { title, items }, and an item is { label, detail,
// href, strength, note }: `strength` runs from 0 to 1 and sets the thickness
// of the item's link, and `note` is added to the link's tooltip.
export function renderRecordLinksGraph(el, { label, detail, groups, empty }) {
  const shown = groups.filter((group) => group.items.length);
  if (!shown.length) {
    el.textContent = empty;
    return;
  }
  if (!window.d3) {
    el.textContent = 'D3 is not available.';
    return;
  }
  el.innerHTML = '';
  el.classList.add('bipartite-viz');

  const viewportWidth = Math.max(520, Math.floor(el.clientWidth || 980));
  const leftX = Math.round(viewportWidth * 0.38);
  const rightX = Math.round(viewportWidth * 0.62);
  const targets = [];
  const headings = [];
  let y = TOP;
  shown.forEach((group, index) => {
    headings.push({ title: `${group.title} (${group.items.length})`, y });
    for (const item of group.items) {
      y += ROW_GAP;
      targets.push({ ...item, y, colour: GROUP_COLOURS[index % GROUP_COLOURS.length] });
    }
    y += ROW_GAP;
  });
  const contentHeight = y;
  const viewportHeight = Math.max(320, Math.min(720, contentHeight));
  const recordY = (targets[0].y + targets[targets.length - 1].y) / 2;
  const mid = (leftX + rightX) / 2;

  const svg = d3
    .select(el)
    .append('svg')
    .attr('width', '100%')
    .attr('height', viewportHeight)
    .attr('viewBox', `0 0 ${viewportWidth} ${viewportHeight}`)
    .attr('preserveAspectRatio', 'xMidYMid meet')
    .attr('role', 'img')
    .attr('aria-label', `${label} and what it is linked to`);
  const scene = svg.append('g').attr('class', 'record-links-graph-scene');
  addZoom(el, svg, scene, {
    contentWidth: viewportWidth,
    contentHeight,
    viewportWidth,
    viewportHeight,
  });

  for (const heading of headings)
    addSvgText(scene, [heading.title], { x: rightX + 14, y: heading.y, fontSize: 13, weight: 700 });

  scene
    .append('g')
    .attr('fill', 'none')
    .selectAll('path')
    .data(targets)
    .join('path')
    .attr(
      'd',
      (target) =>
        `M${leftX},${recordY} C${mid},${recordY} ${mid},${target.y} ${rightX},${target.y}`,
    )
    .attr('stroke', (target) => target.colour)
    .attr('stroke-opacity', 0.32)
    .attr('stroke-width', (target) => 1.5 + (target.strength ?? PLAIN_STRENGTH) * 5)
    .append('title')
    .text((target) => `${label} × ${target.label}${target.note ? `\n${target.note}` : ''}`);

  const record = scene
    .append('g')
    .attr('class', 'bipartite-node')
    .attr('transform', `translate(${leftX},${recordY})`);
  record.append('circle').attr('r', 8).attr('fill', '#7c3aed').attr('opacity', 0.85);
  addNodeLabel(record, {
    left: true,
    primaryLines: splitLabel(label, 38, 2),
    secondaryLines: splitLabel(detail, 44, 1),
    secondaryY: 22,
  });

  scene
    .append('g')
    .selectAll('g')
    .data(targets)
    .join('g')
    .attr('class', 'bipartite-node')
    .attr('transform', (target) => `translate(${rightX},${target.y})`)
    .each(function (target) {
      const node = d3.select(this);
      node.append('circle').attr('r', 6).attr('fill', target.colour).attr('opacity', 0.7);
      addNodeLabel(node, {
        left: false,
        primaryLines: [target.label],
        secondaryLines: target.detail ? splitLabel(target.detail, 52, 1) : [],
        href: target.href,
      });
    });
}
