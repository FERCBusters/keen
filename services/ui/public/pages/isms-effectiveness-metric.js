import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  userPillHtml,
  sourceBadgeHtml,
  canSampleIntoAudit,
  openAuditSampleModal,
  effectivenessMetricValueHtml,
  effectivenessMetricThresholdBadgeHtml,
} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const metricId = params.get('id') || params.get('metric') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canSampleAudits = canSampleIntoAudit(me);
let entry = null;
let sourceMeta = {};

function measureHref(id) {
  return withFramework(`/isms-effectiveness-measure.html?id=${encodeURIComponent(id || '')}`, framework);
}

function metricHref(id = metricId) {
  return withFramework(`/isms-effectiveness-metric.html?id=${encodeURIComponent(id || '')}`, framework);
}

function controlBadges(items) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">No controls linked.</span>';
  return arr.map((c) => `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework))}" title="${esc(c.title || c.ref || 'Control')}">${esc(c.ref || 'Control')}</a>`).join('');
}

function linkOrDash(url, label) {
  if (!url) return '—';
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label || url)}</a>`;
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

function renderMetric(row) {
  entry = row || {};
  const measure = entry.measure || {};
  const title = entry.value_display || 'Metric entry';
  document.title = `Keen – ${title}`;
  $('metricTitle').textContent = title;
  $('metricMeta').textContent = [measure.summary || measure.metric || 'Effectiveness measure', entry.period || '', entry.source_type || 'other'].filter(Boolean).join(' • ');
  $('metricValue').innerHTML = `${effectivenessMetricValueHtml(entry, measure, {fallback: 'Metric entry'})}${effectivenessMetricThresholdBadgeHtml(measure, entry)}`;
  $('metricPeriod').textContent = entry.period || 'No period recorded';
  $('metricQualitative').textContent = entry.qualitative_value || '';
  const sourceType = entry.source_type || 'other';
  $('sourceType').innerHTML = sourceBadgeHtml(sourceType, sourceMeta, sourceType ? withFramework(`/source.html?source=${encodeURIComponent(sourceType)}`, framework) : '');
  $('sourceTitle').textContent = entry.source_title || '—';
  $('sourceReference').textContent = entry.source_reference || '—';
  $('sourceUrl').innerHTML = linkOrDash(entry.source_url, entry.source_url || 'Open source');
  $('sourceEvent').innerHTML = entry.source_event_id ? `<a class="font-monospace" href="${esc(withFramework(`/event.html?id=${encodeURIComponent(entry.source_event_id)}`, framework))}" title="Open KEEN event">${esc(entry.source_event_id)}</a>` : '—';
  $('metricNotes').textContent = entry.notes || 'No notes recorded.';
  $('rawPayload').textContent = JSON.stringify(entry.raw_payload || {}, null, 2);
  $('metricRecorded').textContent = fmtTs(entry.recorded_at) || '—';
  $('metricCreated').textContent = fmtTs(entry.created_at) || '—';
  $('metricUpdated').textContent = fmtTs(entry.updated_at) || '—';
  $('metricControls').innerHTML = controlBadges(measure.controls);

  const parentTitle = measure.summary || measure.metric || measure.effectiveness_measure || 'Effectiveness measure';
  const parentHref = measureHref(measure.id || entry.measure_id);
  $('parentMeasureTitle').textContent = parentTitle;
  $('parentMeasureTitle').href = parentHref;
  $('parentMetricKey').innerHTML = measure.metric_key ? `<code>${esc(measure.metric_key)}</code>${measure.owner ? ` · ${userPillHtml(measure.owner)}` : ''}` : (measure.owner ? userPillHtml(measure.owner) : '—');
  $('parentMetricDefinition').textContent = measure.metric || '—';
  $('openMeasureLink').href = parentHref;
  $('metricBreadcrumb').href = parentHref;

  const sample = $('sampleMetricAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

async function load() {
  if (!metricId) {
    toast(status, 'Missing effectiveness metric id.', 'danger');
    return;
  }
  try {
    await loadSourceMeta();
    const row = await apiGet(`/api/v1/isms/effectiveness-metrics/${encodeURIComponent(metricId)}?framework=${encodeURIComponent(framework)}`);
    renderMetric(row);
  } catch (e) {
    toast(status, `Failed to load effectiveness metric: ${String(e)}`, 'danger');
  }
}

$('sampleMetricAudit')?.addEventListener('click', () => {
  if (!entry) return;
  openAuditSampleModal({
    me,
    entityType: 'isms_effectiveness_metric',
    entityId: entry.id,
    title: entry.value_display || 'Metric entry',
    evidenceUrl: metricHref(entry.id),
    framework,
    statusEl: status,
  });
});

load();
