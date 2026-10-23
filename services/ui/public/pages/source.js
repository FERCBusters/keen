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
  safeExternalHref,
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
if (me?.is_admin && sourceName) {
  const link = document.createElement('a');
  link.className = 'btn btn-outline-secondary';
  link.textContent = 'Evidence retention';
  link.href = '/admin.html?retention_source=' + encodeURIComponent(sourceName) + '#retention';
  document.getElementById('openEventsLink')?.parentElement.appendChild(link);
}
const canViewIsms = !!(
  me?.is_admin ||
  me?.can_view_isms ||
  me?.can_manage_isms ||
  me?.can_view_risks ||
  me?.can_manage_risks
);

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
const insightsTab = document.getElementById('sourceInsightsTab');
const insightsEl = document.getElementById('sourceInsights');
const insightsMeta = document.getElementById('sourceInsightsMeta');
const insightsBadge = document.getElementById('sourceInsightsBadge');
const insightsDays = document.getElementById('sourceInsightsDays');
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

function eventsHref({ unmapped = false } = {}) {
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
  if (eventStartDate)
    eventStartDate.value = /^\d{4}-\d{2}-\d{2}$/.test(state.start_date) ? state.start_date : '';
  if (eventEndDate)
    eventEndDate.value = /^\d{4}-\d{2}-\d{2}$/.test(state.end_date) ? state.end_date : '';
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
  sourceTitle.innerHTML = `${sourceBadgeHtml(sourceName, sourceMeta, sourcePageHref(sourceName, { tab: 'events', q: '', start_date: '', end_date: '', unmapped: false, offset: 0 }))} <span class="ms-2 align-middle">${esc(label)}</span>`;
  document.title = `Keen - ${label}`;
  if (row) {
    sourceMetaEl.textContent = `${Number(row.total_events || 0).toLocaleString()} events · ${Number(row.mapped_events || 0).toLocaleString()} mapped · ${Number(row.unmapped_events || 0).toLocaleString()} unmapped · last seen ${fmtTs(row.last_seen) || '-'}`;
  } else {
    sourceMetaEl.textContent = 'Source summary unavailable.';
  }
  if (openEventsLink) openEventsLink.href = eventsHref();
  if (openUnmappedLink) openUnmappedLink.href = eventsHref({ unmapped: true });
}

