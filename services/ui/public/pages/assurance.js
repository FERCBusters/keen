import {
  safeExternalHref,
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  confirmAction,
  esc,
  toast,
} from '/app.js';
import { featureEnabled } from '/pages/feature-availability.js';

const me = await initNavbar();
if (!featureEnabled(me, 'people') && featureEnabled(me, 'vendors')) {
  document.getElementById('peoplePane')?.classList.remove('show', 'active');
  document.getElementById('vendorsPane')?.classList.add('show', 'active');
}
const manage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const $ = (id) => document.getElementById(id);
const base = '/api/v1/isms';
let meta = { users: [], org_nodes: [], assets: [] };
let people = [],
  vendors = [],
  selectedPerson = null,
  selectedVendor = null,
  selectedAssurance = null;

function show(message, kind = 'success') {
  toast($('status'), message, kind);
}
function errorMessage(err) {
  return String(err?.message || err);
}
function options(id, values, blank = true) {
  $(id).innerHTML =
    (blank ? '<option value="">None</option>' : '') +
    values.map((row) => `<option value="${esc(row.id)}">${esc(row.name)}</option>`).join('');
}
function selectedIds(id) {
  return Array.from($(id).selectedOptions)
    .map((option) => option.value)
    .filter(Boolean);
}
function selectIds(id, ids) {
  const chosen = new Set(ids || []);
  for (const option of $(id).options) option.selected = chosen.has(option.value);
}
function assetNames(ids) {
  return (
    (ids || [])
      .map((id) => meta.assets.find((asset) => asset.id === id)?.name)
      .filter(Boolean)
      .join(', ') || 'No assets linked'
  );
}
function personById(id) {
  return people.find((person) => person.id === id);
}

async function reload() {
  try {
    const [metadata, personData, vendorData] = await Promise.all([
      apiGet(`${base}/assurance/meta`),
      featureEnabled(me, 'people') ? apiGet(`${base}/people`) : { items: [] },
      featureEnabled(me, 'vendors') ? apiGet(`${base}/vendors`) : { items: [] },
    ]);
    meta = metadata;
    people = personData.items;
    vendors = vendorData.items;
    options('personUser', meta.users);
    options('personOrg', meta.org_nodes);
    options('personAssets', meta.assets, false);
    options('vendorAssets', meta.assets, false);
    if (selectedPerson) {
      selectedPerson = personById(selectedPerson.id) || null;
      if (selectedPerson) fillPerson(selectedPerson);
      else clearPerson();
    }
    if (selectedVendor) {
      selectedVendor = vendors.find((v) => v.id === selectedVendor.id) || null;
      if (selectedVendor) fillVendor(selectedVendor);
      else clearVendor();
    }
    render();
  } catch (e) {
    show(`Could not load assurance register: ${errorMessage(e)}`, 'danger');
  }
}

function render() {
  const query = $('personSearch').value.trim().toLowerCase();
  const matching = people.filter((p) =>
    `${p.name} ${p.email} ${p.position} ${p.username || ''}`.toLowerCase().includes(query),
  );
  $('peopleList').innerHTML = matching.length
    ? matching
        .map(
          (p) => `
    <div class="list-group-item d-flex justify-content-between align-items-start gap-2">
      <div><strong>${esc(p.name)}</strong> ${p.username ? `<span class="badge text-bg-light">${esc(p.username)}</span>` : ''}
        <div class="small-muted">${esc(p.position || p.org_node || 'No role recorded')} · ${esc(assetNames(p.asset_ids))} · ${p.assurances.length} assurance records</div></div>
      <div class="d-flex gap-1">${manage ? `<button type="button" class="btn btn-sm btn-outline-primary" data-edit-person="${esc(p.id)}">Edit</button><button type="button" class="btn btn-sm btn-outline-danger" data-delete-person="${esc(p.id)}">Delete</button>` : ''}
        <a class="btn btn-sm btn-outline-secondary" href="/swot.html?target_type=person&target_id=${encodeURIComponent(p.id)}">SWOT</a><button type="button" class="btn btn-sm btn-outline-secondary" data-view-person="${esc(p.id)}">Assurance</button></div>
    </div>`,
        )
        .join('')
    : '<p class="small-muted mb-0">No matching people yet.</p>';
  $('vendorsList').innerHTML = vendors.length
    ? vendors
        .map(
          (v) => `
    <div class="list-group-item d-flex justify-content-between align-items-start gap-2"><div><strong>${esc(v.name)}</strong>
      <div class="small-muted">${esc(v.description || '')}</div><div class="small-muted">${esc(assetNames(v.asset_ids))}</div></div>
      <div class="d-flex gap-1 align-self-start">${manage ? `<button type="button" class="btn btn-sm btn-outline-primary" data-edit-vendor="${esc(v.id)}">Edit</button><button type="button" class="btn btn-sm btn-outline-danger" data-delete-vendor="${esc(v.id)}">Delete</button>` : ''}<a class="btn btn-sm btn-outline-secondary" href="/swot.html?target_type=vendor&target_id=${encodeURIComponent(v.id)}">SWOT</a></div></div>`,
        )
        .join('')
    : '<p class="small-muted mb-0">No vendors yet.</p>';
  renderAssurances();
}

