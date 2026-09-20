import {
  initNavbar,
  apiGet,
  enableTableSorting,
  esc,
  getCurrentFramework,
  shorten,
  titledLinksHtml,
  toast,
  withFramework,
  userPillHtml,
  userPillsHtml,
} from '/app.js';

await initNavbar();
enableTableSorting();

const framework = getCurrentFramework();
const status = document.getElementById('status');
const meta = document.getElementById('soaMeta');
const controlsHead = document.getElementById('controlsHead');
const controlsRows = document.getElementById('controlsRows');
const clausesHead = document.getElementById('clausesHead');
const clausesRows = document.getElementById('clausesRows');
const risksRows = document.getElementById('risksRows');
const pestleRows = document.getElementById('pestleRows');
const interestedPartiesRows = document.getElementById('interestedPartiesRows');
const controlsFilter = document.getElementById('controlsFilter');
const clausesFilter = document.getElementById('clausesFilter');
const risksFilter = document.getElementById('risksFilter');
const pestleFilter = document.getElementById('pestleFilter');
const interestedPartiesFilter = document.getElementById('interestedPartiesFilter');
const ismsObjectivesRows = document.getElementById('ismsObjectivesRows');
const ismsDocumentsRows = document.getElementById('ismsDocumentsRows');
const ismsEffectivenessRows = document.getElementById('ismsEffectivenessRows');
const ismsOrgRows = document.getElementById('ismsOrgRows');
const ismsAssetsRows = document.getElementById('ismsAssetsRows');
const ismsAppConfigHead = document.getElementById('ismsAppConfigHead');
const ismsAppConfigRows = document.getElementById('ismsAppConfigRows');
const ismsObjectivesFilter = document.getElementById('ismsObjectivesFilter');
const ismsDocumentsFilter = document.getElementById('ismsDocumentsFilter');
const ismsEffectivenessFilter = document.getElementById('ismsEffectivenessFilter');

let soa = {controls: [], clauses: [], risks: [], pestle_items: [], business_processes: [], interested_parties: [], isms_objectives: [], isms_documents: [], isms_effectiveness_measures: [], isms_org_nodes: [], isms_assets: [], isms_business_processes: [], isms_application_configurations: [], counts: {}};

