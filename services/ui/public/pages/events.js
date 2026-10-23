import {
  initNavbar,
  apiGet,
  apiPost,
  esc,
  fmtTs,
  tsSortKey,
  toast,
  isoDateToDmy,
  dmyToIsoDate,
  wireIsoDmyDateField,
  qs,
  qbool,
  qint,
  setQuery,
  enableTableSorting,
  shorten,
  sourceBadgeHtml,
  sourceLabel,
  upstreamLinkHtml,
  getCurrentFramework,
  withFramework,
  showTableLoading,
  collectOffsetPagerButtons,
  setOffsetPagerDisabled,
  offsetPagerState,
  wireOffsetPagerButtons,
} from '/app.js';
import {
  enhanceFilterPillMultiSelect,
  selectedFilterPillValues,
  setFilterPillOptions,
} from '/pages/filter-pill-multiselect.js';
import { createConditionEditor, describeCondition } from '/pages/event-filter-conditions.js';
import { createEventPanel } from '/pages/event-panel.js';
import { createEventSelection } from '/pages/event-selection.js';

const me = await initNavbar();
const framework = getCurrentFramework();
enableTableSorting();

// Admin-only shortcut: jump straight to the Diary section.
// Kept out of the HTML so non-admin users never see it.
try {
  const actions = document.getElementById('eventsActions');
  if (actions && me?.is_admin) {
    const a = document.createElement('a');
    a.className = 'btn btn-outline-primary';
    a.href = withFramework('/admin.html#diary', framework);
    a.textContent = 'Add diary entry';
    actions.prepend(a);
  }
} catch {
  // non-fatal
}

const autoApplyFilters = !!me?.preferences?.auto_apply_filters;

const status = document.getElementById('status');
const tbody = document.getElementById('rows');
const meta = document.getElementById('resultMeta');

const pageTitleEl = document.getElementById('pageTitle') || document.querySelector('main h1');

function controlHeading(ref) {
  const r = String(ref || '').trim();
  if (!r) return '';
  const title = String(controlsIndex?.[r]?.title || '').trim();
  // Include BOTH reference + title when we have it.
  return title ? `${r} - ${title}` : r;
}

function splitList(value) {
  return String(value || '')
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);
}

function describeList(values, one, many) {
  if (values.length === 1) return one(values[0]);
  return `${values.length} ${many}`;
}

function updatePageTitle({ source, control, clause } = {}) {
  const sources = splitList(source !== undefined ? source : state.source);
  const controls = splitList(control !== undefined ? control : state.control);
  const clauses = splitList(clause !== undefined ? clause : state.clause);
  const rules = splitList(state.rule);

  let heading = 'Events';
  if (sources.length || controls.length || clauses.length || rules.length) {
    const parts = [];
    if (sources.length)
      parts.push(
        describeList(sources, (src) => String(sourceLabel(src, sourceMeta) || src), 'sources'),
      );
    if (controls.length)
      parts.push(describeList(controls, (ref) => `control ${controlHeading(ref)}`, 'controls'));
    if (clauses.length) parts.push(describeList(clauses, (ref) => `clause ${ref}`, 'clauses'));
    if (rules.length) parts.push(describeList(rules, (id) => `rule ${id}`, 'mapping rules'));
    heading = `Events for ${parts.join(' and ')}`;
  }

  if (pageTitleEl) pageTitleEl.textContent = heading;
  document.title = `Keen - ${heading}`;
}

function updatePageTitleFromInputs() {
  updatePageTitle({
    source: selectedFilterPillValues(selSource).join(','),
    control: selectedFilterPillValues(selControl).join(','),
  });
}

const selSource = document.getElementById('source');
const selControl = document.getElementById('control');
const selRule = document.getElementById('rule');
const chkUnmapped = document.getElementById('unmapped');
const conditionEditor = createConditionEditor({
  host: document.getElementById('conditions'),
  addButton: document.getElementById('addCondition'),
  onChange: () => {
    if (autoApplyFilters) reloadWithFacets(true);
  },
});
const selLimit = document.getElementById('limit');

const startDate = document.getElementById('startDate');
const endDate = document.getElementById('endDate');