function renderAssurances() {
  $('assuranceCard').style.display = selectedPerson ? '' : 'none';
  if (!selectedPerson) return;
  $('assuranceTitle').textContent = `Personnel assurance · ${selectedPerson.name}`;
  $('assuranceRows').innerHTML = selectedPerson.assurances.length
    ? selectedPerson.assurances
        .map(
          (a) => `
    <tr><td>${esc(a.category)}</td><td>${esc(a.name)}</td><td>${esc(a.status.replaceAll('_', ' '))}</td>
    <td>${esc(a.source_system || '-')}</td><td>${esc(a.completed_at || '-')}</td><td>${esc(a.expires_at || '-')}</td>
    <td>${a.evidence_url ? `<a href="${esc(safeExternalHref(a.evidence_url) || '#')}" target="_blank" rel="noopener noreferrer">View</a>` : '-'}</td>
    <td>${manage ? `<button type="button" class="btn btn-sm btn-outline-primary" data-edit-assurance="${esc(a.id)}">Edit</button> <button type="button" class="btn btn-sm btn-outline-danger" data-delete-assurance="${esc(a.id)}">Delete</button>` : ''}</td></tr>`,
        )
        .join('')
    : '<tr><td colspan="8" class="small-muted">No assurance records for this person.</td></tr>';
}

function clearPerson() {
  selectedPerson = null;
  $('personForm').reset();
  selectIds('personAssets', []);
  $('personFormTitle').textContent = 'Add a person';
  renderAssurances();
}
function fillPerson(person) {
  showRelated('person', person);
  selectedPerson = person;
  $('personFormTitle').textContent = `Edit ${person.name}`;
  $('personName').value = person.name;
  $('personEmail').value = person.email;
  $('personPosition').value = person.position;
  $('personNotes').value = person.notes;
  $('personUser').value = person.user_id || '';
  $('personOrg').value = person.org_node_id || '';
  selectIds('personAssets', person.asset_ids);
  renderAssurances();
}
function clearVendor() {
  selectedVendor = null;
  $('vendorForm').reset();
  selectIds('vendorAssets', []);
  $('vendorFormTitle').textContent = 'Add a vendor';
}
function fillVendor(v) {
  showRelated('vendor', v);
  selectedVendor = v;
  $('vendorFormTitle').textContent = `Edit ${v.name}`;
  $('vendorName').value = v.name;
  $('vendorDescription').value = v.description;
  $('vendorWebsite').value = v.website;
  $('vendorContact').value = v.contact;
  selectIds('vendorAssets', v.asset_ids);
}
function clearAssurance() {
  selectedAssurance = null;
  $('assuranceForm').reset();
}
function fillAssurance(a) {
  selectedAssurance = a;
  for (const [id, key] of Object.entries({
    assuranceCategory: 'category',
    assuranceName: 'name',
    assuranceStatus: 'status',
    assuranceSource: 'source_system',
    assuranceEvidence: 'evidence_url',
    assuranceCompleted: 'completed_at',
    assuranceExpires: 'expires_at',
    assuranceNotes: 'notes',
  }))
    $(id).value = a[key] || '';
}

