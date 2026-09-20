import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  esc,
  fmtTs,
  qs,
  toast,
  getCurrentFramework,
  withFramework,
  loadEntityChangelog,
  canSampleIntoAudit,
  openAuditSampleModal,
} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();

const id = qs('id', '');
const status = document.getElementById('status');
const riskTitle = document.getElementById('riskTitle');
const riskMeta = document.getElementById('riskMeta');
const risksBreadcrumb = document.getElementById('risksBreadcrumb');
const riskVisualisation = document.getElementById('riskVisualisation');
const sampleRiskAudit = document.getElementById('sampleRiskAudit');
const editRisk = document.getElementById('editRisk');
const riskTypes = document.getElementById('riskTypes');
const riskAsset = document.getElementById('riskAsset');
const riskCategory = document.getElementById('riskCategory');
const riskSubcategory = document.getElementById('riskSubcategory');
const riskOwner = document.getElementById('riskOwner');
const riskScore = document.getElementById('riskScore');
const residualScore = document.getElementById('residualScore');
const threatSummary = document.getElementById('threatSummary');
const riskNote = document.getElementById('riskNote');
const riskMitigatorContextWrap = document.getElementById('riskMitigatorContextWrap');
const riskMitigatorContext = document.getElementById('riskMitigatorContext');
const controlsCount = document.getElementById('controlsCount');
const linkedControls = document.getElementById('linkedControls');
const riskDetailsTab = document.getElementById('riskDetailsTab');
const riskQuestionsTab = document.getElementById('riskQuestionsTab');
const riskChangelogTab = document.getElementById('riskChangelogTab');
const riskChangelogEl = document.getElementById('riskChangelog');
const riskQuestionsEl = document.getElementById('riskQuestions');
const riskQuestionBody = document.getElementById('riskQuestionBody');
const riskAskQuestion = document.getElementById('riskAskQuestion');
const focusThreadId = String(qs('thread') || '').trim();
let didFocusThread = false;
const canDeleteQuestions = !!(me?.is_admin || me?.can_delete_questions);
let currentRisk = null;

function setRiskTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'details') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}?${p.toString()}`);
}

function activateRiskTab(tabName) {
  const tab = (tabName === 'changelog') ? riskChangelogTab : ((tabName === 'questions') ? riskQuestionsTab : riskDetailsTab);
  if (!tab || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

async function loadRiskChangelog() {
  if (!riskChangelogEl || !id) return;
  try {
    const u = new URL(`/api/v1/risks/${encodeURIComponent(id)}/changelog`, location.origin);
    if (framework) u.searchParams.set('framework', framework);
    u.searchParams.set('limit', '100');
    await loadEntityChangelog(riskChangelogEl, u.pathname + u.search, {
      empty: 'No risk changes have been recorded yet.',
    });
  } catch (e) {
    riskChangelogEl.innerHTML = '<div class="text-danger p-3">Failed to load changelog.</div>';
  }
}

function wireRiskTabs() {
  riskDetailsTab?.addEventListener('shown.bs.tab', () => setRiskTabQuery('details'));
  riskQuestionsTab?.addEventListener('shown.bs.tab', () => {
    setRiskTabQuery('questions');
    refreshRiskQuestions();
  });
  riskChangelogTab?.addEventListener('shown.bs.tab', () => {
    setRiskTabQuery('changelog');
    loadRiskChangelog();
  });
  activateRiskTab(qs('tab', 'details'));
}


function _qStatusBadge(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'answered') return '<span class="badge text-bg-success">answered</span>';
  if (s === 'reviewing') return '<span class="badge text-bg-info">reviewing</span>';
  return '<span class="badge text-bg-warning">unanswered</span>';
}

function _renderRiskQuestionThread(t) {
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
    <select class="form-select form-select-sm" style="width:auto;" data-action="set-risk-question-status" data-thread="${esc(tid)}">
      <option value="unanswered" ${statusVal === 'unanswered' ? 'selected' : ''}>unanswered</option>
      <option value="reviewing" ${statusVal === 'reviewing' ? 'selected' : ''}>reviewing</option>
      <option value="answered" ${statusVal === 'answered' ? 'selected' : ''}>answered</option>
    </select>` : '';
  const deleteCtl = canDeleteQuestions ? `<button class="btn btn-sm btn-outline-danger" type="button" data-action="delete-risk-question" data-thread="${esc(tid)}">Delete</button>` : '';
  const postsHtml = posts.map((p) => `
    <div class="question-post">
      <div class="question-post-meta"><div class="small-muted"><span class="fw-bold">${esc(p?.author_username || '—')}</span> • ${esc(p?.created_at ? fmtTs(p.created_at) : '—')}</div></div>
      <div class="mt-2">${esc(p?.body || '').replace(/\n/g, '<br>')}</div>
    </div>`).join('');
  const replyHtml = canReply ? `
    <div class="mt-3 no-print">
      <div class="small-muted mb-1">Reply</div>
      <textarea class="form-control" rows="3" id="riskReplyBody-${esc(tid)}" placeholder="Write a response…"></textarea>
      <div class="d-flex flex-wrap gap-2 align-items-center mt-2">
        <button class="btn btn-sm btn-primary" data-action="reply-risk-question" data-thread="${esc(tid)}">Send reply</button>
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

function _renderRiskQuestions(threads) {
  const arr = Array.isArray(threads) ? threads : [];
  if (!riskQuestionsEl) return;
  riskQuestionsEl.innerHTML = arr.length ? arr.map(_renderRiskQuestionThread).join('') : '<span class="badge text-bg-light">none</span>';
}

async function refreshRiskQuestions() {
  if (!riskQuestionsEl || !id) return;
  try {
    const res = await apiGet(`/api/v1/questions/entities/risk/${encodeURIComponent(id)}`);
    _renderRiskQuestions(res?.threads || []);
    if (!me?.is_admin) {
      try { await apiPost(`/api/v1/questions/entities/risk/${encodeURIComponent(id)}/mark-seen`, {}); } catch {}
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
    riskQuestionsEl.innerHTML = `<div class="text-danger small">Failed to load questions: ${esc(String(e))}</div>`;
  }
}

wireRiskTabs();

const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
const canManageRisks = !!(me?.is_admin || me?.can_manage_risks);
const canAskQuestions = !!(me?.is_admin || me?.can_question_events);

function riskScoreClass(score) {
  const n = Number(score || 0);
  if (n < 5) return 'text-bg-success';
  if (n <= 12) return 'text-bg-warning';
  return 'text-bg-danger';
}

function scoreBadge(score, title = '') {
  const n = Number(score || 0);
  return `<span class="badge ${riskScoreClass(n)}"${title ? ` title="${esc(title)}"` : ''}>${esc(String(n))}</span>`;
}

function setText(el, value, fallback = '—') {
  if (!el) return;
  const txt = String(value || '').trim();
  el.textContent = txt || fallback;
}

function renderTypes(risk) {
  const types = Array.isArray(risk?.risk_types) ? risk.risk_types : [];
  if (!riskTypes) return;
  riskTypes.innerHTML = types.length
    ? types.map((t) => `<span class="badge badge-soft">${esc(t)}</span>`).join('')
    : '<span class="small-muted">No risk type</span>';
}


function renderMitigatorContext(risk) {
  const ctx = risk?.mitigator_context || {};
  const isMitigator = ctx && ctx.source === 'keen_mitigator';
  if (!riskMitigatorContextWrap || !riskMitigatorContext) return;
  if (!isMitigator) {
    riskMitigatorContextWrap.style.display = 'none';
    return;
  }
  riskMitigatorContextWrap.style.display = '';
  const weights = ctx.risk_weights || {};
  const selected = Array.isArray(ctx.selected_control_ids) ? ctx.selected_control_ids.length : 0;
  const suggested = Array.isArray(ctx.suggested_controls) ? ctx.suggested_controls : [];
  const impacts = Object.entries(ctx.impact_flags || {}).filter(([, v]) => !!v).map(([k]) => k.replaceAll('_', ' '));
  riskMitigatorContext.innerHTML = `
    <div class="mb-2"><span class="fw-semibold">Risk weighting:</span> ${esc(['Confidentiality', 'Integrity', 'Availability'].map((rt) => `${rt} ${Number(weights[rt] || 0)}`).join(' / '))}</div>
    ${impacts.length ? `<div class="mb-2"><span class="fw-semibold">Impact prompts:</span> ${esc(impacts.join(', '))}</div>` : ''}
    <div class="mb-2"><span class="fw-semibold">Suggested controls:</span> ${esc(String(suggested.length))}${selected ? esc(` (${selected} selected when created)`) : ''}</div>
    ${suggested.length ? `<div class="d-flex flex-wrap gap-1">${suggested.slice(0, 12).map((c) => `<span class="badge text-bg-light border">${esc(c.ref || '')} ${esc(c.confidence || '')}</span>`).join('')}</div>` : ''}`;
}

function renderControls(risk) {
  const items = Array.isArray(risk?.controls) ? risk.controls : [];
  if (controlsCount) controlsCount.textContent = String(items.length);
  if (!linkedControls) return;
  if (!items.length) {
    linkedControls.className = 'p-3 small-muted';
    linkedControls.innerHTML = 'No controls are mapped to this risk for the selected framework.';
    return;
  }
  linkedControls.className = 'table-responsive';
  linkedControls.innerHTML = `
    <table class="table table-sm table-hover align-middle mb-0">
      <thead>
        <tr>
          <th style="width:130px;">Control</th>
          <th>Title</th>
          <th style="width:120px;">Scope</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((c) => {
          const href = withFramework(`/control.html?id=${encodeURIComponent(c.id || '')}`, risk?.framework || framework);
          return `<tr>
            <td class="fw-semibold"><a href="${esc(href)}">${esc(c.ref || '')}</a></td>
            <td class="wrap">${esc(c.title || '')}</td>
            <td>${c.in_scope ? '<span class="badge badge-soft">In scope</span>' : '<span class="badge text-bg-light">Out of scope</span>'}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

function renderRisk(risk) {
  currentRisk = risk;
  const asset = risk?.asset || risk?.asset_entity?.name || 'Risk';
  const threat = String(risk?.threat_summary || '').trim();
  if (riskTitle) riskTitle.textContent = threat ? `${asset} — ${threat}` : asset;
  if (riskMeta) {
    const bits = [risk?.framework || framework, `updated ${fmtTs(risk?.updated_at) || 'unknown'}`].filter(Boolean);
    riskMeta.textContent = bits.join(' • ');
  }
  if (risksBreadcrumb) risksBreadcrumb.href = withFramework('/risks.html', risk?.framework || framework);
  if (riskVisualisation) riskVisualisation.href = withFramework('/risks.html?tab=visualisations', risk?.framework || framework);
  if (sampleRiskAudit) sampleRiskAudit.style.display = canSampleIntoAudit(me) ? '' : 'none';
  if (editRisk) {
    editRisk.href = withFramework(`/risks.html?edit=${encodeURIComponent(risk?.id || id)}`, risk?.framework || framework);
    editRisk.style.display = canManageRisks ? '' : 'none';
  }

  renderTypes(risk);
  setText(riskAsset, asset);
  setText(riskCategory, risk?.category?.name);
  setText(riskSubcategory, risk?.subcategory?.name);
  setText(riskOwner, risk?.risk_owner?.username);
  if (riskScore) {
    const detail = `Threat ${Number(risk?.threat_score || 0)} × Vulnerability ${Number(risk?.vulnerability_score || 0)} × Impact ${Number(risk?.impact_score || 0)}`;
    riskScore.innerHTML = `${scoreBadge(risk?.risk_score, detail)} <span class="small-muted ms-1">${esc(detail)}</span>`;
  }
  if (residualScore) {
    const detail = `Residual vulnerability ${Number(risk?.residual_vulnerability_score || 0)} × Residual impact ${Number(risk?.residual_impact_score || 0)}`;
    residualScore.innerHTML = `${scoreBadge(risk?.residual_risk_score, detail)} <span class="small-muted ms-1">${esc(detail)}</span>`;
  }
  setText(threatSummary, threat, 'No threat summary.');
  setText(riskNote, risk?.note, 'No note.');
  renderMitigatorContext(risk);
  renderControls(risk);
}



sampleRiskAudit?.addEventListener('click', () => {
  const risk = currentRisk;
  openAuditSampleModal({
    me,
    entityType: 'risk',
    entityId: risk?.id || id,
    title: risk ? `${risk.asset || 'Risk'}${risk.threat_summary ? ` — ${risk.threat_summary}` : ''}` : 'CIA Triad risk',
    framework: risk?.framework || framework,
    statusEl: status,
  });
});

if (riskAskQuestion) riskAskQuestion.style.display = canAskQuestions ? '' : 'none';

riskAskQuestion?.addEventListener('click', async () => {
  const body = String(riskQuestionBody?.value || '').trim();
  if (!id) return toast(status, 'Risk not loaded yet.', 'warning');
  if (!body) return toast(status, 'Please enter a question', 'warning');
  riskAskQuestion.disabled = true;
  const prev = riskAskQuestion.textContent;
  riskAskQuestion.textContent = 'Submitting…';
  try {
    await apiPost(`/api/v1/questions/entities/risk/${encodeURIComponent(id)}`, {body});
    if (riskQuestionBody) riskQuestionBody.value = '';
    await refreshRiskQuestions();
    toast(status, 'Question submitted', 'success');
  } catch (e) {
    toast(status, String(e), 'danger');
  } finally {
    riskAskQuestion.disabled = false;
    riskAskQuestion.textContent = prev || 'Ask question';
  }
});

riskQuestionsEl?.addEventListener('click', async (ev) => {
  const delBtn = ev.target?.closest?.('[data-action="delete-risk-question"]');
  if (delBtn && id) {
    const tid = delBtn.getAttribute('data-thread') || '';
    if (!tid || !canDeleteQuestions) return;
    if (!confirm('Delete this question thread and all replies?')) return;
    delBtn.disabled = true;
    try {
      await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
      await refreshRiskQuestions();
      toast(status, 'Question deleted', 'success');
    } catch (e) {
      toast(status, String(e), 'danger');
      delBtn.disabled = false;
    }
    return;
  }

  const btn = ev.target?.closest?.('[data-action="reply-risk-question"]');
  if (!btn || !id) return;
  const tid = btn.getAttribute('data-thread') || '';
  const bodyEl = document.getElementById(`riskReplyBody-${tid}`);
  const body = String(bodyEl?.value || '').trim();
  if (!body) return toast(status, 'Please enter a reply', 'warning');
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = 'Sending…';
  try {
    await apiPost(`/api/v1/questions/${encodeURIComponent(tid)}/posts`, {body});
    if (bodyEl) bodyEl.value = '';
    await refreshRiskQuestions();
    toast(status, 'Reply sent', 'success');
  } catch (e) {
    toast(status, String(e), 'danger');
  } finally {
    btn.disabled = false;
    btn.textContent = prev || 'Send reply';
  }
});

riskQuestionsEl?.addEventListener('change', async (ev) => {
  const sel = ev.target?.closest?.('[data-action="set-risk-question-status"]');
  if (!sel || !id) return;
  const tid = sel.getAttribute('data-thread') || '';
  const newStatus = String(sel.value || '').trim();
  if (!tid || !newStatus) return;
  try {
    await apiPatch(`/api/v1/admin/questions/${encodeURIComponent(tid)}`, {status: newStatus});
    await refreshRiskQuestions();
  } catch (e) {
    toast(status, String(e), 'danger');
  }
});

async function loadRisk() {
  // Try the API even when the user lacks global risk.read: risk owners can
  // view their own risks from Account → Risks I own. The backend still
  // enforces risk.read/ownership.
  if (!id) {
    toast(status, 'Missing risk id.', 'danger');
    return;
  }
  try {
    const u = new URL(`/api/v1/risks/${encodeURIComponent(id)}`, location.origin);
    if (framework) u.searchParams.set('framework', framework);
    const risk = await apiGet(u.pathname + u.search);
    renderRisk(risk);
    if (qs('tab') === 'changelog') await loadRiskChangelog();
    if (qs('tab') === 'questions') await refreshRiskQuestions();
  } catch (e) {
    toast(status, `Failed to load risk: ${String(e)}`, 'danger');
    if (linkedControls) linkedControls.innerHTML = '<span class="text-danger">Failed to load linked controls.</span>';
  }
}

await loadRisk();
