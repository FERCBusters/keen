import { featureEnabled } from '/pages/feature-availability.js';
import { mountQuestionThreads } from '/pages/question-threads.js';
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
import { mountStoryRelated } from '/pages/story-related.js';
const $ = (id) => document.getElementById(id),
  params = new URLSearchParams(location.search);
const me = await initNavbar(),
  framework = getCurrentFramework();
const manage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const itemId = params.get('id'),
  isNew = params.has('new'),
  base = '/api/v1/isms/swot';
const fw = `framework=${encodeURIComponent(framework)}`;
const swotQuestions = mountQuestionThreads({
  panel: $('questionPanel'),
  me,
  status: $('status'),
  placeholder: 'Write a question about this SWOT item',
  base: () =>
    itemId ? `/api/v1/questions/entities/isms_swot/${encodeURIComponent(itemId)}` : null,
});
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
const draftLinks = [];
let draftDirty = false;
let mappingCatalog = { controls: [], clauses: [] };
const choiceRecords = new Map();
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
  choiceRecords.set(itemSelect, data.items);
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
  const p = filterQuery();
  p.set('limit', '100');
  p.set('offset', String(offset));
  const data = await apiGet(`${base}?${p}`);
  if (request !== listRequest) return;
  showList(data, requestedOffset);
}
function filterQuery() {
  const p = new URLSearchParams({ q: $('search').value });
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
  return p;
}
function showList(data, requestedOffset) {
  total = data.total;
  rows = data.items.map((r) => normalise(r));

  $('resultCount').textContent =
    total === data.items.length && requestedOffset === 0
      ? `Showing all ${total} matching items.`
      : `Showing ${requestedOffset + (data.items.length ? 1 : 0)}-${requestedOffset + data.items.length} of ${total} matching items. This view includes these items only; use Previous/Next to see more.`;
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
  const current = isNew
    ? {
        title: $('title').value || 'New SWOT item',
        links: draftLinks,
        ...Object.fromEntries(
          ['controls', 'clauses'].map((key) => {
            const selected = new Set(Array.from($(key).selectedOptions, (option) => option.value));
            return [
              key,
              (mappingCatalog[key] || []).filter((item) => selected.has(String(item.id))),
            ];
          }),
        ),
      }
    : row;
  $('relatedList').innerHTML = types
    .map((type) => {
      const links = (current.links || []).filter((link) => link.target_type === type);
      return `<section class="col-12 col-md-6 col-xl-4"><div class="card h-100"><div class="card-body p-3"><h3 class="h6 d-flex justify-content-between">${esc(label(type))}<span class="badge text-bg-secondary">${links.length}</span></h3><div class="d-grid gap-2">${links.map((link) => `<div class="border rounded p-2 d-flex align-items-center gap-2"><a class="text-break flex-grow-1" href="${esc(withFramework(link.url, framework))}"${isNew ? ' target="_blank" rel="noopener noreferrer"' : ''}>${esc(link.title)}</a>${manage ? `<button type="button" class="btn btn-sm btn-outline-danger flex-shrink-0" data-unlink-type="${type}" data-unlink-id="${esc(link.target_id)}" aria-label="Remove ${esc(link.title)}">Unlink</button>` : ''}</div>`).join('') || '<span class="small-muted">No linked items.</span>'}</div></div></div></section>`;
    })
    .join('');
  renderGraph($('graph'), current, framework);
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
  mappingCatalog = meta;
  mappingOptions('controls', meta.controls, row?.controls);
  mappingOptions('clauses', meta.clauses, row?.clauses);
  $('controlLinks').innerHTML = controlBadges(row?.controls || [], framework);
  $('clauseLinks').innerHTML = clauseBadges(row?.clauses || [], framework);
  $('editor').disabled = !manage;
  $('deleteItem').hidden = !manage || isNew;
  $('sample').hidden = isNew || !canSampleIntoAudit(me, 'isms_swot');
  $('linkForm').hidden = !manage;
  $('draftNotice').hidden = !isNew;
  $('saveDraft').hidden = !isNew || !manage;
  $('questionsTab').disabled = isNew;
  $('changelogTab').disabled = isNew;
  renderRelations();
  await choices('linkType', 'linkItem');
  if (row) {
    renderRelations();
    await swotQuestions.refresh();
    await loadEntityChangelog(
      $('changelog'),
      `/api/v1/isms/swot/${encodeURIComponent(itemId)}/changelog?limit=100`,
      { empty: 'No changes recorded.' },
    );
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
      ...(isNew
        ? { links: draftLinks.map(({ target_type, target_id }) => ({ target_type, target_id })) }
        : {}),
    };
    const result = isNew
      ? await apiPost(`${base}?${fw}`, payload)
      : await apiPatch(`${base}/${encodeURIComponent(itemId)}?${fw}`, payload);
    if (isNew) {
      draftDirty = false;
      location.assign(withFramework(`/swot.html?id=${encodeURIComponent(result.id)}`, framework));
    } else {
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
  if (loading) return;
  if (isNew) {
    const type = $('linkType').value;
    const item = (choiceRecords.get('linkItem') || []).find(
      (item) => item.target_id === $('linkItem').value,
    );
    if (!item) return;
    if (
      !draftLinks.some((link) => link.target_type === type && link.target_id === item.target_id)
    ) {
      if (draftLinks.length >= 200) return fail('A SWOT item supports up to 200 relationships.');
      draftLinks.push({ ...item, target_type: type });
      draftDirty = true;
    }
    renderRelations();
    toast($('status'), 'Added to draft. Save the SWOT item to keep this relationship.', 'info');
    return;
  }
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
  if (!b || loading) return;
  if (isNew) {
    const index = draftLinks.findIndex(
      (link) => link.target_type === b.dataset.unlinkType && link.target_id === b.dataset.unlinkId,
    );
    if (index >= 0) draftLinks.splice(index, 1);
    renderRelations();
    return;
  }
  try {
    await apiDelete(
      `${base}/${encodeURIComponent(itemId)}/links/${b.dataset.unlinkType}/${encodeURIComponent(b.dataset.unlinkId)}?expected_version=${row.version}`,
    );
    await loadDetail();
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
  if (itemId || isNew) {
    if (isNew && kinds.includes(params.get('kind'))) $('kind').value = params.get('kind');
    await loadDetail();
    const tab = ['details', 'related', 'visualisation', 'questions', 'changelog'].includes(
      params.get('tab'),
    )
      ? params.get('tab')
      : 'details';
    if (window.bootstrap) window.bootstrap.Tab.getOrCreateInstance($(tab + 'Tab')).show();
    if (itemId) mountStoryRelated({ me, kind: 'swot', id: itemId, framework });
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

$('itemForm').addEventListener(
  'invalid',
  () => {
    window.bootstrap?.Tab.getOrCreateInstance($('detailsTab')).show();
  },
  true,
);

let exportCancelled = false;
let exporting = false;
$('cancelExport').addEventListener('click', () => {
  exportCancelled = true;
});
window.addEventListener('pagehide', () => {
  exportCancelled = true;
});
async function exportSwot(format) {
  if (exporting) return;
  exporting = true;
  exportCancelled = false;
  for (const id of ['exportExcel', 'exportPdf']) $(id).disabled = true;
  $('cancelExport').hidden = false;
  try {
    const { collectExport, buildWorkbook, buildSwotPdf } = await import('/pages/swot-export.js');
    const filters = filterQuery();
    toast($('status'), 'Preparing all matching SWOT items', 'info');
    const items = await collectExport(
      apiGet,
      base,
      filters,
      () => exportCancelled,
      (done, count) => toast($('status'), `Preparing export: ${done} of ${count} items`, 'info'),
    );
    const meta = await apiGet(`/api/v1/isms/meta?${fw}`);
    const owners = new Map(
      (meta.users || []).map((user) => [user.id, user.display_name || user.username || user.name]),
    );
    if (exportCancelled) throw new Error('Export cancelled.');
    const blob =
      format === 'xlsx'
        ? buildWorkbook(items, framework, owners)
        : await buildSwotPdf(items, framework, owners, () => exportCancelled);
    if (exportCancelled) throw new Error('Export cancelled.');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `swot-${new Date().toISOString().slice(0, 10)}.${format}`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast($('status'), `Exported ${items.length} matching SWOT items.`, 'success');
  } catch (error) {
    fail(error);
  } finally {
    exporting = false;
    for (const id of ['exportExcel', 'exportPdf']) $(id).disabled = false;
    $('cancelExport').hidden = true;
  }
}
$('exportExcel').addEventListener('click', () => exportSwot('xlsx'));
$('exportPdf').addEventListener('click', () => exportSwot('pdf'));

$('itemForm').addEventListener('input', () => {
  if (isNew) draftDirty = true;
});
window.addEventListener('beforeunload', (event) => {
  if (isNew && draftDirty) {
    event.preventDefault();
    event.returnValue = '';
  }
});

$('visualisationTab').addEventListener('shown.bs.tab', () => {
  if (isNew) renderRelations();
});
