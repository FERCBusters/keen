import { createEvidenceExporter } from './evidence-export/index.js';
import { renderJson } from '/json-highlight.js';
import {
  initNavbar,
  canSampleIntoAudit,
  canEditAuditForUser,
  apiGet,
  apiPatch,
  apiPost,
  apiDelete,
  apiPostForm,
  esc,
  fmtTs,
  toast,
  qs,
  getSourceMeta,
  sourceBadgeHtml,
  sourceLabel,
  safeExternalHref,
  getCurrentFramework,
  withFramework,
  isAuditActive,
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

const canDeleteQuestions = !!(me?.is_admin || me?.can_delete_questions);

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
  el.textContent = val ?? '—';
}

let currentEvent = null;

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
let editSelectedRefs = [];

async function loadAllControlsOnce(frameworkSlug = null) {
  const fw =
    String(frameworkSlug || pageFramework || getCurrentFramework() || '').trim() ||
    'iso_27001_2022';
  if (editAllControls && editAllControlsFramework === fw) return editAllControls;
  try {
    const res = await apiGet(`/api/v1/controls?framework=${encodeURIComponent(fw)}&limit=5000`);
    editAllControls = (res.items || []).filter((it) => it.ref);
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
          <input class="form-control" data-link-url value="${esc(url)}" placeholder="https://…" />
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
  wrap.innerHTML = editSelectedRefs
    .map(
      (ref) => `
        <span class="badge badge-soft">
          ${esc(ref)}
          <button type="button" class="btn btn-sm btn-link link-danger p-0 ms-1" data-remove="${esc(ref)}" aria-label="Remove">×</button>
        </span>
      `,
    )
    .join('');

  wrap.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ref = btn.getAttribute('data-remove');
      editSelectedRefs = editSelectedRefs.filter((r) => r !== ref);
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
          <button type="button" class="list-group-item list-group-item-action" data-add="${esc(it.ref)}">
            <span class="mono">${esc(it.ref)}</span>${title}
          </button>
        `;
    })
    .join('');

  resEl.querySelectorAll('[data-add]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ref = btn.getAttribute('data-add');
      if (!ref) return;
      if (!editSelectedRefs.includes(ref)) editSelectedRefs.push(ref);
      editSelectedRefs.sort();
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
  editSelectedRefs = (e.controls || []).map((c) => c.ref).filter(Boolean);
  editSelectedRefs = Array.from(new Set(editSelectedRefs)).sort();
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
          controls: editSelectedRefs.slice(),
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
        const msg = String(err);
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
  if (!arr.length) {
    card.style.display = 'none';
    body.innerHTML = '—';
    return;
  }
  card.style.display = '';
  const canDelete = !!(me?.is_admin || me?.can_delete_incidents);
  body.innerHTML = arr
    .map((i) => {
      const who = i.created_by ? ` by ${esc(i.created_by)}` : '';
      const when = i.created_at ? fmtTs(i.created_at) : '—';
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
  const ok = window.confirm('Delete this incident reference from KEEN?');
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
    toast(status, `Failed to delete incident: ${String(err)}`, 'danger');
  }
}

document.getElementById('incidents')?.addEventListener('click', (ev) => {
  const btn = ev.target?.closest?.('[data-incident-delete]');
  if (!btn) return;
  deleteIncident(btn.getAttribute('data-incident-delete') || '');
});

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
    document.getElementById('meta').textContent = `${srcLabel} • ${fmtTs(e.timestamp)}`;

    setText('ts', fmtTs(e.timestamp));
    setText('system', e.system || '—');
    setText('actor', e.actor || '—');
    setText('action', e.action || '—');
    setText('outcome', e.outcome || '—');
    setText('severity', e.severity ?? '—');

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
      document.getElementById('srcBadge').textContent = '—';
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
      controlsEl.innerHTML = `<span class="badge text-bg-light">unmapped</span>`;
      if (rationaleEl) rationaleEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
    } else {
      // Render mapped controls list
      controlsEl.innerHTML = controls
        .map((c) => {
          const titleAttr = c.title ? ` title="${esc(c.title)}"` : '';
          const upstream = (c.upstream_url || '').trim();
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
          const r = (c.rationale || '').trim();
          if (!r) continue;
          if (seen.has(r)) continue;
          seen.add(r);
          uniq.push(r);
        }

        if (uniq.length === 0) {
          rationaleEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
        } else if (uniq.length === 1) {
          rationaleEl.innerHTML = `<div class="mapping-rationale">${esc(uniq[0])}</div>`;
        } else {
          rationaleEl.innerHTML = `<ul class="mb-0 ps-3">
                ${uniq.map((r) => `<li class="mapping-rationale">${esc(r)}</li>`).join('')}
              </ul>`;
        }
      }
    }

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
                      <td class="small-muted">${esc(a.size_bytes ?? '—')}</td>
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
    await refreshQuestions(e.id);

    // JSON panes
    renderJson(document.getElementById('normalized'), pretty(e.normalized_payload ?? {}));
    renderJson(document.getElementById('raw'), pretty(e.raw_pointer ?? {}));

    // If this is the dedicated print window, trigger the native print dialog
    // after the event + thumbnails are rendered.
    if (PRINT_MODE) {
      await exporter.autoPrintEvidence(e);
    }
  } catch (err) {
    toast(status, `Failed to load event: ${String(err)}`, 'danger');
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
  if (!arr.length) {
    card.style.display = 'none';
    body.innerHTML = '—';
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

async function refreshAuditSamples(eventId) {
  const card = document.getElementById('auditSamplesCard');
  if (!canViewAuditEngagements()) {
    if (card) card.style.display = 'none';
    return;
  }
  try {
    const data = await apiGet(`/api/v1/audits/by-event/${encodeURIComponent(eventId)}`);
    renderAuditSamples(data?.items || []);
  } catch (e) {
    // Do not block the evidence view if audit visibility is unavailable.
    if (card) card.style.display = 'none';
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
      '/audits.html?new=1',
      pageFramework || getCurrentFramework(),
    );

  if (auditSampleSelect) {
    auditSampleSelect.innerHTML = '<option value="">Loading audits…</option>';
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
          .join('') || '<option value="">No open or in-progress audits — start one first</option>';
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
      auditSampleSelect.innerHTML = '<option value="">Failed to load audits</option>';
      if (auditSampleSubmit) auditSampleSubmit.disabled = true;
      if (auditSampleNotes) auditSampleNotes.disabled = true;
      if (auditSampleUseCurrentUrl) auditSampleUseCurrentUrl.disabled = true;
      toast(auditSampleStatus || status, `Failed to load audits: ${String(e)}`, 'danger');
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
    btn.textContent = 'Sampling…';
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
    toast(status, 'Evidence sampled into audit', 'success');
    await refreshAuditSamples(currentEvent.id);
    if (window.bootstrap?.Modal && auditSampleModalEl) {
      window.bootstrap.Modal.getOrCreateInstance(auditSampleModalEl).hide();
    }
  } catch (e) {
    toast(auditSampleStatus || status, `Failed: ${String(e)}`, 'danger');
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

async function submitIncident() {
  if (!currentEvent) {
    toast(incidentStatusEl || status, 'Event not loaded yet.', 'warning');
    return;
  }
  const title = _sanitizePlainText(incidentTitleEl?.value || '', 256);
  const text = _sanitizePlainText(incidentTextEl?.value || '', 5000);
  if (!title) {
    toast(incidentStatusEl || status, 'Title is required.', 'warning');
    return;
  }
  const btn = incidentSubmitBtn;
  const prev = btn?.textContent || '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Creating…';
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
    toast(incidentStatusEl || status, `Failed to create incident: ${String(e)}`, 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prev || 'Create Incident';
    }
  }
}

document.getElementById('btnCreateIncident')?.addEventListener('click', openIncidentModal);
incidentTextEl?.addEventListener('input', updateIncidentTextCount);
incidentSubmitBtn?.addEventListener('click', submitIncident);

// ------------------------------------------------------------------
// Questions (auditor Q&A)
// ------------------------------------------------------------------

const questionsEl = document.getElementById('questions');
const btnAskQuestion = document.getElementById('btnAskQuestion');

const questionModalEl = document.getElementById('questionModal');
const questionBodyEl = document.getElementById('questionBody');
const questionAttachmentsEl = document.getElementById('questionAttachments');
const questionSubmitBtn = document.getElementById('questionSubmit');
const questionStatusEl = document.getElementById('questionStatus');

let questionModal = null;
try {
  if (questionModalEl && window.bootstrap?.Modal) {
    questionModal = new bootstrap.Modal(questionModalEl);
  }
} catch {}

const focusThreadId = String(qs('thread') || '').trim();
let didFocusThread = false;

function _qStatusBadge(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'answered') return `<span class="badge text-bg-success">answered</span>`;
  if (s === 'reviewing') return `<span class="badge text-bg-info">reviewing</span>`;
  return `<span class="badge text-bg-warning">unanswered</span>`;
}

function _renderAttLinks(atts) {
  const arr = Array.isArray(atts) ? atts : [];
  if (arr.length === 0) return '';
  const html = arr
    .map((a) => {
      const href = a?.download_url ? String(a.download_url) : '';
      if (!href) return '';
      const name = a?.filename
        ? esc(a.filename)
        : `attachment-${String(a?.artifact_id || '').slice(0, 8)}`;
      return `<a class="mono" href="${esc(href)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-paperclip"></i> ${name}</a>`;
    })
    .filter(Boolean)
    .join(' ');
  return html ? `<div class="question-attachments small-muted mt-1">${html}</div>` : '';
}

function _renderThread(t) {
  const tid = t?.id || '';
  const createdBy = t?.created_by_username || '';
  const createdAt = t?.created_at ? fmtTs(t.created_at) : '—';
  const updatedAt = t?.updated_at ? fmtTs(t.updated_at) : '—';
  const statusVal = String(t?.status || 'unanswered').toLowerCase();
  const posts = Array.isArray(t?.posts) ? t.posts : [];

  const isAdmin = !!me?.is_admin;
  const isAuthor = me?.user && createdBy && String(me.user) === String(createdBy);
  const canReply = isAdmin || isAuthor;

  const statusCtl = isAdmin
    ? `
        <select class="form-select form-select-sm" style="width:auto;" data-action="set-status" data-thread="${esc(tid)}">
          <option value="unanswered" ${statusVal === 'unanswered' ? 'selected' : ''}>unanswered</option>
          <option value="reviewing" ${statusVal === 'reviewing' ? 'selected' : ''}>reviewing</option>
          <option value="answered" ${statusVal === 'answered' ? 'selected' : ''}>answered</option>
        </select>
      `
    : '';
  const deleteCtl = canDeleteQuestions
    ? `<button class="btn btn-sm btn-outline-danger" type="button" data-action="delete-question" data-thread="${esc(tid)}">Delete</button>`
    : '';

  const postsHtml = posts
    .map((p) => {
      const who = p?.author_username || '—';
      const when = p?.created_at ? fmtTs(p.created_at) : '—';
      const body = esc(p?.body || '').replace(/\n/g, '<br>');
      const atts = _renderAttLinks(p?.attachments);
      return `
          <div class="question-post">
            <div class="question-post-meta">
              <div class="small-muted"><span class="fw-bold">${esc(who)}</span> • ${esc(when)}</div>
            </div>
            <div class="mt-2">${body}</div>
            ${atts}
          </div>
        `;
    })
    .join('');

  const replyHtml = canReply
    ? `
        <div class="mt-3 no-print">
          <div class="small-muted mb-1">Reply</div>
          <textarea class="form-control" rows="3" id="replyBody-${esc(tid)}" placeholder="Write a response…"></textarea>
          <div class="d-flex flex-wrap gap-2 align-items-center mt-2">
            <input class="form-control form-control-sm" style="max-width: 360px;" type="file" multiple id="replyFiles-${esc(tid)}">
            <button class="btn btn-sm btn-primary" data-action="reply" data-thread="${esc(tid)}">Send reply</button>
          </div>
          <div class="form-text">Only admins or the original author can reply.</div>
        </div>
      `
    : `<div class="small-muted mt-3">Only admins or the original author can reply.</div>`;

  return `
        <div class="question-thread" id="thread-${esc(tid)}">
          <div class="question-header">
            <div>
              <div class="question-title">Thread by <span class="mono">${esc(createdBy || '—')}</span></div>
              <div class="small-muted">Created ${esc(createdAt)} • Updated ${esc(updatedAt)}</div>
            </div>
            <div class="d-flex flex-wrap gap-2 align-items-center">
              ${_qStatusBadge(statusVal)}
              ${statusCtl}
              ${deleteCtl}
            </div>
          </div>
          ${postsHtml || `<div class="small-muted mt-2">No posts yet.</div>`}
          ${replyHtml}
        </div>
      `;
}

function _renderThreads(threads) {
  const arr = Array.isArray(threads) ? threads : [];
  if (!questionsEl) return;
  if (arr.length === 0) {
    questionsEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
    return;
  }
  questionsEl.innerHTML = arr.map(_renderThread).join('');
}

async function refreshQuestions(eventId) {
  if (!questionsEl) return;
  try {
    const res = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}/questions`);
    _renderThreads(res?.threads || []);
    // Mark questions seen for the thread author so their bell clears immediately.
    if (!PRINT_MODE && !me?.is_admin) {
      try {
        await apiPost(`/api/v1/events/${encodeURIComponent(eventId)}/questions/mark-seen`, {});
      } catch {
        /* ignore */
      }
    }
    if (focusThreadId && !didFocusThread) {
      const el = document.getElementById(`thread-${focusThreadId}`);
      if (el) {
        didFocusThread = true;
        el.classList.add('question-thread-focus');
        try {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch {
          el.scrollIntoView();
        }
      }
    }
  } catch (e) {
    questionsEl.innerHTML = `<div class="text-danger small">Failed to load questions: ${esc(String(e))}</div>`;
  }
}