// Visible dd/mm/yyyy fields (the hidden inputs above keep YYYY-MM-DD for API/URLs)
const startDateDmy = document.getElementById('startDateDmy');
const endDateDmy = document.getElementById('endDateDmy');
const startDatePick = document.getElementById('startDatePick');
const endDatePick = document.getElementById('endDatePick');

const exportCsvBtn = document.getElementById('exportCsv');
const exportJsonBtn = document.getElementById('exportJson');
const exportNdjsonBtn = document.getElementById('exportNdjson');

// One shared offset-pager wiring path covers header buttons (#prev/#next)
// and footer buttons ([data-pager]). This keeps Events aligned with the
// homepage Evidence explorer pagination instead of relying on page-specific
// button discovery.
const pagerButtons = collectOffsetPagerButtons();

// A link from the navbar or Home search carries its text as q; it becomes a
// text filter over any field, which is what the former search box did.
function initialConditions() {
  const params = new URLSearchParams(location.search);
  const conditions = params.getAll('f');
  const q = String(params.get('q') || '').trim();
  if (q) conditions.unshift(`any:contains:${q}`);
  return conditions;
}

let state = {
  source: qs('source', ''),
  control: qs('control', ''),
  clause: qs('clause', ''),
  rule: qs('rule', ''),
  conditions: initialConditions(),
  unmapped: qbool('unmapped', false),
  start_date: qs('start_date', ''),
  end_date: qs('end_date', ''),
  // Optional precise time window (UTC, end exclusive)
  start_ts: parseIsoTs(qs('start_ts', '')),
  end_ts: parseIsoTs(qs('end_ts', '')),
  limit: qint('limit', 50),
  offset: qint('offset', 0),
  total: 0,
  framework,
};

let sourceMeta = {};
let controlsIndex = {};
const eventPanel = createEventPanel({ framework, getSourceMeta: () => sourceMeta });
const selection = createEventSelection({
  me,
  framework,
  tbody,
  bar: document.getElementById('selectionBar'),
  menu: document.getElementById('rowMenu'),
  statusEl: status,
});
const columnCount = 7 + selection.columns;

// The filters a request carries, as the API reads them.
function filterParams() {
  const p = new URLSearchParams();
  if (framework) p.set('framework', framework);
  if (state.source) p.set('source', state.source);
  if (state.control) p.set('control', state.control);
  if (state.clause) p.set('clause', state.clause);
  if (state.rule) p.set('rule', state.rule);
  for (const condition of state.conditions) p.append('f', condition);
  if (state.unmapped) p.set('unmapped', 'true');
  if (state.start_date) p.set('start_date', state.start_date);
  if (state.end_date) p.set('end_date', state.end_date);
  if (state.start_ts) p.set('start_ts', state.start_ts);
  if (state.end_ts) p.set('end_ts', state.end_ts);
  return p;
}

// setQuery writes one value per key; the text filters repeat theirs.
function syncConditionsToUrl() {
  const url = new URL(String(location.href), document.baseURI);
  url.searchParams.delete('f');
  // Search text arriving as q is already one of the conditions.
  url.searchParams.delete('q');
  for (const condition of state.conditions) url.searchParams.append('f', condition);
  history.replaceState({}, '', url.pathname + url.search + url.hash);
}

function picker(el, singular, plural, description) {
  return enhanceFilterPillMultiSelect(el, {
    title: `Choose ${plural}`,
    description,
    buttonLabel: `Choose ${plural}`,
    singular,
    plural,
    emptyText: `All ${plural}`,
    emptySelectedText: `No ${singular} filters selected. All ${plural} are included.`,
    noMatchText: `No ${plural} match your filter.`,
    searchPlaceholder: `Filter ${plural}`,
    options: [],
    selectedValues: splitList(state[el.id]),
  });
}
picker(
  selSource,
  'source',
  'sources',
  'Select one or more sources to include. Clear the selection to include every source.',
);
picker(
  selControl,
  'control',
  'controls',
  'Select one or more controls; an event matches when it is mapped to any of them.',
);
picker(
  selRule,
  'mapping rule',
  'mapping rules',
  'Select one or more mapping rules; an event matches when any of them mapped it to a control.',
);

