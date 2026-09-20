import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  esc,
  fmtTs,
  toast,
  debounce,
  getCurrentFramework,
  withFramework,
  loadEntityChangelog,
  shorten,
  canSampleIntoAudit,
  openAuditSampleModal,
} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(location.search);
let itemId = params.get('id') || '';
const canDeleteQuestions = !!(me?.is_admin || me?.can_delete_questions);
const requestedTab = params.get('tab') || 'details';
const focusThreadId = String(params.get('thread') || '').trim();
let didFocusThread = false;

const canViewPestle = !!(me?.is_admin || me?.can_view_pestle || me?.can_manage_pestle || me?.can_view_risks || me?.can_manage_risks);
const canManagePestle = !!(me?.is_admin || me?.can_manage_pestle || me?.can_manage_risks);
const canAskQuestions = !!(me?.is_admin || me?.can_question_events);

const status = document.getElementById('status');
const pestleBreadcrumb = document.getElementById('pestleBreadcrumb');
const pestleList = document.getElementById('pestleList');
const pestleVisualisations = document.getElementById('pestleVisualisations');
const pestleItemTitle = document.getElementById('pestleItemTitle');
const pestleItemMeta = document.getElementById('pestleItemMeta');
const pestleBadges = document.getElementById('pestleBadges');
const pestleEditorTitle = document.getElementById('pestleEditorTitle');

const pestleDetailsTab = document.getElementById('pestleDetailsTab');
const pestleRelevanceTab = document.getElementById('pestleRelevanceTab');
const pestleQuestionsTab = document.getElementById('pestleQuestionsTab');
const pestleChangelogTab = document.getElementById('pestleChangelogTab');

const pestleForm = document.getElementById('pestleForm');
const pestleId = document.getElementById('pestleId');
const pestleType = document.getElementById('pestleType');
const pestleLens = document.getElementById('pestleLens');
const pestleOverallRelevance = document.getElementById('pestleOverallRelevance');
const pestleItemText = document.getElementById('pestleItemText');
const pestleRationale = document.getElementById('pestleRationale');
const savePestleItem = document.getElementById('savePestleItem');
const deletePestleItem = document.getElementById('deletePestleItem');
const samplePestleAudit = document.getElementById('samplePestleAudit');
const relatedControlsCount = document.getElementById('relatedControlsCount');
const relatedControls = document.getElementById('relatedControls');

const businessProcessRelevanceList = document.getElementById('businessProcessRelevanceList');
const clauseRelevanceList = document.getElementById('clauseRelevanceList');
const clauseRelevanceFilter = document.getElementById('clauseRelevanceFilter');
const saveBusinessProcessRelevance = document.getElementById('saveBusinessProcessRelevance');
const saveClauseRelevance = document.getElementById('saveClauseRelevance');

const pestleChangelogEl = document.getElementById('pestleChangelog');
const pestleQuestionForm = document.getElementById('pestleQuestionForm');
const pestleQuestionsEl = document.getElementById('pestleQuestions');
const pestleQuestionBody = document.getElementById('pestleQuestionBody');
const pestleAskQuestion = document.getElementById('pestleAskQuestion');

let meta = {types: [], lenses: [], relevance_levels: []};
let currentItem = null;
let processRelevanceRows = [];
let clauseRelevanceRows = [];

function setTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (itemId) p.set('id', itemId);
  else p.delete('id');
  if (!tabName || tabName === 'details') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}${p.toString() ? `?${p.toString()}` : ''}`);
}

function activateTab(tabName) {
  const tab = {
    details: pestleDetailsTab,
    relevance: pestleRelevanceTab,
    questions: pestleQuestionsTab,
    changelog: pestleChangelogTab,
  }[tabName || 'details'] || pestleDetailsTab;
  if (!tab || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

function wireTabs() {
  pestleDetailsTab?.addEventListener('shown.bs.tab', () => setTabQuery('details'));
  pestleRelevanceTab?.addEventListener('shown.bs.tab', async () => {
    setTabQuery('relevance');
    if (itemId) await loadItemRelations(itemId);
  });
  pestleQuestionsTab?.addEventListener('shown.bs.tab', async () => {
    setTabQuery('questions');
    await refreshPestleQuestions();
  });
  pestleChangelogTab?.addEventListener('shown.bs.tab', async () => {
    setTabQuery('changelog');
    await loadPestleChangelog();
  });
  activateTab(requestedTab === 'editor' ? 'details' : requestedTab);
}

function relevanceLabel(rowOrCode) {
  if (typeof rowOrCode === 'object' && rowOrCode) return rowOrCode.label || 'N/A';
  const code = String(rowOrCode || 'na');
  return meta.relevance_levels.find((x) => x.code === code)?.label || 'N/A';
}

function relevanceOptions(selectedCodeOrId = 'na') {
  const selected = selectedCodeOrId === null || selectedCodeOrId === undefined ? 'na' : String(selectedCodeOrId);
  return (meta.relevance_levels || []).map((r) => {
    const isSelected = selected !== '' && (selected === String(r.id) || selected === String(r.code));
    return `<option value="${esc(r.code)}" ${isSelected ? 'selected' : ''}>${esc(r.label)}</option>`;
  }).join('');
}

function badgeForRelevance(rel) {
  const code = rel?.code || rel || 'na';
  let cls = 'text-bg-light';
  if (code === 'high') cls = 'text-bg-danger';
  else if (code === 'medium') cls = 'text-bg-warning';
  else if (code === 'low') cls = 'text-bg-success';
  return `<span class="badge ${cls}">${esc(relevanceLabel(rel))}</span>`;
}

function fillSelects() {
  const typeOptions = (meta.types || []).map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
  const lensOptions = (meta.lenses || []).map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
  if (pestleType) pestleType.innerHTML = typeOptions;
  if (pestleLens) pestleLens.innerHTML = lensOptions;
  if (pestleOverallRelevance) pestleOverallRelevance.innerHTML = relevanceOptions('na');
}

function clauseHref(id) {
  return withFramework(`/clause.html?id=${encodeURIComponent(id || '')}&tab=pestle`, framework);
}

function controlHref(id) {
  return withFramework(`/control.html?id=${encodeURIComponent(id || '')}`, framework);
}

function setFormReadOnly(readonly) {
  const disabled = !!readonly;
  for (const el of [pestleType, pestleLens, pestleOverallRelevance, pestleItemText, pestleRationale]) {
    if (el) el.disabled = disabled;
  }
  if (savePestleItem) savePestleItem.style.display = disabled ? 'none' : '';
  if (samplePestleAudit) samplePestleAudit.style.display = (canSampleIntoAudit(me) && itemId) ? '' : 'none';
  if (deletePestleItem) deletePestleItem.style.display = (!disabled && itemId) ? '' : 'none';
  if (saveBusinessProcessRelevance) saveBusinessProcessRelevance.style.display = disabled ? 'none' : '';
  if (saveClauseRelevance) saveClauseRelevance.style.display = disabled ? 'none' : '';
}

function formPayload() {
  const itemText = (pestleItemText?.value || '').trim();
  if (!itemText) throw new Error('Enter a PESTLE(E) item');
  return {
    framework,
    type: pestleType?.value || 'Political',
    lens: pestleLens?.value || 'External',
    item: itemText,
    overall_relevance_code: pestleOverallRelevance?.value || 'na',
    rationale: pestleRationale?.value || '',
  };
}

function renderRelatedControls(item) {
  const rows = Array.isArray(item?.related_controls) ? item.related_controls : [];
  if (relatedControlsCount) relatedControlsCount.textContent = String(rows.length);
  if (!relatedControls) return;
  if (!item?.id) {
    relatedControls.className = 'p-3 small-muted';
    relatedControls.innerHTML = 'Save the item and map clauses to see related controls.';
    return;
  }
  if (!rows.length) {
    relatedControls.className = 'p-3 small-muted';
    relatedControls.innerHTML = 'No controls are related through non-N/A clause relevance mappings yet.';
    return;
  }
  relatedControls.className = 'table-responsive';
  relatedControls.innerHTML = `<table class="table table-sm table-hover align-middle mb-0">
    <thead><tr><th style="width:130px;">Control</th><th>Title</th><th style="width:120px;">Scope</th></tr></thead>
    <tbody>${rows.map((c) => `<tr>
      <td class="fw-semibold"><a href="${esc(controlHref(c.id || ''))}">${esc(c.ref || '')}</a></td>
      <td class="wrap">${esc(c.title || '')}</td>
      <td>${c.in_scope ? '<span class="badge badge-soft">In scope</span>' : '<span class="badge text-bg-light">Out of scope</span>'}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

function renderItem(item = null) {
  currentItem = item;
  if (pestleId) pestleId.value = item?.id || '';
  if (pestleType) pestleType.value = item?.type || meta.types?.[0] || 'Political';
  if (pestleLens) pestleLens.value = item?.lens || meta.lenses?.[0] || 'External';
  if (pestleOverallRelevance) pestleOverallRelevance.value = item?.overall_relevance?.code || 'na';
  if (pestleItemText) pestleItemText.value = item?.item || '';
  if (pestleRationale) pestleRationale.value = item?.rationale || '';

  const isExisting = !!item?.id;
  const title = isExisting ? shorten(item.item || 'PESTLE(E) item', 90) : 'New PESTLE(E) item';
  if (pestleItemTitle) pestleItemTitle.textContent = title;
  if (pestleEditorTitle) pestleEditorTitle.textContent = isExisting ? `${canManagePestle ? 'Edit' : 'View'} PESTLE(E) item` : 'New PESTLE(E) item';
  if (pestleItemMeta) {
    const bits = isExisting
      ? [item.framework || framework, `${item.type || ''} / ${item.lens || ''}`, `updated ${fmtTs(item.updated_at) || 'unknown'}`]
      : [framework || 'selected framework', 'new item'];
    pestleItemMeta.textContent = bits.filter(Boolean).join(' • ');
  }
  if (pestleBadges) {
    pestleBadges.innerHTML = isExisting
      ? `${badgeForRelevance(item.overall_relevance)} <span class="badge badge-soft">${esc(item.type || '')}</span> <span class="badge badge-soft">${esc(item.lens || '')}</span>`
      : '<span class="badge text-bg-light">unsaved</span>';
  }

  setFormReadOnly(!canManagePestle);
  renderRelatedControls(item);
  if (!isExisting) {
    if (pestleChangelogEl) pestleChangelogEl.innerHTML = '<div class="p-3 small-muted">Save the item first.</div>';
    if (pestleQuestionsEl) pestleQuestionsEl.innerHTML = '<div class="small-muted">Save the item first.</div>';
    processRelevanceRows = [];
    clauseRelevanceRows = [];
    renderProcessRelevance();
    renderClauseRelevance();
  }
}

function renderProcessRelevance() {
  if (!businessProcessRelevanceList) return;
  if (!itemId) {
    businessProcessRelevanceList.innerHTML = '<div class="small-muted">Save the item first.</div>';
    return;
  }
  if (!processRelevanceRows.length) {
    businessProcessRelevanceList.innerHTML = '<div class="small-muted">No business processes exist yet. Add them from the PESTLE(E) Impact Assessment page.</div>';
    return;
  }
  businessProcessRelevanceList.innerHTML = `<div class="table-responsive"><table class="table table-sm align-middle mb-0">
    <thead><tr><th>Business process</th><th style="width:140px;">Relevance</th></tr></thead>
    <tbody>${processRelevanceRows.map((row) => {
      const bp = row.business_process || {};
      return `<tr>
        <td>${esc(bp.name || '')}</td>
        <td><select class="form-select form-select-sm process-relevance" data-process-id="${esc(bp.id || '')}" ${canManagePestle ? '' : 'disabled'}>${relevanceOptions(row.relevance?.code || 'na')}</select></td>
      </tr>`;
    }).join('')}</tbody>
  </table></div>`;
}

function renderClauseRelevance() {
  if (!clauseRelevanceList) return;
  if (!itemId) {
    clauseRelevanceList.innerHTML = '<div class="small-muted">Save the item first.</div>';
    return;
  }
  const q = String(clauseRelevanceFilter?.value || '').trim().toLowerCase();
  const rows = clauseRelevanceRows.filter((row) => {
    if (!q) return true;
    const c = row.clause || {};
    return `${c.ref || ''} ${c.title || ''}`.toLowerCase().includes(q);
  });
  if (!rows.length) {
    clauseRelevanceList.innerHTML = '<div class="small-muted">No clauses match.</div>';
    return;
  }
  clauseRelevanceList.innerHTML = `<div class="table-responsive" style="max-height:620px;"><table class="table table-sm align-middle mb-0">
    <thead><tr><th style="width:120px;">Clause</th><th>Title</th><th style="width:140px;">Relevance</th></tr></thead>
    <tbody>${rows.map((row) => {
      const c = row.clause || {};
      return `<tr>
        <td><a class="fw-semibold" href="${esc(clauseHref(c.id || ''))}">${esc(c.ref || '')}</a></td>
        <td class="wrap small-muted">${esc(c.title || '')}</td>
        <td><select class="form-select form-select-sm clause-relevance" data-clause-id="${esc(c.id || '')}" ${canManagePestle ? '' : 'disabled'}>${relevanceOptions(row.relevance?.code || 'na')}</select></td>
      </tr>`;
    }).join('')}</tbody>
  </table></div>`;
}

async function loadMeta() {
  meta = await apiGet('/api/v1/pestle/meta');
  fillSelects();
}

async function loadItem() {
  if (!itemId) {
    if (!canManagePestle) {
      toast(status, 'Missing PESTLE(E) item id.', 'danger');
      return;
    }
    renderItem(null);
    return;
  }
  const u = new URL(`/api/v1/pestle/items/${encodeURIComponent(itemId)}`, location.origin);
  const item = await apiGet(u.pathname + u.search);
  renderItem(item);
}

async function loadItemRelations(id = itemId) {
  if (!id) {
    renderProcessRelevance();
    renderClauseRelevance();
    return;
  }
  const [bpData, clauseData] = await Promise.all([
    apiGet(`/api/v1/pestle/items/${encodeURIComponent(id)}/business-processes`),
    apiGet(`/api/v1/pestle/items/${encodeURIComponent(id)}/clauses`),
  ]);
  processRelevanceRows = bpData?.items || [];
  clauseRelevanceRows = clauseData?.items || [];
  renderProcessRelevance();
  renderClauseRelevance();
}

async function loadPestleChangelog() {
  if (!pestleChangelogEl) return;
  if (!itemId) {
    pestleChangelogEl.innerHTML = '<div class="p-3 small-muted">Save the item first.</div>';
    return;
  }
  try {
    const u = new URL(`/api/v1/pestle/items/${encodeURIComponent(itemId)}/changelog`, location.origin);
    if (framework) u.searchParams.set('framework', framework);
    u.searchParams.set('limit', '100');
    await loadEntityChangelog(pestleChangelogEl, u.pathname + u.search, {
      empty: 'No PESTLE(E) item changes have been recorded yet.',
    });
  } catch (e) {
    pestleChangelogEl.innerHTML = '<div class="text-danger p-3">Failed to load changelog.</div>';
  }
}

function _qStatusBadge(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'answered') return '<span class="badge text-bg-success">answered</span>';
  if (s === 'reviewing') return '<span class="badge text-bg-info">reviewing</span>';
  return '<span class="badge text-bg-warning">unanswered</span>';
}

function _renderPestleQuestionThread(t) {
  const tid = t?.id || '';
  const createdBy = t?.created_by_username || '';
  const createdAt = t?.created_at ? fmtTs(t.created_at) : '—';
  const updatedAt = t?.updated_at ? fmtTs(t.updated_at) : '—';
  const statusVal = String(t?.status || 'unanswered').toLowerCase();
  const posts = Array.isArray(t?.posts) ? t.posts : [];
  const isAdmin = !!me?.is_admin;
  const isAuthor = (me?.user && createdBy && String(me.user) === String(createdBy));
  const canReply = isAdmin || isAuthor;
  const statusCtl = isAdmin ? `
    <select class="form-select form-select-sm" style="width:auto;" data-action="set-pestle-question-status" data-thread="${esc(tid)}">
      <option value="unanswered" ${statusVal === 'unanswered' ? 'selected' : ''}>unanswered</option>
      <option value="reviewing" ${statusVal === 'reviewing' ? 'selected' : ''}>reviewing</option>
      <option value="answered" ${statusVal === 'answered' ? 'selected' : ''}>answered</option>
    </select>` : '';
  const deleteCtl = canDeleteQuestions ? `<button class="btn btn-sm btn-outline-danger" type="button" data-action="delete-pestle-question" data-thread="${esc(tid)}">Delete</button>` : '';
  const postsHtml = posts.map((p) => `
    <div class="question-post">
      <div class="question-post-meta"><div class="small-muted"><span class="fw-bold">${esc(p?.author_username || '—')}</span> • ${esc(p?.created_at ? fmtTs(p.created_at) : '—')}</div></div>
      <div class="mt-2">${esc(p?.body || '').replace(/\n/g, '<br>')}</div>
    </div>`).join('');
  const replyHtml = canReply ? `
    <div class="mt-3 no-print">
      <div class="small-muted mb-1">Reply</div>
      <textarea class="form-control" rows="3" id="pestleReplyBody-${esc(tid)}" placeholder="Write a response…"></textarea>
      <div class="d-flex flex-wrap gap-2 align-items-center mt-2">
        <button class="btn btn-sm btn-primary" data-action="reply-pestle-question" data-thread="${esc(tid)}">Send reply</button>
      </div>
      <div class="form-text">Only admins or the original author can reply.</div>
    </div>` : '<div class="small-muted mt-3">Only admins or the original author can reply.</div>';
  return `
    <div class="question-thread" id="thread-${esc(tid)}">
      <div class="question-header">
        <div>
          <div class="question-title">Thread by <span class="mono">${esc(createdBy || '—')}</span></div>
          <div class="small-muted">Created ${esc(createdAt)} • Updated ${esc(updatedAt)}</div>
        </div>
        <div class="d-flex flex-wrap gap-2 align-items-center">${_qStatusBadge(statusVal)}${statusCtl}${deleteCtl}</div>
      </div>
      ${postsHtml || '<div class="small-muted mt-2">No posts yet.</div>'}
      ${replyHtml}
    </div>`;
}

function _renderPestleQuestions(threads) {
  const arr = Array.isArray(threads) ? threads : [];
  if (!pestleQuestionsEl) return;
  pestleQuestionsEl.innerHTML = arr.length ? arr.map(_renderPestleQuestionThread).join('') : '<span class="badge text-bg-light">none</span>';
}

async function refreshPestleQuestions() {
  if (!pestleQuestionsEl) return;
  if (!itemId) {
    pestleQuestionsEl.innerHTML = '<div class="small-muted">Save the item first.</div>';
    return;
  }
  try {
    const res = await apiGet(`/api/v1/questions/entities/pestle_item/${encodeURIComponent(itemId)}`);
    _renderPestleQuestions(res?.threads || []);
    if (!me?.is_admin) {
      try { await apiPost(`/api/v1/questions/entities/pestle_item/${encodeURIComponent(itemId)}/mark-seen`, {}); } catch {}
    }
    if (focusThreadId && !didFocusThread) {
      const el = document.getElementById(`thread-${focusThreadId}`);
      if (el) {
        didFocusThread = true;
        el.classList.add('question-thread-focus');
        try { el.scrollIntoView({behavior: 'smooth', block: 'start'}); } catch { el.scrollIntoView(); }
      }
    }
  } catch (e) {
    pestleQuestionsEl.innerHTML = `<div class="text-danger small">Failed to load questions: ${esc(String(e))}</div>`;
  }
}

async function saveCurrentItem(ev) {
  ev?.preventDefault();
  if (!canManagePestle) return;
  if (savePestleItem) savePestleItem.disabled = true;
  try {
    const payload = formPayload();
    const saved = itemId
      ? await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(itemId)}`, payload)
      : await apiPost('/api/v1/pestle/items', payload);
    itemId = saved.id;
    currentItem = saved;
    renderItem(saved);
    await Promise.all([loadItemRelations(itemId), loadPestleChangelog(), refreshPestleQuestions()]);
    history.replaceState({}, '', withFramework(`/pestle_item.html?id=${encodeURIComponent(itemId)}`, framework));
    toast(status, 'PESTLE(E) item saved', 'success');
  } catch (e) {
    toast(status, `Failed to save PESTLE(E) item: ${String(e)}`, 'danger');
  } finally {
    if (savePestleItem) savePestleItem.disabled = false;
  }
}

