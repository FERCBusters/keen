import { featureEnabled } from '/pages/feature-availability.js';
import {
  initNavbar,
  initCollapsibleFilterSections,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  esc,
  toast,
  getCurrentFramework,
  withFramework,
  loadEntityChangelog,
  openAuditSampleModal,
  canSampleIntoAudit,
} from '/app.js';
import {
  statuses,
  kinds,
  label,
  normalise,
  renderBoard,
  renderTimeline,
  renderHierarchy,
  renderGraph,
} from '/pages/planning-views.js';
import { controlBadges, clauseBadges } from '/pages/isms-detail-common.js';
const $ = (id) => document.getElementById(id),
  params = new URLSearchParams(location.search);
const me = await initNavbar(),
  framework = getCurrentFramework();
const manage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const objectiveMode = params.get('mode') === 'objectives';
const itemId = params.get('id'),
  isNew = params.has('new'),
  base = '/api/v1/isms/swot';
const fw = `framework=${encodeURIComponent(framework)}`;
let row = null,
  rows = [],
  offset = 0,
  total = 0,
  loading = false;
initCollapsibleFilterSections();
const types = ['objective', 'document', 'asset', 'risk', 'person', 'vendor'].filter((type) =>
  featureEnabled(
    me,
    {
      objective: 'objectives',
      document: 'documents',
      asset: 'assets',
      risk: 'risk_cia',
      person: 'people',
      vendor: 'vendors',
    }[type],
  ),
);
function options(id, values, first = '') {
  $(id).innerHTML =
    first + values.map((v) => `<option value="${esc(v)}">${esc(label(v))}</option>`).join('');
}
options('kind', kinds);
options('kindFilter', kinds, '<option value="">All quadrants</option>');
options('itemStatus', statuses);
options('statusFilter', statuses, '<option value="">All statuses</option>');
options('relatedType', types, '<option value="">Any relationship</option>');
options('linkType', types);
function fail(error) {
  toast($('status'), String(error), 'danger');
}
const choiceRequests = new Map();
async function choices(typeId, itemSelect, query = '') {
  const kind = $(typeId).value;
  const request = (choiceRequests.get(itemSelect) || 0) + 1;
  choiceRequests.set(itemSelect, request);
  $(itemSelect).replaceChildren(new Option('Choose an item', ''));
  $(itemSelect).disabled = true;
  if (itemSelect === 'relatedItem') $('relatedItemField').hidden = true;
  const data = kind
    ? await apiGet(
        `/api/v1/isms/swot-choices/${encodeURIComponent(kind)}?q=${encodeURIComponent(query)}`,
      )
    : { items: [] };
  if (choiceRequests.get(itemSelect) !== request) return;
  $(itemSelect).disabled = !kind || !data.items.length;
  if (itemSelect === 'relatedItem') $('relatedItemField').hidden = !kind || !data.items.length;
  $(itemSelect).innerHTML =
    '<option value="">Choose an item</option>' +
    data.items
      .map((item) => `<option value="${esc(item.target_id)}">${esc(item.title)}</option>`)
      .join('');
}
function draw() {
  const view = $('view').value,
    host = $('planningView');
  $('planningHint').hidden = view !== 'kanban' || !manage;
  if (view === 'timeline') renderTimeline(host, rows, framework);
  else if (view === 'hierarchy') renderHierarchy(host, rows, framework);
  else
    renderBoard(host, rows, {
      view,
      framework,
      onMove: manage ? move : null,
      canCreate: manage,
    });
}
async function move(id, status) {
  if (loading) {
    draw();
    return;
  }
  loading = true;
  $('planningView').setAttribute('aria-busy', 'true');
  try {
    const item = rows.find((r) => r.id === id);
    await apiPatch(`${base}/${encodeURIComponent(id)}?${fw}`, {
      status,
      expected_version: item.version,
    });
    await loadList();
    const savedCard = [...$('planningView').querySelectorAll('[data-drag-id]')].find(
      (card) => card.dataset.dragId === id,
    );
    savedCard?.classList.add('planning-saved');
    toast($('status'), `Status changed to ${label(status)}.`, 'success');
  } catch (e) {
    fail(e);
    draw();
    try {
      await loadList();
    } catch (reloadError) {
      fail(reloadError);
    }
  } finally {
    loading = false;
    $('planningView').removeAttribute('aria-busy');
  }
}
let listRequest = 0;
async function loadList() {
  const request = ++listRequest;
  const requestedOffset = offset;
  const p = new URLSearchParams({ q: $('search').value, limit: '100', offset: String(offset) });
  for (const [key, id] of [
    ['kind', 'kindFilter'],
    ['status', 'statusFilter'],
    ['significance', 'significanceFilter'],
    ['target_type', 'relatedType'],
    ['target_id', 'relatedItem'],
  ])
    if ($(id).value) p.set(key, $(id).value);
  p.set('framework', framework);
  if ($('controlFilter').value) p.set('control', $('controlFilter').value);
  if ($('clauseFilter').value) p.set('clause', $('clauseFilter').value);
  const data = await apiGet(`${base}?${p}`);
  if (request !== listRequest) return;
  total = data.total;
  rows = data.items.map((r) => normalise(r));

  $('resultCount').textContent =
    total === data.items.length && requestedOffset === 0
      ? `Showing all ${total} matching items.`
      : `Showing ${requestedOffset + (data.items.length ? 1 : 0)}–${requestedOffset + data.items.length} of ${total} matching items. This view includes these items only; use Previous/Next to see more.`;
  $('previous').disabled = offset === 0;
  $('next').disabled = offset + 100 >= total;
  draw();
}
async function parents() {
  const data = await apiGet(`${base}?kind=${encodeURIComponent($('kind').value)}&limit=200`);
  const items = data.items.filter((item) => item.id !== itemId);
  $('parent').innerHTML =
    '<option value="">Top level</option>' +
    items.map((item) => `<option value="${esc(item.id)}">${esc(item.title)}</option>`).join('');
  if (row?.parent_id && !items.some((r) => r.id === row.parent_id)) {
    const parent = await apiGet(`${base}/${encodeURIComponent(row.parent_id)}?${fw}`);
    $('parent').add(new Option(parent.title, parent.id));
  }
  $('parent').value = row?.parent_id || '';
}
function renderRelations() {
  $('relatedList').innerHTML = types
    .map((type) => {
      const links = (row.links || []).filter((l) => l.target_type === type);
      return `<section class="mb-3"><h3 class="h6">${esc(label(type))}</h3>${links.map((l) => `<div class="d-flex gap-3 mb-2"><a href="${esc(withFramework(l.url, framework))}">${esc(l.title)}</a>${manage ? `<button type="button" class="btn btn-sm btn-outline-danger" data-unlink-type="${type}" data-unlink-id="${esc(l.target_id)}">Unlink</button>` : ''}</div>`).join('') || '<span class="small-muted">No visible links.</span>'}</section>`;
    })
    .join('');
  renderGraph($('graph'), row, framework);
}
async function questions() {
  if (!featureEnabled(me, 'questions')) return;
  const data = await apiGet(`/api/v1/questions/entities/isms_swot/${encodeURIComponent(itemId)}`);
  $('questionList').innerHTML =
    data.threads
      .map(
        (t) =>
          `<article class="card card-body mb-3"><div class="small-muted">${esc(t.status)}</div>${(t.posts || []).map((p) => `<p class="mb-1"><strong>${esc(p.author_username)}</strong></p><p class="text-break">${esc(p.body)}</p>`).join('')}${me?.is_admin || String(t.created_by_user_id) === String(me?.id || me?.user?.id || '') ? `<form class="planning-question-form" data-reply-thread="${esc(t.id)}"><label class="form-label d-block">Reply<textarea class="form-control" name="body" rows="3" maxlength="5000" required></textarea></label><button class="btn btn-primary mt-2" type="submit">Send reply</button></form>` : ''}</article>`,
      )
      .join('') || '<p class="small-muted">No questions yet.</p>';
}
function mappingOptions(id, records = [], selected = [], filter = false) {
  const chosen = new Set((selected || []).map((record) => String(record.id)));
  $(id).replaceChildren();
  if (filter)
    $(id).append(new Option('All ' + (id === 'controlFilter' ? 'controls' : 'clauses'), ''));
  for (const record of records || []) {
    const option = new Option(
      `${record.ref} ${record.title || ''}`.trim(),
      filter ? record.ref : record.id,
    );
    option.selected = !filter && chosen.has(String(record.id));
    $(id).append(option);
  }
}
async function loadDetail() {
  if (!isNew) row = await apiGet(`${base}/${encodeURIComponent(itemId)}?${fw}`);
  $('pageTitle').textContent = row?.title || 'New SWOT item';
  for (const [key, id] of [
    ['title', 'title'],
    ['description', 'description'],
    ['response', 'response'],
    ['kind', 'kind'],
    ['significance', 'significance'],
    ['status', 'itemStatus'],
    ['start_date', 'startDate'],
    ['target_date', 'targetDate'],
  ])
    if (row) $(id).value = row[key] ?? '';
  const meta = await apiGet(`/api/v1/isms/meta?${fw}`);
  $('owner').innerHTML =
    '<option value="">Unassigned</option>' +
    (meta.users || [])
      .map(
        (u) =>
          `<option value="${esc(u.id)}">${esc(u.display_name || u.username || u.name)}</option>`,
      )
      .join('');
  $('owner').value = row?.owner_user_id || '';
  await parents();
  mappingOptions('controls', meta.controls, row?.controls);
  mappingOptions('clauses', meta.clauses, row?.clauses);
  $('controlLinks').innerHTML = controlBadges(row?.controls || [], framework);
  $('clauseLinks').innerHTML = clauseBadges(row?.clauses || [], framework);
  $('editor').disabled = !manage;
  $('deleteItem').hidden = !manage || isNew;
  $('sample').hidden = isNew || !canSampleIntoAudit(me, 'isms_swot');
  $('linkForm').hidden = !manage || isNew;
  $('questionForm').hidden = isNew || !(me?.is_admin || me?.can_question_events);
  if (row) {
    renderRelations();
    await questions();
    await loadEntityChangelog(
      $('changelog'),
      `/api/v1/isms/swot/${encodeURIComponent(itemId)}/changelog?limit=100`,
      { empty: 'No changes recorded.' },
    );
    await choices('linkType', 'linkItem');
  }
}
let filterTimer;
function applyFilters() {
  clearTimeout(filterTimer);
  offset = 0;
  loadList().catch(fail);
}
$('filters').addEventListener('submit', (e) => {
  e.preventDefault();
  applyFilters();
});
for (const id of [
  'kindFilter',
  'significanceFilter',
  'statusFilter',
  'controlFilter',
  'clauseFilter',
  'relatedItem',
])
  $(id).addEventListener('change', applyFilters);