// A chosen value stays available until its facet count returns.
function keepingSelected(options, selected, describe) {
  const known = new Set(options.map((opt) => opt.value));
  return [
    ...selected.filter((value) => !known.has(value)).map((value) => describe(value)),
    ...options,
  ];
}

function setPickerOptions(el, options, selected, describe) {
  setFilterPillOptions(el, keepingSelected(options, selected, describe), {
    selectedValues: selected,
    pruneSelected: false,
  });
}

function isIsoDateStr(v) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(v || '').trim());
}

// Parse a date entered as YYYY-MM-DD.
// Returns YYYY-MM-DD or "" if invalid.
function parseIsoDate(v) {
  const raw = String(v || '').trim();
  if (!raw) return '';
  return isIsoDateStr(raw) ? raw : '';
}

// Parse an ISO8601 timestamp (optionally without timezone) and normalise to UTC ISO.
// Returns an ISO string (e.g. 2026-01-23T12:34:56.789Z) or "" if invalid.
function parseIsoTs(v) {
  const raw0 = String(v || '').trim();
  if (!raw0) return '';

  let raw = raw0;
  // If the user supplied a timestamp without timezone, treat it as UTC.
  if (/^\d{4}-\d{2}-\d{2}T/.test(raw) && !/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw)) {
    raw = `${raw}Z`;
  }

  const ms = Date.parse(raw);
  if (!Number.isFinite(ms)) return '';
  return new Date(ms).toISOString();
}

// Normalize a YYYY-MM-DD date range.
function normalizeDateRange(a, b) {
  const s = (a || '').trim();
  const e = (b || '').trim();
  if (!s && !e) return { start: '', end: '' };
  // Compare YYYY-MM-DD lexicographically.
  if (s && e && s > e) return { start: e, end: s };
  return { start: s, end: e };
}

function syncInputs() {
  chkUnmapped.checked = state.unmapped;
  selLimit.value = String(state.limit);
  conditionEditor.set(state.conditions);
  // Hidden native date inputs keep YYYY-MM-DD values.
  if (startDate) startDate.value = state.start_date || '';
  if (endDate) endDate.value = state.end_date || '';
  // Visible fields show dd/mm/yyyy.
  if (startDateDmy) startDateDmy.value = isoDateToDmy(state.start_date || '');
  if (endDateDmy) endDateDmy.value = isoDateToDmy(state.end_date || '');
  // control/source filled after options load
}

async function loadSources() {
  try {
    const data = await apiGet('/api/v1/sources/meta');
    const items = (data.items || []).filter((x) => x.source);

    // Build a lightweight source metadata map without triggering source aggregates.
    sourceMeta = {};
    for (const x of items) {
      const src = String(x.source || '').trim();
      if (!src) continue;
      sourceMeta[src] = {
        source: src,
        label: String(x.label || src).trim() || src,
        color: String(x.color || '').trim(),
      };
    }
    setPickerOptions(
      selSource,
      items.map((x) => {
        const src = x.source;
        const label = String(x.label || sourceLabel(src, sourceMeta) || src);
        return { value: src, label: label && label !== src ? `${label} (${src})` : src };
      }),
      splitList(state.source),
      (value) => ({ value, label: value }),
    );
  } catch {
    // The chosen sources stay available without the source register.
    setPickerOptions(selSource, [], splitList(state.source), (value) => ({ value, label: value }));
  }
}

async function loadControls() {
  try {
    const data = await apiGet(
      `/api/v1/controls?limit=5000&framework=${encodeURIComponent(framework)}`,
    );
    const items = (data.items || []).filter((c) => c.ref);

    controlsIndex = {};
    for (const c of items) {
      const ref = String(c.ref || '').trim();
      if (!ref) continue;
      controlsIndex[ref] = { ref, title: String(c.title || '').trim(), type: c.type || 'control' };
    }

    setPickerOptions(
      selControl,
      items.map((c) => ({
        value: c.ref,
        label: c.title ? `${c.ref} - ${c.title}` : c.ref,
        title: `${c.type || 'control'}: ${c.title ? `${c.ref} - ${c.title}` : c.ref}`,
      })),
      splitList(state.control),
      (value) => ({ value, label: `Current filter: ${value}` }),
    );
  } catch {
    // The chosen controls stay available without the catalogue.
    setPickerOptions(selControl, [], splitList(state.control), (value) => ({
      value,
      label: `Current filter: ${value}`,
    }));
  }
}

