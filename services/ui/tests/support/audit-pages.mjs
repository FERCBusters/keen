// Shared fixtures for the audit detail page tests: an audit as the API sends
// it, its catalogue, and a date field that behaves like the design system's.
import { readFile } from 'node:fs/promises';
import { page, settle } from './page-fixture.mjs';

export const d3 = await readFile(
  new URL('../node_modules/d3/dist/d3.min.js', import.meta.url),
  'utf8',
);
// Bodies are created in the page realm; compare them as plain JSON values.
export const plain = (v) => (v instanceof FormData ? v : JSON.parse(JSON.stringify(v)));
export const HOSTILE = '<img src=x onerror=alert(1)>';
const NodeFilter = { SHOW_TEXT: 4, FILTER_ACCEPT: 1, FILTER_REJECT: 2, FILTER_SKIP: 3 };

// An audit as GET /api/v1/audits/{id} returns it (services/audits/queries.py
// get_audit): the summary counts, every list and nullable fields as null.
export function apiAudit(overrides = {}) {
  return {
    id: 'a1',
    title: 'Annual audit',
    framework_slug: 'A',
    status: 'open',
    audit_type: 'internal',
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    schedule_recurrence: 'once',
    schedule_interval: 1,
    schedule_date_rule: 'exact',
    schedule_anchor_month: null,
    schedule_next_run_date: null,
    schedule_until_date: null,
    schedule_last_run_date: null,
    created_by_user_id: 'u1',
    created_by_username: 'alice',
    created_at: '2026-01-01T09:00:00',
    updated_at: '2026-01-05T10:30:00',
    notes: '',
    report_notes: '',
    executive_summary: '',
    has_final_report: false,
    final_report: null,
    evidence_count: 0,
    scope_count: 0,
    swot_scope_count: 0,
    control_scope_count: 0,
    clause_scope_count: 0,
    document_scope_count: 0,
    attendee_count: 0,
    finding_count: 0,
    scoped_controls: [],
    scoped_clauses: [],
    scoped_swot: [],
    scoped_documents: [],
    evidence: [],
    attendees: [],
    findings: [],
    ...overrides,
  };
}

export const finalReport = {
  filename: 'report.pdf',
  content_type: 'application/pdf',
  sha256: 'abc',
  size_bytes: 5,
  uploaded_at: '2026-02-01T00:00:00',
  download_url: '/api/v1/audits/a1/report',
};

// Sampled event evidence as evidence_dict serialises it.
export function eventEvidence(id, eventId, overrides = {}) {
  return {
    id,
    audit_id: 'a1',
    event_id: eventId,
    entity_type: null,
    entity_id: null,
    evidence_url: null,
    title: `Event ${eventId}`,
    notes: '',
    added_by_user_id: 'u1',
    added_by_username: 'alice',
    added_at: '2026-01-03T00:00:00',
    event: {
      id: eventId,
      timestamp: '2026-01-02T00:00:00',
      source: 'auth',
      summary: `Event ${eventId}`,
      controls: [],
    },
    entity: null,
    controls: [],
    ...overrides,
  };
}

// The design system's wireIsoDateTextField: a hidden ISO date and a text
// field showing it as dd/mm/yyyy, kept in step on change. Text it cannot read
// is marked invalid until the field next shows its date.
export function wireIsoDateTextField(hidden, text) {
  if (!hidden || !text) return null;
  const Event = hidden.ownerDocument.defaultView.Event;
  const show = (iso) => (/^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso.split('-').reverse().join('/') : '');
  const parse = (raw) =>
    /^\d{2}\/\d{2}\/\d{4}$/.test(raw)
      ? raw.split('/').reverse().join('-')
      : /^\d{4}-\d{2}-\d{2}$/.test(raw)
        ? raw
        : '';
  const syncTextFromHidden = () => {
    text.value = show(hidden.value || '');
    text.classList.remove('is-invalid');
  };
  const syncHiddenFromText = () => {
    const raw = String(text.value || '').trim();
    if (!raw) {
      hidden.value = '';
      text.classList.remove('is-invalid');
      return true;
    }
    const iso = parse(raw);
    if (!iso) {
      text.classList.add('is-invalid');
      return false;
    }
    hidden.value = iso;
    text.value = show(iso);
    text.classList.remove('is-invalid');
    return true;
  };
  hidden.addEventListener('change', syncTextFromHidden);
  text.addEventListener('change', () => {
    if (syncHiddenFromText()) hidden.dispatchEvent(new Event('change', { bubbles: true }));
  });
  syncTextFromHidden();
  return { syncTextFromHidden, syncHiddenFromText };
}

