import {initNavbar, apiGet, apiDelete, esc, fmtTs, toast, getCurrentFramework, withFramework, safeExternalHref, userPillHtml} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const isAdmin = !!me?.is_admin;
const canDeleteQuestions = !!(me?.is_admin || me?.can_delete_questions);

const statusEl = document.getElementById('status');
const pageTitleEl = document.getElementById('pageTitle');
const pageSubtitleEl = document.getElementById('pageSubtitle');
const adminCardEl = document.getElementById('adminCard');
const userCardEl = document.getElementById('userCard');
const queueBodyEl = document.getElementById('queueBody');
const queueStatusEl = document.getElementById('queueStatus');
const questionsTbodyEl = document.getElementById('questionsTbody');
const emptyEl = document.getElementById('questionsEmpty');
const tableWrapEl = document.getElementById('questionsTableWrap');

function showError(message) {
  if (!statusEl) return;
  statusEl.className = 'alert alert-danger';
  statusEl.style.display = '';
  statusEl.textContent = message;
}

function clearError() {
  if (statusEl) statusEl.style.display = 'none';
}

function statusBadge(status, unread = false) {
  const s = String(status || 'unanswered').toLowerCase();
  if (unread) return '<span class="badge text-bg-warning">new reply</span>';
  if (s === 'answered') return '<span class="badge text-bg-success">answered</span>';
  if (s === 'reviewing') return '<span class="badge text-bg-info">reviewing</span>';
  return '<span class="badge text-bg-secondary">unanswered</span>';
}

function questionHref(it) {
  const href = withFramework(it?.target_url || `/event.html?id=${encodeURIComponent(it?.event_id || '')}&thread=${encodeURIComponent(it?.thread_id || '')}#questions`, framework);
  // Defence-in-depth: target_url is server-constructed today, but validate the
  // scheme so a future change that lets it carry user input cannot yield a
  // javascript:/data: href. safeExternalHref returns '' for unsafe URLs.
  return safeExternalHref(href) || '#';
}

function questionTitle(it) {
  return it?.target_label || it?.event_summary || '(no summary)';
}

function questionSubline(it) {
  const typ = it?.target_type && it.target_type !== 'event' ? it.target_type.replaceAll('_', ' ') : (it?.event_source || '');
  const ts = it?.event_timestamp ? fmtTs(it.event_timestamp) : (it?.target_ref || '—');
  return [typ, ts].filter(Boolean).join(' • ');
}

function renderAdmin(items) {
  if (!queueBodyEl) return;
  const rows = (items || []).map((it) => {
    const href = questionHref(it);
    return `
      <tr>
        <td>${statusBadge(it.status)}</td>
        <td class="small-muted">${esc(it.updated_at ? fmtTs(it.updated_at) : '—')}</td>
        <td class="wrap">
          <div class="fw-semibold">${esc(questionTitle(it))}</div>
          <div class="small-muted">${esc(questionSubline(it))}</div>
        </td>
        <td>${userPillHtml(it.created_by_username)}</td>
        <td class="text-end">
          <div class="btn-group btn-group-sm" role="group" aria-label="Question actions">
            <a class="btn btn-outline-primary" href="${esc(href)}">Open</a>
            ${canDeleteQuestions ? `<button class="btn btn-outline-danger" type="button" data-delete-question="${esc(it.thread_id || '')}">Delete</button>` : ''}
          </div>
        </td>
      </tr>
    `;
  }).join('');
  queueBodyEl.innerHTML = rows || '<tr><td colspan="5" class="small-muted">No matching questions.</td></tr>';
}

function renderUser(items) {
  if (!questionsTbodyEl) return;
  if (!items.length) {
    if (emptyEl) emptyEl.style.display = '';
    if (tableWrapEl) tableWrapEl.style.display = 'none';
    questionsTbodyEl.innerHTML = '';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  if (tableWrapEl) tableWrapEl.style.display = '';
  questionsTbodyEl.innerHTML = items.map((it) => {
    const href = questionHref(it);
    return `
      <tr class="${it.unread ? 'table-warning' : ''}">
        <td class="wrap">
          <div class="fw-semibold">${esc(questionTitle(it))}</div>
          <div class="small-muted">${esc(questionSubline(it))}</div>
        </td>
        <td>${statusBadge(it.status, !!it.unread)}</td>
        <td class="small-muted">${esc(it.updated_at ? fmtTs(it.updated_at) : '—')}</td>
        <td class="text-end">
          <div class="btn-group btn-group-sm" role="group" aria-label="Question actions">
            <a class="btn btn-outline-primary" href="${esc(href)}">Open</a>
            ${canDeleteQuestions ? `<button class="btn btn-outline-danger" type="button" data-delete-question="${esc(it.thread_id || '')}">Delete</button>` : ''}
          </div>
        </td>
      </tr>
    `;
  }).join('');
}


async function deleteQuestion(threadId) {
  const tid = String(threadId || '').trim();
  if (!tid) return;
  if (!confirm('Delete this question thread and all replies?')) return;
  try {
    await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
    toast(statusEl, 'Question deleted', 'success');
    if (isAdmin) await loadAdmin();
    else await loadUser();
  } catch (err) {
    toast(statusEl, err?.message || 'Failed to delete question.', 'danger');
  }
}

function wireDeleteClicks(root) {
  root?.addEventListener('click', async (ev) => {
    const btn = ev.target?.closest?.('[data-delete-question]');
    if (!btn || !canDeleteQuestions) return;
    ev.preventDefault();
    await deleteQuestion(btn.getAttribute('data-delete-question'));
  });
}

async function loadAdmin() {
  clearError();
  const url = new URL('/api/v1/admin/questions', location.origin);
  const status = String(queueStatusEl?.value || '').trim();
  if (status) url.searchParams.set('status', status);
  const data = await apiGet(url.pathname + url.search);
  renderAdmin(Array.isArray(data?.items) ? data.items : []);
  try { window.mospRefreshBell?.(); } catch {}
}

async function loadUser() {
  clearError();
  const data = await apiGet('/api/v1/me/questions');
  renderUser(Array.isArray(data?.items) ? data.items : []);
  try { window.mospRefreshBell?.(); } catch {}
}

async function load() {
  try {
    if (isAdmin) {
      pageTitleEl.textContent = 'Questions';
      pageSubtitleEl.textContent = 'Open and answered question threads across evidence, CIA Triad risks and PESTLE(E) items.';
      adminCardEl.style.display = '';
      userCardEl.style.display = 'none';
      await loadAdmin();
    } else {
      pageTitleEl.textContent = 'Questions';
      pageSubtitleEl.textContent = 'Threads you have opened on evidence, CIA Triad risks and PESTLE(E) items, including any admin responses.';
      userCardEl.style.display = '';
      adminCardEl.style.display = 'none';
      await loadUser();
    }
  } catch (err) {
    showError(`Failed to load questions: ${err?.message || String(err)}`);
    toast(statusEl, err?.message || 'Failed to load questions.', 'danger');
  }
}

document.getElementById('btnRefresh')?.addEventListener('click', loadAdmin);
document.getElementById('btnRefreshUser')?.addEventListener('click', loadUser);
queueStatusEl?.addEventListener('change', loadAdmin);
wireDeleteClicks(queueBodyEl);
wireDeleteClicks(questionsTbodyEl);

await load();
