import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  tsSortKey,
  toast,
  enableTableSorting,
  shorten,
  sourceBadgeHtml,
  sourceLabel,
  upstreamLinkHtml,
  getCurrentFramework,
  withFramework,
  showTableLoading,
  effectivenessMetricValueHtml,
  effectivenessMetricThresholdBadgeHtml,
  collectOffsetPagerButtons,
  setOffsetPagerDisabled,
  offsetPagerState,
  wireOffsetPagerButtons,
} from '/app.js';

const me = await initNavbar();
enableTableSorting();

const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const sourceName = String(params.get('source') || params.get('type') || '').trim();
const canViewIsms = !!(me?.is_admin || me?.can_view_isms || me?.can_manage_isms || me?.can_view_risks || me?.can_manage_risks);

const status = document.getElementById('status');
const sourceTitle = document.getElementById('sourceTitle');
const sourceMetaEl = document.getElementById('sourceMeta');
const openEventsLink = document.getElementById('openEventsLink');
const openUnmappedLink = document.getElementById('openUnmappedLink');
const eventRows = document.getElementById('eventRows');
const eventResultMeta = document.getElementById('eventResultMeta');
const eventQ = document.getElementById('eventQ');
const eventStartDate = document.getElementById('eventStartDate');
const eventEndDate = document.getElementById('eventEndDate');
const eventLimit = document.getElementById('eventLimit');
const eventUnmapped = document.getElementById('eventUnmapped');
const effectivenessTabItem = document.getElementById('sourceEffectivenessTabItem');
const effectivenessTab = document.getElementById('sourceEffectivenessTab');
const effectivenessRows = document.getElementById('sourceEffectivenessRows');
const effectivenessMeta = document.getElementById('sourceEffectivenessMeta');
const visualisationsTab = document.getElementById('sourceVisualisationsTab');
const pagerButtons = collectOffsetPagerButtons();

let sourceMeta = {};
let effectivenessLoaded = false;

let state = {
  q: String(params.get('q') || '').trim(),
  start_date: String(params.get('start_date') || '').trim(),
  end_date: String(params.get('end_date') || '').trim(),
  unmapped: ['1', 'true', 'yes', 'on'].includes(String(params.get('unmapped') || '').toLowerCase()),
  limit: Math.max(1, Math.min(200, Number(params.get('limit') || 50) || 50)),
  offset: Math.max(0, Number(params.get('offset') || 0) || 0),
  total: 0,
};
if (![25, 50, 100, 200].includes(state.limit)) state.limit = 50;

function sourcePageHref(src = sourceName, overrides = {}) {
  const p = new URLSearchParams();
  if (framework) p.set('framework', framework);
  if (src) p.set('source', src);
  const tab = overrides.tab || params.get('tab') || '';
  if (tab && tab !== 'events') p.set('tab', tab);
  const q = overrides.q !== undefined ? overrides.q : state.q;
  const start = overrides.start_date !== undefined ? overrides.start_date : state.start_date;
  const end = overrides.end_date !== undefined ? overrides.end_date : state.end_date;
  const unmapped = overrides.unmapped !== undefined ? overrides.unmapped : state.unmapped;
  const limit = overrides.limit !== undefined ? overrides.limit : state.limit;
  const offset = overrides.offset !== undefined ? overrides.offset : state.offset;
  if (q) p.set('q', q);
  if (start) p.set('start_date', start);
  if (end) p.set('end_date', end);
  if (unmapped) p.set('unmapped', 'true');
  if (limit) p.set('limit', String(limit));
  if (offset) p.set('offset', String(offset));
  return `/source.html?${p.toString()}`;
}

function eventsHref({unmapped = false} = {}) {
  const p = new URLSearchParams();
  if (framework) p.set('framework', framework);
  if (sourceName) p.set('source', sourceName);
  if (unmapped) p.set('unmapped', 'true');
  return `/events.html?${p.toString()}`;
}