function collectProcessRelevancePayload() {
  return {
    items: Array.from(businessProcessRelevanceList?.querySelectorAll('.process-relevance') || []).map((sel) => ({
      business_process_id: sel.dataset.processId,
      relevance_code: sel.value || 'na',
    })).filter((x) => x.business_process_id),
  };
}

function collectClauseRelevancePayload() {
  const visible = Array.from(clauseRelevanceList?.querySelectorAll('.clause-relevance') || []);
  const updates = new Map(visible.map((sel) => [String(sel.dataset.clauseId || ''), sel.value || 'na']));
  return {
    items: clauseRelevanceRows.map((row) => {
      const cid = String(row.clause?.id || '');
      return {
        clause_id: cid,
        relevance_code: updates.has(cid) ? updates.get(cid) : (row.relevance?.code || 'na'),
      };
    }).filter((x) => x.clause_id),
  };
}

pestleForm?.addEventListener('submit', saveCurrentItem);
clauseRelevanceFilter?.addEventListener('input', debounce(renderClauseRelevance, 200));

saveBusinessProcessRelevance?.addEventListener('click', async () => {
  if (!canManagePestle || !itemId) return;
  saveBusinessProcessRelevance.disabled = true;
  try {
    const data = await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(itemId)}/business-processes`, collectProcessRelevancePayload());
    processRelevanceRows = data?.items || [];
    renderProcessRelevance();
    await Promise.all([loadItem(), loadPestleChangelog()]);
    toast(status, 'Business process relevance saved', 'success');
  } catch (e) {
    toast(status, `Failed to save process relevance: ${String(e)}`, 'danger');
  } finally {
    saveBusinessProcessRelevance.disabled = false;
  }
});

saveClauseRelevance?.addEventListener('click', async () => {
  if (!canManagePestle || !itemId) return;
  saveClauseRelevance.disabled = true;
  try {
    const data = await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(itemId)}/clauses`, collectClauseRelevancePayload());
    clauseRelevanceRows = data?.items || [];
    renderClauseRelevance();
    await Promise.all([loadItem(), loadPestleChangelog()]);
    toast(status, 'Clause relevance saved', 'success');
  } catch (e) {
    toast(status, `Failed to save clause relevance: ${String(e)}`, 'danger');
  } finally {
    saveClauseRelevance.disabled = false;
  }
});