$('search').addEventListener('input', () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(applyFilters, 250);
});
$('view').addEventListener('change', draw);
$('previous').addEventListener('click', () => {
  offset = Math.max(0, offset - 100);
  loadList().catch(fail);
});
$('next').addEventListener('click', () => {
  offset += 100;
  loadList().catch(fail);
});
$('relatedType').addEventListener('change', () => {
  choices('relatedType', 'relatedItem').catch(fail);
  applyFilters();
});
$('linkType').addEventListener('change', () => choices('linkType', 'linkItem').catch(fail));
$('findLinks').addEventListener('click', () =>
  choices('linkType', 'linkItem', $('linkSearch').value).catch(fail),
);
$('kind').addEventListener('change', () => parents().catch(fail));
$('itemForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (loading) return;
  loading = true;
  try {
    const payload = {
      title: $('title').value,
      description: $('description').value,
      response: $('response').value,
      kind: $('kind').value,
      significance: Number($('significance').value),
      status: $('itemStatus').value,
      start_date: $('startDate').value || null,
      target_date: $('targetDate').value || null,
      parent_id: $('parent').value || null,
      owner_user_id: $('owner').value || null,
      controls: Array.from($('controls').selectedOptions, (option) => option.value),
      clauses: Array.from($('clauses').selectedOptions, (option) => option.value),
      expected_version: row?.version,
    };
    const result = isNew
      ? await apiPost(`${base}?${fw}`, payload)
      : await apiPatch(`${base}/${encodeURIComponent(itemId)}?${fw}`, payload);
    if (isNew)
      location.assign(withFramework(`/swot.html?id=${encodeURIComponent(result.id)}`, framework));
    else {
      await loadDetail();
      toast($('status'), 'SWOT item saved.', 'success');
    }
  } catch (e) {
    fail(e);
  } finally {
    loading = false;
  }
});
$('linkForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await apiPost(`${base}/${encodeURIComponent(itemId)}/links`, {
      target_type: $('linkType').value,
      target_id: $('linkItem').value,
      expected_version: row.version,
    });
    await loadDetail();
  } catch (e) {
    fail(e);
  }
});
$('relatedList').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-unlink-id]');
  if (!b) return;
  try {
    await apiDelete(
      `${base}/${encodeURIComponent(itemId)}/links/${b.dataset.unlinkType}/${encodeURIComponent(b.dataset.unlinkId)}?expected_version=${row.version}`,
    );
    await loadDetail();
  } catch (e) {
    fail(e);
  }
});
$('questionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await apiPost(`/api/v1/questions/entities/isms_swot/${encodeURIComponent(itemId)}`, {
      body: $('questionBody').value,
    });
    $('questionBody').value = '';
    await questions();
  } catch (e) {
    fail(e);
  }
});
$('deleteItem').addEventListener('click', async () => {
  if (!confirm('Delete this SWOT item? Its changelog remains.')) return;
  try {
    await apiDelete(`${base}/${encodeURIComponent(itemId)}?expected_version=${row.version}`);
    location.assign(withFramework('/swot.html', framework));
  } catch (e) {
    fail(e);
  }
});
$('sample').addEventListener('click', () =>
  openAuditSampleModal({
    me,
    entityType: 'isms_swot',
    entityId: row.id,
    title: row.title,
    evidenceUrl: withFramework(`/swot.html?id=${row.id}`, framework),
    framework,
    statusEl: $('status'),
  }),
);
try {
  $('workspace').hidden = !!itemId || isNew;
  $('detail').hidden = !itemId && !isNew;
  $('newItem').hidden = !manage;
  if (objectiveMode) {
    const view = params.get('view') === 'timeline' ? 'timeline' : 'kanban';
    location.replace(withFramework(`/isms.html?tab=objectives&view=${view}`, framework));
  } else if (itemId || isNew) {
    if (isNew && kinds.includes(params.get('kind'))) $('kind').value = params.get('kind');
    await loadDetail();
    const tab = ['details', 'related', 'visualisation', 'questions', 'changelog'].includes(
      params.get('tab'),
    )
      ? params.get('tab')
      : 'details';
    if (window.bootstrap) window.bootstrap.Tab.getOrCreateInstance($(tab + 'Tab')).show();
  } else {
    const meta = await apiGet(`/api/v1/isms/meta?${fw}`);
    mappingOptions('controlFilter', meta.controls, [], true);
    mappingOptions('clauseFilter', meta.clauses, [], true);
    const type = params.get('target_type'),
      target = params.get('target_id');
    if (types.includes(type) && target) {
      $('relatedType').value = type;
      await choices('relatedType', 'relatedItem');
      if (![...$('relatedItem').options].some((option) => option.value === target))
        $('relatedItem').add(new Option('Selected record', target));
      $('relatedItem').value = target;
      $('relatedItem').disabled = false;
      $('relatedItemField').hidden = false;
    }
    await loadList();
  }
} catch (e) {
  fail(e);
}

$('questionList').addEventListener('submit', async (event) => {
  const form = event.target.closest('[data-reply-thread]');
  if (!form) return;
  event.preventDefault();
  try {
    await apiPost(`/api/v1/questions/${encodeURIComponent(form.dataset.replyThread)}/posts`, {
      body: form.elements.body.value,
    });
    await questions();
  } catch (error) {
    fail(error);
  }
});
