// One story: its timeline, flow chart, comments, questions and changelog.
import { featureEnabled } from '/pages/feature-availability.js';
import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiPut,
  apiDelete,
  esc,
  fmtTs,
  toast,
  getCurrentFramework,
  withFramework,
  loadEntityChangelog,
  openAuditSampleModal,
  canSampleIntoAudit,
} from '/app.js';
import { createFlowCanvas } from '/pages/story-flow.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const me = await initNavbar();
const framework = getCurrentFramework();
const storyId = params.get('id') || '';
const base = `/api/v1/stories/${encodeURIComponent(storyId)}`;
const fw = `framework=${encodeURIComponent(framework)}`;
let story = null;
let layoutDirty = false;
const flow = createFlowCanvas({
  host: $('flow'),
  editable: false,
  onChange: (change) => {
    if (change.type === 'select') {
      $('linkEditor').hidden = !change.link;
      $('linkLabel').value = change.link?.label || '';
      return;
    }
    layoutDirty = true;
    $('saveLayout').disabled = false;
  },
});

function fail(error) {
  toast($('status'), String(error?.message || error), 'danger');
}

// Items in time order; items without a time keep their story order at the end.
export function timelineOrder(items) {
  return [...items].sort((a, b) => {
    if (a.occurred_at && b.occurred_at)
      return a.occurred_at.localeCompare(b.occurred_at) || a.position - b.position;
    if (a.occurred_at) return -1;
    if (b.occurred_at) return 1;
    return a.position - b.position;
  });
}

function itemTitleHtml(item) {
  if (item.withheld) return `<span class="small-muted fst-italic">${esc(item.title)}</span>`;
  if (!item.url) return `<span class="fw-semibold">${esc(item.title)}</span>`;
  return `<a class="fw-semibold" href="${esc(withFramework(item.url, framework))}">${esc(item.title)}</a>`;
}

function itemHtml(item) {
  const controls = (item.controls || [])
    .map(
      (c) =>
        `<a class="badge badge-soft me-1" href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework)}" title="${esc(c.title || '')}">${esc(c.ref)}</a>`,
    )
    .join('');
  const editor = story?.can_edit
    ? `<div class="d-flex gap-2 mt-2">
        <button class="btn btn-sm btn-outline-secondary" type="button" data-edit-item="${esc(item.id)}">Edit</button>
        <button class="btn btn-sm btn-outline-danger" type="button" data-remove-item="${esc(item.id)}">Remove</button>
      </div>
      <form class="story-item-editor mt-2" data-item-form="${esc(item.id)}" hidden>
        <div class="row g-2">
          <div class="col-md-4"><label class="form-label small-muted">Label<input class="form-control form-control-sm" name="label" maxlength="256" value="${esc(item.label)}"></label></div>
          <div class="col-md-4"><label class="form-label small-muted">When<input class="form-control form-control-sm" name="occurred_at" type="datetime-local" value="${esc((item.occurred_at || '').slice(0, 16))}"></label></div>
          <div class="col-md-4"><div class="form-check mt-4"><input class="form-check-input" type="checkbox" name="clear_occurred_at" id="clear-${esc(item.id)}"><label class="form-check-label small-muted" for="clear-${esc(item.id)}">Use the record's own time</label></div></div>
          <div class="col-12"><label class="form-label small-muted w-100">Note<textarea class="form-control form-control-sm" name="note" rows="2" maxlength="4000">${esc(item.note)}</textarea></label></div>
          <div class="col-12 d-flex gap-2"><button class="btn btn-sm btn-primary" type="submit">Save item</button><button class="btn btn-sm btn-outline-secondary" type="button" data-cancel-item="${esc(item.id)}">Cancel</button></div>
        </div>
      </form>`
    : '';
  return `<li class="story-timeline-item${item.withheld ? ' story-timeline-withheld' : ''}" data-item-id="${esc(item.id)}" data-kind="${esc(item.kind)}">
    <div class="story-timeline-time small-muted">${item.occurred_at ? esc(fmtTs(item.occurred_at)) : '<span class="fst-italic">no time</span>'}</div>
    <div class="story-timeline-body">
      <span class="badge badge-soft me-1">${esc(item.kind_label)}</span>${item.label ? `<span class="fw-semibold">${esc(item.label)}</span> · ` : ''}${itemTitleHtml(item)}
      ${item.source ? `<span class="small-muted ms-1">${esc(item.source)}</span>` : ''}
      ${item.note ? `<p class="mb-0 story-note">${esc(item.note)}</p>` : ''}
      ${controls ? `<div class="mt-1">${controls}</div>` : ''}
      ${editor}
    </div>
  </li>`;
}

