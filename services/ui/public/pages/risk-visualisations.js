// Risk → control visualisations for the Risks dashboard: a bipartite graph and
// a heatmap built from the framework's risk/control mappings. Every kind of
// risk is drawn: simple and CIA risks, weighted by the inherent or residual
// score, and PESTLE(E) factors, weighted by their relevance to each control.
// The page wires its own controls through init().
import { apiGet, debounce, esc } from '/app.js';
import {
  addHeadings,
  addNodeLabel,
  addZoom,
  curvedLink,
  splitLabel,
} from '/pages/bipartite-graph.js';
import {
  RISK_METHODS,
  controlHref as controlPageHref,
  methodOf,
  pestleHref,
  relevanceStrength,
  riskHref as riskPageHref,
} from '/pages/risk-common.js';

let framework = '';
let riskVizTopRisks = null;
let riskVizTopControls = null;
let riskVizKind = null;
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
  framework = options.framework;
  riskVizTopRisks = options.topRisks;
  riskVizTopControls = options.topControls;
  riskVizKind = options.kind;
  riskControlsGraph = options.graph;
  riskControlsHeatmap = options.heatmap;
  riskControlsGraphMeta = options.graphMeta;
  riskControlsHeatmapMeta = options.heatmapMeta;
  riskVizScoreMode = normaliseRiskVizScoreMode(options.scoreMode);
}

export function setRiskVizScoreMode(mode) {
  riskVizScoreMode = normaliseRiskVizScoreMode(mode);
  renderRiskVisualisations();
}

export function riskVisualisationsAreLoaded() {
  return riskVisualisationsLoaded;
}

function clampRiskVizLimit(input, fallback) {
  const n = Math.floor(Number(input.value || fallback));
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
  return normaliseRiskVizScoreMode(mode) === 'inherent'
    ? item.risk_score
    : item.residual_risk_score;
}

function isFactor(node) {
  return node.method === 'pestle';
}

// The kind chosen in the filter, or '' for every kind.
function chosenKind() {
  return riskVizKind.value;
}

function riskVizNodeHref(node) {
  if (node.node_kind === 'control') return controlHref(node.control_id);
  return isFactor(node) ? pestleHref(node.pestle_item_id, framework) : riskHref(node.risk_id);
}

// A link's weight from 0 to 1: a factor's relevance to the control, or a
// risk's score against the highest score shown.
function linkStrength(link, scoreMode, maxScore) {
  if (link.relevance) return relevanceStrength(link.relevance);
  return riskVizScoreValue(link, scoreMode) / maxScore;
}

function highestScore(links, scoreMode) {
  return Math.max(
    1,
    ...links.filter((link) => !link.relevance).map((link) => riskVizScoreValue(link, scoreMode)),
  );
}

function noMappingsText() {
  return chosenKind()
    ? 'No risks of this kind are linked to controls in this framework yet.'
    : 'No risk/control mappings are available for this framework yet.';
}

function riskVizPrimaryLabel(node) {
  return node.node_kind === 'control' ? node.ref : node.label;
}

function riskVizSecondaryLabel(node) {
  if (node.node_kind === 'control') return node.title;
  const kind = RISK_METHODS[methodOf(node)].short;
  if (isFactor(node)) return `${kind} · ${node.pestle_type} / ${node.lens}`;
  const parts = [node.category, node.subcategory].filter(Boolean).join(' / ');
  const types = (node.risk_types || []).join(', ');
  return [kind, parts, types].filter(Boolean).join(' · ');
}

