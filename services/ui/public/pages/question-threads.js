// Questions: the thread panel shared by every record that takes questions
// (evidence events, risks, PESTLE(E) items, SWOT items and stories), and the
// row helpers for the question queue and each person's own questions.
import { featureEnabled } from '/pages/feature-availability.js';
import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiPostForm,
  esc,
  fmtTs,
  safeExternalHref,
  toast,
  withFramework,
} from '/app.js';

const STATUSES = ['unanswered', 'reviewing', 'answered'];
const STATUS_BADGES = {
  unanswered: 'text-bg-secondary',
  reviewing: 'text-bg-info',
  answered: 'text-bg-success',
};
const REPLY_RULE = 'Only admins or the original author can reply.';

function statusOf(value) {
  const status = String(value || '').toLowerCase();
  return STATUSES.includes(status) ? status : 'unanswered';
}

export function questionStatusBadge(value, unread = false) {
  if (unread) return '<span class="badge text-bg-warning">new reply</span>';
  const status = statusOf(value);
  return `<span class="badge ${STATUS_BADGES[status]}">${status}</span>`;
}

// Where a question opens: the record's address from the server, or the
// event's Questions tab.
export function questionHref(item, framework) {
  const href = withFramework(
    item?.target_url ||
      `/event.html?id=${encodeURIComponent(item?.event_id || '')}&thread=${encodeURIComponent(item?.thread_id || '')}#questions`,
    framework,
  );
  // target_url is built by the server; checking the scheme keeps a future
  // user-influenced value from becoming a javascript: or data: link.
  return safeExternalHref(href) || '#';
}

export function questionTitle(item) {
  return item?.target_label || item?.event_summary || '(no summary)';
}

export function questionSubline(item) {
  const kind =
    item?.target_type && item.target_type !== 'event'
      ? String(item.target_type).replaceAll('_', ' ')
      : item?.event_source || '';
  const when = item?.event_timestamp ? fmtTs(item.event_timestamp) : item?.target_ref || '-';
  return [kind, when].filter(Boolean).join(' · ');
}

export function questionActionsHtml(item, { framework, canDelete }) {
  const remove = canDelete
    ? `<button class="btn btn-outline-danger" type="button" data-delete-question="${esc(item?.thread_id || '')}">Delete</button>`
    : '';
  return `<div class="btn-group btn-group-sm" role="group" aria-label="Question actions"><a class="btn btn-outline-primary" href="${esc(questionHref(item, framework))}">Open</a>${remove}</div>`;
}

// Asks first; resolves to true once the thread is deleted.
export async function deleteQuestionThread(threadId) {
  if (!confirm('Delete this question thread and all replies?')) return false;
  await apiDelete(`/api/v1/questions/${encodeURIComponent(threadId)}`);
  return true;
}

// A row of the signed-in person's own questions: the record, the status (or
// an unread reply), the last update and the actions.
export function myQuestionRowHtml(item, options) {
  return `<tr class="${item.unread ? 'table-warning' : ''}">
      <td class="wrap">
        <div class="fw-semibold">${esc(questionTitle(item))}</div>
        <div class="small-muted">${esc(questionSubline(item))}</div>
      </td>
      <td>${questionStatusBadge(item.status, !!item.unread)}</td>
      <td class="small-muted">${esc(item.updated_at ? fmtTs(item.updated_at) : '-')}</td>
      <td class="text-end">${questionActionsHtml(item, options)}</td>
    </tr>`;
}

