// The ISMS register page (isms.html) with records shaped as the API's ISMS
// serialisers (services/api/app/services/isms/serializers.py) send them: every
// field present, nullable columns null and optional text empty. Meta and
// summary responses carry the keys isms_meta and isms_summary send for the
// requested section. d3 runs inside the page, as the page's script tag loads it,
// and a minimal Bootstrap Tab moves the active tab and reports it as Bootstrap
// does.
import { readFile } from 'node:fs/promises';
import { page, settle } from './page-fixture.mjs';

export const d3Script = await readFile(
  new URL('../node_modules/d3/dist/d3.min.js', import.meta.url),
  'utf8',
);

const at = '2026-03-01T09:00:00';

export const user = (id, username, email = `${username}@example.test`) => ({
  id,
  username,
  email,
});

export const control = (id, ref, title = `${ref} title`) => ({
  id,
  framework: 'A',
  type: 'annex_control',
  ref,
  title,
  in_scope: true,
  justification: '',
  upstream_url: null,
});

export const clause = (id, ref, title = '') => ({
  id,
  framework: 'A',
  ref,
  title,
  parent_id: null,
});

export const objective = (id, goal, fields = {}) => ({
  id,
  requirement: `${goal} requirement`,
  goal,
  metric: '',
  completion_method: '',
  resource_requirements_text: '',
  resource_users: [],
  resource_user_ids: [],
  owner: null,
  owner_user_id: null,
  start_date: null,
  completion_target_date: null,
  evaluation_method: '',
  status: 'not_started',
  created_at: at,
  updated_at: at,
  display: goal,
  controls: [],
  clauses: [],
  control_count: 0,
  clause_count: 0,
  ...fields,
});

export const documentRecord = (id, title, fields = {}) => ({
  id,
  title,
  document_type: 'policy',
  description: '',
  external_url: null,
  folder_id: null,
  tags: [],
  content_html: '',
  content_version: 0,
  has_file: false,
  filename: null,
  content_type: null,
  size_bytes: null,
  uploaded_at: null,
  uploaded_by: null,
  created_at: at,
  updated_at: at,
  display: title,
  controls: [],
  clauses: [],
  control_count: 0,
  clause_count: 0,
  ...fields,
});

// Meta lists nodes without their people; the summary includes them and links.
export const orgNode = (id, name, fields = {}) => ({
  id,
  parent_id: null,
  parent: null,
  name,
  node_type: 'role',
  description: '',
  sort_order: 0,
  people: [],
  created_at: at,
  updated_at: at,
  ...fields,
});

export const orgRecord = (id, name, fields = {}) => ({
  ...orgNode(id, name),
  display: name,
  controls: [],
  clauses: [],
  control_count: 0,
  clause_count: 0,
  ...fields,
});

export const category = (id, name, subcategories = []) => ({
  id,
  name,
  subcategories: subcategories.map(([subId, subName]) => ({
    id: subId,
    name: subName,
    category_id: id,
  })),
  created_at: at,
  updated_at: at,
});

export const licence = (id, name, fields = {}) => ({
  id,
  name,
  description: '',
  created_at: at,
  updated_at: at,
  display: name,
  asset_count: 0,
  ...fields,
});

export const asset = (id, name, fields = {}) => ({
  id,
  asset: name,
  name,
  category: { id: 'cat1', name: 'Data' },
  category_id: 'cat1',
  category_name: 'Data',
  subcategory: { id: 'sub1', name: 'Databases', category_id: 'cat1' },
  subcategory_id: 'sub1',
  subcategory_name: 'Databases',
  license: '',
  license_id: null,
  license_name: '',
  license_entity: null,
  owner_org_node: null,
  owner_org_node_id: null,
  description: '',
  risk_count: 0,
  created_at: at,
  updated_at: at,
  display: name,
  ...fields,
});

export const assetRecord = (id, name, fields = {}) => ({
  ...asset(id, name),
  controls: [],
  clauses: [],
  control_count: 0,
  clause_count: 0,
  ...fields,
});

// An access row's service is null once its asset is deleted (ON DELETE SET NULL).
export const accessRecord = (id, taskAction, service, roles = [], fields = {}) => ({
  id,
  task_action: taskAction,
  service_asset_id: service?.id ?? null,
  service,
  roles,
  role_org_node_ids: roles.map((role) => role.id),
  notes: '',
  created_at: at,
  updated_at: at,
  display: `${taskAction} × ${service ? service.asset : 'Service'}`,
  ...fields,
});