function stripHtml(value) {
  return String(value || '')
    .replace(/<\/?(?:p|div|h[1-6]|li|ul|ol|blockquote|br|hr)\b[^>]*>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function applicabilityLabel(value) {
  if (value === 'applicable') return 'Applicable';
  if (value === 'partially_applicable') return 'Partially applicable';
  return 'Not applicable';
}

function applicabilityShort(value) {
  if (value === 'applicable') return 'A';
  if (value === 'partially_applicable') return 'P';
  return '—';
}

function applicabilityCell(value) {
  const label = applicabilityLabel(value);
  if (value === 'applicable') return `<span class="badge badge-soft" title="${esc(label)}">A</span>`;
  if (value === 'partially_applicable') return `<span class="badge text-bg-warning" title="${esc(label)}">P</span>`;
  return `<span class="small-muted" title="${esc(label)}">—</span>`;
}

function scopeBadge(control) {
  return control?.in_scope
    ? '<span class="badge badge-soft">in</span>'
    : '<span class="badge text-bg-light">out</span>';
}

function controlLink(control) {
  const href = withFramework(`/control.html?id=${encodeURIComponent(control.id)}`, control.framework || framework);
  return `<a href="${esc(href)}">${esc(control.ref || '')}</a>`;
}

function clauseLink(clause) {
  const href = withFramework(`/clause.html?id=${encodeURIComponent(clause.id)}`, clause.framework || framework);
  return `<a href="${esc(href)}">${esc(clause.ref || '')}</a>`;
}

function riskLink(risk) {
  const href = withFramework(`/risk.html?id=${encodeURIComponent(risk.id)}`, framework);
  const label = [risk.asset, shorten(risk.threat_summary || '', 60)].filter(Boolean).join(' – ') || 'Risk';
  return `<a href="${esc(href)}">${esc(label)}</a>`;
}

function pestleLink(item) {
  const href = withFramework(`/pestle_item.html?id=${encodeURIComponent(item.id)}`, item.framework || framework);
  return `<a href="${esc(href)}">${esc(shorten(item.item || 'PESTLE(E) item', 90))}</a>`;
}

function interestedPartyLink(item) {
  const href = withFramework(`/interested_party.html?id=${encodeURIComponent(item.id)}`, item.framework || framework);
  return `<a href="${esc(href)}">${esc(item.name?.name || 'Interested Party')}</a>`;
}

function badgeList(items, labelKey = 'ref', urlFn = null) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.map((it) => {
    const label = String(it?.[labelKey] || it?.name || it?.title || 'Item');
    const title = String(it?.title || it?.name || label);
    const href = urlFn ? urlFn(it) : '';
    if (href) return `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(href)}" title="${esc(title)}">${esc(label)}</a>`;
    return `<span class="badge badge-soft me-1 mb-1" title="${esc(title)}">${esc(label)}</span>`;
  }).join('');
}
function ismsControlBadges(items) { return badgeList(items, 'ref', (c) => withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework)); }
function ismsClauseBadges(items) { return badgeList(items, 'ref', (c) => withFramework(`/clause.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework)); }
function personBadges(items) { return userPillsHtml(items); }
function documentLink(item) {
  if (item?.external_url) return `<a href="${esc(item.external_url)}" target="_blank" rel="noopener noreferrer">External URL</a>`;
  if (item?.has_file) return `<a href="/api/v1/isms/documents/${encodeURIComponent(item.id)}/file" target="_blank" rel="noopener noreferrer">${esc(item.filename || 'Uploaded file')}</a>`;
  return '<span class="small-muted">—</span>';
}

function effectivenessMeasureLink(item) {
  const href = withFramework(`/isms-effectiveness-measure.html?id=${encodeURIComponent(item.id)}`, item.framework || framework);
  return `<a href="${esc(href)}">${esc(shorten(item.summary || item.effectiveness_measure || item.metric || 'Effectiveness measure', 120))}</a>`;
}

function effectivenessLatestValue(item) {
  const entry = item?.latest_entry || null;
  if (!entry) return '<span class="small-muted">No metric entries yet.</span>';
  const value = entry.value_display || 'Metric entry';
  const href = entry.id ? withFramework(`/isms-effectiveness-metric.html?id=${encodeURIComponent(entry.id)}`, item.framework || framework) : '';
  const valueHtml = href ? `<a href="${esc(href)}">${esc(value)}</a>` : esc(value);
  const bits = [];
  if (entry.period) bits.push(entry.period);
  if (entry.source_title || entry.source_type) bits.push(entry.source_title || entry.source_type);
  return `${valueHtml}${bits.length ? `<div class="small-muted">${esc(bits.join(' · '))}</div>` : ''}`;
}

function relevanceBadge(rel) {
  const label = rel?.label || '—';
  const code = rel?.code || '';
  let cls = 'text-bg-light';
  if (code === 'high') cls = 'text-bg-danger';
  else if (code === 'medium') cls = 'text-bg-warning';
  else if (code === 'low') cls = 'badge-soft';
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function filterText(el) {
  return String(el?.value || '').trim().toLowerCase();
}

function renderControls() {
  const clauses = Array.isArray(soa.clauses) ? soa.clauses : [];
  const controls = Array.isArray(soa.controls) ? soa.controls : [];
  const q = filterText(controlsFilter);

  controlsHead.innerHTML = `<tr>
    <th class="soa-sticky-col" style="min-width:120px;">Control</th>
    <th style="min-width:260px;">Title</th>
    <th style="min-width:90px;">Scope</th>
    <th style="min-width:210px;">Justification</th>
    ${clauses.map((c) => `<th class="text-center" style="min-width:78px;" title="${esc(`${c.ref || ''} ${c.title || ''}`.trim())}">${clauseLink(c)}</th>`).join('')}
  </tr>`;

  const rows = controls.filter((c) => {
    if (!q) return true;
    return `${c.ref || ''} ${c.title || ''} ${c.justification || ''}`.toLowerCase().includes(q);
  });

  controlsRows.innerHTML = rows.map((c) => {
    const links = c.clauses || {};
    return `<tr>
      <td class="soa-sticky-col fw-semibold" data-sort="${esc(c.ref || '')}">${controlLink(c)}</td>
      <td class="wrap">${esc(c.title || '')}</td>
      <td>${scopeBadge(c)}</td>
      <td class="small-muted">${esc(c.justification || '—')}</td>
      ${clauses.map((clause) => `<td class="text-center" data-sort="${esc(applicabilityShort(links[clause.id]))}">${applicabilityCell(links[clause.id])}</td>`).join('')}
    </tr>`;
  }).join('');

  if (!rows.length) {
    controlsRows.innerHTML = `<tr><td colspan="${4 + clauses.length}" class="p-4 small-muted">No controls match.</td></tr>`;
  }
}

function renderClauses() {
  const clauses = Array.isArray(soa.clauses) ? soa.clauses : [];
  const controls = Array.isArray(soa.controls) ? soa.controls : [];
  const q = filterText(clausesFilter);

  clausesHead.innerHTML = `<tr>
    <th class="soa-sticky-col" style="min-width:120px;">Clause</th>
    <th style="min-width:280px;">Title</th>
    <th style="min-width:300px;">Evidence</th>
    ${controls.map((c) => `<th class="text-center" style="min-width:78px;" title="${esc(`${c.ref || ''} ${c.title || ''}`.trim())}">${controlLink(c)}</th>`).join('')}
  </tr>`;

  const rows = clauses.filter((clause) => {
    if (!q) return true;
    const evidence = (clause.evidence_mappings || []).map((m) => `${m.title || ''} ${m.url || ''}`).join(' ');
    return `${clause.ref || ''} ${clause.title || ''} ${evidence}`.toLowerCase().includes(q);
  });

  clausesRows.innerHTML = rows.map((clause) => {
    const links = clause.controls || {};
    return `<tr>
      <td class="soa-sticky-col fw-semibold">${clauseLink(clause)}</td>
      <td class="wrap">${esc(clause.title || '')}</td>
      <td>${titledLinksHtml(clause.evidence_mappings || [], {title: 'Open evidence mapping', empty: '<span class="small-muted">—</span>'})}</td>
      ${controls.map((control) => `<td class="text-center" data-sort="${esc(applicabilityShort(links[control.id]))}">${applicabilityCell(links[control.id])}</td>`).join('')}
    </tr>`;
  }).join('');

  if (!rows.length) {
    clausesRows.innerHTML = `<tr><td colspan="${3 + controls.length}" class="p-4 small-muted">No clauses match.</td></tr>`;
  }
}

function riskScoreBadge(value) {
  const n = Number(value || 0);
  let cls = 'text-bg-success';
  if (n >= 13) cls = 'text-bg-danger';
  else if (n >= 5) cls = 'text-bg-warning';
  return `<span class="badge ${cls}">${esc(String(n))}</span>`;
}

function renderRisks() {
  const risks = Array.isArray(soa.risks) ? soa.risks : [];
  const q = filterText(risksFilter);

  if (!soa.can_view_risks) {
    risksRows.innerHTML = '<tr><td colspan="9" class="p-4 small-muted">You do not have permission to view risks.</td></tr>';
    return;
  }

  const rows = risks.filter((r) => {
    if (!q) return true;
    const controls = (r.controls || []).map((c) => `${c.ref || ''} ${c.title || ''}`).join(' ');
    return `${r.asset || ''} ${r.category?.name || ''} ${r.subcategory?.name || ''} ${(r.risk_types || []).join(' ')} ${r.threat_summary || ''} ${controls}`.toLowerCase().includes(q);
  });

  risksRows.innerHTML = rows.map((r) => {
    const controls = (r.controls || []).map((c) => `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, c.framework || framework))}" title="${esc(c.title || '')}">${esc(c.ref || '')}</a>`).join('') || '<span class="small-muted">—</span>';
    return `<tr>
      <td>${riskLink(r)}</td>
      <td>${esc(r.category?.name || '—')}</td>
      <td>${esc(r.subcategory?.name || '—')}</td>
      <td>${esc((r.risk_types || []).join(', ') || '—')}</td>
      <td class="wrap">${esc(shorten(r.threat_summary || '', 220))}</td>
      <td class="text-end">${riskScoreBadge(r.risk_score)}</td>
      <td class="text-end">${riskScoreBadge(r.residual_risk_score)}</td>
      <td>${userPillHtml(r.risk_owner)}</td>
      <td>${controls}</td>
    </tr>`;
  }).join('');

  if (!rows.length) {
    risksRows.innerHTML = '<tr><td colspan="9" class="p-4 small-muted">No risks match.</td></tr>';
  }
}

