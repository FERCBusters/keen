import { mountQuestionThreads } from '/pages/question-threads.js';
import { catalogueLabel } from '/pages/catalogue-options.js';
import { controlHref, relevanceBadgeHtml, relevanceStrength } from '/pages/risk-common.js';
import { renderRecordLinksGraph } from '/pages/record-links-graph.js';
import {
  affectedBadges,
  affectedChoices,
  affectedPayload,
  loadAffectedOptions,
  mountAffectedPicker,
} from '/pages/affected-common.js';
import {
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  confirmAction,
  esc,
  fmtTs,
  toast,
  node,
  getCurrentFramework,
  withFramework,
  loadEntityChangelog,
  shorten,
  canSampleIntoAudit,
  openAuditSampleModal,
  lookupTab,
} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(location.search);
let itemId = params.get('id') || '';
const requestedTab = params.get('tab') || 'details';

const canViewPestle = !!(
  me?.is_admin ||
  me?.can_view_pestle ||
  me?.can_manage_pestle ||
  me?.can_view_risks ||
  me?.can_manage_risks
);
const canManagePestle = !!(me?.is_admin || me?.can_manage_pestle || me?.can_manage_risks);

const status = document.getElementById('status');
const pestleBreadcrumb = document.getElementById('pestleBreadcrumb');
const pestleList = document.getElementById('pestleList');
const pestleVisualisations = document.getElementById('pestleVisualisations');
const pestleItemTitle = document.getElementById('pestleItemTitle');
const pestleItemMeta = document.getElementById('pestleItemMeta');
const pestleBadges = document.getElementById('pestleBadges');
const pestleEditorTitle = document.getElementById('pestleEditorTitle');

const pestleDetailsTab = document.getElementById('pestleDetailsTab');
const pestleRelevanceTab = document.getElementById('pestleRelevanceTab');
const pestleGraphTab = document.getElementById('pestleGraphTab');
const pestleGraph = document.getElementById('pestleGraph');
const pestleQuestionsTab = document.getElementById('pestleQuestionsTab');
const pestleChangelogTab = document.getElementById('pestleChangelogTab');

const pestleForm = document.getElementById('pestleForm');
const pestleId = document.getElementById('pestleId');
const pestleType = document.getElementById('pestleType');
const pestleLens = document.getElementById('pestleLens');
const pestleOverallRelevance = document.getElementById('pestleOverallRelevance');
const pestleItemText = document.getElementById('pestleItemText');
const pestleRationale = document.getElementById('pestleRationale');
const savePestleItem = document.getElementById('savePestleItem');
const deletePestleItem = document.getElementById('deletePestleItem');
const samplePestleAudit = document.getElementById('samplePestleAudit');
const pestleAffected = document.getElementById('pestleAffected');
const pestleAffectedPicker = document.getElementById('pestleAffectedPicker');

// The item is rated against clauses and against controls in the same way:
// one editor each, differing in the record it lists and the path it saves to.
const RELEVANCE_EDITORS = [
  {
    key: 'clause',
    path: 'clauses',
    plural: 'clauses',
    heading: 'Clause',
    saved: 'Clause relevance saved',
    href: clauseHref,
    list: document.getElementById('clauseRelevanceList'),
    select: document.getElementById('clauseRelevance'),
    save: document.getElementById('saveClauseRelevance'),
    rows: [],
  },
  {
    key: 'control',
    path: 'controls',
    plural: 'controls',
    heading: 'Control',
    saved: 'Control relevance saved',
    href: (id) => controlHref(id, framework),
    list: document.getElementById('controlRelevanceList'),
    select: document.getElementById('controlRelevance'),
    save: document.getElementById('saveControlRelevance'),
    rows: [],
  },
];

const pestleChangelogEl = document.getElementById('pestleChangelog');
const pestleQuestions = mountQuestionThreads({
  panel: document.getElementById('pestleQuestions'),
  me,
  status,
  placeholder: 'Write a question about this PESTLE(E) item',
  base: () =>
    itemId ? `/api/v1/questions/entities/pestle_item/${encodeURIComponent(itemId)}` : null,
  unsaved: 'Save the item first.',
});

let meta = { types: [], lenses: [], relevance_levels: [] };
let currentItem = null;
// Managers who may read the People, Vendors and asset registers pick who and
// what the item affects; everyone else sees the records it names.
let affectedPicker = null;

function setTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (itemId) p.set('id', itemId);
  else p.delete('id');
  if (!tabName || tabName === 'details') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}${p.toString() ? `?${p.toString()}` : ''}`);
}

function activateTab(tabName) {
  const tab = lookupTab(
    {
      details: pestleDetailsTab,
      relevance: pestleRelevanceTab,
      visualisation: pestleGraphTab,
      questions: pestleQuestionsTab,
      changelog: pestleChangelogTab,
    },
    tabName,
    pestleDetailsTab,
  );
  if (!tab || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

function wireTabs() {
  pestleDetailsTab?.addEventListener('shown.bs.tab', () => setTabQuery('details'));
  pestleRelevanceTab?.addEventListener('shown.bs.tab', async () => {
    setTabQuery('relevance');
    if (itemId) await loadItemRelations(itemId);
  });
  pestleGraphTab?.addEventListener('shown.bs.tab', async () => {
    setTabQuery('visualisation');
    // An address that names this tab shows it before the item has loaded;
    // start-up draws the graph then.
    if (currentItem) await loadItemGraph(currentItem);
  });
  pestleQuestionsTab?.addEventListener('shown.bs.tab', async () => {
    setTabQuery('questions');
    await pestleQuestions.refresh();
  });
  pestleChangelogTab?.addEventListener('shown.bs.tab', async () => {
    setTabQuery('changelog');
    await loadPestleChangelog();
  });
  activateTab(requestedTab);
}

function relevanceOptions(selectedCodeOrId = 'na') {
  const selected = String(selectedCodeOrId);
  return meta.relevance_levels
    .map((r) => {
      const isSelected =
        selected !== '' && (selected === String(r.id) || selected === String(r.code));
      return `<option value="${esc(r.code)}" ${isSelected ? 'selected' : ''}>${esc(r.label)}</option>`;
    })
    .join('');
}

function fillSelects() {
  const typeOptions = meta.types
    .map((x) => `<option value="${esc(x)}">${esc(x)}</option>`)
    .join('');
  const lensOptions = meta.lenses
    .map((x) => `<option value="${esc(x)}">${esc(x)}</option>`)
    .join('');
  if (pestleType) pestleType.innerHTML = typeOptions;
  if (pestleLens) pestleLens.innerHTML = lensOptions;
  if (pestleOverallRelevance) pestleOverallRelevance.innerHTML = relevanceOptions('na');
}

function clauseHref(id) {
  return withFramework(`/clause.html?id=${encodeURIComponent(id)}&tab=pestle`, framework);
}

function setFormReadOnly(readonly) {
  const disabled = !!readonly;
  for (const el of [
    pestleType,
    pestleLens,
    pestleOverallRelevance,
    pestleItemText,
    pestleRationale,
  ]) {
    if (el) el.disabled = disabled;
  }
  if (savePestleItem) savePestleItem.style.display = disabled ? 'none' : '';
  if (samplePestleAudit)
    samplePestleAudit.style.display = canSampleIntoAudit(me, 'pestle_item') && itemId ? '' : 'none';
  if (deletePestleItem) deletePestleItem.style.display = !disabled && itemId ? '' : 'none';
  for (const editor of RELEVANCE_EDITORS) editor.save.style.display = disabled ? 'none' : '';
}

function formPayload() {
  const itemText = pestleItemText.value.trim();
  if (!itemText) throw new Error('Enter a PESTLE(E) item');
  return {
    framework,
    type: pestleType.value,
    lens: pestleLens.value,
    item: itemText,
    overall_relevance_code: pestleOverallRelevance.value,
    rationale: pestleRationale.value,
    ...(affectedPicker ? { affected: affectedPayload(affectedPicker.value()) } : {}),
  };
}

async function mountAffected() {
  if (!canManagePestle) return;
  const options = await loadAffectedOptions().catch(() => null);
  if (!options) return;
  affectedPicker = mountAffectedPicker(pestleAffectedPicker, {
    prefix: 'pestleAffected',
    options,
    chosen: affectedChoices(currentItem?.affected),
  });
  pestleAffectedPicker.hidden = false;
  pestleAffected.hidden = true;
}

function renderItem(item = null) {
  currentItem = item;
  if (pestleId) pestleId.value = item?.id || '';
  if (pestleType) pestleType.value = item?.type || meta.types[0];
  if (pestleLens) pestleLens.value = item?.lens || meta.lenses[0];
  if (pestleOverallRelevance) pestleOverallRelevance.value = item?.overall_relevance?.code || 'na';
  if (pestleItemText) pestleItemText.value = item?.item || '';
  if (pestleRationale) pestleRationale.value = item?.rationale || '';
  pestleAffected.replaceChildren(...affectedBadges(item?.affected, framework));
  affectedPicker?.set(affectedChoices(item?.affected));

  const isExisting = !!item?.id;
  const title = isExisting ? shorten(item.item, 90) : 'New PESTLE(E) item';
  if (pestleItemTitle) pestleItemTitle.textContent = title;
  if (pestleEditorTitle)
    pestleEditorTitle.textContent = isExisting
      ? `${canManagePestle ? 'Edit' : 'View'} PESTLE(E) item`
      : 'New PESTLE(E) item';
  if (pestleItemMeta) {
    const bits = isExisting
      ? [item.framework, `${item.type} / ${item.lens}`, `updated ${fmtTs(item.updated_at)}`]
      : [framework || 'selected framework', 'new item'];
    pestleItemMeta.textContent = bits.filter(Boolean).join(' · ');
  }
  if (pestleBadges) {
    pestleBadges.innerHTML = isExisting
      ? `${relevanceBadgeHtml(item.overall_relevance)} <span class="badge badge-soft">${esc(item.type)}</span> <span class="badge badge-soft">${esc(item.lens)}</span>`
      : '<span class="badge text-bg-light">unsaved</span>';
  }

  setFormReadOnly(!canManagePestle);
  if (!isExisting) {
    if (pestleChangelogEl)
      pestleChangelogEl.innerHTML = '<div class="p-3 small-muted">Save the item first.</div>';
    pestleQuestions.refresh();
    for (const editor of RELEVANCE_EDITORS) {
      editor.rows = [];
      renderRelevance(editor);
    }
  }
}

// Each list is a multiple select, which the design system presents as its
// picker: a record is chosen when the item is relevant to it, and carries how
// relevant as a value of its own. A record left out is not applicable.
function renderRelevance(editor) {
  const levels = meta.relevance_levels.filter((level) => level.code !== 'na');
  editor.select.dataset.pickerExtra = JSON.stringify({
    label: 'Relevance',
    choices: levels.map((level) => [level.code, level.label]),
    // A newly chosen record starts at the item's overall relevance.
    default: pestleOverallRelevance.value,
  });
  editor.select.disabled = !canManagePestle;
  editor.select.replaceChildren(
    ...editor.rows.map((row) => {
      const target = row[editor.key];
      const option = node('option', { value: target.id }, catalogueLabel(target));
      option.selected = row.relevance.code !== 'na';
      if (option.selected) option.dataset.extra = row.relevance.code;
      return option;
    }),
  );
  editor.list.textContent = itemId ? '' : 'Save the item first.';
  editor.list.hidden = Boolean(itemId);
}

async function loadMeta() {
  meta = await apiGet('/api/v1/pestle/meta');
  fillSelects();
}

async function loadItem() {
  if (!itemId) {
    if (!canManagePestle) {
      toast(status, 'Missing PESTLE(E) item id.', 'danger');
      return;
    }
    renderItem(null);
    return;
  }
  const u = new URL(`/api/v1/pestle/items/${encodeURIComponent(itemId)}`, location.origin);
  const item = await apiGet(u.pathname + u.search);
  renderItem(item);
}

async function loadItemRelations(id) {
  await Promise.all(
    RELEVANCE_EDITORS.map(async (editor) => {
      const data = await apiGet(`/api/v1/pestle/items/${encodeURIComponent(id)}/${editor.path}`);
      editor.rows = data.items;
      renderRelevance(editor);
    }),
  );
}

// The saved item with every clause and control it is rated above N/A for.
async function loadItemGraph(item) {
  await loadItemRelations(item.id);
  renderRecordLinksGraph(pestleGraph, {
    label: item.item,
    detail: `${item.type} / ${item.lens}`,
    empty: 'This item is not rated as relevant to any control or clause of this framework yet.',
    groups: [...RELEVANCE_EDITORS].reverse().map((editor) => ({
      title: `${editor.heading}s`,
      items: editor.rows
        .filter((row) => row.relevance.code !== 'na')
        .map((row) => ({
          label: row[editor.key].ref,
          detail: `${row.relevance.label} · ${row[editor.key].title}`,
          href: editor.href(row[editor.key].id),
          strength: relevanceStrength(row.relevance),
          note: `Relevance: ${row.relevance.label}`,
        })),
    })),
  });
}

async function loadPestleChangelog() {
  if (!itemId) {
    pestleChangelogEl.innerHTML = '<div class="p-3 small-muted">Save the item first.</div>';
    return;
  }
  const u = new URL(
    `/api/v1/pestle/items/${encodeURIComponent(itemId)}/changelog`,
    location.origin,
  );
  if (framework) u.searchParams.set('framework', framework);
  u.searchParams.set('limit', '100');
  await loadEntityChangelog(pestleChangelogEl, u.pathname + u.search, {
    empty: 'No PESTLE(E) item changes have been recorded yet.',
  });
}

async function saveCurrentItem(ev) {
  ev?.preventDefault();
  if (!canManagePestle) return;
  if (savePestleItem) savePestleItem.disabled = true;
  try {
    const payload = formPayload();
    const saved = itemId
      ? await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(itemId)}`, payload)
      : await apiPost('/api/v1/pestle/items', payload);
    itemId = saved.id;
    currentItem = saved;
    renderItem(saved);
    await Promise.all([
      loadItemRelations(itemId),
      loadPestleChangelog(),
      pestleQuestions.refresh(),
    ]);
    history.replaceState(
      {},
      '',
      withFramework(`/pestle_item.html?id=${encodeURIComponent(itemId)}`, framework),
    );
    toast(status, 'PESTLE(E) item saved', 'success');
  } catch (e) {
    toast(status, `Failed to save PESTLE(E) item: ${errorText(e)}`, 'danger');
  } finally {
    if (savePestleItem) savePestleItem.disabled = false;
  }
}

