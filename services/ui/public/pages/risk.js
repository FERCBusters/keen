import { mountQuestionThreads } from '/pages/question-threads.js';
import {
  initNavbar,
  apiGet,
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
import {
  methodBadgeHtml,
  methodOf,
  partyBadgesHtml,
  treatmentLabel,
  treatmentStatusLabel,
  riskDisplayTitle,
  riskWizardHref,
} from '/pages/risk-common.js';

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
const riskParties = document.getElementById('riskParties');
const riskTreatment = document.getElementById('riskTreatment');
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
const riskQuestions = mountQuestionThreads({
  panel: document.getElementById('riskQuestions'),
  me,
  status,
  placeholder: 'Write a question about this risk',
  base: () => (id ? `/api/v1/questions/entities/risk/${encodeURIComponent(id)}` : null),
});
let currentRisk = null;

function setRiskTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'details') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}?${p.toString()}`);
}

function activateRiskTab(tabName) {
  const tab =
    tabName === 'changelog'
      ? riskChangelogTab
      : tabName === 'questions'
        ? riskQuestionsTab
        : riskDetailsTab;
  if (!tab || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

async function loadRiskChangelog() {
  if (!riskChangelogEl || !id) return;
  const u = new URL(`/api/v1/risks/${encodeURIComponent(id)}/changelog`, location.origin);
  if (framework) u.searchParams.set('framework', framework);
  u.searchParams.set('limit', '100');
  await loadEntityChangelog(riskChangelogEl, u.pathname + u.search, {
    empty: 'No risk changes have been recorded yet.',
  });
}

function wireRiskTabs() {
  riskDetailsTab?.addEventListener('shown.bs.tab', () => setRiskTabQuery('details'));
  riskQuestionsTab?.addEventListener('shown.bs.tab', () => {
    setRiskTabQuery('questions');
    riskQuestions.refresh();
  });
  riskChangelogTab?.addEventListener('shown.bs.tab', () => {
    setRiskTabQuery('changelog');
    loadRiskChangelog();
  });
  activateRiskTab(qs('tab', 'details'));
}

wireRiskTabs();

const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
const canManageRisks = !!(me?.is_admin || me?.can_manage_risks);

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

function setText(el, value, fallback = '-') {
  if (!el) return;
  const txt = String(value || '').trim();
  el.textContent = txt || fallback;
}

function renderTypes(risk) {
  const types = Array.isArray(risk?.risk_types) ? risk.risk_types : [];
  if (!riskTypes) return;
  riskTypes.innerHTML =
    methodBadgeHtml(methodOf(risk)) +
    types.map((t) => ` <span class="badge badge-soft">${esc(t)}</span>`).join('');
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
  const impacts = Object.entries(ctx.impact_flags || {})
    .filter(([, v]) => !!v)
    .map(([k]) => k.replaceAll('_', ' '));
  riskMitigatorContext.innerHTML = `
    <div class="mb-2"><span class="fw-semibold">Risk weighting:</span> ${esc(['Confidentiality', 'Integrity', 'Availability'].map((rt) => `${rt} ${Number(weights[rt] || 0)}`).join(' / '))}</div>
    ${impacts.length ? `<div class="mb-2"><span class="fw-semibold">Impact prompts:</span> ${esc(impacts.join(', '))}</div>` : ''}
    <div class="mb-2"><span class="fw-semibold">Suggested controls:</span> ${esc(String(suggested.length))}${selected ? esc(` (${selected} selected when created)`) : ''}</div>
    ${
      suggested.length
        ? `<div class="d-flex flex-wrap gap-1">${suggested
            .slice(0, 12)
            .map(
              (c) =>
                `<span class="badge text-bg-light border">${esc(c.ref || '')} ${esc(c.confidence || '')}</span>`,
            )
            .join('')}</div>`
        : ''
    }`;
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
        ${items
          .map((c) => {
            const href = withFramework(
              `/control.html?id=${encodeURIComponent(c.id || '')}`,
              risk?.framework || framework,
            );
            return `<tr>
            <td class="fw-semibold"><a href="${esc(href)}">${esc(c.ref || '')}</a></td>
            <td class="wrap">${esc(c.title || '')}</td>
            <td>${c.in_scope ? '<span class="badge badge-soft">In scope</span>' : '<span class="badge text-bg-light">Out of scope</span>'}</td>
          </tr>`;
          })
          .join('')}
      </tbody>
    </table>`;
}

