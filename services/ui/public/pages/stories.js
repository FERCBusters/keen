// The story register: search, open and start stories.
import {
  initNavbar,
  apiGet,
  apiPost,
  esc,
  fmtTs,
  toast,
  getCurrentFramework,
  withFramework,
  offsetPagerState,
} from '/app.js';

const $ = (id) => document.getElementById(id);
const me = await initNavbar();
const framework = getCurrentFramework();
const canManage = Boolean(me?.is_admin || me?.can_manage_stories);
const state = { q: '', status: '', limit: 50, offset: 0, total: 0 };

function readUrl() {
  const params = new URLSearchParams(location.search);
  state.q = params.get('q') || '';
  state.status = ['open', 'closed'].includes(params.get('status')) ? params.get('status') : '';
  state.offset = Math.max(0, Number(params.get('offset')) || 0);
  $('q').value = state.q;
  $('statusFilter').value = state.status;
}

function syncUrl() {
  const url = new URL(String(location.href), document.baseURI);
  for (const [key, value] of [
    ['q', state.q],
    ['status', state.status],
    ['offset', state.offset ? String(state.offset) : ''],
  ]) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  history.replaceState(history.state, '', url.pathname + url.search + url.hash);
}

function statusBadge(status) {
  return `<span class="badge ${status === 'closed' ? 'text-bg-secondary' : 'text-bg-success'}">${esc(status)}</span>`;
}

function renderRows(items) {
  const tbody = $('rows');
  tbody.innerHTML = items.length
    ? items
        .map(
          (story) => `<tr data-story-id="${esc(story.id)}">
        <td class="wrap"><a class="fw-semibold" href="${withFramework(`/story.html?id=${encodeURIComponent(story.id)}`, framework)}">${esc(story.title)}</a>${story.summary ? `<div class="small-muted text-truncate" style="max-width:48rem;">${esc(story.summary)}</div>` : ''}</td>
        <td>${statusBadge(story.status)}</td>
        <td>${esc(story.item_count ?? 0)}</td>
        <td class="small-muted">${esc(fmtTs(story.updated_at))}</td>
        <td class="small-muted">${esc(story.created_by || '')}</td>
      </tr>`,
        )
        .join('')
    : '<tr><td colspan="5" class="small-muted p-4">No stories match the current filters.</td></tr>';
}

async function load() {
  const params = new URLSearchParams();
  if (state.q) params.set('q', state.q);
  if (state.status) params.set('status', state.status);
  params.set('limit', String(state.limit));
  params.set('offset', String(state.offset));
  $('meta').textContent = 'Loading…';
  try {
    const data = await apiGet(`/api/v1/stories?${params}`);
    state.total = data.total || 0;
    const page = offsetPagerState(state);
    $('meta').textContent = `${page.start}–${page.end} of ${page.total}`;
    $('prev').disabled = state.offset <= 0;
    $('next').disabled = state.offset + state.limit >= state.total;
    renderRows(data.items || []);
  } catch (error) {
    toast($('status'), `Failed to load stories: ${String(error)}`, 'danger');
    $('meta').textContent = '—';
  }
}

$('filters').addEventListener('submit', (ev) => {
  ev.preventDefault();
  state.q = $('q').value.trim();
  state.status = $('statusFilter').value;
  state.offset = 0;
  syncUrl();
  load();
});
$('reset').addEventListener('click', () => {
  $('q').value = '';
  $('statusFilter').value = '';
  state.q = '';
  state.status = '';
  state.offset = 0;
  syncUrl();
  load();
});
$('prev').addEventListener('click', () => {
  state.offset = Math.max(0, state.offset - state.limit);
  syncUrl();
  load();
});
$('next').addEventListener('click', () => {
  state.offset += state.limit;
  syncUrl();
  load();
});
$('showCreate').hidden = !canManage;
$('showCreate').addEventListener('click', () => {
  $('createCard').hidden = false;
  $('createTitle').focus();
});
$('cancelCreate').addEventListener('click', () => {
  $('createCard').hidden = true;
});
$('createForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    const story = await apiPost(`/api/v1/stories?framework=${encodeURIComponent(framework)}`, {
      title: $('createTitle').value.trim(),
      summary: $('createSummary').value,
    });
    location.assign(withFramework(`/story.html?id=${encodeURIComponent(story.id)}`, framework));
  } catch (error) {
    toast($('status'), `Failed to create the story: ${String(error)}`, 'danger');
  }
});

readUrl();
load();
