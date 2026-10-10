import {fieldLabel, draftFields, observedPayloadFields, fieldChoiceGroups} from '/pages/evidence-fields.js';
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
  forgejo: {organizations: ['org', 'label'], users: ['user', 'label'], feeds: ['url', 'label']},
  gitea: {organizations: ['org', 'label'], users: ['user', 'label'], feeds: ['url', 'label']},
  riskledger: {organizations: ['org', 'label']},
  redmine: {projects: ['project', 'label']},
  gitlab: {groups: ['group', 'label'], users: ['user', 'label']},
  cloudwatch_logs: {queries: ['name', 'region', 'log_group', 'filter_pattern', 'event.system', 'event.action', 'event.outcome']},
  taiga: {projects: ['id', 'label']},
  bookstack: {selected_pages: ['id', 'book_id', 'book_slug', 'slug', 'name'],
    page_mappings: ['match.book_slug', 'match.slug', 'match.title', 'match.title_regex', 'confidence', 'rationale', 'map_to']},
  google_workspace: {streams: ['name', 'enabled', 'application', 'user_key', 'system']},
  webhooks: {providers: ['ui_name', 'badge_color', 'secret_header', 'secret_env']},
};
const apiRoot = '/api/v1/admin';
let settingsVersion = 0;
let draftEventFields = [];
let rules = [], ruleVersion = 0, selectedRule = null, targets = [], frameworks = [], samples = [], previewedRule = null, previewCount = 0;
let sourceConnections = [];
let collectors = [], definitionDocument = {}, definitionVersion = 0, definitionEntryKey = null;
const definitionAdapters = ['jenkins', 'loki', 'rss', 'github', 'forgejo', 'gitea', 'gitlab', 'redmine', 'riskledger', 'cloudwatch_logs', 'taiga', 'google_workspace', 'webhooks', 'bookstack'];
let catalogBooks = [], catalogPages = [], catalogChapters = [], nextBookOffset = null, nextPageOffset = null;
async function catalogRequest(kind, offset = 0, bookId = null) {
  return apiGet(`${apiRoot}/bookstack/catalog?kind=${kind}&offset=${offset}${selectedConnection() ? `&connection_id=${encodeURIComponent(selectedConnection())}` : ''}${bookId ? `&book_id=${bookId}` : ''}`);
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
  if (adapter === 'riskledger') return String(entry.org || '').trim();
  if (adapter === 'redmine') return String(entry.project || '').trim();
  const field = section === 'groups' ? 'group' : section === 'users' ? 'user' : section === 'feeds' ? 'url' : section === 'organizations' ? 'org' :
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
  $('collection-settings').hidden = section === 'source';
  $('source-scope-help').hidden = section !== 'source';
  $('manage-collections').hidden = !layouts[adapter];
  const needsCollection = layouts[adapter] && !collectors.some(item => item.adapter === adapter);
  $('source-scope-description').textContent =
    'This matches events already collected from this source, including events from any configured collection. It does not discover or start collecting from users, organisations, repositories or other remote inputs.' +
    (needsCollection ? ` No collections are configured for ${adapter}. Set up a collection first so the ingester has something to fetch.` : '');
  if(section !== 'source') $('input-options').open=true;
  if (section === 'source') {
    definitionEntryKey = null;
    $('definition-fields').replaceChildren(); $('definition-extra').value = '{}';
    const connection=sourceConnections.find(c=>c.id===selectedConnection());
    $('when-source').value = adapter==='webhooks'&&connection?.configuration.provider ? 'webhook:'+connection.configuration.provider : adapter;
    $('bookstack-picker').hidden = true;
    $('bookstack-conditions-panel').hidden = adapter !== 'bookstack';
    loadSamples().catch(error => status(error.message));
    return;
  }
  const selected = $('definition-entry').value;
  const found = collectors.find(item => item.adapter === adapter && item.section === section && inConnection(item) && item.collector === selected);
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
  if (section === 'source') { choice.replaceChildren(new Option(`Collected events from ${adapter}`, '__all__')); paintDefinitionEntry(); return; }
  choice.replaceChildren();
  for (const item of collectors.filter(item => item.adapter === adapter && item.section === section && inConnection(item)))
    choice.add(new Option(item.key, item.collector));
  paintDefinitionEntry();
}
function selectedConnection(){return $('definition-connection')?.value || '';}
function inConnection(item){return (item.connection_id || 'env:'+item.adapter) === selectedConnection();}
function paintConnectionChoices(){
 let select=$('definition-connection');
 if(!select){const label=document.createElement('label');label.className='form-label d-block';label.textContent='Connection';select=document.createElement('select');select.id='ec-definition-connection';select.className='form-select';label.append(select);$('definition-adapter').parentElement.after(label);select.onchange=()=>selectDefinitionAdapter(true).catch(error=>status(error.message));}
 const previous=select.value,adapter=$('definition-adapter').value;
 select.replaceChildren(new Option('All connections',''),new Option('Environment default','env:'+adapter));
 for(const c of sourceConnections.filter(c=>c.source===adapter))select.add(new Option(c.name,c.id));
 if([...select.options].some(option=>option.value===previous))select.value=previous;
}
async function selectDefinitionAdapter(keepConnection=false) {
  if(!keepConnection)paintConnectionChoices();
  const adapter = $('definition-adapter').value;
  const connection=sourceConnections.find(c=>c.id===selectedConnection());
  if(connection){definitionDocument=connection.inputs;definitionVersion=connection.version;}
  else if (layouts[adapter]) {
    const data = await apiGet(`${apiRoot}/managed-configurations/${adapter}`);
    definitionDocument = data.document; definitionVersion = data.version;
  } else { definitionDocument = {}; definitionVersion = 0; }
  $('definition-section').replaceChildren();
  $('definition-section').add(new Option('All collected events (any collection)', 'source'));
  for (const section of Object.keys(selectedConnection() ? layouts[adapter] || {} : {}))
    if (section !== 'page_mappings') $('definition-section').add(new Option(label(section), section));
  paintDefinitionChoices();
  if (adapter === 'bookstack') {
    try { await loadBookstackBooks(); }
    catch (error) { $('bookstack-status').textContent = error.message; }
  }
}
function definitionFromForm() {
  const adapter = $('definition-adapter').value, section = $('definition-section').value;
  if (!adapter) throw new Error('Enable an ingester before creating a mapping.');
  if (layouts[adapter] && adapter !== 'webhooks' && !collectors.some(item => item.adapter === adapter && (!selectedConnection() || inConnection(item)))) throw new Error('Add an input under Sources before creating a mapping rule.');
  if (section === 'source') return {adapter, section, entry: {}, key: '__all__', collector: null,
    entry_key: null, connector_version: 0, connection_id: selectedConnection() || null};
  if (!$('definition-entry').value) throw new Error('Add an input under Sources before creating its mapping rule.');
  const entry = definitionEntry(), key = definitionKey(adapter, section, entry);
  if (!key || key === '/') throw new Error('Give the collection item a name, URL or ID.');
  return {adapter, section, entry, key, collector: definitionId(adapter, section, key),
    entry_key: definitionEntryKey, connector_version: definitionVersion, connection_id: selectedConnection() || null};
}
let step = 1, rulePage = 0;
let enabledSources = [];
function sourceChoices(extra) {
 const select=$('definition-adapter'); const current=extra || select.value;
 select.replaceChildren(...enabledSources.map(item=>new Option(item.label,item.value)));
 if(extra && !enabledSources.some(item=>item.value===extra))select.add(new Option(extra+' (inactive / existing evidence)',extra));
 if([...select.options].some(o=>o.value===current))select.value=current;
}
const fieldTitles = {system:'Which system? (optional)',action:'What happened? (action)',outcome:'What was the result? (outcome)',actor:'Who did it? (actor)',severity:'Severity number',label:'Collection label'};
const fieldHelp = {
 system:'For example, web-prod-01. Leave empty to include every system within the selected source.',
 actor:'Copy the account from an event only when the rule should apply to that account.',
 action:'The event’s activity name. Copy an observed value such as build or package.upgrade. Typing here filters events; it does not run or rename an activity.',
 outcome:'The recorded result. Copy its exact value, such as success, failed or info. “info” means the event reports information; it makes no success/failure claim.',
 severity:'An exact number assigned by the source. A value of 4 matches severity 4 only.',
 label:'A tag supplied by some collectors. Leave empty unless the source supplies the label you need.'
};
function ruleSentence() {
 if (!$('rule-sentence')) return;
 const parts=['From '+($('definition-adapter').selectedOptions[0]?.textContent||'the selected source')];
 for (const [key,title] of [['system','on system'],['action','where the activity is'],['outcome','and the result is']]) {
  const value=$('when-'+key)?.value.trim();if(value)parts.push(title+' “'+value+'”');
 }
 const extra=[...conditions.filter(x=>!['source','system','action','outcome'].includes(x)),...regexConditions,...bookstackConditions].filter(x=>$('when-'+x)?.value.trim());
 if(extra.length)parts.push('with '+extra.length+' additional filter'+(extra.length===1?'':'s'));
 $('rule-sentence').textContent=parts.join(', ')+'. '+(targets.length?'Matching events link to '+targets.length+' selected control(s).':'Next: choose the controls this evidence supports.');
 const more=$('more-filters');if(more&&['actor','severity','label'].some(x=>$('when-'+x)?.value.trim()))more.open=true;
}
const stepTitles = ['Choose matching events', 'Name and choose controls', 'Check and publish'];
function showStep(number) {
  step = Math.max(1, Math.min(3, number));
  for (const section of document.querySelectorAll('[data-ec-step]')) section.hidden = Number(section.dataset.ecStep) !== step;
  $('step-number').textContent = step; $('step-title').textContent = stepTitles[step - 1];
  $('step-progress').style.width = `${step * 100 / 3}%`;
  $('step-progress').parentElement.setAttribute('aria-valuenow', String(step));
  $('prev-step').hidden = step === 1; $('next-step').hidden = step === 3;
  ruleSentence();
  $('editor-heading').textContent = selectedRule ? `Edit ${$('description').value || selectedRule}` : 'Create an evidence definition';
}
function showEditor(number = 1) { window.dispatchEvent(new Event('keen-show-evidence-mapping')); $('library').hidden = true; $('editor').hidden = false; showStep(number); }
function showLibrary() { pollingJobId = null; $('editor').hidden = true; $('library').hidden = false; renderRules(); }
function nextStep() {
  if (step === 2 && !$('description').value.trim()) { status('Describe what this evidence demonstrates before continuing.'); $('description').focus(); return; }
  if (step === 1) { try { definitionFromForm(); } catch (error) { status(error.message); return; } }
  if (step === 2 && !targets.length) { status('Add at least one framework control or clause before continuing.'); $('framework').focus(); return; }
  status(''); showStep(step + 1);
}