function render() {
  document.title = `Keen – ${story.title}`;
  $('storyTitle').textContent = story.title;
  $('storyStatus').textContent = story.status;
  $('storyStatus').className =
    `badge ${story.status === 'closed' ? 'text-bg-secondary' : 'text-bg-success'}`;
  $('storyMeta').textContent =
    `${story.item_count} item${story.item_count === 1 ? '' : 's'} · created by ${story.created_by || 'unknown'} · updated ${fmtTs(story.updated_at)}`;
  $('storySummary').textContent = story.summary || '';
  $('storySummary').hidden = !story.summary;
  $('edit').hidden = !story.can_edit;
  $('deleteStory').hidden = !story.can_edit;
  $('sample').hidden = !canSampleIntoAudit(me, 'story');
  $('addItems').hidden = !story.can_edit;
  $('flowToolbar').hidden = !story.can_edit;
  $('flowHint').hidden = !story.can_edit;
  $('commentForm').hidden = false;
  $('questionForm').hidden = !(me?.is_admin || me?.can_create_questions);
  $('commentCount').textContent = story.comment_count ? String(story.comment_count) : '';
  const controls = story.controls || [];
  $('controlsLine').hidden = !controls.length;
  $('controlsList').innerHTML = controls
    .map(
      (c) =>
        `<a class="badge badge-soft me-1" href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework)}" title="${esc(c.title || '')}">${esc(c.ref)}</a>`,
    )
    .join('');
  const ordered = timelineOrder(story.items || []);
  $('timeline').innerHTML = ordered.length
    ? ordered.map(itemHtml).join('')
    : '<li class="small-muted">No items yet. Add events from the Events screen or records below.</li>';
  flow.setEditable(story.can_edit);
  flow.load(story.items || [], story.links || []);
  layoutDirty = false;
  $('saveLayout').disabled = true;
  $('linkEditor').hidden = true;
}

async function load() {
  story = await apiGet(`${base}?${fw}`);
  render();
}

function applyStory(next) {
  story = next;
  render();
}

// --- Story details -----------------------------------------------------------

$('edit').addEventListener('click', () => {
  $('title').value = story.title;
  $('summary').value = story.summary || '';
  $('statusSelect').value = story.status;
  $('storyForm').hidden = false;
  $('title').focus();
});
$('cancelEdit').addEventListener('click', () => {
  $('storyForm').hidden = true;
});
$('storyForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    applyStory(
      await apiPatch(`${base}?${fw}`, {
        title: $('title').value.trim(),
        summary: $('summary').value,
        status: $('statusSelect').value,
        expected_version: story.version,
      }),
    );
    $('storyForm').hidden = true;
    toast($('status'), 'Story saved.', 'success');
  } catch (error) {
    fail(error);
  }
});
$('deleteStory').addEventListener('click', async () => {
  if (
    !confirm(
      'Delete this story? Its items stay where they came from; the story and its comments are removed.',
    )
  )
    return;
  try {
    await apiDelete(`${base}?expected_version=${story.version}`);
    location.assign(withFramework('/stories.html', framework));
  } catch (error) {
    fail(error);
  }
});
$('sample').addEventListener('click', () =>
  openAuditSampleModal({
    me,
    entityType: 'story',
    entityId: story.id,
    title: story.title,
    evidenceUrl: withFramework(`/story.html?id=${story.id}`, framework),
    framework,
    statusEl: $('status'),
  }),
);

// --- Items -------------------------------------------------------------------

function kindFieldsVisible() {
  const note = $('addKind').value === 'note';
  $('searchField').hidden = note;
  $('searchAction').hidden = note;
  $('noteLabelField').hidden = !note;
  $('noteTextField').hidden = !note;
  $('noteAction').hidden = !note;
  $('candidates').hidden = true;
}
$('addKind').addEventListener('change', kindFieldsVisible);

async function addItems(items) {
  applyStory(await apiPost(`${base}/items?${fw}`, { items, expected_version: story.version }));
}

function candidateHtml(candidate) {
  return `<li class="list-group-item d-flex justify-content-between align-items-center gap-2">
    <span class="text-break"><span class="badge badge-soft me-1">${esc(candidate.kind_label)}</span>${esc(candidate.title)}${candidate.when ? `<span class="small-muted ms-2">${esc(fmtTs(candidate.when))}</span>` : ''}</span>
    <button class="btn btn-sm btn-outline-primary" type="button" data-add-kind="${esc(candidate.kind)}" data-add-id="${esc(candidate.id)}">Add</button>
  </li>`;
}