async function loadSourceMeta() {
  try {
    const data = await apiGet(`/api/v1/sources?framework=${encodeURIComponent(framework)}`);
    sourceMeta = {};
    let current = null;
    for (const x of data.items || []) {
      const src = String(x.source || '').trim();
      if (!src) continue;
      sourceMeta[src] = {
        source: src,
        label: String(x.label || src).trim() || src,
        color: String(x.color || '').trim(),
      };
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
  if (!arr.length) return '<span class="small-muted">-</span>';
  const shown = arr.slice(0, 5);
  const more =
    arr.length > shown.length
      ? `<span class="badge text-bg-light">+${arr.length - shown.length}</span>`
      : '';
  return (
    shown
      .map(
        (c) =>
          `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework))}" title="${esc(c.title || c.ref || 'Control')}">${esc(c.ref || 'Control')}</a>`,
      )
      .join('') + more
  );
}

function eventUuidLink(e) {
  const id = String(e?.id || e?.event_id || '').trim();
  if (!id) return '<span class="small-muted">-</span>';
  const href = withFramework(`/event.html?id=${encodeURIComponent(id)}`, framework);
  return `<a class="font-monospace small" href="${esc(href)}" title="${esc(id)}">${esc(id)}</a>`;
}

function renderEvents(items) {
  eventRows.innerHTML =
    (items || [])
      .map((e) => {
        const eventHref = withFramework(`/event.html?id=${encodeURIComponent(e.id)}`, framework);
        const linksHtml = upstreamLinkHtml(e.source_url, { title: 'Open source event' });
        const artifacts = e.artifact_count
          ? `<span class="badge badge-soft">${esc(e.artifact_count)}</span>`
          : '<span class="badge text-bg-light">0</span>';
        const context = e.identifier ?? e.system ?? '';
        return `<tr>
      <td class="small-muted" data-sort="${esc(tsSortKey(e.timestamp))}">${esc(fmtTs(e.timestamp) || '-')}</td>
      <td data-sort="${esc(e.id || '')}">${eventUuidLink(e)}</td>
      <td class="small-muted" data-sort="${esc(context)}">${context ? esc(shorten(context, 90)) : '-'}</td>
      <td class="wrap"><a class="fw-semibold" href="${esc(eventHref)}">${esc(shorten(e.summary || '(no summary)', 180))}</a></td>
      <td class="wrap">${controlBadges(e.controls)}</td>
      <td class="text-center">${linksHtml}</td>
      <td data-sort="${esc(e.artifact_count || 0)}">${artifacts}</td>
    </tr>`;
      })
      .join('') ||
    '<tr><td colspan="7" class="p-4 small-muted">No events match this source and filter set.</td></tr>';
}

async function loadEvents() {
  if (!sourceName) return;
  showTableLoading(eventRows, 7, 'Loading source events');
  eventResultMeta.textContent = 'Loading';
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
    eventResultMeta.textContent = `${page.start}-${page.end} of ${page.total}`;
    renderEvents(data.items || []);
    setOffsetPagerDisabled(pagerButtons, state);
  } catch (e) {
    toast(status, `Failed to load source events: ${String(e)}`, 'danger');
    eventResultMeta.textContent = '-';
    eventRows.innerHTML =
      '<tr><td colspan="7" class="p-4 text-danger">Failed to load source events.</td></tr>';
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
  return withFramework(
    `/isms-effectiveness-measure.html?id=${encodeURIComponent(item?.id || '')}`,
    framework,
  );
}

function controlsHtml(items) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">-</span>';
  return arr
    .map(
      (c) =>
        `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework))}" title="${esc(c.title || c.ref || 'Control')}">${esc(c.ref || 'Control')}</a>`,
    )
    .join('');
}

function latestEntryForSource(item) {
  const entries = Array.isArray(item?.metric_entries) ? item.metric_entries : [];
  return (
    entries
      .filter((entry) => String(entry?.source_type || '') === sourceName)
      .sort((a, b) => String(b.recorded_at || '').localeCompare(String(a.recorded_at || '')))[0] ||
    null
  );
}

function entryValueHtml(entry, measure) {
  if (!entry) return '<span class="small-muted">-</span>';
  const source = entry.source_url
    ? `<a href="${esc(safeExternalHref(entry.source_url) || '#')}" target="_blank" rel="noopener noreferrer">${esc(entry.source_title || entry.source_type || 'source')}</a>`
    : esc(entry.source_title || entry.source_type || 'source');
  const eventLink = entry.source_event_id
    ? ` · <a class="font-monospace" href="${esc(withFramework(`/event.html?id=${encodeURIComponent(entry.source_event_id)}`, framework))}" title="Open Keen event">${esc(entry.source_event_id)}</a>`
    : '';
  const valueHtml = effectivenessMetricValueHtml(entry, measure, { fallback: 'Metric entry' });
  const thresholdHtml = effectivenessMetricThresholdBadgeHtml(measure, entry);
  return `<div>${valueHtml}${thresholdHtml}${entry.period ? `<span class="small-muted ms-2">${esc(entry.period)}</span>` : ''}</div><div class="small-muted">${source}${entry.source_reference ? ` · ${esc(entry.source_reference)}` : ''}${eventLink}</div>`;
}

function renderEffectiveness(items) {
  if (!effectivenessRows || !canViewIsms) return;
  const rows = [];
  for (const item of items || []) {
    const entry = latestEntryForSource(item);
    rows.push(`<tr>
      <td class="wrap"><a class="fw-semibold" href="${esc(measureHref(item))}">${esc(shorten(item.summary || 'Effectiveness measure', 180))}</a>${item.metric_key ? `<div class="small-muted"><code>${esc(item.metric_key)}</code></div>` : ''}</td>
      <td>${esc(item.target_display || '-')}</td>
      <td class="wrap">${entryValueHtml(entry, item)}</td>
      <td class="wrap">${controlsHtml(item.controls)}</td>
    </tr>`);
  }
  effectivenessMeta.textContent = `Measures with metric entries recorded against ${sourceName}.`;
  effectivenessRows.innerHTML =
    rows.join('') ||
    `<tr><td colspan="4" class="p-4 small-muted">No effectiveness measures have metric entries for ${esc(sourceName)}.</td></tr>`;
}

async function loadEffectiveness({ force = false } = {}) {
  if (!effectivenessRows || !canViewIsms || !sourceName) return;
  if (effectivenessLoaded && !force) return;
  effectivenessRows.innerHTML =
    '<tr><td colspan="4" class="p-4 small-muted">Loading effectiveness measures</td></tr>';
  try {
    const data = await apiGet(
      `/api/v1/sources/${encodeURIComponent(sourceName)}/effectiveness-measures?framework=${encodeURIComponent(framework)}`,
    );
    renderEffectiveness(data.items || []);
    effectivenessLoaded = true;
  } catch (e) {
    effectivenessLoaded = false;
    effectivenessRows.innerHTML =
      '<tr><td colspan="4" class="p-4 text-danger">Failed to load effectiveness measures.</td></tr>';
  }
}

const INSIGHT_STATUS = {
  quiet: ['text-bg-warning', 'Quiet'],
  active: ['text-bg-success', 'Active'],
  insufficient: ['text-bg-secondary', 'No baseline yet'],
  no_events: ['text-bg-secondary', 'No events'],
};

function fmtDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return 'under a minute';
  const units = [
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ];
  const parts = [];
  let rest = total;
  for (const [name, size] of units) {
    const value = Math.floor(rest / size);
    rest -= value * size;
    if (value) parts.push(`${value} ${name}${value === 1 ? '' : 's'}`);
    if (parts.length === 2) break;
  }
  return parts.join(' ');
}