samplePestleAudit?.addEventListener('click', () => {
  openAuditSampleModal({
    me,
    entityType: 'pestle_item',
    entityId: currentItem?.id || itemId,
    title: currentItem?.item || 'PESTLE(E) item',
    framework: currentItem?.framework || framework,
    statusEl: status,
  });
});

deletePestleItem?.addEventListener('click', async () => {
  if (!canManagePestle || !itemId) return;
  if (!confirm('Delete this PESTLE(E) item and all of its relevance mappings?')) return;
  deletePestleItem.disabled = true;
  try {
    await apiDelete(`/api/v1/pestle/items/${encodeURIComponent(itemId)}`);
    toast(status, 'PESTLE(E) item deleted', 'success');
    location.assign(withFramework('/pestle.html', framework));
  } catch (e) {
    toast(status, `Failed to delete PESTLE(E) item: ${String(e)}`, 'danger');
    deletePestleItem.disabled = false;
  }
});

if (pestleQuestionForm) pestleQuestionForm.style.display = canAskQuestions ? '' : 'none';

pestleAskQuestion?.addEventListener('click', async () => {
  if (!itemId) return toast(status, 'Save the PESTLE(E) item first.', 'warning');
  const body = String(pestleQuestionBody?.value || '').trim();
  if (!body) return toast(status, 'Please enter a question', 'warning');
  pestleAskQuestion.disabled = true;
  const prev = pestleAskQuestion.textContent;
  pestleAskQuestion.textContent = 'Submitting…';
  try {
    await apiPost(`/api/v1/questions/entities/pestle_item/${encodeURIComponent(itemId)}`, {body});
    if (pestleQuestionBody) pestleQuestionBody.value = '';
    await refreshPestleQuestions();
    toast(status, 'Question submitted', 'success');
  } catch (e) {
    toast(status, String(e), 'danger');
  } finally {
    pestleAskQuestion.disabled = false;
    pestleAskQuestion.textContent = prev || 'Ask question';
  }
});