async function findCandidates() {
  const kind = $('addKind').value;
  const q = $('addSearch').value.trim();
  const list = $('candidates');
  list.hidden = false;
  list.innerHTML = '<li class="list-group-item small-muted">Searching…</li>';
  try {
    let candidates;
    if (kind === 'event') {
      const params = new URLSearchParams({ q, limit: '20', framework });
      const data = await apiGet(`/api/v1/events?${params}`);
      candidates = (data.items || []).map((event) => ({
        kind,
        kind_label: 'Event',
        id: event.id,
        title: event.summary || '(no summary)',
        when: event.timestamp,
      }));
    } else {
      const data = await apiGet(
        `/api/v1/stories/choices/${encodeURIComponent(kind)}?q=${encodeURIComponent(q)}`,
      );
      candidates = (data.items || []).map((record) => ({
        kind,
        kind_label: $('addKind').selectedOptions[0].textContent,
        id: record.target_id,
        title: record.title,
      }));
    }
    const present = new Set((story.items || []).map((item) => `${item.kind}:${item.entity_id}`));
    candidates = candidates.filter((c) => !present.has(`${c.kind}:${c.id}`));
    list.innerHTML = candidates.length
      ? candidates.map(candidateHtml).join('')
      : '<li class="list-group-item small-muted">Nothing new matches.</li>';
  } catch (error) {
    list.innerHTML = `<li class="list-group-item text-danger">Search failed: ${esc(String(error))}</li>`;
  }
}
$('findItems').addEventListener('click', findCandidates);
$('addSearch').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') {
    ev.preventDefault();
    findCandidates();
  }
});
$('candidates').addEventListener('click', async (ev) => {
  const button = ev.target.closest('[data-add-id]');
  if (!button) return;
  try {
    await addItems([{ kind: button.dataset.addKind, entity_id: button.dataset.addId }]);
    button.closest('li').remove();
    toast($('status'), 'Added to the story.', 'success');
  } catch (error) {
    fail(error);
  }
});
$('addNote').addEventListener('click', async () => {
  try {
    await addItems([
      { kind: 'note', label: $('noteLabel').value.trim(), note: $('noteText').value },
    ]);
    $('noteLabel').value = '';
    $('noteText').value = '';
  } catch (error) {
    fail(error);
  }
});
$('timeline').addEventListener('click', async (ev) => {
  const edit = ev.target.closest('[data-edit-item]');
  if (edit) {
    const form = $('timeline').querySelector(
      `[data-item-form="${CSS.escape(edit.dataset.editItem)}"]`,
    );
    form.hidden = !form.hidden;
    return;
  }
  const cancel = ev.target.closest('[data-cancel-item]');
  if (cancel) {
    $('timeline').querySelector(
      `[data-item-form="${CSS.escape(cancel.dataset.cancelItem)}"]`,
    ).hidden = true;
    return;
  }
  const remove = ev.target.closest('[data-remove-item]');
  if (!remove) return;
  if (!confirm('Remove this item from the story?')) return;
  try {
    applyStory(
      await apiDelete(
        `${base}/items/${encodeURIComponent(remove.dataset.removeItem)}?expected_version=${story.version}&${fw}`,
      ),
    );
  } catch (error) {
    fail(error);
  }
});
$('timeline').addEventListener('submit', async (ev) => {
  const form = ev.target.closest('[data-item-form]');
  if (!form) return;
  ev.preventDefault();
  const when = form.elements.occurred_at.value;
  const payload = {
    label: form.elements.label.value,
    note: form.elements.note.value,
    expected_version: story.version,
  };
  if (form.elements.clear_occurred_at.checked) payload.clear_occurred_at = true;
  else if (when) payload.occurred_at = new Date(when).toISOString();
  try {
    applyStory(
      await apiPatch(`${base}/items/${encodeURIComponent(form.dataset.itemForm)}?${fw}`, payload),
    );
  } catch (error) {
    fail(error);
  }
});

// --- Flow chart --------------------------------------------------------------