export const metricEntry = (id, measureId, fields = {}) => ({
  id,
  measure_id: measureId,
  recorded_at: at,
  period_start: null,
  period_end: null,
  period: '',
  metric_value: null,
  metric_unit: '',
  qualitative_value: '',
  value_display: '',
  source_type: 'other',
  source_title: '',
  source_url: null,
  source_reference: '',
  source_event_id: null,
  source_event_url: null,
  notes: '',
  raw_payload: {},
  threshold_status: null,
  created_at: at,
  updated_at: at,
  display: 'Metric entry',
  ...fields,
});

export const measure = (id, summary, fields = {}) => ({
  id,
  framework: 'A',
  summary,
  description: '',
  metric_key: null,
  source_type: 'other',
  target_value: null,
  target_unit: '',
  threshold_operator: '',
  target_display: '',
  owner: null,
  owner_user_id: null,
  frequency: '',
  notes: '',
  metric_entry_count: 0,
  latest_entry: null,
  latest_threshold_status: null,
  created_at: at,
  updated_at: at,
  display: summary || 'Effectiveness measure',
  quiz: null,
  submission_secret_issued_at: null,
  metric_entries: [],
  controls: [],
  control_count: 0,
  ...fields,
});

export const meeting = (id, title, date, fields = {}) => ({
  id,
  title,
  date,
  start_time: null,
  end_time: null,
  attendees: [],
  apologies: [],
  attendee_user_ids: [],
  apology_user_ids: [],
  attendee_person_ids: [],
  apology_person_ids: [],
  links: [],
  agenda_minutes_notes: '',
  created_at: at,
  updated_at: at,
  display: title,
  controls: [],
  clauses: [],
  control_count: 0,
  clause_count: 0,
  ...fields,
});

// A meeting's link to an ISMS document carries the document without its links.
export const documentLink = (id, doc) => ({
  id,
  link_type: 'isms_document',
  document_id: doc.id,
  document: doc,
  title: doc.title,
  url: null,
});

export const externalLink = (id, url, title = '') => ({
  id,
  link_type: 'external_url',
  document_id: null,
  document: null,
  title,
  url,
});

// Which meta keys isms_meta sends for each section.
const metaSections = {
  users: ['objectives', 'org', 'assets', 'access', 'effectiveness', 'meetings'],
  org_nodes: ['org', 'assets', 'access'],
  documents: ['meetings'],
  assets: ['access'],
  asset_categories: ['assets'],
  licenses: ['assets'],
  controls: ['objectives', 'documents', 'org', 'assets', 'effectiveness', 'meetings'],
  clauses: ['objectives', 'documents', 'org', 'assets', 'meetings'],
  effectiveness_metric_sources: ['effectiveness'],
  effectiveness_metric_source_types: ['effectiveness'],
};

// The record list isms_summary sends for each section.
export const summaryKeys = {
  objectives: 'objectives',
  documents: 'documents',
  org: 'org_nodes',
  assets: 'assets',
  access: 'access_control_matrix',
  effectiveness: 'effectiveness_measures',
  meetings: 'meetings',
};

export const otherSource = { id: 'other', source: 'other', label: 'Other', color: '' };

export function metaResponse(section, lists = {}) {
  const out = {
    framework: 'A',
    section,
    can_view: true,
    can_manage: true,
    document_types: ['policy', 'process', 'procedure'],
    objective_statuses: ['defined', 'planned', 'in_progress', 'completed'],
    application_values: ['Low', 'Medium', 'High'],
    application_source_types: [],
    effectiveness_threshold_operators: ['', 'lt', 'lte', 'eq', 'gte', 'gt'],
  };
  const sources = lists.effectiveness_metric_sources || [otherSource];
  const all = {
    users: [],
    org_nodes: [],
    documents: [],
    assets: [],
    asset_categories: [],
    licenses: [],
    controls: [],
    clauses: [],
    ...lists,
    effectiveness_metric_sources: sources,
    effectiveness_metric_source_types: sources.map((src) => src.id),
  };
  for (const [key, sections] of Object.entries(metaSections))
    if (sections.includes(section)) out[key] = structuredClone(all[key]);
  return out;
}

export function summaryResponse(section, records = {}) {
  const out = {
    framework: 'A',
    section,
    can_manage: true,
    counts: Object.fromEntries(
      Object.values(summaryKeys).map((key) => [key, (records[key] || []).length]),
    ),
  };
  const key = summaryKeys[section];
  if (key) out[key] = structuredClone(records[key] || []);
  return out;
}