pestleQuestionsEl?.addEventListener('click', async (ev) => {
  const delBtn = ev.target?.closest?.('[data-action="delete-pestle-question"]');
  if (delBtn && itemId) {
    const tid = delBtn.getAttribute('data-thread') || '';
    if (!tid || !canDeleteQuestions) return;
    if (!confirm('Delete this question thread and all replies?')) return;
    delBtn.disabled = true;
    try {
      await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
      await refreshPestleQuestions();
      toast(status, 'Question deleted', 'success');
    } catch (e) {
      toast(status, String(e), 'danger');
      delBtn.disabled = false;
    }
    return;
  }

  const btn = ev.target?.closest?.('[data-action="reply-pestle-question"]');
  if (!btn || !itemId) return;
  const tid = btn.getAttribute('data-thread') || '';
  const bodyEl = document.getElementById(`pestleReplyBody-${tid}`);
  const body = String(bodyEl?.value || '').trim();
  if (!body) return toast(status, 'Please enter a reply', 'warning');
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = 'Sending…';
  try {
    await apiPost(`/api/v1/questions/${encodeURIComponent(tid)}/posts`, {body});
    if (bodyEl) bodyEl.value = '';
    await refreshPestleQuestions();
    toast(status, 'Reply sent', 'success');
  } catch (e) {
    toast(status, String(e), 'danger');
  } finally {
    btn.disabled = false;
    btn.textContent = prev || 'Send reply';
  }
});

