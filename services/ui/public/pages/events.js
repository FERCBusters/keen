import {
  initNavbar,
  apiGet,
  apiPost,
  esc,
  fmtTs,
  tsSortKey,
  toast,
  debounce,
  isoDateToDmy,
  dmyToIsoDate,
  wireIsoDmyDateField,
  qs,
  qbool,
  qint,
  setQuery,
  enableTableSorting,
  shorten,
  attachSavedSearchAutocomplete,
  resolveSavedSearchUrl,
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
    a.textContent = 'Add Diary Entry';
    actions.prepend(a);
  }
} catch {
  // non-fatal
}


const autoApplyFilters = !!(me?.preferences?.auto_apply_filters);


const status = document.getElementById('status');
const tbody = document.getElementById('rows');
const meta = document.getElementById('resultMeta');

const pageTitleEl = document.getElementById('pageTitle') || document.querySelector('main h1');

function controlHeading(ref) {
  const r = String(ref || '').trim();
  if (!r) return '';

  // Prefer the global controls index (from /api/v1/controls)
  let title = String(controlsIndex?.[r]?.title || '').trim();

  // Fallback: if controls failed to load, try to infer the title from the <select> option text.
  if (!title) {
    try {
      const opt = Array.from(selControl?.options || []).find((o) => o.value === r);
      if (opt) {
        let t = String(opt.textContent || '').trim();
        t = t.replace(/\s*\(\d+\)\s*$/, '').trim(); // strip trailing counts
        t = t.replace(/^Current filter:\s*/i, '').trim();
        const parts = t.split('—').map((x) => x.trim()).filter(Boolean);
        // Option text is usually: "A.8.6 — Secure coding" → extract the title part
        if (parts.length >= 2) title = parts.slice(1).join(' — ');
      }
    } catch {
      // non-fatal
    }
  }

  // Include BOTH reference + title when we have it.
  return title ? `${r} — ${title}` : r;
}

function updatePageTitle({source, control, clause} = {}) {
  const src = source !== undefined ? String(source || '').trim() : String(state.source || '').trim();
  const ctl = control !== undefined ? String(control || '').trim() : String(state.control || '').trim();
  const cls = clause !== undefined ? String(clause || '').trim() : String(state.clause || '').trim();

  const srcLabel = src ? String(sourceLabel(src, sourceMeta) || src).trim() : '';
  const ctlLabel = ctl ? controlHeading(ctl) : '';

  let heading = 'Events';
  if (srcLabel || ctlLabel || cls) {
    const parts = [];
    if (srcLabel) parts.push(srcLabel);
    if (ctlLabel) parts.push(`control ${ctlLabel}`);
    if (cls) parts.push(`clause ${cls}`);
    heading = `Events for ${parts.join(' and ')}`;
  }

  if (pageTitleEl) pageTitleEl.textContent = heading;
  document.title = `Keen – ${heading}`;
}

function updatePageTitleFromInputs() {
  updatePageTitle({source: selSource?.value || '', control: selControl?.value || ''});
}

const inpQ = document.getElementById('q');

// Saved-search autocomplete for the Events page search box
attachSavedSearchAutocomplete(inpQ);
const selSource = document.getElementById('source');
const selControl = document.getElementById('control');
const chkUnmapped = document.getElementById('unmapped');
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

let state = {
  q: qs('q', ''),
  source: qs('source', ''),
  control: qs('control', ''),
  clause: qs('clause', ''),
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
  if (/^\d{4}-\d{2}-\d{2}T/.test(raw) && !(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw))) {
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
  if (!s && !e) return {start: '', end: ''};
  // Compare YYYY-MM-DD lexicographically.
  if (s && e && s > e) return {start: e, end: s};
  return {start: s, end: e};
}

