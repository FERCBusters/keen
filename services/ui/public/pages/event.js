import { featureEnabled } from '/pages/feature-availability.js';
import { canManageStories, mountStoryRelated } from '/pages/story-related.js';
import { createEvidenceExporter } from './evidence-export/index.js';
import { renderJson } from '/json-highlight.js';
import { mountQuestionThreads } from '/pages/question-threads.js';
import {
  errorText,
  initNavbar,
  canSampleIntoAudit,
  canEditAuditForUser,
  apiGet,
  apiPatch,
  apiPost,
  apiDelete,
  confirmAction,
  esc,
  fmtTs,
  toast,
  qs,
  getSourceMeta,
  sourceBadgeHtml,
  sourceLabel,
  safeExternalHref,
  safeAbsoluteHttpHref,
  getCurrentFramework,
  withFramework,
  isAuditActive,
  preselectSamplingAudit,
  rememberSamplingAudit,
} from '/app.js';

const PRINT_MODE = (() => {
  const v = String(qs('print') || '').toLowerCase();
  return v === '1' || v === 'true';
})();

let me;
if (PRINT_MODE) {
  document.body.classList.add('print-view');
  // Load /me but avoid rendering the fixed navbar in the print-only window.
  me = await apiGet('/api/v1/me');
  window.keenMe = me;
  window.keenDefaultTimezone = me?.default_timezone || 'Etc/UTC';
  window.keenPreferences = me?.preferences || {};
  window.keenAvailableThemes = me?.available_themes || null;
} else {
  me = await initNavbar();
}

const id = qs('id');
const status = document.getElementById('status');
let pageFramework = qs('framework', getCurrentFramework());

let sourceMeta = {};
const exporter = createEvidenceExporter({
  preferences: me,
  getSourceMeta: () => sourceMeta,
  status,
});

// ------------------------------------------------------------------
// Evidence file naming (PDF/ZIP)
// ------------------------------------------------------------------
function pretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

function setText(id, val) {
  const el = document.getElementById(id);
  el.textContent = val ?? '-';
}

let currentEvent = null;

// ------------------------------------------------------------------
// Tabs: the evidence snapshot, then stories, audits, incidents and questions.
// A tab other than the snapshot appears when it has something to show.
// ------------------------------------------------------------------

const EVENT_TABS = {
  snapshot: 'eventSnapshotTab',
  stories: 'eventStoriesTab',
  audits: 'eventAuditsTab',
  incidents: 'eventIncidentsTab',
  questions: 'eventQuestionsTab',
};

function setEventTab(name, visible, count = 0) {
  const tab = document.getElementById(EVENT_TABS[name]);
  const item = tab?.closest('li');
  if (item) item.hidden = !visible;
  const badge = document.getElementById(
    `event${name.charAt(0).toUpperCase()}${name.slice(1)}Count`,
  );
  if (badge) {
    badge.textContent = count ? String(count) : '';
    badge.hidden = !count;
  }
}

function ruleSettingsHref(ruleId) {
  return `/admin.html?rule=${encodeURIComponent(ruleId)}#evidence-config`;
}

// A control listed through a crosswalk: the event was mapped to a control in
// another framework that cross-references this one. That control, and the rule
// that mapped the event to it, explain why the event counts here.
function inheritedRationaleHtml(c) {
  const from = c.inherited_from || {};
  const label = [from.framework, from.ref].filter(Boolean).join(' ');
  const links = !PRINT_MODE;
  let source = "Another framework's control";
  if (links && from.control_id) {
    const href = withFramework(
      `/control.html?id=${encodeURIComponent(from.control_id)}`,
      from.framework || pageFramework,
    );
    source = `<a href="${esc(href)}" title="${esc(label)}">${source}</a>`;
  } else if (label) {
    // Paper has no links, so name the control.
    source += ` (${esc(label)})`;
  }
  const mapped =
    links && me?.is_admin && from.rule_id
      ? `<a href="${ruleSettingsHref(from.rule_id)}">mapped</a>`
      : 'mapped';
  const note = String(c.rationale || '').trim();
  return `${source} cross-referenced this control when the event was ${mapped}.${
    note ? `<div class="small-muted">${esc(note)}</div>` : ''
  }`;
}

