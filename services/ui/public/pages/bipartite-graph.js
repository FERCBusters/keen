// Drawing helpers shared by the bipartite relationship graphs: risks to
// controls, clauses to PESTLE(E) items and interested parties to their related
// items. Each graph keeps its own layout, colours and tooltips.
import { esc } from '/app.js';

// Wraps a label onto at most `maxLines` lines of about `maxChars`, ending with
// an ellipsis when words are dropped.
export function splitLabel(value, maxChars = 34, maxLines = 2) {
  const text =
    String(value || '-')
      .replace(/\s+/g, ' ')
      .trim() || '-';
  if (text.length <= maxChars) return [text];
  const words = text.split(' ');
  const lines = [];
  let current = '';
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > maxChars && current) {
      lines.push(current);
      current = word;
      if (lines.length >= maxLines) break;
    } else {
      current = next;
    }
  }
  if (lines.length < maxLines && current) lines.push(current);
  if (!lines.length) lines.push(text.slice(0, maxChars));
  if (lines.join(' ').length < text.length) {
    // Words were dropped: always mark the cut, even when the last line is short.
    const last = lines.length - 1;
    lines[last] = `${lines[last].slice(0, Math.max(8, maxChars - 1)).trimEnd()}…`;
  }
  return lines.slice(0, maxLines);
}

// Appends one SVG text element with a tspan per line. Text is set through the
// DOM text API, so labels are never parsed as markup.
export function addSvgText(
  selection,
  lines,
  {
    x = 0,
    y = 0,
    anchor = 'start',
    fontSize = 12,
    weight = 600,
    lineHeight = 13,
    opacity = 1,
    underline = false,
  } = {},
) {
  const text = selection
    .append('text')
    .attr('x', x)
    .attr('y', y)
    .attr('font-size', fontSize)
    .attr('font-weight', weight)
    .attr('text-anchor', anchor)
    .attr('fill', 'currentColor')
    .attr('opacity', opacity);
  if (underline) text.style('text-decoration', 'underline');
  (lines || ['-']).forEach((line, idx) => {
    text
      .append('tspan')
      .attr('x', x)
      .attr('dy', idx === 0 ? 0 : lineHeight)
      .text(line);
  });
  return text;
}

function zoomButton(label, action, title) {
  return `<button class="viz-zoom-btn" type="button" data-zoom="${esc(action)}" title="${esc(title)}" aria-label="${esc(title)}">${esc(label)}</button>`;
}

// Adds zoom in, zoom out and reset buttons to `el` and fits the scene into the
// viewport. Dragging pans; the mouse wheel scrolls the page.
export function addZoom(
  el,
  svg,
  scene,
  { contentWidth, contentHeight, viewportWidth, viewportHeight },
) {
  // d3 is the page's vendored global, as in the graphs that call this.
  const controls = document.createElement('div');
  controls.className = 'viz-zoom-controls no-print';
  controls.innerHTML = [
    zoomButton('+', 'in', 'Zoom in'),
    zoomButton('−', 'out', 'Zoom out'),
    zoomButton('Reset', 'reset', 'Reset zoom to fit'),
  ].join('');
  el.insertBefore(controls, svg.node());
  const fitScale = Math.min(
    1,
    (viewportWidth - 20) / contentWidth,
    (viewportHeight - 20) / contentHeight,
  );
  const fitX = Math.max(10, (viewportWidth - contentWidth * fitScale) / 2);
  const fitY = Math.max(10, (viewportHeight - contentHeight * fitScale) / 2);
  const fitTransform = d3.zoomIdentity.translate(fitX, fitY).scale(fitScale);
  const zoom = d3
    .zoom()
    .scaleExtent([Math.min(0.15, fitScale), 4])
    .translateExtent([
      [-contentWidth, -contentHeight],
      [contentWidth * 2, contentHeight * 2],
    ])
    .on('zoom', (ev) => scene.attr('transform', ev.transform));
  svg.call(zoom).on('wheel.zoom', null);
  svg.call(zoom.transform, fitTransform);
  controls.addEventListener('click', (ev) => {
    const btn = ev.target?.closest?.('[data-zoom]');
    if (!btn) return;
    const action = btn.getAttribute('data-zoom');
    if (action === 'in') svg.transition().duration(160).call(zoom.scaleBy, 1.25);
    else if (action === 'out') svg.transition().duration(160).call(zoom.scaleBy, 0.8);
    else svg.transition().duration(160).call(zoom.transform, fitTransform);
  });
}

// The column headings above each side and the count line under the left one.
export function addHeadings(scene, { leftX, rightX, titles, summary, y = 24, summaryY = y + 22 }) {
  addSvgText(scene, [titles[0]], { x: leftX - 16, y, anchor: 'end', fontSize: 13, weight: 700 });
  addSvgText(scene, [titles[1]], { x: rightX + 16, y, anchor: 'start', fontSize: 13, weight: 700 });
  scene
    .append('text')
    .attr('x', leftX - 16)
    .attr('y', summaryY)
    .attr('font-size', 12)
    .attr('fill', 'currentColor')
    .attr('opacity', 0.72)
    .attr('text-anchor', 'end')
    .text(summary);
}

// A curve from a node on the left column to one on the right.
export function curvedLink(leftX, rightX, leftY, rightY, fallbackY) {
  return (link) => {
    const y1 = leftY(link.source) || fallbackY;
    const y2 = rightY(link.target) || fallbackY;
    const mid = (leftX + rightX) / 2;
    return `M${leftX},${y1} C${mid},${y1} ${mid},${y2} ${rightX},${y2}`;
  };
}

// A node's label: its name (underlined and clickable when it links to a page)
// and an optional muted second line. Left-column labels sit to the left.
export function addNodeLabel(group, { left, primaryLines, secondaryLines = [], secondaryY, href }) {
  const x = left ? -14 : 14;
  const anchor = left ? 'end' : 'start';
  addSvgText(group, primaryLines, {
    x,
    y: primaryLines.length > 1 ? -10 : -3,
    anchor,
    fontSize: 12,
    weight: 650,
    lineHeight: 12.5,
    underline: Boolean(href),
  });
  if (secondaryLines.length)
    addSvgText(group, secondaryLines, {
      x,
      y: primaryLines.length > 1 ? secondaryY : 13,
      anchor,
      fontSize: 10.5,
      weight: 400,
      opacity: 0.68,
    });
  if (href)
    group.style('cursor', 'pointer').on('click', () => {
      window.location.href = href;
    });
}
