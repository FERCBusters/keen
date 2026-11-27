// The legal register: the list of laws, regulations and contractual
// requirements with its filters, export and categories, and one item's page
// with its details, assessments, related records, graph, questions and history.
// Everything shown is built as elements, so stored text is never parsed as HTML.
import { featureEnabled } from '/pages/feature-availability.js';
import { mountQuestionThreads } from '/pages/question-threads.js';
import { renderRelatedRecords, storiesCard } from '/pages/related-records.js';
import { renderRecordLinksGraph } from '/pages/record-links-graph.js';
import {
  errorText,
  initNavbar,
  initCollapsibleFilterSections,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  confirmAction,
  icon,
  isoDateToDisplay,
  node,
  toast,
  getCurrentFramework,
  withFramework,
  localPath,
  loadEntityChangelog,
  openAuditSampleModal,
  canSampleIntoAudit,
  safeExternalHref,
} from '/app.js';

const $ = (id) => document.getElementById(id),
  params = new URLSearchParams(location.search);
const me = await initNavbar(),
  framework = getCurrentFramework();
const manage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const itemId = params.get('id'),
  isNew = params.has('new'),
  base = '/api/v1/isms/legal-register';
const fw = `framework=${encodeURIComponent(framework)}`;
const PAGE = 100;
const LINK_TYPES = { document: 'Documents', asset: 'Assets', risk: 'Risks', swot: 'SWOT items' };
const LINK_FEATURES = { document: 'documents', asset: 'assets', risk: 'risk_cia', swot: 'swot' };
// Related records are offered only for the features that are switched on.
const types = Object.keys(LINK_TYPES).filter((type) => featureEnabled(me, LINK_FEATURES[type]));
const STATUS_BADGES = {
  not_assessed: 'text-bg-secondary',
  compliant: 'text-bg-success',
  partially_compliant: 'text-bg-warning',
  non_compliant: 'text-bg-danger',
};
// The register can be opened on the items related to one record.
const relatedTo =
  types.includes(params.get('target_type')) && params.get('target_id')
    ? { target_type: params.get('target_type'), target_id: params.get('target_id') }
    : null;
const TABS = ['details', 'assessments', 'related', 'graph', 'questions', 'changelog'];

let meta = { categories: [], statuses: [], outcomes: [], default_controls: [] };
let row = null,
  offset = 0,
  total = 0,
  busy = false;

initCollapsibleFilterSections();
const questions = mountQuestionThreads({
  panel: $('questionPanel'),
  me,
  status: $('status'),
  placeholder: 'Write a question about this legal register item',
  // Questions are read only for a saved item, which has an id.
  base: () => `/api/v1/questions/entities/isms_legal_item/${encodeURIComponent(itemId)}`,
});

function fail(error) {
  toast($('status'), errorText(error), 'danger');
}
const itemHref = (id) =>
  withFramework(`/legal-register.html?id=${encodeURIComponent(id)}`, framework);
// A related record's address comes from the server; one that is not a path on
// Keen opens nothing.
const recordHref = (link) => localPath(withFramework(link.url, framework)) || '#';
const displayDate = (value, empty = '') => isoDateToDisplay(value) || empty;
const clip = (text) => (text.length > 240 ? text.slice(0, 239) + '…' : text);
const statusLabel = (code) => meta.statuses.find((status) => status.code === code).label;
const statusBadge = (code) =>
  node('span', { class: `badge ${STATUS_BADGES[code]}` }, statusLabel(code));
const scopeBadge = (inScope) =>
  node(
    'span',
    { class: inScope ? 'badge badge-soft' : 'badge text-bg-light' },
    inScope ? 'In scope' : 'Out of scope',
  );
const dueBadge = () => node('span', { class: 'badge text-bg-warning ms-1' }, 'Due');
const muted = (text) => node('span', { class: 'small-muted' }, text);

function setOptions(select, records, first, text = (record) => record.name) {
  select.replaceChildren(
    ...(first ? [new Option(first, '')] : []),
    ...records.map((record) => new Option(text(record), record.id ?? record.code)),
  );
}

async function loadMeta() {
  meta = await apiGet(`${base}/meta?${fw}`);
  const filter = $('categoryFilter').value;
  setOptions($('categoryFilter'), meta.categories, 'All categories');
  $('categoryFilter').value = filter;
  setOptions($('category'), meta.categories, 'Uncategorised');
  setOptions($('statusFilter'), meta.statuses, 'All statuses', (status) => status.label);
  setOptions($('outcome'), meta.outcomes, '', (status) => status.label);
  $('categoryList').replaceChildren(...meta.categories.map(categoryRow));
}

