import {initNavbar, apiGet, apiPatch, esc, toast, debounce, enableTableSorting, isoDateToDmy, dmyToIsoDate, wireIsoDmyDateField, getCurrentFramework, withFramework, showInlineLoading, showTableLoading, initCollapsibleFilterSections} from '/app.js';


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

function ensureEmbeddedVisualisationMarkup() {
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

ensureEmbeddedVisualisationMarkup();
initCollapsibleFilterSections();

const me = window.keenMe || window.mospMe || await initNavbar();
const framework = getCurrentFramework();
const visualisationMount = document.querySelector('[data-keen-visualisation-mount]');
function scopedSourceFilter() {
  const direct = String(visualisationMount?.dataset?.vizSource || '').trim();
  if (direct) return direct;
  const paramName = String(visualisationMount?.dataset?.vizSourceParam || '').trim();
  if (!paramName) return '';
  try {
    return String(new URLSearchParams(location.search || '').get(paramName) || '').trim();
  } catch {
    return '';
  }
}
const scopedSource = scopedSourceFilter();
const autoApplyFilters = !!(me?.preferences?.auto_apply_filters);


const status = document.getElementById('status');
const gapsEl = document.getElementById('gaps');
const gapQ = document.getElementById('gapQ');
const coverageGapsPanel = document.getElementById('coverageGapsPanel');

const topSourcesEl = document.getElementById('topSources');
const topControlsEl = document.getElementById('topControls');
const distributionTitle = document.getElementById('distributionTitle');
const distributionSubtitle = document.getElementById('distributionSubtitle');
const topSourcesTitle = document.getElementById('topSourcesTitle');
const topControlsTitle = document.getElementById('topControlsTitle');
const topSourcesColHeader = document.getElementById('topSourcesColHeader');
const topSourcesCountHeader = document.getElementById('topSourcesCountHeader');
const topControlsColHeader = document.getElementById('topControlsColHeader');
const topControlsCountHeader = document.getElementById('topControlsCountHeader');
const coverageGapsTitle = document.getElementById('coverageGapsTitle');
const coverageGapsSubtitle = document.getElementById('coverageGapsSubtitle');

const vizSelect = document.getElementById('vizSelect');
const vizTitle = document.getElementById('vizTitle');
const vizSubtitle = document.getElementById('vizSubtitle');
const vizCaption = document.getElementById('vizCaption');

// Sunburst: small-segment aggregation breakdown panel
const sunburstOtherPanel = document.getElementById('sunburstOtherPanel');

// Control panels
const controlsDateRange = document.getElementById('controlsDateRange');
const dateFrom = document.getElementById('dateFrom');
const dateTo = document.getElementById('dateTo');
const dateFromDmy = document.getElementById('dateFromDmy');
const dateToDmy = document.getElementById('dateToDmy');
const dateFromPick = document.getElementById('dateFromPick');
const dateToPick = document.getElementById('dateToPick');
const dateApply = document.getElementById('dateApply');
const resetBtn = document.getElementById('vizReset') || document.getElementById('reset');

// Date UI: keep internal filters as YYYY-MM-DD (hidden native <input type="date">),
// but display dd/mm/yyyy to users.
function _syncDmyFromIso() {
  if (dateFromDmy && dateFrom) dateFromDmy.value = isoDateToDmy(dateFrom.value || '');
  if (dateToDmy && dateTo) dateToDmy.value = isoDateToDmy(dateTo.value || '');
}

function _syncIsoFromDmy() {
  if (dateFrom && dateFromDmy) {
    const raw = String(dateFromDmy.value || '').trim();
    if (!raw) {
      dateFrom.value = '';
    } else {
      const iso = dmyToIsoDate(raw);
      if (iso) dateFrom.value = iso;
    }
  }
  if (dateTo && dateToDmy) {
    const raw = String(dateToDmy.value || '').trim();
    if (!raw) {
      dateTo.value = '';
    } else {
      const iso = dmyToIsoDate(raw);
      if (iso) dateTo.value = iso;
    }
  }
}

function _setIsoDateInput(el, iso) {
  if (!el) return;
  el.value = iso || '';
}

function _setRangeIso(fromIso, toIso) {
  if (dateFrom) _setIsoDateInput(dateFrom, fromIso);
  if (dateTo) _setIsoDateInput(dateTo, toIso);
  _syncDmyFromIso();
}

wireIsoDmyDateField(dateFrom, dateFromDmy, dateFromPick, {listenInput: false, dispatchChangeOnText: true});
wireIsoDmyDateField(dateTo, dateToDmy, dateToPick, {listenInput: false, dispatchChangeOnText: true});

const controlsHeatmap = document.getElementById('controlsHeatmap');
const heatmapTopSources = document.getElementById('heatmapTopSources');
const heatmapTopControls = document.getElementById('heatmapTopControls');
const heatmapApply = document.getElementById('heatmapApply');

const controlsHistogram = document.getElementById('controlsHistogram');
const histDays = document.getElementById('histDays');
const histApply = document.getElementById('histApply');
const histBack = document.getElementById('histBack');
const histResetZoom = document.getElementById('histResetZoom');
const histZoomModeBtn = document.getElementById('histZoomMode');
const saveDefaultViz = document.getElementById('saveDefaultViz');

// Graph controls + export
const controlsGraph = document.getElementById('controlsGraph');
const graphTopSources = document.getElementById('graphTopSources');
const graphTopControls = document.getElementById('graphTopControls');
const graphMinEdge = document.getElementById('graphMinEdge');
const graphApply = document.getElementById('graphApply');

const exportCsvBtn = document.getElementById('vizExportCsv') || document.getElementById('exportCsv');
const exportJsonBtn = document.getElementById('vizExportJson') || document.getElementById('exportJson');
const exportSvgBtn = document.getElementById('vizExportSvg') || document.getElementById('exportSvg');
const copyVizLinkBtn = document.getElementById('copyVizLink');

// Capture the HTML defaults for numeric controls before query-string overrides apply.
// Reset should restore these defaults, while still respecting any saved default
// visualisation preference for mode/date range.
const DEFAULT_NUMERIC = {
  heatmapTopSources: String(heatmapTopSources?.value ?? '18'),
  heatmapTopControls: String(heatmapTopControls?.value ?? '36'),
  graphTopSources: String(graphTopSources?.value ?? '30'),
  graphTopControls: String(graphTopControls?.value ?? '60'),
  graphMinEdge: String(graphMinEdge?.value ?? '1'),
};

const svg = d3.select('#viz');
const wrap = document.getElementById('vizWrap');
const ACCENT_RGB = (() => {
  try {
    const v = getComputedStyle(document.documentElement)
        .getPropertyValue('--keen-accent-rgb')
        .trim();
    return v || '124, 58, 237';
  } catch {
    return '124, 58, 237';
  }
})();
const accent = (alpha) => `rgba(${ACCENT_RGB}, ${alpha})`;

// -------------------------
// Heatmap colour helpers
// -------------------------
// Minimum intensity for non-zero cells (0..1). Increase to make low values darker/more visible.
const HEATMAP_MIN_INTENSITY = 0.22;
// Where the "base" (source) colour sits within the intensity scale.
const HEATMAP_MIDPOINT = 0.62;
const _heatmapColorCache = new Map();

function _clamp01(x) {
  const n = Number(x);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

function _srgbToLinear(c) {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function _relativeLuminance(rgb) {
  // W3C relative luminance (0..1)
  const r = _srgbToLinear(rgb.r);
  const g = _srgbToLinear(rgb.g);
  const b = _srgbToLinear(rgb.b);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function _mixRgb(a, b, t) {
  // t=0 -> a, t=1 -> b
  const tt = _clamp01(t);
  return d3.rgb(
      a.r + (b.r - a.r) * tt,
      a.g + (b.g - a.g) * tt,
      a.b + (b.b - a.b) * tt
  );
}

// -------------------------
// Histogram colour helpers
// -------------------------
const _histogramColorCache = new Map();

function _histFillStrokeForBase(baseHex) {
  const key = String(baseHex || '').trim().toLowerCase() || '__fallback';
  if (_histogramColorCache.has(key)) return _histogramColorCache.get(key);

  // Special-case neutral "Other" key (kept intentionally grey).
  // We want it to be visible but not compete with the primary source hues.
  if (key.includes('31,31,42') || key === '__other__' || key === 'other') {
    const out = {fill: 'rgba(31,31,42,0.30)', stroke: 'rgba(31,31,42,0.55)'};
    _histogramColorCache.set(key, out);
    return out;
  }

  // Reuse the heatmap "depth" mapper to keep the same hue logic as elsewhere,
  // but pick fixed intensities so the histogram stacks are consistently bold.
  const f = _heatColorForBase(key);
  const out = {
    fill: f(0.72),
    stroke: f(0.90),
  };
  _histogramColorCache.set(key, out);
  return out;
}

function _heatIntensity(v, vMax) {
  const denom = Math.max(1, Number(vMax || 0));
  const raw = _clamp01(Number(v || 0) / denom);
  // Boost small values so they aren't imperceptible.
  const boosted = Math.sqrt(raw);
  return HEATMAP_MIN_INTENSITY + (1 - HEATMAP_MIN_INTENSITY) * boosted;
}

function _heatColorForBase(baseHex) {
  const key = String(baseHex || '').trim().toLowerCase() || '__fallback';
  if (_heatmapColorCache.has(key)) return _heatmapColorCache.get(key);

  const fallback = d3.color('#7c3aed'); // matches default accent in most themes
  const base = d3.color(key) || fallback;
  const baseRgb = d3.rgb(base);

  // Luminance-aware tuning: very light colours (eg yellow) need a tighter
  // "towards-white" range, otherwise low values disappear.
  const lum = _relativeLuminance(baseRgb);

  // How much to mix towards white for the low end.
  // Lower = closer to base (more saturated); higher = closer to white.
  let whiteMix = 0.55;
  if (lum > 0.75) whiteMix = 0.30;
  else if (lum > 0.60) whiteMix = 0.38;
  else if (lum < 0.25) whiteMix = 0.62;

  // How much to mix towards black for the high end.
  let blackMix = 0.62;
  if (lum > 0.75) blackMix = 0.78;
  else if (lum > 0.60) blackMix = 0.72;
  else if (lum < 0.25) blackMix = 0.55;

  const lowRgb = _mixRgb(baseRgb, d3.rgb('#ffffff'), whiteMix);
  const highRgb = _mixRgb(baseRgb, d3.rgb('#000000'), blackMix);

  const lerpLow = d3.interpolateRgb(lowRgb.formatRgb(), baseRgb.formatRgb());
  const lerpHigh = d3.interpolateRgb(baseRgb.formatRgb(), highRgb.formatRgb());

  const fn = (intensity01) => {
    const t = _clamp01(intensity01);
    if (t <= HEATMAP_MIDPOINT) {
      return lerpLow(t / HEATMAP_MIDPOINT);
    }
    return lerpHigh((t - HEATMAP_MIDPOINT) / (1 - HEATMAP_MIDPOINT));
  };

  _heatmapColorCache.set(key, fn);
  return fn;
}

let graphData = null;
let graphDatasets = {control_source: null, clause_source: null, clause_control: null};
let graphDatasetPromises = {control_source: null, clause_source: null, clause_control: null};
// Stats returned by /api/v1/stats/controls for the active date range.
// Used for exports and the "Coverage gaps" list.
let controlStats = null;
let controlStatsPromise = null;
let gaps = [];
let statusHideTimer = null;

const timeseriesCache = new Map();

// Histogram zoom (brush-driven drill-down).
// Each entry captures a prior view so users can step back.
// start_ts/end_ts are ISO strings (UTC, end exclusive).
let histZoomStack = [];
const HIST_MAX_BUCKETS = 5000;

// Histogram range selection (brush) activation.
// We keep it OFF by default so bar clicks still work.
// Users can:
//  - Hold Shift and drag on the histogram to zoom, OR
//  - Toggle "Zoom mode" to drag without a keyboard (touch devices).
let histZoomModeOn = false;
let histBrushShiftActive = false;
let _setHistogramBrushEnabled = null;
const _VIZ_MODES = ['graph', 'heatmap', 'sunburst_source', 'sunburst_control', 'graph_clause_events', 'heatmap_clause_events', 'graph_clause_control', 'histogram'];

let defaultVizPref = (me?.preferences?.default_visualisation && typeof me.preferences.default_visualisation === 'object') ?
  me.preferences.default_visualisation :
  null;

function availableVizModes() {
  const modes = Array.from(vizSelect?.options || [])
      .map((opt) => String(opt.value || '').trim())
      .filter((mode) => _VIZ_MODES.includes(mode));
  return modes.length ? modes : _VIZ_MODES.slice();
}

function firstAvailableVizMode() {
  return availableVizModes()[0] || 'graph';
}

function isAvailableVizMode(mode) {
  return availableVizModes().includes(mode);
}

function getDefaultVizMode() {
  const mountDefault = String(document.querySelector('[data-keen-visualisation-mount]')?.dataset?.vizDefaultMode || '').trim();
  const fallback = isAvailableVizMode(mountDefault) ? mountDefault : firstAvailableVizMode();
  const m = (defaultVizPref && typeof defaultVizPref.mode === 'string' && isAvailableVizMode(defaultVizPref.mode)) ?
    defaultVizPref.mode :
    fallback;
  return isAvailableVizMode(m) ? m : fallback;
}

function getDefaultSunburstFocus() {
  if (defaultVizPref && defaultVizPref.sunburst_focus && typeof defaultVizPref.sunburst_focus === 'object') {
    // Shallow-copy to avoid mutating the preference object.
    return {...defaultVizPref.sunburst_focus};
  }
  return null;
}

function clearVizQueryOverrides() {
  try {
    const u = new URL(location.href);
    for (const k of ['mode', 'from', 'to', 'days', 'hs', 'hc', 'gs', 'gc', 'minw', 'hstart', 'hend', 'hint']) {
      u.searchParams.delete(k);
    }
    const next = u.pathname + (u.searchParams.toString() ? `?${u.searchParams.toString()}` : '') + (u.hash || '');
    history.replaceState({}, '', next);
  } catch {
    // ignore
  }
}

// Visualisation page initial state:
// - If the user saved a default visualisation state, use it.
// - Otherwise fall back to Graph + last 7 days.
let vizMode = getDefaultVizMode();
if (!isAvailableVizMode(vizMode)) vizMode = firstAvailableVizMode();

// Track the current sunburst zoom (top-level segment). Stored in the same shape as the preference.
let sunburstFocus = (defaultVizPref && defaultVizPref.sunburst_focus && typeof defaultVizPref.sunburst_focus === 'object') ?
  defaultVizPref.sunburst_focus :
  null;

// Captures the data currently being shown (used by Export).
let lastView = {mode: null, payload: null, range: null};

const VIZ_META = {
  graph: {
    title: 'Source ↔ control graph',
    subtitle:
      'Click a source to open its source detail page. Click a control to open clause evidence.',
    caption: 'Edges represent number of mapped events from a source to a control.',
  },
  heatmap: {
    title: 'Heatmap: sources × controls',
    subtitle:
      'Darker cells indicate more mapped events. Click a cell to open matching events. Hover over a heatmap cell to display counters and other info.',
    caption: 'Showing top sources and controls by mapped events.',
  },
  sunburst_source: {
    title: 'Sunburst: by source',
    subtitle:
      'Ring 1 = sources. Ring 2 = controls mapped from that source. Click inner segments to zoom; click outer segments to open filtered events; click the center to zoom out. Hover over segments to see counters and other info.',
    caption: 'Values are mapped-event counts (per source→control edge).',
  },
  sunburst_control: {
    title: 'Sunburst: by control',
    subtitle:
      'Ring 1 = controls. Ring 2 = sources mapped to that control. Click inner segments to zoom; click outer segments to open filtered events; click the center to zoom out. Hover over segments to see counters and other info.',
    caption: 'Values are mapped-event counts (per source→control edge).',
  },
  graph_clause_events: {
    title: 'Source ↔ clause graph',
    subtitle: 'Click a source to open filtered events. Click a clause to open the clause page.',
    caption: 'Edges represent number of mapped events from a source to a clause.',
  },
  heatmap_clause_events: {
    title: 'Heatmap: sources × clauses',
    subtitle: 'Darker cells indicate more mapped events. Click a cell to open matching events.',
    caption: 'Showing top sources and clauses by mapped events.',
  },
  graph_clause_control: {
    title: 'Clause ↔ control graph',
    subtitle: 'Click a clause or control to open its detail page.',
    caption: 'Edges represent clause/control applicability links.',
  },
  histogram: {
    title: 'Histogram: events over time',
    subtitle:
      'Daily event volume (stacked: mapped vs unmapped). Click a stack segment to open matching events. Hover over a stack to display counters and other info.',
    caption: 'Mapped counts are distinct mapped events.',
  },
};

function setVizCaption(mode = vizMode, detail = '') {
  if (!vizCaption) return;
  const meta = VIZ_META[mode] || VIZ_META.graph || {};
  const base = String(meta.caption || '').trim();
  const extra = String(detail || '').trim();
  if (base && extra && extra !== base) {
    vizCaption.textContent = `${base} ${extra}`;
  } else {
    vizCaption.textContent = base || extra;
  }
}

// -------------------------
// Date range (all visualisations)
// -------------------------

const MS_PER_DAY = 24 * 60 * 60 * 1000;

function histStepMs(interval) {
  const it = String(interval || 'day').trim().toLowerCase();
  if (it === 'second') return 1000;
  if (it === 'minute') return 60 * 1000;
  if (it === 'hour') return 60 * 60 * 1000;
  return MS_PER_DAY;
}

function histNextFinerInterval(interval) {
  const it = String(interval || 'day').trim().toLowerCase();
  if (it === 'day') return 'hour';
  if (it === 'hour') return 'minute';
  if (it === 'minute') return 'second';
  return 'second';
}

function histBucketToUtcMs(bucket, interval) {
  const b = String(bucket || '').trim();
  if (!b) return NaN;
  if (String(interval || 'day').trim().toLowerCase() === 'day') {
    // Ensure date-only buckets are interpreted in UTC.
    return Date.parse(`${b}T00:00:00Z`);
  }
  // Backend returns timestamps with a trailing Z.
  return Date.parse(b);
}

function histBucketToIsoDate(bucket, interval) {
  const ms = histBucketToUtcMs(bucket, interval);
  if (!Number.isFinite(ms)) return '';
  return isoDateFromUtcMs(ms);
}

function updateHistogramZoomButtons() {
  const active = Array.isArray(histZoomStack) ? histZoomStack.length : 0;
  if (histBack) histBack.classList.toggle('d-none', active < 1);
  if (histResetZoom) histResetZoom.classList.toggle('d-none', active < 1);
}

function updateHistogramBrushEnabled() {
  // Update button appearance.
  if (histZoomModeBtn) {
    histZoomModeBtn.classList.toggle('active', !!histZoomModeOn);
    // Keep the label short; Bootstrap will apply the "active" styling.
    histZoomModeBtn.textContent = histZoomModeOn ? 'Zoom mode: on' : 'Zoom mode';
  }

  const enabled = (vizMode === 'histogram') && (histZoomModeOn || histBrushShiftActive);
  try {
    if (typeof _setHistogramBrushEnabled === 'function') _setHistogramBrushEnabled(enabled);
  } catch {
    // ignore
  }
}

function isIsoDate(v) {
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v);
}

function utcMsFromIsoDate(v) {
  if (!isIsoDate(v)) return NaN;
  const [y, m, d] = v.split('-').map((x) => Number(x));
  if (!y || !m || !d) return NaN;
  return Date.UTC(y, m - 1, d);
}

function isoDateFromUtcMs(ms) {
  const dt = new Date(ms);
  // Use UTC getters to avoid local timezone shifting.
  const y = dt.getUTCFullYear();
  const m = String(dt.getUTCMonth() + 1).padStart(2, '0');
  const d = String(dt.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function isoTodayUtc() {
  const now = new Date();
  return isoDateFromUtcMs(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

function isoAddDays(iso, deltaDays) {
  const base = utcMsFromIsoDate(iso);
  if (!Number.isFinite(base)) return null;
  return isoDateFromUtcMs(base + deltaDays * MS_PER_DAY);
}

function diffDaysInclusive(startIso, endIso) {
  const a = utcMsFromIsoDate(startIso);
  const b = utcMsFromIsoDate(endIso);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.floor((b - a) / MS_PER_DAY) + 1;
}

function getActiveRange() {
  const from = dateFrom?.value || '';
  const to = dateTo?.value || '';
  if (!isIsoDate(from) || !isIsoDate(to)) return null;
  if (utcMsFromIsoDate(from) > utcMsFromIsoDate(to)) {
    return {from: to, to: from};
  }
  return {from, to};
}

let _suppressRangeSync = false;

function syncHistDaysFromRange() {
  if (!histDays) return;
  const r = getActiveRange();
  if (!r) return;
  const days = diffDaysInclusive(r.from, r.to);
  if (!days) return;
  _suppressRangeSync = true;
  histDays.value = String(days);
  _suppressRangeSync = false;
}

function syncRangeFromHistDays() {
  if (!dateFrom || !dateTo || !histDays) return;
  const end = isIsoDate(dateTo.value) ? dateTo.value : isoTodayUtc();
  const d = Math.max(1, Math.min(3650, Number(histDays.value || 7)));
  const start = isoAddDays(end, -(d - 1));
  if (!start) return;
  _suppressRangeSync = true;
  _setRangeIso(start, end);
  _suppressRangeSync = false;
}

function initDateRangeDefaults() {
  if (!dateFrom || !dateTo) return;

  const dr = (defaultVizPref && typeof defaultVizPref === 'object') ? defaultVizPref.date_range : null;

  // Default window: last 7 days ending today (UTC).
  const fallbackEnd = isoTodayUtc();
  const fallbackStart = isoAddDays(fallbackEnd, -(7 - 1));

  if (dr && typeof dr === 'object') {
    const days = dr.days;
    const start = (dr.start_date || dr.from || '').trim();
    const end = (dr.end_date || dr.to || '').trim();

    if (days != null && String(days).trim() !== '') {
      const d = Math.max(1, Math.min(3650, Number(days)));
      const e = fallbackEnd;
      const s = isoAddDays(e, -(d - 1));
      _setRangeIso(s || fallbackStart || '', e);
      if (histDays) histDays.value = String(d);
    } else if (isIsoDate(start) && isIsoDate(end)) {
      _setRangeIso(start, end);
    } else {
      _setRangeIso(fallbackStart || '', fallbackEnd);
    }
  } else {
    _setRangeIso(fallbackStart || '', fallbackEnd);
  }

  // Keep histogram "Days" aligned to the selected window.
  syncHistDaysFromRange();
}

// Set initial date window for the visualisation page.
initDateRangeDefaults();
applyQueryOverrides();

function showControls() {
  const isHeatmap = vizMode === 'heatmap' || vizMode === 'heatmap_clause_events';
  const isGraph = vizMode === 'graph' || vizMode === 'graph_clause_events' || vizMode === 'graph_clause_control';

  // Bootstrap's display utility classes use `!important`, so toggling inline
  // styles isn't reliable. Use classList instead.
  const setFlex = (el, on) => {
    if (!el) return;
    if (on) {
      el.classList.remove('d-none');
      el.classList.add('d-flex');
    } else {
      el.classList.add('d-none');
      el.classList.remove('d-flex');
    }
  };
  setFlex(controlsHeatmap, isHeatmap);
  setFlex(controlsGraph, isGraph);
  // "Days" is a convenient shortcut for all visualisations.
  setFlex(controlsHistogram, true);
}

function setViz(mode, opts = {}) {
  vizMode = mode;
  activateGraphDataForMode(vizMode);
  renderTopLists();
  if (vizSelect) vizSelect.value = vizMode;

  const meta = VIZ_META[vizMode] || VIZ_META.graph;
  if (vizTitle) vizTitle.textContent = meta.title;
  if (vizSubtitle) vizSubtitle.textContent = meta.subtitle;
  setVizCaption(vizMode);

  const labels = vizRelationshipLabels(vizMode);
  if (distributionTitle) distributionTitle.textContent = labels.distributionTitle;
  if (distributionSubtitle) distributionSubtitle.textContent = labels.distributionSubtitle;
  if (topSourcesTitle) topSourcesTitle.textContent = `Top ${labels.leftPlural}`;
  if (topControlsTitle) topControlsTitle.textContent = `Top ${labels.rightPlural}`;
  if (topSourcesColHeader) topSourcesColHeader.textContent = labels.leftSingular;
  if (topSourcesCountHeader) topSourcesCountHeader.textContent = labels.leftMetric;
  if (topControlsColHeader) topControlsColHeader.textContent = labels.rightSingular;
  if (topControlsCountHeader) topControlsCountHeader.textContent = labels.rightMetric;
  if (coverageGapsTitle) coverageGapsTitle.textContent = labels.gapsTitle;
  if (coverageGapsSubtitle) coverageGapsSubtitle.innerHTML = labels.gapsSubtitle;
  rebuildCoverageGaps();
  renderGaps();

  // Keep histogram "Days" in sync with the active date window.
  if (vizMode === 'histogram') {
    syncHistDaysFromRange();
  }

  // Enable/disable the histogram brush based on mode + user intent.
  updateHistogramBrushEnabled();

  showControls();
  if (!opts?.skipDraw) {
    if (vizMode !== 'histogram' && !graphData) {
      showTableLoading(topSourcesEl, 2, `Loading ${vizRelationshipLabels(vizMode).leftPlural}…`);
      showTableLoading(topControlsEl, 2, `Loading ${vizRelationshipLabels(vizMode).rightPlural}…`);
      showVizLoading('Loading visualisation data…');
      ensureGraphDatasetForMode(vizMode)
          .then(() => drawActive())
          .catch((err) => toast(status, `Failed to load visualisation data: ${String(err)}`, 'danger'));
    } else {
      drawActive();
    }
  }
}

function renderGaps() {
  if (!controlStats && !controlStatsPromise) {
    if (gapsEl) gapsEl.textContent = vizRelationshipLabels(vizMode).gapsIntro;
    return;
  }
  const q = (gapQ?.value || '').trim().toLowerCase();
  const view = gaps.filter((c) => {
    if (!q) return true;
    return `${c.ref} ${c.title || ''}`.toLowerCase().includes(q);
  });

  if (!gapsEl) return;

  if (view.length === 0) {
    gapsEl.textContent = q ? 'No matches.' : 'None 🎉';
    return;
  }

  gapsEl.innerHTML = view
      .slice(0, 200)
      .map((c) => {
        return `<div class="gap-item">
      <a href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework)}" class="link-dark text-decoration-none">
        <span class="badge badge-chip me-1">${esc(c.ref)}</span>
        <span class="small-muted">${esc(c.title || '')}</span>
      </a>
    </div>`;
      })
      .join('');
}

function renderTopLists() {
  if (!graphData || !topSourcesEl || !topControlsEl) return;

  const labels = vizRelationshipLabels(vizMode);
  const nodes = graphData.nodes || [];
  const sourceNodes = nodes.filter((n) => n.type === 'source');
  const controlNodes = nodes.filter((n) => n.type === 'control');

  // Prefer backend-provided distinct mapped event totals (avoids double-counting).
  const hasMappedTotals = nodes.some((n) => typeof n.mapped_events === 'number');

  const topSources = (hasMappedTotals ?
    sourceNodes.map((n) => ({
      node: n,
      total: Number(n.mapped_events || 0),
    })) :
    [])
      .filter((x) => x.total > 0)
      .sort((a, b) => b.total - a.total)
      .slice(0, 12);

  const topControls = (hasMappedTotals ?
    controlNodes.map((n) => ({
      node: n,
      total: Number(n.mapped_events || 0),
    })) :
    [])
      .filter((x) => x.total > 0)
      .sort((a, b) => b.total - a.total)
      .slice(0, 12);

  // Fallback (older API): sum link weights (may overcount when events map to multiple controls)
  if (!hasMappedTotals) {
    const nodeById = new Map(nodes.map((n) => [n.id, n]));
    const srcTotals = new Map();
    const ctrlTotals = new Map();

    for (const l of graphData.links || []) {
      const v = Number(l.value || 0);
      if (!v) continue;
      srcTotals.set(l.source, (srcTotals.get(l.source) || 0) + v);
      ctrlTotals.set(l.target, (ctrlTotals.get(l.target) || 0) + v);
    }

    topSources.length = 0;
    topControls.length = 0;

    topSources.push(
        ...Array.from(srcTotals.entries())
            .map(([id, total]) => ({node: nodeById.get(id), total}))
            .filter((x) => x.node)
            .sort((a, b) => b.total - a.total)
            .slice(0, 12)
    );

    topControls.push(
        ...Array.from(ctrlTotals.entries())
            .map(([id, total]) => ({node: nodeById.get(id), total}))
            .filter((x) => x.node)
            .sort((a, b) => b.total - a.total)
            .slice(0, 12)
    );
  }

  topSourcesEl.innerHTML = topSources.length ? topSources
      .map((s) => {
        const label = s.node?.label || s.node?.id || '';
        const key = s.node?.source || label;
        const href = (s.node?.node_kind === 'clause')
          ? withFramework(`/clause.html?id=${encodeURIComponent(s.node?.clause_id || '')}`, framework)
          : buildEventsHref(key, '');
        return `<tr>
      <td><a class="link-dark text-decoration-none" href="${href}">${esc(
    label
)}</a></td>
      <td class="text-end" data-sort="${s.total}">${esc(
    String(s.total)
)}</td>
    </tr>`;
      })
      .join('') : `<tr><td colspan="2" class="small-muted p-3">${esc(labels.emptyLeft)}</td></tr>`;

  topControlsEl.innerHTML = topControls.length ? topControls
      .map((c) => {
        const ref = c.node?.label || c.node?.id || '';
        const cid = c.node?.control_id || '';
        const href = (c.node?.node_kind === 'clause')
          ? withFramework(`/clause.html?id=${encodeURIComponent(c.node?.clause_id || cid || '')}`, framework)
          : (cid ? withFramework(`/control.html?id=${encodeURIComponent(cid)}`, framework) : '#');
        return `<tr>
      <td><a class="link-dark text-decoration-none" href="${href}">${esc(
    ref
)}</a></td>
      <td class="text-end" data-sort="${c.total}">${esc(
    String(c.total)
)}</td>
    </tr>`;
      })
      .join('') : `<tr><td colspan="2" class="small-muted p-3">${esc(labels.emptyRight)}</td></tr>`;

  enableTableSorting(document);
}

function buildEventsHref(sourceKey, controlIdOrRef, rangeOverride = null, opts = {}) {
  const u = new URL('/events.html', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if (sourceKey) u.searchParams.set('source', sourceKey);
  if (controlIdOrRef) u.searchParams.set('control', controlIdOrRef);
  if (opts?.clause) u.searchParams.set('clause', opts.clause);
  const r = rangeOverride || getActiveRange();

  // Support both day-granularity ranges ({from,to}) and precise time windows ({start_ts,end_ts}).
  // When a time window is supplied, we ALSO include derived start_date/end_date so the Events UI
  // can keep its existing date pickers populated.
  if (r && typeof r === 'object') {
    const sts = String(r.start_ts || '').trim();
    const ets = String(r.end_ts || '').trim();
    if (sts && ets) {
      u.searchParams.set('start_ts', sts);
      u.searchParams.set('end_ts', ets);

      // Add date-range hints for the Events page (end is exclusive).
      const a = Date.parse(sts);
      const b = Date.parse(ets);
      if (Number.isFinite(a) && Number.isFinite(b) && b > a) {
        const d0 = isoDateFromUtcMs(a);
        const d1 = isoDateFromUtcMs(b - 1);
        if (d0) u.searchParams.set('start_date', String(r.from || d0));
        if (d1) u.searchParams.set('end_date', String(r.to || d1));
      }
      return u.pathname + u.search;
    }

    // Default day-range path.
    if (r.from && r.to) {
      u.searchParams.set('start_date', r.from);
      u.searchParams.set('end_date', r.to);
    }
  }
  return u.pathname + u.search;
}

function openEventsForLink(sourceKey, controlIdOrRef, ev = null, rangeOverride = null, opts = {}) {
  const href = buildEventsHref(sourceKey, controlIdOrRef, rangeOverride, opts);
  // Support cmd/ctrl-click semantics for D3 click handlers.
  if (ev && (ev.metaKey || ev.ctrlKey)) {
    window.open(href, '_blank', 'noopener');
    return;
  }
  location.href = href;
}

function openControl(controlId) {
  if (!controlId) return;
  location.href = withFramework(`/control.html?id=${encodeURIComponent(controlId)}`, framework);
}

function openClause(clauseId) {
  if (!clauseId) return;
  location.href = withFramework(`/clause.html?id=${encodeURIComponent(clauseId)}`, framework);
}

function openGraphNode(node, ev = null) {
  if (!node) return;
  if (node.node_kind === 'clause' || node.type === 'clause') {
    const href = withFramework(`/clause.html?id=${encodeURIComponent(node.clause_id || node.control_id || '')}`, framework);
    if (ev && (ev.metaKey || ev.ctrlKey)) window.open(href, '_blank', 'noopener');
    else location.href = href;
    return;
  }
  if (node.node_kind === 'control' || node.type === 'control') {
    const cid = node.control_id || '';
    if (cid) openControl(cid);
    return;
  }
  if (node.type === 'source') {
    const src = node.source || '';
    const href = withFramework(`/source.html?source=${encodeURIComponent(src)}`, framework);
    if (ev && (ev.metaKey || ev.ctrlKey)) window.open(href, '_blank', 'noopener');
    else location.href = href;
  }
}

function graphKind() {
  return String(graphData?.graph_kind || 'source_control_events');
}

function datasetKeyForMode(mode = vizMode) {
  if (mode === 'graph_clause_events' || mode === 'heatmap_clause_events') return 'clause_source';
  if (mode === 'graph_clause_control') return 'clause_control';
  return 'control_source';
}


function vizRelationshipLabels(mode = vizMode) {
  const key = datasetKeyForMode(mode);
  if (key === 'clause_control') {
    return {
      leftPlural: 'clauses',
      leftSingular: 'Clause',
      rightPlural: 'controls',
      rightSingular: 'Control',
      distributionTitle: 'Clause/control distribution',
      distributionSubtitle: 'Top clauses by linked controls and top controls by linked clauses.',
      leftMetric: 'Mapped controls',
      rightMetric: 'Mapped clauses',
      emptyLeft: 'No mapped clause/control relationships.',
      emptyRight: 'No mapped clause/control relationships.',
      gapsTitle: 'Controls without events for this date range',
      gapsSubtitle: '<strong>For this date range</strong>, no events occurred for the following controls.',
      gapsIntro: 'Open this panel to load controls without events for the selected date range.',
    };
  }
  if (key === 'clause_source') {
    return {
      leftPlural: 'sources',
      leftSingular: 'Source',
      rightPlural: 'clauses',
      rightSingular: 'Clause',
      distributionTitle: 'Evidence distribution',
      distributionSubtitle: 'Top sources and clauses by mapped events (selected date range).',
      leftMetric: 'Mapped events',
      rightMetric: 'Mapped events',
      emptyLeft: 'No mapped source evidence in this date range.',
      emptyRight: 'No mapped clause evidence in this date range.',
      gapsTitle: 'Controls without events for this date range',
      gapsSubtitle: '<strong>For this date range</strong>, no events occurred for the following controls.',
      gapsIntro: 'Open this panel to load controls without events for the selected date range.',
    };
  }
  return {
    leftPlural: 'sources',
    leftSingular: 'Source',
    rightPlural: 'controls',
    rightSingular: 'Control',
    distributionTitle: 'Evidence distribution',
    distributionSubtitle: 'Top sources and controls by mapped events (selected date range).',
    leftMetric: 'Mapped events',
    rightMetric: 'Mapped events',
    emptyLeft: 'No mapped source evidence in this date range.',
    emptyRight: 'No mapped control evidence in this date range.',
    gapsTitle: 'Controls without events for this date range',
    gapsSubtitle: '<strong>For this date range</strong>, no events occurred for the following controls.',
    gapsIntro: 'Open this panel to load controls without events for the selected date range.',
  };
}

function rebuildCoverageGaps() {
  const items = (controlStats && controlStats.items) ? controlStats.items : [];
  let view = items
      .filter((c) => c.in_scope)
      .filter((c) => Number(c.evidence_count || 0) === 0);

  gaps = view.map((c) => ({id: c.id, ref: c.ref, title: c.title}));
}

function activateGraphDataForMode(mode = vizMode) {
  const key = datasetKeyForMode(mode);
  graphData = graphDatasets[key] || null;
}

function buildGraphDatasetUrl(key, range = getActiveRange()) {
  const pathByKey = {
    control_source: '/api/v1/graph/control_source',
    clause_source: '/api/v1/graph/clause_source',
    clause_control: '/api/v1/graph/clause_control',
  };
  const u = new URL(pathByKey[key] || pathByKey.control_source, location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if (range && key !== 'clause_control') {
    u.searchParams.set('start_date', range.from);
    u.searchParams.set('end_date', range.to);
  }
  if (scopedSource && key !== 'clause_control') {
    u.searchParams.set('source', scopedSource);
  }
  return u.pathname + u.search;
}

async function loadGraphDataset(key, opts = {}) {
  const k = key || 'control_source';
  if (!graphDatasets[k] || opts.force) {
    if (!graphDatasetPromises[k] || opts.force) {
      graphDatasetPromises[k] = apiGet(buildGraphDatasetUrl(k))
          .then((payload) => {
            graphDatasets[k] = payload;
            return payload;
          })
          .finally(() => {
            graphDatasetPromises[k] = null;
          });
    }
    return graphDatasetPromises[k];
  }
  return graphDatasets[k];
}

async function ensureGraphDatasetForMode(mode = vizMode, opts = {}) {
  const key = datasetKeyForMode(mode);
  const payload = await loadGraphDataset(key, opts);
  if (mode === vizMode) {
    graphData = payload;
    renderTopLists();
    rebuildCoverageGaps();
    renderGaps();
  }
  return payload;
}

function buildControlStatsUrl(range = getActiveRange()) {
  const u = new URL('/api/v1/stats/controls', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  u.searchParams.set('limit', '5000');
  if (range) {
    u.searchParams.set('start_date', range.from);
    u.searchParams.set('end_date', range.to);
  }
  return u.pathname + u.search;
}

async function loadControlStatsForGaps(opts = {}) {
  if (controlStats && !opts.force) return controlStats;
  if (!controlStatsPromise || opts.force) {
    if (gapsEl) showInlineLoading(gapsEl, 'Loading controls…');
    controlStatsPromise = apiGet(buildControlStatsUrl())
        .then((payload) => {
          controlStats = payload;
          rebuildCoverageGaps();
          renderGaps();
          return payload;
        })
        .catch((err) => {
          if (gapsEl) gapsEl.textContent = 'Failed to load controls.';
          throw err;
        })
        .finally(() => {
          controlStatsPromise = null;
        });
  }
  return controlStatsPromise;
}

function coverageGapsOpen() {
  return !!coverageGapsPanel?.classList?.contains('show');
}

function clearRangeSensitiveCaches() {
  // The source/control and source/clause graphs are date-window sensitive.
  // Clause/control is a static relationship graph for the selected framework,
  // so keep it warm when only the range changes.
  graphDatasets = {
    control_source: null,
    clause_source: null,
    clause_control: graphDatasets.clause_control || null,
  };
  graphDatasetPromises = {control_source: null, clause_source: null, clause_control: null};
  graphData = null;
  controlStats = null;
  controlStatsPromise = null;
  gaps = [];
}

function isRelationshipGraph() {
  return graphKind() === 'clause_control_relationship';
}
function clampInt(x, min, max) {
  const n = Number(x);
  if (!Number.isFinite(n)) return null;
  const v = Math.floor(n);
  if (v < min) return min;
  if (v > max) return max;
  return v;
}

function getGraphTopSources() {
  // 0/blank means "all".
  const v = clampInt(graphTopSources?.value, 0, 500);
  return v && v > 0 ? v : null;
}

function getGraphTopControls() {
  const v = clampInt(graphTopControls?.value, 0, 500);
  return v && v > 0 ? v : null;
}

function getGraphMinEdge() {
  const v = clampInt(graphMinEdge?.value, 1, 1000000);
  return v || 1;
}

function downloadBlob(filename, blob) {
  const a = document.createElement('a');
  const url = URL.createObjectURL(blob);
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function csvEscape(v) {
  if (v == null) return '';
  const s = String(v);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function toCsv(rows) {
  const r = rows || [];
  if (r.length === 0) return '';
  const keys = Array.from(
      r.reduce((acc, row) => {
        Object.keys(row || {}).forEach((k) => acc.add(k));
        return acc;
      }, new Set())
  );
  const header = keys.map(csvEscape).join(',');
  const body = r
      .map((row) => keys.map((k) => csvEscape(row?.[k])).join(','))
      .join('\n');
  return header + '\n' + body + '\n';
}

function downloadCsv(filename, rows) {
  const csv = toCsv(rows);
  downloadBlob(filename, new Blob([csv], {type: 'text/csv;charset=utf-8'}));
}

function downloadJson(filename, obj) {
  const json = JSON.stringify(obj, null, 2) + '\n';
  downloadBlob(filename, new Blob([json], {type: 'application/json'}));
}

function downloadCurrentSvg(filename) {
  const el = document.getElementById('viz');
  if (!el) return;
  const clone = el.cloneNode(true);
  // Ensure xmlns so the downloaded SVG opens cleanly.
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  const svgText = new XMLSerializer().serializeToString(clone);
  downloadBlob(filename, new Blob([svgText], {type: 'image/svg+xml;charset=utf-8'}));
}

function buildShareUrl() {
  const u = new URL(location.href);
  u.searchParams.set('mode', vizMode);

  const r = getActiveRange();
  if (r) {
    u.searchParams.set('from', r.from);
    u.searchParams.set('to', r.to);
  } else {
    u.searchParams.delete('from');
    u.searchParams.delete('to');
  }

  if (histDays?.value) u.searchParams.set('days', String(histDays.value));
  if (heatmapTopSources?.value) u.searchParams.set('hs', String(heatmapTopSources.value));
  if (heatmapTopControls?.value) u.searchParams.set('hc', String(heatmapTopControls.value));
  if (graphTopSources?.value) u.searchParams.set('gs', String(graphTopSources.value));
  if (graphTopControls?.value) u.searchParams.set('gc', String(graphTopControls.value));
  if (graphMinEdge?.value) u.searchParams.set('minw', String(graphMinEdge.value));

  // Histogram zoom window (precise timestamps).
  // We store only the current zoom level (top of the stack) in the URL.
  // This keeps share links short, while still restoring the zoomed view.
  const hz = (vizMode === 'histogram' && Array.isArray(histZoomStack) && histZoomStack.length) ?
    histZoomStack[histZoomStack.length - 1] :
    null;
  if (hz && hz.start_ts && hz.end_ts) {
    u.searchParams.set('hstart', String(hz.start_ts));
    u.searchParams.set('hend', String(hz.end_ts));
    if (hz.interval) u.searchParams.set('hint', String(hz.interval));
  } else {
    u.searchParams.delete('hstart');
    u.searchParams.delete('hend');
    u.searchParams.delete('hint');
  }

  return u.toString();
}

function syncAddressBarToShareState() {
  // Keep the address bar aligned with what Copy link would produce,
  // so refresh/navigation preserves the current view (including histogram zoom).
  try {
    const u = new URL(buildShareUrl());
    const next = u.pathname + u.search + (u.hash || '');
    history.replaceState({}, '', next);
  } catch {
    // ignore
  }
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

function applyQueryOverrides() {
  const qs = new URLSearchParams(location.search || '');
  const mode = (qs.get('mode') || '').trim();
  if (mode && isAvailableVizMode(mode)) vizMode = mode;

  const from = (qs.get('from') || '').trim();
  const to = (qs.get('to') || '').trim();
  if (from && to && dateFrom && dateTo) {
    _setRangeIso(from, to);
    normalizeRangeInputs();
    syncHistDaysFromRange();
  }

  // Histogram zoom window (precise timestamps).
  // Only applied when the mode is histogram.
  if (vizMode === 'histogram') {
    const hstart = String(qs.get('hstart') || '').trim();
    const hend = String(qs.get('hend') || '').trim();
    const hint = String(qs.get('hint') || '').trim();
    const okStart = hstart && Number.isFinite(Date.parse(hstart));
    const okEnd = hend && Number.isFinite(Date.parse(hend));
    if (okStart && okEnd && Date.parse(hend) > Date.parse(hstart)) {
      const it = String(hint || 'day').toLowerCase();
      const interval = (it === 'day' || it === 'hour' || it === 'minute' || it === 'second') ? it : 'day';
      histZoomStack = [{
        start_ts: hstart,
        end_ts: hend,
        interval,
      }];
      updateHistogramZoomButtons();
    }
  }

  const hs = (qs.get('hs') || '').trim();
  const hc = (qs.get('hc') || '').trim();
  if (hs && heatmapTopSources) heatmapTopSources.value = hs;
  if (hc && heatmapTopControls) heatmapTopControls.value = hc;

  const gs = (qs.get('gs') || '').trim();
  const gc = (qs.get('gc') || '').trim();
  const minw = (qs.get('minw') || '').trim();
  if (gs && graphTopSources) graphTopSources.value = gs;
  if (gc && graphTopControls) graphTopControls.value = gc;
  if (minw && graphMinEdge) graphMinEdge.value = minw;

  const days = (qs.get('days') || '').trim();
  if (days && histDays) histDays.value = days;
}


function clearSvg() {
  svg.selectAll('*').remove();
}

function showVizLoading(message = 'Loading visualisation data…') {
  clearSvg();
  setSunburstOtherPanel('');
  const width = getWrapWidth();
  const height = 320;
  sizeSvg(width, height);
  svg.append('text')
      .attr('x', width / 2)
      .attr('y', height / 2)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('class', 'small-muted')
      .text(message);
}

function setSunburstOtherPanel(html) {
  if (!sunburstOtherPanel) return;
  const h = String(html || '');
  sunburstOtherPanel.innerHTML = h;
  sunburstOtherPanel.style.display = h.trim() ? '' : 'none';
}

function sizeSvg(width, height) {
  svg
      .attr('viewBox', `0 0 ${width} ${height}`)
      .attr('width', width)
      .attr('height', height)
      .style('height', `${height}px`);
}

function getWrapWidth(min = 680) {
  return Math.max(min, wrap?.clientWidth || 900);
}

function truncateMiddleText(value, maxChars = 64) {
  const s = String(value || '').trim();
  const n = Number(maxChars || 0);
  if (!n || s.length <= n) return s;
  if (n <= 4) return s.slice(0, n);
  const left = Math.ceil((n - 1) / 2);
  const right = Math.floor((n - 1) / 2);
  return `${s.slice(0, left)}…${s.slice(s.length - right)}`;
}

function splitLongToken(token, maxChars) {
  const out = [];
  let s = String(token || '');
  const n = Math.max(4, Number(maxChars || 12));
  while (s.length > n) {
    out.push(s.slice(0, n));
    s = s.slice(n);
  }
  if (s) out.push(s);
  return out;
}

function wrapLabelLines(value, maxCharsPerLine, maxLines = 3) {
  const raw = String(value || '').replace(/\s+/g, ' ').trim();
  const perLine = Math.max(4, Number(maxCharsPerLine || 18));
  const lineLimit = Math.max(1, Number(maxLines || 1));
  if (!raw) return [];

  const tokens = raw.split(' ').flatMap((w) => w.length > perLine ? splitLongToken(w, perLine) : [w]);
  const lines = [];
  let line = '';
  for (const token of tokens) {
    const next = line ? `${line} ${token}` : token;
    if (next.length <= perLine) {
      line = next;
      continue;
    }
    if (line) lines.push(line);
    line = token;
    if (lines.length >= lineLimit) break;
  }
  if (line && lines.length < lineLimit) lines.push(line);

  if (tokens.join(' ').length > lines.join(' ').length && lines.length) {
    lines[lines.length - 1] = truncateMiddleText(lines[lines.length - 1], Math.max(4, perLine - 1));
    if (!lines[lines.length - 1].endsWith('…')) lines[lines.length - 1] = `${lines[lines.length - 1]}…`;
  }
  return lines;
}

function renderWrappedNodeLabel(textSel, value, opts = {}) {
  if (!textSel) return;
  const width = Number(opts.width || 160);
  const height = Number(opts.height || 28);
  const x = Number(opts.x ?? width / 2);
  const y = Number(opts.y ?? height / 2);
  const linePx = Number(opts.linePx || 14);
  const charPx = Number(opts.charPx || 7);
  const maxLines = Math.max(1, Math.min(Number(opts.maxLines || 3), Math.floor(Math.max(1, height - 8) / linePx) || 1));
  const maxChars = Math.max(4, Math.floor(Math.max(20, width - 16) / charPx));
  const lines = wrapLabelLines(value, maxChars, maxLines);
  const totalHeight = Math.max(linePx, lines.length * linePx);
  const startY = y - (totalHeight / 2) + (linePx / 2);

  textSel.text(null);
  lines.forEach((line, idx) => {
    textSel
        .append('tspan')
        .attr('x', x)
        .attr('y', startY + (idx * linePx))
        .text(line);
  });
}

function drawGraph(minW, topS = null, topC = null) {
  clearSvg();
  if (!graphData) return;

  const nodeById = new Map((graphData.nodes || []).map((n) => [n.id, n]));
  const linksAll = (graphData.links || [])
      .filter((l) => Number(l.value || 0) >= minW)
      .filter((l) => nodeById.has(l.source) && nodeById.has(l.target))
      .map((l) => ({
        ...l,
        value: Number(l.value || 0),
        sourceNode: nodeById.get(l.source),
        targetNode: nodeById.get(l.target),
      }));

  // Connected nodes only
  const connected = new Set();
  for (const l of linksAll) {
    connected.add(l.source);
    connected.add(l.target);
  }

  const nodes = (graphData.nodes || []).filter((n) => connected.has(n.id));
  let sources = nodes.filter((n) => n.type === 'source');
  let controls = nodes.filter((n) => n.type === 'control');

  // Totals (for captions). For ordering, prefer backend distinct totals if present.
  const totalById = new Map();
  for (const l of linksAll) {
    totalById.set(l.source, (totalById.get(l.source) || 0) + l.value);
    totalById.set(l.target, (totalById.get(l.target) || 0) + l.value);
  }

  const orderVal = (n) => {
    const m = Number(n?.mapped_events || 0);
    if (m) return m; // distinct mapped events (preferred)
    return totalById.get(n.id) || 0;
  };

  sources.sort((a, b) => orderVal(b) - orderVal(a));
  controls.sort((a, b) => orderVal(b) - orderVal(a));

  // Apply optional "top N" caps for readability on large datasets.
  if (topS && Number.isFinite(topS)) sources = sources.slice(0, topS);
  if (topC && Number.isFinite(topC)) controls = controls.slice(0, topC);

  // Filter edges to the selected node sets.
  const allow = new Set([...sources.map((n) => n.id), ...controls.map((n) => n.id)]);
  const links = linksAll.filter((l) => allow.has(l.source) && allow.has(l.target));

  // Remove any nodes that became disconnected after the top-N filter.
  const connected2 = new Set();
  for (const l of links) {
    connected2.add(l.source);
    connected2.add(l.target);
  }
  sources = sources.filter((n) => connected2.has(n.id));
  controls = controls.filter((n) => connected2.has(n.id));

  if (links.length === 0) {
    const width = getWrapWidth(680);
    sizeSvg(width, 280);
    svg
        .append('text')
        .attr('x', 14)
        .attr('y', 24)
        .attr('class', 'small-muted')
        .text('No edges match the current Graph filters.');
    setVizCaption(vizMode, 'No edges match the current Graph filters.');
    lastView = {mode: 'graph', payload: {edges: []}, range: getActiveRange()};
    return;
  }

  const width = getWrapWidth(680);
  const graphLabels = vizRelationshipLabels(vizMode);
  const linkMetricLabel = graphKind() === 'clause_control_relationship' ? 'Link count' : 'Mapped events';

  // Make the chart comfortably tall. We use a row-based height heuristic so
  // the diagram expands when there are many sources/controls.
  const rowPx = 30;
  const rows = Math.max(sources.length, controls.length, 10);
  const height = Math.max(820, rows * rowPx + 140);

  const margin = {top: 24, right: 18, bottom: 24, left: 18};
  const sourceNodeW = 170;
  const controlNodeW = 170;

  const xL = margin.left;
  const xR = width - margin.right - controlNodeW;

  const yS = d3
      .scaleBand()
      .domain(sources.map((s) => s.id))
      .range([margin.top, height - margin.bottom])
      .padding(0.2);

  const yC = d3
      .scaleBand()
      .domain(controls.map((c) => c.id))
      .range([margin.top, height - margin.bottom])
      .padding(0.2);

  sizeSvg(width, height);

  // Build per-source colour styling (respecting user overrides).
  // Note: SVG presentation attributes are overridden by CSS rules in styles.css,
  // so we use inline styles (.style) to ensure the per-source colours show.
  const defs = svg.append('defs');

  const _safeBaseColor = (hex) => {
    const c = (typeof hex === 'string') ? d3.color(hex.trim()) : null;
    return c || d3.rgb(124, 58, 237);
  };

  const _idSafe = (s) => String(s || '').replace(/[^a-zA-Z0-9_-]/g, '_');

  const sourceStyleById = new Map();
  const sourceGradById = new Map();
  for (const s of sources) {
    const base = _safeBaseColor(s.color);
    const lum = _relativeLuminance(base);

    // Dark tile fill with white labels (requested): keep the source hue but add depth.
    // We darken more for very light colours so white text stays readable.
    let darken = 0.28;
    if (lum > 0.78) darken = 0.52;
    else if (lum > 0.62) darken = 0.40;
    else if (lum < 0.25) darken = 0.18;

    const fill = _mixRgb(base, d3.rgb(0, 0, 0), darken).formatHex();
    // Border: a subtle lighter rim, still tied to the source hue.
    const stroke = _mixRgb(base, d3.rgb(255, 255, 255), 0.16).formatHex();

    sourceStyleById.set(s.id, {fill, stroke, base: base.formatHex()});

    // Gradient for edges from this source → controls.
    const gradId = `srcgrad_${_idSafe(s.id)}`;
    const darker = _mixRgb(base, d3.rgb(0, 0, 0), 0.30);
    const lighter = _mixRgb(base, d3.rgb(255, 255, 255), 0.52);

    const g = defs
        .append('linearGradient')
        .attr('id', gradId)
        .attr('x1', '0%')
        .attr('y1', '0%')
        .attr('x2', '100%')
        .attr('y2', '0%');

    g.append('stop')
        .attr('offset', '0%')
        .attr('stop-color', darker.formatHex())
        .attr('stop-opacity', 0.62);
    g.append('stop')
        .attr('offset', '100%')
        .attr('stop-color', lighter.formatHex())
        .attr('stop-opacity', 0.52);

    sourceGradById.set(s.id, `url(#${gradId})`);
  }

  const vals = links.map((l) => l.value);
  const vMin = d3.min(vals) || 1;
  const vMax = d3.max(vals) || 1;
  const wScale = d3.scaleSqrt().domain([vMin, vMax]).range([1.25, 10]);

  // Links
  const gLinks = svg.append('g').attr('class', 'links');
  const link = gLinks
      .selectAll('path')
      .data(links)
      .enter()
      .append('path')
      .attr('class', 'link')
      .attr('fill', 'none')
      .attr('stroke-width', (d) => wScale(d.value))
      .style('stroke', (d) => sourceGradById.get(d.source) || accent(0.30))
      .style('stroke-linecap', 'round')
      .attr('d', (d) => {
        const y1 = (yS(d.source) ?? 0) + yS.bandwidth() / 2;
        const y2 = (yC(d.target) ?? 0) + yC.bandwidth() / 2;
        const x1 = xL + sourceNodeW;
        const x2 = xR;
        const xm = (x1 + x2) / 2;
        return `M ${x1} ${y1} C ${xm} ${y1}, ${xm} ${y2}, ${x2} ${y2}`;
      })
      .on('click', (ev, d) => {
        ev.preventDefault();
        if (d.link_kind === 'relationship' || isRelationshipGraph()) {
          openGraphNode(d.targetNode || d.sourceNode, ev);
          return;
        }
        const srcKey = d.sourceNode?.source || '';
        const target = d.targetNode || {};
        if (target.node_kind === 'clause') {
          openEventsForLink(srcKey, '', ev, null, {clause: target.label || target.ref || target.clause_id || ''});
          return;
        }
        // Use human-friendly control refs (e.g. A.8.6) for the Events page filter.
        const ctrlRef = target.label || target.control_ref || '';
        openEventsForLink(srcKey, ctrlRef, ev);
      });

  link
      .append('title')
      .text((d) => {
        const s = d.sourceNode?.label || d.source;
        const c = d.targetNode?.label || d.target;
        return `${s} → ${c}\n${linkMetricLabel}: ${d.value}`;
      });

  // Nodes
  const gNodes = svg.append('g').attr('class', 'nodes');

  const srcG = gNodes
      .selectAll('g.source')
      .data(sources)
      .enter()
      .append('g')
      .attr('class', 'node node-source')
      .attr('transform', (d) => `translate(${xL}, ${yS(d.id) ?? 0})`)
      .on('click', (ev, d) => {
        ev.preventDefault();
        openGraphNode(d, ev);
      });

  srcG
      .append('rect')
      .attr('rx', 8)
      .attr('ry', 8)
      .attr('width', sourceNodeW)
      .attr('height', () => yS.bandwidth());

  // Apply per-source fill/stroke colours to the left nodes.
  srcG.selectAll('rect')
      .style('fill', (d) => sourceStyleById.get(d.id)?.fill || accent(0.12))
      .style('stroke', (d) => sourceStyleById.get(d.id)?.stroke || accent(0.35))
      .style('stroke-width', 1);

  srcG
    .append('text')
    .attr('x', sourceNodeW / 2)
    .attr('y', () => yS.bandwidth() / 2)
    .attr('text-anchor', 'middle')
    .each(function(d) {
      renderWrappedNodeLabel(d3.select(this), d.label, {
        width: sourceNodeW,
        height: yS.bandwidth(),
        x: sourceNodeW / 2,
        y: yS.bandwidth() / 2,
        linePx: 17,
        charPx: 7.8,
        maxLines: 3,
      });
    });

  srcG
      .append('title')
      .text(
          (d) =>
            `${d.label}\n${graphLabels.leftMetric}: ${
              Number(d.mapped_events || 0) || totalById.get(d.id) || 0
            }`
      );

  const ctrlG = gNodes
      .selectAll('g.control')
      .data(controls)
      .enter()
      .append('g')
      .attr('class', 'node node-control')
      .attr('transform', (d) => `translate(${xR}, ${yC(d.id) ?? 0})`)
      .on('click', (ev, d) => {
        ev.preventDefault();
        openGraphNode(d, ev);
      });

  ctrlG
      .append('rect')
      .attr('rx', 8)
      .attr('ry', 8)
      .attr('width', controlNodeW)
      .attr('height', () => yC.bandwidth());

  // Controls don't have per-control colours; render them as dark neutral tiles
  // so the graph reads as a two-column matrix with white labels.
  ctrlG.selectAll('rect')
      .style('fill', 'rgba(31,31,42,0.82)')
      .style('stroke', 'rgba(255,255,255,0.18)')
      .style('stroke-width', 1);

  ctrlG
      .append('text')
      .attr('x', 10)
      .attr('y', () => yC.bandwidth() / 2)
      .attr('text-anchor', 'start')
      .each(function(d) {
        renderWrappedNodeLabel(d3.select(this), d.label, {
          width: controlNodeW - 18,
          height: yC.bandwidth(),
          x: 10,
          y: yC.bandwidth() / 2,
          linePx: 13,
          charPx: 6.2,
          maxLines: 2,
        });
      });

  ctrlG
      .append('title')
      .text(
          (d) =>
            `${d.label} — ${d.title || ''}\n${graphLabels.rightMetric}: ${
              Number(d.mapped_events || 0) || totalById.get(d.id) || 0
            }`
      );

  // A tiny caption with counts
  svg
      .append('text')
      .attr('x', margin.left)
      .attr('y', 14)
      .attr('class', 'small-muted')
      .text(`Edges shown: ${links.length}`);

  // Update caption + export payload.
  {
    const leftLabel = graphKind() === 'clause_control_relationship' ? 'clauses' : 'sources';
    setVizCaption(vizMode, `Showing ${sources.length} ${leftLabel} × ${controls.length} controls (edges ≥ ${minW}).`);
  }

  lastView = {
    mode: 'graph',
    range: getActiveRange(),
    payload: {
      edges: links.map((l) => ({
        source: l.sourceNode?.source || '',
        source_label: l.sourceNode?.label || l.sourceNode?.source || '',
        control_id: l.targetNode?.control_id || '',
        control_ref: l.targetNode?.label || '',
        control_title: l.targetNode?.title || '',
        mapped_events: l.value,
      })),
    },
  };
}

function drawHeatmap() {
  clearSvg();
  if (!graphData) return;

  const topS = Math.max(5, Math.min(200, Number(heatmapTopSources?.value || 18)));
  const topC = Math.max(5, Math.min(200, Number(heatmapTopControls?.value || 36)));

  const nodes = graphData.nodes || [];
  const sourcesAll = nodes
      .filter((n) => n.type === 'source')
      .slice()
      .sort((a, b) => Number(b.mapped_events || 0) - Number(a.mapped_events || 0));
  const controlsAll = nodes
      .filter((n) => n.type === 'control')
      .slice()
      .sort((a, b) => Number(b.mapped_events || 0) - Number(a.mapped_events || 0));

  const sources = sourcesAll.slice(0, topS);
  const controls = controlsAll.slice(0, topC);

  const srcById = new Map(sources.map((s) => [s.id, s]));
  const ctrlById = new Map(controls.map((c) => [c.id, c]));

  const weightByKey = new Map();
  let vMax = 0;
  for (const l of graphData.links || []) {
    if (!srcById.has(l.source) || !ctrlById.has(l.target)) continue;
    const v = Number(l.value || 0);
    if (!v) continue;
    const k = `${l.source}::${l.target}`;
    weightByKey.set(k, v);
    if (v > vMax) vMax = v;
  }

  const cell = 18;
  const heatmapLabels = vizRelationshipLabels(vizMode);
  const heatmapMetricLabel = graphKind() === 'clause_control_relationship' ? 'Link count' : 'Mapped events';
  const wrapWidth = getWrapWidth(680);
  // Leave ample headroom for rotated control labels.
  const margin = {
    top: 150,
    right: 18,
    bottom: 24,
    left: 190,
  };

  const width = Math.max(
      wrapWidth,
      margin.left + margin.right + controls.length * cell
  );
  const heightNeeded = margin.top + margin.bottom + sources.length * cell;
  const height = Math.max(260, heightNeeded); // 260 is a small "safety" minimum

  sizeSvg(width, height);

  // Heatmap colour: hue comes from each source's configured colour (including user overrides);
  // intensity comes from mapped-event concentration.
  const exampleLegendColor = _heatColorForBase(sources?.[0]?.color);

  // Legend (mapped-event intensity)
  {
    const legendW = 120;
    const legendH = 10;
    const legendX = width - margin.right - legendW - 10;
    const legendY = 44;
    const lg = svg
        .append('g')
        .attr('class', 'viz-legend')
        .attr('transform', `translate(${legendX}, ${legendY})`);

    // Keep legend text readable on small screens:
    //  - Put the title above the bar
    //  - Put 0 / vmax below the bar
    const titleY = -12;
    const numY = legendH + 14;

    lg
        .append('text')
        .attr('x', legendW)
        .attr('y', titleY)
        .attr('text-anchor', 'end')
        .attr('class', 'small-muted')
        .text(heatmapMetricLabel);

    for (let i = 0; i < legendW; i++) {
      const t = i / (legendW - 1);
      const v = t * Math.max(1, vMax);
      lg
          .append('rect')
          .attr('x', i)
          .attr('y', 0)
          .attr('width', 1)
          .attr('height', legendH)
          .attr('fill', v <= 0 ? 'rgba(31,31,42,0.02)' : exampleLegendColor(_heatIntensity(v, vMax)));
    }

    lg.append('text').attr('x', 0).attr('y', numY).attr('class', 'small-muted').text('0');
    lg
        .append('text')
        .attr('x', legendW)
        .attr('y', numY)
        .attr('text-anchor', 'end')
        .attr('class', 'small-muted')
        .text(String(vMax || 0));
  }

  // Column labels (controls)
  const x0 = margin.left;
  const y0 = margin.top;

  // Place labels well above the grid so they don't overlap the first row.
  const colLabelY = y0 - 56;
  const colLabels = svg
      .append('g')
      .attr('class', 'viz-axis')
      .attr('transform', `translate(${x0}, ${colLabelY})`);

  colLabels
      .selectAll('text')
      .data(controls)
      .enter()
      .append('text')
      .attr('x', (_, i) => i * cell + cell / 2)
      .attr('y', 0)
      .attr('dy', '0.32em')
      .attr('text-anchor', 'end')
      .attr('transform', (_, i) => `rotate(-45, ${i * cell + cell / 2}, 0)`)
      .text((d) => d.label)
      .style('cursor', 'pointer')
      .on('click', (ev, d) => {
        ev.preventDefault();
        openGraphNode(d, ev);
      })
      .append('title')
      .text((d) => `${d.label} — ${d.title || ''}`);

  // Row labels (sources)
  // Ensure the swatch never overlaps the first heatmap column.
  // We position the legend group so its right-most edge (swatch end) sits ROW_GRID_GAP px left of the grid start (x0).
  const ROW_SWATCH = 10;
  const ROW_SWATCH_X = 8;
  const ROW_GRID_GAP = 8;
  const rowLabelsX = x0 - ROW_GRID_GAP - (ROW_SWATCH_X + ROW_SWATCH);

  const rowLabels = svg
      .append('g')
      .attr('class', 'viz-axis')
      .attr('transform', `translate(${rowLabelsX}, ${y0})`);

  // Colour swatch per source (matches source colour across the app)
  rowLabels
      .selectAll('rect')
      .data(sources)
      .enter()
      .append('rect')
      .attr('x', ROW_SWATCH_X)
      .attr('y', (_, i) => i * cell + (cell / 2) - 5)
      .attr('width', ROW_SWATCH)
      .attr('height', ROW_SWATCH)
      .attr('rx', 2)
      .attr('ry', 2)
      .attr('fill', (d) => d?.color || '#7c3aed')
      .attr('stroke', 'rgba(0,0,0,0.25)')
      .attr('stroke-width', 1)
      .style('cursor', 'pointer')
      .on('click', (ev, d) => {
        ev.preventDefault();
        openGraphNode(d, ev);
      })
      .append('title')
      .text((d) => d?.label || '');

  rowLabels
      .selectAll('text')
      .data(sources)
      .enter()
      .append('text')
      .attr('x', 0)
      .attr('y', (_, i) => i * cell + cell / 2)
      .attr('text-anchor', 'end')
      .attr('dominant-baseline', 'middle')
      .text((d) => truncateMiddleText(d.label, 28))
      .style('cursor', 'pointer')
      .on('click', (ev, d) => {
        ev.preventDefault();
        openGraphNode(d, ev);
      })
      .append('title')
      .text((d) => d.label || '');

  // Cells
  const cells = [];
  for (let i = 0; i < sources.length; i++) {
    for (let j = 0; j < controls.length; j++) {
      const s = sources[i];
      const c = controls[j];
      const k = `${s.id}::${c.id}`;
      const v = Number(weightByKey.get(k) || 0);
      cells.push({
        i,
        j,
        v,
        s,
        c,
      });
    }
  }

  svg
      .append('g')
      .attr('transform', `translate(${x0}, ${y0})`)
      .selectAll('rect')
      .data(cells)
      .enter()
      .append('rect')
      .attr('class', 'heat-cell')
      .attr('x', (d) => d.j * cell)
      .attr('y', (d) => d.i * cell)
      .attr('width', cell)
      .attr('height', cell)
      .attr('fill', (d) => {
        if (!d.v) return 'rgba(31,31,42,0.02)';
        const f = _heatColorForBase(d.s?.color);
        return f(_heatIntensity(d.v, vMax));
      })
      .on('click', (ev, d) => {
        ev.preventDefault();
        if (!d.v) return;
        if (d.c?.node_kind === 'clause') {
          openEventsForLink(d.s?.source || '', '', ev, null, {clause: d.c?.label || d.c?.ref || d.c?.clause_id || ''});
          return;
        }
        if (d.s?.node_kind === 'clause' || isRelationshipGraph()) {
          openGraphNode(d.c || d.s, ev);
          return;
        }
        // Pass control ref (shown in dropdowns) instead of UUID.
        openEventsForLink(d.s.source, d.c?.label || d.c?.control_ref || '', ev);
      })
      .append('title')
      .text((d) => {
        const s = d.s?.label || '';
        const c = d.c?.label || '';
        return `${s} → ${c}\n${heatmapMetricLabel}: ${d.v}`;
      });

  // A note
  svg
      .append('text')
      .attr('x', 14)
      .attr('y', 18)
      .attr('class', 'small-muted')
      .text(`Showing ${sources.length} ${heatmapLabels.leftPlural} × ${controls.length} ${heatmapLabels.rightPlural}`);

  if (graphKind() === 'clause_control_relationship') {
    setVizCaption(vizMode, `Showing top ${sources.length} clauses × top ${controls.length} controls (cells show link count).`);
  } else {
    setVizCaption(vizMode, `Showing top ${sources.length} ${heatmapLabels.leftPlural} × top ${controls.length} ${heatmapLabels.rightPlural} (ranked by mapped events).`);
  }

  lastView = {
    mode: 'heatmap',
    range: getActiveRange(),
    payload: {
      top_sources: sources.length,
      top_controls: controls.length,
      vmax: vMax,
      cells: cells.filter((d) => d.v).map((d) => ({
        source: d.s?.source || '',
        source_label: d.s?.label || '',
        control_id: d.c?.control_id || '',
        control_ref: d.c?.label || '',
        control_title: d.c?.title || '',
        mapped_events: d.v,
      })),
    },
  };
}

function buildHierarchy(mode) {
  if (!graphData) return {name: 'All', children: []};

  // Used to estimate *absolute* slice angle when deciding whether something is too small to
  // render as its own segment. This must be based on the whole sunburst total, not the
  // parent subtotal, otherwise we can dramatically overestimate angles for children of small
  // top-level segments (which is what produces the hairline-slice clusters).
  let ROOT_TOTAL_FOR_ANGLE = 0;


  // Sunburst: merge very small sibling segments into a single "Other" slice.
  // This reduces the hairline-slice problem (and unreadable label "smear")
  // when a parent has many tiny children.
  //
  // We tune thresholds slightly differently for the two sunburst modes:
  //  - by source: controls can get very fragmented; be more aggressive
  //  - by control: sources are usually fewer; be less aggressive
  const OTHER_DEFAULTS = {
    min_frac: 0.006,     // merge children <0.6% of the parent total (after keeping the largest few)
    min_keep: 10,        // always keep at least this many largest children
    max_keep: 26,        // cap visible children; remainder goes into Other
    min_merge: 2,        // only create Other if at least this many children are merged
    keep_upto: 0.992,    // keep explicit slices until we cover this fraction of the parent total
    min_angle_rad: 0.0,  // additional visual guardrail: merge if slice angle is too small
    force_min_keep: false, // when true, keep the largest min_keep even if individually small
  };

  const OTHER_OPTS = {
    // Ring 1 = sources, Ring 2 = controls mapped to that source
    sunburst_source: {
      // Root-level merging is the only place we create an "Other" bucket.
      // This ensures the whole sundial has a *single* "Other" wedge.
      // "By source" can be very skewed (one dominant source + lots of tiny ones).
      // Be more aggressive at the root to avoid the "hairline cluster" that can appear
      // even after we create a global "Other" slice.
      // Root thresholds here are intentionally stricter than other charts.
      // We use *absolute* slice angle (relative to the entire sunburst) so that
      // small sources don't survive just because they are "large" within a tiny parent.
      // Goal: if a source is visually tiny in ring-1, it should be folded into the single
      // global "Other" wedge so it doesn't leave thin slivers (and their fragmented ring-2 leaves)
      // floating after "Other".
      root:  {min_frac: 0.010, min_keep: 5, max_keep: 10, keep_upto: 0.980, min_merge: 2, min_angle_rad: 0.160, extend_outer: true},
      child: {min_frac: 0.004, min_keep: 6, max_keep: 16, keep_upto: 0.985, min_merge: 2, min_angle_rad: 0.050},
    },
    // Ring 1 = controls, Ring 2 = sources mapped to that control
    sunburst_control: {
      root:  {...OTHER_DEFAULTS, min_frac: 0.006, min_angle_rad: 0.080, extend_outer: true},
      child: OTHER_DEFAULTS,
    },
  };

  function mergeChildrenIntoOther(parent, opts = OTHER_DEFAULTS) {
    if (!parent || !Array.isArray(parent.children) || parent.children.length === 0) return;

    const OTHER_MIN_FRAC = Number.isFinite(opts.min_frac) ? opts.min_frac : OTHER_DEFAULTS.min_frac;
    const OTHER_MIN_KEEP = Number.isFinite(opts.min_keep) ? opts.min_keep : OTHER_DEFAULTS.min_keep;
    const OTHER_MAX_KEEP = Number.isFinite(opts.max_keep) ? opts.max_keep : OTHER_DEFAULTS.max_keep;
    const OTHER_MIN_MERGE = Number.isFinite(opts.min_merge) ? opts.min_merge : OTHER_DEFAULTS.min_merge;
    const KEEP_UPTO = Number.isFinite(opts.keep_upto) ? opts.keep_upto : OTHER_DEFAULTS.keep_upto;
    const MIN_ANGLE = Number.isFinite(opts.min_angle_rad) ? opts.min_angle_rad : OTHER_DEFAULTS.min_angle_rad;
    const FORCE_MIN_KEEP = !!opts.force_min_keep;
    const EXTEND_OUTER = !!opts.extend_outer;

    // Determine each child's total contribution:
    //  - leaf nodes: use `value`
    //  - internal nodes: sum direct children leaf `value`s
    const childTotal = (n) => {
      const v = Number(n?.value);
      if (Number.isFinite(v) && v > 0) return v;

      if (Array.isArray(n?.children) && n.children.length) {
        let t = 0;
        for (const c of n.children) {
          const cv = Number(c?.value);
          if (Number.isFinite(cv) && cv > 0) t += cv;
        }
        return t;
      }
      return 0;
    };

    const kids = parent.children
        .map((node) => ({node, v: childTotal(node)}))
        .filter((kv) => Number.isFinite(kv.v) && kv.v > 0);

    if (kids.length === 0) return;

    kids.sort((a, b) => Number(b.v || 0) - Number(a.v || 0));
    const total = kids.reduce((acc, kv) => acc + Number(kv.v || 0), 0);
    if (!total) return;

    // Absolute total for the whole sunburst (fallback to local total if not set yet).
    const ROOT_TOTAL = Math.max(1, Number(ROOT_TOTAL_FOR_ANGLE || total));

    const keep = [];
    const merged = [];
    let keptFrac = 0;

    for (let i = 0; i < kids.length; i++) {
      const kv = kids[i];
      const frac = Number(kv.v || 0) / total;

      // IMPORTANT: use *absolute* angle (relative to the whole sunburst), not angle within
      // the parent wedge. Otherwise children of small top-level segments look far larger
      // than they really are, and don't get merged.
      const absFrac = Number(kv.v || 0) / ROOT_TOTAL;
      const angle = 2 * Math.PI * absFrac;

      // Keep the largest few to preserve recognisable structure, then rely on size thresholds.
      const mustKeep = keep.length < OTHER_MIN_KEEP && (FORCE_MIN_KEEP || (frac >= OTHER_MIN_FRAC && angle >= MIN_ANGLE));

      // Visual + fraction gating (prevents lots of hairline slices).
      const keepBySize = frac >= OTHER_MIN_FRAC;
      const keepByAngle = angle >= MIN_ANGLE;

      // Respect a maximum number of explicit slices; everything else gets merged.
      const canKeepMore = keep.length < OTHER_MAX_KEEP;

      // Also stop keeping once we've covered most of the parent total.
      const withinCum = keptFrac < KEEP_UPTO;

      if (canKeepMore && (mustKeep || (keepBySize && keepByAngle && withinCum))) {
        keep.push(kv);
        keptFrac += frac;
      } else {
        merged.push(kv);
      }
    }

    if (merged.length < OTHER_MIN_MERGE) {
      // Not enough to justify an "Other" bucket.
      parent.children = kids.map((kv) => kv.node);
      return;
    }

    const first = (keep[0] || merged[0] || {}).node || {};
    const kind = String(first?.kind || '').trim() || 'control';
    const mergedTotal = merged.reduce((acc, kv) => acc + Number(kv.v || 0), 0);

    const otherItems = merged.map((kv) => {
      const n = kv.node || {};
      return {
        name: n.name,
        kind: n.kind || kind,
        value: Number(kv.v || 0),
        control_id: n.control_id,
        control_ref: n.control_ref,
        source_key: n.source_key,
        source_label: n.source_label,
        clause_id: n.clause_id,
        clause_ref: n.clause_ref,
        color: n.color,
      };
    });

    // Preserve the full node structures for drill-down.
    // These nodes include their children (ring-2 leaves), allowing the UI to
    // re-render a "fresh" sunburst when the user clicks the "Other" wedge.
    const otherNodes = merged.map((kv) => kv.node).filter(Boolean);

    const out = keep.map((kv) => kv.node);

    // IMPORTANT:
    // We only want one "Other" wedge for the whole sundial.
    // When merging at the root, we also want the "Other" wedge to visually extend
    // across both rings. To do that without double-counting (d3.hierarchy().sum()
    // adds a node's own `value` *and* its children), we make the root-level "Other"
    // an internal node with a single child that carries the numeric `value`.
    const otherNode = {
      name: 'Other',
      kind,
      is_other: true,
      other_count: merged.length,
      other_items: otherItems,
      other_nodes: otherNodes,
    };
    if (EXTEND_OUTER) {
      otherNode.children = [{
        name: 'Other',
        kind,
        is_other: true,
        value: mergedTotal,
        other_count: merged.length,
        other_items: otherItems,
        other_nodes: otherNodes,
      }];
    } else {
      otherNode.value = mergedTotal;
    }

    out.push(otherNode);

    parent.children = out;
  }

  const nodes = graphData.nodes || [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const root = {name: 'All', children: []};

  if (mode === 'sunburst_source') {
    const srcMap = new Map();
    let totalAll = 0;
    for (const l of graphData.links || []) {
      const s = byId.get(l.source);
      const c = byId.get(l.target);
      if (!s || !c) continue;
      if (s.type !== 'source' || c.type !== 'control') continue;
      const v = Number(l.value || 0);
      if (!v) continue;

      totalAll += v;

      if (!srcMap.has(s.id)) {
        srcMap.set(s.id, {
          name: s.label,
          kind: s.node_kind === 'clause' ? 'clause' : 'source',
          source_label: s.label,
          source_key: s.source,
          clause_id: s.clause_id,
          clause_ref: s.ref || s.label,
          color: s.color,
          children: [],
        });
      }

      srcMap.get(s.id).children.push({
        name: c.label,
        kind: c.node_kind === 'clause' ? 'clause' : 'control',
        control_id: c.control_id,
        control_ref: c.label,
        clause_id: c.clause_id,
        clause_ref: c.ref || c.label,
        value: v,
      });
    }

    // Set absolute total so merge thresholds based on slice angle are accurate.
    ROOT_TOTAL_FOR_ANGLE = totalAll;

    // Source/control sunbursts keep a single root-level "Other" bucket.
    const modeOpts = (OTHER_OPTS[mode] || OTHER_OPTS.sunburst_source);

    root.children = Array.from(srcMap.values()).map((s) => {
      s.children.sort((a, b) => Number(b.value || 0) - Number(a.value || 0));
      return s;
    });
    root.children.sort((a, b) => {
      const sa = a.children.reduce((acc, x) => acc + Number(x.value || 0), 0);
      const sb = b.children.reduce((acc, x) => acc + Number(x.value || 0), 0);
      return sb - sa;
    });
    // Also merge very small top-level segments into a single "Other" slice.
    mergeChildrenIntoOther(root, modeOpts.root);
    return root;
  }

  // sunburst_control
  const ctrlMap = new Map();
  let totalAll = 0;
  for (const l of graphData.links || []) {
    const s = byId.get(l.source);
    const c = byId.get(l.target);
    if (!s || !c) continue;
    if (s.type !== 'source' || c.type !== 'control') continue;
    const v = Number(l.value || 0);
    if (!v) continue;

    totalAll += v;

    if (!ctrlMap.has(c.id)) {
      ctrlMap.set(c.id, {
        name: c.label,
        kind: c.node_kind === 'clause' ? 'clause' : 'control',
        control_id: c.control_id,
        control_ref: c.label,
        clause_id: c.clause_id,
        clause_ref: c.ref || c.label,
        children: [],
      });
    }

    ctrlMap.get(c.id).children.push({
      name: s.label,
      kind: s.node_kind === 'clause' ? 'clause' : 'source',
      source_label: s.label,
      source_key: s.source,
      clause_id: s.clause_id,
      clause_ref: s.ref || s.label,
      color: s.color,
      value: v,
    });
  }

  // Set absolute total so merge thresholds based on slice angle are accurate.
  ROOT_TOTAL_FOR_ANGLE = totalAll;

  // Control/source sunbursts keep a single root-level "Other" bucket.
  const modeOpts = (OTHER_OPTS[mode] || OTHER_OPTS.sunburst_control);

  root.children = Array.from(ctrlMap.values()).map((c) => {
    c.children.sort((a, b) => Number(b.value || 0) - Number(a.value || 0));
    return c;
  });
  root.children.sort((a, b) => {
    const sa = a.children.reduce((acc, x) => acc + Number(x.value || 0), 0);
    const sb = b.children.reduce((acc, x) => acc + Number(x.value || 0), 0);
    return sb - sa;
  });
  // Also merge very small top-level segments into a single "Other" slice.
  mergeChildrenIntoOther(root, modeOpts.root);
  return root;
}

function drawSunburst(mode, initialFocus = null, drill = null) {
  clearSvg();
  // Default: hide the "Other" breakdown panel; it will be shown when relevant.
  setSunburstOtherPanel('');
  if (!graphData) return;

  const isDrill = !!(drill && typeof drill === 'object' && drill.data);
  const drillBack = isDrill ? (drill.back || null) : null;
  const data = isDrill ? drill.data : buildHierarchy(mode);

  // If we're drilling into "Other", update the copy in the header so it's clear
  // we're looking at a scoped subset (and how to go back).
  if (isDrill) {
    const meta = VIZ_META[mode] || VIZ_META.sunburst_source;
    if (vizTitle) vizTitle.textContent = `${meta.title} — Other`;
    if (vizSubtitle) {
      vizSubtitle.textContent =
        'Drill-down view of segments previously merged into "Other". ' +
        'Click inner segments to zoom; click outer segments to open filtered events; ' +
        'click the center to go back.';
    }
    setVizCaption(vizMode);
  }
  if (!data.children || data.children.length === 0) {
    const w = getWrapWidth(680);
    sizeSvg(w, 360);
    svg
        .append('text')
        .attr('x', 14)
        .attr('y', 24)
        .attr('class', 'small-muted')
        .text('No mapped events yet.');
    return;
  }

  const width = getWrapWidth(680);
  // When switching from a tall visualisation (e.g. Graph), the wrapper can remain tall
  // due to the previous SVG height, which creates a large amount of whitespace above/below
  // the sunburst if we size it from wrap.clientHeight. Use a width-based height with a cap.
  const height = Math.max(560, Math.min(820, Math.round(width * 0.92)));
  sizeSvg(width, height);

  const radius = Math.min(width, height) / 2 - 20;

  const root = d3
      .hierarchy(data)
      .sum((d) => Number(d.value || 0))
      .sort((a, b) => (b.value || 0) - (a.value || 0));

  // Make the centre "hole" smaller (more chart area), while keeping the outer radius the same.
  // We do this by asking d3.partition for a slightly larger radial size, then shifting everything
  // inward by a fixed delta.
  const holeDelta = Math.round(radius * 0.16);
  d3.partition().size([2 * Math.PI, radius + holeDelta])(root);
  root.each((d) => {
    d.y0 = Math.max(0, d.y0 - holeDelta);
    d.y1 = Math.max(0, d.y1 - holeDelta);
    d.current = {x0: d.x0, x1: d.x1, y0: d.y0, y1: d.y1};
  });

  // Colour strategy:
  //  - Source segments use the same badge colour as elsewhere (graphData node colour already respects user overrides)
  //  - Control segments use a separate neutral palette to avoid colliding with arbitrary source hues
  function _hashUnit(str) {
    // FNV-1a 32-bit -> [0,1]
    const s = String(str || '');
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0) / 4294967295;
  }

  function _controlFill(d) {
    const key = String(d?.data?.control_id || d?.data?.control_ref || d?.data?.name || '');
    const u = _hashUnit(key);
    const hue = 360 * u;

    // Make controls bolder (less pastel): higher saturation + lower lightness,
    // with a small deterministic jitter per control so neighbouring slices still differentiate.
    // Also clamp lightness so labels can safely default to white.
    const depth = Number(d?.depth || 1);
    const sat = depth <= 1 ? 0.74 : 0.66;
    const baseL = depth <= 1 ? 0.34 : 0.42;
    const l = Math.max(0.26, Math.min(0.54, baseL + (u - 0.5) * 0.08));
    return d3.hsl(hue, sat, l).formatHex();
  }

  function _sourceFill(d) {
    const base = String(d?.data?.color || '').trim() || 'rgba(124, 58, 237, 0.95)';
    // Add depth (less pastel) while keeping the badge hue as the reference.
    try {
      const rgb = d3.rgb(base);
      const lum = _relativeLuminance(rgb);
      let darken = 0.18;
      if (lum > 0.78) darken = 0.38;
      else if (lum > 0.62) darken = 0.28;
      else if (lum < 0.22) darken = 0.10;
      return _mixRgb(rgb, d3.rgb(0, 0, 0), darken).formatHex();
    } catch {
      // ignore
    }
    return base;
  }

  function _fillFor(d) {
    // A slightly stronger neutral so "Other" doesn't look washed out next to bolder colours.
    if (d?.data?.is_other) return 'rgba(31,31,42,0.28)';
    return d?.data?.kind === 'source' ? _sourceFill(d) : _controlFill(d);
  }

  const arc = d3
      .arc()
      .startAngle((d) => d.x0)
      .endAngle((d) => d.x1)
      .innerRadius((d) => d.y0)
      .outerRadius((d) => d.y1);

  const g = svg
      .append('g')
      .attr('transform', `translate(${width / 2}, ${height / 2})`);

  const arcVisible = (c) => c.y1 <= radius && c.y0 >= 0 && c.x1 > c.x0;
  const isControlRef = (s) => /^A\.\d+(?:\.\d+)*$/i.test(String(s || '').trim());

  let focus = root;

  // Label visibility: use pixel-ish heuristics instead of pure angle thresholds.
  // This reduces label clutter (and the "ink smear" effect from label stroke)
  // when there are many small segments.
  const labelVisible = (d, coords = d.current) => {
    // When zoomed into a top-level segment, its name is already shown in the center.
    // Hide ring-1 labels to avoid the vertical/overlapping arc label on the full-circle inner ring.
    if (focus && focus.depth > 0 && d.depth === 1) return false;

    if (!arcVisible(coords)) return false;

    // Always show the label for the main "Other" bucket so users can recognise
    // the merged slice, but do not label every per-clause child "Other" bucket;
    // that created repeated "Other" labels around the outer ring.
    if (d?.data?.is_other) {
      // If the root-level Other is implemented as an internal node with a single child
      // (so it visually spans both rings), hide the child's label to avoid duplicates.
      if (d.parent?.data?.is_other) return false;
      return d.depth <= 2;
    }

    // Avoid clutter: only label the first two rings.
    if (d.depth > 2) return false;

    const c = coords;
    const midR = (c.y0 + c.y1) / 2;
    const angle = c.x1 - c.x0;
    const arcLen = angle * midR; // ~px of arc length available
    const radial = c.y1 - c.y0; // ~px of ring thickness

    const name = String(d.data?.name || '');
    if (!name) return false;
    const relaxedCtrl = mode === 'sunburst_source' && d.depth === 2 && isControlRef(name);
    const txt = name.length <= 18 ? name : `${name.slice(0, 16)}…`;

    // Rough text-width estimate (enough to decide if it will fit).
    const charW = d.depth === 1 ? 6.5 : 6.0;
    const need = txt.length * charW;

    // Minimums tuned to prevent the "ink smear" effect when there are many tiny slices.
    // Relax ONLY for control refs on Sunburst (by source) ring-2 labels.
    const minAngle = relaxedCtrl ? 0.08 : (d.depth === 1 ? 0.12 : 0.16); // ~4.6° / ~7° / ~9°
    const minRadial = relaxedCtrl ? 10 : (d.depth === 1 ? 16 : 12);
    const minArc = relaxedCtrl ?
      Math.max(42, need) :
      (d.depth === 1 ? Math.max(70, need) : Math.max(90, need + 8));

    // Also require a small share of the total so we don't label near-zero slices.
    const frac = (d.value || 0) / (root.value || 1);
    const minFrac = relaxedCtrl ? 0.001 : (d.depth === 1 ? 0.01 : 0.005);

    return radial >= minRadial && angle >= minAngle && arcLen >= minArc && frac >= minFrac;
  };
  const labelTransform = (c) => {
    const x = (c.x0 + c.x1) / 2;
    const y = (c.y0 + c.y1) / 2;
    const rotate = (x * 180) / Math.PI - 90;
    const flip = x < Math.PI ? 0 : 180;
    return `rotate(${rotate}) translate(${y},0) rotate(${flip})`;
  };

  const labelText = (d) => {
    const name = String(d.data?.name || '');
    if (!name) return '';
    if (d?.data?.is_other && d.parent?.data?.is_other) return '';
    if (name.length <= 18) return name;
    return `${name.slice(0, 16)}…`;
  };

  // Center click target for zoom-out.
  // IMPORTANT: the inner "hole" radius is ~root.y1 (and equals the first ring's inner radius).
  // If we keep this too small, users can miss the target and think zoom-out is broken.
  const innerHoleR = (root.children?.[0]?.y0 ?? root.y1) || 0;
  const centerR = Math.max(40, innerHoleR + 4);

  const parent = g
      .append('circle')
      .datum(root)
      .attr('class', 'sunburst-center-target')
      .attr('r', centerR)
      .attr('fill', 'rgba(255,255,255,0.95)')
      .attr('stroke', 'rgba(31,31,42,0.10)')
      .attr('stroke-width', 1)
      .style('pointer-events', 'all')
      .style('cursor', (isDrill && drillBack) ? 'pointer' : 'default')
      .on('click', (ev, d) => {
        ev.preventDefault();
        // In "Other" drill-down mode, clicking the center at the drill root returns
        // to the main sunburst.
        if (isDrill && drillBack && (!focus || !focus.parent)) {
          setViz(mode, {skipDraw: true});
          drawSunburst(mode, drillBack.initialFocus || null);
          return;
        }
        // `d` is the parent of the current focus (set in clicked()).
        if (d) clicked(d);
      });


  function renderOtherPanel(node) {
  // Show breakdown when:
  //  - focused on root (depth 0) and the root has an "Other" top-level slice, OR
  //  - focused on a top-level segment (depth 1) and that segment has an "Other" child slice.
  if (!node || (node.depth !== 0 && node.depth !== 1)) {
    setSunburstOtherPanel('');
    return;
  }

  const other = (node.children || []).find((c) => !!c?.data?.is_other) || null;
  const items = other && Array.isArray(other.data?.other_items) ? other.data.other_items.slice() : [];
  if (!other || items.length === 0) {
    setSunburstOtherPanel('');
    return;
  }

  items.sort((a, b) => Number(b?.value || 0) - Number(a?.value || 0));

  const r = getActiveRange();

  const otherItemType = (() => {
    if (node.depth === 0) {
      if (mode === 'sunburst_source') return 'source';
      if (mode === 'sunburst_control') return 'control';
      return 'segment';
    }
    if (mode === 'sunburst_source') return 'control';
    if (mode === 'sunburst_control') return 'source';
    return 'segment';
  })();

  const labelForType = (t, plural = false) => {
    const labels = {
      source: ['Source', 'Sources'],
      control: ['Control', 'Controls'],
      clause: ['Clause', 'Clauses'],
      segment: ['Segment', 'Segments'],
    };
    const pair = labels[t] || labels.segment;
    return plural ? pair[1] : pair[0];
  };

  const title = `${labelForType(otherItemType, true)} merged into "Other"`;
  const scopeName = node.depth === 0 ? 'All' : String(node.data?.name || '');
  const header = labelForType(otherItemType, false);
  const countLabel = 'events';
  const itemActionLabel = 'open filtered events';

  const hrefForOtherItem = (it) => {
    const label = String(it?.name || '');

    if (mode === 'sunburst_source') {
      if (otherItemType === 'source') return buildEventsHref(String(it?.source_key || '').trim(), '');
      const src = String(node.data?.source_key || '').trim();
      const control = String(it?.control_ref || label || it?.control_id || '').trim();
      return buildEventsHref(src, control);
    }

    if (mode === 'sunburst_control') {
      if (otherItemType === 'control') {
        const control = String(it?.control_ref || label || it?.control_id || '').trim();
        return buildEventsHref('', control);
      }
      const src = String(it?.source_key || '').trim();
      const control = String(node.data?.control_ref || node.data?.name || node.data?.control_id || '').trim();
      return buildEventsHref(src, control);
    }

    return '#';
  };

  const rows = items.map((it) => {
    const v = Number(it?.value || 0);
    const label = String(it?.name || '');
    const href = hrefForOtherItem(it);
    return `<tr>
      <td><a class="link-dark text-decoration-none" href="${href}">${esc(label)}</a></td>
      <td class="text-end" data-sort="${v}">${esc(String(Math.round(v)))}</td>
    </tr>`;
  }).join('');

  const total = Math.round(items.reduce((acc, it) => acc + Number(it?.value || 0), 0));
  const rangeTxt = (r && r.from && r.to) ? `${esc(r.from)} → ${esc(r.to)}` : '';

  setSunburstOtherPanel(`
    <div class="border rounded p-2" style="background: rgba(31,31,42,0.03)">
      <div class="d-flex justify-content-between align-items-baseline">
        <div class="small-muted fw-bold">${title}</div>
        <div class="small-muted">${items.length} segments • ${esc(String(total))} ${countLabel}${rangeTxt ? ` • ${rangeTxt}` : ''}</div>
      </div>
      <div class="small-muted" style="margin-top:2px">Scope: <span class="fw-bold">${esc(scopeName)}</span> — click an item to ${itemActionLabel}.</div>
      <div class="table-responsive" style="max-height: 210px; overflow:auto; margin-top:6px">
        <table class="table table-sm mb-0">
          <thead>
            <tr>
              <th>${header}</th>
              <th class="text-end">Mapped ${countLabel}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `);
}


  const centerLabel = g
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('class', 'small-muted')
      .style('font-size', '16px')
      .style('font-weight', 700)
      .style('pointer-events', 'none')
      .text('All');

  // Breadcrumb + hint (shown only when zoomed in)
  const centerBreadcrumb = g
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('class', 'small-muted')
      .attr('y', 18)
      .style('font-size', '11.5px')
      .style('pointer-events', 'none')
      .style('opacity', 0)
      .text('');

  const centerHint = g
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('class', 'small-muted')
      .attr('y', 4)
      .style('font-size', '12.5px')
      .style('pointer-events', 'none')
      .style('opacity', 1)
      .text('');

  const centerHintZoom = g
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('class', 'small-muted')
      .attr('y', 34)
      .style('font-size', '11.5px')
      .style('pointer-events', 'none')
      .style('opacity', 0)
      .text('Click center to zoom out');

  function setMultilineText(textSel, lines, lineDyEm = 1.18) {
    if (!textSel) return;
    textSel.text(null);
    const ll = Array.isArray(lines) ? lines : [String(lines || '')];
    ll.filter((x) => String(x || '').trim()).forEach((line, i) => {
      textSel
          .append('tspan')
          .attr('x', 0)
          .attr('dy', i === 0 ? 0 : `${lineDyEm}em`)
          .text(String(line));
    });
  }

  // Wrapped centre hint (requested): fits better in the smaller centre hole.
  setMultilineText(centerHint, ['Click outer wedges for events,', 'click inners to zoom']);

  function truncateMiddle(str, max = 56) {
    const ss = String(str || '');
    if (ss.length <= max) return ss;
    const keep = Math.max(10, Math.floor((max - 1) / 2));
    return `${ss.slice(0, keep)}…${ss.slice(-keep)}`;
  }

  function breadcrumbsFor(node) {
    const parts = node
        .ancestors()
        .reverse()
        .map((a) => a.data?.name)
        .filter(Boolean);
    if (parts.length <= 1) return 'All';
    return parts.join(' › ');
  }

  function updateCenterUI(node) {
    const isRoot = !node?.parent;
    const name = String(node?.data?.name || 'All');

    // Root (All): show the multi-line helper in the middle.
    // Zoomed-in: hide the helper so it doesn't clash with the focused node label + breadcrumb.
    centerLabel.attr('y', isRoot ? -14 : -12).text(name);

    if (isRoot) {
      centerHint.style('opacity', 1);
      centerBreadcrumb.style('opacity', 0).text('');
      if (isDrill && drillBack) {
        centerHintZoom.style('opacity', 1).text('Click center to go back');
      } else {
        centerHintZoom.style('opacity', 0).text('Click center to zoom out');
      }
    } else {
      centerHint.style('opacity', 0);
      centerBreadcrumb
          .style('opacity', 1)
          .text(truncateMiddle(breadcrumbsFor(node)));
      centerHintZoom.style('opacity', 1).text('Click center to zoom out');
    }
  }

  updateCenterUI(root);

  const path = g
      .append('g')
      .selectAll('path')
      .data(root.descendants().filter((d) => d.depth))
      .join('path')
      .attr('class', 'sunburst-arc')
      .attr('fill', (d) => _fillFor(d))
      // Less translucent = less "pastel" looking.
      .attr('fill-opacity', (d) => (arcVisible(d.current) ? (d.depth === 1 ? 0.97 : 0.90) : 0))
      .attr('pointer-events', (d) => (arcVisible(d.current) ? 'auto' : 'none'))
      .attr('d', (d) => arc(d.current))
      .on('click', (ev, d) => {
        ev.preventDefault();
        if (!d) return;

        // "Other" represents many aggregated segments.
        // Default behaviour: drill into it and render a fresh sunburst containing
        // the merged segments as explicit ring-1 slices.
        // (Shift-click keeps the old behaviour: show a breakdown list with links.)
        if (d?.data?.is_other) {
          const p = d.parent || root;
          if (ev.shiftKey) {
            if (p && focus !== p) clicked(p);
            else renderOtherPanel(p);
            return;
          }

          const nodes = Array.isArray(d?.data?.other_nodes) ? d.data.other_nodes : [];
          if (nodes.length) {
            // If this is a second-ring "Other" bucket, preserve the focused
            // parent (for example, the clause) as the drill-down root so leaf
            // clicks still carry the correct source/control/clause context.
            const contextualParent = (d.parent && d.parent.depth > 0 && !d.parent?.data?.is_other) ? d.parent : null;
            const drillRoot = contextualParent ? {...(contextualParent.data || {}), children: nodes} : {name: 'Other', children: nodes};
            drawSunburst(mode, null, {
              data: drillRoot,
              back: {initialFocus: sunburstFocus},
            });
            return;
          }

          // Fallback: if we don't have drill nodes for some reason, show the list.
          if (p && focus !== p) clicked(p);
          else renderOtherPanel(p);
          return;
        }

        // If the user clicks a leaf (no children), open filtered Events.
        // Also: if the user is already focused on a segment (can't zoom further), clicking it opens Events.
        const isLeaf = !d.children;
        const isFocused = (focus === d);

        const filters = (() => {
          let sourceKey = '';
          let controlRef = '';
          let clauseRef = '';
          let clauseId = '';
          let controlId = '';
          for (const a of d.ancestors()) {
            if (!sourceKey && a.data?.kind === 'source' && a.data?.source_key) sourceKey = String(a.data.source_key);
            // Prefer human-friendly ref (e.g. A.8.6) over UUID for Events filtering.
            if (!controlRef && a.data?.kind === 'control') {
              const v = a.data?.control_ref || a.data?.name || '';
              if (v) controlRef = String(v);
              if (a.data?.control_id) controlId = String(a.data.control_id);
            }
            if (!clauseRef && a.data?.kind === 'clause') {
              const v = a.data?.clause_ref || a.data?.name || '';
              if (v) clauseRef = String(v);
              if (a.data?.clause_id) clauseId = String(a.data.clause_id);
            }
          }
          return {sourceKey, controlRef, clauseRef, clauseId, controlId};
        })();

        if (isLeaf || isFocused) {
          openEventsForLink(filters.sourceKey, filters.controlRef, ev, null, {clause: filters.clauseRef});
          return;
        }

        // Otherwise, zoom into the clicked node.
        if (d.children) clicked(d);
      });

  function arcCursor(d, coords = d.current) {
    if (!arcVisible(coords)) return 'default';
    // Leaves always open Events. Nodes with children zoom unless already focused.
    if (!d.children) return 'pointer';
    return (focus === d) ? 'pointer' : 'zoom-in';
  }

  path.style('cursor', (d) => arcCursor(d));

  path
      .append('title')
      .text((d) => {
        const names = d
            .ancestors()
            .reverse()
            .map((a) => a.data?.name)
            .filter(Boolean)
            .slice(1);
        const label = names.join(' → ');
        if (d?.data?.is_other) {
          const n = Number(d?.data?.other_count || (Array.isArray(d?.data?.other_items) ? d.data.other_items.length : 0)) || 0;
          return `${label}\nMerged: ${n} segments\nMapped events: ${Math.round(d.value || 0)}`;
        }
        return `${label}\nMapped events: ${Math.round(d.value || 0)}`;
      });

  const labels = g
      .append('g')
      .attr('class', 'sunburst-labels')
      .attr('pointer-events', 'none')
      .selectAll('text')
      .data(root.descendants().filter((d) => d.depth))
      .join('text')
      .attr('class', (d) => `sunburst-label sunburst-label-depth${d.depth}`)
      .attr('dy', '0.32em')
      .attr('text-anchor', 'middle')
      .style('opacity', (d) => (labelVisible(d) ? 1 : 0))
      .attr('transform', (d) => labelTransform(d.current))
      .text(labelText);

  // Labels: default to white (requested). With bolder segment colours and higher opacity,
  // this stays readable without switching some labels to black.
  labels
      .style('fill', 'rgba(255,255,255,0.92)')
      .style('paint-order', 'stroke')
      .style('stroke', (d) => (d?.depth === 1 ? 'rgba(0,0,0,0.30)' : 'none'))
      .style('stroke-width', (d) => (d?.depth === 1 ? '1.05px' : '0px'))
      .style('font-weight', (d) => (d?.data?.is_other ? '700' : '600'));

  labels
      .append('title')
      .text((d) => {
        const names = d
            .ancestors()
            .reverse()
            .map((a) => a.data?.name)
            .filter(Boolean)
            .slice(1);
        const label = names.join(' → ');
        return `${label}\nMapped events: ${Math.round(d.value || 0)}`;
      });

  // Ensure the center click target stays above arcs.
  // During zoom, some arcs can reach y0=0 and would otherwise intercept clicks.
  parent.raise();
  centerLabel.raise();

  centerBreadcrumb?.raise?.();
  centerHint?.raise?.();
  centerHintZoom?.raise?.();

  function clicked(p) {
    if (!p) return;
    focus = p;

    updateCenterUI(p);
    renderOtherPanel(p);
    parent
        .datum(p.parent || root)
        .style('cursor', p.parent ? 'pointer' : ((isDrill && drillBack) ? 'pointer' : 'default'));

    root.each((d) => {
      d.target = {
        x0:
          Math.max(0, Math.min(1, (d.x0 - p.x0) / (p.x1 - p.x0))) *
          2 *
          Math.PI,
        x1:
          Math.max(0, Math.min(1, (d.x1 - p.x0) / (p.x1 - p.x0))) *
          2 *
          Math.PI,
        y0: Math.max(0, d.y0 - p.y0),
        y1: Math.max(0, d.y1 - p.y0),
      };
    });

    // Update cursors immediately based on the new focus/targets.
    path.style('cursor', (d) => arcCursor(d, d.target));

    const t = g.transition().duration(750);

    path
        .transition(t)
        .tween('data', (d) => {
          const i = d3.interpolate(d.current, d.target);
          return (tt) => {
            d.current = i(tt);
          };
        })
        .attrTween('d', (d) => () => arc(d.current))
    // Use the *target* geometry to decide final visibility and pointer events.
    // (Using d.current here can leave hidden arcs clickable until the next click.)
        .attr('fill-opacity', (d) =>
        // Keep the bolder colour treatment during/after zoom as well.
        arcVisible(d.target) ? (d.depth === 1 ? 0.97 : 0.90) : 0
        )
        .attr('pointer-events', (d) => (arcVisible(d.target) ? 'auto' : 'none'));

    // Cursor semantics change with focus.
    path.style('cursor', (d) => arcCursor(d, d.target));

    labels
        .transition(t)
    // Use target geometry so labels don't get "stuck" visible after zoom.
        .style('opacity', (d) => (labelVisible(d, d.target) ? 1 : 0))
        .attrTween('transform', (d) => () => labelTransform(d.current));

    // Remember the current zoom level so it can be saved / restored.
    // In "Other" drill-down mode, don't overwrite the main-chart focus state.
    if (!isDrill) {
      if (focus === root || focus.depth === 0) {
        sunburstFocus = null;
      } else if (mode === 'sunburst_source') {
        const k = focus.data?.source_key;
        sunburstFocus = k ? {source_key: String(k)} : null;
      } else if (mode === 'sunburst_control') {
        const k = focus.data?.control_id;
        sunburstFocus = k ? {control_id: String(k)} : null;
      }
    }
  }

  // Apply saved initial focus (top-level segment) if provided.
  const focusNode = (() => {
    if (!initialFocus || typeof initialFocus !== 'object') return null;
    if (mode === 'sunburst_source') {
      const key = String(initialFocus.source_key || '').trim();
      if (!key) return null;
      return (root.children || []).find((n) => String(n.data?.source_key || '') === key) || null;
    }
    if (mode === 'sunburst_control') {
      const key = String(initialFocus.control_id || '').trim();
      if (!key) return null;
      return (root.children || []).find((n) => String(n.data?.control_id || '') === key) || null;
    }
    return null;
  })();
  if (focusNode) {
    // Clicking triggers the same transitions and state updates as user interaction.
    clicked(focusNode);
  }
}

async function loadTimeseries(days, opts = null) {
  const o = (opts && typeof opts === 'object') ? opts : {};
  const breakdown = String(o.breakdown || '').trim();
  const interval = String(o.interval || '').trim();
  const startTs = String(o.start_ts || '').trim();
  const endTs = String(o.end_ts || '').trim();

  const d = Math.max(1, Math.min(3650, Number(days || 7)));
  const r = getActiveRange();
  const key = (r ? `range:${r.from}:${r.to}` : `days:${d}`)
      + (breakdown ? `:bd:${breakdown}` : '')
      + (interval ? `:int:${interval}` : '')
      + (startTs ? `:sts:${startTs}` : '')
      + (endTs ? `:ets:${endTs}` : '');
  if (timeseriesCache.has(key)) return timeseriesCache.get(key);

  const u = new URL('/api/v1/charts/event_volume_timeseries', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if (r) {
    u.searchParams.set('start_date', r.from);
    u.searchParams.set('end_date', r.to);
    // Keep days as a fallback for older backends / partial ranges.
    u.searchParams.set('days', String(d));
  } else {
    u.searchParams.set('days', String(d));
  }

  if (breakdown) u.searchParams.set('breakdown', breakdown);
  if (interval) u.searchParams.set('interval', interval);
  if (startTs) u.searchParams.set('start_ts', startTs);
  if (endTs) u.searchParams.set('end_ts', endTs);

  const res = await apiGet(u.pathname + u.search);
  timeseriesCache.set(key, res);
  return res;

  // Update export payload (sunburst uses the same edge list as the graph).
  {
    const nodeById = new Map((graphData?.nodes || []).map((n) => [n.id, n]));
    const edges = (graphData?.links || []).map((l) => {
      const s = nodeById.get(l.source);
      const c = nodeById.get(l.target);
      return {
        source: s?.source || '',
        source_label: s?.label || s?.source || '',
        control_id: c?.control_id || '',
        control_ref: c?.label || '',
        control_title: c?.title || '',
        mapped_events: Number(l.value || 0),
      };
    }).filter((e) => e.mapped_events > 0);
    lastView = {mode, range: getActiveRange(), payload: {edges}};
  }
}

async function drawHistogram() {
  clearSvg();

  updateHistogramZoomButtons();

  const days = Math.max(1, Math.min(3650, Number(histDays?.value || 7)));

  // Zoom state: when active, the histogram becomes time-precision (hour/minute/second)
  // for the selected window.
  const zoom = (Array.isArray(histZoomStack) && histZoomStack.length > 0) ?
    histZoomStack[histZoomStack.length - 1] :
    null;

  // Stacked chart: each bucket is one bar, stacked by source (badge colours).
  // The backend includes per-source colours that respect user overrides.
  const reqInterval = zoom?.interval || 'day';
  const res = await loadTimeseries(days, {
    breakdown: 'source',
    interval: reqInterval,
    start_ts: zoom?.start_ts || '',
    end_ts: zoom?.end_ts || '',
  });

  const intervalEff = String(res?.interval || reqInterval || 'day').trim().toLowerCase() || 'day';
  const items = Array.isArray(res?.items) ? res.items : [];
  if (items.length === 0) {
    const w = getWrapWidth(680);
    sizeSvg(w, 360);
    svg
        .append('text')
        .attr('x', 14)
        .attr('y', 24)
        .attr('class', 'small-muted')
        .text('No events in the selected window.');
    return;
  }
  // If the backend coarsened the requested interval (eg too many buckets),
  // keep the zoom state consistent with what is actually displayed.
  if (zoom && intervalEff && zoom.interval !== intervalEff) {
    histZoomStack[histZoomStack.length - 1] = {
      ...zoom,
      interval: intervalEff,
      start_ts: String(res?.start_ts || zoom.start_ts || ''),
      end_ts: String(res?.end_ts || zoom.end_ts || ''),
    };
    updateHistogramZoomButtons();
  }

  // We'll set lastView after we determine the displayed stack keys,
  // so CSV/JSON exports can reflect exactly what is shown.

  const margin = {top: 62, right: 18, bottom: 122, left: 54};

  // Choose a compact bar width for very dense buckets (minute/second).
  const targetW = Math.max(680, getWrapWidth(680));
  const bar = Math.max(2, Math.min(12, Math.floor((targetW - margin.left - margin.right) / Math.max(1, items.length))));
  const width = Math.max(
      targetW,
      margin.left + margin.right + items.length * bar
  );
  const height = 520;
  sizeSvg(width, height);

  const buckets = items.map((d) => d.bucket || d.date);
  const x = d3
      .scaleBand()
      .domain(buckets)
      .range([margin.left, width - margin.right])

      .padding(items.length > 240 ? 0.02 : 0.1);

  // Determine the source stacks to show (top N by volume in the window).
  const sourcesMeta = Array.isArray(res?.sources) ? res.sources : [];
  const sourcesSorted = sourcesMeta
      .slice()
      .sort((a, b) => Number(b?.total || 0) - Number(a?.total || 0));

  const MAX_STACKS = 12;
  const primary = sourcesSorted.slice(0, MAX_STACKS);
  const remainder = sourcesSorted.slice(MAX_STACKS);
  const OTHER_KEY = '__other__';

  const stackKeys = primary.map((s) => String(s.source));
  const showOther = remainder.length > 0;
  if (showOther) stackKeys.push(OTHER_KEY);

  const labelByKey = new Map(primary.map((s) => [String(s.source), String(s.label || s.source)]));
  const colorByKey = new Map(primary.map((s) => [String(s.source), String(s.color || '#7c3aed')]));
  if (showOther) {
    labelByKey.set(OTHER_KEY, 'Other');
    colorByKey.set(OTHER_KEY, 'rgba(31,31,42,0.18)');
  }

  // Capture what is actually displayed for exports.
  lastView = {
    mode: 'histogram',
    range: getActiveRange(),
    payload: {
      days,
      interval: intervalEff,
      start_ts: String(res?.start_ts || zoom?.start_ts || ''),
      end_ts: String(res?.end_ts || zoom?.end_ts || ''),
      items,
      sources: sourcesMeta,
      stack_keys: stackKeys,
      label_by_key: Object.fromEntries(labelByKey.entries()),
      other_key: showOther ? OTHER_KEY : null,
    },
  };

  // Convert API items into a form d3.stack() can consume.
  const stackedData = items.map((it) => {
    const bucket = String(it?.bucket || it?.date || '').trim();
    const o = {
      bucket,
      total: Number(it.total || 0),
      mapped: Number(it.mapped || 0),
      unmapped: Number(it.unmapped || 0),
    };

    for (const k of primary) {
      o[String(k.source)] = 0;
    }

    let other = 0;
    const by = (it && typeof it.by_source === 'object' && it.by_source) ? it.by_source : {};
    for (const [src, n] of Object.entries(by)) {
      const key = String(src);
      const v = Number(n || 0);
      if (Number.isFinite(v) && v > 0) {
        if (labelByKey.has(key)) o[key] = (o[key] || 0) + v;
        else other += v;
      }
    }
    if (showOther) o[OTHER_KEY] = other;
    return o;
  });

  const yMax = d3.max(stackedData, (d) => d3.sum(stackKeys, (k) => Number(d[k] || 0))) || 1;
  const y = d3
      .scaleLinear()
      .domain([0, yMax])
      .nice()
      .range([height - margin.bottom, margin.top]);

  const gAxis = svg.append('g');

  const mainBottom = height - margin.bottom;

  const tickEvery = Math.max(1, Math.ceil(buckets.length / 12));
  const tickVals = buckets.filter((_, i) => i % tickEvery === 0);

  const fmtTime = (() => {
    if (intervalEff === 'day') return null;
    if (intervalEff === 'second') return d3.utcFormat('%H:%M:%S');
    // hour/minute
    return d3.utcFormat('%H:%M');
  })();

  const tickFormat = (d) => {
    if (intervalEff === 'day') return String(d).slice(5);
    const ms = histBucketToUtcMs(d, intervalEff);
    if (!Number.isFinite(ms)) return '';
    return fmtTime ? fmtTime(new Date(ms)) : '';
  };

  gAxis
      .append('g')
      .attr('class', 'viz-axis')
      .attr('transform', `translate(0, ${mainBottom})`)
      .call(
          d3
              .axisBottom(x)
              .tickValues(tickVals)
              .tickFormat(tickFormat)
      )
      .selectAll('text')
      .attr('text-anchor', 'end')
      .attr('transform', 'rotate(-35)');

  gAxis
      .append('g')
      .attr('class', 'viz-axis')
      .attr('transform', `translate(${margin.left}, 0)`)
      .call(d3.axisLeft(y).ticks(6));

  // Bars (stacked by source)
  const gBars = svg.append('g');

  const stack = d3.stack().keys(stackKeys);
  const series = stack(stackedData);

  const layer = gBars
      .selectAll('g.layer')
      .data(series)
      .enter()
      .append('g')
      .attr('class', 'layer')
      // Pastel fill + stronger outline per stack key (similar to Graph nodes)
      .attr('fill', (s) => {
        const base = colorByKey.get(s.key) || accent(0.55);
        return _histFillStrokeForBase(base).fill;
      });

  layer
      .selectAll('rect')
      .data((s) => s.map((d) => ({key: s.key, y0: d[0], y1: d[1], data: d.data})))
      .enter()
      .append('rect')
      .attr('x', (d) => x(d.data.bucket))
      .attr('y', (d) => y(d.y1))
      .attr('width', x.bandwidth())
      .attr('height', (d) => Math.max(0, y(d.y0) - y(d.y1)))
      .attr('stroke', (d) => {
        const base = colorByKey.get(d.key) || accent(0.55);
        return _histFillStrokeForBase(base).stroke;
      })
      .attr('stroke-width', 1)
      .attr('vector-effect', 'non-scaling-stroke')
      .style('cursor', (d) => ((d.y1 - d.y0) > 0 ? 'pointer' : 'default'))
      .on('click', (ev, d) => {
        ev.preventDefault();
        const n = Number(d?.y1 || 0) - Number(d?.y0 || 0);
        if (!(n > 0)) return;
        const bucket = String(d?.data?.bucket || '').trim();
        if (!bucket) return;
        const src = d.key === OTHER_KEY ? '' : String(d.key || '').trim();
        if (intervalEff === 'day') {
          // Filter Events page to the clicked day (start=end) and source.
          openEventsForLink(src, '', ev, {from: bucket, to: bucket});
          return;
        }

        // For hour/minute/second views, pass an exact time window so the Events page
        // can filter precisely (rather than collapsing back to day-only).
        const startMs = histBucketToUtcMs(bucket, intervalEff);
        if (!Number.isFinite(startMs)) return;
        const endMs = startMs + histStepMs(intervalEff);
        openEventsForLink(src, '', ev, {
          start_ts: new Date(startMs).toISOString(),
          end_ts: new Date(endMs).toISOString(),
        });
      })
      .append('title')
      .text((d) => {
        const label = labelByKey.get(d.key) || d.key;
        const n = Math.max(0, Math.round((d.y1 - d.y0) * 1000) / 1000);
        const t = d.data;
        return `${t.bucket}\n${label}: ${n}\nTotal: ${t.total}\nMapped: ${t.mapped}\nUnmapped: ${t.unmapped}`;
      });

  // Legend (wrap across rows so it doesn't overlap)
  const legend = svg
      .append('g')
      .attr('class', 'viz-legend')
      .attr('transform', `translate(${margin.left}, 16)`);

  const maxLegendW = Math.max(180, width - margin.left - margin.right);
  let lx = 0;
  let ly = 0;
  const rowH = 16;
  const sw = 12;
  const gap = 6;

  for (const k of stackKeys) {
    const label = labelByKey.get(k) || k;
    const approxW = sw + gap + 6.2 * String(label).length + 18;
    if (lx + approxW > maxLegendW) {
      lx = 0;
      ly += rowH;
    }

    const base = colorByKey.get(k) || accent(0.55);
    const cs = _histFillStrokeForBase(base);

    const g = legend.append('g').attr('transform', `translate(${lx}, ${ly})`);
    g.append('rect')
        .attr('x', 0)
        .attr('y', 0)
        .attr('width', sw)
        .attr('height', sw)
        .attr('rx', 3)
        .attr('ry', 3)
        .attr('fill', cs.fill)
        .attr('stroke', cs.stroke)
        .attr('stroke-width', 1)
        .attr('vector-effect', 'non-scaling-stroke');
    g.append('text')
        .attr('x', sw + gap)
        .attr('y', 10)
        .text(label);

    lx += approxW;
  }

  {
    const r = getActiveRange();
    const startLabel = String(res?.start_ts || zoom?.start_ts || '') || '';
    const endLabel = String(res?.end_ts || zoom?.end_ts || '') || '';
    const intervalLabel = intervalEff === 'day' ? 'Daily' : intervalEff.charAt(0).toUpperCase() + intervalEff.slice(1);
    const hint = 'Shift-drag (or toggle Zoom mode) on the chart to select a time window.';
    if (zoom && startLabel && endLabel) {
      setVizCaption(vizMode, `${intervalLabel} volume from ${startLabel} to ${endLabel}. ${hint} Use Back to return.`);
    } else if (r) {
      setVizCaption(vizMode, `Daily volume from ${r.from} to ${r.to} (${days} days). ${hint}`);
    } else {
      setVizCaption(vizMode, `Daily volume for the last ${days} days. ${hint}`);
    }
  }

  // Main-chart brush (Grafana/Kibana-style range selector)
  // Enabled via Shift (temporarily) or via the "Zoom mode" toggle.
  {
    const bandIndexForPx = (px) => {
      const x0 = x.range()[0];
      const step = x.step();
      const i = Math.floor((px - x0) / Math.max(1e-6, step));
      return Math.max(0, Math.min(buckets.length - 1, i));
    };

    const brush = d3.brushX()
        .extent([[margin.left, margin.top], [width - margin.right, mainBottom]])
        .on('end', (ev) => {
          const sel = ev?.selection;
          if (!sel || !Array.isArray(sel)) return;
          const [px0, px1] = sel;
          if (!(px1 > px0)) return;

          const i0 = bandIndexForPx(px0);
          const i1 = Math.max(i0, Math.min(buckets.length - 1, Math.ceil((px1 - x.range()[0]) / x.step()) - 1));

          // If we're already at second-level precision, require more than one bucket.
          if (intervalEff === 'second' && i1 <= i0) {
            return;
          }

          const b0 = buckets[i0];
          const b1 = buckets[i1];
          const startMs = histBucketToUtcMs(b0, intervalEff);
          const endMs = histBucketToUtcMs(b1, intervalEff);
          if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return;
          const endExclMs = endMs + histStepMs(intervalEff);

          // Progressive drill-down: day -> hour -> minute -> second (when feasible).
          const cand = histNextFinerInterval(intervalEff);
          const candBuckets = Math.ceil((endExclMs - startMs) / histStepMs(cand));
          const nextInterval = (candBuckets > HIST_MAX_BUCKETS) ? intervalEff : cand;

          histZoomStack = Array.isArray(histZoomStack) ? histZoomStack : [];
          histZoomStack.push({
            start_ts: new Date(startMs).toISOString(),
            end_ts: new Date(endExclMs).toISOString(),
            interval: nextInterval,
          });
          updateHistogramZoomButtons();
          syncAddressBarToShareState();

          // Re-render histogram for the zoomed range.
          drawHistogram().catch((err) => {
            toast(status, `Failed to load histogram zoom: ${String(err)}`, 'danger');
          });
        });

    const brushG = svg.append('g')
        .attr('class', 'hist-main-brush')
        .call(brush);

    // Provide a setter so global keyboard / button handlers can enable/disable it
    // without needing to re-render the chart.
    _setHistogramBrushEnabled = (enabled) => {
      const on = !!enabled;
      brushG.classed('hist-brush-enabled', on);
      // d3.brush attaches pointer-events to the overlay rect explicitly.
      // So disabling the parent <g> is not enough; we must turn off pointer-events
      // on the brush primitives themselves, otherwise they will steal clicks from bars.
      brushG.selectAll('.overlay,.selection,.handle')
          .style('pointer-events', on ? 'all' : 'none');
      brushG.selectAll('.overlay').style('cursor', on ? 'crosshair' : 'default');
      if (!on) {
        try { brushG.call(brush.move, null); } catch { /* ignore */ }
      }
    };

    updateHistogramBrushEnabled();
  }
}

function drawActive() {
  activateGraphDataForMode(vizMode);
  if (!graphData && vizMode !== 'histogram') return;

  // Sunburst-only UI: ensure the "Other" breakdown panel doesn't linger when switching views.
  if (!['sunburst_source', 'sunburst_control'].includes(vizMode)) {
    setSunburstOtherPanel('');
  }

  try {
    if (vizMode === 'graph' || vizMode === 'graph_clause_events' || vizMode === 'graph_clause_control') {
      drawGraph(getGraphMinEdge(), getGraphTopSources(), getGraphTopControls());
      return;
    }

    if (vizMode === 'heatmap' || vizMode === 'heatmap_clause_events') {
      drawHeatmap();
      return;
    }

    if (vizMode === 'sunburst_source' || vizMode === 'sunburst_control') {
      drawSunburst(vizMode, sunburstFocus);
      return;
    }

    if (vizMode === 'histogram') {
      // async: fire-and-forget; errors are shown via toast below
      drawHistogram().catch((err) => {
        toast(status, `Failed to load histogram data: ${String(err)}`, 'danger');
      });
    }
  } catch (err) {
    toast(status, `Failed to render visualisation: ${String(err)}`, 'danger');
  }
}


gapQ?.addEventListener('input', () => {
  if (!controlStats && coverageGapsOpen()) {
    loadControlStatsForGaps().catch((err) => toast(status, `Failed to load controls: ${String(err)}`, 'danger'));
    return;
  }
  renderGaps();
});
(document.getElementById('vizReloadGaps') || document.getElementById('reload'))?.addEventListener('click', () => {
  loadControlStatsForGaps({force: true}).catch((err) => toast(status, `Failed to load controls: ${String(err)}`, 'danger'));
});
coverageGapsPanel?.addEventListener('shown.bs.collapse', () => {
  if (!controlStats) loadControlStatsForGaps().catch((err) => toast(status, `Failed to load controls: ${String(err)}`, 'danger'));
});

function normalizeRangeInputs() {
  if (!dateFrom || !dateTo) return false;
  // If the user typed dd/mm/yyyy, convert it before validating.
  _syncIsoFromDmy();
  const r = getActiveRange();
  if (!r) return false;
  if (dateFrom.value !== r.from || dateTo.value !== r.to) {
    _setRangeIso(r.from, r.to);
  } else {
    // Still keep visible fields aligned.
    _syncDmyFromIso();
  }
  return true;
}

dateApply?.addEventListener('click', () => {
  if (!normalizeRangeInputs()) {
    toast(status, 'Please choose both start and end dates.', 'warning');
    return;
  }
  // Keep days shortcut in sync when the user applies a new range.
  syncHistDaysFromRange();
  load();
});

const debRangeApply = autoApplyFilters ?
  debounce(() => {
    if (!normalizeRangeInputs()) return;
    load();
  }, 500) :
  null;

resetBtn?.addEventListener('click', () => {
  // Cancel any pending auto-apply (so it doesn't run after resetting).
  debRangeApply?.cancel?.();

  if (gapQ) gapQ.value = '';

  // Restore numeric controls (undo URL overrides / edits).
  if (heatmapTopSources) heatmapTopSources.value = DEFAULT_NUMERIC.heatmapTopSources;
  if (heatmapTopControls) heatmapTopControls.value = DEFAULT_NUMERIC.heatmapTopControls;
  if (graphTopSources) graphTopSources.value = DEFAULT_NUMERIC.graphTopSources;
  if (graphTopControls) graphTopControls.value = DEFAULT_NUMERIC.graphTopControls;
  if (graphMinEdge) graphMinEdge.value = DEFAULT_NUMERIC.graphMinEdge;

  // Restore default view (saved preference if present; otherwise Graph + last 7 days).
  histZoomStack = [];
  updateHistogramZoomButtons();
  sunburstFocus = getDefaultSunburstFocus();
  initDateRangeDefaults();
  setViz(getDefaultVizMode(), {skipDraw: true});

  // Remove share-link query params (mode/range/etc) from the address bar.
  clearVizQueryOverrides();

  renderGaps();
  load();
});

const onRangeFieldChange = () => {
  if (_suppressRangeSync) return;
  syncHistDaysFromRange();
  if (debRangeApply) debRangeApply();
};

dateFrom?.addEventListener('change', onRangeFieldChange);
dateTo?.addEventListener('change', onRangeFieldChange);

// Days shortcut: always keep the date range inputs aligned as the user edits.
histDays?.addEventListener('input', () => {
  if (_suppressRangeSync) return;
  syncRangeFromHistDays();
  if (debRangeApply) debRangeApply();
});

vizSelect?.addEventListener('change', () => {
  const next = vizSelect.value;
  setViz(next);
});

saveDefaultViz?.addEventListener('click', async () => {
  const r = getActiveRange();
  if (!r) {
    toast(status, 'Please choose a valid date range first.', 'warning');
    return;
  }

  // Prefer a relative range (last N days) when the range ends today (UTC), so the default stays fresh.
  const today = isoTodayUtc();
  const days = diffDaysInclusive(r.from, r.to);

  const defaultState = {
    mode: vizMode,
    date_range: (days && r.to === today) ?
      {days} :
      {start_date: r.from, end_date: r.to},
  };

  if (vizMode === 'sunburst_source' || vizMode === 'sunburst_control') {
    if (sunburstFocus && typeof sunburstFocus === 'object') {
      defaultState.sunburst_focus = sunburstFocus;
    }
  }

  try {
    const updated = await apiPatch('/api/v1/me/preferences', {default_visualisation: defaultState});
    window.keenPreferences = updated || window.keenPreferences || {};
    defaultVizPref = (updated && updated.default_visualisation && typeof updated.default_visualisation === 'object') ?
      updated.default_visualisation :
      defaultVizPref;
    // Success feedback should be transient.
    toast(status, 'Saved as your default visualisation.', 'success', 2800);
  } catch (err) {
    toast(status, `Could not save default visualisation: ${String(err)}`, 'danger');
  }
});

heatmapApply?.addEventListener('click', () => {
  if (!(vizMode === 'heatmap' || vizMode === 'heatmap_clause_events')) return;
  drawHeatmap();
});

graphApply?.addEventListener('click', () => {
  if (!(vizMode === 'graph' || vizMode === 'graph_clause_events' || vizMode === 'graph_clause_control')) return;
  drawActive();
});

exportCsvBtn?.addEventListener('click', () => {
  try {
    const mode = lastView?.mode || vizMode;
    const r = lastView?.range || getActiveRange();
    const stamp = r ? `${r.from}_to_${r.to}` : isoTodayUtc();

    if (mode === 'histogram') {
      const payload = lastView?.payload || {};
      const items = Array.isArray(payload.items) ? payload.items : [];
      const stackKeys = Array.isArray(payload.stack_keys) ? payload.stack_keys : [];
      const labelByKey = (payload.label_by_key && typeof payload.label_by_key === 'object') ? payload.label_by_key : {};
      const otherKey = String(payload.other_key || '__other__');
      const primary = new Set(stackKeys.filter((k) => String(k) !== otherKey).map((k) => String(k)));

      // Flatten by_source objects into one column per displayed stack.
      const rows = items.map((it) => {
        const out = {
          bucket: it?.bucket || it?.date || '',
          total: Number(it?.total || 0),
        };

        const by = (it && typeof it.by_source === 'object' && it.by_source) ? it.by_source : {};

        if (stackKeys.length > 0) {
          for (const k0 of stackKeys) {
            const k = String(k0);
            const col = String(labelByKey?.[k] || k);
            if (k === otherKey) {
              let sum = 0;
              for (const [kk, vv] of Object.entries(by)) {
                const key = String(kk);
                if (!primary.has(key)) sum += Number(vv || 0);
              }
              out[col] = sum;
            } else {
              out[col] = Number(by?.[k] || 0);
            }
          }
        } else {
          for (const [k, v] of Object.entries(by)) {
            out[String(k)] = Number(v || 0);
          }
        }

        return out;
      });

      downloadCsv(`keen_histogram_${stamp}.csv`, rows);
      return;
    }

    const rows = lastView?.payload?.cells || lastView?.payload?.edges || [];
    downloadCsv(`keen_${mode}_${stamp}.csv`, rows);
  } catch (err) {
    toast(status, `Export failed: ${String(err)}`, 'danger');
  }
});

exportJsonBtn?.addEventListener('click', () => {
  try {
    const r = getActiveRange();
    downloadJson('keen_visualisation.json', {
      exported_at: new Date().toISOString(),
      mode: vizMode,
      range: r,
      graph: graphData,
      controls: controlStats,
      last_view: lastView,
    });
  } catch (err) {
    toast(status, `Export failed: ${String(err)}`, 'danger');
  }
});

exportSvgBtn?.addEventListener('click', () => {
  try {
    const mode = lastView?.mode || vizMode;
    const r = lastView?.range || getActiveRange();
    const stamp = r ? `${r.from}_to_${r.to}` : isoTodayUtc();
    downloadCurrentSvg(`keen_${mode}_${stamp}.svg`);
  } catch (err) {
    toast(status, `Export failed: ${String(err)}`, 'danger');
  }
});

copyVizLinkBtn?.addEventListener('click', async () => {
  const u = buildShareUrl();
  const ok = await copyToClipboard(u);
  // Always auto-hide this feedback so it doesn't stick around.
  toast(status,
    ok ? 'Copied link to clipboard.' : 'Could not copy link.',
    ok ? 'success' : 'warning',
    ok ? 2400 : 3600
  );
});


histZoomModeBtn?.addEventListener('click', () => {
  histZoomModeOn = !histZoomModeOn;
  updateHistogramBrushEnabled();

  if (vizMode !== 'histogram') return;
  if (histZoomModeOn) {
    toast(status, 'Zoom mode enabled: drag on the histogram to select a time window. Turn it off to click bars.', 'secondary', 5200);
  } else {
    toast(status, 'Zoom mode disabled. Tip: hold Shift and drag to zoom temporarily.', 'secondary', 4200);
  }
});


histApply?.addEventListener('click', () => {
  // Applying a new day-based range resets histogram zoom.
  histZoomStack = [];
  updateHistogramZoomButtons();
  syncAddressBarToShareState();
  // Day-based changes implicitly exit zoom/brush mode.
  histZoomModeOn = false;
  histBrushShiftActive = false;
  updateHistogramBrushEnabled();
  if (!_suppressRangeSync) syncRangeFromHistDays();
  if (!normalizeRangeInputs()) return;
  load();
});

histBack?.addEventListener('click', () => {
  if (!Array.isArray(histZoomStack) || histZoomStack.length < 1) return;
  histZoomStack.pop();
  updateHistogramZoomButtons();
  syncAddressBarToShareState();
  if (vizMode === 'histogram') {
    drawHistogram().catch((err) => toast(status, `Failed to render histogram: ${String(err)}`, 'danger'));
  }
});

histResetZoom?.addEventListener('click', () => {
  if (!Array.isArray(histZoomStack) || histZoomStack.length < 1) return;
  histZoomStack = [];
  updateHistogramZoomButtons();
  syncAddressBarToShareState();
  if (vizMode === 'histogram') {
    drawHistogram().catch((err) => toast(status, `Failed to render histogram: ${String(err)}`, 'danger'));
  }
});

// Auto-apply visualisation controls as fields change (user preference).
if (autoApplyFilters) {
  const debHeatmap = debounce(() => {
    if (!(vizMode === 'heatmap' || vizMode === 'heatmap_clause_events')) return;
    drawHeatmap();
  }, 250);

  heatmapTopSources?.addEventListener('input', () => debHeatmap());
  heatmapTopControls?.addEventListener('input', () => debHeatmap());

  const debGraph = debounce(() => {
    if (!(vizMode === 'graph' || vizMode === 'graph_clause_events' || vizMode === 'graph_clause_control')) return;
    drawActive();
  }, 250);

  graphTopSources?.addEventListener('input', () => debGraph());
  graphTopControls?.addEventListener('input', () => debGraph());
  graphMinEdge?.addEventListener('input', () => debGraph());
}

// Histogram brush hotkey (Shift-drag to zoom).
// We keep this global so it works even when the SVG re-renders.
window.addEventListener('keydown', (e) => {
  if (e.key !== 'Shift') return;
  if (histBrushShiftActive) return;
  histBrushShiftActive = true;
  updateHistogramBrushEnabled();
});

window.addEventListener('keyup', (e) => {
  if (e.key !== 'Shift') return;
  if (!histBrushShiftActive) return;
  histBrushShiftActive = false;
  updateHistogramBrushEnabled();
});

window.addEventListener('blur', () => {
  if (!histBrushShiftActive) return;
  histBrushShiftActive = false;
  updateHistogramBrushEnabled();
});

window.addEventListener('resize', () => {
  drawActive();
});

async function load() {
  // Persist current range (if present) so Reload / refresh keeps it.
  if (normalizeRangeInputs());

  // Histogram data is range-sensitive; keep the cache small.
  timeseriesCache.clear();
  clearRangeSensitiveCaches();

  // Validate the saved/query-selected mode before deciding what to fetch.
  if (!isAvailableVizMode(vizMode)) vizMode = firstAvailableVizMode();

  toast(status, 'Loading visualisation data…', 'secondary');
  if (vizMode !== 'histogram') {
    showTableLoading(topSourcesEl, 2, `Loading ${vizRelationshipLabels(vizMode).leftPlural}…`);
    showTableLoading(topControlsEl, 2, `Loading ${vizRelationshipLabels(vizMode).rightPlural}…`);
    showVizLoading('Loading visualisation data…');
  } else {
    if (topSourcesEl) topSourcesEl.innerHTML = '<tr><td colspan="2" class="small-muted p-3">Evidence distribution loads when a graph, heatmap, or sunburst is selected.</td></tr>';
    if (topControlsEl) topControlsEl.innerHTML = '<tr><td colspan="2" class="small-muted p-3">Evidence distribution loads when a graph, heatmap, or sunburst is selected.</td></tr>';
  }
  renderGaps();

  try {
    const jobs = [];
    if (vizMode !== 'histogram') jobs.push(ensureGraphDatasetForMode(vizMode, {force: true}));
    if (coverageGapsOpen()) jobs.push(loadControlStatsForGaps({force: true}));
    await Promise.all(jobs);

    // Draw only after the selected graph dataset is present. Clause datasets are
    // now fetched on demand instead of blocking every page load.
    setViz(vizMode);

    toast(status, 'Loaded.', 'success');
    if (statusHideTimer) clearTimeout(statusHideTimer);
    statusHideTimer = setTimeout(() => {
      if (status) status.style.display = 'none';
    }, 1800);
  } catch (err) {
    toast(status, `Failed to load visualisation: ${String(err)}`, 'danger');
    if (gapsEl && coverageGapsOpen()) gapsEl.textContent = 'Failed to load controls.';
    if (topSourcesEl) topSourcesEl.innerHTML = `<tr><td colspan="2" class="small-muted p-3">Failed to load ${esc(vizRelationshipLabels(vizMode).leftPlural)}.</td></tr>`;
    if (topControlsEl) topControlsEl.innerHTML = `<tr><td colspan="2" class="small-muted p-3">Failed to load ${esc(vizRelationshipLabels(vizMode).rightPlural)}.</td></tr>`;
  }
}


function embeddedVisualisationPane() {
  return document.getElementById('embeddedVisualisationsPane') ||
    document.querySelector('[data-keen-visualisation-mount]')?.closest('.tab-pane') ||
    null;
}

function embeddedVisualisationTabButton(pane = embeddedVisualisationPane()) {
  const paneId = String(pane?.id || '').trim();
  if (!paneId) return null;
  const escapedPaneId = (window.CSS && typeof window.CSS.escape === 'function') ? window.CSS.escape(paneId) : paneId.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  const selector = `[data-bs-target="#${escapedPaneId}"], [href="#${escapedPaneId}"]`;
  return document.querySelector(selector);
}

function activateEmbeddedVisualisationTabFromQuery() {
  try {
    const requested = new URLSearchParams(location.search || '').get('tab');
    if (requested !== 'visualisations') return;
    const btn = embeddedVisualisationTabButton();
    if (btn && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(btn).show();
  } catch {
    // ignore
  }
}

function setEmbeddedVisualisationTabQuery(active) {
  try {
    const u = new URL(location.href);
    if (active) u.searchParams.set('tab', 'visualisations');
    else if (u.searchParams.get('tab') === 'visualisations') u.searchParams.delete('tab');
    history.replaceState({}, '', u.pathname + (u.searchParams.toString() ? `?${u.searchParams.toString()}` : '') + (u.hash || ''));
  } catch {
    // ignore
  }
}

let visualisationStarted = false;
function startVisualisation() {
  if (visualisationStarted) return;
  visualisationStarted = true;
  load();
}

activateEmbeddedVisualisationTabFromQuery();

const embeddedPane = embeddedVisualisationPane();
const embeddedTabButton = embeddedVisualisationTabButton(embeddedPane);
if (embeddedPane && !embeddedPane.classList.contains('active') && !embeddedPane.classList.contains('show')) {
  embeddedTabButton?.addEventListener('shown.bs.tab', () => {
    setEmbeddedVisualisationTabQuery(true);
    startVisualisation();
  });
  const otherTabButtons = Array.from(document.querySelectorAll('[data-bs-toggle="tab"]')).filter((btn) => btn !== embeddedTabButton);
  for (const btn of otherTabButtons) {
    btn.addEventListener('shown.bs.tab', () => setEmbeddedVisualisationTabQuery(false));
  }
} else {
  if (embeddedPane) setEmbeddedVisualisationTabQuery(true);
  startVisualisation();
}

