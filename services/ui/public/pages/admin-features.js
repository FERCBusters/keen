import { applyFeatures } from '/pages/feature-availability.js';
import { apiGet, apiPut, toast } from '/app.js';

export function mountFeatures(me) {
  const root = document.getElementById('feature-settings');
  const status = document.getElementById('feature-status');
  const tab = document.getElementById('tab-features');
  if (!root || !tab) return;
  if (!me?.is_admin) {
    tab.closest('li').hidden = true;
    return;
  }
  function show(message, kind) {
    status.hidden = false;
    toast(status, message, kind);
  }
  let config;
  let busy = false;
  let pending = null;
  const reload = document.getElementById('features-reload');
  function render() {
    root.setAttribute('aria-busy', String(busy));
    if (reload) reload.disabled = busy;
    root.replaceChildren();
    for (const item of config.items) {
      const row = document.createElement('div');
      row.className = `form-check form-switch py-2 ${item.parent ? 'ms-4' : 'border-top mt-3'}`;
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.className = 'form-check-input';
      input.id = `feature-${item.key}`;
      input.setAttribute('role', 'switch');
      input.checked = pending?.key === item.key ? pending.checked : config.configured[item.key];
      const parentOff = item.parent && !config.enabled[item.parent];
      input.disabled = busy || !!parentOff;
      const label = document.createElement('label');
      label.htmlFor = input.id;
      label.className = 'form-check-label';
      label.textContent = item.label;
      row.append(input, label);
      if (parentOff) {
        const help = document.createElement('div');
        help.className = 'form-text';
        help.id = `${input.id}-help`;
        help.textContent =
          'Unavailable while its parent feature is off. Your setting is preserved.';
        input.setAttribute('aria-describedby', help.id);
        row.append(help);
      }
      input.addEventListener('change', async () => {
        if (busy) return;
        busy = true;
        const checked = input.checked;
        pending = { key: item.key, checked };
        show(`Saving ${item.label}`, 'info');
        render();
        try {
          config = await apiPut('/api/v1/admin/features', {
            version: config.version,
            changes: { [item.key]: checked },
          });
          applyFeatures({ ...me, features: { version: config.version, enabled: config.enabled } });
          show(
            `${item.label} ${checked ? 'enabled' : 'disabled'}. Settings saved. Reload the page to refresh navigation.`,
            'success',
          );
        } catch (error) {
          show(`Could not save: ${String(error)}. Reload settings before trying again.`, 'danger');
          try {
            config = await apiGet('/api/v1/admin/features');
          } catch {
            /* Keep the last confirmed values. */
          }
        } finally {
          busy = false;
          pending = null;
          render();
          document.getElementById(`feature-${item.key}`)?.focus();
        }
      });
      root.append(row);
    }
    document.getElementById('feature-version').textContent =
      `Configuration revision ${config.version}`;
  }
  async function load() {
    if (busy) return;
    busy = true;
    root.setAttribute('aria-busy', 'true');
    if (reload) reload.disabled = true;
    if (config) render();
    show('Loading feature settings', 'info');
    try {
      config = await apiGet('/api/v1/admin/features');
      status.hidden = true;
    } catch (error) {
      show(`Could not load features: ${String(error)}. Use Reload settings to retry.`, 'danger');
    } finally {
      busy = false;
      root.setAttribute('aria-busy', 'false');
      if (reload) reload.disabled = false;
      if (config) render();
    }
  }
  tab.addEventListener('shown.bs.tab', load);
  reload?.addEventListener('click', load);
  const isActive = () =>
    tab.classList.contains('active') || location.hash.toLowerCase() === '#features';
  // Bootstrap may have activated the tab while initNavbar was awaiting the API.
  // Restoring a document from the back/forward cache also emits no tab event.
  window.addEventListener('pageshow', (event) => {
    if (event.persisted && isActive()) load();
  });
  if (isActive()) load();
}