function renderRisk(risk) {
  currentRisk = risk;
  const asset = risk?.asset || risk?.asset_entity?.name || 'Risk';
  const threat = String(risk?.threat_summary || '').trim();
  if (riskTitle) riskTitle.textContent = riskDisplayTitle(risk);
  if (riskMeta) {
    const bits = [
      risk?.framework || framework,
      `updated ${fmtTs(risk?.updated_at) || 'unknown'}`,
    ].filter(Boolean);
    riskMeta.textContent = bits.join(' · ');
  }
  if (risksBreadcrumb)
    risksBreadcrumb.href = withFramework('/risks.html', risk?.framework || framework);
  if (riskVisualisation)
    riskVisualisation.href = withFramework(
      '/risks.html?tab=visualisations',
      risk?.framework || framework,
    );
  if (sampleRiskAudit) sampleRiskAudit.style.display = canSampleIntoAudit(me, 'risk') ? '' : 'none';
  if (editRisk) {
    editRisk.href = riskWizardHref(risk?.framework || framework, { id: risk?.id || id });
    editRisk.style.display = canManageRisks ? '' : 'none';
  }

  renderTypes(risk);
  setText(riskAsset, asset);
  setText(riskCategory, risk?.category?.name);
  setText(riskSubcategory, risk?.subcategory?.name);
  setText(riskOwner, risk?.risk_owner?.username);
  if (riskScore) {
    const detail = `Inherent likelihood ${Number(risk?.register_likelihood || 0)} × impact ${Number(risk?.register_impact || 0)}`;
    riskScore.innerHTML = `${scoreBadge(risk?.risk_score, detail)} <span class="small-muted ms-1">${esc(detail)}</span>`;
  }
  if (residualScore) {
    const detail = `Residual likelihood ${Number(risk?.register_residual_likelihood || 0)} × impact ${Number(risk?.register_residual_impact || 0)}`;
    residualScore.innerHTML = `${scoreBadge(risk?.residual_risk_score, detail)} <span class="small-muted ms-1">${esc(detail)}</span>`;
  }
  if (riskParties)
    riskParties.innerHTML = partyBadgesHtml(risk?.interested_parties, risk?.framework || framework);
  if (riskTreatment) {
    const due = risk?.treatment_due_at ? ` · due ${esc(risk.treatment_due_at)}` : '';
    riskTreatment.innerHTML = `${esc(treatmentLabel(risk?.treatment_strategy))} <span class="small-muted">(${esc(treatmentStatusLabel(risk?.treatment_status))}${due})</span>${risk?.treatment_plan ? `<div class="small whitespace-pre-wrap mt-1">${esc(risk.treatment_plan)}</div>` : ''}`;
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
    title: risk ? riskDisplayTitle(risk) : 'Risk',
    framework: risk?.framework || framework,
    statusEl: status,
  });
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
    if (qs('tab') === 'questions') await riskQuestions.refresh();
  } catch (e) {
    toast(status, `Failed to load risk: ${String(e)}`, 'danger');
    if (linkedControls)
      linkedControls.innerHTML = '<span class="text-danger">Failed to load linked controls.</span>';
  }
}

await loadRisk();

import { mountSwotRelated } from '/pages/swot-related.js';
import { mountStoryRelated } from '/pages/story-related.js';
mountSwotRelated('risk', new URLSearchParams(location.search).get('id'));
mountStoryRelated({ me, kind: 'risk', id: new URLSearchParams(location.search).get('id') });