// --- The register ---------------------------------------------------------------

function categoryRow(category) {
  return node(
    'div',
    { class: 'd-flex flex-wrap gap-2 align-items-center', 'data-category': category.id },
    node('input', {
      class: 'form-control form-control-sm w-auto flex-grow-1',
      maxlength: '128',
      'aria-label': `Name of ${category.name}`,
      value: category.name,
    }),
    muted(`${category.item_count} item${category.item_count === 1 ? '' : 's'}`),
    node('button', { type: 'button', class: 'btn btn-sm btn-outline-primary' }, 'Rename'),
    node(
      'button',
      {
        type: 'button',
        class: 'btn btn-sm btn-outline-danger',
        'data-remove': '1',
        // A category in use is moved off its items first.
        ...(category.item_count ? { disabled: '' } : {}),
      },
      'Delete',
    ),
  );
}

function registerRow(item) {
  const external = safeExternalHref(item.url);
  const cell = (...children) => node('td', {}, ...children);
  return node(
    'tr',
    {},
    cell(
      node('a', { class: 'fw-semibold', href: itemHref(item.id) }, item.title),
      external
        ? node(
            'a',
            {
              class: 'ms-1',
              href: external,
              target: '_blank',
              rel: 'noopener noreferrer',
              'aria-label': `Open the text of ${item.title}`,
            },
            icon('bi-box-arrow-up-right'),
          )
        : null,
      node(
        'div',
        { class: 'small-muted' },
        [item.jurisdiction, item.authority].filter(Boolean).join(' · '),
      ),
    ),
    cell(item.category?.name || ''),
    node('td', { class: 'wrap' }, clip(item.requirement)),
    node('td', { class: 'wrap' }, clip(item.applicability)),
    cell(scopeBadge(item.in_scope)),
    cell(statusBadge(item.compliance_status)),
    cell(displayDate(item.last_assessed_date)),
    cell(displayDate(item.next_assessment_date), item.assessment_due ? dueBadge() : null),
    cell(item.owner?.username || ''),
  );
}

function filterQuery() {
  const query = new URLSearchParams({ framework });
  for (const [key, id] of [
    ['q', 'search'],
    ['category_id', 'categoryFilter'],
    ['in_scope', 'scopeFilter'],
    ['status', 'statusFilter'],
  ])
    if ($(id).value) query.set(key, $(id).value);
  if ($('dueFilter').checked) query.set('due', 'true');
  for (const [key, value] of Object.entries(relatedTo || {})) query.set(key, value);
  return query;
}

let listRequest = 0;
// Shows the page of the register starting at `at`; the page in view changes
// only once its items arrive, so a failed turn leaves the pager where it was.
async function loadList(at = offset) {
  const request = ++listRequest;
  const query = filterQuery();
  // The export follows the filters the register is showing.
  $('exportCsv').href = `${base}/export.csv?${query}`;
  query.set('limit', String(PAGE));
  query.set('offset', String(at));
  const data = await apiGet(`${base}?${query}`);
  // A slower answer to an earlier filter never replaces a newer one.
  if (request !== listRequest) return;
  offset = at;
  total = data.total;
  $('registerRows').replaceChildren(
    ...(data.items.length
      ? data.items.map(registerRow)
      : [node('tr', {}, node('td', { colspan: '9', class: 'small-muted' }, 'No items match.'))]),
  );
  $('resultCount').textContent = data.items.length
    ? `Showing ${offset + 1}-${offset + data.items.length} of ${total} items.`
    : 'No items match.';
  $('previous').disabled = offset === 0;
  $('next').disabled = offset + PAGE >= total;
}

let filterTimer;
function applyFilters() {
  clearTimeout(filterTimer);
  loadList(0).catch(fail);
}
$('filters').addEventListener('submit', (event) => {
  event.preventDefault();
  applyFilters();
});
for (const id of ['categoryFilter', 'scopeFilter', 'statusFilter', 'dueFilter'])
  $(id).addEventListener('change', applyFilters);
$('search').addEventListener('input', () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(applyFilters, 250);
});
$('previous').addEventListener('click', () => loadList(offset - PAGE).catch(fail));
$('next').addEventListener('click', () => loadList(offset + PAGE).catch(fail));

$('manageCategories').addEventListener('click', () => {
  const panel = $('categoryPanel');
  panel.hidden = !panel.hidden;
  $('manageCategories').setAttribute('aria-expanded', String(!panel.hidden));
});

