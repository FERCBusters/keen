import {apiGet, apiPost} from '/app.js';

// Both administration views use the same impact preview and confirmation.
export async function confirmEvidenceRemoval(item, {onRemoved, onError}) {
  const el = id => document.getElementById(`hub-${id}`);
  const url = '/api/v1/admin/evidence-connections/' + encodeURIComponent(item.key);
  const plan = await apiGet(url + '/removal');
  if (plan.blocked) throw new Error(plan.blocked);
  const dialog = el('confirm');
  const detail = el('impact');
  detail.replaceChildren();
  const individual = plan.kind === 'collector';
  const action = individual ? 'Delete API ingester' : plan.kind === 'builtin' ? 'Clear connection' : 'Delete connection';
  el('confirm-heading').textContent = action;
  el('delete').textContent = action;
  const summary = document.createElement('p');
  summary.textContent = `${plan.name}: remove ${plan.collections} ${individual || plan.kind === 'api' ? 'API ingester(s)' : 'collection(s)'}, ${plan.definitions.length} mapping rule(s) and ${plan.automatic_mappings} automatic evidence-to-control link(s).`;
  detail.append(summary);
  if (plan.collectors?.length) {
    const heading = document.createElement('h3'); heading.className = 'h6'; heading.textContent = 'API ingesters'; detail.append(heading);
    const list = document.createElement('ul');
    for (const collector of plan.collectors) {const li = document.createElement('li'); li.textContent = collector.name; list.append(li);}
    detail.append(list);
  }
  if (plan.definitions.length) {
    const heading = document.createElement('h3'); heading.className = 'h6'; heading.textContent = 'Mapping rules'; detail.append(heading);
    const list = document.createElement('ul');
    for (const rule of plan.definitions) {const li = document.createElement('li'); li.textContent = rule.name; list.append(li);}
    detail.append(list);
  }
  el('retained').textContent = 'Collected events, evidence artifacts, measurements, manual/imported mappings and shared cross-source rules remain. ' +
    (individual ? 'This ingester’s revisions and run history are removed. Its connection and other ingesters remain available.' : plan.kind === 'api' ? 'The connection’s credentials and affected ingesters’ revisions and run history are removed.' : 'Server environment credentials remain configured.');
  el('confirm-name').textContent = plan.name;
  el('typed-name').value = '';
  el('delete').disabled = true;
  el('typed-name').oninput = () => {el('delete').disabled = el('typed-name').value !== plan.name;};
  el('cancel').onclick = () => dialog.close();
  el('delete').onclick = async () => {
    el('delete').disabled = true;
    try {
      await apiPost(url + '/remove', {fingerprint: plan.fingerprint});
      dialog.close();
      await onRemoved();
      window.dispatchEvent(new Event('keen-connections-changed'));
    } catch (error) {dialog.close(); onError(error);}
  };
  dialog.showModal();
  el('typed-name').focus();
}
