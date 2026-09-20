import {
  initNavbar,
  apiGet,
  apiDelete,
  esc,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  enableTableSorting,
  shorten,
  userPillHtml,
  sourceBadgeHtml,
  canSampleIntoAudit,
  openAuditSampleModal,
  loadEntityChangelog,
  effectivenessMetricValueHtml,
  effectivenessMetricThresholdBadgeHtml,
} from '/app.js';

const me = await initNavbar();
enableTableSorting();

const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const measureId = params.get('id') || params.get('measure') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canSampleAudits = canSampleIntoAudit(me);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
let measure = null;
let metrics = [];
let sourceMeta = {};

function measureHref(id = measureId) {
  return withFramework(`/isms-effectiveness-measure.html?id=${encodeURIComponent(id || '')}`, framework);
}

function metricHref(id) {
  return withFramework(`/isms-effectiveness-metric.html?id=${encodeURIComponent(id || '')}`, framework);
}

function controlBadges(items) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">No controls linked.</span>';
  return arr.map((c) => `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework))}" title="${esc(c.title || c.ref || 'Control')}">${esc(c.ref || 'Control')}</a>`).join('');
}

function sourceHtml(entry) {
  const source = entry?.source_type || 'other';
  const badge = sourceBadgeHtml(source, sourceMeta, source ? withFramework(`/source.html?source=${encodeURIComponent(source)}`, framework) : '');
  const title = entry?.source_url
    ? `<a href="${esc(entry.source_url)}" target="_blank" rel="noopener noreferrer">${esc(entry.source_title || 'Open source')}</a>`
    : esc(entry?.source_title || '');
  const event = entry?.source_event_id ? ` · <a class="font-monospace" href="${esc(withFramework(`/event.html?id=${encodeURIComponent(entry.source_event_id)}`, framework))}" title="Open KEEN event">${esc(entry.source_event_id)}</a>` : '';
  const ref = entry?.source_reference ? ` · <span class="small-muted">${esc(entry.source_reference)}</span>` : '';
  return `<div>${badge}</div><div class="small-muted mt-1">${title || '—'}${ref}${event}</div>`;
}

function latestValueHtml(entry, row) {
  if (!entry) return '<span class="small-muted">No metric entries yet.</span>';
  const valueHtml = effectivenessMetricValueHtml(entry, row, {href: metricHref(entry.id), fallback: 'Metric entry'});
  return `${valueHtml}${effectivenessMetricThresholdBadgeHtml(row, entry)}${entry.period ? `<div class="small-muted">${esc(entry.period)}</div>` : ''}`;
}