function renderPestle() {
  const items = Array.isArray(soa.pestle_items) ? soa.pestle_items : [];
  const q = filterText(pestleFilter);

  if (!pestleRows) return;

  if (!soa.can_view_pestle) {
    pestleRows.innerHTML = '<tr><td colspan="7" class="p-4 small-muted">You do not have permission to view PESTLE(E) items.</td></tr>';
    return;
  }

  const rows = items.filter((item) => {
    if (!q) return true;
    const processes = (item.business_processes || []).map((x) => `${x.business_process?.name || ''} ${x.relevance?.label || ''}`).join(' ');
    const clauses = (item.clauses || []).map((x) => `${x.clause?.ref || ''} ${x.clause?.title || ''} ${x.relevance?.label || ''}`).join(' ');
    return `${item.type || ''} ${item.lens || ''} ${item.item || ''} ${item.rationale || ''} ${item.overall_relevance?.label || ''} ${processes} ${clauses}`.toLowerCase().includes(q);
  });

  pestleRows.innerHTML = rows.map((item) => {
    const processes = (item.business_processes || []).map((x) => `<span class="badge badge-soft me-1 mb-1" title="${esc(x.relevance?.label || '—')}">${esc(x.business_process?.name || 'Business process')}</span>`).join('') || '<span class="small-muted">—</span>';
    const clauses = (item.clauses || []).map((x) => {
      const c = x.clause || {};
      const href = withFramework(`/clause.html?id=${encodeURIComponent(c.id || '')}&tab=pestle`, c.framework || framework);
      return `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(href)}" title="${esc(`${c.title || ''} · ${x.relevance?.label || '—'}`)}">${esc(c.ref || 'Clause')}</a>`;
    }).join('') || '<span class="small-muted">—</span>';
    return `<tr>
      <td>${esc(item.type || '—')}</td>
      <td>${esc(item.lens || '—')}</td>
      <td class="wrap fw-semibold">${pestleLink(item)}</td>
      <td>${relevanceBadge(item.overall_relevance)}</td>
      <td class="wrap small-muted">${esc(shorten(item.rationale || '', 220) || '—')}</td>
      <td>${processes}</td>
      <td>${clauses}</td>
    </tr>`;
  }).join('');

  if (!rows.length) {
    pestleRows.innerHTML = '<tr><td colspan="7" class="p-4 small-muted">No PESTLE(E) items match.</td></tr>';
  }
}