$('personSearch').addEventListener('input', render);
$('newPerson').addEventListener('click', clearPerson);
$('newVendor').addEventListener('click', clearVendor);
$('newAssurance').addEventListener('click', clearAssurance);
$('peopleList').addEventListener('click', async (event) => {
  const deleting = event.target.closest('[data-delete-person]');
  if (deleting && manage) {
    if (
      !(await confirmAction({
        title: 'Delete person',
        message: 'Delete this person and their assurance records?',
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return;
    try {
      await apiDelete(`${base}/people/${deleting.dataset.deletePerson}`);
      clearPerson();
      await reload();
      show('Person deleted.');
    } catch (e) {
      show(`Could not delete person: ${errorMessage(e)}`, 'danger');
    }
    return;
  }
  const btn = event.target.closest('[data-edit-person], [data-view-person]');
  if (!btn) return;
  const person = personById(btn.dataset.editPerson || btn.dataset.viewPerson);
  if (!person) return;
  fillPerson(person);
  clearAssurance();
  (btn.dataset.editPerson ? $('personForm') : $('assuranceCard')).scrollIntoView({
    behavior: 'smooth',
    block: 'start',
  });
});
$('vendorsList').addEventListener('click', async (event) => {
  const deleting = event.target.closest('[data-delete-vendor]');
  if (deleting && manage) {
    if (
      !(await confirmAction({
        title: 'Delete vendor',
        message:
          'Delete this vendor from the assurance register? Assets will remain in the asset register.',
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return;
    try {
      await apiDelete(`${base}/vendors/${deleting.dataset.deleteVendor}`);
      clearVendor();
      await reload();
      show('Vendor deleted.');
    } catch (e) {
      show(`Could not delete vendor: ${errorMessage(e)}`, 'danger');
    }
    return;
  }
  const btn = event.target.closest('[data-edit-vendor]');
  if (!btn) return;
  const vendor = vendors.find((v) => v.id === btn.dataset.editVendor);
  if (vendor) {
    fillVendor(vendor);
    $('vendorForm').scrollIntoView({ behavior: 'smooth' });
  }
});
$('assuranceRows').addEventListener('click', async (event) => {
  const deleting = event.target.closest('[data-delete-assurance]');
  if (deleting && selectedPerson && manage) {
    if (
      !(await confirmAction({
        title: 'Delete assurance record',
        message: 'Delete this assurance record?',
        confirmLabel: 'Delete',
      }))
    )
      return;
    try {
      await apiDelete(
        `${base}/people/${selectedPerson.id}/assurances/${deleting.dataset.deleteAssurance}`,
      );
      clearAssurance();
      await reload();
      show('Assurance record deleted.');
    } catch (e) {
      show(`Could not delete assurance: ${errorMessage(e)}`, 'danger');
    }
    return;
  }
  const btn = event.target.closest('[data-edit-assurance]');
  if (!btn || !selectedPerson) return;
  const assurance = selectedPerson.assurances.find((a) => a.id === btn.dataset.editAssurance);
  if (assurance) {
    fillAssurance(assurance);
    $('assuranceForm').scrollIntoView({ behavior: 'smooth' });
  }
});

$('personForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!manage) return;
  const payload = {
    name: $('personName').value,
    email: $('personEmail').value,
    position: $('personPosition').value,
    notes: $('personNotes').value,
    user_id: $('personUser').value || null,
    org_node_id: $('personOrg').value || null,
    asset_ids: selectedIds('personAssets'),
  };
  try {
    const saved = selectedPerson
      ? await apiPatch(`${base}/people/${selectedPerson.id}`, payload)
      : await apiPost(`${base}/people`, payload);
    selectedPerson = saved;
    await reload();
    show('Person saved.');
  } catch (e) {
    show(`Could not save person: ${errorMessage(e)}`, 'danger');
  }
});
$('vendorForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!manage) return;
  const payload = {
    name: $('vendorName').value,
    description: $('vendorDescription').value,
    website: $('vendorWebsite').value,
    contact: $('vendorContact').value,
    asset_ids: selectedIds('vendorAssets'),
  };
  try {
    const saved = selectedVendor
      ? await apiPatch(`${base}/vendors/${selectedVendor.id}`, payload)
      : await apiPost(`${base}/vendors`, payload);
    selectedVendor = saved;
    await reload();
    show('Vendor saved.');
  } catch (e) {
    show(`Could not save vendor: ${errorMessage(e)}`, 'danger');
  }
});
$('assuranceForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!manage || !selectedPerson) return;
  const payload = {
    category: $('assuranceCategory').value,
    name: $('assuranceName').value,
    status: $('assuranceStatus').value,
    source_system: $('assuranceSource').value,
    evidence_url: $('assuranceEvidence').value,
    completed_at: $('assuranceCompleted').value || null,
    expires_at: $('assuranceExpires').value || null,
    notes: $('assuranceNotes').value,
  };
  try {
    const url = `${base}/people/${selectedPerson.id}/assurances`;
    if (selectedAssurance) await apiPatch(`${url}/${selectedAssurance.id}`, payload);
    else await apiPost(url, payload);
    clearAssurance();
    await reload();
    show('Assurance record saved.');
  } catch (e) {
    show(`Could not save assurance: ${errorMessage(e)}`, 'danger');
  }
});

if (!manage) {
  for (const form of [$('personForm'), $('vendorForm'), $('assuranceForm')])
    form.style.display = 'none';
}
await reload();

import { mountSwotRelated } from '/pages/swot-related.js';
import { canViewStories, mountStoryRelated } from '/pages/story-related.js';
// A chosen person or vendor shows the SWOT items and stories that refer to it.
function showRelated(type, record) {
  let card = document.getElementById('assuranceRelated');
  if (!card) {
    card = document.createElement('section');
    card.id = 'assuranceRelated';
    card.className = 'card mt-3';
    document.querySelector('main').append(card);
  }
  const stories = canViewStories(me);
  card.innerHTML = `<div class="card-header">
      <div class="fw-semibold">Related to ${esc(record.name)}</div>
      <ul class="nav nav-tabs card-header-tabs mt-2" role="tablist">
        <li class="nav-item" role="presentation"><button class="nav-link active" id="assuranceSwotTab" type="button" role="tab" data-bs-toggle="tab" data-bs-target="#assuranceSwot" aria-controls="assuranceSwot" aria-selected="true">SWOT items</button></li>
        ${stories ? '<li class="nav-item" role="presentation"><button class="nav-link" id="assuranceStoriesTab" type="button" role="tab" data-bs-toggle="tab" data-bs-target="#assuranceStories" aria-controls="assuranceStories" aria-selected="false">Stories</button></li>' : ''}
      </ul>
    </div>
    <div class="card-body tab-content">
      <div class="tab-pane fade show active" id="assuranceSwot" role="tabpanel" aria-labelledby="assuranceSwotTab"></div>
      ${stories ? '<div class="tab-pane fade" id="assuranceStories" role="tabpanel" aria-labelledby="assuranceStoriesTab"></div>' : ''}
    </div>`;
  mountSwotRelated(type, record.id, $('assuranceSwot'));
  if (stories)
    mountStoryRelated({
      me,
      kind: type,
      id: record.id,
      host: $('assuranceStories'),
      trigger: $('assuranceStoriesTab'),
    });
}

const swotLinkParams = new URLSearchParams(location.search);
const swotLinkedId = swotLinkParams.get('edit');
if (swotLinkedId) {
  const vendor = vendors.find((v) => v.id === swotLinkedId),
    person = people.find((p) => p.id === swotLinkedId);
  if (vendor) {
    fillVendor(vendor);
    const tab = document.querySelector('[data-bs-target="#vendorsPane"]');
    if (tab && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(tab).show();
  }
  if (person) {
    fillPerson(person);
  }
}