// Every record is sent: the chosen ones with their relevance, the rest as
// not applicable.
function relevancePayload(editor) {
  return {
    items: Array.from(editor.select.options, (option) => ({
      [`${editor.key}_id`]: option.value,
      relevance_code: option.selected ? option.dataset.extra : 'na',
    })),
  };
}

pestleForm?.addEventListener('submit', saveCurrentItem);
for (const editor of RELEVANCE_EDITORS) {
  editor.save.addEventListener('click', async () => {
    if (!canManagePestle || !itemId) return;
    editor.save.disabled = true;
    try {
      const data = await apiPatch(
        `/api/v1/pestle/items/${encodeURIComponent(itemId)}/${editor.path}`,
        relevancePayload(editor),
      );
      editor.rows = data.items;
      renderRelevance(editor);
      await Promise.all([loadItem(), loadPestleChangelog()]);
      toast(status, editor.saved, 'success');
    } catch (e) {
      toast(status, `Failed to save ${editor.key} relevance: ${errorText(e)}`, 'danger');
    } finally {
      editor.save.disabled = false;
    }
  });
}

samplePestleAudit?.addEventListener('click', () => {
  openAuditSampleModal({
    me,
    entityType: 'pestle_item',
    entityId: currentItem.id,
    title: currentItem.item,
    framework: currentItem.framework,
    statusEl: status,
  });
});