function showEventTab(name) {
  const id = Object.hasOwn(EVENT_TABS, name) ? EVENT_TABS[name] : EVENT_TABS.snapshot;
  const tab = document.getElementById(id);
  if (!tab || tab.closest('li')?.hidden || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

for (const [name, tabId] of Object.entries(EVENT_TABS)) {
  document.getElementById(tabId)?.addEventListener('shown.bs.tab', () => {
    const url = new URL(window.location.href);
    if (name === 'snapshot') url.searchParams.delete('tab');
    else url.searchParams.set('tab', name);
    window.history.replaceState({}, '', url.pathname + url.search + url.hash);
  });
}

// ------------------------------------------------------------------
// Evidence helpers
// ------------------------------------------------------------------

function renderDiaryEvidence(e) {
  const block = document.getElementById('diaryBlock');
  if (!block) return;

  if ((e?.source || '') !== 'diary') {
    block.style.display = 'none';
    block.innerHTML = '';
    return;
  }

  const np = e?.normalized_payload || {};
  const details = np && typeof np === 'object' ? np.details || {} : {};
  const notes =
    details && typeof details === 'object' && typeof details.notes === 'string'
      ? details.notes
      : '';
  const links = Array.isArray(np?.links) ? np.links : [];

  let html = '';

  if (notes && notes.trim()) {
    html += `
          <div class="fw-bold mb-2">Diary notes</div>
          <div class="mb-3">${esc(notes).replaceAll('\n', '<br>')}</div>
        `;
  } else if (details && typeof details === 'object' && Object.keys(details).length) {
    const pretty = (() => {
      try {
        return JSON.stringify(details, null, 2);
      } catch {
        return String(details);
      }
    })();
    html += `
          <div class="fw-bold mb-2">Diary details</div>
          <pre class="pretty mono mb-3">${esc(pretty)}</pre>
        `;
  }

  const linkItems = links
    .map((l) => {
      const rawUrl = String(l?.url || '').trim();
      if (!rawUrl) return '';
      // Validate scheme: drop javascript:/data:/protocol-relative URLs so a
      // diary link cannot become a click-to-XSS vector. esc() alone does not
      // stop javascript: (it contains no HTML metacharacters).
      const url = safeExternalHref(rawUrl);
      if (!url) return '';
      const label = String(l?.text || '').trim() || rawUrl;
      return `<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer"> ${esc(label)}</a></li>`;
    })
    .filter(Boolean)
    .join('');

  if (linkItems) {
    html += `
          <div class="fw-bold mb-2">Links</div>
          <ul class="mb-0 ps-3">${linkItems}</ul>
        `;
  }

  if (!html.trim()) {
    block.style.display = 'none';
    block.innerHTML = '';
    return;
  }

  block.style.display = '';
  block.innerHTML = html + '<hr class="my-3">';
}

// ------------------------------------------------------------------
// Diary editor modal (admin-only)
// ------------------------------------------------------------------

let editAllControls = null;
let editAllControlsFramework = null;
// Chosen catalogue rows ({ id, ref, type }). A clause and a control can share a
// reference, so choices are sent by id.
let editSelected = [];

function editControlLabel(item) {
  return `${item?.type === 'clause' ? 'Clause ' : ''}${item?.ref || ''}`;
}

function editSortSelected() {
  editSelected.sort((a, b) =>
    editControlLabel(a).localeCompare(editControlLabel(b), undefined, { numeric: true }),
  );
}

async function loadAllControlsOnce(frameworkSlug = null) {
  const fw =
    String(frameworkSlug || pageFramework || getCurrentFramework() || '').trim() ||
    'iso_27001_2022';
  if (editAllControls && editAllControlsFramework === fw) return editAllControls;
  try {
    const res = await apiGet(`/api/v1/controls?framework=${encodeURIComponent(fw)}&limit=5000`);
    editAllControls = (res.items || []).filter((it) => it.id && it.ref);
    editAllControlsFramework = fw;
  } catch {
    editAllControls = [];
    editAllControlsFramework = fw;
  }
  return editAllControls;
}

const editLinksWrap = document.getElementById('editDiaryLinks');

function editAddLinkRow(url = '', text = '') {
  if (!editLinksWrap) return;
  const row = document.createElement('div');
  row.className = 'row g-2 align-items-end';
  row.setAttribute('data-link-row', '1');
  row.innerHTML = `
        <div class="col-12 col-lg-6">
          <label class="form-label small-muted mb-1">URL</label>
          <input class="form-control" data-link-url value="${esc(url)}" placeholder="https://" />
        </div>
        <div class="col-10 col-lg-5">
          <label class="form-label small-muted mb-1">Link text</label>
          <input class="form-control" data-link-text value="${esc(text)}" placeholder="e.g. Change request #123" />
        </div>
        <div class="col-2 col-lg-1 d-grid">
          <button type="button" class="btn btn-outline-danger" data-link-remove aria-label="Remove">×</button>
        </div>
      `;
  editLinksWrap.appendChild(row);
  row.querySelector('[data-link-remove]')?.addEventListener('click', () => row.remove());
}

function editReadLinks() {
  if (!editLinksWrap) return [];
  const links = [];
  editLinksWrap.querySelectorAll('[data-link-row]').forEach((row) => {
    const url = (row.querySelector('[data-link-url]')?.value || '').trim();
    const text = (row.querySelector('[data-link-text]')?.value || '').trim();
    if (url) links.push({ url, text });
  });
  return links;
}

function editRenderSelected() {
  const wrap = document.getElementById('editControlSelected');
  if (!wrap) return;
  wrap.innerHTML = editSelected
    .map(
      (item) => `
        <span class="badge badge-soft">
          ${esc(editControlLabel(item))}
          <button type="button" class="btn btn-sm btn-link link-danger p-0 ms-1" data-remove="${esc(item.id)}" aria-label="Remove">×</button>
        </span>
      `,
    )
    .join('');

  wrap.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-remove');
      editSelected = editSelected.filter((item) => item.id !== id);
      editRenderSelected();
    });
  });
}

function editRenderResults(items) {
  const resEl = document.getElementById('editControlResults');
  if (!resEl) return;
  if (!items.length) {
    resEl.innerHTML = '';
    return;
  }
  resEl.innerHTML = items
    .map((it) => {
      const title = it.title ? ` <span class="small-muted">${esc(it.title)}</span>` : '';
      return `
          <button type="button" class="list-group-item list-group-item-action" data-add="${esc(it.id)}">
            <span class="mono">${esc(editControlLabel(it))}</span>${title}
          </button>
        `;
    })
    .join('');

  resEl.querySelectorAll('[data-add]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-add');
      const chosen = (editAllControls || []).find((item) => item.id === id);
      if (!chosen) return;
      if (!editSelected.some((item) => item.id === id)) editSelected.push(chosen);
      editSortSelected();
      editRenderSelected();
    });
  });
}

