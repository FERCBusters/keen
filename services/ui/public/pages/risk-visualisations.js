// Risk → control visualisations for the Risks dashboard: a bipartite graph and
// a heatmap built from the framework's risk/control mappings, weighted by the
// inherent or residual score. The page wires its own controls through init().
import { apiGet, debounce, esc } from '/app.js';
import { riskHref as riskPageHref, controlHref as controlPageHref } from '/pages/risk-common.js';

let framework = '';
let riskVizTopRisks = null;
let riskVizTopControls = null;
let riskControlsGraph = null;
let riskControlsHeatmap = null;
let riskControlsGraphMeta = null;
let riskControlsHeatmapMeta = null;
let riskVizScoreMode = 'residual';
let riskControlGraphData = null;
let riskVisualisationsLoaded = false;

function riskHref(id) {
  return riskPageHref(id, framework);
}

function controlHref(id) {
  return controlPageHref(id, framework);
}

export function initRiskVisualisations(options) {
  framework = options.framework || '';
  riskVizTopRisks = options.topRisks || null;
  riskVizTopControls = options.topControls || null;
  riskControlsGraph = options.graph || null;
  riskControlsHeatmap = options.heatmap || null;
  riskControlsGraphMeta = options.graphMeta || null;
  riskControlsHeatmapMeta = options.heatmapMeta || null;
  riskVizScoreMode = normaliseRiskVizScoreMode(options.scoreMode || 'residual');
}

export function setRiskVizScoreMode(mode) {
  riskVizScoreMode = normaliseRiskVizScoreMode(mode);
  renderRiskVisualisations();
}

export function riskVisualisationsAreLoaded() {
  return riskVisualisationsLoaded;
}

function clampRiskVizLimit(input, fallback) {
  const n = Math.floor(Number(input?.value || fallback));
  if (!Number.isFinite(n)) return fallback;
  return Math.max(5, Math.min(200, n));
}

function normaliseRiskVizScoreMode(value) {
  return value === 'inherent' ? 'inherent' : 'residual';
}

function riskVizScoreLabel(mode = riskVizScoreMode) {
  return normaliseRiskVizScoreMode(mode) === 'inherent'
    ? 'inherent risk score'
    : 'residual risk score';
}

function riskVizScoreShortLabel(mode = riskVizScoreMode) {
  return normaliseRiskVizScoreMode(mode) === 'inherent' ? 'Risk score' : 'Residual score';
}

function riskVizScoreValue(item, mode = riskVizScoreMode) {
  const scoreMode = normaliseRiskVizScoreMode(mode);
  const field = scoreMode === 'inherent' ? 'risk_score' : 'residual_risk_score';
  const fallback = scoreMode === 'inherent' ? item?.value : item?.risk_score;
  const n = Number(item?.[field] ?? fallback ?? 0);
  return Number.isFinite(n) ? n : 0;
}

function riskVizNodeHref(node) {
  if (node?.node_kind === 'risk' && node.risk_id) return riskHref(node.risk_id);
  if (node?.node_kind === 'control' && node.control_id) return controlHref(node.control_id);
  return '';
}

function riskVizPrimaryLabel(node) {
  if (!node) return '—';
  if (node.node_kind === 'control') return node.ref || node.label || 'Control';
  return node.label || node.asset || 'Risk';
}

function riskVizSecondaryLabel(node) {
  if (!node) return '';
  if (node.node_kind === 'control') return node.title || '';
  const parts = [node.category, node.subcategory].filter(Boolean).join(' / ');
  const types = (node.risk_types || []).join(', ');
  return [parts, types].filter(Boolean).join(' • ');
}