// One change at a time: a second click while a save is in flight does nothing.
async function change(work, done) {
  if (busy) return;
  busy = true;
  try {
    await work();
    if (done) toast($('status'), done, 'success');
  } catch (error) {
    fail(error);
  } finally {
    busy = false;
  }
}

async function reloadRegister() {
  await loadMeta();
  await loadList();
}

$('categoryForm').addEventListener('submit', (event) => {
  event.preventDefault();
  change(async () => {
    await apiPost(`${base}/categories`, {
      name: $('categoryName').value,
      description: $('categoryDescription').value,
    });
    $('categoryForm').reset();
    await reloadRegister();
  }, 'Category added.');
});
$('categoryList').addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  const entry = button.closest('[data-category]');
  const path = `${base}/categories/${encodeURIComponent(entry.dataset.category)}`;
  if (!button.dataset.remove)
    return change(async () => {
      await apiPatch(path, { name: entry.querySelector('input').value });
      await reloadRegister();
    }, 'Category renamed.');
  if (
    !(await confirmAction({
      title: 'Delete category',
      message: 'Delete this category? No item uses it.',
      confirmLabel: 'Delete',
      danger: true,
    }))
  )
    return;
  return change(async () => {
    await apiDelete(path);
    await reloadRegister();
  }, 'Category deleted.');
});

// --- One item -------------------------------------------------------------------

function refBadges(host, records, page, tab) {
  host.replaceChildren(
    ...(records.length
      ? records.map((record) =>
          node(
            'a',
            {
              class: 'badge badge-soft text-decoration-none me-1 mb-1',
              href: withFramework(
                `/${page}.html?id=${encodeURIComponent(record.id)}&tab=${tab}`,
                framework,
              ),
              title: record.title || record.ref,
            },
            record.ref,
          ),
        )
      : [muted('-')]),
  );
}

function mappingOptions(id, records, selected) {
  const chosen = new Set(selected.map((record) => String(record.id)));
  $(id).replaceChildren(
    ...records.map((record) => {
      const option = new Option(`${record.ref} ${record.title || ''}`.trim(), record.id);
      option.selected = chosen.has(String(record.id));
      return option;
    }),
  );
}

function assessmentRow(assessment) {
  return node(
    'tr',
    {},
    node('td', {}, displayDate(assessment.assessed_on)),
    node('td', {}, statusBadge(assessment.outcome)),
    node('td', {}, assessment.assessed_by?.username || ''),
    node('td', { class: 'wrap whitespace-pre-wrap' }, assessment.notes),
    node(
      'td',
      { class: 'text-end' },
      manage
        ? node(
            'button',
            {
              type: 'button',
              class: 'btn btn-sm btn-outline-danger',
              'data-remove-assessment': assessment.id,
              'aria-label': `Remove the assessment of ${assessment.assessed_on}`,
            },
            'Remove',
          )
        : null,
    ),
  );
}

function renderAssessments() {
  const summary = () => [
    statusBadge(row.compliance_status),
    row.assessment_due ? dueBadge() : null,
    node(
      'span',
      { class: 'small-muted ms-2' },
      `Last assessed: ${displayDate(row.last_assessed_date, 'never')} · Next assessment: ${displayDate(row.next_assessment_date, 'not set')}`,
    ),
  ];
  $('statusSummary').replaceChildren(...summary().filter(Boolean));
  $('assessmentSummary').replaceChildren(...summary().filter(Boolean));
  $('assessmentRows').replaceChildren(
    ...(row.assessments.length
      ? row.assessments.map(assessmentRow)
      : [
          node(
            'tr',
            {},
            node('td', { colspan: '5', class: 'small-muted' }, 'No assessments recorded.'),
          ),
        ]),
  );
}

function relatedLinks(type) {
  return row.links.filter((link) => link.target_type === type);
}

// The stories card is built once and shown again each time the tab is drawn.
let relatedStories = null;

function renderRelated() {
  renderRelatedRecords($('relatedList'), {
    types,
    label: (type) => LINK_TYPES[type],
    links: row.links,
    href: recordHref,
    manage,
    stories: relatedStories,
  });
}