async function openEditDiaryModal() {
  const e = currentEvent;
  if (!e || (e.source || '') !== 'diary') return;
  if (!me?.is_admin) return;

  const modalEl = document.getElementById('editDiaryModal');
  if (!modalEl) return;

  // Prefill fields
  const np = e.normalized_payload || {};
  const sum =
    (np.summary || '').trim() ||
    String(e.summary || '')
      .replace(/^diary:\s*/i, '')
      .trim();
  document.getElementById('editDiarySummary').value = sum;

  const details = np.details || {};
  const notes =
    details && typeof details === 'object' && typeof details.notes === 'string'
      ? details.notes
      : '';
  const detailsEl = document.getElementById('editDiaryDetails');
  if (detailsEl) {
    if (notes) detailsEl.value = notes;
    else if (details && typeof details === 'object' && Object.keys(details).length) {
      try {
        detailsEl.value = JSON.stringify(details, null, 2);
      } catch {
        detailsEl.value = String(details);
      }
    } else {
      detailsEl.value = '';
    }
  }

  // Timestamp placeholder (do not auto-set value to avoid accidental changes)
  const tsEl = document.getElementById('editDiaryTs');
  if (tsEl) {
    tsEl.value = '';
    tsEl.placeholder = fmtTs(e.timestamp);
  }

  // Links
  if (editLinksWrap) {
    editLinksWrap.innerHTML = '';
    const links = Array.isArray(np.links) ? np.links : [];
    if (links.length) {
      for (const l of links) editAddLinkRow(String(l?.url || ''), String(l?.text || ''));
    }
    if (editLinksWrap.children.length === 0) editAddLinkRow();
  }

  // Controls selection
  editSelected = [
    ...new Map(
      (e.controls || [])
        .filter((c) => c.id && c.ref)
        .map((c) => [c.id, { id: c.id, ref: c.ref, type: c.type }]),
    ).values(),
  ];
  editSortSelected();
  editRenderSelected();

  // Wire up control search
  await loadAllControlsOnce(pageFramework);
  const searchEl = document.getElementById('editControlSearch');
  if (searchEl && !searchEl.dataset.bound) {
    searchEl.dataset.bound = '1';
    searchEl.addEventListener('input', (ev) => {
      const q = (ev.target.value || '').trim().toLowerCase();
      if (!q || !Array.isArray(editAllControls) || editAllControls.length === 0) {
        editRenderResults([]);
        return;
      }
      const hits = [];
      for (const c of editAllControls) {
        const hay = `${c.ref} ${c.title || ''}`.toLowerCase();
        if (hay.includes(q)) {
          hits.push(c);
          if (hits.length >= 25) break;
        }
      }
      editRenderResults(hits);
    });
  }

  // Add link button
  const addLinkBtn = document.getElementById('editDiaryAddLink');
  if (addLinkBtn && !addLinkBtn.dataset.bound) {
    addLinkBtn.dataset.bound = '1';
    addLinkBtn.addEventListener('click', () => editAddLinkRow());
  }

  // Save button
  const saveBtn = document.getElementById('editDiarySave');
  const modalStatus = document.getElementById('editDiaryStatus');
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = '1';
    saveBtn.addEventListener('click', async () => {
      if (modalStatus) {
        modalStatus.style.display = 'none';
        modalStatus.textContent = '';
      }
      try {
        const summary = document.getElementById('editDiarySummary').value.trim();
        if (!summary) throw new Error('Missing summary');

        const detailsText = (document.getElementById('editDiaryDetails').value || '').trim();
        let detailsPayload = detailsText;
        if (detailsText && (detailsText.startsWith('{') || detailsText.startsWith('['))) {
          try {
            detailsPayload = JSON.parse(detailsText);
          } catch {
            detailsPayload = detailsText;
          }
        }

        const payload = {
          summary,
          details: detailsPayload,
          links: editReadLinks(),
          controls: editSelected.map((item) => item.id),
          framework: pageFramework,
        };

        const tsVal = document.getElementById('editDiaryTs').value;
        if (tsVal) {
          const dt = new Date(tsVal);
          if (!Number.isNaN(dt.getTime())) payload.timestamp = dt.toISOString();
        }

        const res = await apiPatch(`/api/v1/admin/diary/${encodeURIComponent(e.id)}`, payload);

        // Close modal + refresh
        try {
          const inst = window.bootstrap?.Modal?.getOrCreateInstance(modalEl);
          inst?.hide();
        } catch {}

        toast(status, 'Diary entry updated', 'success');
        await load();
      } catch (err) {
        const msg = errorText(err);
        if (modalStatus) {
          modalStatus.className = 'alert alert-danger';
          modalStatus.textContent = msg;
          modalStatus.style.display = '';
        } else {
          toast(status, msg, 'danger');
        }
      }
    });
  }

  // Show modal
  try {
    const inst = window.bootstrap?.Modal?.getOrCreateInstance(modalEl);
    inst?.show();
  } catch {
    modalEl.style.display = '';
  }
}

function renderIncidents(items) {
  const card = document.getElementById('incidentsCard');
  const body = document.getElementById('incidents');
  if (!card || !body) return;
  const arr = Array.isArray(items) ? items : [];
  setEventTab('incidents', arr.length > 0, arr.length);
  if (!arr.length) {
    card.style.display = 'none';
    body.innerHTML = '-';
    return;
  }
  card.style.display = '';
  const canDelete = !!(me?.is_admin || me?.can_delete_incidents);
  body.innerHTML = arr
    .map((i) => {
      const who = i.created_by ? ` by ${esc(i.created_by)}` : '';
      const when = i.created_at ? fmtTs(i.created_at) : '-';
      const text = String(i.text || '').trim();
      const shortText = text.length > 350 ? `${text.slice(0, 350)}…` : text;
      const deleteBtn = canDelete
        ? `
          <button class="btn btn-sm btn-outline-danger" type="button" data-incident-delete="${esc(i.id || '')}" title="Delete incident">
            <i class="bi bi-trash" aria-hidden="true"></i> Delete
          </button>`
        : '';
      return `
      <div class="incident-ref border rounded-3 p-3 mb-2">
        <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between">
          <div class="fw-bold">${esc(i.title || 'Incident')}</div>
          <div class="d-flex flex-wrap gap-2 align-items-center">
            <div class="small-muted">${esc(when)}${who}</div>
            ${deleteBtn}
          </div>
        </div>
        ${shortText ? `<div class="small-muted mt-2 whitespace-pre-wrap">${esc(shortText)}</div>` : ''}
      </div>
    `;
    })
    .join('');
}