const defaultControls = [
  { id: 'c1', ref: 'A1', title: 'Access', type: 'annex_control' },
  { id: 'c2', ref: 'A2', title: 'Backup', type: 'annex_control' },
];
const defaultClauses = [
  { id: 'cl4', ref: '4', title: 'Context', parent_id: null },
  { id: 'cl41', ref: '4.1', title: 'Organisation', parent_id: 'cl4' },
  { id: 'cl42', ref: '4.2', title: 'Interested parties', parent_id: 'cl4' },
];

// The audit page with every request answered from `options`. A response may
// be a function of the request URL, which can also wait or throw.
export async function auditPage(t, options = {}) {
  const state = {
    audit: options.audit || apiAudit(),
    patched: [],
    posted: [],
    put: null,
    removed: [],
    gets: [],
  };
  const answer = async (value, u) => (typeof value === 'function' ? value(u, state) : value);
  const responses = {
    '/api/v1/audits/users': { items: [{ id: 'u2', username: 'zed', email: 'zed@example.test' }] },
    '/api/v1/isms/people': { items: [] },
    '/api/v1/controls': { items: defaultControls },
    '/api/v1/clauses': { items: defaultClauses },
    '/api/v1/graph/clause_control': {
      nodes: [],
      links: [{ source: 'clause:cl41', target: 'control:c1' }],
    },
    '/api/v1/isms/documents': { items: [] },
    '/api/v1/isms/swot': { items: [] },
    '/api/v1/audits/a1/scope-map': { clauses: [], controls: [], links: [], summary: {} },
    ...(options.responses || {}),
  };
  const ui = await page(t, 'audit', {
    query: options.query ?? '&id=a1',
    scripts: [d3],
    me: options.me || { is_admin: true, user_id: 'u1' },
    globals: { NodeFilter, ...(options.globals || {}) },
    confirm: options.confirm,
    setup: options.setup,
    vendor: { wireIsoDateTextField, ...(options.vendor || {}) },
    get: async (u) => {
      state.gets.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/audits/a1') return structuredClone(await answer(state.audit, u));
      if (u.pathname.startsWith('/api/v1/events/') && options.events) {
        const id = decodeURIComponent(u.pathname.split('/').pop());
        if (id in options.events) return answer(options.events[id], u);
      }
      if (u.pathname in responses) return answer(responses[u.pathname], u);
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Patch rejected');
      return { ok: true };
    },
    put: async (u, b) => {
      state.put = { path: u.pathname, body: plain(b) };
      return { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.post) return options.post(u, b, state);
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.remove) return options.remove(u, state);
      return { ok: true };
    },
  });
  return { ui, state };
}

// Mutations reload the audit; wait for `times` more loads of it, and their
// scope maps, to finish before the window closes.
export async function reloaded(ui, times = 1) {
  const count = (path) =>
    ui.calls.filter((c) => c.method === 'GET' && c.url.split('?')[0] === path).length;
  await settle(
    () =>
      count('/api/v1/audits/a1') === 1 + times &&
      count('/api/v1/audits/a1/scope-map') === 1 + times &&
      ui.idle(),
  );
}

export const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

export const change = (ui, el, props = {}) => {
  Object.assign(el, props);
  el.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
};
