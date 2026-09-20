import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  renderRichTextContent,
  loadEntityChangelog,
  userPillsHtml,
} from '/app.js';

const me = await initNavbar();
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);

const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const meetingId = params.get('id') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);

function personBadges(list) {
  return userPillsHtml(list);
}

function badgeList(items, labelKey = 'ref', urlFn = null) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.map((it) => {
    const label = String(it?.[labelKey] || it?.name || it?.title || 'Item');
    const title = String(it?.title || it?.name || label);
    const href = urlFn ? urlFn(it) : '';
    if (href) return `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(href)}" title="${esc(title)}">${esc(label)}</a>`;
    return `<span class="badge badge-soft me-1 mb-1" title="${esc(title)}">${esc(label)}</span>`;
  }).join('');
}

function controlBadges(items) {
  return badgeList(items, 'ref', (c) => withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework));
}

function clauseBadges(items) {
  return badgeList(items, 'ref', (c) => withFramework(`/clause.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework));
}

function linkHtml(link) {
  if (link?.link_type === 'isms_document' && link.document) {
    const doc = link.document;
    const href = withFramework(`/isms-document.html?id=${encodeURIComponent(doc.id || link.document_id || '')}`, framework);
    return `<li><a href="${esc(href)}">${esc(doc.title || link.title || 'ISMS document')}</a><span class="small-muted ms-2">ISMS document</span></li>`;
  }
  if (link?.url) {
    return `<li><a href="${esc(link.url)}" target="_blank" rel="noopener noreferrer">${esc(link.title || link.url)}</a></li>`;
  }
  return '';
}


function renderMeetingNotes(text) {
  return renderRichTextContent(text, '<p>No agenda/minutes/notes recorded.</p>');
}


function renderMeeting(meeting) {
  const title = meeting.title || 'Meeting minutes';
  document.title = `Keen – ${title}`;
  $('meetingTitle').textContent = title;
  $('meetingMeta').textContent = `${meeting.date || 'No date'} • ${[meeting.start_time, meeting.end_time].filter(Boolean).join('–') || 'No time recorded'}`;
  $('meetingDate').textContent = meeting.date || '—';
  $('meetingTime').textContent = [meeting.start_time, meeting.end_time].filter(Boolean).join('–') || '—';
  $('meetingAttendees').innerHTML = personBadges(meeting.attendees);
  $('meetingApologies').innerHTML = personBadges(meeting.apologies);
  $('meetingUpdated').textContent = fmtTs(meeting.updated_at) || '—';
  $('meetingNotes').innerHTML = renderMeetingNotes(meeting.agenda_minutes_notes);
  $('meetingLinks').innerHTML = (meeting.links || []).length
    ? `<ul class="mb-0">${(meeting.links || []).map(linkHtml).filter(Boolean).join('')}</ul>`
    : '<span class="small-muted">No supporting links recorded.</span>';
  $('meetingControls').innerHTML = controlBadges(meeting.controls);
  $('meetingClauses').innerHTML = clauseBadges(meeting.clauses);
  const edit = $('editMeetingLink');
  if (edit) {
    edit.href = withFramework(`/isms.html?tab=meetings&edit=meeting:${encodeURIComponent(meeting.id)}`, framework);
    edit.style.display = canManageIsms ? '' : 'none';
  }
}

async function loadChangelog() {
  await loadEntityChangelog(
    $('meetingChangelog'),
    `/api/v1/isms/meeting/${encodeURIComponent(meetingId)}/changelog?limit=100`,
    {empty: 'No meeting changes have been recorded yet.'}
  );
}

function activateTabFromHashOrQuery() {
  const tab = String(params.get('tab') || location.hash.replace(/^#/, '') || '').toLowerCase();
  const target = tab === 'changelog' ? $('meetingChangelogTab') : null;
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}

async function load() {
  if (!meetingId) {
    toast(status, 'Missing meeting id.', 'danger');
    return;
  }
  try {
    const meeting = await apiGet(`/api/v1/isms/meetings/${encodeURIComponent(meetingId)}?framework=${encodeURIComponent(framework)}`);
    renderMeeting(meeting);
    await loadChangelog();
    activateTabFromHashOrQuery();
  } catch (e) {
    toast(status, `Failed to load meeting minutes: ${String(e)}`, 'danger');
  }
}

window.addEventListener('hashchange', activateTabFromHashOrQuery);
load();