function updateQuery() {
  const url = new URL(window.location.href);
  const tab = String(url.searchParams.get('tab') || '').trim();
  url.search = '';
  if (framework) url.searchParams.set('framework', framework);
  if (sourceName) url.searchParams.set('source', sourceName);
  if (tab && tab !== 'events') url.searchParams.set('tab', tab);
  if (state.q) url.searchParams.set('q', state.q);
  if (state.start_date) url.searchParams.set('start_date', state.start_date);
  if (state.end_date) url.searchParams.set('end_date', state.end_date);
  if (state.unmapped) url.searchParams.set('unmapped', 'true');
  if (state.limit) url.searchParams.set('limit', String(state.limit));
  if (state.offset) url.searchParams.set('offset', String(state.offset));
  window.history.replaceState({}, '', url.toString());
}

function syncInputs() {
  if (eventQ) eventQ.value = state.q || '';
  if (eventStartDate) eventStartDate.value = /^\d{4}-\d{2}-\d{2}$/.test(state.start_date) ? state.start_date : '';
  if (eventEndDate) eventEndDate.value = /^\d{4}-\d{2}-\d{2}$/.test(state.end_date) ? state.end_date : '';
  if (eventLimit) eventLimit.value = String(state.limit);
  if (eventUnmapped) eventUnmapped.checked = !!state.unmapped;
}

function renderSourceChrome(row = null) {
  if (!sourceName) {
    sourceTitle.textContent = 'Source not specified';
    sourceMetaEl.textContent = 'Add ?source=<name> to open a source page.';
    return;
  }
  const label = sourceLabel(sourceName, sourceMeta) || sourceName;
  sourceTitle.innerHTML = `${sourceBadgeHtml(sourceName, sourceMeta, sourcePageHref(sourceName, {tab: 'events', q: '', start_date: '', end_date: '', unmapped: false, offset: 0}))} <span class="ms-2 align-middle">${esc(label)}</span>`;
  document.title = `Keen – ${label}`;
  if (row) {
    sourceMetaEl.textContent = `${Number(row.total_events || 0).toLocaleString()} events • ${Number(row.mapped_events || 0).toLocaleString()} mapped • ${Number(row.unmapped_events || 0).toLocaleString()} unmapped • last seen ${fmtTs(row.last_seen) || '—'}`;
  } else {
    sourceMetaEl.textContent = 'Source summary unavailable.';
  }
  if (openEventsLink) openEventsLink.href = eventsHref();
  if (openUnmappedLink) openUnmappedLink.href = eventsHref({unmapped: true});
}

async function loadSourceMeta() {
  try {
    const data = await apiGet(`/api/v1/sources?framework=${encodeURIComponent(framework)}`);
    sourceMeta = {};
    let current = null;
    for (const x of data.items || []) {
      const src = String(x.source || '').trim();
      if (!src) continue;
      sourceMeta[src] = {source: src, label: String(x.label || src).trim() || src, color: String(x.color || '').trim()};
      if (src === sourceName) current = x;
    }
    renderSourceChrome(current);
  } catch {
    sourceMeta = {};
    renderSourceChrome(null);
  }
}

function controlBadges(controls) {
  const arr = Array.isArray(controls) ? controls : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  const shown = arr.slice(0, 5);
  const more = arr.length > shown.length ? `<span class="badge text-bg-light">+${arr.length - shown.length}</span>` : '';
  return shown.map((c) => `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework))}" title="${esc(c.title || c.ref || 'Control')}">${esc(c.ref || 'Control')}</a>`).join('') + more;
}

function eventUuidLink(e) {
  const id = String(e?.id || e?.event_id || '').trim();
  if (!id) return '<span class="small-muted">—</span>';
  const href = withFramework(`/event.html?id=${encodeURIComponent(id)}`, framework);
  return `<a class="font-monospace small" href="${esc(href)}" title="${esc(id)}">${esc(id)}</a>`;
}