pestleQuestionsEl?.addEventListener('change', async (ev) => {
  const sel = ev.target?.closest?.('[data-action="set-pestle-question-status"]');
  if (!sel || !itemId) return;
  const tid = sel.getAttribute('data-thread') || '';
  const newStatus = String(sel.value || '').trim();
  if (!tid || !newStatus) return;
  try {
    await apiPatch(`/api/v1/admin/questions/${encodeURIComponent(tid)}`, {status: newStatus});
    await refreshPestleQuestions();
  } catch (e) {
    toast(status, String(e), 'danger');
  }
});

if (pestleBreadcrumb) pestleBreadcrumb.href = withFramework('/pestle.html', framework);
if (pestleList) pestleList.href = withFramework('/pestle.html', framework);
if (pestleVisualisations) pestleVisualisations.href = withFramework('/pestle.html?tab=visualisations', framework);

wireTabs();

try {
  await loadMeta();
  if (!canViewPestle && !canManagePestle) {
    toast(status, 'You do not have permission to view PESTLE(E) assessments.', 'danger');
  } else {
    await loadItem();
    if (itemId && ['relevance', 'editor'].includes(requestedTab)) await loadItemRelations(itemId);
    if (itemId && requestedTab === 'questions') await refreshPestleQuestions();
    if (itemId && requestedTab === 'changelog') await loadPestleChangelog();
  }
} catch (e) {
  toast(status, `Failed to load PESTLE(E) item: ${String(e)}`, 'danger');
}