function buildEventsHref(overrides = {}) {
  const p = new URLSearchParams();

  const q = overrides.q !== undefined ? overrides.q : state.q;
  const source = overrides.source !== undefined ? overrides.source : state.source;
  const control = overrides.control !== undefined ? overrides.control : state.control;
  const clause = overrides.clause !== undefined ? overrides.clause : state.clause;
  const unmapped = overrides.unmapped !== undefined ? overrides.unmapped : state.unmapped;
  const start_date = overrides.start_date !== undefined ? overrides.start_date : state.start_date;
  const end_date = overrides.end_date !== undefined ? overrides.end_date : state.end_date;
  const start_ts = overrides.start_ts !== undefined ? overrides.start_ts : state.start_ts;
  const end_ts = overrides.end_ts !== undefined ? overrides.end_ts : state.end_ts;
  const limit = overrides.limit !== undefined ? overrides.limit : state.limit;
  const offset = overrides.offset !== undefined ? overrides.offset : state.offset;

  if (q) p.set('q', q);
  if (framework) p.set('framework', framework);
  if (source) p.set('source', source);
  if (control) p.set('control', control);
  if (clause) p.set('clause', clause);
  if (unmapped) p.set('unmapped', 'true');
  if (start_date) p.set('start_date', start_date);
  if (end_date) p.set('end_date', end_date);
  if (start_ts) p.set('start_ts', String(start_ts));
  if (end_ts) p.set('end_ts', String(end_ts));
  if (limit) p.set('limit', String(limit));
  if (offset && Number(offset) > 0) p.set('offset', String(offset));

  return '/events.html' + (p.toString() ? `?${p.toString()}` : '');
}

function syncInputs() {
  inpQ.value = state.q;
  chkUnmapped.checked = state.unmapped;
  selLimit.value = String(state.limit);
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
    const opts = items
        .map((x) => {
          const src = x.source;
          const label = String(x.label || sourceLabel(src, sourceMeta) || src);
          const text = label && label !== src ? `${label} (${src})` : src;
          return `<option value="${esc(src)}">${esc(text)}</option>`;
        })
        .join('');
    selSource.innerHTML = `<option value="">All sources</option>` + opts;
    if (state.source) selSource.value = state.source;
  } catch {
    // non-fatal
  }
}

async function loadControls() {
  try {
    const data = await apiGet(`/api/v1/controls?limit=5000&framework=${encodeURIComponent(framework)}`);
    const items = (data.items || []).filter((c) => c.ref);

    controlsIndex = {};
    for (const c of items) {
      const ref = String(c.ref || '').trim();
      if (!ref) continue;
      controlsIndex[ref] = {ref, title: String(c.title || '').trim(), type: c.type || 'control'};
    }

    const byType = {};
    for (const c of items) {
      const t = c.type || 'control';
      byType[t] = byType[t] || [];
      byType[t].push(c);
    }

    const knownRefs = new Set(items.map((c) => c.ref));
    let html = `<option value="">All controls</option>`;

    const types = Object.keys(byType).sort();
    for (const t of types) {
      const group = byType[t];
      const options = group
          .map((c) => {
            const label = c.title ? `${c.ref} — ${c.title}` : c.ref;
            return `<option value="${esc(c.ref)}">${esc(label)}</option>`;
          })
          .join('');
      html += `<optgroup label="${esc(t)}">${options}</optgroup>`;
    }

    // Preserve a UUID/ref filter that isn't in the current controls list (rare, but possible)
    if (state.control && !knownRefs.has(state.control)) {
      html = `<option value="${esc(state.control)}">Current filter: ${esc(state.control)}</option>` + html;
    }

    selControl.innerHTML = html;
    if (state.control) selControl.value = state.control;
  } catch {
    // non-fatal
    selControl.innerHTML = `<option value="">All controls</option>`;
    if (state.control) {
      const opt = document.createElement('option');
      opt.value = state.control;
      opt.textContent = `Current filter: ${state.control}`;
      selControl.insertBefore(opt, selControl.firstChild);
      selControl.value = state.control;
    }
  }
}


