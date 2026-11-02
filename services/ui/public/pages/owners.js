// Owners of a control or clause: the people answerable for it. Everyone sees
// them; administrators choose them from the active accounts, which load the
// first time the editor opens. The page holds the Owners card markup
// (ownersList, ownersEdit, ownersEditor, ownersSelect, ownersSave).
import { apiGet, apiPut, errorText, toast, userPillsHtml } from '/app.js';

export const ownerNames = (owners) => (owners || []).map((owner) => owner.username).join(', ');

export function mountOwners({ me, path, status, onSaved }) {
  const [list, edit, editor, select, save] = [
    'ownersList',
    'ownersEdit',
    'ownersEditor',
    'ownersSelect',
    'ownersSave',
  ].map((id) => document.getElementById(id));
  let current = [];
  let accounts = null;

  function show(owners) {
    current = owners || [];
    list.innerHTML = userPillsHtml(current, { empty: 'No owners yet.' });
    edit.hidden = !me?.is_admin;
  }

  function close() {
    editor.hidden = true;
    edit.textContent = 'Edit';
  }

  async function open() {
    edit.disabled = true;
    try {
      accounts ||= await apiGet('/api/v1/users');
    } catch (error) {
      toast(status, `Failed to load accounts for owners: ${errorText(error)}`, 'danger');
      return;
    } finally {
      edit.disabled = false;
    }
    const chosen = new Set(current.map((owner) => owner.id));
    select.replaceChildren(
      ...accounts
        .filter((account) => account.is_active)
        .map((account) => {
          const option = new Option(account.username, account.id);
          option.selected = chosen.has(String(account.id));
          return option;
        }),
    );
    editor.hidden = false;
    edit.textContent = 'Hide edit';
  }

  edit.addEventListener('click', () => (editor.hidden ? open() : close()));
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      const { owners } = await apiPut(path(), {
        user_ids: [...select.selectedOptions].map((option) => option.value),
      });
      show(owners);
      close();
      toast(status, 'Owners saved', 'success');
      onSaved?.(owners);
    } catch (error) {
      toast(status, `Failed to save owners: ${errorText(error)}`, 'danger');
    } finally {
      save.disabled = false;
    }
  });
  return { show };
}