function eventFilterHref(overrides) {
  return sourcePageHref(sourceName, { tab: 'events', offset: 0, ...overrides });
}

function dailyBarsHtml(daily) {
  const series = Array.isArray(daily) ? daily : [];
  const max = Math.max(1, ...series.map((d) => Number(d.count) || 0));
  if (!series.length) return '';
  return `<div class="source-insight-bars" role="img" aria-label="Events per day over the window, ${esc(series.length)} days, busiest day ${esc(max)} events">${series
    .map((d) => {
      const count = Number(d.count) || 0;
      const height = count ? Math.max(6, Math.round((count / max) * 100)) : 2;
      return `<a class="source-insight-bar${count ? '' : ' is-empty'}" style="height:${height}%" href="${esc(eventFilterHref({ start_date: d.date, end_date: d.date, q: '', unmapped: false }))}" title="${esc(d.date)}: ${esc(count)} event${count === 1 ? '' : 's'}"></a>`;
    })
    .join('')}</div>`;
}

function renderInsights(data) {
  if (!insightsEl) return;
  const status = String(data?.status || 'no_events');
  const [badgeClass, badgeLabel] = INSIGHT_STATUS[status] || INSIGHT_STATUS.no_events;
  if (insightsBadge) insightsBadge.hidden = status !== 'quiet';
  if (insightsMeta)
    insightsMeta.textContent = `${Number(data?.window_events || 0).toLocaleString()} events in the last ${Number(data?.days || 0)} days${data?.sampled ? ` (gap and summary statistics use the most recent ${Number(data.sample_size || 0).toLocaleString()})` : ''}.`;
  const trend = data?.trend || {};
  const trendText =
    trend.change_pct === null || trend.change_pct === undefined
      ? `${Number(trend.recent_7_days || 0)} in the last 7 days`
      : `${Number(trend.recent_7_days || 0)} in the last 7 days, ${trend.change_pct >= 0 ? 'up' : 'down'} ${Math.abs(trend.change_pct)}% on the 7 days before`;
  const facts = [
    [
      'Last event',
      data?.last_seen
        ? `${fmtTs(data.last_seen)} (${fmtDuration(data.since_last_seconds)} ago)`
        : '-',
    ],
    [
      'Usual rhythm',
      data?.typical_gap_seconds !== null && data?.typical_gap_seconds !== undefined
        ? `one every ${fmtDuration(data.typical_gap_seconds)}; gaps beyond ${fmtDuration(data.usual_longest_gap_seconds)} are unusual`
        : 'needs at least 3 events in the window',
    ],
    ['Trend', trendText],
  ];
  const quiet = Array.isArray(data?.quiet_periods) ? data.quiet_periods : [];
  const spikes = Array.isArray(data?.spikes) ? data.spikes : [];
  const repeats = Array.isArray(data?.repeated_summaries) ? data.repeated_summaries : [];
  const section = (title, body) =>
    `<div class="mt-4"><div class="fw-bold mb-2">${title}</div>${body}</div>`;
  insightsEl.innerHTML = `
    <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
      <span class="badge ${badgeClass}" id="sourceInsightStatus">${esc(badgeLabel)}</span>
      <span id="sourceInsightMessage">${esc(data?.message || '')}</span>
    </div>
    <dl class="row mb-0 small">
      ${facts.map(([k, v]) => `<dt class="col-sm-3 col-lg-2 small-muted fw-normal">${esc(k)}</dt><dd class="col-sm-9 col-lg-10">${esc(v)}</dd>`).join('')}
    </dl>
    ${dailyBarsHtml(data?.daily)}
    <div class="small-muted">Events per day; select a bar to open that day's events.</div>
    ${section(
      'Quiet periods',
      quiet.length
        ? `<table class="table table-sm mb-0"><thead><tr><th>From</th><th>To</th><th>Silence</th></tr></thead><tbody>${quiet
            .map(
              (p) =>
                `<tr><td>${esc(fmtTs(p.start) || p.start)}</td><td>${esc(fmtTs(p.end) || p.end)}</td><td>${esc(p.label || fmtDuration(p.seconds))}</td></tr>`,
            )
            .join('')}</tbody></table>`
        : '<div class="small-muted">No unusually long gaps between events in this window.</div>',
    )}
    ${section(
      'Spikes',
      spikes.length
        ? `<table class="table table-sm mb-0"><thead><tr><th>Day</th><th>Events</th><th>Typical active day</th></tr></thead><tbody>${spikes
            .map(
              (sp) =>
                `<tr><td><a href="${esc(eventFilterHref({ start_date: sp.date, end_date: sp.date, q: '', unmapped: false }))}">${esc(sp.date)}</a></td><td>${esc(sp.count)}${sp.ratio ? ` <span class="small-muted">(×${esc(sp.ratio)})</span>` : ''}</td><td>${esc(sp.baseline)}</td></tr>`,
            )
            .join('')}</tbody></table>`
        : `<div class="small-muted">No day stands out against this source's typical active day.</div>`,
    )}
    ${section(
      'Repeated summaries',
      repeats.length
        ? `<table class="table table-sm mb-0"><thead><tr><th>Summary</th><th>Count</th><th>Unmapped</th><th>Last seen</th></tr></thead><tbody>${repeats
            .map(
              (r) =>
                `<tr><td class="wrap"><a href="${esc(withFramework(`/event.html?id=${encodeURIComponent(r.example_event_id || '')}`, framework))}" title="Open an example event">${esc(shorten(r.example || r.pattern || '', 160))}</a></td><td>${esc(r.count)} <span class="small-muted">(${esc(r.share_pct)}%)</span></td><td>${Number(r.unmapped_count) ? `<a href="${esc(eventFilterHref({ unmapped: true, q: '', start_date: '', end_date: '' }))}">${esc(r.unmapped_count)}</a>` : '0'}</td><td class="small-muted">${esc(fmtTs(r.last_seen) || '-')}</td></tr>`,
            )
            .join(
              '',
            )}</tbody></table><div class="small-muted mt-1">Numbers and identifiers are ignored when grouping. An unmapped repeat is a candidate for a mapping rule: open the example event to see suggested controls.</div>`
        : '<div class="small-muted">No summary repeats three or more times in the sampled events.</div>',
    )}`;
}

let insightsRequest = 0;

async function loadInsights() {
  if (!insightsEl || !sourceName) return;
  const days = Number(insightsDays?.value || 90) || 90;
  const request = ++insightsRequest;
  insightsEl.innerHTML = '<div class="small-muted">Loading insights</div>';
  try {
    const data = await apiGet(
      `/api/v1/sources/${encodeURIComponent(sourceName)}/insights?framework=${encodeURIComponent(framework)}&days=${encodeURIComponent(days)}`,
    );
    if (request !== insightsRequest) return;
    renderInsights(data);
    const quietNotice = document.getElementById('sourceQuietNotice');
    if (quietNotice && request === 1) {
      const quiet = data?.status === 'quiet';
      quietNotice.hidden = !quiet;
      const message = document.getElementById('sourceQuietMessage');
      if (quiet && message)
        message.textContent = `${sourceLabel(sourceName, sourceMeta) || sourceName} has gone quiet. ${data.message || ''}`;
    }
  } catch (e) {
    if (request !== insightsRequest) return;
    insightsEl.innerHTML = '<div class="text-danger">Failed to load source insights.</div>';
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
  const target =
    tab === 'effectiveness' || tab === 'effectiveness-measures'
      ? effectivenessTab
      : tab === 'visualisations' || tab === 'visualizations'
        ? visualisationsTab
        : tab === 'insights'
          ? insightsTab
          : null;
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
  loadInsights();
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

effectivenessTab?.addEventListener('shown.bs.tab', () => {
  setActiveTabQuery('effectiveness');
  loadEffectiveness();
});
visualisationsTab?.addEventListener('shown.bs.tab', () => setActiveTabQuery('visualisations'));
insightsTab?.addEventListener('shown.bs.tab', () => setActiveTabQuery('insights'));
insightsDays?.addEventListener('change', () => loadInsights());
document.getElementById('sourceQuietOpen')?.addEventListener('click', () => {
  if (insightsTab && window.bootstrap?.Tab)
    window.bootstrap.Tab.getOrCreateInstance(insightsTab).show();
  else insightsTab?.click();
});
document.getElementById('sourceInsightsForm')?.addEventListener('submit', (ev) => {
  ev.preventDefault();
  loadInsights();
});
document
  .getElementById('sourceEventsTab')
  ?.addEventListener('shown.bs.tab', () => setActiveTabQuery('events'));

const initialTabName = String(
  new URLSearchParams(location.search || '').get('tab') || '',
).toLowerCase();
if (
  sourceName &&
  canViewIsms &&
  (initialTabName === 'effectiveness' || initialTabName === 'effectiveness-measures')
) {
  loadEffectiveness();
}