function renderSourceOptions(facets) {
  const current = state.source || '';
  const rows = Array.isArray(facets) ? facets : [];
  const counts = new Map(rows.map((r) => [String(r.source), Number(r.count || 0)]));

  const items = rows
      .filter((r) => r && r.source && Number(r.count || 0) > 0)
      .map((r) => ({
        source: String(r.source),
        count: Number(r.count || 0),
      }));

  let html = '<option value="">All sources</option>';

  // Preserve current selection even if it yields zero results under other filters.
  if (current && !counts.has(current)) {
    const label = String(sourceLabel(current, sourceMeta) || current);
    const text = label && label !== current ? `${label} (${current})` : current;
    html += `<option value="${esc(current)}">${esc(text)} (0)</option>`;
  }

  for (const it of items) {
    const src = it.source;
    const label = String(sourceLabel(src, sourceMeta) || src);
    const text = label && label !== src ? `${label} (${src})` : src;
    html += `<option value="${esc(src)}">${esc(text)} (${it.count})</option>`;
  }

  selSource.innerHTML = html;
  if (current) selSource.value = current;
}

function renderControlOptions(facets) {
  const current = state.control || '';
  const rows = Array.isArray(facets) ? facets : [];
  const counts = new Map(rows.map((r) => [String(r.ref), Number(r.count || 0)]));

  const byType = {};
  for (const r of rows) {
    const ref = String(r.ref || '').trim();
    if (!ref) continue;
    const count = Number(r.count || 0);
    if (count <= 0) continue;
    const ty = String(r.type || controlsIndex[ref]?.type || 'control');
    byType[ty] = byType[ty] || [];
    byType[ty].push({ref, title: String(r.title || controlsIndex[ref]?.title || ''), count});
  }

  let html = '<option value="">All controls</option>';

  // Preserve current selection even if it yields zero results under other filters.
  if (current && !counts.has(current)) {
    html = `<option value="${esc(current)}">Current filter: ${esc(current)} (0)</option>` + html;
  }

  const types = Object.keys(byType).sort();
  for (const ty of types) {
    const opts = byType[ty]
        .sort((a, b) => (b.count - a.count) || a.ref.localeCompare(b.ref))
        .map((c) => {
          const label = c.title ? `${c.ref} — ${c.title}` : c.ref;
          return `<option value="${esc(c.ref)}">${esc(label)} (${c.count})</option>`;
        })
        .join('');
    html += `<optgroup label="${esc(ty)}">${opts}</optgroup>`;
  }

  selControl.innerHTML = html;
  if (current) selControl.value = current;
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
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (framework) p.set('framework', framework);
    if (state.source) p.set('source', state.source);
    if (state.control) p.set('control', state.control);
    if (state.clause) p.set('clause', state.clause);
    if (state.unmapped) p.set('unmapped', 'true');
    if (state.start_date) p.set('start_date', state.start_date);
    if (state.end_date) p.set('end_date', state.end_date);
    if (state.start_ts) p.set('start_ts', state.start_ts);
    if (state.end_ts) p.set('end_ts', state.end_ts);

    const data = await apiGet(`/api/v1/events/facets?${p.toString()}`);
    renderSourceOptions(data?.sources || []);
    renderControlOptions(data?.controls || []);
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

function maybeNavigateSavedSearchOrReload(resetOffset = true) {
  const qRaw = inpQ?.value?.trim?.() ? inpQ.value.trim() : '';
  const saved = resolveSavedSearchUrl(qRaw);
  if (saved) {
    // Match the behavior of the navbar and the Controls page: saved searches are URLs,
    // not literal strings to search for.
    location.href = withFramework(saved, framework);
    return;
  }
  reloadWithFacets(resetOffset);
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
                `<a class="badge badge-soft me-1 mb-1" href="${withFramework(`/control.html?id=${encodeURIComponent(
                    c.id
                )}`, framework)}" title="${esc(c.title || '')}">${esc(c.ref)}</a>`
          )
          .join('') + (more ? `<span class="badge text-bg-light">+${more}</span>` : '');

    const sourceHref = e.source ? withFramework(`/source.html?source=${encodeURIComponent(e.source)}`, framework) : '';
    const sourceHtml = e.source ? sourceBadgeHtml(e.source, sourceMeta, sourceHref) : '';

    const artifactHtml = e.artifact_count ?
      `<span class="badge badge-soft">${e.artifact_count}</span>` :
      `<span class="badge text-bg-light">0</span>`;

    const linksHtml = upstreamLinkHtml(e.source_url, {title: 'Open source event'});

    const tr = document.createElement('tr');

    // Identifier column: broad, human-friendly context.
    // Prefer the API-provided identifier (computed from config for some sources,
    // e.g. RSS feed label), otherwise fall back to the stored system field.
    const context = e.identifier ?? e.system ?? '';

    tr.innerHTML = `
      <td class="small-muted" data-sort="${esc(tsSortKey(e.timestamp))}">${esc(fmtTs(e.timestamp))}</td>
      <td data-sort="${esc(e.source || '')}">${sourceHtml}</td>
      <td class="small-muted" data-sort="${esc(context)}">${context ? esc(context) : ''}</td>
      <td class="wrap"><a href="${withFramework(`/event.html?id=${encodeURIComponent(e.id)}`, framework)}" class="fw-semibold" title="${esc(
    e.summary || '(no summary)'
)}">${esc(shorten(e.summary || '(no summary)', 160))}</a></td>
      <td class="wrap">${controlsHtml}</td>
      <td class="text-center">${linksHtml}</td>
      <td data-sort="${esc(e.artifact_count || 0)}">${artifactHtml}</td>
    `;
    tbody.appendChild(tr);
  }

  if ((items || []).length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="7" class="small-muted p-4">No events match the current filters.</td>`;
    tbody.appendChild(tr);
  }
}

async function loadEvents() {
  status.style.display = 'none';
  meta.textContent = 'Loading…';
  showTableLoading(tbody, 7, 'Loading events…');
  try {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (framework) p.set('framework', framework);
    if (state.source) p.set('source', state.source);
    if (state.control) p.set('control', state.control);
    if (state.clause) p.set('clause', state.clause);
    if (state.unmapped) p.set('unmapped', 'true');
    if (state.start_date) p.set('start_date', state.start_date);
    if (state.end_date) p.set('end_date', state.end_date);
    if (state.start_ts) p.set('start_ts', state.start_ts);
    if (state.end_ts) p.set('end_ts', state.end_ts);
    p.set('limit', String(state.limit));
    p.set('offset', String(state.offset));

    const data = await apiGet(`/api/v1/events?${p.toString()}`);
    state.total = data.total || 0;

    const page = offsetPagerState(state);
    const timeNote = (state.start_ts && state.end_ts) ? ` • window ${fmtTs(state.start_ts)}–${fmtTs(state.end_ts)}` : '';
    meta.textContent = `${page.start}–${page.end} of ${page.total}${timeNote}`;

    renderRows(data.items || []);

    setOffsetPagerDisabled(pagerButtons, state);
  } catch (e) {
    toast(status, `Failed to load events: ${String(e)}`, 'danger');
    meta.textContent = '—';
    tbody.innerHTML = '<tr><td colspan="7" class="small-muted p-4">Failed to load events.</td></tr>';
  }
}

function applyFromInputs(resetOffset = true) {
  const prevStartDate = state.start_date || '';
  const prevEndDate = state.end_date || '';

  state.q = inpQ.value.trim();
  state.source = selSource.value;
  state.control = selControl.value;
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
        toast(status, 'Invalid \'From\' date. Use dd/mm/yyyy.', 'warning');
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
        toast(status, 'Invalid \'To\' date. Use dd/mm/yyyy.', 'warning');
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
    toast(status, 'Invalid \'From\' date. Use dd/mm/yyyy.', 'warning');
    startIso = state.start_date || '';
  }
  if (endRaw && !endIso) {
    toast(status, 'Invalid \'To\' date. Use dd/mm/yyyy.', 'warning');
    endIso = state.end_date || '';
  }

  const dr = normalizeDateRange(startIso, endIso);
  state.start_date = dr.start;
  state.end_date = dr.end;

  // If the user changed the day-range controls, drop any precise time window
  // (start_ts/end_ts) because there is no time-based UI on this page.
  const dateChanged = (state.start_date || '') !== prevStartDate || (state.end_date || '') !== prevEndDate;
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
    q: state.q || null,
    source: state.source || null,
    control: state.control || null,
    clause: state.clause || null,
    unmapped: state.unmapped ? 'true' : null,
    start_date: state.start_date || null,
    end_date: state.end_date || null,
    start_ts: state.start_ts || null,
    end_ts: state.end_ts || null,
    limit: state.limit,
    offset: state.offset,
  });
  // Keep the page heading in sync with the current filters.
  updatePageTitle();
}

document.getElementById('filters').addEventListener('submit', (ev) => {
  ev.preventDefault();

  // If the search box matches a saved search name, jump to that saved URL.
  // (Same behavior as the global and navbar event search.)
  const qRaw = inpQ.value.trim();
  const saved = resolveSavedSearchUrl(qRaw);
  if (saved) {
    location.href = withFramework(saved, framework);
    return;
  }
  reloadWithFacets(true);
});

// Update the page title as the user changes the main filters (even before pressing Apply).
selSource?.addEventListener('change', updatePageTitleFromInputs);
selControl?.addEventListener('change', updatePageTitleFromInputs);

document.getElementById('reset').addEventListener('click', () => {
  inpQ.value = '';
  selSource.value = '';
  selControl.value = '';
  chkUnmapped.checked = false;
  if (startDate) startDate.value = '';
  if (endDate) endDate.value = '';
  if (startDateDmy) startDateDmy.value = '';
  if (endDateDmy) endDateDmy.value = '';
  selLimit.value = '50';
  state = {...state, q: '', source: '', control: '', clause: '', unmapped: false, start_date: '', end_date: '', start_ts: '', end_ts: '', limit: 50, offset: 0};
  reloadWithFacets(true);
});

// Optional UX: auto-apply filters as fields change (user preference).
if (autoApplyFilters) {
  // Longer debounce to avoid interrupting datalist (saved-search) selection while typing.
  // (Updating the URL + rerendering the table can close the browser's datalist dropdown.)
  const debouncedQ = debounce(() => maybeNavigateSavedSearchOrReload(true), 1500);

  inpQ?.addEventListener('input', (ev) => {
    // If the user selected a saved search from the datalist, the value becomes the saved
    // search *name*. In auto-apply mode we should navigate to the saved URL, not treat it
    // as a literal search string.
    const qRaw = inpQ.value.trim();
    const saved = resolveSavedSearchUrl(qRaw);
    const inputType = ev?.inputType || '';
    if (saved && inputType === 'insertReplacementText') {
      debouncedQ.cancel?.();
      location.href = withFramework(saved, framework);
      return;
    }
    debouncedQ();
  });

  // Selecting an item from the saved-search datalist often fires a 'change' event.
  // Apply immediately in that case.
  inpQ?.addEventListener('change', () => {
    debouncedQ.cancel?.();
    maybeNavigateSavedSearchOrReload(true);
  });

  // If a saved search name is present in the search box (e.g., chosen but not yet applied),
  // treat it consistently as a saved-search selection.
  selSource?.addEventListener('change', () => maybeNavigateSavedSearchOrReload(true));
  selControl?.addEventListener('change', () => maybeNavigateSavedSearchOrReload(true));
  chkUnmapped?.addEventListener('change', () => maybeNavigateSavedSearchOrReload(true));
  startDate?.addEventListener('change', () => maybeNavigateSavedSearchOrReload(true));
  endDate?.addEventListener('change', () => maybeNavigateSavedSearchOrReload(true));
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

  const p = new URLSearchParams();
  if (state.q) p.set('q', state.q);
  if (framework) p.set('framework', framework);
  if (state.source) p.set('source', state.source);
  if (state.control) p.set('control', state.control);
  if (state.clause) p.set('clause', state.clause);
  if (state.unmapped) p.set('unmapped', 'true');
  if (state.start_date) p.set('start_date', state.start_date);
  if (state.end_date) p.set('end_date', state.end_date);
  if (state.start_ts) p.set('start_ts', state.start_ts);
  if (state.end_ts) p.set('end_ts', state.end_ts);
  p.set('format', fmt);

  const url = `/api/v1/events/export?${p.toString()}`;
  triggerDownload(url);
}

exportCsvBtn?.addEventListener('click', (ev) => {
  ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation?.(); exportEvents('csv');
});
exportJsonBtn?.addEventListener('click', (ev) => {
  ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation?.(); exportEvents('json');
});
exportNdjsonBtn?.addEventListener('click', (ev) => {
  ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation?.(); exportEvents('ndjson');
});


document.getElementById('saveSearch').addEventListener('click', async () => {
  // Sync current inputs to state + URL
  applyFromInputs(false);

  // Build a canonical URL without pagination
  const p = new URLSearchParams();
  if (state.q) p.set('q', state.q);
  if (state.source) p.set('source', state.source);
  if (state.control) p.set('control', state.control);
  if (state.clause) p.set('clause', state.clause);
  if (state.unmapped) p.set('unmapped', 'true');
  if (state.start_date) p.set('start_date', state.start_date);
  if (state.end_date) p.set('end_date', state.end_date);
  if (state.start_ts) p.set('start_ts', state.start_ts);
  if (state.end_ts) p.set('end_ts', state.end_ts);
  if (state.limit) p.set('limit', String(state.limit));
  const url = '/events.html' + (p.toString() ? `?${p.toString()}` : '');

  const parts = [];
  if (state.q) parts.push(`q=${state.q}`);
  if (state.source) parts.push(`source=${state.source}`);
  if (state.control) parts.push(`control=${state.control}`);
  if (state.clause) parts.push(`clause=${state.clause}`);
  if (state.unmapped) parts.push('unmapped');
  const defName = ('Events' + (parts.length ? ` (${parts.join(', ')})` : ' (all)')).slice(0, 128);

  const name = (prompt('Name this saved search', defName) || '').trim();
  if (!name) return;

  try {
    await apiPost('/api/v1/me/saved-searches', {name, url});
    toast(status, 'Saved', 'success');
  } catch (e) {
    toast(status, `Failed to save: ${String(e)}`, 'danger');
  }
});

// Pager listeners (bind to BOTH top + bottom)
wireOffsetPagerButtons(pagerButtons, (direction) => {
  state.offset = direction === 'prev'
    ? Math.max(0, state.offset - state.limit)
    : state.offset + state.limit;
  applyFromInputs(false);
  loadEvents();
});

selLimit.addEventListener('change', () => {
  reloadWithFacets(true);
});

// Date helpers: open native picker via the hidden <input type="date">, but
// display dd/mm/yyyy in the visible text boxes.
wireIsoDmyDateField(startDate, startDateDmy, startDatePick, {dispatchChangeOnText: false});
wireIsoDmyDateField(endDate, endDateDmy, endDatePick, {dispatchChangeOnText: false});

// Init from query
syncInputs();
updatePageTitle();
await loadSources();
await loadControls();
updatePageTitle();
if (shouldSkipAutoFacets()) {
  await loadEvents();
} else {
  await Promise.all([loadEvents(), loadFacets()]);
}