function renderMeasure(row) {
  measure = row || {};
  const title = measure.summary || measure.metric || measure.effectiveness_measure || 'Effectiveness measure';
  document.title = `Keen – ${title}`;
  $('measureTitle').textContent = title;
  $('measureMeta').textContent = [measure.framework || framework, measure.metric_key ? `key: ${measure.metric_key}` : '', measure.metric_entry_count ? `${measure.metric_entry_count} metric entries` : 'No metric entries'].filter(Boolean).join(' • ');
  $('measureSummary').textContent = measure.summary || 'Effectiveness measure';
  $('measureDescription').textContent = measure.description || 'No description recorded.';
  $('measureBody').textContent = measure.effectiveness_measure || '—';
  $('metricDefinition').textContent = measure.metric || '—';
  $('measureNotes').textContent = measure.notes || 'No notes recorded.';
  $('latestValue').innerHTML = latestValueHtml(measure.latest_entry, measure);
  $('targetDisplay').textContent = measure.target_display || '—';
  $('metricCount').innerHTML = measure.metric_entry_count ? `<a href="#metrics">${esc(measure.metric_entry_count)} metric ${measure.metric_entry_count === 1 ? 'entry' : 'entries'}</a>` : '0';
  $('measureFrequency').textContent = measure.frequency || '—';
  $('measureOwner').innerHTML = userPillHtml(measure.owner);
  $('metricKey').innerHTML = measure.metric_key ? `<code>${esc(measure.metric_key)}</code>` : '—';
  $('measureUpdated').textContent = fmtTs(measure.updated_at) || '—';
  $('linkedControls').innerHTML = controlBadges(measure.controls);
  const edit = $('editMeasureLink');
  if (edit) {
    edit.href = withFramework(`/isms.html?tab=effectiveness&edit=effectiveness:${encodeURIComponent(measure.id)}`, framework);
    edit.style.display = canManageIsms ? '' : 'none';
  }
  const record = $('recordMetricLink');
  if (record) {
    record.href = withFramework(`/isms.html?tab=effectiveness&record=${encodeURIComponent(measure.id)}`, framework);
    record.style.display = canManageIsms ? '' : 'none';
  }
  const sample = $('sampleMeasureAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

function renderMetrics() {
  const tbody = $('metricsRows');
  if (!tbody) return;
  const q = String($('metricsFilter')?.value || '').trim().toLowerCase();
  const filtered = metrics.filter((entry) => {
    const hay = [entry.value_display, entry.period, entry.source_type, entry.source_title, entry.source_reference, entry.notes, entry.qualitative_value].filter(Boolean).join(' • ').toLowerCase();
    return !q || hay.includes(q);
  });
  $('metricsMeta').textContent = `${filtered.length} / ${metrics.length}`;
  tbody.innerHTML = filtered.map((entry) => {
    const sample = canSampleAudits ? `<button class="btn btn-sm btn-outline-success" type="button" data-sample-metric="${esc(entry.id)}" title="Sample metric into audit"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="visually-hidden">Sample metric into audit</span></button>` : '';
    const del = canManageIsms ? `<button class="btn btn-sm btn-outline-danger" type="button" data-delete-metric="${esc(entry.id)}" title="Delete metric entry"><i class="bi bi-trash" aria-hidden="true"></i><span class="visually-hidden">Delete metric entry</span></button>` : '';
    return `<tr data-metric-id="${esc(entry.id)}">
      <td class="small-muted" data-sort="${esc(entry.recorded_at || '')}">${esc(fmtTs(entry.recorded_at) || '—')}</td>
      <td>${esc(entry.period || '—')}</td>
      <td>${effectivenessMetricValueHtml(entry, measure, {href: metricHref(entry.id), fallback: 'Metric entry'})}${effectivenessMetricThresholdBadgeHtml(measure, entry)}</td>
      <td class="wrap">${sourceHtml(entry)}</td>
      <td class="wrap small-muted">${esc(shorten(entry.notes || entry.qualitative_value || '', 180) || '—')}</td>
      <td class="no-print text-nowrap"><div class="btn-group btn-group-sm"><a class="btn btn-outline-primary" href="${esc(metricHref(entry.id))}" title="Open metric entry"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i><span class="visually-hidden">Open</span></a>${sample}${del}</div></td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="p-4 small-muted">No metric entries recorded yet.</td></tr>';
}


function metricEntryDate(entry) {
  const raw = entry?.recorded_at || entry?.created_at || '';
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? new Date(ms) : null;
}

function isoDateOnly(date) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return '';
  return date.toISOString().slice(0, 10);
}

function numericMetricEntriesForTrend() {
  const src = String($('trendSource')?.value || '').trim();
  const from = String($('trendFrom')?.value || '').trim();
  const to = String($('trendTo')?.value || '').trim();
  const fromMs = from ? Date.parse(`${from}T00:00:00Z`) : null;
  const toMs = to ? Date.parse(`${to}T23:59:59.999Z`) : null;
  return (metrics || [])
    .map((entry) => ({entry, date: metricEntryDate(entry), value: Number(entry.metric_value)}))
    .filter((x) => x.date && Number.isFinite(x.value))
    .filter((x) => !src || String(x.entry.source_type || '') === src)
    .filter((x) => fromMs === null || x.date.getTime() >= fromMs)
    .filter((x) => toMs === null || x.date.getTime() <= toMs)
    .sort((a, b) => a.date - b.date);
}

function populateTrendSources() {
  const select = $('trendSource');
  if (!select) return;
  const current = String(select.value || '').trim();
  const sources = Array.from(new Set((metrics || []).map((entry) => String(entry.source_type || 'other').trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b));
  select.innerHTML = '<option value="">All sources</option>' + sources.map((src) => `<option value="${esc(src)}">${esc(src)}</option>`).join('');
  const latestSource = String(measure?.latest_entry?.source_type || '').trim();
  if (current && sources.includes(current)) select.value = current;
  else if (latestSource && sources.includes(latestSource)) select.value = latestSource;
  else if (sources.length === 1) select.value = sources[0];
}

function resetTrendRangeToData() {
  const dated = (metrics || []).map(metricEntryDate).filter(Boolean).sort((a, b) => a - b);
  if ($('trendFrom')) $('trendFrom').value = dated.length ? isoDateOnly(dated[0]) : '';
  if ($('trendTo')) $('trendTo').value = dated.length ? isoDateOnly(dated[dated.length - 1]) : '';
}

function renderMetricTrend() {
  const svgEl = $('metricTrendSvg');
  const meta = $('trendMeta');
  if (!svgEl || !meta) return;
  const d3lib = window.d3;
  if (!d3lib) {
    meta.textContent = 'D3 is unavailable; cannot render metric trend visualisation.';
    return;
  }
  const data = numericMetricEntriesForTrend();
  const selectedSource = String($('trendSource')?.value || '').trim();
  const totalInSelection = (metrics || []).filter((entry) => !selectedSource || String(entry.source_type || '') === selectedSource).length;
  const nonNumericCount = Math.max(0, totalInSelection - data.length);
  const svg = d3lib.select(svgEl);
  svg.selectAll('*').remove();

  const width = Math.max(720, Math.floor(svgEl.getBoundingClientRect().width || svgEl.parentElement?.clientWidth || 900));
  const height = 360;
  const margin = {top: 24, right: 28, bottom: 72, left: 72};
  svg.attr('viewBox', `0 0 ${width} ${height}`).attr('width', '100%').attr('height', height);

  if (!data.length) {
    meta.textContent = nonNumericCount
      ? 'No numeric metric values are available for this source and period; qualitative entries cannot be plotted as a value trend.'
      : 'No metric entries match this source and period.';
    svg.append('text')
      .attr('x', width / 2)
      .attr('y', height / 2)
      .attr('text-anchor', 'middle')
      .attr('class', 'small-muted')
      .text('No numeric metric values to display');
    return;
  }

  const minDate = data[0].date;
  const maxDate = data[data.length - 1].date;
  let xDomain = [minDate, maxDate];
  if (minDate.getTime() === maxDate.getTime()) {
    xDomain = [new Date(minDate.getTime() - 60 * 60 * 1000), new Date(maxDate.getTime() + 60 * 60 * 1000)];
  }
  const yMax = Math.max(...data.map((d) => d.value), Number(measure?.target_value || 0));
  const yMin = Math.min(0, ...data.map((d) => d.value));
  const x = d3lib.scaleTime().domain(xDomain).range([margin.left, width - margin.right]);
  const y = d3lib.scaleLinear().domain([yMin, yMax || 1]).nice().range([height - margin.bottom, margin.top]);
  const barWidth = Math.max(4, Math.min(34, (width - margin.left - margin.right) / Math.max(data.length, 1) * 0.55));

  svg.append('g')
    .attr('transform', `translate(0,${height - margin.bottom})`)
    .call(d3lib.axisBottom(x).ticks(Math.min(8, Math.max(2, data.length))).tickSizeOuter(0))
    .selectAll('text')
    .attr('transform', 'rotate(-30)')
    .attr('text-anchor', 'end');

  svg.append('g')
    .attr('transform', `translate(${margin.left},0)`)
    .call(d3lib.axisLeft(y).ticks(6));

  svg.append('text')
    .attr('x', margin.left)
    .attr('y', 14)
    .attr('class', 'metric-trend-axis-label')
    .text(measure?.target_unit ? `Value (${measure.target_unit})` : 'Value');

  const baseline = y(0);
  svg.selectAll('rect.metric-trend-bar')
    .data(data)
    .join('rect')
    .attr('class', 'metric-trend-bar')
    .attr('x', (d) => x(d.date) - barWidth / 2)
    .attr('y', (d) => Math.min(y(d.value), baseline))
    .attr('width', barWidth)
    .attr('height', (d) => Math.max(1, Math.abs(y(d.value) - baseline)))
    .append('title')
    .text((d) => `${d.entry.value_display || d.value}\n${fmtTs(d.entry.recorded_at)}\n${d.entry.source_title || d.entry.source_type || ''}`);

  svg.selectAll('circle.metric-trend-point')
    .data(data)
    .join('circle')
    .attr('class', 'metric-trend-point')
    .attr('cx', (d) => x(d.date))
    .attr('cy', (d) => y(d.value))
    .attr('r', 3.5);

  const target = Number(measure?.target_value);
  if (Number.isFinite(target) && target >= y.domain()[0] && target <= y.domain()[1]) {
    svg.append('line')
      .attr('class', 'metric-trend-target')
      .attr('x1', margin.left)
      .attr('x2', width - margin.right)
      .attr('y1', y(target))
      .attr('y2', y(target));
    svg.append('text')
      .attr('class', 'metric-trend-target-label')
      .attr('x', width - margin.right)
      .attr('y', y(target) - 6)
      .attr('text-anchor', 'end')
      .text(`Target ${measure.target_display || target}`);
  }

  meta.textContent = `${data.length} numeric metric ${data.length === 1 ? 'entry' : 'entries'} plotted${selectedSource ? ` for ${selectedSource}` : ''}${nonNumericCount ? ` (${nonNumericCount} qualitative/non-numeric omitted)` : ''}.`;
}

async function loadSourceMeta() {
  try {
    const data = await apiGet('/api/v1/sources');
    sourceMeta = {};
    for (const x of data.items || []) {
      const src = String(x.source || '').trim();
      if (!src) continue;
      sourceMeta[src] = {source: src, label: String(x.label || src).trim() || src, color: String(x.color || '').trim()};
    }
  } catch {
    sourceMeta = {};
  }
}

async function loadChangelog() {
  try {
    await loadEntityChangelog(
      $('measureChangelog'),
      `/api/v1/isms/effectiveness_measure/${encodeURIComponent(measureId)}/changelog?limit=100`,
      {empty: 'No effectiveness measure changes have been recorded yet.'}
    );
  } catch {
    $('measureChangelog').innerHTML = '<div class="text-danger p-3">Failed to load changelog.</div>';
  }
}

function activateTabFromHashOrQuery() {
  const tab = String(params.get('tab') || location.hash.replace(/^#/, '') || '').toLowerCase();
  const target = tab === 'metrics'
    ? $('measureMetricsTab')
    : (tab === 'visualisations' || tab === 'visualizations'
      ? $('measureVisualisationsTab')
      : (tab === 'changelog' ? $('measureChangelogTab') : null));
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}

async function load() {
  if (!measureId) {
    toast(status, 'Missing effectiveness measure id.', 'danger');
    return;
  }
  try {
    await loadSourceMeta();
    const row = await apiGet(`/api/v1/isms/effectiveness-measures/${encodeURIComponent(measureId)}?framework=${encodeURIComponent(framework)}`);
    renderMeasure(row);
    const metricData = await apiGet(`/api/v1/isms/effectiveness-measures/${encodeURIComponent(measureId)}/metrics?framework=${encodeURIComponent(framework)}`);
    metrics = metricData.items || [];
    renderMetrics();
    populateTrendSources();
    resetTrendRangeToData();
    renderMetricTrend();
    await loadChangelog();
    activateTabFromHashOrQuery();
  } catch (e) {
    toast(status, `Failed to load effectiveness measure: ${String(e)}`, 'danger');
  }
}

window.addEventListener('hashchange', activateTabFromHashOrQuery);
$('metricsFilter')?.addEventListener('input', renderMetrics);
$('trendSource')?.addEventListener('change', renderMetricTrend);
$('trendApply')?.addEventListener('click', renderMetricTrend);
$('trendReset')?.addEventListener('click', () => { resetTrendRangeToData(); renderMetricTrend(); });
window.addEventListener('resize', () => {
  if (document.getElementById('measureVisualisationsPane')?.classList.contains('active')) renderMetricTrend();
});
$('sampleMeasureAudit')?.addEventListener('click', () => {
  if (!measure) return;
  openAuditSampleModal({
    me,
    entityType: 'isms_effectiveness_measure',
    entityId: measure.id,
    title: measure.summary || measure.metric || 'Effectiveness measure',
    evidenceUrl: measureHref(measure.id),
    framework,
    statusEl: status,
  });
});

document.addEventListener('click', async (ev) => {
  const sampleBtn = ev.target.closest?.('[data-sample-metric]');
  const deleteBtn = ev.target.closest?.('[data-delete-metric]');
  if (sampleBtn) {
    const id = sampleBtn.getAttribute('data-sample-metric');
    const entry = metrics.find((x) => String(x.id) === String(id));
    openAuditSampleModal({
      me,
      entityType: 'isms_effectiveness_metric',
      entityId: id,
      title: entry?.value_display || 'Metric entry',
      evidenceUrl: metricHref(id),
      framework,
      statusEl: status,
    });
    return;
  }
  if (deleteBtn) {
    const id = deleteBtn.getAttribute('data-delete-metric');
    if (!id || !confirm('Delete this effectiveness metric entry?')) return;
    try {
      await apiDelete(`/api/v1/isms/effectiveness-metrics/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`);
      metrics = metrics.filter((x) => String(x.id) !== String(id));
      if (measure) measure.metric_entry_count = Math.max(0, Number(measure.metric_entry_count || 0) - 1);
      renderMetrics();
      populateTrendSources();
      renderMetricTrend();
      toast(status, 'Effectiveness metric entry deleted.', 'success');
    } catch (e) {
      toast(status, `Failed to delete metric entry: ${String(e)}`, 'danger');
    }
  }
});

for (const btn of Array.from(document.querySelectorAll('#measureTabs [data-bs-toggle="tab"]'))) {
  btn.addEventListener('shown.bs.tab', () => {
    const name = String(btn.id || '').replace(/^measure/, '').replace(/Tab$/, '').toLowerCase();
    const url = new URL(window.location.href);
    if (name && name !== 'details') url.searchParams.set('tab', name);
    else url.searchParams.delete('tab');
    if (name === 'metrics') url.hash = 'metrics';
    else url.hash = '';
    if (name === 'visualisations') renderMetricTrend();
    window.history.replaceState({}, '', url.toString());
  });
}

load();