function renderEvents(items) {
  eventRows.innerHTML = (items || []).map((e) => {
    const eventHref = withFramework(`/event.html?id=${encodeURIComponent(e.id)}`, framework);
    const linksHtml = upstreamLinkHtml(e.source_url, {title: 'Open source event'});
    const artifacts = e.artifact_count ? `<span class="badge badge-soft">${esc(e.artifact_count)}</span>` : '<span class="badge text-bg-light">0</span>';
    const context = e.identifier ?? e.system ?? '';
    return `<tr>
      <td class="small-muted" data-sort="${esc(tsSortKey(e.timestamp))}">${esc(fmtTs(e.timestamp) || '—')}</td>
      <td data-sort="${esc(e.id || '')}">${eventUuidLink(e)}</td>
      <td class="small-muted" data-sort="${esc(context)}">${context ? esc(shorten(context, 90)) : '—'}</td>
      <td class="wrap"><a class="fw-semibold" href="${esc(eventHref)}">${esc(shorten(e.summary || '(no summary)', 180))}</a></td>
      <td class="wrap">${controlBadges(e.controls)}</td>
      <td class="text-center">${linksHtml}</td>
      <td data-sort="${esc(e.artifact_count || 0)}">${artifacts}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="p-4 small-muted">No events match this source and filter set.</td></tr>';
}

async function loadEvents() {
  if (!sourceName) return;
  showTableLoading(eventRows, 7, 'Loading source events…');
  eventResultMeta.textContent = 'Loading…';
  try {
    const p = new URLSearchParams();
    if (framework) p.set('framework', framework);
    p.set('source', sourceName);
    if (state.q) p.set('q', state.q);
    if (state.start_date) p.set('start_date', state.start_date);
    if (state.end_date) p.set('end_date', state.end_date);
    if (state.unmapped) p.set('unmapped', 'true');
    p.set('limit', String(state.limit));
    p.set('offset', String(state.offset));
    const data = await apiGet(`/api/v1/events?${p.toString()}`);
    state.total = Number(data.total || 0);
    const page = offsetPagerState(state);
    eventResultMeta.textContent = `${page.start}–${page.end} of ${page.total}`;
    renderEvents(data.items || []);
    setOffsetPagerDisabled(pagerButtons, state);
  } catch (e) {
    toast(status, `Failed to load source events: ${String(e)}`, 'danger');
    eventResultMeta.textContent = '—';
    eventRows.innerHTML = '<tr><td colspan="7" class="p-4 text-danger">Failed to load source events.</td></tr>';
  }
}

function applyEventFilters(resetOffset = true) {
  state.q = String(eventQ?.value || '').trim();
  state.start_date = String(eventStartDate?.value || '').trim();
  state.end_date = String(eventEndDate?.value || '').trim();
  if (state.start_date && state.end_date && state.start_date > state.end_date) {
    const tmp = state.start_date;
    state.start_date = state.end_date;
    state.end_date = tmp;
  }
  state.unmapped = !!eventUnmapped?.checked;
  state.limit = Math.max(1, Math.min(200, Number(eventLimit?.value || 50) || 50));
  if (![25, 50, 100, 200].includes(state.limit)) state.limit = 50;
  if (resetOffset) state.offset = 0;
  syncInputs();
  updateQuery();
  loadEvents();
}

function measureHref(item) {
  return withFramework(`/isms-effectiveness-measure.html?id=${encodeURIComponent(item?.id || '')}`, framework);
}

function controlsHtml(items) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.map((c) => `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework))}" title="${esc(c.title || c.ref || 'Control')}">${esc(c.ref || 'Control')}</a>`).join('');
}

function latestEntryForSource(item) {
  const entries = Array.isArray(item?.metric_entries) ? item.metric_entries : [];
  return entries.filter((entry) => String(entry?.source_type || '') === sourceName)
      .sort((a, b) => String(b.recorded_at || '').localeCompare(String(a.recorded_at || '')))[0] || null;
}

function entryValueHtml(entry, measure) {
  if (!entry) return '<span class="small-muted">—</span>';
  const source = entry.source_url
    ? `<a href="${esc(entry.source_url)}" target="_blank" rel="noopener noreferrer">${esc(entry.source_title || entry.source_type || 'source')}</a>`
    : esc(entry.source_title || entry.source_type || 'source');
  const eventLink = entry.source_event_id
    ? ` · <a class="font-monospace" href="${esc(withFramework(`/event.html?id=${encodeURIComponent(entry.source_event_id)}`, framework))}" title="Open KEEN event">${esc(entry.source_event_id)}</a>`
    : '';
  const valueHtml = effectivenessMetricValueHtml(entry, measure, {fallback: 'Metric entry'});
  const thresholdHtml = effectivenessMetricThresholdBadgeHtml(measure, entry);
  return `<div>${valueHtml}${thresholdHtml}${entry.period ? `<span class="small-muted ms-2">${esc(entry.period)}</span>` : ''}</div><div class="small-muted">${source}${entry.source_reference ? ` · ${esc(entry.source_reference)}` : ''}${eventLink}</div>`;
}