function renderInterestedParties() {
  if (!interestedPartiesRows) return;
  const items = Array.isArray(soa.interested_parties) ? soa.interested_parties : [];
  const q = filterText(interestedPartiesFilter);

  if (!soa.can_view_interested_parties) {
    interestedPartiesRows.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">You do not have permission to view Interested Parties.</td></tr>'; 
    return;
  }

  const rows = items.filter((item) => {
    if (!q) return true;
    const controls = (item.controls || []).map((c) => `${c.ref || ''} ${c.title || ''}`).join(' ');
    const comms = (item.communications || []).map((c) => `${c.event || ''} ${c.when || ''} ${c.with_whom || ''} ${(c.methods || []).join(' ')}`).join(' ');
    return `${item.name?.name || ''} ${item.nature?.name || ''} ${item.note || ''} ${controls} ${comms}`.toLowerCase().includes(q);
  });

  interestedPartiesRows.innerHTML = rows.map((item) => {
    const controls = (item.controls || []).map((c) => `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=interested-parties`, c.framework || framework))}" title="${esc(c.title || '')}">${esc(c.ref || '')}</a>`).join('') || '<span class="small-muted">—</span>';
    const comms = (item.communications || []).map((c) => `${esc(c.event || '')} • ${esc(c.when || '')} • ${esc(c.with_whom || '')}`).join('<br>') || '<span class="small-muted">—</span>';
    const methods = Array.from(new Set((item.communications || []).flatMap((c) => c.methods || []))).map((m) => `<span class="badge badge-soft me-1 mb-1">${esc(m)}</span>`).join('') || '<span class="small-muted">—</span>';
    const note = (item.note || '').trim() ? esc(shorten(item.note, 180)) : '<span class="small-muted">—</span>';
    return `<tr>
      <td class="fw-semibold">${interestedPartyLink(item)}</td>
      <td>${esc(item.nature?.name || '—')}</td>
      <td class="wrap small-muted">${note}</td>
      <td>${controls}</td>
      <td class="small">${comms}</td>
      <td>${methods}</td>
    </tr>`;
  }).join('');

  if (!rows.length) {
    interestedPartiesRows.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">No Interested Parties match.</td></tr>'; 
  }
}


