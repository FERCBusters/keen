import {initNavbar, apiGet, apiDelete, esc, fmtTs, toast, getCurrentFramework, withFramework, safeExternalHref} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const canDeleteQuestions = !!(me?.is_admin || me?.can_delete_questions);

const statusEl = document.getElementById('status');
const tbodyEl = document.getElementById('questionsTbody');
const emptyEl = document.getElementById('questionsEmpty');
const tableWrapEl = document.getElementById('questionsTableWrap');
const refreshBtn = document.getElementById('btnRefresh');

function _statusBadge(st, unread) {
  const s = String(st || 'unanswered').toLowerCase();
  if (unread) return `<span class="badge text-bg-warning">new reply</span>`;
  if (s === 'answered') return `<span class="badge text-bg-success">answered</span>`;
  if (s === 'reviewing') return `<span class="badge text-bg-info">reviewing</span>`;
  return `<span class="badge text-bg-secondary">unanswered</span>`;
}

function _questionHref(it) {
  const href = withFramework(it?.target_url || `/event.html?id=${encodeURIComponent(it?.event_id || '')}&thread=${encodeURIComponent(it?.thread_id || '')}#questions`, framework);
  // Defence-in-depth: validate scheme so a future user-influenced target_url
  // cannot produce a javascript:/data: href.
  return safeExternalHref(href) || '#';
}

function _row(it) {
  const href = _questionHref(it);
  const title = it.target_label || it.event_summary || '(no summary)';
  const src = it.target_type && it.target_type !== 'event' ? String(it.target_type).replaceAll('_', ' ') : (it.event_source || '');
  const ts = it.event_timestamp ? fmtTs(it.event_timestamp) : (it.target_ref || '—');
  const updated = it.updated_at ? fmtTs(it.updated_at) : '—';
  const badge = _statusBadge(it.status, !!it.unread);

  return `
    <tr class="${it.unread ? 'table-warning' : ''}">
      <td class="wrap">
        <div class="fw-semibold">${esc(title)}</div>
        <div class="small-muted">${esc(src)} • ${esc(ts)}</div>
      </td>
      <td>${badge}</td>
      <td class="small-muted">${esc(updated)}</td>
      <td class="text-end">
        <div class="btn-group btn-group-sm" role="group" aria-label="Question actions">
          <a class="btn btn-outline-primary" href="${esc(href)}">Open</a>
          ${canDeleteQuestions ? `<button class="btn btn-outline-danger" type="button" data-delete-question="${esc(it.thread_id || '')}">Delete</button>` : ''}
        </div>
      </td>
    </tr>
  `;
}


async function deleteQuestion(threadId) {
  const tid = String(threadId || '').trim();
  if (!tid || !canDeleteQuestions) return;
  if (!confirm('Delete this question thread and all replies?')) return;
  try {
    await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
    toast(statusEl, 'Question deleted', 'success');
    await load();
  } catch (err) {
    toast(statusEl, err?.message || 'Failed to delete question.', 'danger');
  }
}

async function load() {
  if (!tbodyEl) return;
  try {
    if (statusEl) statusEl.style.display = 'none';
    const res = await apiGet('/api/v1/me/questions');
    const items = Array.isArray(res?.items) ? res.items : [];

    if (items.length === 0) {
      if (emptyEl) emptyEl.style.display = '';
      if (tableWrapEl) tableWrapEl.style.display = 'none';
      tbodyEl.innerHTML = '';
    } else {
      if (emptyEl) emptyEl.style.display = 'none';
      if (tableWrapEl) tableWrapEl.style.display = '';
      tbodyEl.innerHTML = items.map(_row).join('');
    }

    // Keep the navbar badge in sync if the user hits refresh here.
    try {
 window.keenRefreshQuestionBell?.();
    } catch {}
  } catch (err) {
    const msg = `Failed to load questions: ${String(err)}`;
    if (statusEl) {
      statusEl.className = 'alert alert-danger';
      statusEl.style.display = '';
      statusEl.textContent = msg;
    } else {
      toast(statusEl, msg, 'danger');
    }
  }
}

refreshBtn?.addEventListener('click', load);
tbodyEl?.addEventListener('click', async (ev) => {
  const btn = ev.target?.closest?.('[data-delete-question]');
  if (!btn) return;
  ev.preventDefault();
  await deleteQuestion(btn.getAttribute('data-delete-question'));
});

// Initial load
await load();