if (btnAskQuestion) {
  const canQuestion = !!(me?.is_admin || me?.can_question_events);
  btnAskQuestion.style.display = canQuestion ? '' : 'none';
  btnAskQuestion.addEventListener('click', () => {
    if (!questionModal) return;
    if (questionStatusEl) {
      questionStatusEl.style.display = 'none';
      questionStatusEl.textContent = '';
    }
    if (questionBodyEl) questionBodyEl.value = '';
    if (questionAttachmentsEl) questionAttachmentsEl.value = '';
    questionModal.show();
  });
}

questionSubmitBtn?.addEventListener('click', async () => {
  const e = currentEvent;
  if (!e?.id) {
    toast(status, 'Event not loaded yet', 'warning');
    return;
  }
  const body = String(questionBodyEl?.value || '').trim();
  if (!body) {
    toast(status, 'Please enter a question', 'warning');
    return;
  }

  if (questionStatusEl) {
    questionStatusEl.style.display = '';
    questionStatusEl.textContent = 'Submitting…';
  }

  const fd = new FormData();
  fd.append('body', body);
  const files = questionAttachmentsEl?.files ? Array.from(questionAttachmentsEl.files) : [];
  for (const f of files.slice(0, 10)) {
    fd.append('attachments', f, f.name);
  }

  try {
    await apiPostForm(`/api/v1/events/${encodeURIComponent(e.id)}/questions`, fd);
    questionModal?.hide();
    toast(status, 'Question submitted', 'success', 2500);
    await refreshQuestions(e.id);
  } catch (err) {
    const msg = String(err);
    if (questionStatusEl) {
      questionStatusEl.style.display = '';
      questionStatusEl.textContent = msg;
    } else {
      toast(status, msg, 'danger');
    }
  }
});