function renderIsmsObjectives() {
  if (!ismsObjectivesRows) return;
  if (!soa.can_view_isms) { ismsObjectivesRows.innerHTML = '<tr><td colspan="7" class="p-4 small-muted">You do not have permission to view ISMS objectives.</td></tr>'; return; }
  const q = filterText(ismsObjectivesFilter);
  const rows = (soa.isms_objectives || []).filter((o) => !q || `${o.goal || ''} ${o.requirement || ''} ${o.metric || ''} ${o.owner?.username || ''}`.toLowerCase().includes(q));
  ismsObjectivesRows.innerHTML = rows.map((o) => `<tr><td class="wrap fw-semibold">${esc(shorten(o.goal || o.requirement || 'Objective', 180))}<div class="small-muted">${esc(shorten(o.requirement || '', 180))}</div></td><td>${esc(o.metric || '—')}</td><td>${userPillHtml(o.owner)}</td><td>${esc(o.completion_target_date || '—')}</td><td>${personBadges(o.resource_users)}<div class="small-muted">${esc(shorten(o.resource_requirements_text || '', 120))}</div></td><td>${ismsControlBadges(o.controls)}</td><td>${ismsClauseBadges(o.clauses)}</td></tr>`).join('') || '<tr><td colspan="7" class="p-4 small-muted">No ISMS objectives match.</td></tr>';
}