// The question threads of one record: an Ask a question form, then each thread
// with its posts, status and reply box. `base()` returns the record's question
// address, or null while the record is unsaved; the list is read from it, new
// questions are posted to it and `${base}/mark-seen` clears unread replies.
// Evidence questions take attachments.
export function mountQuestionThreads({
  panel,
  me,
  base,
  status,
  attachments = false,
  placeholder = 'Write a question',
  unsaved = 'Save the record first.',
  markSeen = true,
  onCount,
  onFocus,
}) {
  const canAsk = !!(me?.is_admin || me?.can_create_questions);
  const canDelete = !!(me?.is_admin || me?.can_delete_questions);
  const focusId = String(new URLSearchParams(location.search).get('thread') || '');
  let focused = false;
  const fileInput = (name, label) =>
    attachments
      ? `<input class="form-control form-control-sm mt-2" type="file" name="${name}" multiple aria-label="${label}">`
      : '';
  panel.innerHTML = `<form class="no-print mb-3" data-question-ask hidden>
      <label class="form-label" for="questionBody">Ask a question</label>
      <textarea id="questionBody" name="body" class="form-control" rows="3" placeholder="${esc(placeholder)}"></textarea>
      ${fileInput('attachments', 'Question attachments')}
      <div class="d-flex justify-content-end mt-2"><button class="btn btn-sm btn-primary" type="submit">Ask question</button></div>
    </form>
    <div data-question-list class="small-muted">Loading questions</div>`;
  const form = panel.querySelector('[data-question-ask]');
  const list = panel.querySelector('[data-question-list]');

  function send(url, body, files) {
    if (!attachments) return apiPost(url, { body });
    const data = new FormData();
    data.append('body', body);
    for (const file of [...(files?.files || [])].slice(0, 10))
      data.append('attachments', file, file.name);
    return apiPostForm(url, data);
  }

  function attachmentLinks(items) {
    const links = (Array.isArray(items) ? items : [])
      .map((a) => {
        const href = safeExternalHref(a?.download_url);
        if (!href) return '';
        const name = a.filename || `attachment-${String(a.artifact_id || '').slice(0, 8)}`;
        return `<a class="mono" href="${esc(href)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-paperclip"></i> ${esc(name)}</a>`;
      })
      .filter(Boolean)
      .join(' ');
    return links ? `<div class="question-attachments small-muted mt-1">${links}</div>` : '';
  }

  function postHtml(post) {
    return `<div class="question-post">
        <div class="question-post-meta"><div class="small-muted"><span class="fw-bold">${esc(post?.author_username || '-')}</span> · ${esc(post?.created_at ? fmtTs(post.created_at) : '-')}</div></div>
        <div class="mt-2">${esc(post?.body || '').replace(/\n/g, '<br>')}</div>
        ${attachmentLinks(post?.attachments)}
      </div>`;
  }

  function threadHtml(thread) {
    const tid = esc(thread?.id || '');
    const current = statusOf(thread?.status);
    const author =
      me?.user_id && thread?.created_by_user_id
        ? String(me.user_id) === String(thread.created_by_user_id)
        : false;
    const menu = me?.is_admin
      ? `<select class="form-select form-select-sm w-auto" data-question-action="status" aria-label="Question status">${STATUSES.map((s) => `<option value="${s}"${s === current ? ' selected' : ''}>${s}</option>`).join('')}</select>`
      : '';
    const remove = canDelete
      ? `<button class="btn btn-sm btn-outline-danger" type="button" data-question-action="delete">Delete</button>`
      : '';
    const reply =
      me?.is_admin || author
        ? `<div class="mt-3 no-print">
            <label class="small-muted mb-1" for="questionReply-${tid}">Reply</label>
            <textarea class="form-control" rows="3" id="questionReply-${tid}" placeholder="Write a response"></textarea>
            ${fileInput('replyAttachments', 'Reply attachments')}
            <div class="d-flex flex-wrap gap-2 align-items-center mt-2"><button class="btn btn-sm btn-primary" type="button" data-question-action="reply">Send reply</button></div>
            <div class="form-text">${REPLY_RULE}</div>
          </div>`
        : `<div class="small-muted mt-3">${REPLY_RULE}</div>`;
    const posts = (Array.isArray(thread?.posts) ? thread.posts : []).map(postHtml).join('');
    return `<div class="question-thread" id="thread-${tid}" data-thread="${tid}">
        <div class="question-header">
          <div>
            <div class="question-title">Thread by <span class="mono">${esc(thread?.created_by_username || '-')}</span></div>
            <div class="small-muted">Created ${esc(thread?.created_at ? fmtTs(thread.created_at) : '-')} · Updated ${esc(thread?.updated_at ? fmtTs(thread.updated_at) : '-')}</div>
          </div>
          <div class="d-flex flex-wrap gap-2 align-items-center">${questionStatusBadge(current)}${menu}${remove}</div>
        </div>
        ${posts || '<div class="small-muted mt-2">No posts yet.</div>'}
        ${reply}
      </div>`;
  }

  // Opens the thread named in ?thread= once, for links from notifications.
  function focusRequestedThread() {
    const el = focusId && !focused ? document.getElementById(`thread-${focusId}`) : null;
    if (!el) return;
    focused = true;
    onFocus?.();
    el.classList.add('question-thread-focus');
    try {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch {
      el.scrollIntoView();
    }
  }

  async function refresh() {
    if (!featureEnabled(me, 'questions')) return;
    const url = base();
    form.hidden = !url || !canAsk;
    if (!url) {
      list.innerHTML = `<div class="small-muted">${esc(unsaved)}</div>`;
      return;
    }
    try {
      const res = await apiGet(url);
      const threads = Array.isArray(res?.threads) ? res.threads : [];
      list.innerHTML =
        threads.map(threadHtml).join('') || '<div class="small-muted">No questions yet.</div>';
      onCount?.(threads.length);
      // Reading the threads clears their unread replies for the author.
      if (markSeen && !me?.is_admin) {
        try {
          await apiPost(`${url}/mark-seen`, {});
        } catch {
          // The navbar count catches up on its next poll.
        }
      }
      focusRequestedThread();
    } catch (err) {
      list.innerHTML = `<div class="text-danger small">Failed to load questions: ${esc(String(err))}</div>`;
    }
  }

  // Disables `button` while `work` runs, showing `busy` as its label.
  async function busyWhile(button, busy, work) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = busy;
    try {
      await work();
    } catch (err) {
      toast(status, String(err), 'danger');
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const url = base();
    const body = form.elements.body.value.trim();
    if (!url) return;
    if (!body) return toast(status, 'Please enter a question', 'warning');
    await busyWhile(form.querySelector('[type="submit"]'), 'Submitting', async () => {
      await send(url, body, form.elements.attachments);
      form.reset();
      await refresh();
      toast(status, 'Question submitted', 'success');
    });
  });

  list.addEventListener('click', async (ev) => {
    const button = ev.target.closest?.('[data-question-action]');
    const thread = button?.closest('[data-thread]');
    if (!thread) return;
    const tid = thread.dataset.thread;
    if (button.dataset.questionAction === 'delete') {
      await busyWhile(button, 'Delete', async () => {
        if (!(await deleteQuestionThread(tid))) return;
        await refresh();
        toast(status, 'Question deleted', 'success');
      });
      return;
    }
    if (button.dataset.questionAction !== 'reply') return;
    const body = thread.querySelector('textarea').value.trim();
    if (!body) return toast(status, 'Please enter a reply', 'warning');
    await busyWhile(button, 'Sending', async () => {
      await send(
        `/api/v1/questions/${encodeURIComponent(tid)}/posts`,
        body,
        thread.querySelector('input[type="file"]'),
      );
      await refresh();
      toast(status, 'Reply sent', 'success');
    });
  });

  list.addEventListener('change', async (ev) => {
    const menu = ev.target.closest?.('[data-question-action="status"]');
    const tid = menu?.closest('[data-thread]')?.dataset.thread;
    if (!tid) return;
    try {
      await apiPatch(`/api/v1/admin/questions/${encodeURIComponent(tid)}`, { status: menu.value });
      await refresh();
    } catch (err) {
      toast(status, String(err), 'danger');
    }
  });

  return { refresh };
}
