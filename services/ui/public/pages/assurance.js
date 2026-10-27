import {
  initNavbar,
  apiGet,
  esc,
  toast,
  errorText,
  withFramework,
  getCurrentFramework,
} from '/app.js';
import { featureEnabled } from './feature-availability.js';

const me = await initNavbar(),
  framework = getCurrentFramework();
const $ = (id) => document.getElementById(id);
const manage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const enabled = (kind) => featureEnabled(me, kind);
const params = new URLSearchParams(location.search);
const data = { people: [], vendors: [] },
  offsets = { people: 0, vendors: 0 };
const pageSize = 50;
function activate(kind) {
  if (!enabled(kind)) return;
  for (const name of ['people', 'vendors']) {
    $(name + 'Pane').classList.toggle('show', name === kind);
    $(name + 'Pane').classList.toggle('active', name === kind);
    $(name + 'Tab').classList.toggle('active', name === kind);
    $(name + 'Tab').setAttribute('aria-selected', String(name === kind));
    $(name + 'Tab').tabIndex = name === kind ? 0 : -1;
  }
  const url = new URL(window.location.href);
  url.searchParams.set('tab', kind);
  history.replaceState({}, '', url.pathname + url.search);
}
for (const kind of ['people', 'vendors']) {
  $(kind + 'Tab').parentElement.hidden = !enabled(kind);
  $(kind + 'Tab').onclick = () => activate(kind);
  $(kind + 'Tab').onkeydown = (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const available = ['people', 'vendors'].filter(enabled);
    const other =
      event.key === 'Home'
        ? available[0]
        : event.key === 'End'
          ? available.at(-1)
          : kind === 'people'
            ? 'vendors'
            : 'people';
    if (enabled(other)) {
      activate(other);
      $(other + 'Tab').focus();
    }
  };
  const entity = kind === 'people' ? 'person' : 'vendor';
  $('new' + entity[0].toUpperCase() + entity.slice(1)).hidden = !manage || !enabled(kind);
  $('new' + entity[0].toUpperCase() + entity.slice(1)).href = withFramework(
    '/' + entity + '.html',
    framework,
  );
  $(entity + 'Search').oninput = () => {
    offsets[kind] = 0;
    render(kind);
  };
  document.querySelector(`[data-page-prev="${kind}"]`).onclick = () => {
    offsets[kind] = Math.max(0, offsets[kind] - pageSize);
    render(kind);
  };
  document.querySelector(`[data-page-next="${kind}"]`).onclick = () => {
    offsets[kind] += pageSize;
    render(kind);
  };
}
function render(kind) {
  const person = kind === 'people',
    term = $(person ? 'personSearch' : 'vendorSearch')
      .value.trim()
      .toLowerCase();
  const rows = data[kind].filter((r) =>
    [r.name, r.email, r.position, r.org_node, r.username, r.contact, r.description].some((v) =>
      String(v || '')
        .toLowerCase()
        .includes(term),
    ),
  );
  offsets[kind] = Math.min(
    offsets[kind],
    Math.max(0, Math.ceil(rows.length / pageSize) - 1) * pageSize,
  );
  const visible = rows.slice(offsets[kind], offsets[kind] + pageSize);
  $(kind === 'people' ? 'peopleList' : 'vendorsList').innerHTML =
    visible
      .map(
        (r) =>
          `<tr><td><a class="fw-semibold" href="${esc(withFramework(`/${person ? 'person' : 'vendor'}.html?id=${encodeURIComponent(r.id)}`, framework))}">${esc(r.name)}</a>${person && r.email ? `<div class="small-muted">${esc(r.email)}</div>` : ''}</td>${person ? `<td>${esc(r.position || r.org_node || '—')}</td><td>${esc(r.username || '—')}</td><td>${Number(r.assurances?.length || 0)}</td>` : `<td>${esc(r.contact || '—')}</td><td class="wrap">${esc(r.description || '—')}</td><td>${Number(r.asset_ids?.length || 0)}</td>`}</tr>`,
      )
      .join('') || '<tr><td colspan="4" class="p-4 small-muted">No matching records.</td></tr>';
  $(kind + 'Count').textContent =
    `${rows.length ? offsets[kind] + 1 : 0}–${Math.min(offsets[kind] + pageSize, rows.length)} of ${rows.length}`;
  document.querySelector(`[data-page-prev="${kind}"]`).disabled = offsets[kind] === 0;
  document.querySelector(`[data-page-next="${kind}"]`).disabled =
    offsets[kind] + pageSize >= rows.length;
}
async function load() {
  $('retryRegister').hidden = true;
  try {
    await Promise.all(
      ['people', 'vendors'].map(async (kind) => {
        data[kind] = enabled(kind) ? (await apiGet('/api/v1/isms/' + kind)).items : [];
        render(kind);
      }),
    );
    const legacy = params.get('edit');
    if (legacy) {
      const kind = data.people.some((r) => r.id === legacy)
        ? 'person'
        : data.vendors.some((r) => r.id === legacy)
          ? 'vendor'
          : null;
      if (kind)
        location.replace(
          withFramework(`/${kind}.html?id=${encodeURIComponent(legacy)}`, framework),
        );
    }
  } catch (error) {
    toast($('status'), `Could not load the register: ${errorText(error)}`, 'danger');
    $('retryRegister').hidden = false;
  }
}
$('retryRegister').onclick = load;
activate(
  params.get('tab') === 'vendors' && enabled('vendors')
    ? 'vendors'
    : enabled('people')
      ? 'people'
      : 'vendors',
);
await load();