function renderIsmsDocuments() {
  if (!ismsDocumentsRows) return;
  if (!soa.can_view_isms) { ismsDocumentsRows.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">You do not have permission to view ISMS documents.</td></tr>'; return; }
  const q = filterText(ismsDocumentsFilter);
  const rows = (soa.isms_documents || []).filter((d) => !q || `${d.title || ''} ${d.document_type || ''} ${d.description || ''} ${d.external_url || ''}`.toLowerCase().includes(q));
  ismsDocumentsRows.innerHTML = rows.map((d) => `<tr><td class="fw-semibold wrap">${esc(d.title || 'Document')}</td><td>${esc(d.document_type || '—')}</td><td>${documentLink(d)}</td><td class="wrap small-muted">${esc(shorten(d.description || '', 180) || '—')}</td><td>${ismsControlBadges(d.controls)}</td><td>${ismsClauseBadges(d.clauses)}</td></tr>`).join('') || '<tr><td colspan="6" class="p-4 small-muted">No ISMS documents match.</td></tr>';
}

function renderIsmsEffectiveness() {
  if (!ismsEffectivenessRows) return;
  if (!soa.can_view_isms) {
    ismsEffectivenessRows.innerHTML = '<tr><td colspan="8" class="p-4 small-muted">You do not have permission to view ISMS effectiveness measures.</td></tr>';
    return;
  }
  const q = filterText(ismsEffectivenessFilter);
  const rows = (soa.isms_effectiveness_measures || []).filter((item) => {
    if (!q) return true;
    const controls = (item.controls || []).map((c) => `${c.ref || ''} ${c.title || ''}`).join(' ');
    const latest = item.latest_entry || {};
    return `${item.summary || ''} ${item.description || ''} ${item.effectiveness_measure || ''} ${item.metric || ''} ${item.metric_key || ''} ${item.target_display || ''} ${item.frequency || ''} ${item.owner?.username || ''} ${item.notes || ''} ${latest.value_display || ''} ${latest.source_title || ''} ${latest.source_type || ''} ${controls}`.toLowerCase().includes(q);
  });
  ismsEffectivenessRows.innerHTML = rows.map((item) => `<tr>
    <td class="wrap fw-semibold">${effectivenessMeasureLink(item)}${item.description ? `<div class="small-muted mt-1">${esc(shorten(item.description || '', 180))}</div>` : ''}${item.metric_key ? `<div class="small-muted mt-1"><code>${esc(item.metric_key)}</code></div>` : ''}</td>
    <td class="wrap">${esc(shorten(item.effectiveness_measure || '', 180) || '—')}</td>
    <td class="wrap">${esc(shorten(item.metric || '', 140) || '—')}</td>
    <td class="wrap">${effectivenessLatestValue(item)}${Number(item.metric_entry_count || 0) ? `<div class="small-muted">${esc(String(item.metric_entry_count))} metric ${Number(item.metric_entry_count || 0) === 1 ? 'entry' : 'entries'}</div>` : ''}</td>
    <td>${esc(item.target_display || '—')}</td>
    <td>${esc(item.frequency || '—')}</td>
    <td>${userPillHtml(item.owner)}</td>
    <td>${ismsControlBadges(item.controls)}</td>
  </tr>`).join('') || '<tr><td colspan="8" class="p-4 small-muted">No effectiveness measures match.</td></tr>';
}

function renderIsmsOrgAssets() {
  if (ismsOrgRows) {
    const orgs = soa.can_view_isms ? (soa.isms_org_nodes || []) : [];
    ismsOrgRows.innerHTML = orgs.map((n) => `<tr><td class="fw-semibold">${esc(n.name || 'Org node')}</td><td>${esc(n.parent?.name || 'Root')}</td><td>${personBadges(n.people)}</td><td>${ismsControlBadges(n.controls)}</td><td>${ismsClauseBadges(n.clauses)}</td></tr>`).join('') || `<tr><td colspan="5" class="p-4 small-muted">${soa.can_view_isms ? 'No organisation chart nodes yet.' : 'You do not have permission to view ISMS.'}</td></tr>`;
  }
  if (ismsAssetsRows) {
    const assets = soa.can_view_isms ? (soa.isms_assets || []) : [];
    ismsAssetsRows.innerHTML = assets.map((a) => `<tr><td class="fw-semibold">${esc(a.asset || a.name || 'Asset')}</td><td>${esc(a.category?.name || a.category_name || '—')}</td><td>${esc(a.subcategory?.name || a.subcategory_name || '—')}</td><td>${esc(a.license || '—')}</td><td>${esc(a.owner_org_node?.name || '—')}</td><td>${esc(a.register_held_by_org_node?.name || '—')}</td><td>${ismsControlBadges(a.controls)}</td><td>${ismsClauseBadges(a.clauses)}</td></tr>`).join('') || `<tr><td colspan="8" class="p-4 small-muted">${soa.can_view_isms ? 'No ISMS assets yet.' : 'You do not have permission to view ISMS.'}</td></tr>`;
  }
}

function ismsAppSourceTypeLabel(type) {
  if (type === 'document') return 'Policies and Processes';
  if (type === 'person') return 'People';
  if (type === 'asset') return 'Assets';
  if (type === 'org_node') return 'Org chart positions';
  return 'Other';
}

function ismsAppSourceSort(type) {
  return {document: 1, person: 2, asset: 3, org_node: 4}[type] || 9;
}

function ismsAppSourceKey(sourceType, id) {
  return `${sourceType || ''}:${id || ''}`;
}

function ismsAppCellClass(value) {
  const v = String(value || '').toLowerCase();
  if (v === 'high') return 'isms-app-cell-high';
  if (v === 'medium') return 'isms-app-cell-medium';
  if (v === 'low') return 'isms-app-cell-low';
  return 'isms-app-cell-empty';
}

function renderIsmsAppConfig() {
  if (!ismsAppConfigRows) return;
  const ismsProcesses = Array.isArray(soa.isms_business_processes) ? soa.isms_business_processes : [];
  const legacyProcesses = Array.isArray(soa.business_processes) ? soa.business_processes : [];
  const processes = ismsProcesses.length ? ismsProcesses : legacyProcesses;
  if (ismsAppConfigHead) ismsAppConfigHead.innerHTML = `<tr><th class="isms-app-head-group">Group</th><th class="isms-app-head-linkage">Key linkages</th>${processes.map((bp) => `<th class="text-center isms-app-process-heading" title="${esc(bp.description || bp.name || '')}">${esc(bp.name || 'Business process')}</th>`).join('')}</tr>`;
  if (!soa.can_view_isms) { ismsAppConfigRows.innerHTML = `<tr><td colspan="${Math.max(3, processes.length + 2)}" class="p-4 small-muted">You do not have permission to view ISMS application configuration.</td></tr>`; return; }
  const entries = soa.isms_application_configurations || [];
  const sources = [];
  const seen = new Set();
  for (const entry of entries) {
    const id = entry.source?.id || entry.document_id || entry.user_id || entry.asset_id || entry.org_node_id;
    const key = ismsAppSourceKey(entry.source_type, id);
    if (!id || seen.has(key)) continue;
    seen.add(key);
    sources.push({key, source_type: entry.source_type, group: ismsAppSourceTypeLabel(entry.source_type), label: entry.source?.label || entry.display || 'Source', source: entry.source || null});
  }
  sources.sort((a, b) => ismsAppSourceSort(a.source_type) - ismsAppSourceSort(b.source_type) || String(a.label).localeCompare(String(b.label), undefined, {numeric: true}));
  const entryMap = new Map();
  for (const entry of entries) {
    const id = entry.source?.id || entry.document_id || entry.user_id || entry.asset_id || entry.org_node_id;
    const key = `${ismsAppSourceKey(entry.source_type, id)}::${entry.business_process_id || entry.business_process?.id || ''}`;
    if (!entryMap.has(key)) entryMap.set(key, []);
    entryMap.get(key).push(entry);
  }
  if (!sources.length || !processes.length) {
    ismsAppConfigRows.innerHTML = `<tr><td colspan="${Math.max(3, processes.length + 2)}" class="p-4 small-muted">No application configuration matrix entries yet.</td></tr>`;
    return;
  }
  const grouped = [];
  for (const src of sources) {
    let group = grouped.find((g) => g.name === src.group);
    if (!group) {
      group = {name: src.group, rows: []};
      grouped.push(group);
    }
    group.rows.push(src);
  }
  ismsAppConfigRows.innerHTML = grouped.map((group) => group.rows.map((src, idx) => `<tr>
    ${idx === 0 ? `<td class="isms-app-group" rowspan="${group.rows.length}" data-sort="${esc(group.name)}"><span>${esc(group.name)}</span></td>` : ''}
    <td class="wrap fw-semibold isms-app-key-linkage" data-sort="${esc(src.label)}">${src.source_type === 'person' ? userPillHtml(src.source || src.label) : esc(src.label)}</td>
    ${processes.map((bp) => {
      const cellEntries = entryMap.get(`${src.key}::${bp.id}`) || [];
      if (!cellEntries.length) return '<td class="isms-app-cell isms-app-cell-empty" data-sort=""></td>';
      const first = cellEntries[0];
      const value = first.value || '—';
      const title = cellEntries.map((entry) => `${entry.value || ''}${entry.notes ? ` — ${entry.notes}` : ''}`).join('\n');
      return `<td class="isms-app-cell ${ismsAppCellClass(value)}" data-sort="${esc(value)}" title="${esc(title)}"><div class="fw-semibold">${esc(value)}</div>${cellEntries.length > 1 ? `<div class="small">+${cellEntries.length - 1}</div>` : ''}</td>`;
    }).join('')}
  </tr>`).join('')).join('');
}

function renderAll() {
  const counts = soa.counts || {};
  if (meta) {
    const businessProcessCount = counts.isms_business_processes ?? counts.business_processes ?? 0;
    meta.textContent = `${soa.framework || framework} • ${counts.controls || 0} controls • ${counts.clauses || 0} clauses • ${counts.risks || 0} CIA risks • ${counts.pestle || 0} PESTLE(E) items • ${businessProcessCount || 0} business processes • ${counts.interested_parties || 0} Interested Parties • ${counts.isms_objectives || 0} ISMS objectives • ${counts.isms_documents || 0} ISMS documents • ${counts.isms_effectiveness_measures || 0} effectiveness measures • ${counts.isms_assets || 0} assets`;
  }
  renderControls();
  renderClauses();
  renderRisks();
  renderPestle();
  renderInterestedParties();
  renderIsmsObjectives();
  renderIsmsDocuments();
  renderIsmsEffectiveness();
  renderIsmsOrgAssets();
  renderIsmsAppConfig();
}


async function load() {
  status.style.display = 'none';
  try {
    soa = await apiGet(`/api/v1/statement-of-applicability?framework=${encodeURIComponent(framework)}`);
    renderAll();
  } catch (e) {
    toast(status, `Failed to load Statement of Applicability: ${String(e)}`, 'danger');
    controlsRows.innerHTML = '<tr><td class="p-4 small-muted">Failed to load controls.</td></tr>';
    clausesRows.innerHTML = '<tr><td class="p-4 small-muted">Failed to load clauses.</td></tr>';
    risksRows.innerHTML = '<tr><td colspan="9" class="p-4 small-muted">Failed to load risks.</td></tr>';
    if (pestleRows) pestleRows.innerHTML = '<tr><td colspan="7" class="p-4 small-muted">Failed to load PESTLE(E) items.</td></tr>';
    if (interestedPartiesRows) interestedPartiesRows.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">Failed to load Interested Parties.</td></tr>'; 
    if (ismsObjectivesRows) ismsObjectivesRows.innerHTML = '<tr><td colspan="7" class="p-4 small-muted">Failed to load ISMS objectives.</td></tr>';
    if (ismsDocumentsRows) ismsDocumentsRows.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">Failed to load ISMS documents.</td></tr>';
    if (ismsEffectivenessRows) ismsEffectivenessRows.innerHTML = '<tr><td colspan="8" class="p-4 small-muted">Failed to load effectiveness measures.</td></tr>';
    if (ismsOrgRows) ismsOrgRows.innerHTML = '<tr><td colspan="5" class="p-4 small-muted">Failed to load organisation chart.</td></tr>';
    if (ismsAssetsRows) ismsAssetsRows.innerHTML = '<tr><td colspan="8" class="p-4 small-muted">Failed to load assets.</td></tr>';
    if (ismsAppConfigRows) ismsAppConfigRows.innerHTML = '<tr><td colspan="7" class="p-4 small-muted">Failed to load application configuration.</td></tr>';
  }
}

controlsFilter?.addEventListener('input', renderControls);
clausesFilter?.addEventListener('input', renderClauses);
risksFilter?.addEventListener('input', renderRisks);
pestleFilter?.addEventListener('input', renderPestle);
interestedPartiesFilter?.addEventListener('input', renderInterestedParties);
ismsObjectivesFilter?.addEventListener('input', renderIsmsObjectives);
ismsDocumentsFilter?.addEventListener('input', renderIsmsDocuments);
ismsEffectivenessFilter?.addEventListener('input', renderIsmsEffectiveness);

function activateInitialTab() {
  const tab = new URLSearchParams(window.location.search || '').get('tab');
  if (!tab) return;
  const mapped = {interestedparties: 'soaInterestedPartiesTab', isms: 'soaIsmsObjectivesTab', ismsobjectives: 'soaIsmsObjectivesTab', ismsdocuments: 'soaIsmsDocumentsTab', effectiveness: 'soaIsmsEffectivenessTab', ismseffectiveness: 'soaIsmsEffectivenessTab', ismseffectivenessmeasures: 'soaIsmsEffectivenessTab', orgassets: 'soaIsmsOrgAssetsTab', appconfig: 'soaIsmsAppConfigTab'}[String(tab || '').toLowerCase()];
  const target = document.getElementById(mapped || `soa${tab.charAt(0).toUpperCase()}${tab.slice(1)}Tab`);
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}

for (const btn of Array.from(document.querySelectorAll('#soaTabs [data-bs-toggle="tab"]'))) {
  btn.addEventListener('shown.bs.tab', () => {
    const name = String(btn.id || '').replace(/^soa/, '').replace(/Tab$/, '').toLowerCase();
    const url = new URL(window.location.href);
    url.searchParams.set('tab', name);
    window.history.replaceState({}, '', url.toString());
  });
}

load().then(activateInitialTab);