deletePestleItem?.addEventListener('click', async () => {
  if (
    !(await confirmAction({
      title: 'Delete PESTLE(E) item',
      message: 'Delete this PESTLE(E) item and all of its relevance mappings?',
      confirmLabel: 'Delete',
      danger: true,
    }))
  )
    return;
  deletePestleItem.disabled = true;
  try {
    await apiDelete(`/api/v1/pestle/items/${encodeURIComponent(itemId)}`);
    toast(status, 'PESTLE(E) item deleted', 'success');
    location.assign(withFramework('/pestle.html', framework));
  } catch (e) {
    toast(status, `Failed to delete PESTLE(E) item: ${errorText(e)}`, 'danger');
    deletePestleItem.disabled = false;
  }
});

if (pestleBreadcrumb) pestleBreadcrumb.href = withFramework('/pestle.html', framework);
if (pestleList) pestleList.href = withFramework('/pestle.html', framework);
if (pestleVisualisations)
  pestleVisualisations.href = withFramework('/pestle.html?tab=visualisations', framework);

wireTabs();

try {
  await loadMeta();
  if (!canViewPestle && !canManagePestle) {
    toast(status, 'You do not have permission to view PESTLE(E) assessments.', 'danger');
  } else {
    await loadItem();
    await mountAffected();
    if (itemId && requestedTab === 'relevance') await loadItemRelations(itemId);
    if (currentItem && requestedTab === 'visualisation') await loadItemGraph(currentItem);
    if (itemId && requestedTab === 'questions') await pestleQuestions.refresh();
    if (itemId && requestedTab === 'changelog') await loadPestleChangelog();
  }
} catch (e) {
  toast(status, `Failed to load PESTLE(E) item: ${errorText(e)}`, 'danger');
}
