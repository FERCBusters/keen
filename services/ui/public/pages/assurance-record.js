import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  esc,
  toast,
  errorText,
  safeExternalHref,
  confirmAction,
  withFramework,
  getCurrentFramework,
} from '/app.js';
import { featureEnabled } from './feature-availability.js';
import { workstationPosture } from './workstation-posture.js';
import { mountSwotRelated } from './swot-related.js';
import { canViewStories, mountStoryRelated } from './story-related.js';
import { personQuizzes } from './person-quizzes.js';
import { unsavedGuard } from './unsaved-guard.js';

export async function assuranceRecord(kind) {
  const me = await initNavbar(),
    person = kind === 'person',
    feature = person ? 'people' : 'vendors';
  const $ = (id) => document.getElementById(id),
    params = new URLSearchParams(location.search);
  const id = params.get('id'),
    base = '/api/v1/isms/' + feature,
    framework = getCurrentFramework();
  const back = withFramework('/assurance.html?tab=' + feature, framework);
  $('registerLink').href = back;
  const manage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
  const canPosture =
    person && featureEnabled(me, 'evidence') && !!(me?.is_admin || me?.can_view_events);
  const canQuizzes =
    person && featureEnabled(me, 'quizzes') && !!(me?.is_admin || me?.can_manage_quizzes);
  const enabled = featureEnabled(me, feature);
  let record = null,
    metadata = null,
    editedAssurance = null,
    relatedLoaded = false,
    saving = false;
  const form = $(kind + 'Form');
  const guard = unsavedGuard({ root: form, hint: $('recordDirty') });
  const assuranceGuard = person
    ? unsavedGuard({ root: $('assuranceForm'), hint: $('assuranceDirty') })
    : null;
  const posture = person ? workstationPosture($('workstationPosture'), canPosture) : null;
  const tabButtons = [...document.querySelectorAll('[data-record-tab]')];
  if (person) $('tab-posture').parentElement.hidden = !canPosture;
  $('tab-assets').parentElement.hidden = !featureEnabled(me, 'assets');
  function activate(tab, update = true) {
    const button = $('tab-' + tab);
    if (!button || button.parentElement.hidden) return;
    for (const item of tabButtons) {
      const on = item === button,
        name = item.dataset.recordTab;
      item.classList.toggle('active', on);
      item.setAttribute('aria-selected', String(on));
      item.tabIndex = on ? 0 : -1;
      $('pane-' + name).classList.toggle('active', on);
      $('pane-' + name).classList.toggle('show', on);
    }
    if (update) {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', tab);
      history.replaceState({}, '', url.pathname + url.search);
    }
    if (record && tab === 'posture') posture.select(record);
    if (record && tab === 'related' && !relatedLoaded) {
      relatedLoaded = true;
      let any = false;
      if (featureEnabled(me, 'swot')) {
        any = true;
        $('recordSwot').hidden = false;
        mountSwotRelated(kind, record.id, $('recordSwot'));
      }
      if (canViewStories(me)) {
        any = true;
        $('recordStories').hidden = false;
        mountStoryRelated({ me, kind, id: record.id, host: $('recordStories') });
      }
      if (canQuizzes) {
        any = true;
        personQuizzes($('recordQuizzes'), record.id).load();
      }
      $('noRelated').hidden = any;
    }
  }
  for (const button of tabButtons) {
    button.onclick = () => activate(button.dataset.recordTab);
    button.onkeydown = (event) => {
      const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      const visible = tabButtons.filter((b) => !b.parentElement.hidden),
        index = visible.indexOf(button);
      const next =
        event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? visible.length - 1
            : (index + (event.key === 'ArrowRight' ? 1 : -1) + visible.length) % visible.length;
      visible[next].focus();
      activate(visible[next].dataset.recordTab);
    };
  }
  function options(element, values, selected, multiple = false) {
    element.replaceChildren();
    if (!multiple) element.add(new Option('None', ''));
    for (const value of values) element.add(new Option(value.name, value.id));
    for (const value of selected.filter(Boolean))
      if (![...element.options].some((o) => o.value === value))
        element.add(
          new Option(record?.username || record?.org_node || 'Previously linked record', value),
        );
    for (const option of element.options) option.selected = selected.includes(option.value);
  }
  function fill() {
    const fields = person
      ? { Name: 'name', Email: 'email', Position: 'position', Notes: 'notes' }
      : { Name: 'name', Description: 'description', Website: 'website', Contact: 'contact' };
    for (const [suffix, key] of Object.entries(fields))
      $(kind + suffix).value = record?.[key] || '';
    if (metadata) {
      if (person) {
        options($('personUser'), metadata.users, [record?.user_id || '']);
        options($('personOrg'), metadata.org_nodes, [record?.org_node_id || '']);
      }
      options($(kind + 'Assets'), metadata.assets, record?.asset_ids || [], true);
      $(kind + 'Assets').parentElement.hidden = !featureEnabled(me, 'assets');
    }
    guard.markClean();
  }
  function renderRecord() {
    $('recordTitle').textContent = record?.name || `New ${kind}`;
    document.title = `Keen - ${record?.name || 'New ' + kind}`;
    $('recordSubtitle').textContent = person
      ? [record?.position, record?.email, record?.org_node].filter(Boolean).join(' · ')
      : record?.contact || '';
    $('editRecord').hidden = !manage || !record;
    $('deleteRecord').hidden = !manage || !record;
    $('recordTabs').hidden = !record;
    const facts = person
      ? [
          ['Name', record?.name],
          ['Email', record?.email],
          ['Position', record?.position],
          ['Keen account', record?.username],
          ['Organisation role', record?.org_node],
          ['Notes', record?.notes],
        ]
      : [
          ['Name', record?.name],
          ['Description', record?.description],
          ['Contact', record?.contact],
          ['Website', record?.website],
        ];
    $('recordFacts').innerHTML =
      `<dl class="row mb-0">${facts.map(([label, value]) => `<dt class="col-sm-3">${esc(label)}</dt><dd class="col-sm-9 text-break" style="white-space:pre-wrap">${esc(value || '—')}</dd>`).join('')}</dl>`;
    $('recordAssets').innerHTML =
      (record?.assets || [])
        .map(
          (a) =>
            `<p><a href="${esc(withFramework('/isms-asset.html?id=' + encodeURIComponent(a.id), framework))}">${esc(a.name)}</a></p>`,
        )
        .join('') || '<p class="small-muted mb-0">No assets linked.</p>';
    if (person) {
      $('addAssurance').hidden = !manage;
      renderAssurances();
    }
  }
  async function edit() {
    if (!manage || !enabled || saving) return;
    try {
      metadata = metadata || (await apiGet('/api/v1/isms/assurance/meta'));
      fill();
      $('recordEditor').hidden = false;
      $('recordFacts').hidden = true;
      activate('details');
      $(kind + 'Name').focus();
    } catch (error) {
      toast($('status'), `Could not open the editor: ${errorText(error)}`, 'danger');
    }
  }
  $('editRecord').onclick = edit;
  $('new' + kind[0].toUpperCase() + kind.slice(1)).onclick = async () => {
    if (
      guard.isDirty() &&
      !(await confirmAction({
        title: 'Discard changes?',
        message: 'Discard the unsaved details?',
        confirmLabel: 'Discard',
      }))
    )
      return;
    guard.markClean();
    if (!record) {
      location.assign(back);
      return;
    }
    fill();
    $('recordEditor').hidden = true;
    $('recordFacts').hidden = false;
  };
  function selectedAssets() {
    return featureEnabled(me, 'assets')
      ? [...$(kind + 'Assets').selectedOptions].map((o) => o.value)
      : record?.asset_ids || [];
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!manage || !enabled || saving) return;
    const payload = person
      ? {
          name: $('personName').value,
          email: $('personEmail').value,
          position: $('personPosition').value,
          notes: $('personNotes').value,
          user_id: $('personUser').value || null,
          org_node_id: $('personOrg').value || null,
          asset_ids: selectedAssets(),
        }
      : {
          name: $('vendorName').value,
          description: $('vendorDescription').value,
          website: $('vendorWebsite').value,
          contact: $('vendorContact').value,
          asset_ids: selectedAssets(),
        };
    saving = true;
    form.querySelector('[type="submit"]').disabled = true;
    try {
      if (record) await apiPatch(base + '/' + encodeURIComponent(record.id), payload);
      else await apiPost(base, payload);
      guard.markClean();
      location.assign(back);
    } catch (error) {
      toast($('status'), `Could not save ${kind}: ${errorText(error)}`, 'danger');
    } finally {
      saving = false;
      form.querySelector('[type="submit"]').disabled = false;
    }
  });
  $('deleteRecord').onclick = async () => {
    if (!manage || !record || saving) return;
    if (
      !(await confirmAction({
        title: `Delete ${kind}`,
        message: person
          ? 'Delete this person and their assurance records? Linked workstation agents will be unassigned.'
          : 'Delete this vendor? Its assets remain in the asset register.',
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return;
    saving = true;
    try {
      await apiDelete(base + '/' + encodeURIComponent(record.id));
      guard.markClean();
      assuranceGuard?.markClean();
      location.assign(back);
    } catch (error) {
      toast($('status'), `Could not delete ${kind}: ${errorText(error)}`, 'danger');
    } finally {
      saving = false;
    }
  };
  const assuranceFields = {
    assuranceCategory: 'category',
    assuranceName: 'name',
    assuranceStatus: 'status',
    assuranceSource: 'source_system',
    assuranceEvidence: 'evidence_url',
    assuranceCompleted: 'completed_at',
    assuranceExpires: 'expires_at',
    assuranceNotes: 'notes',
  };
  function renderAssurances() {
    $('assuranceRows').innerHTML =
      (record?.assurances || [])
        .map(
          (a) =>
            `<tr><td>${esc(a.category)}</td><td>${esc(a.name)}</td><td>${esc(a.status.replaceAll('_', ' '))}</td><td>${esc(a.source_system || '—')}</td><td>${esc(a.completed_at || '—')}</td><td>${esc(a.expires_at || '—')}</td><td>${safeExternalHref(a.evidence_url) ? `<a href="${esc(safeExternalHref(a.evidence_url))}" target="_blank" rel="noopener noreferrer">View</a>` : '—'}</td><td>${manage ? `<button type="button" class="btn btn-sm btn-outline-primary" data-edit-assurance="${esc(a.id)}">Edit</button> <button type="button" class="btn btn-sm btn-outline-danger" data-delete-assurance="${esc(a.id)}">Delete</button>` : ''}</td></tr>`,
        )
        .join('') ||
      '<tr><td colspan="8" class="small-muted">No assurance records for this person.</td></tr>';
  }
  async function assuranceEdit(row = null) {
    if (!manage || !record || saving) return;
    if (
      assuranceGuard.isDirty() &&
      !(await confirmAction({
        title: 'Discard changes?',
        message: 'Discard the unsaved assurance record?',
        confirmLabel: 'Discard',
      }))
    )
      return;
    editedAssurance = row;
    $('assuranceForm').reset();
    for (const [field, key] of Object.entries(assuranceFields))
      $(field).value = row?.[key] || (key === 'status' ? 'pending' : '');
    $('assuranceFormTitle').textContent = row ? 'Edit assurance record' : 'Add assurance record';
    $('assuranceEditor').hidden = false;
    assuranceGuard.markClean();
    $('assuranceCategory').focus();
  }
  if (person) {
    $('addAssurance').onclick = () => assuranceEdit();
    $('newAssurance').onclick = async () => {
      if (
        assuranceGuard.isDirty() &&
        !(await confirmAction({
          title: 'Discard changes?',
          message: 'Discard the unsaved assurance record?',
          confirmLabel: 'Discard',
        }))
      )
        return;
      assuranceGuard.markClean();
      $('assuranceEditor').hidden = true;
      editedAssurance = null;
    };
    $('assuranceRows').onclick = async (event) => {
      const editButton = event.target.closest('[data-edit-assurance]');
      if (editButton) {
        assuranceEdit(record.assurances.find((a) => a.id === editButton.dataset.editAssurance));
        return;
      }
      const deleting = event.target.closest('[data-delete-assurance]');
      if (!deleting || !manage || saving) return;
      if (
        !(await confirmAction({
          title: 'Delete assurance record',
          message: 'Delete this assurance record?',
          confirmLabel: 'Delete',
          danger: true,
        }))
      )
        return;
      saving = true;
      try {
        await apiDelete(
          `${base}/${encodeURIComponent(record.id)}/assurances/${encodeURIComponent(deleting.dataset.deleteAssurance)}`,
        );
        record = await apiGet(base + '/' + encodeURIComponent(record.id));
        renderAssurances();
        if (editedAssurance?.id === deleting.dataset.deleteAssurance) {
          assuranceGuard.markClean();
          $('assuranceEditor').hidden = true;
          editedAssurance = null;
        }
      } catch (error) {
        toast($('status'), errorText(error), 'danger');
      } finally {
        saving = false;
      }
    };
    $('assuranceForm').onsubmit = async (event) => {
      event.preventDefault();
      if (!manage || !record || saving) return;
      const payload = Object.fromEntries(
        Object.entries(assuranceFields).map(([field, key]) => [
          key,
          $(field).value || (['completed_at', 'expires_at'].includes(key) ? null : ''),
        ]),
      );
      const url = `${base}/${encodeURIComponent(record.id)}/assurances`;
      saving = true;
      $('assuranceForm').querySelector('[type="submit"]').disabled = true;
      try {
        if (editedAssurance)
          await apiPatch(url + '/' + encodeURIComponent(editedAssurance.id), payload);
        else await apiPost(url, payload);
        assuranceGuard.markClean();
        $('assuranceEditor').hidden = true;
        editedAssurance = null;
        record = await apiGet(base + '/' + encodeURIComponent(record.id));
        renderAssurances();
        toast($('status'), 'Assurance record saved.', 'success');
      } catch (error) {
        toast($('status'), `Could not save assurance: ${errorText(error)}`, 'danger');
      } finally {
        saving = false;
        $('assuranceForm').querySelector('[type="submit"]').disabled = false;
      }
    };
  }
  async function load() {
    $('retryRecord').hidden = true;
    if (!enabled) {
      $('recordSubtitle').textContent = 'This section is disabled.';
      return;
    }
    if (!id) {
      if (!manage) {
        $('recordSubtitle').textContent = 'You do not have permission to add records.';
        return;
      }
      renderRecord();
      await edit();
      return;
    }
    try {
      record = await apiGet(base + '/' + encodeURIComponent(id));
      renderRecord();
      const wanted = params.get('tab');
      activate(
        tabButtons.some((b) => b.dataset.recordTab === wanted && !b.parentElement.hidden)
          ? wanted
          : person
            ? canPosture
              ? 'posture'
              : 'assurance'
            : 'details',
        false,
      );
      if (params.get('edit') === '1') await edit();
    } catch (error) {
      $('recordSubtitle').textContent = 'Record unavailable.';
      toast($('status'), `Could not load ${kind}: ${errorText(error)}`, 'danger');
      $('retryRecord').hidden = false;
    }
  }
  $('retryRecord').onclick = load;
  await load();
}