function renderSourceOptions(facets) {
  const rows = (Array.isArray(facets) ? facets : []).filter(
    (r) => r && r.source && Number(r.count || 0) > 0,
  );
  setPickerOptions(
    selSource,
    rows.map((r) => {
      const src = String(r.source);
      const label = String(sourceLabel(src, sourceMeta) || src);
      const text = label && label !== src ? `${label} (${src})` : src;
      return { value: src, label: `${text} (${Number(r.count || 0)})` };
    }),
    splitList(state.source),
    // Preserve current selection even if it yields zero results under other filters.
    (value) => ({ value, label: `${sourceLabel(value, sourceMeta) || value} (0)` }),
  );
}

function renderControlOptions(facets) {
  const rows = (Array.isArray(facets) ? facets : []).filter(
    (r) => r && String(r.ref || '').trim() && Number(r.count || 0) > 0,
  );
  setPickerOptions(
    selControl,
    rows.map((r) => {
      const ref = String(r.ref).trim();
      const title = String(r.title || controlsIndex[ref]?.title || '');
      const type = String(r.type || controlsIndex[ref]?.type || 'control');
      // Without the catalogue, the results still name the control.
      if (title && !controlsIndex[ref]) controlsIndex[ref] = { ref, title, type };
      const label = title ? `${ref} - ${title}` : ref;
      return {
        value: ref,
        label: `${label} (${Number(r.count || 0)})`,
        title: `${type}: ${label}`,
      };
    }),
    splitList(state.control),
    (value) => ({ value, label: `Current filter: ${value} (0)` }),
  );
}

function renderRuleOptions(facets) {
  const rows = (Array.isArray(facets) ? facets : []).filter(
    (r) => r && String(r.rule_id || '').trim() && Number(r.count || 0) > 0,
  );
  setPickerOptions(
    selRule,
    rows.map((r) => {
      const id = String(r.rule_id).trim();
      const description = String(r.description || '').trim();
      const targets = (r.targets || []).join(', ');
      return {
        value: id,
        label: `${description ? `${id} - ${description}` : id} (${Number(r.count || 0)})`,
        title: targets ? `${id} maps to ${targets}` : id,
      };
    }),
    splitList(state.rule),
    (value) => ({ value, label: `Current filter: ${value} (0)` }),
  );
}

function shouldSkipAutoFacets() {
  // Unmapped-only pages already run the expensive anti-join for the event list
  // itself. The controls facet is necessarily empty for unmapped results, and
  // the source facet is a convenience rather than required for the initial
  // page. Keep the fast metadata options from loadSources/loadControls and
  // avoid doing a second full unmapped scan on first load and filter changes.
  return !!state.unmapped;
}

async function loadFacets() {
  try {
    const data = await apiGet(`/api/v1/events/facets?${filterParams().toString()}`);
    renderSourceOptions(data?.sources || []);
    renderControlOptions(data?.controls || []);
    renderRuleOptions(data?.rules || []);
  } catch {
    // Non-fatal; keep existing options.
  }
}

function reloadWithFacets(resetOffset = true) {
  applyFromInputs(resetOffset);
  loadEvents();
  if (!shouldSkipAutoFacets()) {
    loadFacets();
  }
}