questionsEl?.addEventListener('click', async (ev) => {
  const delBtn = ev.target?.closest?.('[data-action="delete-question"]');
  if (delBtn) {
    const tid = delBtn.getAttribute('data-thread');
    if (!tid || !canDeleteQuestions) return;
    if (!confirm('Delete this question thread and all replies?')) return;
    delBtn.disabled = true;
    try {
      await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
      toast(status, 'Question deleted', 'success', 2000);
      if (currentEvent?.id) await refreshQuestions(currentEvent.id);
    } catch (err) {
      toast(status, String(err), 'danger');
      delBtn.disabled = false;
    }
    return;
  }

  const btn = ev.target?.closest?.('[data-action="reply"]');
  if (!btn) return;
  const tid = btn.getAttribute('data-thread');
  if (!tid) return;

  const bodyEl = document.getElementById(`replyBody-${tid}`);
  const fileEl = document.getElementById(`replyFiles-${tid}`);
  const body = String(bodyEl?.value || '').trim();
  if (!body) {
    toast(status, 'Please enter a reply', 'warning');
    return;
  }

  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = 'Sending…';

  const fd = new FormData();
  fd.append('body', body);
  const files = fileEl?.files ? Array.from(fileEl.files) : [];
  for (const f of files.slice(0, 10)) {
    fd.append('attachments', f, f.name);
  }

  try {
    await apiPostForm(`/api/v1/questions/${encodeURIComponent(tid)}/posts`, fd);
    toast(status, 'Reply sent', 'success', 2000);
    if (currentEvent?.id) await refreshQuestions(currentEvent.id);
    if (bodyEl) bodyEl.value = '';
    if (fileEl) fileEl.value = '';
  } catch (err) {
    toast(status, String(err), 'danger');
  } finally {
    btn.disabled = false;
    btn.textContent = prev || 'Send reply';
  }
});

questionsEl?.addEventListener('change', async (ev) => {
  const sel = ev.target?.closest?.('[data-action="set-status"]');
  if (!sel) return;
  const tid = sel.getAttribute('data-thread');
  const newStatus = String(sel.value || '').trim();
  if (!tid || !newStatus) return;
  try {
    await apiPatch(`/api/v1/admin/questions/${encodeURIComponent(tid)}`, { status: newStatus });
    if (currentEvent?.id) await refreshQuestions(currentEvent.id);
  } catch (err) {
    toast(status, String(err), 'danger');
  }
});

load();