function truncateRiskVizText(value, max = 72) {
  const text = String(value || '')
    .replace(/\s+/g, ' ')
    .trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

function splitRiskVizLabel(value, maxChars = 34, maxLines = 2) {
  const text =
    String(value || '—')
      .replace(/\s+/g, ' ')
      .trim() || '—';
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

function addRiskVizText(
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
  (lines || ['—']).forEach((line, idx) => {
    text
      .append('tspan')
      .attr('x', x)
      .attr('dy', idx === 0 ? 0 : lineHeight)
      .text(line);
  });
  return text;
}

function riskVizButton(label, action, title) {
  return `<button class="viz-zoom-btn" type="button" data-zoom="${esc(action)}" title="${esc(title)}" aria-label="${esc(title)}">${esc(label)}</button>`;
}

function riskControlDataset() {
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const data = riskControlGraphData || { nodes: [], links: [] };
  const nodeById = new Map((data.nodes || []).map((node) => [node.id, node]));
  const linkedRiskIds = new Set();
  const linkedControlIds = new Set();
  for (const link of data.links || []) {
    if (nodeById.get(link.source)?.node_kind === 'risk') linkedRiskIds.add(link.source);
    if (nodeById.get(link.target)?.node_kind === 'control') linkedControlIds.add(link.target);
  }
  const risks = (data.nodes || [])
    .filter((node) => node.node_kind === 'risk' && linkedRiskIds.has(node.id))
    .sort(
      (a, b) =>
        Number(b.link_count || 0) - Number(a.link_count || 0) ||
        riskVizScoreValue(b, scoreMode) - riskVizScoreValue(a, scoreMode) ||
        String(a.label || '').localeCompare(String(b.label || '')),
    )
    .slice(0, clampRiskVizLimit(riskVizTopRisks, 30));
  const selectedRiskIds = new Set(risks.map((node) => node.id));
  const controlsForTopRisks = new Set(
    (data.links || [])
      .filter((link) => selectedRiskIds.has(link.source))
      .map((link) => link.target),
  );
  const controls = (data.nodes || [])
    .filter(
      (node) =>
        node.node_kind === 'control' &&
        linkedControlIds.has(node.id) &&
        controlsForTopRisks.has(node.id),
    )
    .sort(
      (a, b) =>
        Number(b.link_count || 0) - Number(a.link_count || 0) ||
        String(a.ref || a.label || '').localeCompare(String(b.ref || b.label || '')),
    )
    .slice(0, clampRiskVizLimit(riskVizTopControls, 50));
  const controlIds = new Set(controls.map((node) => node.id));
  const links = (data.links || []).filter(
    (link) => selectedRiskIds.has(link.source) && controlIds.has(link.target),
  );
  return { risks, controls, links };
}

function renderRiskControlsGraph() {
  const el = riskControlsGraph;
  if (!el) return;
  if (!window.d3) {
    el.textContent = 'D3 is not available.';
    return;
  }
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const { risks, controls: controlNodes, links } = riskControlDataset();
  if (!risks.length || !controlNodes.length || !links.length) {
    el.textContent = 'No risk/control mappings are available for this framework yet.';
    return;
  }
  el.innerHTML = '';
  el.classList.add('interested-party-bipartite-viz');
  const riskMap = new Map(risks.map((node) => [node.id, node]));
  const controlMap = new Map(controlNodes.map((node) => [node.id, node]));
  const compact = el.clientWidth && el.clientWidth < 720;
  const viewportWidth = Math.max(compact ? 460 : 720, Math.floor(el.clientWidth || 980));
  const rowCount = Math.max(risks.length, controlNodes.length, 4);
  const rowGap = compact ? 62 : 64;
  const topPad = 76;
  const bottomPad = 40;
  const contentHeight = Math.max(340, rowCount * rowGap + topPad + bottomPad);
  const viewportHeight = Math.max(520, Math.min(920, contentHeight));
  const leftColumnWidth = Math.max(
    compact ? 180 : 280,
    Math.min(compact ? 300 : 430, viewportWidth * 0.34),
  );
  const rightColumnWidth = Math.max(
    compact ? 180 : 250,
    Math.min(compact ? 300 : 380, viewportWidth * 0.3),
  );
  const middleWidth = Math.max(compact ? 220 : 330, Math.min(560, viewportWidth * 0.42));
  const contentWidth = Math.ceil(leftColumnWidth + middleWidth + rightColumnWidth + 56);
  const leftX = Math.round(leftColumnWidth + 18);
  const rightX = Math.round(leftX + middleWidth);
  const riskY = d3
    .scalePoint()
    .domain(risks.map((node) => node.id))
    .range([topPad + 32, contentHeight - bottomPad])
    .padding(0.6);
  const controlY = d3
    .scalePoint()
    .domain(controlNodes.map((node) => node.id))
    .range([topPad + 32, contentHeight - bottomPad])
    .padding(0.6);
  const maxScore = d3.max(links, (link) => riskVizScoreValue(link, scoreMode)) || 1;
  const strokeWidth = d3
    .scaleLinear()
    .domain([0, Math.max(1, maxScore)])
    .range([1.5, 6.5]);

  const zoomControls = document.createElement('div');
  zoomControls.className = 'viz-zoom-controls no-print';
  zoomControls.innerHTML = [
    riskVizButton('+', 'in', 'Zoom in'),
    riskVizButton('−', 'out', 'Zoom out'),
    riskVizButton('Reset', 'reset', 'Reset zoom to fit'),
  ].join('');
  el.appendChild(zoomControls);

  const svg = d3
    .select(el)
    .append('svg')
    .attr('width', '100%')
    .attr('height', viewportHeight)
    .attr('viewBox', `0 0 ${viewportWidth} ${viewportHeight}`)
    .attr('preserveAspectRatio', 'xMidYMid meet')
    .attr('role', 'img')
    .attr('aria-label', 'Risks to controls graph');
  const scene = svg.append('g').attr('class', 'risk-controls-graph-scene');
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
  zoomControls.addEventListener('click', (ev) => {
    const btn = ev.target?.closest?.('[data-zoom]');
    if (!btn) return;
    const action = btn.getAttribute('data-zoom');
    if (action === 'in') svg.transition().duration(160).call(zoom.scaleBy, 1.25);
    else if (action === 'out') svg.transition().duration(160).call(zoom.scaleBy, 0.8);
    else svg.transition().duration(160).call(zoom.transform, fitTransform);
  });

  addRiskVizText(scene, ['Risks'], {
    x: leftX - 16,
    y: 24,
    anchor: 'end',
    fontSize: 13,
    weight: 700,
  });
  addRiskVizText(scene, ['Controls'], {
    x: rightX + 16,
    y: 24,
    anchor: 'start',
    fontSize: 13,
    weight: 700,
  });
  scene
    .append('text')
    .attr('x', leftX - 16)
    .attr('y', 46)
    .attr('font-size', 12)
    .attr('fill', 'currentColor')
    .attr('opacity', 0.72)
    .attr('text-anchor', 'end')
    .text(`${risks.length} risk(s), ${controlNodes.length} control(s), ${links.length} mapping(s)`);

  const path = (link) => {
    const y1 = riskY(link.source) || topPad;
    const y2 = controlY(link.target) || topPad;
    const mid = (leftX + rightX) / 2;
    return `M${leftX},${y1} C${mid},${y1} ${mid},${y2} ${rightX},${y2}`;
  };
  scene
    .append('g')
    .attr('fill', 'none')
    .selectAll('path')
    .data(links)
    .join('path')
    .attr('d', path)
    .attr('stroke', (link) => riskMap.get(link.source)?.color || 'currentColor')
    .attr('stroke-opacity', 0.28)
    .attr('stroke-width', (link) => strokeWidth(riskVizScoreValue(link, scoreMode)))
    .append('title')
    .text((link) => {
      const risk = riskMap.get(link.source);
      const control = controlMap.get(link.target);
      const selectedScore = riskVizScoreValue(link, scoreMode);
      return `${riskVizPrimaryLabel(risk)} → ${riskVizPrimaryLabel(control)}\n${riskVizScoreShortLabel(scoreMode)}: ${selectedScore}\nInherent risk score: ${link.risk_score ?? link.value ?? '—'}\nResidual risk score: ${link.residual_risk_score ?? '—'}`;
    });

  const renderNode = (selection, side) => {
    const isRisk = side === 'risk';
    const x = isRisk ? leftX : rightX;
    const yScale = isRisk ? riskY : controlY;
    const anchor = isRisk ? 'end' : 'start';
    const labelX = isRisk ? -14 : 14;
    const maxPrimary = isRisk ? (compact ? 28 : 44) : compact ? 28 : 36;
    const maxSecondary = isRisk ? (compact ? 26 : 42) : compact ? 28 : 48;
    selection
      .attr('class', 'interested-party-bipartite-node')
      .attr('transform', (node) => `translate(${x},${yScale(node.id) || topPad})`);
    selection
      .append('circle')
      .attr('r', isRisk ? 7 : 6)
      .attr('fill', (node) => node.color || 'currentColor')
      .attr('opacity', isRisk ? 0.85 : 0.62);
    selection.each(function (node) {
      const group = d3.select(this);
      const href = riskVizNodeHref(node);
      const primary = `${riskVizPrimaryLabel(node)}${node.link_count ? ` (${node.link_count})` : ''}`;
      const primaryLines = splitRiskVizLabel(primary, maxPrimary, isRisk ? 3 : 2);
      addRiskVizText(group, primaryLines, {
        x: labelX,
        y: primaryLines.length > 1 ? -10 : -3,
        anchor,
        fontSize: 12,
        weight: 650,
        lineHeight: 12.5,
        underline: Boolean(href),
      });
      const secondary = riskVizSecondaryLabel(node);
      if (secondary)
        addRiskVizText(group, splitRiskVizLabel(secondary, maxSecondary, 1), {
          x: labelX,
          y: primaryLines.length > 1 ? 22 : 13,
          anchor,
          fontSize: 10.5,
          weight: 400,
          opacity: 0.68,
        });
      if (href)
        group.style('cursor', 'pointer').on('click', () => {
          window.location.href = href;
        });
    });
    selection
      .append('title')
      .text(
        (node) =>
          `${riskVizPrimaryLabel(node)}${riskVizSecondaryLabel(node) ? `\n${riskVizSecondaryLabel(node)}` : ''}`,
      );
  };
  scene.append('g').selectAll('g').data(risks).join('g').call(renderNode, 'risk');
  scene.append('g').selectAll('g').data(controlNodes).join('g').call(renderNode, 'control');
}

function renderRiskControlsHeatmap() {
  const el = riskControlsHeatmap;
  if (!el) return;
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const { risks, controls: controlNodes, links } = riskControlDataset();
  if (!risks.length || !controlNodes.length || !links.length) {
    el.textContent = 'No risk/control mappings are available for this framework yet.';
    return;
  }
  const linkMap = new Map(links.map((link) => [`${link.source}::${link.target}`, link]));
  const maxScore = Math.max(1, ...links.map((link) => riskVizScoreValue(link, scoreMode)));
  const scoreLabel = riskVizScoreLabel(scoreMode);
  el.classList.add('interested-party-control-matrix');
  el.innerHTML = `
    <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3 no-print">
      <div class="small-muted">${esc(risks.length)} risk(s), ${esc(controlNodes.length)} control(s), ${esc(links.length)} mapping(s). Darker cells have higher ${esc(scoreLabel)}.</div>
      <input class="form-control form-control-sm" data-risk-control-matrix-filter type="search" placeholder="Filter risks or controls…" style="max-width:320px;">
    </div>
    <div data-risk-control-matrix-host></div>
  `;
  const input = el.querySelector('[data-risk-control-matrix-filter]');
  const host = el.querySelector('[data-risk-control-matrix-host]');
  const renderTable = () => {
    const qText = (input?.value || '').trim().toLowerCase();
    const rowMatches = new Set();
    const colMatches = new Set();
    if (qText) {
      for (const risk of risks) {
        const haystack =
          `${risk.label || ''} ${risk.asset || ''} ${risk.category || ''} ${risk.subcategory || ''} ${(risk.risk_types || []).join(' ')} ${risk.threat_summary || ''}`.toLowerCase();
        if (haystack.includes(qText)) rowMatches.add(risk.id);
      }
      for (const control of controlNodes) {
        const haystack =
          `${control.ref || ''} ${control.label || ''} ${control.title || ''}`.toLowerCase();
        if (haystack.includes(qText)) colMatches.add(control.id);
      }
    }
    const visibleCols =
      !qText || rowMatches.size
        ? controlNodes
        : controlNodes.filter((node) => colMatches.has(node.id));
    const visibleColIds = new Set(visibleCols.map((node) => node.id));
    const visibleRows = !qText
      ? risks
      : rowMatches.size
        ? risks.filter((node) => rowMatches.has(node.id))
        : risks.filter((node) =>
            links.some((link) => link.source === node.id && visibleColIds.has(link.target)),
          );
    if (!visibleRows.length || !visibleCols.length) {
      host.innerHTML =
        '<div class="border rounded p-3 bg-light small-muted">No risks or controls match that filter.</div>';
      return;
    }
    host.innerHTML = `
      <div class="interested-party-control-matrix-wrap risk-control-heatmap-wrap">
        <table class="table table-sm table-bordered align-middle mb-0 interested-party-control-matrix-table risk-control-heatmap-table">
          <thead>
            <tr>
              <th class="matrix-sticky-col matrix-corner">Risk scenario</th>
              ${visibleCols.map((control) => `<th class="matrix-control-header" title="${esc(control.ref || control.label || 'Control')} — ${esc(control.title || '')}"><a href="${esc(controlHref(control.control_id || ''))}" class="matrix-control-link">${esc(control.ref || control.label || 'Control')}</a></th>`).join('')}
            </tr>
          </thead>
          <tbody>
            ${visibleRows
              .map((risk) => {
                const linkedCount = visibleCols.reduce(
                  (total, control) => total + (linkMap.has(`${risk.id}::${control.id}`) ? 1 : 0),
                  0,
                );
                const subtitle = riskVizSecondaryLabel(risk);
                return `<tr>
                <th class="matrix-sticky-col matrix-party-label">
                  <a class="fw-semibold" href="${esc(riskHref(risk.risk_id || ''))}">${esc(truncateRiskVizText(riskVizPrimaryLabel(risk), 92))}</a>
                  <div class="small-muted">${esc(subtitle)}${linkedCount ? ` • ${linkedCount} shown control(s)` : ''}</div>
                  <div class="small-muted">Risk score: ${esc(String(risk.risk_score ?? '—'))} • Residual: ${esc(String(risk.residual_risk_score ?? '—'))}</div>
                </th>
                ${visibleCols
                  .map((control) => {
                    const link = linkMap.get(`${risk.id}::${control.id}`);
                    if (!link)
                      return '<td class="matrix-cell matrix-cell-empty" aria-label="Not linked"></td>';
                    const score = riskVizScoreValue(link, scoreMode);
                    const alpha = Math.max(0.18, Math.min(0.92, 0.16 + (score / maxScore) * 0.74));
                    const title = `${riskVizPrimaryLabel(risk)} → ${riskVizPrimaryLabel(control)} — ${riskVizScoreShortLabel(scoreMode).toLowerCase()} ${score}, inherent ${link.risk_score ?? link.value ?? '—'}, residual ${link.residual_risk_score ?? '—'}`;
                    return `<td class="matrix-cell risk-control-heatmap-cell" title="${esc(title)}" style="background:rgba(var(--bs-primary-rgb), ${alpha.toFixed(3)});"><a href="${esc(controlHref(control.control_id || ''))}" aria-label="${esc(title)}">${esc(String(score))}</a></td>`;
                  })
                  .join('')}
              </tr>`;
              })
              .join('')}
          </tbody>
        </table>
      </div>
    `;
  };
  input?.addEventListener('input', debounce(renderTable, 120));
  renderTable();
}

export function renderRiskVisualisations() {
  renderRiskControlsGraph();
  renderRiskControlsHeatmap();
  const { risks, controls: controlNodes, links } = riskControlDataset();
  const totalRisks = (riskControlGraphData?.nodes || []).filter(
    (node) => node.node_kind === 'risk',
  ).length;
  const totalControls = (riskControlGraphData?.nodes || []).filter(
    (node) => node.node_kind === 'control',
  ).length;
  const meta = `${risks.length} of ${totalRisks} risks, ${controlNodes.length} of ${totalControls} controls, ${links.length} visible mappings for ${framework || 'the current framework'}.`;
  const scoreLabel = riskVizScoreLabel();
  if (riskControlsGraphMeta)
    riskControlsGraphMeta.textContent = `${meta} Link thickness follows ${scoreLabel}.`;
  if (riskControlsHeatmapMeta)
    riskControlsHeatmapMeta.textContent = `${meta} Darker cells have higher ${scoreLabel}.`;
}

export async function loadRiskVisualisations(opts = {}) {
  if (!riskControlsGraph && !riskControlsHeatmap) return;
  if (!riskControlGraphData || opts.force) {
    if (riskControlsGraph)
      riskControlsGraph.innerHTML =
        '<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading risk/control graph…</span></div>';
    if (riskControlsHeatmap)
      riskControlsHeatmap.innerHTML =
        '<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading risk/control heatmap…</span></div>';
    riskControlGraphData = await apiGet(
      `/api/v1/graph/risk_control?framework=${encodeURIComponent(framework)}`,
    );
  }
  renderRiskVisualisations();
  riskVisualisationsLoaded = true;
}