const tabIds = {
  overview: 'overviewTab',
  swot: 'swotTab',
  objectives: 'objectivesTab',
  documents: 'documentsTab',
  org: 'orgTab',
  assets: 'assetsTab',
  access: 'accessControlTab',
  effectiveness: 'effectivenessTab',
  meetings: 'meetingsTab',
  soa: 'soaTab',
};

// Bootstrap's Tab: show() leaves an active tab alone, asks show.bs.tab, moves
// the active class within the tab list and then reports shown.bs.tab.
export function installTabs(window) {
  window.bootstrap = {
    ...window.bootstrap,
    Tab: {
      getOrCreateInstance: (el) => ({
        show() {
          if (el.classList.contains('active')) return;
          const show = new window.Event('show.bs.tab', { bubbles: true, cancelable: true });
          if (!el.dispatchEvent(show)) return;
          for (const other of el
            .closest('[role="tablist"], .nav')
            .querySelectorAll('[data-bs-toggle="tab"]'))
            other.classList.remove('active');
          el.classList.add('active');
          el.dispatchEvent(new window.Event('shown.bs.tab', { bubbles: true }));
        },
      }),
    },
  };
}

const plain = (value) =>
  value instanceof FormData ? value : JSON.parse(JSON.stringify(value ?? null));

// Opens the register. `lists` feeds meta, `records` the summary; `get` and
// `respond` may answer (or hold) any request first; `respond` also answers
// writes, which default to echoing the body with a new id.
export async function register(
  t,
  {
    tab = '',
    query = '',
    me = { is_admin: true },
    lists = {},
    records = {},
    people = [],
    folders = [],
    get,
    respond,
    confirm = true,
    vendor,
    tabs = true,
    d3 = true,
    setup,
  } = {},
) {
  const sent = [];
  const record = (method) => async (u, body) => {
    sent.push({ method, url: u.pathname + u.search, body: plain(body) });
    const answer = await respond?.(method, u, body);
    if (answer instanceof Error) throw answer;
    return answer ?? { id: 'new1', ...(body instanceof FormData ? {} : body) };
  };
  const ui = await page(t, 'isms', {
    query: (tab ? '&tab=' + tab : '') + query,
    me,
    confirm,
    vendor,
    scripts: d3 ? [d3Script, 'window.d3 = d3;'] : [],
    setup(window) {
      if (tabs) installTabs(window);
      setup?.(window);
    },
    get: async (u) => {
      sent.push({ method: 'GET', url: u.pathname + u.search });
      const answer = await get?.(u);
      if (answer instanceof Error) throw answer;
      if (answer !== undefined) return answer;
      const section = u.searchParams.get('section');
      if (u.pathname === '/api/v1/isms/meta') return metaResponse(section, lists);
      if (u.pathname === '/api/v1/isms/summary') return summaryResponse(section, records);
      if (u.pathname === '/api/v1/isms/document-folders') return structuredClone(folders);
      if (u.pathname === '/api/v1/isms/people') return { items: structuredClone(people) };
      throw new Error('Unexpected GET ' + u);
    },
    post: record('POST'),
    patch: record('PATCH'),
    remove: record('DELETE'),
  });
  const writes = () => sent.filter((call) => call.method !== 'GET');
  const requests = (path, section) =>
    sent.filter((call) => {
      const u = new URL(call.url, 'https://keen.example');
      return (
        call.method === 'GET' &&
        u.pathname === path &&
        (section === undefined || u.searchParams.get('section') === section)
      );
    });
  const submit = (id) =>
    ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  const click = (selector) => {
    const el = ui.document.querySelector(selector);
    if (!el) throw new Error('Nothing matches ' + selector);
    el.click();
  };
  const statusIs = (text) => settle(() => ui.el('status').textContent === text);
  const openTab = (section) =>
    ui.window.bootstrap.Tab.getOrCreateInstance(ui.el(tabIds[section])).show();
  // The add button opens its form once its section has loaded.
  const add = async (kind, card) => {
    click(`[data-isms-add="${kind}"]`);
    await settle(() => ui.el(card).style.display === '');
  };
  const select = (id, ...values) => {
    for (const option of ui.el(id).options) option.selected = values.includes(option.value);
    ui.el(id).dispatchEvent(new ui.window.Event('change'));
  };
  return { ...ui, sent, writes, requests, submit, click, statusIs, openTab, add, select };
}

// A response the test releases when it chooses, as a slow network would.
export function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export { settle };
