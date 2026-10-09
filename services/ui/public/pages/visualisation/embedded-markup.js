// Embedded visualisation controls and mount options. No page initialization.
import {esc} from '/app.js';

const EMBEDDED_VIZ_MODE_OPTIONS = {
  graph: 'Graph: sources × controls',
  heatmap: 'Heatmap: sources × controls',
  sunburst_source: 'Sunburst: by source',
  sunburst_control: 'Sunburst: by control',
  graph_clause_events: 'Graph: sources × clauses',
  heatmap_clause_events: 'Heatmap: sources × clauses',
  graph_clause_control: 'Graph: clauses × controls',
  histogram: 'Histogram: Events over time',
};

const EMBEDDED_VIZ_MODE_ORDER = [
  'graph',
  'heatmap',
  'sunburst_source',
  'sunburst_control',
  'graph_clause_events',
  'heatmap_clause_events',
  'graph_clause_control',
  'histogram',
];

function _embeddedVizModesFromMount(mount) {
  const raw = String(mount?.dataset?.vizModes || '').trim();
  if (!raw || raw.toLowerCase() === 'all') return EMBEDDED_VIZ_MODE_ORDER.slice();
  const modes = raw.split(',').map((x) => x.trim()).filter((x) => EMBEDDED_VIZ_MODE_OPTIONS[x]);
  return modes.length ? modes : EMBEDDED_VIZ_MODE_ORDER.slice();
}