function renderGraph() {
  const framed = (page, tab) => (record) => ({
    label: record.ref,
    detail: record.title || '',
    href: withFramework(`/${page}.html?id=${encodeURIComponent(record.id)}&tab=${tab}`, framework),
  });
  renderRecordLinksGraph($('graph'), {
    label: row.title,
    detail: row.category?.name || '',
    empty: 'This item is not linked to any control, clause or related record yet.',
    groups: [
      { title: 'Controls', items: row.controls.map(framed('control', 'items')) },
      { title: 'Clauses', items: row.clauses.map(framed('clause', 'related')) },
      ...types.map((type) => ({
        title: LINK_TYPES[type],
        items: relatedLinks(type).map((link) => ({
          label: link.title,
          detail: '',
          href: recordHref(link),
        })),
      })),
    ],
  });
}

let choiceRequest = 0;
async function choices(query = '') {
  const request = ++choiceRequest;
  const select = $('linkItem');
  select.replaceChildren(new Option('Choose a record', ''));
  select.disabled = true;
  const data = await apiGet(
    `${base}/choices/${encodeURIComponent($('linkType').value)}?q=${encodeURIComponent(query)}`,
  );
  if (request !== choiceRequest) return;
  for (const item of data.items) select.add(new Option(item.title, item.target_id));
  select.disabled = !data.items.length;
}

async function loadDetail() {
  if (!isNew) row = await apiGet(`${base}/${encodeURIComponent(itemId)}?${fw}`);
  const isms = await apiGet(`/api/v1/isms/meta?${fw}`);
  await loadMeta();
  $('pageTitle').textContent = row?.title || 'New legal register item';
  for (const [key, id] of [
    ['title', 'title'],
    ['url', 'url'],
    ['jurisdiction', 'jurisdiction'],
    ['authority', 'authority'],
    ['requirement', 'requirement'],
    ['applicability', 'applicability'],
    ['next_assessment_date', 'nextAssessment'],
    ['category_id', 'category'],
  ])
    if (row) $(id).value = row[key] ?? '';
  $('newCategory').value = '';
  $('inScope').value = String(row?.in_scope ?? true);
  const users = isms.users || [];
  setOptions($('owner'), users, 'Unassigned', (user) => user.username);
  $('owner').value = row?.owner_user_id || '';
  // A new item starts with the framework's control for legal requirements.
  const controls = row ? row.controls : meta.default_controls;
  const clauses = row?.clauses || [];
  mappingOptions('controls', isms.controls || [], controls);
  mappingOptions('clauses', isms.clauses || [], clauses);
  refBadges($('controlLinks'), controls, 'control', 'items');
  refBadges($('clauseLinks'), clauses, 'clause', 'related');
  $('editor').disabled = !manage;
  $('draftNotice').hidden = !isNew;
  for (const tab of TABS.slice(1)) $(tab + 'Tab').disabled = isNew;
  if (!row) return;
  const external = safeExternalHref(row.url);
  $('sourceLink').hidden = !external;
  if (external) $('sourceLink').href = external;
  $('deleteItem').hidden = !manage;
  $('sample').hidden = !canSampleIntoAudit(me, 'isms_legal_item');
  $('assessmentForm').hidden = !manage;
  $('linkForm').hidden = !manage || !types.length;
  setOptions(
    $('linkType'),
    types.map((type) => ({ id: type, name: LINK_TYPES[type] })),
  );
  setOptions($('assessedBy'), users, '', (user) => user.username);
  if (users.some((user) => user.id === me?.user_id)) $('assessedBy').value = me.user_id;
  $('assessedOn').value = new Date().toISOString().slice(0, 10);
  $('assessmentNext').value = '';
  $('assessmentNotes').value = '';
  renderAssessments();
  renderRelated();
  renderGraph();
  if (types.length && manage) await choices();
  await questions.refresh();
  await loadEntityChangelog(
    $('changelog'),
    `/api/v1/isms/legal-item/${encodeURIComponent(itemId)}/changelog?limit=100`,
    { empty: 'No changes recorded.' },
  );
}

$('itemForm').addEventListener('submit', (event) => {
  event.preventDefault();
  change(
    async () => {
      const selected = (id) => Array.from($(id).selectedOptions, (option) => option.value);
      const newCategory = $('newCategory').value.trim();
      const payload = {
        title: $('title').value,
        url: $('url').value.trim() || null,
        jurisdiction: $('jurisdiction').value,
        authority: $('authority').value,
        requirement: $('requirement').value,
        applicability: $('applicability').value,
        in_scope: $('inScope').value === 'true',
        owner_user_id: $('owner').value || null,
        next_assessment_date: $('nextAssessment').value || null,
        controls: selected('controls'),
        clauses: selected('clauses'),
        // A name typed for a new category takes the place of the one chosen.
        ...(newCategory
          ? { category_name: newCategory }
          : { category_id: $('category').value || null }),
      };
      if (isNew) {
        const created = await apiPost(`${base}?${fw}`, payload);
        location.assign(itemHref(created.id));
        return;
      }
      await apiPatch(`${base}/${encodeURIComponent(itemId)}?${fw}`, {
        ...payload,
        expected_version: row.version,
      });
      await loadDetail();
    },
    isNew ? '' : 'Legal register item saved.',
  );
});
// A field the browser refuses may be on a hidden tab; show the one holding it.
$('itemForm').addEventListener(
  'invalid',
  () => window.bootstrap?.Tab.getOrCreateInstance($('detailsTab')).show(),
  true,
);