async function deleteIncident(incidentId) {
  if (!currentEvent || !incidentId) return;
  if (!(me?.is_admin || me?.can_delete_incidents)) {
    toast(status, 'incident.delete permission required.', 'danger');
    return;
  }
  const ok = await confirmAction({
    title: 'Delete incident reference',
    message: 'Delete this incident reference from Keen?',
    confirmLabel: 'Delete',
  });
  if (!ok) return;
  try {
    await apiDelete(
      `/api/v1/events/${encodeURIComponent(currentEvent.id)}/incidents/${encodeURIComponent(incidentId)}`,
    );
    currentEvent.incidents = (currentEvent.incidents || []).filter(
      (item) => String(item.id || '') !== String(incidentId),
    );
    renderIncidents(currentEvent.incidents);
    toast(status, 'Incident deleted.', 'success');
  } catch (err) {
    toast(status, `Failed to delete incident: ${errorText(err)}`, 'danger');
  }
}

document.getElementById('incidents')?.addEventListener('click', (ev) => {
  const btn = ev.target?.closest?.('[data-incident-delete]');
  if (!btn) return;
  deleteIncident(btn.getAttribute('data-incident-delete') || '');
});

const SUGGESTED_PRESELECT = 3;

function proposeRuleHref(eventId, refs) {
  const p = new URLSearchParams();
  p.set('framework', pageFramework);
  p.set('from_event', eventId);
  if (refs.length) p.set('targets', refs.join(','));
  return '/admin.html?' + p.toString() + '#evidence-config';
}

function renderSuggestedControls(e, data) {
  const list = document.getElementById('suggestedControlsList');
  const meta = document.getElementById('suggestedControlsMeta');
  const actions = document.getElementById('suggestedControlsActions');
  const propose = document.getElementById('suggestedControlsPropose');
  const items = Array.isArray(data?.items) ? data.items : [];
  const canPropose = !!me?.is_admin && !PRINT_MODE;
  if (meta) {
    meta.textContent = items.length
      ? `Catalogue matches in ${data?.framework || pageFramework} for this event's wording. Review each one; nothing is mapped until a rule is saved.`
      : `No clear catalogue matches in ${data?.framework || pageFramework} for this event's wording. Search the controls list or describe the evidence in the mapping editor.`;
  }
  if (!list) return;
  list.replaceChildren();
  items.forEach((item, index) => {
    const row = document.createElement('label');
    row.className = 'list-group-item d-flex gap-2 align-items-start';
    if (canPropose) {
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.className = 'form-check-input mt-1 flex-shrink-0';
      box.value = String(item.ref || '');
      box.checked = index < SUGGESTED_PRESELECT;
      box.setAttribute('aria-label', `Include ${item.ref || 'control'} in the proposed rule`);
      row.append(box);
    }
    const body = document.createElement('div');
    const title = document.createElement('div');
    const link = document.createElement('a');
    link.className = 'badge badge-soft me-2';
    link.href = withFramework(
      `/control.html?id=${encodeURIComponent(item.id || '')}`,
      item.framework || pageFramework,
    );
    link.textContent = String(item.ref || 'Control');
    const name = document.createElement('span');
    name.textContent = String(item.title || '');
    title.append(link, name);
    const reason = document.createElement('div');
    reason.className = 'small-muted';
    reason.textContent = String(item.reason || '');
    body.append(title, reason);
    row.append(body);
    list.append(row);
  });
  if (actions) actions.hidden = !(canPropose && items.length);
  const sync = () => {
    if (!propose) return;
    const refs = [...list.querySelectorAll('input[type="checkbox"]:checked')].map((b) => b.value);
    propose.href = proposeRuleHref(e.id, refs);
    propose.textContent = refs.length
      ? `Propose a rule with ${refs.length} ticked control${refs.length === 1 ? '' : 's'}`
      : 'Propose a rule from this event';
  };
  list.addEventListener('change', sync);
  sync();
}

async function loadSuggestedControls(e) {
  const list = document.getElementById('suggestedControlsList');
  const meta = document.getElementById('suggestedControlsMeta');
  const button = document.getElementById('suggestedControlsLoad');
  if (button) button.hidden = true;
  if (meta) meta.textContent = 'Looking for matching controls';
  try {
    const data = await apiGet(
      `/api/v1/events/${encodeURIComponent(e.id)}/control-suggestions?framework=${encodeURIComponent(pageFramework)}`,
    );
    renderSuggestedControls(e, data);
  } catch (err) {
    if (meta) meta.textContent = 'Control suggestions are unavailable right now.';
    if (list) list.replaceChildren();
  }
}

function setupSuggestedControls(e, controls) {
  const wrap = document.getElementById('suggestedControls');
  const button = document.getElementById('suggestedControlsLoad');
  const meta = document.getElementById('suggestedControlsMeta');
  if (!wrap || PRINT_MODE || !e?.id) return;
  wrap.hidden = false;
  if (!(controls || []).length) {
    loadSuggestedControls(e);
    return;
  }
  if (meta)
    meta.textContent =
      'This event is mapped. Ask for suggestions to see other controls its wording resembles.';
  if (button) {
    button.hidden = false;
    button.addEventListener('click', () => loadSuggestedControls(e), { once: true });
  }
}

