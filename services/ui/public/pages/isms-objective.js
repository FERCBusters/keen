import {
  initNavbar,
  apiGet,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  userPillHtml,
  canSampleIntoAudit,
  openAuditSampleModal,
  loadEntityChangelog,
} from '/app.js';
import {activateTabFromHashOrQuery, clauseBadges, controlBadges} from '/pages/isms-detail-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const objectiveId = params.get('id') || params.get('objective') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me);
let objective = null;

function objectiveHref(id = objectiveId) {
  return withFramework(`/isms-objective.html?id=${encodeURIComponent(id || '')}`, framework);
}

function statusLabel(value) {
  const raw = String(value || 'not_started');
  return raw.replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function userBadges(items) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.map((user) => userPillHtml(user)).join(' ');
}

function renderObjective(row) {
  objective = row || {};
  const title = objective.goal || objective.requirement || 'ISMS objective';
  document.title = `Keen – ${title}`;
  $('objectiveTitle').textContent = title;
  $('objectiveMeta').textContent = [objective.completion_target_date || '', statusLabel(objective.status), `updated ${fmtTs(objective.updated_at) || '—'}`].filter(Boolean).join(' • ');
  $('objectiveRequirement').textContent = objective.requirement || 'No requirement recorded.';
  $('objectiveGoal').textContent = objective.goal || 'No goal recorded.';
  $('objectiveMetric').textContent = objective.metric || 'No metric recorded.';
  $('objectiveCompletion').textContent = objective.completion_method || 'No completion method recorded.';
  $('objectiveEvaluation').textContent = objective.evaluation_method || 'No evaluation method recorded.';
  $('objectiveOwner').innerHTML = userPillHtml(objective.owner);
  $('objectiveResources').innerHTML = userBadges(objective.resource_users);
  $('objectiveResourceText').textContent = objective.resource_requirements_text || '—';
  $('objectiveTarget').textContent = objective.completion_target_date || '—';
  $('objectiveStatusValue').textContent = statusLabel(objective.status);
  $('objectiveUpdated').textContent = fmtTs(objective.updated_at) || '—';
  $('objectiveControls').innerHTML = controlBadges(objective.controls, framework);
  $('objectiveClauses').innerHTML = clauseBadges(objective.clauses, framework);

  const edit = $('editObjectiveLink');
  if (edit) {
    edit.href = withFramework(`/isms.html?tab=objectives&edit=objective:${encodeURIComponent(objective.id || '')}`, framework);
    edit.style.display = canManageIsms ? '' : 'none';
  }
  const sample = $('sampleObjectiveAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

async function loadChangelog() {
  await loadEntityChangelog(
    $('objectiveChangelog'),
    `/api/v1/isms/objective/${encodeURIComponent(objectiveId)}/changelog?limit=100`,
    {empty: 'No objective changes have been recorded yet.'}
  );
}

async function load() {
  if (!objectiveId) {
    toast(status, 'Missing objective id.', 'danger');
    return;
  }
  try {
    const row = await apiGet(`/api/v1/isms/objectives/${encodeURIComponent(objectiveId)}?framework=${encodeURIComponent(framework)}`);
    renderObjective(row);
    await loadChangelog();
    activateTabFromHashOrQuery(params, {changelog: $('objectiveChangelogTab')});
  } catch (e) {
    toast(status, `Failed to load objective: ${String(e)}`, 'danger');
  }
}

$('sampleObjectiveAudit')?.addEventListener('click', () => {
  if (!objective) return;
  openAuditSampleModal({
    me,
    entityType: 'isms_objective',
    entityId: objective.id,
    title: objective.goal || objective.requirement || 'ISMS objective',
    evidenceUrl: objectiveHref(objective.id),
    framework,
    statusEl: status,
  });
});

window.addEventListener('hashchange', () => activateTabFromHashOrQuery(params, {changelog: $('objectiveChangelogTab')}));
load();