$('assessmentForm').addEventListener('submit', (event) => {
  event.preventDefault();
  change(async () => {
    const next = $('assessmentNext').value;
    await apiPost(`${base}/${encodeURIComponent(itemId)}/assessments?${fw}`, {
      assessed_on: $('assessedOn').value,
      outcome: $('outcome').value,
      notes: $('assessmentNotes').value,
      assessed_by_user_id: $('assessedBy').value || null,
      expected_version: row.version,
      // Left empty, the item keeps the next assessment date it has.
      ...(next ? { next_assessment_date: next } : {}),
    });
    await loadDetail();
  }, 'Assessment added.');
});
$('assessmentRows').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-remove-assessment]');
  if (!button) return;
  if (
    !(await confirmAction({
      title: 'Remove assessment',
      message:
        'Remove this assessment? The item returns to the outcome of the newest assessment left.',
      confirmLabel: 'Remove',
      danger: true,
    }))
  )
    return;
  change(async () => {
    await apiDelete(
      `${base}/${encodeURIComponent(itemId)}/assessments/${encodeURIComponent(button.dataset.removeAssessment)}?expected_version=${row.version}&${fw}`,
    );
    await loadDetail();
  }, 'Assessment removed.');
});

$('linkType').addEventListener('change', () => choices().catch(fail));
$('findLinks').addEventListener('click', () => choices($('linkSearch').value).catch(fail));
$('linkForm').addEventListener('submit', (event) => {
  event.preventDefault();
  change(async () => {
    await apiPost(`${base}/${encodeURIComponent(itemId)}/links`, {
      target_type: $('linkType').value,
      target_id: $('linkItem').value,
      expected_version: row.version,
    });
    await loadDetail();
  }, 'Record linked.');
});
$('relatedList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-unlink-id]');
  if (!button) return;
  change(async () => {
    await apiDelete(
      `${base}/${encodeURIComponent(itemId)}/links/${button.dataset.unlinkType}/${encodeURIComponent(button.dataset.unlinkId)}?expected_version=${row.version}`,
    );
    await loadDetail();
  }, 'Record unlinked.');
});

$('deleteItem').addEventListener('click', async () => {
  if (
    !(await confirmAction({
      title: 'Delete legal register item',
      message: 'Delete this item and its assessments? Its changelog remains.',
      confirmLabel: 'Delete',
      danger: true,
    }))
  )
    return;
  change(async () => {
    await apiDelete(`${base}/${encodeURIComponent(itemId)}?expected_version=${row.version}&${fw}`);
    location.assign(withFramework('/legal-register.html', framework));
  });
});
$('sample').addEventListener('click', () =>
  openAuditSampleModal({
    me,
    entityType: 'isms_legal_item',
    entityId: row.id,
    title: row.title,
    evidenceUrl: itemHref(row.id),
    framework,
    statusEl: $('status'),
  }),
);
// The graph measures its pane, which has no width until its tab is shown.
$('graphTab').addEventListener('shown.bs.tab', renderGraph);

try {
  const detail = !!itemId || isNew;
  $('workspace').hidden = detail;
  $('detail').hidden = !detail;
  $('newItem').hidden = !manage || detail;
  $('manageCategories').hidden = !manage;
  $('relatedNotice').hidden = !relatedTo || detail;
  if (detail) {
    await loadDetail();
    // The stories that include the item are a card among its related items;
    // a draft has none yet.
    if (itemId) {
      relatedStories = storiesCard({
        me,
        kind: 'legal_item',
        id: itemId,
        framework,
        trigger: $('relatedTab'),
        noun: 'legal register item',
      });
      renderRelated();
    }
    const tab = TABS.includes(params.get('tab')) && !isNew ? params.get('tab') : 'details';
    window.bootstrap?.Tab.getOrCreateInstance($(tab + 'Tab')).show();
  } else {
    await loadMeta();
    await loadList();
  }
} catch (error) {
  fail(error);
}