async function load() {
  if (!id) {
    toast(status, 'Missing event id.', 'danger');
    return;
  }
  status.style.display = 'none';

  try {
    sourceMeta = await getSourceMeta();
    const e = await apiGet(
      `/api/v1/events/${encodeURIComponent(id)}?framework=${encodeURIComponent(pageFramework)}`,
    );
    const mappingButton = document.getElementById('btnCreateMapping');
    if (mappingButton && me?.is_admin && !PRINT_MODE) {
      mappingButton.hidden = false;
      mappingButton.href =
        '/admin.html?framework=' +
        encodeURIComponent(pageFramework) +
        '&from_event=' +
        encodeURIComponent(e.id) +
        '#evidence-config';
    }
    currentEvent = e;

    document.getElementById('summary').textContent = e.summary || '(no summary)';
    const srcLabel = e.source ? sourceLabel(e.source, sourceMeta) || e.source : 'unknown';
    document.getElementById('meta').textContent = `${srcLabel} · ${fmtTs(e.timestamp)}`;

    setText('ts', fmtTs(e.timestamp));
    setText('system', e.system || '-');
    setText('actor', e.actor || '-');
    setText('action', e.action || '-');
    setText('outcome', e.outcome || '-');
    setText('severity', e.severity ?? '-');

    // Source badge + sideways navigation
    if (e.source) {
      const sourceHref = withFramework(
        `/source.html?source=${encodeURIComponent(e.source)}`,
        pageFramework,
      );
      document.getElementById('srcBadge').innerHTML = sourceBadgeHtml(
        e.source,
        sourceMeta,
        sourceHref,
      );
      if (e.connection_id) {
        const origin = document.createElement('span');
        origin.className = 'badge text-bg-secondary ms-2';
        origin.textContent = e.connection_name || e.connection_id;
        origin.title = 'Connection: ' + e.connection_id;
        document.getElementById('srcBadge').append(origin);
      }
      document.getElementById('bySource').href = sourceHref;
    } else {
      document.getElementById('srcBadge').textContent = '-';
      document.getElementById('bySource').href = withFramework('/events.html', pageFramework);
    }

    // Open in source
    if (e.source_url) {
      const safeSource = safeExternalHref(e.source_url);
      if (safeSource) {
        const a = document.getElementById('openSource');
        a.href = safeSource;
        a.style.display = '';
      }
    }

    // Controls
    const controlsEl = document.getElementById('controls');
    const rationaleEl = document.getElementById('rationale');

    const controls = e.controls || [];
    if (!qs('framework')) {
      const inferred = String(controls?.[0]?.framework || '').trim();
      if (inferred) pageFramework = inferred;
    }

    if (controls.length === 0) {
      controlsEl.innerHTML = `<span class="badge text-bg-light">Unmapped</span>`;
      if (rationaleEl) rationaleEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
    } else {
      // Render mapped controls list
      controlsEl.innerHTML = controls
        .map((c) => {
          const titleAttr = c.title ? ` title="${esc(c.title)}"` : '';
          const upstream = safeAbsoluteHttpHref(c.upstream_url);
          const titleHtml = c.title
            ? upstream
              ? `<a class="link-subtle small-muted" href="${esc(upstream)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-info-circle ms-1" aria-hidden="true"></i> ${esc(c.title)}</a>`
              : `<span class="small-muted">${esc(c.title)}</span>`
            : '';
          return `
              <div class="mb-2">
                <a class="badge badge-soft me-2" href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, c.framework || pageFramework)}"${titleAttr}>${esc(c.ref)}</a>
                ${titleHtml}
              </div>
            `;
        })
        .join('');
      // Render rationale
      if (rationaleEl) {
        const uniq = [];
        const seen = new Set();

        for (const c of controls) {
          let html = '';
          if (c.inherited_from) {
            html = inheritedRationaleHtml(c);
          } else {
            const r = (c.rationale || '').trim();
            if (!r) continue;
            html =
              me?.is_admin && !PRINT_MODE && c.rule_id
                ? `<a href="${ruleSettingsHref(c.rule_id)}">${esc(r)}</a>`
                : esc(r);
          }
          if (seen.has(html)) continue;
          seen.add(html);
          uniq.push(html);
        }

        if (uniq.length === 0) {
          rationaleEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
        } else if (uniq.length === 1) {
          rationaleEl.innerHTML = `<div class="mapping-rationale">${uniq[0]}</div>`;
        } else {
          rationaleEl.innerHTML = `<ul class="mb-0 ps-3">
                ${uniq.map((r) => `<li class="mapping-rationale">${r}</li>`).join('')}
              </ul>`;
        }
      }
    }

    setupSuggestedControls(e, controls);

    // Diary notes + links (if present)
    renderDiaryEvidence(e);

    // Show diary edit button for admins
    const editBtn = document.getElementById('btnEditDiary');
    if (editBtn && me?.is_admin && (e.source || '') === 'diary') {
      editBtn.style.display = '';
    } else if (editBtn) {
      editBtn.style.display = 'none';
    }

    // Show audit sampling button for users with audits.manage/admins.
    const auditBtn = document.getElementById('btnSampleAudit');
    if (auditBtn && canSampleIntoAudit(me, 'event')) {
      auditBtn.style.display = '';
    } else if (auditBtn) {
      auditBtn.style.display = 'none';
    }

    const incidentBtn = document.getElementById('btnCreateIncident');
    if (incidentBtn && me?.can_create_incidents) {
      incidentBtn.style.display = '';
    } else if (incidentBtn) {
      incidentBtn.style.display = 'none';
    }

    renderIncidents(e.incidents || []);

    await refreshAuditSamples(e.id);
    showStories(e.id);

    // Artifacts list
    const arts = e.artifacts || [];
    const artsEl = document.getElementById('artifacts');

    // Show/hide the ZIP evidence button depending on whether we have artifacts.
    const zipBtn = document.getElementById('btnZip');
    if (zipBtn) zipBtn.style.display = arts.length ? '' : 'none';

    // Inline previews for attached images + PDFs (useful for diary evidence)
    const previewsEl = document.getElementById('imagePreviews');
    const imgArts = arts.filter((a) => (a.content_type || '').toLowerCase().startsWith('image/'));
    const pdfArts = arts.filter((a) =>
      (a.content_type || '').toLowerCase().includes('application/pdf'),
    );

    if (imgArts.length || pdfArts.length) {
      previewsEl.style.display = '';

      const imgHtml = imgArts
        .map((a) => {
          const src = `/api/v1/artifacts/${encodeURIComponent(a.id)}/download`;
          return `
              <div class="col-12 col-md-6">
                <a href="${src}" class="d-block" target="_blank" rel="noopener noreferrer">
                  <img class="evidence-img" src="${src}" alt="attachment" loading="${PRINT_MODE ? 'eager' : 'lazy'}" />
                </a>
              </div>
            `;
        })
        .join('');

      const pdfHtml = pdfArts
        .map((a) => {
          const src = `/api/v1/artifacts/${encodeURIComponent(a.id)}/download`;
          const prev = `/api/v1/artifacts/${encodeURIComponent(a.id)}/preview`;
          return `
              <div class="col-12 col-md-6">
                <div class="pdf-preview-card">
                  <div class="d-flex align-items-center justify-content-between mb-2">
                    <div class="small-muted">
                      <i class="bi bi-file-earmark-pdf"></i>
                      PDF document
                    </div>
                    <a class="btn btn-sm btn-outline-primary" href="${src}" target="_blank" rel="noopener noreferrer">Download</a>
                  </div>
                  <a href="${src}" class="d-block" target="_blank" rel="noopener noreferrer">
                    <img class="evidence-img evidence-pdf-thumb" src="${prev}" alt="PDF preview" loading="${PRINT_MODE ? 'eager' : 'lazy'}" />
                  </a>
                </div>
              </div>
            `;
        })
        .join('');

      previewsEl.innerHTML = `
            <div class="fw-bold mb-2 pdf-keep-with-next">Attached files</div>
            <div class="row g-2">
              ${imgHtml}
              ${pdfHtml}
            </div>
            <hr class="my-3">
          `;
      // PDF previews are thumbnails rendered server-side.
    } else {
      previewsEl.style.display = 'none';
      previewsEl.innerHTML = '';
    }
    if (arts.length === 0) {
      artsEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
    } else {
      artsEl.innerHTML = `
            <div class="table-responsive">
              <table class="table table-sm align-middle mb-0">
                <thead>
                  <tr>
                    <th>Captured</th>
                    <th>Kind</th>
                    <th>Type</th>
                    <th>Size</th>
                    <th class="no-print">Download</th>
                  </tr>
                </thead>
                <tbody>
                  ${arts
                    .map(
                      (a) => `
                    <tr>
                      <td class="small-muted">${esc(fmtTs(a.captured_at))}</td>
                      <td><span class="badge badge-chip">${esc(a.kind || 'artifact')}</span></td>
                      <td class="small-muted">${esc(a.content_type || 'application/octet-stream')}</td>
                      <td class="small-muted">${esc(a.size_bytes ?? '-')}</td>
                      <td class="no-print"><a class="btn btn-sm btn-outline-primary" href="/api/v1/artifacts/${encodeURIComponent(a.id)}/download">Download</a></td>
                    </tr>
                  `,
                    )
                    .join('')}
                </tbody>
              </table>
            </div>
          `;
    }

    // Questions (auditor Q&A)
    await questionThreads.refresh();

    // JSON panes
    renderJson(document.getElementById('normalized'), pretty(e.normalized_payload ?? {}));
    renderJson(document.getElementById('raw'), pretty(e.raw_pointer ?? {}));

    // A deep link opens its tab once the tabs know what they hold; a linked
    // question thread has already opened the Questions tab.
    if (!questionThreadFocused && qs('tab')) showEventTab(qs('tab'));

    // If this is the dedicated print window, trigger the native print dialog
    // after the event + thumbnails are rendered. The printout shows the raw
    // pointer and the questions with the snapshot.
    if (PRINT_MODE) {
      const rawPointer = document.getElementById('rawPointerCard');
      if (rawPointer) rawPointer.open = true;
      await exporter.autoPrintEvidence(e);
    }
  } catch (err) {
    toast(status, `Failed to load event: ${errorText(err)}`, 'danger');
  }
}