export function ensureEmbeddedVisualisationMarkup() {
  const mount = document.querySelector('[data-keen-visualisation-mount]');
  if (!mount || mount.dataset.visualisationMarkupReady === '1') return;
  const modes = _embeddedVizModesFromMount(mount);
  const options = modes.map((mode, idx) => `<option value="${mode}"${idx === 0 ? ' selected' : ''}>${EMBEDDED_VIZ_MODE_OPTIONS[mode]}</option>`).join('');
  const distribution = String(mount.dataset.showDistribution || '1') !== '0';
  const gaps = String(mount.dataset.showCoverageGaps || '1') !== '0';
  const title = esc(String(mount.dataset.vizTitle || 'Visualisations').trim() || 'Visualisations');
  const intro = esc(String(mount.dataset.vizIntro || 'Explore evidence relationships in context without leaving this page.').trim());
  mount.innerHTML = `
    <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3">
      <div>
        <h2 class="h4 mb-1">${title}</h2>
        <div class="small-muted">${intro}</div>
      </div>
    </div>
    <div class="row g-3">
      <div class="col-12 ${distribution ? 'col-lg-8' : ''}">
        <div class="card" data-mosp-filter-section="visualisation-filters" data-mosp-filter-title="Visualisation filters">
          <div class="card-header d-flex flex-wrap gap-2 align-items-center justify-content-between">
            <div>
              <div class="d-flex flex-wrap gap-2 align-items-center">
                <div class="fw-bold" id="vizTitle">Visualisation</div>
                <select id="vizSelect" class="form-select form-select-sm" style="width:auto;">${options}</select>
              </div>
              <div class="small-muted" id="vizSubtitle">Loading visualisation…</div>
              <div class="small text-body-secondary mt-1" id="vizCaption" role="note"></div>
            </div>
          </div>
          <div class="card-body border-bottom no-print" data-mosp-filter-body>
            <div class="d-flex flex-wrap gap-3 align-items-center">
              <div id="controlsDateRange" class="d-flex flex-wrap gap-2 align-items-center">
                <label class="small-muted">From</label>
                <input id="dateFrom" type="date" class="form-control form-control-sm d-none" autocomplete="off">
                <div class="input-group input-group-sm" style="width:170px;">
                  <input id="dateFromDmy" type="text" class="form-control form-control-sm" inputmode="numeric" placeholder="dd/mm/yyyy" autocomplete="off">
                  <button id="dateFromPick" class="btn btn-outline-secondary" type="button" title="Pick date"><i class="bi bi-calendar"></i></button>
                </div>
                <label class="small-muted">To</label>
                <input id="dateTo" type="date" class="form-control form-control-sm d-none" autocomplete="off">
                <div class="input-group input-group-sm" style="width:170px;">
                  <input id="dateToDmy" type="text" class="form-control form-control-sm" inputmode="numeric" placeholder="dd/mm/yyyy" autocomplete="off">
                  <button id="dateToPick" class="btn btn-outline-secondary" type="button" title="Pick date"><i class="bi bi-calendar"></i></button>
                </div>
                <button id="dateApply" class="btn btn-sm btn-outline-primary" type="button">Apply</button>
                <button id="vizReset" class="btn btn-sm btn-outline-primary" type="button">Reset</button>
              </div>

              <div id="controlsHeatmap" class="flex-wrap gap-2 align-items-center d-none">
                <label class="small-muted">Top sources</label>
                <input id="heatmapTopSources" type="number" class="form-control form-control-sm" min="5" max="200" value="18" style="width:90px;">
                <label class="small-muted">Top controls</label>
                <input id="heatmapTopControls" type="number" class="form-control form-control-sm" min="5" max="200" value="36" style="width:90px;">
                <button id="heatmapApply" class="btn btn-sm btn-outline-primary" type="button">Apply</button>
              </div>

              <div id="controlsGraph" class="flex-wrap gap-2 align-items-center d-none">
                <label class="small-muted">Top sources</label>
                <input id="graphTopSources" type="number" class="form-control form-control-sm" min="0" max="500" value="30" style="width:90px;">
                <label class="small-muted">Top controls</label>
                <input id="graphTopControls" type="number" class="form-control form-control-sm" min="0" max="500" value="60" style="width:90px;">
                <label class="small-muted">Min edge</label>
                <input id="graphMinEdge" type="number" class="form-control form-control-sm" min="1" max="1000000" value="1" style="width:90px;">
                <button id="graphApply" class="btn btn-sm btn-outline-primary" type="button">Apply</button>
              </div>

              <div id="controlsHistogram" class="d-flex flex-wrap gap-2 align-items-center">
                <label class="small-muted">Days</label>
                <input id="histDays" type="number" class="form-control form-control-sm" min="1" max="3650" value="7" style="width:100px;">
                <button id="histApply" class="btn btn-sm btn-outline-primary" type="button">Apply</button>
                <button id="histBack" class="btn btn-sm btn-outline-secondary d-none" type="button" title="Go back one zoom level">Back</button>
                <button id="histResetZoom" class="btn btn-sm btn-outline-secondary d-none" type="button" title="Reset histogram zoom to the selected date range">Reset zoom</button>
                <button id="histZoomMode" class="btn btn-sm btn-outline-secondary" type="button" title="Toggle range-selection mode for the histogram (drag on the chart to zoom)">Zoom mode</button>
              </div>

              <div class="dropdown">
                <button class="btn btn-sm btn-outline-secondary dropdown-toggle" type="button" id="exportMenuBtn" data-bs-toggle="dropdown" aria-expanded="false">Export</button>
                <ul class="dropdown-menu dropdown-menu-end" aria-labelledby="exportMenuBtn">
                  <li><button class="dropdown-item" id="vizExportCsv" type="button">CSV (current view)</button></li>
                  <li><button class="dropdown-item" id="vizExportJson" type="button">JSON (graph + controls)</button></li>
                  <li><button class="dropdown-item" id="vizExportSvg" type="button">SVG (current chart)</button></li>
                </ul>
              </div>
              <button id="copyVizLink" class="btn btn-sm btn-outline-secondary" type="button" title="Copy a shareable link with the current view settings">Copy link</button>
              <button id="saveDefaultViz" class="btn btn-sm btn-outline-success" type="button" title="Save the current view (mode/date range/zoom) as your default">Save as default</button>
            </div>
          </div>
          <div class="card-body" data-mosp-viz-body>
            <div id="vizWrap">
              <svg id="viz"></svg>
              <div id="sunburstOtherPanel" class="mt-2"></div>
            </div>
          </div>
        </div>
      </div>
      ${distribution ? `<div class="col-12 col-lg-4">
        <div class="card mb-3">
          <div class="card-header">
            <div class="fw-bold" id="distributionTitle">Evidence distribution</div>
            <div class="small-muted" id="distributionSubtitle">Top sources and controls by mapped events (selected date range).</div>
          </div>
          <div class="card-body">
            <div class="mb-3">
              <div class="small-muted fw-bold mb-1" id="topSourcesTitle">Top sources</div>
              <div class="table-responsive">
                <table class="table table-sm mb-0" data-sortable>
                  <thead><tr><th id="topSourcesColHeader">Source</th><th id="topSourcesCountHeader" class="text-end">Mapped events</th></tr></thead>
                  <tbody id="topSources"><tr class="table-loading-row"><td colspan="2" class="p-3"><div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading sources…</span></div></td></tr></tbody>
                </table>
              </div>
            </div>
            <div>
              <div class="small-muted fw-bold mb-1" id="topControlsTitle">Top controls</div>
              <div class="table-responsive">
                <table class="table table-sm mb-0" data-sortable>
                  <thead><tr><th id="topControlsColHeader">Control</th><th id="topControlsCountHeader" class="text-end">Mapped events</th></tr></thead>
                  <tbody id="topControls"><tr class="table-loading-row"><td colspan="2" class="p-3"><div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading controls…</span></div></td></tr></tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
        ${gaps ? `<div class="card">
          <div class="card-header d-flex gap-2 align-items-start justify-content-between">
            <div>
              <div class="fw-bold" id="coverageGapsTitle">Controls without events for this date range</div>
              <div class="small-muted" id="coverageGapsSubtitle"><strong>For this selected date range</strong>, no events occurred for the following controls.</div>
            </div>
            <button class="btn btn-sm btn-outline-primary collapsed no-print" type="button" data-bs-toggle="collapse" data-bs-target="#coverageGapsPanel" aria-expanded="false" aria-controls="coverageGapsPanel">
              <span class="when-expanded">Collapse</span><span class="when-collapsed">Expand</span>
            </button>
          </div>
          <div id="coverageGapsPanel" class="collapse">
            <div class="card-body">
              <div class="d-flex gap-2 align-items-center mb-2 no-print">
                <input id="gapQ" class="form-control form-control-sm" type="text" placeholder="Filter controls…">
                <button id="vizReloadGaps" type="button" class="btn btn-sm btn-outline-primary">Reload</button>
              </div>
              <div id="gaps" class="small-muted"><div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading controls…</span></div></div>
            </div>
          </div>
        </div>` : ''}
      </div>` : ''}
    </div>`;
  mount.dataset.visualisationMarkupReady = '1';
}
