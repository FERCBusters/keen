// A side panel with the essentials of one event: what happened, which controls
// it maps to and why, and the structured fields that identify it. The full
// event page remains one link away.
import { apiGet, esc, fmtTs, sourceBadgeHtml, upstreamLinkHtml, withFramework } from '/app.js';
import { observedPayloadFields } from '/pages/evidence-fields.js';

export const PANEL_FIELD_LIMIT = 12;

function detail(label, value) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  return `<dt class="col-4 small-muted fw-normal">${esc(label)}</dt><dd class="col-8 mb-1 text-break">${esc(text)}</dd>`;
}

// Reference and title only; method, confidence and rationale stay on the
// full event page.
function controlHtml(control, framework) {
  const href = withFramework(
    `/control.html?id=${encodeURIComponent(control.id)}`,
    control.framework || framework,
  );
  return `
    <li class="mb-1" data-panel-control="${esc(control.ref)}">
      <a class="badge badge-soft me-1" href="${href}">${esc(control.ref)}</a>
      <span>${esc(control.title || '')}</span>
    </li>`;
}

export function createEventPanel({ framework, getSourceMeta = () => ({}) }) {
  const panel = document.getElementById('eventPanel');
  if (!panel) return { open: () => {} };
  const body = panel.querySelector('[data-panel-body]');
  const title = panel.querySelector('[data-panel-title]');
  const fullLink = panel.querySelector('[data-panel-full]');
  let current = null;

  function show() {
    const Offcanvas = globalThis.bootstrap?.Offcanvas;
    if (Offcanvas) {
      Offcanvas.getOrCreateInstance(panel).show();
      return;
    }
    panel.classList.add('show');
    panel.style.visibility = 'visible';
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('role', 'dialog');
  }

  function hide() {
    const Offcanvas = globalThis.bootstrap?.Offcanvas;
    if (Offcanvas) {
      Offcanvas.getOrCreateInstance(panel).hide();
      return;
    }
    panel.classList.remove('show');
    panel.style.visibility = '';
  }

  function render(event) {
    const np = event.normalized_payload;
    const fields = observedPayloadFields(np && typeof np === 'object' ? np : {});
    const shown = fields.slice(0, PANEL_FIELD_LIMIT);
    const more = fields.length - shown.length;
    const controls = Array.isArray(event.controls) ? event.controls : [];
    const sourceHref = event.source
      ? withFramework(`/source.html?source=${encodeURIComponent(event.source)}`, framework)
      : '';
    title.textContent = event.summary || '(no summary)';
    body.innerHTML = `
      <div class="d-flex flex-wrap gap-2 align-items-center mb-2">
        ${event.source ? sourceBadgeHtml(event.source, getSourceMeta(), sourceHref) : ''}
        <span class="small-muted">${esc(fmtTs(event.timestamp))}</span>
        ${upstreamLinkHtml(event.source_url, { title: 'Open source event' })}
      </div>
      <dl class="row mb-3" data-panel-details>
        ${detail('System', event.system)}
        ${detail('Actor', event.actor)}
        ${detail('Action', event.action)}
        ${detail('Outcome', event.outcome)}
        ${detail('Severity', event.severity)}
        ${detail('Connection', event.connection_name || event.connection_id)}
        ${detail('Artifacts', (event.artifacts || []).length || '')}
      </dl>
      <h3 class="h6">Mapped controls</h3>
      ${
        controls.length
          ? `<ul class="list-unstyled mb-3" data-panel-controls>${controls
              .map((c) => controlHtml(c, framework))
              .join('')}</ul>`
          : '<p class="small-muted" data-panel-controls>No mapped controls in this framework.</p>'
      }
      <h3 class="h6">Structured fields</h3>
      ${
        shown.length
          ? `<dl class="row mb-0" data-panel-fields>${shown
              .map(
                (f) =>
                  `<dt class="col-5 small-muted fw-normal text-break">${esc(f.path.join(' › '))}</dt><dd class="col-7 mb-1 text-break">${esc(f.value)}</dd>`,
              )
              .join('')}</dl>`
          : '<p class="small-muted" data-panel-fields>No structured fields.</p>'
      }
      ${more > 0 ? `<p class="small-muted mb-0" data-panel-more>${more} more in the full event.</p>` : ''}
    `;
  }

  async function open(eventId) {
    current = eventId;
    const href = withFramework(`/event.html?id=${encodeURIComponent(eventId)}`, framework);
    fullLink.setAttribute('href', href);
    title.textContent = 'Loading…';
    body.innerHTML = '<p class="small-muted">Loading event…</p>';
    show();
    try {
      const event = await apiGet(
        `/api/v1/events/${encodeURIComponent(eventId)}?framework=${encodeURIComponent(framework)}`,
      );
      if (current !== eventId) return;
      render(event);
    } catch (error) {
      if (current !== eventId) return;
      title.textContent = 'Event unavailable';
      body.innerHTML = `<p class="text-danger">Failed to load the event: ${esc(String(error))}</p>`;
    }
  }

  panel.querySelector('[data-panel-close]')?.addEventListener('click', hide);
  return { open, hide };
}