$('linkMode').addEventListener('click', () => {
  flow.setLinkMode(!flow.linkMode);
  $('linkMode').setAttribute('aria-pressed', String(flow.linkMode));
  $('linkMode').classList.toggle('active', flow.linkMode);
});
$('arrange').addEventListener('click', () => flow.arrange());
$('linkLabel').addEventListener('input', () => flow.labelSelectedLink($('linkLabel').value));
$('removeLink').addEventListener('click', () => {
  flow.removeSelectedLink();
  $('linkEditor').hidden = true;
});
$('saveLayout').addEventListener('click', async () => {
  try {
    applyStory(
      await apiPut(`${base}/layout?${fw}`, { ...flow.layout(), expected_version: story.version }),
    );
    toast($('status'), 'Layout saved.', 'success');
  } catch (error) {
    fail(error);
  }
});
window.addEventListener('beforeunload', (ev) => {
  if (layoutDirty) ev.preventDefault();
});

// --- Comments ----------------------------------------------------------------

async function loadComments() {
  const data = await apiGet(`${base}/comments`);
  const items = data.items || [];
  $('comments').innerHTML = items.length
    ? items
        .map(
          (c) => `<article class="card card-body mb-2" data-comment-id="${esc(c.id)}">
        <div class="small-muted">${esc(c.created_by || 'unknown')} · ${esc(fmtTs(c.created_at))}${c.can_delete ? ` <button class="btn btn-link btn-sm p-0 ms-2" type="button" data-delete-comment="${esc(c.id)}">Delete</button>` : ''}</div>
        <p class="mb-0 story-note">${esc(c.body)}</p>
      </article>`,
        )
        .join('')
    : '<p class="small-muted">No comments yet.</p>';
  $('commentCount').textContent = items.length ? String(items.length) : '';
}
$('commentForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    await apiPost(`${base}/comments`, { body: $('commentBody').value });
    $('commentBody').value = '';
    await loadComments();
  } catch (error) {
    fail(error);
  }
});
$('comments').addEventListener('click', async (ev) => {
  const button = ev.target.closest('[data-delete-comment]');
  if (!button || !confirm('Delete this comment?')) return;
  try {
    await apiDelete(`${base}/comments/${encodeURIComponent(button.dataset.deleteComment)}`);
    await loadComments();
  } catch (error) {
    fail(error);
  }
});

// --- Questions ---------------------------------------------------------------

async function loadQuestions() {
  if (!featureEnabled(me, 'questions')) return;
  const data = await apiGet(`/api/v1/questions/entities/story/${encodeURIComponent(storyId)}`);
  const threads = data.threads || [];
  $('questionList').innerHTML = threads.length
    ? threads
        .map(
          (t) =>
            `<article class="card card-body mb-3"><div class="small-muted">${esc(t.status)}</div>${(t.posts || []).map((p) => `<p class="mb-1"><strong>${esc(p.author_username)}</strong></p><p class="text-break">${esc(p.body)}</p>`).join('')}${me?.is_admin || (t.created_by_user_id && String(t.created_by_user_id) === String(me?.user_id || '')) ? `<form class="planning-question-form" data-reply-thread="${esc(t.id)}"><label class="form-label d-block">Reply<textarea class="form-control" name="body" rows="3" maxlength="5000" required></textarea></label><button class="btn btn-primary mt-2" type="submit">Send reply</button></form>` : ''}</article>`,
        )
        .join('')
    : '<p class="small-muted">No questions yet.</p>';
}
$('questionForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    await apiPost(`/api/v1/questions/entities/story/${encodeURIComponent(storyId)}`, {
      body: $('questionBody').value,
    });
    $('questionBody').value = '';
    await loadQuestions();
  } catch (error) {
    fail(error);
  }
});
$('questionList').addEventListener('submit', async (ev) => {
  const form = ev.target.closest('[data-reply-thread]');
  if (!form) return;
  ev.preventDefault();
  try {
    await apiPost(`/api/v1/questions/${encodeURIComponent(form.dataset.replyThread)}/posts`, {
      body: form.elements.body.value,
    });
    await loadQuestions();
  } catch (error) {
    fail(error);
  }
});

// --- Start -------------------------------------------------------------------

try {
  if (!storyId) throw new Error('No story was named.');
  await load();
  kindFieldsVisible();
  const tab = params.get('tab');
  if (['flow', 'comments', 'questions', 'changelog'].includes(tab)) {
    window.bootstrap?.Tab.getOrCreateInstance($(`${tab}Tab`))?.show();
  }
  await Promise.all([
    loadComments(),
    loadQuestions(),
    loadEntityChangelog($('changelog'), `${base}/changelog?limit=100`, {
      empty: 'No changes recorded.',
    }),
  ]);
} catch (error) {
  fail(error);
  $('storyTitle').textContent = 'Story unavailable';
}