document
  .getElementById('btnPdf')
  .addEventListener('click', () => exporter.downloadPdf(currentEvent));

document.getElementById('btnEditDiary')?.addEventListener('click', () => openEditDiaryModal());

document
  .getElementById('btnZip')
  ?.addEventListener('click', () => exporter.downloadArtifactsZip(currentEvent));

// ------------------------------------------------------------------
// Audit engagement sampling
// ------------------------------------------------------------------

const auditSampleModalEl = document.getElementById('auditSampleModal');
const auditSampleSelect = document.getElementById('auditSampleSelect');
const auditSampleNotes = document.getElementById('auditSampleNotes');
const auditSampleStatus = document.getElementById('auditSampleStatus');
const auditSampleSubmit = document.getElementById('auditSampleSubmit');
const auditSampleUseCurrentUrl = document.getElementById('auditSampleUseCurrentUrl');
const auditSampleCreate = document.getElementById('auditSampleCreate');

function _currentEventRelativeUrl() {
  const fw = pageFramework || getCurrentFramework();
  return withFramework(`/event.html?id=${encodeURIComponent(currentEvent?.id || id)}`, fw);
}

function canViewAuditEngagements() {
  return Boolean(me?.can_view_audits || me?.can_manage_audits || me?.is_admin);
}

