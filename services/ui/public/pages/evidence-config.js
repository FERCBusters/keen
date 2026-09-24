import {apiGet, apiPost, apiPut, apiDelete} from '/app.js';

const $ = suffix => document.getElementById(`ec-${suffix}`);
function status(message, tone = 'danger') {
  const element = $('status');
  let readable = String(message || '');
  if (readable.startsWith('{')) {
    try {
      const detail = JSON.parse(readable).detail;
      if (typeof detail === 'string') readable = detail;
      else if (Array.isArray(detail)) readable = detail.map(item => item.msg || String(item)).join('; ');
    } catch { /* Keep plain text errors as they are. */ }
  }
  element.textContent = readable;
  element.hidden = !readable;
  element.className = readable ? `alert alert-${tone} mb-3` : '';
  element.setAttribute('role', readable && tone === 'danger' ? 'alert' : 'status');
}
const conditions = ['source', 'system', 'actor', 'action', 'outcome', 'severity', 'label'];
const regexConditions = ['source_regex', 'system_regex', 'actor_regex', 'action_regex', 'outcome_regex', 'label_regex', 'summary_regex'];
const bookstackConditions = ['bookstack_book_id', 'bookstack_book_slug', 'bookstack_page_id', 'bookstack_page_slug', 'bookstack_page_slug_regex', 'bookstack_page_title', 'bookstack_page_title_regex'];
const layouts = {
  jenkins: {jobs: ['name', 'label', 'kind']},
  loki: {queries: ['name', 'logql', 'event.source', 'event.system', 'event.action', 'event.outcome', 'event.severity']},
  rss: {feeds: ['name', 'label', 'system', 'url', 'enabled', 'max_items', 'overlap_minutes']},
  github: {organizations: ['org', 'label', 'include', 'org_events_mode', 'repo_limit'],
    repos: ['owner', 'repo', 'label'], feeds: ['url', 'label']},
  forgejo: {feeds: ['url', 'label']},
  cloudwatch_logs: {queries: ['name', 'region', 'log_group', 'filter_pattern', 'event.system', 'event.action', 'event.outcome']},
  taiga: {projects: ['id', 'label']},
  bookstack: {selected_pages: ['id', 'book_id', 'book_slug', 'slug', 'name'],
    page_mappings: ['match.book_slug', 'match.slug', 'match.title', 'match.title_regex', 'confidence', 'rationale', 'map_to']},
  google_workspace: {streams: ['name', 'enabled', 'application', 'user_key', 'system']},
  webhooks: {providers: ['ui_name', 'badge_color', 'secret_header', 'secret_env']},
};
const apiRoot = '/api/v1/admin';
let settingsVersion = 0;
let rules = [], ruleVersion = 0, selectedRule = null, targets = [], frameworks = [], samples = [], reviewed = null, previewedRule = null, previewCount = 0;
let collectors = [], definitionDocument = {}, definitionVersion = 0, definitionEntryKey = null;
const definitionAdapters = ['jenkins', 'loki', 'rss', 'github', 'forgejo', 'cloudwatch_logs', 'taiga', 'google_workspace', 'webhooks', 'bookstack'];
let catalogBooks = [], catalogPages = [], catalogChapters = [], nextBookOffset = null, nextPageOffset = null;
async function catalogRequest(kind, offset = 0, bookId = null) {
  return apiGet(`${apiRoot}/bookstack/catalog?kind=${kind}&offset=${offset}${bookId ? `&book_id=${bookId}` : ''}`);
}
async function loadBookstackBooks(more = false) {
  const data = await catalogRequest('books', more ? nextBookOffset : 0);
  if (!more) { catalogBooks = []; $('bookstack-book').replaceChildren(new Option('Choose a book', '')); }
  catalogBooks.push(...data.items); nextBookOffset = data.next_offset;
  for (const book of data.items) $('bookstack-book').add(new Option(book.name || book.slug || `Book ${book.id}`, String(book.id)));
  $('bookstack-more-books').hidden = nextBookOffset === null;
}
async function loadBookstackPages(more = false) {
  const bookId = Number($('bookstack-book').value);
  if (!bookId) return;
  if (!more) {
    catalogPages = []; $('bookstack-page').replaceChildren(new Option('Choose a page', ''));
    catalogChapters = [];
    let offset = 0;
    for (let batch = 0; batch < 5; batch++) {
      const data = await catalogRequest('chapters', offset, bookId);
      catalogChapters.push(...data.items);
      if (data.next_offset === null) break;
      offset = data.next_offset;
    }
  }
  const data = await catalogRequest('pages', more ? nextPageOffset : 0, bookId);
  catalogPages.push(...data.items); nextPageOffset = data.next_offset;
  const pageSelect = $('bookstack-page'); pageSelect.replaceChildren(new Option('Choose a page', ''));
  const groups = new Map();
  for (const page of catalogPages) {
    const chapter = catalogChapters.find(item => item.id === page.chapter_id);
    const title = chapter?.name || 'Pages outside chapters';
    if (!groups.has(title)) { const group = document.createElement('optgroup'); group.label = title; groups.set(title, group); pageSelect.append(group); }
    groups.get(title).append(new Option(page.name || page.slug || `Page ${page.id}`, String(page.id)));
  }
  $('bookstack-more-pages').hidden = nextPageOffset === null;
  $('bookstack-status').textContent = `${catalogPages.length} pages loaded from ${catalogBooks.find(book => book.id === bookId)?.name || 'this book'}.`;
}
function selectBookstackPage() {
  const book = catalogBooks.find(item => item.id === Number($('bookstack-book').value));
  const page = catalogPages.find(item => item.id === Number($('bookstack-page').value));
  if (!book || !page) return;
  for (const [name, value] of Object.entries({id: page.id, book_id: book.id, book_slug: book.slug,
    slug: page.slug, name: page.name})) {
    const input = $('definition-fields').querySelector(`[data-field="${name}"]`);
    if (input) input.value = value ?? '';
  }
  $('bookstack-status').textContent = `Selected ${book.name} → ${page.name}. Save to capture this page and map its evidence.`;
}
const definitionId = (adapter, section, key) => JSON.stringify([adapter, section, String(key)]);
function definitionKey(adapter, section, entry) {
  if (section === 'source') return '__all__';
  if (adapter === 'webhooks') return $('definition-entry').value === '__new' ?
    $('definition-fields').querySelector('[data-field="provider_name"]')?.value.trim() : definitionEntryKey;
  if (adapter === 'github' && section === 'repos') return `${entry.owner || ''}/${entry.repo || ''}`;
  const field = section === 'feeds' ? 'url' : section === 'organizations' ? 'org' :
    section === 'projects' || section === 'selected_pages' ? 'id' : 'name';
  return String(entry[field] ?? '').trim();
}
function definitionEntry() {
  if ($('definition-section').value === 'source') return {};
  const entry = JSON.parse($('definition-extra').value || '{}');
  if (!entry || Array.isArray(entry) || typeof entry !== 'object') throw new Error('Additional collection settings must be a JSON object.');
  for (const input of $('definition-fields').querySelectorAll('[data-field]')) {
    if (input.dataset.field === 'provider_name') continue;
    if (input.type !== 'checkbox' && !input.value.trim()) continue;
    pathSet(entry, input.dataset.field, input.type === 'checkbox' ? input.checked :
      input.type === 'number' ? Number(input.value) : input.value.trim());
  }
  return entry;
}
function paintDefinitionEntry() {
  const adapter = $('definition-adapter').value, section = $('definition-section').value;
  if (section === 'source') {
    definitionEntryKey = null;
    $('definition-fields').replaceChildren(); $('definition-extra').value = '{}';
    $('when-source').value = adapter;
    $('bookstack-picker').hidden = true;
    $('bookstack-conditions-panel').hidden = adapter !== 'bookstack';
    loadSamples().catch(error => status(error.message));
    return;
  }
  const selected = $('definition-entry').value;
  const found = collectors.find(item => item.adapter === adapter && item.section === section && item.collector === selected);
  definitionEntryKey = found?.key ?? null;
  const entry = found?.entry || {};
  const fields = $('definition-fields'); fields.replaceChildren();
  if (adapter === 'webhooks') {
    const provider = fieldNode('provider_name', found?.key || '');
    if (found) provider.querySelector('[data-field]').readOnly = true;
    fields.append(provider);
  }
  const names = layouts[adapter][section];
  for (const name of names) {
    const current = pathGet(entry, name) ?? (selected === '__new' && name === 'enabled' ? true : undefined);
    const field = fieldNode(name, current, typeof current === 'boolean' || name === 'enabled' ? 'checkbox' :
      typeof current === 'number' || ['event.severity', 'id', 'book_id'].includes(name) ? 'number' : 'text');
    field.className = name === 'logql' || name === 'filter_pattern' ? 'col-12' : 'col-md-6';
    if (adapter === 'bookstack') field.querySelector('[data-field]').readOnly = true;
    fields.append(field);
  }
  const extra = structuredClone(entry);
  for (const name of names) {
    const parts = name.split('.'); let cursor = extra;
    for (const part of parts.slice(0, -1)) cursor = cursor?.[part];
    if (cursor) delete cursor[parts.at(-1)];
  }
  $('definition-extra').value = JSON.stringify(extra, null, 2);
  $('when-source').value = adapter === 'webhooks' ? `webhook:${found?.key || ''}` : adapter;
  $('bookstack-picker').hidden = adapter !== 'bookstack';
  $('bookstack-conditions-panel').hidden = adapter !== 'bookstack';
  loadSamples().catch(error => status(error.message));
}
function paintDefinitionChoices() {
  const adapter = $('definition-adapter').value, section = $('definition-section').value;
  const choice = $('definition-entry');
  if (section === 'source') { choice.replaceChildren(new Option(`All events from ${adapter}`, '__all__')); paintDefinitionEntry(); return; }
  choice.replaceChildren(new Option('Create a new collection item', '__new'));
  for (const item of collectors.filter(item => item.adapter === adapter && item.section === section))
    choice.add(new Option(item.key, item.collector));
  paintDefinitionEntry();
}
async function selectDefinitionAdapter() {
  const adapter = $('definition-adapter').value;
  if (layouts[adapter]) {
    const data = await apiGet(`${apiRoot}/managed-configurations/${adapter}`);
    definitionDocument = data.document; definitionVersion = data.version;
  } else { definitionDocument = {}; definitionVersion = 0; }
  $('definition-section').replaceChildren();
  $('definition-section').add(new Option('All events from this source', 'source'));
  for (const section of Object.keys(layouts[adapter] || {}))
    if (section !== 'page_mappings') $('definition-section').add(new Option(label(section), section));
  paintDefinitionChoices();
  if (adapter === 'bookstack') {
    try { await loadBookstackBooks(); }
    catch (error) { $('bookstack-status').textContent = error.message; }
  }
}
function definitionFromForm() {
  const adapter = $('definition-adapter').value, section = $('definition-section').value;
  if (section === 'source') return {adapter, section, entry: {}, key: '__all__', collector: null,
    entry_key: null, connector_version: 0};
  const entry = definitionEntry(), key = definitionKey(adapter, section, entry);
  if (!key || key === '/') throw new Error('Give the collection item a name, URL or ID.');
  return {adapter, section, entry, key, collector: definitionId(adapter, section, key),
    entry_key: definitionEntryKey, connector_version: definitionVersion};
}
let step = 1, visibleRules = 20;
const fieldHelp = {
  system: 'The service or product involved, such as Jenkins, GitHub or a production server. Use this when the rule applies to that system.',
  actor: 'The person or account that performed the action. Often changes per event; leave blank to include all actors.',
  action: 'What happened, such as build_completed, login or deployment. Usually a useful match condition.',
  outcome: 'The result, such as success or failure. Use this to distinguish successful checks from failed ones.',
  severity: 'A numeric level assigned by the connector. Leave blank unless the connector consistently sets it.',
  label: 'A connector-specific tag, such as a job or feed label. Use this to narrow the rule to a particular stream.'
};
const stepTitles = ['Describe', 'Match evidence', 'Map frameworks', 'Preview and save'];
function showStep(number) {
  step = Math.max(1, Math.min(4, number));
  for (const section of document.querySelectorAll('[data-ec-step]')) section.hidden = Number(section.dataset.ecStep) !== step;
  $('step-number').textContent = step; $('step-title').textContent = stepTitles[step - 1];
  $('step-progress').style.width = `${step * 25}%`;
  $('step-progress').parentElement.setAttribute('aria-valuenow', String(step));
  $('prev-step').hidden = step === 1; $('next-step').hidden = step === 4;
  $('editor-heading').textContent = selectedRule ? `Edit ${$('description').value || selectedRule}` : 'Create an evidence definition';
}
function showEditor(number = 1) { $('library').hidden = true; $('editor').hidden = false; showStep(number); }
function showLibrary() { pollingJobId = null; $('editor').hidden = true; $('library').hidden = false; renderRules(); }
function nextStep() {
  if (step === 1 && !$('description').value.trim()) { status('Describe what this evidence demonstrates before continuing.'); $('description').focus(); return; }
  if (step === 2) { try { definitionFromForm(); } catch (error) { status(error.message); return; } }
  if (step === 3) addSelectedTarget();
  if (step === 3 && !targets.length) { status('Add at least one framework control or clause before continuing.'); $('framework').focus(); return; }
  status(''); showStep(step + 1);
}