function truncateRiskVizText(value, max = 72) {
  const text = String(value).replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

function riskControlDataset() {
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const data = riskControlGraphData || { nodes: [], links: [] };
  const nodeById = new Map(data.nodes.map((node) => [node.id, node]));
  const linkedRiskIds = new Set();
  const linkedControlIds = new Set();
  for (const link of data.links) {
    if (nodeById.get(link.source)?.node_kind === 'risk') linkedRiskIds.add(link.source);
    if (nodeById.get(link.target)?.node_kind === 'control') linkedControlIds.add(link.target);
  }
  const risks = data.nodes
    .filter(
      (node) =>
        node.node_kind === 'risk' &&
        linkedRiskIds.has(node.id) &&
        (!chosenKind() || methodOf(node) === chosenKind()),
    )
    .sort(
      (a, b) =>
        b.link_count - a.link_count ||
        (riskVizScoreValue(b, scoreMode) || 0) - (riskVizScoreValue(a, scoreMode) || 0) ||
        a.label.localeCompare(b.label),
    )
    .slice(0, clampRiskVizLimit(riskVizTopRisks, 30));
  const selectedRiskIds = new Set(risks.map((node) => node.id));
  const controlsForTopRisks = new Set(
    data.links.filter((link) => selectedRiskIds.has(link.source)).map((link) => link.target),
  );
  const controls = data.nodes
    .filter(
      (node) =>
        node.node_kind === 'control' &&
        linkedControlIds.has(node.id) &&
        controlsForTopRisks.has(node.id),
    )
    .sort((a, b) => b.link_count - a.link_count || a.ref.localeCompare(b.ref))
    .slice(0, clampRiskVizLimit(riskVizTopControls, 50));
  const controlIds = new Set(controls.map((node) => node.id));
  const links = data.links.filter(
    (link) => selectedRiskIds.has(link.source) && controlIds.has(link.target),
  );
  return { risks, controls, links };
}

function renderRiskControlsGraph() {
  const el = riskControlsGraph;
  if (!window.d3) {
    el.textContent = 'D3 is not available.';
    return;
  }
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const { risks, controls: controlNodes, links } = riskControlDataset();
  if (!risks.length || !controlNodes.length || !links.length) {
    el.textContent = noMappingsText();
    return;
  }
  el.innerHTML = '';
  el.classList.add('bipartite-viz');
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
  const maxScore = highestScore(links, scoreMode);
  const strokeWidth = d3.scaleLinear().domain([0, 1]).range([1.5, 6.5]);

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
  addZoom(el, svg, scene, { contentWidth, contentHeight, viewportWidth, viewportHeight });
  addHeadings(scene, {
    leftX,
    rightX,
    titles: ['Risks', 'Controls'],
    summary: `${risks.length} risk(s), ${controlNodes.length} control(s), ${links.length} mapping(s)`,
  });
  scene
    .append('g')
    .attr('fill', 'none')
    .selectAll('path')
    .data(links)
    .join('path')
    .attr('d', curvedLink(leftX, rightX, riskY, controlY, topPad))
    .attr('stroke', (link) => riskMap.get(link.source)?.color || 'currentColor')
    .attr('stroke-opacity', 0.28)
    .attr('stroke-width', (link) => strokeWidth(linkStrength(link, scoreMode, maxScore)))
    .append('title')
    .text((link) => {
      const risk = riskMap.get(link.source);
      const control = controlMap.get(link.target);
      if (link.relevance)
        return `${riskVizPrimaryLabel(risk)} × ${riskVizPrimaryLabel(control)}\nRelevance: ${link.relevance.label}`;
      const selectedScore = riskVizScoreValue(link, scoreMode);
      return `${riskVizPrimaryLabel(risk)} × ${riskVizPrimaryLabel(control)}\n${riskVizScoreShortLabel(scoreMode)}: ${selectedScore}\nInherent risk score: ${link.risk_score}\nResidual risk score: ${link.residual_risk_score}`;
    });

  const renderNode = (selection, side) => {
    const isRisk = side === 'risk';
    const x = isRisk ? leftX : rightX;
    const yScale = isRisk ? riskY : controlY;
    const maxPrimary = isRisk ? (compact ? 28 : 44) : compact ? 28 : 36;
    const maxSecondary = isRisk ? (compact ? 26 : 42) : compact ? 28 : 48;
    selection
      .attr('class', 'bipartite-node')
      .attr('transform', (node) => `translate(${x},${yScale(node.id)})`);
    selection
      .append('circle')
      .attr('r', isRisk ? 7 : 6)
      .attr('fill', (node) => node.color || 'currentColor')
      .attr('opacity', isRisk ? 0.85 : 0.62);
    selection.each(function (node) {
      const primary = `${riskVizPrimaryLabel(node)} (${node.link_count})`;
      const secondary = riskVizSecondaryLabel(node);
      addNodeLabel(d3.select(this), {
        left: isRisk,
        primaryLines: splitLabel(primary, maxPrimary, isRisk ? 3 : 2),
        secondaryLines: secondary ? splitLabel(secondary, maxSecondary, 1) : [],
        secondaryY: 22,
        href: riskVizNodeHref(node),
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
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const { risks, controls: controlNodes, links } = riskControlDataset();
  if (!risks.length || !controlNodes.length || !links.length) {
    el.textContent = noMappingsText();
    return;
  }
  const linkMap = new Map(links.map((link) => [`${link.source}::${link.target}`, link]));
  const maxScore = highestScore(links, scoreMode);
  const scoreLabel = riskVizScoreLabel(scoreMode);
  el.classList.add('record-control-matrix');
  el.innerHTML = `
    <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3 no-print">
      <div class="small-muted">${esc(risks.length)} risk(s), ${esc(controlNodes.length)} control(s), ${esc(links.length)} mapping(s). Darker cells have higher ${esc(scoreLabel)}.</div>
      <input class="form-control form-control-sm" data-risk-control-matrix-filter type="search" placeholder="Filter risks or controls" style="max-width:320px;">
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
          `${risk.label} ${risk.asset || ''} ${risk.category || ''} ${risk.subcategory || ''} ${(risk.risk_types || []).join(' ')} ${risk.threat_summary || ''}`.toLowerCase();
        if (haystack.includes(qText)) rowMatches.add(risk.id);
      }
      for (const control of controlNodes) {
        const haystack =
          `${control.ref} ${control.label || ''} ${control.title || ''}`.toLowerCase();
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
      <div class="record-control-matrix-wrap risk-control-heatmap-wrap">
        <table class="table table-sm table-bordered align-middle mb-0 record-control-matrix-table risk-control-heatmap-table">
          <thead>
            <tr>
              <th class="matrix-sticky-col matrix-corner">Risk scenario</th>
              ${visibleCols.map((control) => `<th class="matrix-control-header" title="${esc(control.ref)} - ${esc(control.title)}"><a href="${esc(controlHref(control.control_id))}" class="matrix-control-link">${esc(control.ref)}</a></th>`).join('')}
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
                <th class="matrix-sticky-col matrix-row-label">
                  <a class="fw-semibold" href="${esc(riskVizNodeHref(risk))}">${esc(truncateRiskVizText(riskVizPrimaryLabel(risk), 92))}</a>
                  <div class="small-muted">${esc(subtitle)}${linkedCount ? ` · ${linkedCount} shown control(s)` : ''}</div>
                  <div class="small-muted">${esc(isFactor(risk) ? `Overall relevance: ${risk.overall_relevance.label}` : `Risk score: ${risk.risk_score} · Residual: ${risk.residual_risk_score}`)}</div>
                </th>
                ${visibleCols
                  .map((control) => {
                    const link = linkMap.get(`${risk.id}::${control.id}`);
                    if (!link)
                      return '<td class="matrix-cell matrix-cell-empty" aria-label="Not linked"></td>';
                    const score = link.relevance
                      ? link.relevance.label
                      : riskVizScoreValue(link, scoreMode);
                    const alpha = Math.max(
                      0.18,
                      Math.min(0.92, 0.16 + linkStrength(link, scoreMode, maxScore) * 0.74),
                    );
                    const pair = `${riskVizPrimaryLabel(risk)} × ${riskVizPrimaryLabel(control)}`;
                    const title = link.relevance
                      ? `${pair} - relevance ${score}`
                      : `${pair} - ${riskVizScoreShortLabel(scoreMode).toLowerCase()} ${score}, inherent ${link.risk_score}, residual ${link.residual_risk_score}`;
                    return `<td class="matrix-cell risk-control-heatmap-cell" title="${esc(title)}" style="background:rgba(var(--bs-primary-rgb), ${alpha.toFixed(3)});"><a href="${esc(controlHref(control.control_id))}" aria-label="${esc(title)}">${esc(String(score))}</a></td>`;
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
    (node) => node.node_kind === 'risk' && (!chosenKind() || methodOf(node) === chosenKind()),
  ).length;
  const totalControls = (riskControlGraphData?.nodes || []).filter(
    (node) => node.node_kind === 'control',
  ).length;
  const meta = `${risks.length} of ${totalRisks} risks, ${controlNodes.length} of ${totalControls} controls, ${links.length} visible mappings for ${framework || 'the current framework'}.`;
  const scoreLabel = riskVizScoreLabel();
  const factors = risks.some(isFactor) ? ', or the relevance of a PESTLE(E) factor' : '';
  if (riskControlsGraphMeta)
    riskControlsGraphMeta.textContent = `${meta} Link thickness follows ${scoreLabel}${factors}.`;
  if (riskControlsHeatmapMeta)
    riskControlsHeatmapMeta.textContent = `${meta} Darker cells have higher ${scoreLabel}${factors}.`;
}

export async function loadRiskVisualisations(opts = {}) {
  if (!riskControlGraphData || opts.force) {
    if (riskControlsGraph)
      riskControlsGraph.innerHTML =
        '<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading risk/control graph</span></div>';
    if (riskControlsHeatmap)
      riskControlsHeatmap.innerHTML =
        '<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading risk/control heatmap</span></div>';
    riskControlGraphData = await apiGet(
      `/api/v1/graph/risk_control?framework=${encodeURIComponent(framework)}`,
    );
    // Factors are offered as a kind only to a reader the graph includes them for.
    const factorOption = riskVizKind.querySelector('option[value="pestle"]');
    factorOption.hidden = factorOption.disabled = !riskControlGraphData.includes_factors;
  }
  renderRiskVisualisations();
  riskVisualisationsLoaded = true;
}