function renderAuditSamples(items) {
  const card = document.getElementById('auditSamplesCard');
  const body = document.getElementById('auditSamples');
  const arr = Array.isArray(items) ? items : [];
  if (!card || !body) return;
  setEventTab('audits', arr.length > 0, arr.length);
  if (!arr.length) {
    card.style.display = 'none';
    body.innerHTML = '-';
    return;
  }

  card.style.display = '';
  body.innerHTML = `<div class="list-group">${arr
    .map((x) => {
      const a = x.audit || {};
      const ev = x.evidence || {};
      const url = withFramework(
        `/audit.html?id=${encodeURIComponent(a.id || '')}`,
        a.framework_slug || pageFramework || getCurrentFramework(),
      );
      const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ');
      const meta = [
        a.framework_slug,
        dates,
        ev.added_at ? `sampled ${fmtTs(ev.added_at)}` : '',
        ev.added_by_username ? `by ${ev.added_by_username}` : '',
      ]
        .filter(Boolean)
        .join(' · ');
      const notes = (ev.notes || '').trim();
      return `<div class="list-group-item">
      <div class="d-flex flex-wrap gap-2 justify-content-between align-items-start">
        <div>
          <a class="fw-semibold" href="${esc(url)}">${esc(a.title || 'Audit')}</a>
          <div class="small-muted">${esc(meta || 'Sampled into this audit')}</div>
          ${notes ? `<div class="mt-2 small">${esc(notes).replaceAll('\n', '<br>')}</div>` : ''}
        </div>
        <span class="badge text-bg-light">${esc((a.status || 'open').replaceAll('_', ' '))}</span>
      </div>
    </div>`;
    })
    .join('')}</div>`;
}

// The tab appears when the event is in a story, or for authors who can add it.
function showStories(eventId) {
  const card = document.getElementById('storiesCard');
  const mounted = mountStoryRelated({
    me,
    kind: 'event',
    id: eventId,
    host: document.getElementById('eventStories'),
    framework: pageFramework || getCurrentFramework(),
    onLoad: (stories) => {
      const visible = stories.length > 0 || canManageStories(me);
      card.style.display = visible ? '' : 'none';
      setEventTab('stories', visible, stories.length);
    },
  });
  if (!mounted) {
    card.style.display = 'none';
    setEventTab('stories', false);
  }
}

async function refreshAuditSamples(eventId) {
  if (!featureEnabled(me, 'audits')) return;
  const card = document.getElementById('auditSamplesCard');
  if (!canViewAuditEngagements()) {
    if (card) card.style.display = 'none';
    setEventTab('audits', false);
    return;
  }
  try {
    const data = await apiGet(`/api/v1/audits/by-event/${encodeURIComponent(eventId)}`);
    renderAuditSamples(data?.items || []);
  } catch (e) {
    // Do not block the evidence view if audit visibility is unavailable.
    if (card) card.style.display = 'none';
    setEventTab('audits', false);
  }
}

async function openAuditSampleModal() {
  if (!currentEvent) {
    toast(status, 'Event not loaded yet', 'warning');
    return;
  }
  if (!canSampleIntoAudit(me, 'event')) {
    toast(status, 'audits.manage permission required.', 'danger');
    return;
  }

  if (auditSampleStatus) auditSampleStatus.style.display = 'none';
  if (auditSampleNotes) {
    auditSampleNotes.value = '';
    auditSampleNotes.disabled = false;
  }
  if (auditSampleUseCurrentUrl) auditSampleUseCurrentUrl.disabled = false;
  if (auditSampleSubmit) auditSampleSubmit.disabled = false;
  if (auditSampleCreate)
    auditSampleCreate.href = withFramework(
      '/audit-wizard.html',
      pageFramework || getCurrentFramework(),
    );

  if (auditSampleSelect) {
    auditSampleSelect.innerHTML = '<option value="">Loading audits</option>';
    try {
      const fw = pageFramework || getCurrentFramework();
      const data = await apiGet(`/api/v1/audits?framework=${encodeURIComponent(fw)}&limit=500`);
      const items = (data?.items || []).filter(
        (a) => isAuditActive(a) && canEditAuditForUser(me, a),
      );
      auditSampleSelect.innerHTML =
        items
          .map((a) => {
            const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ');
            const suffix = dates ? ` · ${dates}` : '';
            const statusSuffix =
              (a.status || 'open') === 'in_progress' ? ' · In progress' : ' · Open';
            return `<option value="${esc(a.id)}">${esc(a.title || 'Audit')}${esc(suffix)}${esc(statusSuffix)}</option>`;
          })
          .join('') || '<option value="">No open or in-progress audits - start one first</option>';
      preselectSamplingAudit(auditSampleSelect);
      if (auditSampleSubmit) auditSampleSubmit.disabled = !items.length;
      if (auditSampleNotes) auditSampleNotes.disabled = !items.length;
      if (auditSampleUseCurrentUrl) auditSampleUseCurrentUrl.disabled = !items.length;
      if (!items.length) {
        toast(
          auditSampleStatus || status,
          'No open or in-progress audits are available. Start an Audit first, then sample this event into it.',
          'warning',
        );
      }
    } catch (e) {
      console.debug('DEBUG-1154', e, typeof errorText, errorText(e));
      auditSampleSelect.innerHTML = '<option value="">Failed to load audits</option>';
      if (auditSampleSubmit) auditSampleSubmit.disabled = true;
      if (auditSampleNotes) auditSampleNotes.disabled = true;
      if (auditSampleUseCurrentUrl) auditSampleUseCurrentUrl.disabled = true;
      toast(auditSampleStatus || status, `Failed to load audits: ${errorText(e)}`, 'danger');
    }
  }

  if (window.bootstrap?.Modal && auditSampleModalEl) {
    window.bootstrap.Modal.getOrCreateInstance(auditSampleModalEl).show();
  }
}