const label = field => field.replaceAll('.', ' / ').replaceAll('_', ' ');
const inputType = section => ({queries: 'query', jobs: 'job', feeds: 'feed',
  selected_pages: 'page', organizations: 'organisation', repos: 'repository',
  projects: 'project', streams: 'stream', providers: 'provider'})[section] || label(section);
function fieldNode(name, value, kind = 'text') {
  const wrapper = document.createElement('div');
  wrapper.className = 'mb-2';
  const text = document.createElement('label');
  text.className = 'form-label';
  text.textContent = label(name);
  wrapper.append(text);
  const input = name === 'logql' || name === 'rationale' ? document.createElement('textarea') : document.createElement('input');
  input.className = 'form-control';
  input.dataset.field = name;
  if (kind === 'checkbox') {
    input.type = 'checkbox'; input.className = 'form-check-input'; input.checked = Boolean(value);
  } else { input.value = value ?? ''; if (kind === 'number') input.type = 'number'; }
  wrapper.append(input);
  const helpText = name === 'kind' && $('definition-adapter').value === 'jenkins' ?
    'Action assigned to Jenkins events from this job, such as build or deployment. Match it with the Action field below.' :
    name === 'label' && $('definition-adapter').value === 'jenkins' ?
      'System assigned to Jenkins events from this job. Match it with the System field below.' :
    name === 'logql' ? 'Loki query selecting log lines to collect. A separate match below narrows the resulting events.' : '';
  if (helpText) { const help = document.createElement('div'); help.className = 'form-text'; help.textContent = helpText; wrapper.append(help); }
  return wrapper;
}
function pathGet(obj, path) { return path.split('.').reduce((value, part) => value?.[part], obj); }
function pathSet(obj, path, value) {
  const parts = path.split('.');
  let cursor = obj;
  for (const part of parts.slice(0, -1)) cursor = cursor[part] ||= {};
  cursor[parts.at(-1)] = value;
}
async function loadRevisions(name, element) {
  const data = await apiGet(`${apiRoot}/configuration-revisions/${encodeURIComponent(name)}`);
  element.replaceChildren(new Option('Select revision', ''));
  for (const row of data.items) element.add(new Option(`#${row.version} · ${row.at}`, String(row.version)));
}