const label = field => field.replaceAll('.', ' / ').replaceAll('_', ' ');
const inputType = section => ({groups: 'group', users: 'user',queries: 'query', jobs: 'job', feeds: 'feed',
  selected_pages: 'page', organizations: 'organisation', repos: 'repository',
  projects: 'project', streams: 'stream', providers: 'provider'})[section] || label(section);
function fieldNode(name, value, kind = 'text') {
  const wrapper = document.createElement('div');
  wrapper.className = 'mb-2';
  const text = document.createElement('label');
  text.className = 'form-label';
  text.textContent = fieldTitles[name] || label(name);
  wrapper.append(text);
  const input = name === 'logql' || name === 'rationale' ? document.createElement('textarea') : document.createElement('input');
  input.className = 'form-control';
  input.dataset.field = name;
  if (kind === 'checkbox') {
    input.type = 'checkbox'; input.className = 'form-check-input'; input.checked = Boolean(value);
  } else { input.value = value ?? ''; if (kind === 'number') input.type = 'number'; }
  wrapper.append(input);
  const helpText = name === 'org' && $('definition-adapter').value === 'riskledger' ? 'Use * for the authenticated organisation, or its UUID to pin the expected API account. Imports supplier/risk snapshots; assessment answers and evidence files are unavailable through the public API.' : name === 'project' && $('definition-adapter').value === 'redmine' ? 'Project ID or identifier. Use * as the sole collection to discover all projects accessible to the API key.' : name === 'kind' && $('definition-adapter').value === 'jenkins' ?
    'Action assigned to Jenkins events from this job, such as build or deployment. Match it with the Action field below.' :
    name === 'label' && $('definition-adapter').value === 'jenkins' ?
      'System assigned to Jenkins events from this job. Match it with the System field below.' :
    name === 'logql' ? 'Loki query selecting log lines to collect. A separate match below narrows the resulting events.' :
    name === 'event.action' ? 'Activity name this collection assigns to incoming events. For example, login. The matching Activity field below can select this exact value.' :
    name === 'event.outcome' ? 'Result this collection assigns to incoming events. Use success only when the query actually selects successful events. Use info when it makes no result claim.' : '';
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
async function loadRuleRevisions() {
  const ruleId = selectedRule;
  const element = $('rule-revision');
  element.replaceChildren(new Option('No saved revisions', ''));
  $('rule-history').hidden = !ruleId;
  $('rule-restore').disabled = true;
  if (!ruleId) return;
  const data = await apiGet(`${apiRoot}/mapping-rules/${encodeURIComponent(ruleId)}/revisions`);
  if (selectedRule !== ruleId) return;
  element.replaceChildren(new Option('Select this rule’s revision', ''));
  for (const row of data.items) element.add(new Option(`#${row.revision} · ${row.at}`, String(row.version)));
  $('rule-restore').disabled = !data.items.length;
}

async function loadSamples() {
  const source = $('when-source').value.trim();
  const identity = $('definition-entry').value;
  const isCollection = collectors.some(item => item.adapter === $('definition-adapter').value && item.collector === identity);
  const filter = (isCollection ? `&collector=${encodeURIComponent(identity)}` : '') + (selectedConnection() ? `&connection_id=${encodeURIComponent(selectedConnection())}` : '');
  const result = source && source !== 'webhook:' ?
    await apiGet(`${apiRoot}/evidence-event-samples?source=${encodeURIComponent(source)}${filter}`) : {items: []};
  samples = result.items || [];
  $('sample').replaceChildren(new Option(samples.length ? 'Select an event' : 'No recent events for this source', ''));
  for (const [index, event] of samples.entries()) $('sample').add(new Option(`${event.action || '(no action)'} · ${event.outcome || '(no result)'} · ${event.summary}`, String(index)));
  for(const field of conditions.filter(x=>x!=='source'&&x!=='severity')) {
   const list=$('observed-'+field);if(!list)continue;list.replaceChildren();
   for(const value of [...new Set(samples.map(e=>e[field]).filter(v=>v!==null&&v!==undefined&&v!==''))].sort())list.append(new Option(value,value));
  }
  $('sample-details').textContent=samples.length?'Choose an event to see the exact activity and result it recorded.':'No recent events yet. Collect an event first, or use known source values and the advanced predefine option.';
  refreshFieldChoices();
  ruleSentence();
}
function textLabel(wrapper,field){wrapper.querySelector("label").htmlFor="ec-when-"+field;}
function conditionForm() {
  for (const field of conditions.filter(value => value !== 'source')) {
    const wrapper = fieldNode(field, '', field === 'severity' ? 'number' : 'text');
    wrapper.className = 'col-md-6';
    wrapper.querySelector('[data-field]').id = `ec-when-${field}`;
    const help = document.createElement('div'); help.className = 'form-text'; help.id = `ec-help-${field}`;
    help.textContent = fieldHelp[field]; wrapper.append(help);
    wrapper.querySelector('[data-field]').setAttribute('aria-describedby', help.id);
    textLabel(wrapper, field);
    const input=wrapper.querySelector('[data-field]');
    input.placeholder=field==='severity'?'Any severity':'Any '+field;
    if(field!=='severity'){input.setAttribute('list','ec-observed-'+field);const choices=document.createElement('datalist');choices.id='ec-observed-'+field;wrapper.append(choices);}
    input.addEventListener('input',ruleSentence);
    (['system','action','outcome'].includes(field)?$('conditions'):$('extra-conditions')).append(wrapper);
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
  if (!$('description').value.trim()) throw new Error('Give the mapping a name.');
  const when = {};
  const fieldConditions = readFieldConditions();
  if (fieldConditions.length) when.fields = fieldConditions;
  for (const field of [...conditions, ...regexConditions, ...bookstackConditions]) {
    const value = $(`when-${field}`).value.trim();
    if (value) when[field] = ['severity', 'bookstack_book_id', 'bookstack_page_id'].includes(field) ? Number(value) : value;
  }
  when.source = definition.section === 'source' && $('when-source').value.startsWith('webhook:') ?
    $('when-source').value : definition.adapter === 'webhooks' && definition.section !== 'source' ? `webhook:${definition.key}` : definition.adapter;
  if (definition.connection_id) when.connection_id = definition.connection_id;
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
async function newRule() {
  draftEventFields = [];
  sourceChoices();
  $('draft-notice').hidden=true;
  $('field-rows').replaceChildren();
  selectedRule = null; previewedRule = null; previewCount = 0;
  await loadRuleRevisions();
  $('input-picker').hidden = false; $('selected-input').hidden = true;
  $('apply-existing').checked = true; $('predefine').checked = false;
  $('rule-id').value = ''; $('rule-id').disabled = false;
  $('description').value = ''; $('confidence').value = '.8'; $('enabled').checked = true;
  for (const field of [...conditions, ...regexConditions, ...bookstackConditions]) $(`when-${field}`).value = '';
  targets = []; renderTargets(); $('preview-results').textContent = ''; $('backfill').replaceChildren();
  $('sample').replaceChildren(new Option('Choose a source to see recent events', ''));
  $('sample-details').textContent = 'Choose a recent event to inspect its fields. You can also define a rule before evidence arrives.';
  await selectDefinitionAdapter();
  showEditor(1);
}
function showSelectedInput() {
  const adapter = $('definition-adapter').value, section = $('definition-section').value;
  const collector = collectors.find(item => item.collector === $('definition-entry').value && item.adapter === adapter && inConnection(item));
  const connectionLabel=sourceConnections.find(c=>c.id===selectedConnection())?.name || (selectedConnection() ? 'Environment default' : 'all connections');
  $('selected-input').textContent = section === 'source' ?
    `Selected input: All ${$('when-source').value || adapter} events from ${connectionLabel}. Choose another definition from the library to change inputs.` :
    `Selected input: ${adapter} ${inputType(section)} “${collector?.key || $('definition-entry').selectedOptions[0]?.textContent || ''}” from ${connectionLabel}. Choose another definition from the library to change inputs.`;
  $('input-picker').hidden = true; $('selected-input').hidden = false;
}
async function editRule(rule) {
  await newRule(); selectedRule = rule.id;
  for(const item of rule.when?.fields || []) addFieldCondition(item);
  $('rule-id').value = rule.id; $('rule-id').disabled = true;
  $('description').value = rule.description || ''; $('confidence').value = rule.confidence ?? .8;
  $('enabled').checked = rule.enabled !== false;
  for (const field of [...conditions, ...regexConditions, ...bookstackConditions]) $(`when-${field}`).value = rule.when?.[field] ?? '';
  targets = structuredClone(rule.map_to || []); renderTargets();
  const linked = collectors.find(item => item.collector === rule.when?.collector && (item.connection_id || "env:"+item.adapter) === (rule.when?.connection_id || "env:"+item.adapter));
  if (linked) {
    sourceChoices(linked.adapter); $('definition-adapter').value = linked.adapter;
    await selectDefinitionAdapter();$('definition-connection').value=rule.when?.connection_id||'';await selectDefinitionAdapter(true);
    $('definition-section').value = linked.section;
    paintDefinitionChoices(); $('definition-entry').value = linked.collector; paintDefinitionEntry();
  } else {
    const source = rule.when?.source || 'webhook';
    const adapter = source.startsWith('webhook:') ? 'webhooks' : source;
    if (!$('definition-adapter').querySelector(`option[value="${CSS.escape(adapter)}"]`))
      $('definition-adapter').add(new Option(adapter.replaceAll('_', ' '), adapter));
    $('definition-adapter').value = adapter;
    await selectDefinitionAdapter();$('definition-connection').value=rule.when?.connection_id||'';await selectDefinitionAdapter(true); $('definition-section').value = 'source'; paintDefinitionChoices();
    $('when-source').value = source;
  }
  showSelectedInput();
  await loadRuleRevisions();
  loadRecentBackfill(rule.id).catch(error => status(error.message));
  showStep(1);
}
function renderRules() {
  const list = $('rules'); list.replaceChildren();
  const query = $('rule-filter').value.trim().toLowerCase();

  const entries = [
    ...rules.map(rule => ({rule, origin: collectors.find(item => item.collector === rule.when?.collector && (item.connection_id || "env:"+item.adapter) === (rule.when?.connection_id || "env:"+item.adapter))})),

  ];
  const chosenSource = $('source-filter').value;
  const filtered = entries.filter(({rule, origin}) => (!chosenSource || (rule?.when?.source || origin?.adapter) === chosenSource) && (!query ||
    `${rule?.id || ''} ${rule?.description || ''} ${rule?.when?.source || ''} ${JSON.stringify(rule?.map_to || [])} ${origin?.adapter || ''} ${origin?.key || ''}`.toLowerCase().includes(query)));
  rulePage=Math.min(rulePage,Math.max(0,Math.ceil(filtered.length/20)-1));
  const visibleRules=(rulePage+1)*20;
  $('previous-rules').hidden=rulePage===0;
  $('rule-count').textContent = `${filtered.length} of ${entries.length} mapping rules · page ${rulePage+1} of ${Math.max(1,Math.ceil(filtered.length/20))}`;
  $('more-rules').hidden = filtered.length <= visibleRules;
  if (!filtered.length) { const empty = document.createElement('p'); empty.className = 'small-muted'; empty.textContent = query ? 'No evidence definitions match that search.' : 'No evidence definitions yet. Create one to get started.'; list.append(empty); }
  for (const {rule, origin} of filtered.slice(rulePage*20, visibleRules)) {
    const btn = document.createElement('button'); btn.type = 'button';
    btn.className = 'list-group-item list-group-item-action text-start';
    const title = document.createElement('strong'); title.textContent = rule?.description || rule?.id || `${origin.key} · needs framework mappings`;
    const meta = document.createElement('div'); meta.className = 'small-muted';
    meta.textContent = rule ? `${rule.enabled === false ? 'Paused · ' : ''}${origin ?
      `${origin.adapter} ${inputType(origin.section)}: ${origin.key}` :
      `All ${rule.when?.source || 'source'} events`} · ${rule.map_to?.length || 0} framework targets` :
      `${origin.adapter} ${inputType(origin.section)}: ${origin.key} · add framework targets`;
    if(rule?.when?.connection_id)meta.textContent += " · "+(sourceConnections.find(c=>c.id===rule.when.connection_id)?.name || rule.when.connection_id);
    btn.append(title, meta);
    btn.addEventListener('click', async () => {
      if (rule) { editRule(rule).catch(error => status(error.message)); return; }
      await newRule(); $('definition-adapter').value = origin.adapter;
      selectDefinitionAdapter().then(() => {
        $('definition-section').value = origin.section; paintDefinitionChoices();
        $('definition-entry').value = origin.collector; paintDefinitionEntry();
        showSelectedInput();
      }).catch(error => status(error.message));
    });
    const row = document.createElement('div'); row.className = 'd-flex gap-2 align-items-center mb-2';
    btn.classList.add('flex-grow-1'); row.append(btn);
    if (rule) {
      const remove = document.createElement('button'); remove.type='button'; remove.className='btn btn-outline-danger btn-sm'; remove.textContent='Delete definition';
      remove.addEventListener('click', async () => {
        if (!confirm(`Delete evidence definition ${rule.description || rule.id}? Collection and existing evidence are retained.`)) return;
        try { await apiDelete(`${apiRoot}/mapping-rules/${encodeURIComponent(rule.id)}?version=${ruleVersion}`); await loadRules(); status('Evidence definition deleted. Collection and existing evidence retained.', 'success'); }
        catch(error) { status(error.message); }
      }); row.append(remove);
    }
    if (origin && !origin.connection_id) {
      const remove = document.createElement('button'); remove.type='button'; remove.className='btn btn-outline-danger btn-sm'; remove.textContent='Remove collection';
      remove.addEventListener('click', () => removeCollection(origin)); row.append(remove);
    }
    row.classList.add('flex-wrap'); list.append(row);
  }
}
async function removeCollection(origin) {
  const linked = rules.filter(rule => rule.when?.collector === origin.collector);
  if (linked.length) { status(`Delete the ${linked.length} linked evidence definition(s) first. Existing evidence will be retained.`); return; }
  if (!confirm(`Stop collecting ${origin.key}? Existing evidence is retained.`)) return;
  try {
    const url = `${apiRoot}/managed-configurations/${encodeURIComponent(origin.adapter)}`;
    const config = await apiGet(url);
    const document = structuredClone(config.document);
    const section = document[origin.section];
    if (origin.section === 'providers') {
      if (!Object.hasOwn(section || {}, origin.key)) throw new Error('Collection changed. Reload before removing it.');
      delete section[origin.key];
    } else {
      if (!Array.isArray(section)) throw new Error('This collection type cannot be removed here.');
      const remaining = section.filter(entry => definitionKey(origin.adapter, origin.section, entry) !== String(origin.key));
      if (remaining.length !== section.length - 1) throw new Error('Expected exactly one collection. Reload before removing it.');
      document[origin.section] = remaining;
    }
    await apiPut(url, {version: config.version, document});
    await loadRules(); status('Collection removed. Existing evidence retained.', 'success');
  } catch(error) { status(error.message); }
}
function renderTargets() {
  ruleSentence();
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
  sourceConnections=(await apiGet(`${apiRoot}/source-connections`)).connections;
  rules = data.rules; collectors = data.collectors; ruleVersion = data.rules_version;
  const selectedSource = $('source-filter').value;
  $('source-filter').replaceChildren(new Option('All sources', ''));
  for (const source of [...new Set([...rules.map(r=>r.when?.source), ...collectors.map(c=>c.adapter)])].filter(Boolean).sort()) $('source-filter').add(new Option(source,source));
  $('source-filter').value = selectedSource;
  renderRules(); await newRule(); showLibrary();
  if (data.warnings?.length) status(data.warnings.join(' · '), 'warning');

}
async function loadFrameworkControls() {
  const data = await apiGet(`/api/v1/controls?framework=${encodeURIComponent($('framework').value)}&limit=5000`);
  $('control').replaceChildren(new Option('Choose a control or clause', ''));
  for (const control of data.items) $('control').add(new Option(`${control.ref} — ${control.title || control.type}`, control.ref));
}
$('framework').addEventListener('change', () => loadFrameworkControls().catch(error => status(error.message)));
async function restore(selectedRevision) {
  const ruleId = selectedRule;
  if (!ruleId || !selectedRevision || !confirm('Restore this rule to the selected revision? Other rules and collection settings are unchanged.')) return;
  await apiPost(`${apiRoot}/mapping-rules/${encodeURIComponent(ruleId)}/revisions/${encodeURIComponent(selectedRevision)}/restore`, {version: ruleVersion});
  await loadRules();
  const restored = rules.find(rule => rule.id === ruleId);
  if (restored) await editRule(restored);
  status('Restored this rule. New evidence uses the restored settings; remap existing evidence separately.', 'success');
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
    ['system', 'action', 'outcome', 'actor', 'severity'].map(field => `${fieldTitles[field]}: ${sample[field] ?? '(not supplied)'}`).join(' · ') :
    'Choose a recent event to inspect its fields.';
  refreshFieldChoices();
});
$('use-sample').addEventListener('click', () => {
  if (!$('sample').value) return;
  const sample = samples[Number($('sample').value)];
  if (!sample) return;
  $('sample').dispatchEvent(new Event('change'));
  // System, action and outcome usually identify the event type. Actor and
  // severity vary more often, so leave those for the administrator to choose.
  for (const field of ['system', 'action', 'outcome']) $(`when-${field}`).value = sample[field] ?? '';
  ruleSentence();
  if (!selectedRule && !$('rule-id').value) {
    $('rule-id').value = [sample.source, sample.system, sample.action, sample.outcome]
      .filter(Boolean).join('_').toLowerCase().replace(/[^a-z0-9_-]+/g, '_').slice(0, 96);
  }
  $('preview-results').textContent = 'Review the filled fields, select framework targets, then preview matches.';
  previewedRule = null;
});
$('export-rules').addEventListener('click', async () => {
  const button = $('export-rules'); button.disabled = true;
  try {
    const source = $('source-filter').value;
    const response = await fetch(`${apiRoot}/mapping-rules/export.yaml?source=${encodeURIComponent(source)}`, {credentials:'same-origin'});
    if (!response.ok) throw new Error('Could not export mapping rules. Check your session and permissions.');
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href=url;
    link.download=`keen-rules-${source.replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,80)||'all'}.yaml`;
    document.body.append(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  } catch(error) {status(error.message);}
  finally {button.disabled=false;}
});
$('rule-filter').addEventListener('input', () => { rulePage = 0; renderRules(); });
$('more-rules').addEventListener('click', () => { rulePage++; renderRules(); });
$('previous-rules').onclick=()=>{rulePage=Math.max(0,rulePage-1);renderRules();};
$('close-editor').addEventListener('click', showLibrary);
$('prev-step').addEventListener('click', () => showStep(step - 1));
$('next-step').addEventListener('click', nextStep);
$('new-rule').addEventListener('click', ()=>newRule().catch(error=>status(error.message)));
function addSelectedTarget() {
  const framework = $('framework').value, ref = $('control').value;
  if (!framework || !ref) return;
  if (!targets.some(t => t.framework === framework && t.ref === ref)) targets.push({framework, ref});
  renderTargets();
}
$('add-target').addEventListener('click', addSelectedTarget);
$('suggest-controls').addEventListener('click', async () => {
  const framework = $('framework').value;
  const description = $('description').value.trim();
  const sample = samples[Number($('sample').value)];
  const list = $('control-suggestions');
  list.replaceChildren();
  if (!framework || !description) { status('Choose a framework and describe the evidence first.', 'warning'); return; }
  try {
    const result = await apiPost(`${apiRoot}/control-suggestions`, {framework, description, sample_summary:String(sample?.summary || '').slice(0, 4000)});
    if (!result.items.length) { list.textContent = 'No clear matches in this framework. Search the control list or try a more specific description.'; return; }
    for (const suggestion of result.items) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'list-group-item list-group-item-action text-start';
      const title = document.createElement('strong'); title.textContent = `${suggestion.ref} · ${suggestion.title || 'Control'}`;
      const reason = document.createElement('div'); reason.className = 'small-muted';
      reason.textContent = `${suggestion.reason || `Matched: ${(suggestion.matched_terms || []).join(', ')}.`} Click to add; review suitability before publishing.`;
      button.append(title, reason);
      button.addEventListener('click', () => {
        if (!targets.some(t => t.framework === framework && t.ref === suggestion.ref)) {
          targets.push({framework, ref:suggestion.ref}); renderTargets();
        }
        button.disabled = true; button.classList.add('opacity-50');
      });
      list.append(button);
    }
  } catch (error) { status(error.message); }
});
$('preview').addEventListener('click', async () => {
  try {
    const rule = readRule();
    const data = await apiPost(`${apiRoot}/mapping-rules/preview`, {version: ruleVersion, rule});
    previewedRule = JSON.stringify(rule); previewCount = data.examined;
    const container = $('preview-results'); container.replaceChildren();
    const heading = document.createElement('p'); heading.textContent = `${data.matched} of ${data.examined} recent ${rule.when.source} events matched. ${data.note || ''}`;
    container.append(heading);
    for (const item of data.examples) {
      const p = document.createElement('p'); p.className = 'alert alert-success py-2 mb-2'; p.textContent = `✓ Matches: ${item.summary} → ${Object.entries(item.targets).map(([fw, refs]) => `${fw}: ${refs.join(', ')}`).join('; ')}`;
      container.append(p);
    }
    for (const item of data.nonmatches || []) {
      const p = document.createElement('p'); p.className = 'alert alert-warning py-2 mb-2'; p.textContent = `— Does not match: ${item.summary}. ${(item.reasons||[]).join(' ') || 'Review the additional filters.'}`;
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
  message.textContent = `Historical evidence: ${data.status}. Checked ${data.examined || 0} of ${data.total_estimate ? `about ${data.total_estimate}` : 'an estimated total being calculated'} events; ${data.matched || 0} matched, ${data.created_mappings || 0} new mappings.${data.status === 'completed' ? ' Automatic mappings have been reconciled with the saved rule.' : ''}${data.error ? ` Error: ${data.error}` : ''}`;
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
      entry: definition.entry, connection_id: definition.connection_id, rule, connector_version: definition.connector_version,
      rules_version: ruleVersion, apply_existing: $('apply-existing').checked});
    await loadRules(); await editRule(rules.find(item => item.id === rule.id));
    showStep(3);
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
  if (!selectedRule) { status('This item has no saved mapping. Use Remove collection in the library to stop collecting it.'); return; }
  if (!confirm(`Delete evidence definition ${selectedRule}? Collected evidence and existing mappings are retained.`)) return;
  try {
    await apiDelete(`${apiRoot}/mapping-rules/${encodeURIComponent(selectedRule)}?version=${ruleVersion}`);
    await loadRules(); status('Definition removed. Existing evidence and mappings are retained.', 'success');
  } catch (error) { status(error.message); }
});
async function init() {
  conditionForm();
  const observed = await apiGet(`${apiRoot}/evidence-event-samples`);
  for (const source of observed.sources || []) $('source-options').append(new Option(source, source));
  const catalogue = await apiGet(`${apiRoot}/source-catalogue`);
  enabledSources=(catalogue.items||[]).filter(item=>item.enabled && item.mapping_source !== false).map(item=>({value:item.adapter,label:item.adapter==='keen-agent'?'KEEN Agent / OTLP logs':item.adapter.replaceAll('_',' ')}));
  for (const adapter of definitionAdapters) $('settings-adapter').add(new Option(adapter.replaceAll('_',' '),adapter));
  const integrations = await apiGet('/api/v1/admin/integrations');
  for(const collector of integrations.collectors||[])if(collector.enabled && collector.live_revision)enabledSources.push({value:'integration:'+collector.id,label:collector.name});
  sourceChoices();
  await selectDefinitionAdapter(); await loadRules(); await loadSettings();
  const fw = await apiGet('/api/v1/frameworks'); frameworks = fw.items;
  for (const item of frameworks) $('framework').add(new Option(item.name || item.slug, item.slug));
  await loadFrameworkControls();
  await draftFromEvent();
}
init().catch(error => status(error.message));

// Generic integrations are source-wide inputs; rules retain the existing mapping workflow.
window.addEventListener('keen-integration-rule', async event => {
  try {
    const {source,name} = event.detail;
    if (![...$('definition-adapter').options].some(o => o.value === source))
      $('definition-adapter').add(new Option(name, source));
    await newRule(); sourceChoices(source); $('definition-adapter').value = source;
    await selectDefinitionAdapter(); $('description').value = name;
  } catch(error) { status(error.message); }
});

$('source-filter').addEventListener('change', ()=>{rulePage=0;renderRules();});
window.addEventListener('keen-filter-source', async event => {
  await loadRules();
  const source=event.detail.source;
  if (![...$('source-filter').options].some(o=>o.value===source)) $('source-filter').add(new Option(source,source));
  $('browse').open=true; $('source-filter').value=source; $('rule-filter').value=''; renderRules();
});
window.addEventListener('keen-collection-create', async event => {
  await loadRules();await newRule();$('definition-adapter').value=event.detail.source;
  await selectDefinitionAdapter();
  if(event.detail.connection_id){$('definition-connection').value=event.detail.connection_id;await selectDefinitionAdapter(true);}
  const input=event.detail.input;
  if(input?.section){$('definition-section').value=input.section;paintDefinitionChoices();$('definition-entry').value=input.collector;paintDefinitionEntry();}
  $('input-options').open=true;
});
window.addEventListener('keen-connections-changed', () => loadRules().catch(e=>status(e.message)));

function currentEventFields(){
 const index=$('sample').value;
 return index!==''&&samples[Number(index)] ? samples[Number(index)].fields||[] : draftEventFields;
}
function availableEventFields(){return [...draftEventFields,...samples.flatMap(sample=>sample.fields||[])];}
function populateFieldChoices(select,selectedPath=null){
 select.replaceChildren(new Option('Choose an event field…',''));
 for(const group of fieldChoiceGroups(currentEventFields(),availableEventFields(),selectedPath)){
  const optgroup=document.createElement('optgroup');optgroup.label=group.label;
  for(const item of group.items)optgroup.append(new Option(item.label,JSON.stringify(item.path)));
  select.append(optgroup);
 }
 select.value=selectedPath?JSON.stringify(selectedPath):'';
}
function refreshFieldChoices(){
 const list=$('field-paths');list.replaceChildren();const paths=new Set();
 for(const item of availableEventFields()){const path=JSON.stringify(item.path);if(paths.has(path))continue;paths.add(path);list.append(new Option(item.path.join(' › '),path));}
 // Regroup rows as the inspected sample changes, preserving all conditions.
 for(const row of $('field-rows').children){
  const select=row.fieldInputs.path;
  populateFieldChoices(select,select.value?JSON.parse(select.value):null);
 }
}
function addFieldCondition(item={}){
 const row=document.createElement('div');row.className='d-flex flex-wrap gap-2 align-items-start';
 const path=document.createElement('select');path.className='form-select';path.style.flex='2 1 260px';path.style.width='auto';path.setAttribute('aria-label','Event field');path.add(new Option('Choose an event field…',''));
 populateFieldChoices(path,item.path||null);
 const op=document.createElement('select');op.className='form-select w-auto';op.setAttribute('aria-label','Field operator');for(const [value,label] of [['equals','equals'],['starts_with','starts with'],['contains','contains'],['exists','exists'],['in_cidr','is in IP network (CIDR)']])op.add(new Option(label,value));op.value=item.operator||'equals';
 const value=document.createElement('input');value.className='form-control';value.style.flex='2 1 200px';value.style.width='auto';value.setAttribute('aria-label','Field value');value.placeholder='Match value';value.value=item.value??'';
 const remove=document.createElement('button');remove.type='button';remove.className='btn btn-outline-danger';remove.textContent='Remove';remove.onclick=()=>row.remove();
 op.onchange=()=>{value.hidden=op.value==='exists';};op.onchange();
 path.onchange=()=>{if(value.value)return;const observed=currentEventFields().find(x=>JSON.stringify(x.path)===path.value);if(observed)value.value=observed.value;};
 row.append(path,op,value,remove);row.fieldInputs={path,op,value};$('field-rows').append(row);
}
function readFieldConditions(){return [...$('field-rows').children].map(row=>{const {path,op,value}=row.fieldInputs;let keys;try{keys=JSON.parse(path.value);}catch{throw new Error('Choose an event field.');}if(!Array.isArray(keys)||!keys.length||keys.some(k=>typeof k!=='string'||!k))throw new Error('Field paths must be arrays of non-empty keys.');return {path:keys,operator:op.value,...(op.value==='exists'?{}:{value:value.value})};});}
$('field-add').onclick=()=>addFieldCondition();

async function draftFromEvent(){
 const params=new URLSearchParams(location.search), id=params.get('from_event');if(!id)return;
 if(!/^[0-9a-f-]{36}$/i.test(id))throw new Error('Invalid event identifier for mapping draft.');
 const framework=params.get('framework');
 const event=await apiGet('/api/v1/events/'+encodeURIComponent(id)+(framework?'?framework='+encodeURIComponent(framework):''));
 await newRule();
 if(![...$('definition-adapter').options].some(o=>o.value===event.source))$('definition-adapter').add(new Option(event.source,event.source));
 $('definition-adapter').value=event.source;await selectDefinitionAdapter();
 $('description').value='Evidence: '+(event.action||event.source);
 for(const key of ['source','system','actor','action','outcome','severity'])if(event[key]!==null&&event[key]!==undefined)$('when-'+key).value=String(event[key]);
 draftEventFields=observedPayloadFields(event.normalized_payload);
 refreshFieldChoices();
 const chosen=draftFields(event.normalized_payload);
 for(const condition of chosen)addFieldCondition(condition);
 document.getElementById('hub-panel')?.removeAttribute('open');
 $('sample-details').textContent='Draft from event '+id+'. Review every condition: clear System and remove Host for multiple servers; remove Client IP for multiple addresses. Transient identifiers and timestamps are omitted. Select framework targets before saving.';
 const notice=$('draft-notice');notice.hidden=false;notice.textContent=$('sample-details').textContent;
 showEditor(1);ruleSentence();
 // Reloading the editor must not unexpectedly recreate the draft.
 params.delete('from_event');history.replaceState(null,'',location.pathname+'?'+params.toString()+'#evidence-config');
}

$('description').addEventListener('input',()=>{ $('control-suggestions').replaceChildren(); });

$('manage-collections').addEventListener('click', () => {
  window.dispatchEvent(new CustomEvent('keen-manage-source', {detail: {source: $('definition-adapter').value}}));
});