function renderEffectiveness(items) {
  if (!effectivenessRows || !canViewIsms) return;
  const rows = [];
  for (const item of items || []) {
    const entry = latestEntryForSource(item);
    rows.push(`<tr>
      <td class="wrap"><a class="fw-semibold" href="${esc(measureHref(item))}">${esc(shorten(item.summary || item.metric || item.effectiveness_measure || 'Effectiveness measure', 180))}</a>${item.metric_key ? `<div class="small-muted"><code>${esc(item.metric_key)}</code></div>` : ''}</td>
      <td class="wrap">${esc(shorten(item.metric || '', 180) || '—')}</td>
      <td class="wrap">${entryValueHtml(entry, item)}</td>
      <td class="wrap">${controlsHtml(item.controls)}</td>
    </tr>`);
  }
  effectivenessMeta.textContent = `Measures with metric entries recorded against ${sourceName}.`;
  effectivenessRows.innerHTML = rows.join('') || `<tr><td colspan="4" class="p-4 small-muted">No Effectiveness Measures have metric entries for ${esc(sourceName)}.</td></tr>`;
}

async function loadEffectiveness({force = false} = {}) {
  if (!effectivenessRows || !canViewIsms || !sourceName) return;
  if (effectivenessLoaded && !force) return;
  effectivenessRows.innerHTML = '<tr><td colspan="4" class="p-4 small-muted">Loading Effectiveness Measures…</td></tr>';
  try {
    const data = await apiGet(`/api/v1/sources/${encodeURIComponent(sourceName)}/effectiveness-measures?framework=${encodeURIComponent(framework)}`);
    renderEffectiveness(data.items || []);
    effectivenessLoaded = true;
  } catch (e) {
    effectivenessLoaded = false;
    effectivenessRows.innerHTML = '<tr><td colspan="4" class="p-4 text-danger">Failed to load Effectiveness Measures.</td></tr>';
  }
}

function setActiveTabQuery(name) {
  const url = new URL(window.location.href);
  if (name && name !== 'events') url.searchParams.set('tab', name);
  else url.searchParams.delete('tab');
  window.history.replaceState({}, '', url.toString());
}

function activateInitialTab() {
  const tab = String(new URLSearchParams(location.search || '').get('tab') || '').toLowerCase();
  const target = tab === 'effectiveness' || tab === 'effectiveness-measures'
    ? effectivenessTab
    : (tab === 'visualisations' || tab === 'visualizations' ? visualisationsTab : null);
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}

if (!canViewIsms) {
  effectivenessTabItem?.classList.add('d-none');
  document.getElementById('sourceEffectivenessPane')?.classList.add('d-none');
}

syncInputs();
if (!sourceName) {
  toast(status, 'Missing source name.', 'danger');
} else {
  renderSourceChrome();
  await loadSourceMeta();
  await loadEvents();
  activateInitialTab();
}

document.getElementById('sourceEventFilters')?.addEventListener('submit', (ev) => {
  ev.preventDefault();
  applyEventFilters(true);
});
document.getElementById('eventReset')?.addEventListener('click', () => {
  state.q = '';
  state.start_date = '';
  state.end_date = '';
  state.unmapped = false;
  state.offset = 0;
  syncInputs();
  updateQuery();
  loadEvents();
});
wireOffsetPagerButtons(pagerButtons, (direction) => {
  const page = offsetPagerState(state);
  state.offset = direction === 'prev' ? page.prevOffset : page.nextOffset;
  updateQuery();
  loadEvents();
});

effectivenessTab?.addEventListener('shown.bs.tab', () => { setActiveTabQuery('effectiveness'); loadEffectiveness(); });
visualisationsTab?.addEventListener('shown.bs.tab', () => setActiveTabQuery('visualisations'));
document.getElementById('sourceEventsTab')?.addEventListener('shown.bs.tab', () => setActiveTabQuery('events'));

const initialTabName = String(new URLSearchParams(location.search || '').get('tab') || '').toLowerCase();
if (sourceName && canViewIsms && (initialTabName === 'effectiveness' || initialTabName === 'effectiveness-measures')) {
  loadEffectiveness();
}
