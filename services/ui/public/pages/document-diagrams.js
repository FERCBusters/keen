// MIDENA diagrams shown with a policy or process document: the drawing of
// each captured version as an image, and, for ISMS managers, finding a
// MIDENA diagram to add, refreshing one to MIDENA's current version and
// removing one. Drawings arrive sanitised by Keen and are shown only through
// <img>, which runs no script whatever the markup holds.
import {
  apiDelete,
  apiGet,
  apiPost,
  confirmAction,
  errorText,
  fmtTs,
  safeExternalHref,
  toast,
  userDisplayName,
} from '/app.js';

const $ = (id) => document.getElementById(id);

function element(tag, className = '', text = '') {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  return el;
}

function button(label, className) {
  const el = element('button', className, label);
  el.type = 'button';
  return el;
}

export function svgImageSource(svg) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function versionText(item) {
  return item.version_number == null
    ? 'MIDENA version unknown'
    : `MIDENA version ${item.version_number}`;
}

export function mountDocumentDiagrams({
  documentId,
  row,
  me,
  canManage,
  framework,
  statusEl,
  onChange,
}) {
  const card = $('documentDiagramsCard');
  const list = $('documentDiagrams');
  const add = $('addDiagramButton');
  const picker = $('diagramPicker');
  const search = $('diagramSearch');
  const results = $('diagramResults');
  const more = $('diagramMore');
  const manage = !!(canManage && me.midena_enabled);
  const base = `/api/v1/isms/documents/${encodeURIComponent(documentId)}/diagrams`;
  const fw = `framework=${encodeURIComponent(framework)}`;
  let shown = [];
  let query = '';
  let offset = 0;
  let pageSize = 50;

  async function showDrawing(item, image) {
    try {
      const detail = await apiGet(`${base}/${encodeURIComponent(item.id)}`);
      image.src = svgImageSource(detail.svg);
    } catch (err) {
      image.replaceWith(
        element('div', 'small-muted', `The drawing could not be loaded: ${errorText(err)}`),
      );
    }
  }

  function renderItem(item) {
    const figure = element('figure', 'mb-0 border rounded p-2');
    figure.dataset.diagramId = item.id;
    const head = element('div', 'd-flex flex-wrap gap-2 align-items-start justify-content-between');
    const about = element('div');
    about.append(element('div', 'fw-semibold', item.name));
    const captured = [
      versionText(item),
      `captured ${fmtTs(item.captured_at)}`,
      item.captured_by ? `by ${userDisplayName(item.captured_by, 'Former user')}` : '',
    ]
      .filter(Boolean)
      .join(' · ');
    about.append(element('div', 'small-muted', captured));
    const checksum = element('div', 'small-muted text-break', 'SHA-256 ');
    checksum.append(element('code', '', item.sha256));
    about.append(checksum);
    head.append(about);

    const actions = element('div', 'd-flex flex-wrap gap-2 no-print');
    const href = safeExternalHref(item.midena_url || '');
    if (href) {
      const open = element('a', 'btn btn-sm btn-outline-secondary', 'Open in MIDENA');
      open.href = href;
      open.target = '_blank';
      open.rel = 'noopener noreferrer';
      actions.append(open);
    }
    if (manage) {
      const refresh = button('Refresh', 'btn btn-sm btn-outline-primary');
      refresh.addEventListener('click', () => refreshItem(item, refresh));
      const remove = button('Remove', 'btn btn-sm btn-outline-danger');
      remove.addEventListener('click', () => removeItem(item));
      actions.append(refresh, remove);
    }
    head.append(actions);
    figure.append(head);

    const image = element('img', 'img-fluid d-block mt-2 border rounded bg-white');
    image.alt = item.name;
    image.loading = 'lazy';
    figure.append(image);
    if (item.description) {
      figure.append(
        element('figcaption', 'small-muted whitespace-pre-wrap mt-2', item.description),
      );
    }
    showDrawing(item, image);
    return figure;
  }

  function render() {
    card.hidden = !shown.length && !manage;
    add.hidden = !manage;
    if (!shown.length) {
      list.replaceChildren(
        element('p', 'small-muted mb-0', 'No MIDENA diagrams are shown with this document.'),
      );
      return;
    }
    list.replaceChildren(...shown.map(renderItem));
  }

  async function load() {
    const listed = await apiGet(base);
    shown = listed.items;
    render();
  }

  async function changed(message) {
    toast(statusEl, message, 'success');
    await load();
    onChange();
  }

  async function refreshItem(item, control) {
    control.disabled = true;
    try {
      const updated = await apiPost(`${base}/${encodeURIComponent(item.id)}/refresh?${fw}`, {});
      await changed(`${updated.name}: ${versionText(updated)} captured.`);
    } catch (err) {
      control.disabled = false;
      toast(statusEl, `Could not refresh the diagram: ${errorText(err)}`, 'danger');
    }
  }

  async function removeItem(item) {
    const confirmed = await confirmAction({
      title: 'Remove diagram',
      message: `Stop showing "${item.name}" with this document? The diagram stays in MIDENA.`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!confirmed) return;
    try {
      await apiDelete(`${base}/${encodeURIComponent(item.id)}?${fw}`);
      await changed(`${item.name} removed from this document.`);
    } catch (err) {
      toast(statusEl, `Could not remove the diagram: ${errorText(err)}`, 'danger');
    }
  }

  async function attach(found, control) {
    control.disabled = true;
    try {
      const added = await apiPost(`${base}?${fw}`, { diagram_uuid: found.uuid });
      await changed(`${added.name} added to this document.`);
      control.textContent = 'Shown';
    } catch (err) {
      control.disabled = false;
      toast(statusEl, `Could not add the diagram: ${errorText(err)}`, 'danger');
    }
  }

  function renderResult(found) {
    const item = element(
      'div',
      'list-group-item d-flex gap-2 align-items-center justify-content-between px-0',
    );
    const about = element('div');
    about.append(element('div', 'fw-semibold', found.name));
    about.append(
      element(
        'div',
        'small-muted',
        [
          found.diagram_type,
          found.folder_name,
          versionText(found),
          found.updated_at ? `updated ${fmtTs(found.updated_at)}` : '',
        ]
          .filter(Boolean)
          .join(' · '),
      ),
    );
    const already = shown.some((row) => row.diagram_uuid === found.uuid);
    const choose = button(already ? 'Shown' : 'Add', 'btn btn-sm btn-outline-primary');
    choose.disabled = already;
    choose.addEventListener('click', () => attach(found, choose));
    item.append(about, choose);
    return item;
  }

  async function find(next = 0) {
    offset = next;
    if (!next) results.replaceChildren(element('div', 'small-muted', 'Searching MIDENA'));
    try {
      const page = await apiGet(
        `/api/v1/isms/midena-diagrams?q=${encodeURIComponent(query)}&offset=${next}`,
      );
      pageSize = page.limit;
      const rows = page.items.map(renderResult);
      if (next) results.append(...rows);
      else if (rows.length) results.replaceChildren(...rows);
      else results.replaceChildren(element('div', 'small-muted', 'No MIDENA diagrams match.'));
      more.hidden = page.offset + page.items.length >= page.total;
    } catch (err) {
      more.hidden = true;
      results.replaceChildren(
        element('div', 'small text-danger', `MIDENA could not be searched: ${errorText(err)}`),
      );
    }
  }

  add.addEventListener('click', () => {
    picker.hidden = !picker.hidden;
    add.setAttribute('aria-expanded', String(!picker.hidden));
    if (!picker.hidden) {
      search.focus();
      find(0);
    }
  });
  picker.addEventListener('submit', (ev) => {
    ev.preventDefault();
    query = search.value.trim();
    find(0);
  });
  more.addEventListener('click', () => find(offset + pageSize));

  // Only a document that shows diagrams needs them listed.
  if (!(row.diagrams || []).length) {
    render();
    return Promise.resolve();
  }
  card.hidden = false;
  add.hidden = !manage;
  list.replaceChildren(element('div', 'small-muted', 'Loading diagrams'));
  return load().catch((err) => {
    list.replaceChildren(
      element('div', 'small-muted', `Diagrams could not be loaded: ${errorText(err)}`),
    );
    card.hidden = false;
  });
}
