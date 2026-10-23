import { initNavbar, apiGet, esc, fmtTs, toast, getCurrentFramework, userPillHtml } from '/app.js';
import {
  deleteQuestionThread,
  myQuestionRowHtml,
  questionActionsHtml,
  questionStatusBadge,
  questionSubline,
  questionTitle,
} from '/pages/question-threads.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const isAdmin = !!me?.is_admin;
const canDelete = !!(me?.is_admin || me?.can_delete_questions);

const statusEl = document.getElementById('status');
const queueBodyEl = document.getElementById('queueBody');
const queueStatusEl = document.getElementById('queueStatus');
const questionsTbodyEl = document.getElementById('questionsTbody');
const emptyEl = document.getElementById('questionsEmpty');
const tableWrapEl = document.getElementById('questionsTableWrap');

// Administrators work through every thread; everyone else sees their own.
function renderQueue(items) {
  queueBodyEl.innerHTML =
    items
      .map(
        (it) => `<tr>
        <td>${questionStatusBadge(it.status)}</td>
        <td class="small-muted">${esc(it.updated_at ? fmtTs(it.updated_at) : '-')}</td>
        <td class="wrap">
          <div class="fw-semibold">${esc(questionTitle(it))}</div>
          <div class="small-muted">${esc(questionSubline(it))}</div>
        </td>
        <td>${userPillHtml(it.created_by_username)}</td>
        <td class="text-end">${questionActionsHtml(it, { framework, canDelete })}</td>
      </tr>`,
      )
      .join('') || '<tr><td colspan="5" class="small-muted">No matching questions.</td></tr>';
}

function renderOwn(items) {
  emptyEl.style.display = items.length ? 'none' : '';
  tableWrapEl.style.display = items.length ? '' : 'none';
  questionsTbodyEl.innerHTML = items
    .map((it) => myQuestionRowHtml(it, { framework, canDelete }))
    .join('');
}

async function load() {
  statusEl.style.display = 'none';
  try {
    if (isAdmin) {
      const url = new URL('/api/v1/admin/questions', location.origin);
      const status = String(queueStatusEl.value || '').trim();
      if (status) url.searchParams.set('status', status);
      const data = await apiGet(url.pathname + url.search);
      renderQueue(Array.isArray(data?.items) ? data.items : []);
    } else {
      const data = await apiGet('/api/v1/me/questions');
      renderOwn(Array.isArray(data?.items) ? data.items : []);
    }
    // Keep the navbar's question count in step with the list.
    window.keenRefreshBell?.();
  } catch (err) {
    statusEl.className = 'alert alert-danger';
    statusEl.style.display = '';
    statusEl.textContent = `Failed to load questions: ${err?.message || String(err)}`;
  }
}

async function deleteClicked(ev) {
  const button = ev.target.closest?.('[data-delete-question]');
  if (!button || !canDelete) return;
  ev.preventDefault();
  try {
    if (await deleteQuestionThread(button.dataset.deleteQuestion)) {
      toast(statusEl, 'Question deleted', 'success');
      await load();
    }
  } catch (err) {
    toast(statusEl, err?.message || 'Failed to delete question.', 'danger');
  }
}

document.getElementById('pageSubtitle').textContent = isAdmin
  ? 'Open and answered question threads across evidence, risks, PESTLE(E) items, SWOT items and stories.'
  : 'Threads you have opened on evidence, risks, PESTLE(E) items, SWOT items and stories, with their replies.';
document.getElementById(isAdmin ? 'adminCard' : 'userCard').style.display = '';
document.getElementById('btnRefresh').addEventListener('click', load);
document.getElementById('btnRefreshUser').addEventListener('click', load);
queueStatusEl.addEventListener('change', load);
queueBodyEl.addEventListener('click', deleteClicked);
questionsTbodyEl.addEventListener('click', deleteClicked);

await load();