async function loadSamples() {
  const source = $('when-source').value.trim();
  const identity = $('definition-entry').value;
  const isCollection = collectors.some(item => item.adapter === $('definition-adapter').value && item.collector === identity);
  const filter = isCollection ? `&collector=${encodeURIComponent(identity)}` : '';
  const result = source && source !== 'webhook:' ?
    await apiGet(`${apiRoot}/evidence-event-samples?source=${encodeURIComponent(source)}${filter}`) : {items: []};
  samples = result.items || [];
  $('sample').replaceChildren(new Option(samples.length ? 'Select an event' : 'No recent events for this source', ''));
  for (const [index, event] of samples.entries()) $('sample').add(new Option(`${event.action || ''} · ${event.summary}`, String(index)));
}
function conditionForm() {
  for (const field of conditions.filter(value => value !== 'source')) {
    const wrapper = fieldNode(field, '', field === 'severity' ? 'number' : 'text');
    wrapper.className = 'col-md-6';
    wrapper.querySelector('[data-field]').id = `ec-when-${field}`;
    const help = document.createElement('div'); help.className = 'form-text'; help.id = `ec-help-${field}`;
    help.textContent = fieldHelp[field]; wrapper.append(help);
    wrapper.querySelector('[data-field]').setAttribute('aria-describedby', help.id);
    $('conditions').append(wrapper);
  }
  for (const field of regexConditions) {
    const wrapper = fieldNode(field, ''); wrapper.className = 'col-md-6';
    wrapper.querySelector('[data-field]').id = `ec-when-${field}`;
    const help = document.createElement('div'); help.className = 'form-text';
    help.textContent = `Pattern for ${field.replace('_regex', '')}. Leave blank unless exact matching is insufficient.`;
    wrapper.append(help);
    $('regex-conditions').append(wrapper);
  }
  for (const field of bookstackConditions) {
    const wrapper = fieldNode(field.replace('bookstack_', ''), '', field.endsWith('_id') ? 'number' : 'text');
    wrapper.className = 'col-md-6';
    const input = wrapper.querySelector('[data-field]'); input.dataset.field = field; input.id = `ec-when-${field}`;
    const help = document.createElement('div'); help.className = 'form-text';
    help.textContent = 'Optional BookStack selector. Only pages matching this value contribute evidence.';
    wrapper.append(help); $('bookstack-conditions').append(wrapper);
  }
}
function readRule() {
  const definition = definitionFromForm();
  const when = {};
  for (const field of [...conditions, ...regexConditions, ...bookstackConditions]) {
    const value = $(`when-${field}`).value.trim();
    if (value) when[field] = ['severity', 'bookstack_book_id', 'bookstack_page_id'].includes(field) ? Number(value) : value;
  }
  when.source = definition.section === 'source' && selectedRule && $('when-source').value.startsWith('webhook:') ?
    $('when-source').value : definition.adapter === 'webhooks' && definition.section !== 'source' ? `webhook:${definition.key}` : definition.adapter;
  if (definition.collector) when.collector = definition.collector;
  if (!$('rule-id').value.trim()) {
    const basis = definition.section === 'source' ?
      `${definition.adapter}_${$('description').value}` : `${definition.adapter}_${definition.key}`;
    const stem = basis.toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 88) || 'evidence';
    let candidate = stem, suffix = 2;
    while (rules.some(rule => rule.id === candidate)) candidate = `${stem}_${suffix++}`;
    $('rule-id').value = candidate;
  }
  return {id: $('rule-id').value.trim(), description: $('description').value.trim(), when,
    map_to: structuredClone(targets), confidence: Number($('confidence').value), enabled: $('enabled').checked};
}
function newRule() {
  selectedRule = null; previewedRule = null; previewCount = 0;
  $('input-picker').hidden = false; $('selected-input').hidden = true;
  $('apply-existing').checked = true; $('predefine').checked = false;
  $('rule-id').value = ''; $('rule-id').disabled = false;
  $('description').value = ''; $('confidence').value = '.8'; $('enabled').checked = true;
  for (const field of [...conditions, ...regexConditions, ...bookstackConditions]) $(`when-${field}`).value = '';
  targets = []; renderTargets(); $('preview-results').textContent = ''; $('backfill').replaceChildren();
  $('sample').replaceChildren(new Option('Choose a source to see recent events', ''));
  $('sample-details').textContent = 'Choose a recent event to inspect its fields. You can also define a rule before evidence arrives.';
  if ($('definition-section').options.length) paintDefinitionChoices();
  showEditor(1);
}
function showSelectedInput() {
  const adapter = $('definition-adapter').value, section = $('definition-section').value;
  const collector = collectors.find(item => item.collector === $('definition-entry').value && item.adapter === adapter);
  $('selected-input').textContent = section === 'source' ?
    `Selected input: All ${$('when-source').value || adapter} events. Choose another definition from the library to change inputs.` :
    `Selected input: ${adapter} ${inputType(section)} “${collector?.key || $('definition-entry').selectedOptions[0]?.textContent || ''}”. Choose another definition from the library to change inputs.`;
  $('input-picker').hidden = true; $('selected-input').hidden = false;
}
async function editRule(rule) {
  newRule(); selectedRule = rule.id;
  $('rule-id').value = rule.id; $('rule-id').disabled = true;
  $('description').value = rule.description || ''; $('confidence').value = rule.confidence ?? .8;
  $('enabled').checked = rule.enabled !== false;
  for (const field of [...conditions, ...regexConditions, ...bookstackConditions]) $(`when-${field}`).value = rule.when?.[field] ?? '';
  targets = structuredClone(rule.map_to || []); renderTargets();
  const linked = collectors.find(item => item.collector === rule.when?.collector);
  if (linked) {
    $('definition-adapter').value = linked.adapter;
    await selectDefinitionAdapter();
    $('definition-section').value = linked.section;
    paintDefinitionChoices(); $('definition-entry').value = linked.collector; paintDefinitionEntry();
  } else {
    const source = rule.when?.source || 'webhook';
    const adapter = source.startsWith('webhook:') ? 'webhooks' : source;
    if (!$('definition-adapter').querySelector(`option[value="${CSS.escape(adapter)}"]`))
      $('definition-adapter').add(new Option(adapter.replaceAll('_', ' '), adapter));
    $('definition-adapter').value = adapter;
    await selectDefinitionAdapter(); $('definition-section').value = 'source'; paintDefinitionChoices();
    $('when-source').value = source;
  }
  showSelectedInput();
  loadRecentBackfill(rule.id).catch(error => status(error.message));
  showStep(1);
}
function renderRules() {
  const list = $('rules'); list.replaceChildren();
  const query = $('rule-filter').value.trim().toLowerCase();
  const linked = new Set(rules.map(rule => rule.when?.collector).filter(Boolean));
  const entries = [
    ...rules.map(rule => ({rule, origin: collectors.find(item => item.collector === rule.when?.collector)})),
    ...collectors.filter(item => !linked.has(item.collector)).map(origin => ({origin})),
  ];
  const filtered = entries.filter(({rule, origin}) => !query ||
    `${rule?.id || ''} ${rule?.description || ''} ${rule?.when?.source || ''} ${JSON.stringify(rule?.map_to || [])} ${origin?.adapter || ''} ${origin?.key || ''}`.toLowerCase().includes(query));
  $('rule-count').textContent = `${filtered.length} of ${entries.length} evidence definitions and available collection items${filtered.length > visibleRules ? ` · showing first ${visibleRules}` : ''}`;
  $('more-rules').hidden = filtered.length <= visibleRules;
  if (!filtered.length) { const empty = document.createElement('p'); empty.className = 'small-muted'; empty.textContent = query ? 'No evidence definitions match that search.' : 'No evidence definitions yet. Create one to get started.'; list.append(empty); }
  for (const {rule, origin} of filtered.slice(0, visibleRules)) {
    const btn = document.createElement('button'); btn.type = 'button';
    btn.className = 'list-group-item list-group-item-action text-start';
    const title = document.createElement('strong'); title.textContent = rule?.description || rule?.id || `${origin.key} · needs framework mappings`;
    const meta = document.createElement('div'); meta.className = 'small-muted';
    meta.textContent = rule ? `${rule.enabled === false ? 'Paused · ' : ''}${origin ?
      `${origin.adapter} ${inputType(origin.section)}: ${origin.key}` :
      `All ${rule.when?.source || 'source'} events`} · ${rule.map_to?.length || 0} framework targets` :
      `${origin.adapter} ${inputType(origin.section)}: ${origin.key} · add framework targets`;
    btn.append(title, meta);
    btn.addEventListener('click', () => {
      if (rule) { editRule(rule).catch(error => status(error.message)); return; }
      newRule(); $('definition-adapter').value = origin.adapter;
      selectDefinitionAdapter().then(() => {
        $('definition-section').value = origin.section; paintDefinitionChoices();
        $('definition-entry').value = origin.collector; paintDefinitionEntry();
        showSelectedInput();
      }).catch(error => status(error.message));
    }); list.append(btn);
  }
}
function renderTargets() {
  const list = $('targets'); list.replaceChildren();
  for (const [index, target] of targets.entries()) {
    const row = document.createElement('div'); row.className = 'list-group-item d-flex justify-content-between align-items-center';
    const text = document.createElement('span'); text.textContent = `${target.framework} / ${target.ref}${target.roles_any?.length ? ` · roles: ${target.roles_any.join(', ')}` : ''}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn-sm btn-outline-danger'; remove.textContent = 'Remove';
    remove.addEventListener('click', () => { targets.splice(index, 1); renderTargets(); });
    row.append(text, remove); list.append(row);
  }
}
async function loadRules() {
  const data = await apiGet(`${apiRoot}/evidence-definitions`);
  rules = data.rules; collectors = data.collectors; ruleVersion = data.rules_version;
  renderRules(); newRule(); showLibrary();
  if (data.warnings?.length) status(data.warnings.join(' · '), 'warning');
  await loadRevisions('rules', $('rule-revision'));
}
async function loadFrameworkControls() {
  const data = await apiGet(`/api/v1/controls?framework=${encodeURIComponent($('framework').value)}&limit=5000`);
  $('control').replaceChildren();
  for (const control of data.items) $('control').add(new Option(`${control.ref} — ${control.title || control.type}`, control.ref));
}
$('framework').addEventListener('change', () => loadFrameworkControls().catch(error => status(error.message)));
async function restore(selectedRevision) {
  if (!selectedRevision || !confirm(`Restore rules revision #${selectedRevision}? This creates a new revision.`)) return;
  const result = await apiPost(`${apiRoot}/configuration-revisions/rules/${encodeURIComponent(selectedRevision)}/restore`, {version: ruleVersion});
  await loadRules(); status(`Restored rules as revision ${result.version}.`, 'success');
}
$('rule-restore').addEventListener('click', () => restore($('rule-revision').value).catch(error => status(error.message)));
async function loadSettings() {
  const adapter = $('settings-adapter').value;
  const data = await apiGet(`${apiRoot}/adapter-settings/${encodeURIComponent(adapter)}`);
  settingsVersion = data.version;
  $('settings-json').value = JSON.stringify(data.settings, null, 2);
}
$('settings-adapter').addEventListener('change', () => loadSettings().catch(error => status(error.message)));
$('save-settings').addEventListener('click', async () => {
  try {
    const adapter = $('settings-adapter').value;
    const settings = JSON.parse($('settings-json').value);
    const result = await apiPut(`${apiRoot}/adapter-settings/${encodeURIComponent(adapter)}`, {version: settingsVersion, settings});
    await loadSettings(); status(`Saved shared ${adapter} settings as revision ${result.version}.`, 'success');
  } catch (error) { status(error.message); }
});
$('when-source').addEventListener('change', () => loadSamples().catch(error => status(error.message)));
$('definition-adapter').addEventListener('change', () => selectDefinitionAdapter().catch(error => status(error.message)));
$('definition-section').addEventListener('change', paintDefinitionChoices);
$('definition-entry').addEventListener('change', paintDefinitionEntry);
$('bookstack-book').addEventListener('change', () => loadBookstackPages().catch(error => $('bookstack-status').textContent = error.message));
$('bookstack-page').addEventListener('change', selectBookstackPage);
$('bookstack-more-books').addEventListener('click', () => loadBookstackBooks(true).catch(error => $('bookstack-status').textContent = error.message));
$('bookstack-more-pages').addEventListener('click', () => loadBookstackPages(true).catch(error => $('bookstack-status').textContent = error.message));
$('sample').addEventListener('change', () => {
  const sample = samples[Number($('sample').value)];
  $('sample-details').textContent = $('sample').value && sample ?
    ['source', 'system', 'actor', 'action', 'outcome', 'severity', 'label'].map(field => `${field}: ${sample[field] ?? '(empty)'}`).join(' · ') :
    'Choose a recent event to inspect its fields.';
});
$('use-sample').addEventListener('click', () => {
  if (!$('sample').value) return;
  const sample = samples[Number($('sample').value)];
  if (!sample) return;
  $('sample').dispatchEvent(new Event('change'));
  // System, action and outcome usually identify the event type. Actor and
  // severity vary more often, so leave those for the administrator to choose.
  for (const field of ['source', 'system', 'action', 'outcome'])
    if (sample[field] !== null && sample[field] !== undefined) $(`when-${field}`).value = sample[field];
  if (!selectedRule && !$('rule-id').value) {
    $('rule-id').value = [sample.source, sample.system, sample.action, sample.outcome]
      .filter(Boolean).join('_').toLowerCase().replace(/[^a-z0-9_-]+/g, '_').slice(0, 96);
  }
  $('preview-results').textContent = 'Review the filled fields, select framework targets, then preview matches.';
  previewedRule = null;
});
$('prune-source').addEventListener('input', () => { reviewed = null; $('prune-apply').disabled = true; });
$('prune-offset').addEventListener('input', () => { reviewed = null; $('prune-apply').disabled = true; });
$('prune-preview').addEventListener('click', async () => {
  try {
    const source = $('prune-source').value.trim();
    if (!source) throw new Error('Enter the evidence source first');
    const offset = Number($('prune-offset').value);
    reviewed = await apiGet(`${apiRoot}/stale-rule-mappings?source=${encodeURIComponent(source)}&offset=${offset}`);
    $('prune-apply').disabled = reviewed.stale.length === 0;
    const out = $('prune-results'); out.replaceChildren();
    const heading = document.createElement('p');
    heading.textContent = `${reviewed.stale.length} stale of ${reviewed.examined} rule mappings examined. Batch starts at ${offset}. ${reviewed.next_offset !== null ? `Continue at offset ${reviewed.next_offset} to review older records.` : 'End of results.'}`;
    out.append(heading);
    for (const item of reviewed.stale) {
      const line = document.createElement('p'); line.textContent = `${item.framework} / ${item.ref}: ${item.summary}`; out.append(line);
    }
  } catch (error) { reviewed = null; $('prune-apply').disabled = true; $('prune-results').textContent = error.message; }
});
$('prune-apply').addEventListener('click', async () => {
  if (!reviewed?.stale?.length) return;
  if (!confirm(`Remove ${reviewed.stale.length} reviewed automatic mappings? Manual mappings are retained.`)) return;
  try {
    const result = await apiPost(`${apiRoot}/stale-rule-mappings/prune`, {
      version: reviewed.version, source: reviewed.source,
      mapping_ids: reviewed.stale.map(item => item.mapping_id)});
    reviewed = null; $('prune-apply').disabled = true;
    $('prune-results').textContent = `Removed ${result.deleted} stale automatic mappings. Review again to continue.`;
  } catch (error) { $('prune-results').textContent = error.message; $('prune-apply').disabled = true; }
});
$('rule-filter').addEventListener('input', () => { visibleRules = 20; renderRules(); });
$('more-rules').addEventListener('click', () => { visibleRules += 20; renderRules(); });
$('close-editor').addEventListener('click', showLibrary);
$('prev-step').addEventListener('click', () => showStep(step - 1));
$('next-step').addEventListener('click', nextStep);
$('new-rule').addEventListener('click', newRule);
function addSelectedTarget() {
  const framework = $('framework').value, ref = $('control').value;
  if (!framework || !ref) return;
  if (!targets.some(t => t.framework === framework && t.ref === ref)) targets.push({framework, ref});
  renderTargets();
}
$('add-target').addEventListener('click', addSelectedTarget);
$('preview').addEventListener('click', async () => {
  try {
    const rule = readRule();
    const data = await apiPost(`${apiRoot}/mapping-rules/preview`, {version: ruleVersion, rule});
    previewedRule = JSON.stringify(rule); previewCount = data.examined;
    const container = $('preview-results'); container.replaceChildren();
    const heading = document.createElement('p'); heading.textContent = `${data.matched} of ${data.examined} recent ${rule.when.source} events matched. ${data.note || ''}`;
    container.append(heading);
    for (const item of data.examples) {
      const p = document.createElement('p'); p.textContent = `Matches: ${item.summary} → ${Object.entries(item.targets).map(([fw, refs]) => `${fw}: ${refs.join(', ')}`).join('; ')}`;
      container.append(p);
    }
    for (const item of data.nonmatches || []) {
      const p = document.createElement('p'); p.textContent = `Does not match: ${item.summary} (${item.system || 'no system'} / ${item.action || 'no action'} / ${item.outcome || 'no outcome'})`;
      container.append(p);
    }
  } catch (error) { previewedRule = null; $('preview-results').textContent = error.message; }
});
let pollingJobId = null;
function showBackfill(data) {
  const element = $('backfill'); element.replaceChildren();
  if (!data || data.status === 'no_previous_evidence') {
    element.textContent = 'No previously collected evidence to evaluate. The rule will apply to future events.';
    return;
  }
  const message = document.createElement('span');
  message.textContent = `Historical evidence: ${data.status}. Checked ${data.examined || 0} of ${data.total_estimate ? `about ${data.total_estimate}` : 'an estimated total being calculated'} events; ${data.matched || 0} matched, ${data.created_mappings || 0} new mappings.${data.error ? ` Error: ${data.error}` : ''}`;
  element.append(message);
  if (['queued', 'running'].includes(data.status)) {
    const cancel = document.createElement('button'); cancel.type = 'button';
    cancel.className = 'btn btn-sm btn-outline-secondary ms-2'; cancel.textContent = 'Cancel';
    cancel.addEventListener('click', async () => {
      if (!confirm('Stop applying this rule to older evidence? Completed batches stay mapped.')) return;
      await apiPost(`${apiRoot}/rule-backfills/${encodeURIComponent(data.id)}/cancel`, {});
      await pollBackfill(data.id);
    });
    element.append(cancel);
  }
}
async function pollBackfill(id) {
  pollingJobId = id;
  const data = await apiGet(`${apiRoot}/rule-backfills/${encodeURIComponent(id)}`);
  if (pollingJobId !== id) return;
  showBackfill(data);
  if (['queued', 'running'].includes(data.status)) setTimeout(() => {
    if (pollingJobId === id) pollBackfill(id).catch(error => status(error.message));
  }, 5000);
}
async function loadRecentBackfill(ruleId) {
  const result = await apiGet(`${apiRoot}/rule-backfills?rule_id=${encodeURIComponent(ruleId)}`);
  if (result.items.length) await pollBackfill(result.items[0].id);
}
$('save-rule').addEventListener('click', async () => {
  try {
    const rule = readRule();
    if (!$('predefine').checked && JSON.stringify(rule) !== previewedRule)
      throw new Error('Preview this version of the rule before saving, or choose Advanced → Predefine before evidence is available.');
    if (previewedRule && !previewCount && !$('predefine').checked &&
        !confirm('There is no recent evidence for this source. Save the rule without a sample match?')) return;
    const definition = definitionFromForm();
    const saved = await apiPut(`${apiRoot}/evidence-definitions/${encodeURIComponent(rule.id)}`, {
      adapter: definition.adapter, section: definition.section, entry_key: definition.entry_key,
      entry: definition.entry, rule, connector_version: definition.connector_version,
      rules_version: ruleVersion, apply_existing: $('apply-existing').checked});
    await loadRules(); await editRule(rules.find(item => item.id === rule.id));
    showStep(4);
    if (saved.backfill?.id) await pollBackfill(saved.backfill.id);
    else if (saved.backfill) showBackfill(saved.backfill);
    status('Evidence definition saved. Future evidence uses it immediately; historical evaluation runs in the background when selected.', 'success');
  } catch (error) { status(error.message); }
});
$('backfill-start').addEventListener('click', async () => {
  if (!selectedRule) { status('Save the rule first.'); return; }
  if (!confirm(`Evaluate all previous ${$('when-source').value} evidence against saved rule ${selectedRule}? This runs in the background.`)) return;
  try {
    const result = await apiPost(`${apiRoot}/mapping-rules/${encodeURIComponent(selectedRule)}/backfill`, {});
    if (result.backfill?.id) await pollBackfill(result.backfill.id);
    else showBackfill(result.backfill);
  } catch (error) { status(error.message); }
});
$('delete-rule').addEventListener('click', async () => {
  if (!selectedRule || !confirm(`Delete mapping rule ${selectedRule}?`)) return;
  try {
    await apiDelete(`${apiRoot}/mapping-rules/${encodeURIComponent(selectedRule)}?version=${ruleVersion}`);
    await loadRules(); status('Definition removed. Existing mappings remain until separately reviewed.', 'success');
  } catch (error) { status(error.message); }
});
async function init() {
  conditionForm();
  const observed = await apiGet(`${apiRoot}/evidence-event-samples`);
  for (const source of observed.sources || []) $('source-options').append(new Option(source, source));
  for (const adapter of definitionAdapters) {
    $('definition-adapter').add(new Option(adapter.replaceAll('_', ' '), adapter));
    $('settings-adapter').add(new Option(adapter.replaceAll('_', ' '), adapter));
  }
  await selectDefinitionAdapter(); await loadRules(); await loadSettings();
  const fw = await apiGet('/api/v1/frameworks'); frameworks = fw.items;
  for (const item of frameworks) $('framework').add(new Option(item.name || item.slug, item.slug));
  await loadFrameworkControls();
}
init().catch(error => status(error.message));