function renderRows(items) {
  tbody.innerHTML = '';
  for (const e of items) {
    const controls = e.controls || [];
    const max = 4;
    const shown = controls.slice(0, max);
    const more = controls.length > max ? controls.length - max : 0;

    const controlsHtml =
      shown
        .map(
          (c) =>
            `<a class="badge badge-soft me-1 mb-1" href="${withFramework(
              `/control.html?id=${encodeURIComponent(c.id)}`,
              framework,
            )}" title="${esc(c.title || '')}">${esc(c.ref)}</a>`,
        )
        .join('') + (more ? `<span class="badge text-bg-light">+${more}</span>` : '');

    const sourceHref = e.source
      ? withFramework(`/source.html?source=${encodeURIComponent(e.source)}`, framework)
      : '';
    const sourceHtml =
      (e.source ? sourceBadgeHtml(e.source, sourceMeta, sourceHref) : '') +
      (e.connection_id
        ? `<div class="small text-muted" title="${esc(e.connection_id)}">${esc(e.connection_name || e.connection_id)}</div>`
        : '');

    const artifactHtml = e.artifact_count
      ? `<span class="badge badge-soft">${e.artifact_count}</span>`
      : `<span class="badge text-bg-light">0</span>`;

    const linksHtml = upstreamLinkHtml(e.source_url, { title: 'Open source event' });

    const tr = document.createElement('tr');
    tr.dataset.eventId = e.id;
    tr.className = 'event-row';

    // Identifier column: broad, human-friendly context.
    // Prefer the API-provided identifier (computed from config for some sources,
    // e.g. RSS feed label), otherwise fall back to the stored system field.
    const context = e.identifier ?? e.system ?? '';

    tr.innerHTML = `${selection.cellHtml(e)}
      <td class="small-muted" data-sort="${esc(tsSortKey(e.timestamp))}">${esc(fmtTs(e.timestamp))}</td>
      <td data-sort="${esc(e.source || '')}">${sourceHtml}</td>
      <td class="small-muted" data-sort="${esc(context)}">${context ? esc(context) : ''}</td>
      <td class="wrap"><a href="${withFramework(`/event.html?id=${encodeURIComponent(e.id)}`, framework)}" class="fw-semibold" data-event-summary title="${esc(
        e.summary || '(no summary)',
      )}">${esc(shorten(e.summary || '(no summary)', 160))}</a></td>
      <td class="wrap">${controlsHtml}</td>
      <td class="text-center">${linksHtml}</td>
      <td data-sort="${esc(e.artifact_count || 0)}">${artifactHtml}</td>
    `;
    tbody.appendChild(tr);
  }
  selection.rendered();

  if ((items || []).length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="${columnCount}" class="small-muted p-4">No events match the current filters.</td>`;
    tbody.appendChild(tr);
  }
}

// A click on a row opens the event's details beside the list. Links and
// buttons in the row keep their own behaviour, and a modified click on the
// summary still opens the full page in a new tab.
tbody.addEventListener('click', (ev) => {
  const row = ev.target.closest('tr[data-event-id]');
  if (!row || !tbody.contains(row)) return;
  const summary = ev.target.closest('[data-event-summary]');
  if (ev.target.closest('a, button, input, label') && !summary) return;
  if (summary && (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button === 1)) return;
  ev.preventDefault();
  eventPanel.open(row.dataset.eventId);
});

async function loadEvents() {
  status.style.display = 'none';
  meta.textContent = 'Loading';
  showTableLoading(tbody, columnCount, 'Loading events');
  try {
    const p = filterParams();
    p.set('limit', String(state.limit));
    p.set('offset', String(state.offset));

    const data = await apiGet(`/api/v1/events?${p.toString()}`);
    state.total = data.total || 0;

    const page = offsetPagerState(state);
    const timeNote =
      state.start_ts && state.end_ts
        ? ` · window ${fmtTs(state.start_ts)}-${fmtTs(state.end_ts)}`
        : '';
    meta.textContent = `${page.start}-${page.end} of ${page.total}${timeNote}`;

    renderRows(data.items || []);

    setOffsetPagerDisabled(pagerButtons, state);
  } catch (e) {
    toast(status, `Failed to load events: ${String(e)}`, 'danger');
    meta.textContent = '-';
    tbody.innerHTML = `<tr><td colspan="${columnCount}" class="small-muted p-4">Failed to load events.</td></tr>`;
  }
}

function applyFromInputs(resetOffset = true) {
  const prevStartDate = state.start_date || '';
  const prevEndDate = state.end_date || '';

  state.source = selectedFilterPillValues(selSource).join(',');
  state.control = selectedFilterPillValues(selControl).join(',');
  state.rule = selectedFilterPillValues(selRule).join(',');
  state.conditions = conditionEditor.values();
  state.unmapped = chkUnmapped.checked;

  // Sync visible dd/mm/yyyy fields into the hidden YYYY-MM-DD inputs.
  const startUser = (startDateDmy?.value || '').trim();
  const endUser = (endDateDmy?.value || '').trim();

  if (startDate && startDateDmy) {
    if (!startUser) {
      startDate.value = '';
    } else {
      const iso = dmyToIsoDate(startUser);
      if (iso) {
        startDate.value = iso;
      } else {
        toast(status, "Invalid 'From' date. Use dd/mm/yyyy.", 'warning');
        // keep previous hidden value
      }
    }
  }
  if (endDate && endDateDmy) {
    if (!endUser) {
      endDate.value = '';
    } else {
      const iso = dmyToIsoDate(endUser);
      if (iso) {
        endDate.value = iso;
      } else {
        toast(status, "Invalid 'To' date. Use dd/mm/yyyy.", 'warning');
        // keep previous hidden value
      }
    }
  }

  const startRaw = startDate?.value || '';
  const endRaw = endDate?.value || '';

  // Keep previous values if the user types an invalid date (avoid silently clearing filters).
  let startIso = startRaw ? parseIsoDate(startRaw) : '';
  let endIso = endRaw ? parseIsoDate(endRaw) : '';

  if (startRaw && !startIso) {
    toast(status, "Invalid 'From' date. Use dd/mm/yyyy.", 'warning');
    startIso = state.start_date || '';
  }
  if (endRaw && !endIso) {
    toast(status, "Invalid 'To' date. Use dd/mm/yyyy.", 'warning');
    endIso = state.end_date || '';
  }

  const dr = normalizeDateRange(startIso, endIso);
  state.start_date = dr.start;
  state.end_date = dr.end;

  // If the user changed the day-range controls, drop any precise time window
  // (start_ts/end_ts) because there is no time-based UI on this page.
  const dateChanged =
    (state.start_date || '') !== prevStartDate || (state.end_date || '') !== prevEndDate;
  if (dateChanged) {
    state.start_ts = '';
    state.end_ts = '';
  }

  // Reflect normalized values back into the inputs.
  if (startDate) startDate.value = state.start_date || '';
  if (endDate) endDate.value = state.end_date || '';
  if (startDateDmy) startDateDmy.value = isoDateToDmy(state.start_date || '');
  if (endDateDmy) endDateDmy.value = isoDateToDmy(state.end_date || '');

  state.limit = parseInt(selLimit.value || '50', 10);
  if (resetOffset) state.offset = 0;

  setQuery({
    framework: framework || null,
    q: null,
    source: state.source || null,
    control: state.control || null,
    clause: state.clause || null,
    rule: state.rule || null,
    unmapped: state.unmapped ? 'true' : null,
    start_date: state.start_date || null,
    end_date: state.end_date || null,
    start_ts: state.start_ts || null,
    end_ts: state.end_ts || null,
    limit: state.limit,
    offset: state.offset,
  });
  syncConditionsToUrl();
  // Keep the page heading in sync with the current filters.
  updatePageTitle();
}

document.getElementById('filters').addEventListener('submit', (ev) => {
  ev.preventDefault();
  reloadWithFacets(true);
});

// Update the page title as the user changes the main filters (even before pressing Apply).
selSource?.addEventListener('change', updatePageTitleFromInputs);
selControl?.addEventListener('change', updatePageTitleFromInputs);

document.getElementById('reset').addEventListener('click', () => {
  for (const el of [selSource, selControl, selRule])
    setFilterPillOptions(el, [], { selectedValues: [] });
  conditionEditor.set([]);
  chkUnmapped.checked = false;
  if (startDate) startDate.value = '';
  if (endDate) endDate.value = '';
  if (startDateDmy) startDateDmy.value = '';
  if (endDateDmy) endDateDmy.value = '';
  selLimit.value = '50';
  state = {
    ...state,
    source: '',
    control: '',
    clause: '',
    rule: '',
    conditions: [],
    unmapped: false,
    start_date: '',
    end_date: '',
    start_ts: '',
    end_ts: '',
    limit: 50,
    offset: 0,
  };
  reloadWithFacets(true);
});

// Optional UX: auto-apply filters as fields change (user preference).
if (autoApplyFilters) {
  selSource?.addEventListener('change', () => reloadWithFacets(true));
  selControl?.addEventListener('change', () => reloadWithFacets(true));
  selRule?.addEventListener('change', () => reloadWithFacets(true));
  chkUnmapped?.addEventListener('change', () => reloadWithFacets(true));
  startDate?.addEventListener('change', () => reloadWithFacets(true));
  endDate?.addEventListener('change', () => reloadWithFacets(true));
}

let exportInFlight = false;

function triggerDownload(url) {
  // Use a temporary anchor to initiate a download without opening a new tab.
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function exportEvents(fmt) {
  if (exportInFlight) return;
  exportInFlight = true;
  setTimeout(() => {
    exportInFlight = false;
  }, 600);

  // Sync inputs to state (but keep current offset unchanged)
  applyFromInputs(false);

  const p = filterParams();
  p.set('format', fmt);

  const url = `/api/v1/events/export?${p.toString()}`;
  triggerDownload(url);
}

exportCsvBtn?.addEventListener('click', (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ev.stopImmediatePropagation?.();
  exportEvents('csv');
});
exportJsonBtn?.addEventListener('click', (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ev.stopImmediatePropagation?.();
  exportEvents('json');
});
exportNdjsonBtn?.addEventListener('click', (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  ev.stopImmediatePropagation?.();
  exportEvents('ndjson');
});

document.getElementById('saveSearch').addEventListener('click', async () => {
  // Sync current inputs to state + URL
  applyFromInputs(false);

  // Build a canonical URL without pagination; every filter on screen is kept.
  const p = new URLSearchParams();
  if (state.source) p.set('source', state.source);
  if (state.control) p.set('control', state.control);
  if (state.clause) p.set('clause', state.clause);
  if (state.rule) p.set('rule', state.rule);
  for (const condition of state.conditions) p.append('f', condition);
  if (state.unmapped) p.set('unmapped', 'true');
  if (state.start_date) p.set('start_date', state.start_date);
  if (state.end_date) p.set('end_date', state.end_date);
  if (state.start_ts) p.set('start_ts', state.start_ts);
  if (state.end_ts) p.set('end_ts', state.end_ts);
  if (state.limit) p.set('limit', String(state.limit));
  const url = '/events.html' + (p.toString() ? `?${p.toString()}` : '');

  const parts = [];
  if (state.source) parts.push(`source=${state.source}`);
  if (state.control) parts.push(`control=${state.control}`);
  if (state.clause) parts.push(`clause=${state.clause}`);
  if (state.rule) parts.push(`rule=${state.rule}`);
  for (const condition of state.conditions) parts.push(describeCondition(condition));
  if (state.unmapped) parts.push('unmapped');
  const defName = ('Events' + (parts.length ? ` (${parts.join(', ')})` : ' (all)')).slice(0, 128);

  const name = (prompt('Name this saved search', defName) || '').trim();
  if (!name) return;

  try {
    await apiPost('/api/v1/me/saved-searches', { name, url });
    toast(status, 'Saved', 'success');
  } catch (e) {
    toast(status, `Failed to save: ${String(e)}`, 'danger');
  }
});

// Pager listeners (bind to BOTH top + bottom)
wireOffsetPagerButtons(pagerButtons, (direction) => {
  state.offset =
    direction === 'prev' ? Math.max(0, state.offset - state.limit) : state.offset + state.limit;
  applyFromInputs(false);
  loadEvents();
});

selLimit.addEventListener('change', () => {
  reloadWithFacets(true);
});

// Date helpers: open native picker via the hidden <input type="date">, but
// display dd/mm/yyyy in the visible text boxes.
wireIsoDmyDateField(startDate, startDateDmy, startDatePick, { dispatchChangeOnText: false });
wireIsoDmyDateField(endDate, endDateDmy, endDatePick, { dispatchChangeOnText: false });

// Init from query
syncInputs();
syncConditionsToUrl();
updatePageTitle();
await loadSources();
await loadControls();
updatePageTitle();
if (shouldSkipAutoFacets()) {
  await loadEvents();
} else {
  await Promise.all([loadEvents(), loadFacets()]);
}