async function submitAuditSample() {
  const auditId = (auditSampleSelect?.value || '').trim();
  if (!auditId) {
    toast(
      auditSampleStatus || status,
      'Start an Audit first, then sample this event into it.',
      'warning',
    );
    return;
  }
  const btn = auditSampleSubmit;
  const prev = btn?.textContent || '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Sampling';
  }
  try {
    const evidence_url = auditSampleUseCurrentUrl?.checked ? _currentEventRelativeUrl() : null;
    const body = {
      event_id: currentEvent.id,
      evidence_url,
      notes: auditSampleNotes?.value || '',
      title: currentEvent.summary || 'Keen event evidence',
    };
    await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence`, body);
    rememberSamplingAudit(auditId);
    toast(status, 'Evidence sampled into audit', 'success');
    await refreshAuditSamples(currentEvent.id);
    if (window.bootstrap?.Modal && auditSampleModalEl) {
      window.bootstrap.Modal.getOrCreateInstance(auditSampleModalEl).hide();
    }
  } catch (e) {
    toast(auditSampleStatus || status, `Failed: ${errorText(e)}`, 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prev || 'Sample into audit';
    }
  }
}

document.getElementById('btnSampleAudit')?.addEventListener('click', openAuditSampleModal);
auditSampleSubmit?.addEventListener('click', submitAuditSample);

// ------------------------------------------------------------------
// Incident creation webhook
// ------------------------------------------------------------------
const incidentModalEl = document.getElementById('incidentModal');
const incidentTitleEl = document.getElementById('incidentTitle');
const incidentTextEl = document.getElementById('incidentText');
const incidentTextCountEl = document.getElementById('incidentTextCount');
const incidentStatusEl = document.getElementById('incidentStatus');
const incidentSubmitBtn = document.getElementById('incidentSubmit');

function updateIncidentTextCount() {
  if (!incidentTextEl || !incidentTextCountEl) return;
  incidentTextCountEl.textContent = String(incidentTextEl.value.length || 0);
}

function openIncidentModal() {
  if (!me?.can_create_incidents) {
    toast(status, 'incident.create permission required.', 'danger');
    return;
  }
  if (!currentEvent) {
    toast(status, 'Event not loaded yet.', 'warning');
    return;
  }
  if (incidentStatusEl) incidentStatusEl.style.display = 'none';
  if (incidentTitleEl) {
    const summary = String(currentEvent.summary || 'Keen event incident').trim();
    const prefix = summary.toLowerCase().startsWith('incident') ? '' : 'Incident: ';
    incidentTitleEl.value = `${prefix}${summary}`.slice(0, 256);
  }
  if (incidentTextEl) incidentTextEl.value = '';
  updateIncidentTextCount();
  if (window.bootstrap?.Modal && incidentModalEl) {
    window.bootstrap.Modal.getOrCreateInstance(incidentModalEl).show();
  }
}

// Incident titles and text are plain text: normalise newlines, drop control
// characters and cap the length before sending them to the API.
function sanitizePlainText(value, maxChars) {
  let txt = String(value || '');
  txt = txt.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  txt = txt.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '');
  txt = txt.trim();
  const max = Number(maxChars || 0);
  if (max > 0 && txt.length > max) txt = txt.slice(0, max);
  return txt;
}

async function submitIncident() {
  if (!currentEvent) {
    toast(incidentStatusEl || status, 'Event not loaded yet.', 'warning');
    return;
  }
  const title = sanitizePlainText(incidentTitleEl?.value || '', 256);
  const text = sanitizePlainText(incidentTextEl?.value || '', 5000);
  if (!title) {
    toast(incidentStatusEl || status, 'Title is required.', 'warning');
    return;
  }
  const btn = incidentSubmitBtn;
  const prev = btn?.textContent || '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Creating';
  }
  try {
    const res = await apiPost(`/api/v1/events/${encodeURIComponent(currentEvent.id)}/incident`, {
      title,
      text,
    });
    const incident = res?.incident;
    if (incident) {
      currentEvent.incidents = [incident, ...(currentEvent.incidents || [])];
      renderIncidents(currentEvent.incidents);
    }
    toast(status, 'Incident created from event.', 'success');
    if (window.bootstrap?.Modal && incidentModalEl) {
      window.bootstrap.Modal.getOrCreateInstance(incidentModalEl).hide();
    }
  } catch (e) {
    toast(incidentStatusEl || status, `Failed to create incident: ${errorText(e)}`, 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prev || 'Create incident';
    }
  }
}

document.getElementById('btnCreateIncident')?.addEventListener('click', openIncidentModal);
incidentTextEl?.addEventListener('input', updateIncidentTextCount);
incidentSubmitBtn?.addEventListener('click', submitIncident);

// ------------------------------------------------------------------
// Questions (auditor Q&A)
// ------------------------------------------------------------------

// A link to a question thread opens the Questions tab instead of ?tab=.
let questionThreadFocused = false;
const questionThreads = mountQuestionThreads({
  panel: document.getElementById('questions'),
  me,
  status,
  attachments: true,
  placeholder: 'e.g. What was this about?',
  base: () =>
    currentEvent?.id ? `/api/v1/events/${encodeURIComponent(currentEvent.id)}/questions` : null,
  // The print window leaves unread replies for the author to read on screen.
  markSeen: !PRINT_MODE,
  onCount: (count) => setEventTab('questions', featureEnabled(me, 'questions'), count),
  onFocus: () => {
    questionThreadFocused = true;
    showEventTab('questions');
  },
});

load();
