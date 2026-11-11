// The folder panel beside Policies & processes: a tree of document folders
// that works like a file manager. Folders are explored with the mouse or the
// keyboard (arrow keys, Home, End, Enter, F2 to rename, Delete to delete),
// created, renamed, moved and deleted in place, and documents and folders are
// filed by dragging them onto a folder or through the Move dialog, which
// keyboard users reach from each document's actions.
//
// All names are set as text, so hostile folder or document names stay inert.
import { apiDelete, apiGet, apiPatch, apiPost, confirmAction, errorText, toast } from '/app.js';

const API = '/api/v1/isms/document-folders';
// The two views that are not folders: every document, and documents filed in
// no folder. Dropping a folder on "All documents" moves it to the top level.
export const ALL = null;
export const UNFILED = '';

function node(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === 'class') el.className = value;
    else el.setAttribute(name, value);
  }
  el.append(...children.filter((child) => child !== null));
  return el;
}

function icon(name) {
  return node('i', { class: `bi ${name}`, 'aria-hidden': 'true' });
}

// The key a view carries in the tree: "all", "unfiled" or a folder id.
const keyOf = (view) => (view === ALL ? 'all' : view === UNFILED ? 'unfiled' : view);
const viewOf = (key) => (key === 'all' ? ALL : key === 'unfiled' ? UNFILED : key);

export function mountDocumentFolders({ panel, canManage, status, framework, onSelect, onChange }) {
  let folders = [];
  let byId = new Map();
  let children = new Map();
  let documents = [];
  let selected = ALL;
  let drag = null; // {kind: 'document' | 'folder', id}
  const expanded = new Set();

  const tree = node('ul', {
    class: 'keen-folder-tree list-unstyled mb-0',
    role: 'tree',
    'aria-label': 'Document folders',
  });
  const toolbar = node('div', {
    class: 'keen-folder-toolbar d-flex flex-wrap gap-1 mb-2',
    role: 'toolbar',
    'aria-label': 'Folder actions',
  });
  const tool = (action, iconName, text) =>
    node(
      'button',
      {
        class: 'btn btn-sm btn-outline-secondary',
        type: 'button',
        'data-folder-action': action,
        title: text,
      },
      icon(iconName),
      node('span', { class: 'visually-hidden' }, text),
    );
  const tools = {
    create: tool('create', 'bi-folder-plus', 'New folder'),
    rename: tool('rename', 'bi-input-cursor-text', 'Rename folder'),
    move: tool('move', 'bi-folder-symlink', 'Move folder'),
    remove: tool('remove', 'bi-trash', 'Delete folder'),
  };
  toolbar.append(...Object.values(tools));
  panel.replaceChildren(...(canManage ? [toolbar] : []), tree);

  // The folders from the top level down to `id`; none for a view.
  function trail(id) {
    const out = [];
    const seen = new Set();
    let current = byId.get(id);
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      out.unshift(current);
      current = byId.get(current.parent_id);
    }
    return out;
  }
  const pathOf = (id) => trail(id).map((folder) => folder.name);

  // The folder and every folder inside it. A set visits each folder once,
  // whatever the parents say.
  function within(id) {
    const out = new Set([id]);
    for (const current of out) for (const child of children.get(current) || []) out.add(child.id);
    return out;
  }

  function index(list) {
    folders = list;
    byId = new Map(list.map((folder) => [folder.id, folder]));
    children = new Map();
    for (const folder of list) {
      // A folder whose parent is unknown is shown at the top level.
      const parent = byId.has(folder.parent_id) ? folder.parent_id : null;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(folder);
    }
    for (const siblings of children.values())
      siblings.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    if (selected !== ALL && selected !== UNFILED && !byId.has(selected)) selected = ALL;
  }

  function countBadge(count) {
    return node('span', { class: 'badge text-bg-light ms-auto' }, String(count));
  }

  function item(view, label, iconName, count, level, folder = null) {
    const key = keyOf(view);
    const kids = folder ? children.get(folder.id) || [] : [];
    const open = folder && expanded.has(folder.id);
    const li = node('li', {
      role: 'treeitem',
      'data-folder-key': key,
      'aria-level': String(level),
      'aria-selected': String(selected === view),
      tabindex: selected === view ? '0' : '-1',
    });
    const toggle =
      folder && kids.length
        ? node(
            'span',
            { class: 'keen-folder-toggle', 'data-folder-toggle': key, 'aria-hidden': 'true' },
            icon(open ? 'bi-chevron-down' : 'bi-chevron-right'),
          )
        : node('span', { class: 'keen-folder-toggle', 'aria-hidden': 'true' });
    const row = node(
      'div',
      { class: 'keen-folder-row', 'data-folder-drop': key },
      toggle,
      icon(`${iconName} keen-folder-icon`),
      node('span', { class: 'keen-folder-name', 'data-folder-name': key }, label),
      countBadge(count),
    );
    if (folder && canManage) row.setAttribute('draggable', 'true');
    li.append(row);
    if (kids.length) {
      li.setAttribute('aria-expanded', String(open));
      const group = node('ul', { class: 'list-unstyled keen-folder-group', role: 'group' });
      group.hidden = !open;
      group.append(...kids.map((kid) => folderItem(kid, level + 1)));
      li.append(group);
    }
    return li;
  }

  function folderItem(folder, level) {
    const open = expanded.has(folder.id) && (children.get(folder.id) || []).length;
    return item(
      folder.id,
      folder.name,
      open ? 'bi-folder2-open' : 'bi-folder',
      folder.document_count,
      level,
      folder,
    );
  }

  function render() {
    const unfiled = documents.filter((doc) => !doc.folder_id).length;
    tree.replaceChildren(
      item(ALL, 'All documents', 'bi-collection', documents.length, 1),
      item(UNFILED, 'Unfiled', 'bi-inbox', unfiled, 1),
      ...(children.get(null) || []).map((folder) => folderItem(folder, 1)),
    );
    // When the open folder sits inside a closed one, Tab reaches the tree
    // through the closed folder that is shown.
    let current = treeItem(keyOf(selected));
    const shown = current;
    while (current.parentElement.closest('[role="group"][hidden]'))
      current = current.parentElement.closest('[role="treeitem"]');
    if (current !== shown) {
      shown.tabIndex = -1;
      current.tabIndex = 0;
    }
    const real = selected !== ALL && selected !== UNFILED;
    tools.rename.disabled = !real;
    tools.move.disabled = !real;
    tools.remove.disabled = !real;
  }

  function treeItem(key) {
    return [...tree.querySelectorAll('[role="treeitem"]')].find(
      (el) => el.dataset.folderKey === key,
    );
  }

  function focus(key) {
    for (const el of tree.querySelectorAll('[role="treeitem"]')) el.tabIndex = -1;
    const target = treeItem(key);
    target.tabIndex = 0;
    target.focus();
  }

  function select(view, { focusItem = true } = {}) {
    selected = view;
    // Opening a folder shows where it sits.
    for (const folder of trail(view).slice(0, -1)) expanded.add(folder.id);
    render();
    if (focusItem) focus(keyOf(view));
    onSelect?.(view);
  }

  async function load() {
    index(await apiGet(API));
    render();
  }

  function report(message, error) {
    toast(status, `${message}: ${errorText(error)}`, 'danger');
  }

  async function changed(message) {
    toast(status, message, 'success');
    await load();
    await onChange?.();
  }

  // Inline name editing, as a file manager does: Enter saves, Escape cancels.
  // A new folder keeps its suggested name; a rename to the same name is no
  // change.
  function editName(key, initial, save, { fresh = false } = {}) {
    const label = tree.querySelector(`[data-folder-name="${CSS.escape(key)}"]`);
    const input = node('input', {
      class: 'form-control form-control-sm keen-folder-input',
      'aria-label': 'Folder name',
      maxlength: '256',
    });
    input.value = initial;
    label.replaceChildren(input);
    input.focus();
    input.select();
    let done = false;
    const finish = async (keep) => {
      if (done) return;
      done = true;
      const name = input.value.trim();
      if (!keep || !name || (name === initial && !fresh)) {
        render();
        focus(keyOf(selected));
        return;
      }
      await save(name);
    };
    input.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Enter') {
        ev.preventDefault();
        finish(true);
      } else if (ev.key === 'Escape') {
        ev.preventDefault();
        finish(false);
      }
    });
    input.addEventListener('blur', () => finish(true));
  }

  function createFolder() {
    const parent = selected === ALL || selected === UNFILED ? null : selected;
    if (parent) expanded.add(parent);
    render();
    const draft = item('new-folder', '', 'bi-folder-plus', 0, parent ? 2 : 1);
    // The folder being named is neither selected nor a place to drop.
    draft.firstChild.removeAttribute('data-folder-drop');
    const owner = parent && treeItem(parent);
    const host = owner
      ? (owner.querySelector(':scope > [role="group"]') ??
        owner.appendChild(node('ul', { class: 'list-unstyled keen-folder-group', role: 'group' })))
      : tree;
    host.prepend(draft);
    editName(
      'new-folder',
      'New folder',
      async (name) => {
        try {
          const folder = await apiPost(API, { name, parent_id: parent });
          selected = folder.id;
          await changed(`Folder “${name}” created.`);
          select(folder.id);
        } catch (error) {
          report('Could not create the folder', error);
          render();
        }
      },
      { fresh: true },
    );
  }

  function renameFolder(id) {
    const folder = byId.get(id);
    editName(id, folder.name, async (name) => {
      try {
        await apiPatch(`${API}/${encodeURIComponent(id)}`, {
          name,
          parent_id: folder.parent_id,
        });
        await changed(`Folder renamed to “${name}”.`);
        focus(id);
      } catch (error) {
        report('Could not rename the folder', error);
        render();
      }
    });
  }

  async function moveFolder(id, parent) {
    const folder = byId.get(id);
    if (folder.parent_id === parent) return;
    try {
      await apiPatch(`${API}/${encodeURIComponent(id)}`, { name: folder.name, parent_id: parent });
      if (parent) expanded.add(parent);
      await changed(
        parent
          ? `Moved “${folder.name}” into “${byId.get(parent).name}”.`
          : `Moved “${folder.name}” to the top level.`,
      );
    } catch (error) {
      report('Could not move the folder', error);
    }
  }

  async function removeFolder(id) {
    const folder = byId.get(id);
    const ok = await confirmAction({
      title: 'Delete folder',
      message: `Delete the folder “${folder.name}”? Only an empty folder can be deleted; move its documents and folders out first.`,
      confirmLabel: 'Delete folder',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiDelete(`${API}/${encodeURIComponent(id)}`);
      selected = byId.get(folder.parent_id) ? folder.parent_id : ALL;
      await changed(`Folder “${folder.name}” deleted.`);
      select(selected);
    } catch (error) {
      report('Could not delete the folder', error);
    }
  }

  async function moveDocument(id, folderId) {
    const doc = documents.find((item) => item.id === id);
    if ((doc.folder_id || UNFILED) === (folderId || UNFILED)) return;
    try {
      await apiPatch(
        `/api/v1/isms/documents/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`,
        { folder_id: folderId || null },
      );
      await changed(
        folderId
          ? `Moved “${doc.title}” into “${byId.get(folderId).name}”.`
          : `“${doc.title}” is now unfiled.`,
      );
    } catch (error) {
      report('Could not move the document', error);
    }
  }

  // Where a dragged or chosen record may go: a folder cannot sit inside itself
  // or one of its own folders, and only documents can be unfiled.
  function accepts(key, moving = drag) {
    if (!moving) return false;
    if (key === 'unfiled') return moving.kind === 'document';
    if (key === 'all') return moving.kind === 'folder';
    return moving.kind === 'document' || !within(moving.id).has(key);
  }

  // The Move dialog: the keyboard way to file a document or folder.
  async function chooseDestination(title, moving) {
    const current =
      moving.kind === 'document'
        ? documents.find((doc) => doc.id === moving.id).folder_id || 'unfiled'
        : byId.get(moving.id).parent_id || 'all';
    const choice = node('select', { class: 'form-select', id: 'keenFolderMoveTarget' });
    const option = (value, text) => {
      const el = node('option', { value }, text);
      el.disabled = !accepts(value, moving);
      el.selected = value === current;
      return el;
    };
    choice.append(
      moving.kind === 'document' ? option('unfiled', 'Unfiled') : option('all', 'Top level'),
      ...[...folders]
        .map((folder) => [pathOf(folder.id).join(' › '), folder.id])
        .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
        .map(([path, id]) => option(id, path)),
    );
    const dialog = node(
      'dialog',
      { class: 'keen-confirm', 'aria-labelledby': 'keenFolderMoveTitle' },
      node('h2', { class: 'h5 mb-3', id: 'keenFolderMoveTitle' }, title),
      node('label', { class: 'form-label', for: 'keenFolderMoveTarget' }, 'Move to'),
      choice,
      node(
        'div',
        { class: 'd-flex gap-2 justify-content-end mt-3' },
        node(
          'button',
          { class: 'btn btn-outline-secondary', type: 'button', 'data-move-cancel': '' },
          'Cancel',
        ),
        node(
          'button',
          { class: 'btn btn-primary', type: 'button', 'data-move-confirm': '' },
          'Move',
        ),
      ),
    );
    document.body.append(dialog);
    const answer = await new Promise((resolve) => {
      dialog.addEventListener('cancel', (ev) => {
        ev.preventDefault();
        resolve(null);
      });
      dialog.querySelector('[data-move-cancel]').addEventListener('click', () => resolve(null));
      dialog
        .querySelector('[data-move-confirm]')
        .addEventListener('click', () => resolve(choice.value));
      dialog.showModal();
      choice.focus();
    });
    dialog.close();
    dialog.remove();
    return answer;
  }

  async function move(moving, key) {
    if (moving.kind === 'document') await moveDocument(moving.id, viewOf(key) || null);
    else await moveFolder(moving.id, viewOf(key) || null);
  }

  async function moveWithDialog(moving) {
    const name =
      moving.kind === 'document'
        ? documents.find((doc) => doc.id === moving.id).title
        : byId.get(moving.id).name;
    const key = await chooseDestination(`Move “${name}”`, moving);
    if (key !== null) await move(moving, key);
  }

  // --- Events -----------------------------------------------------------------------------

  tree.addEventListener('click', (ev) => {
    if (ev.target.closest('input')) return;
    const toggle = ev.target.closest('[data-folder-toggle]');
    if (toggle) {
      const id = toggle.dataset.folderToggle;
      if (expanded.has(id)) expanded.delete(id);
      else expanded.add(id);
      render();
      // Focus stays on the folder that opened or closed, which is always shown.
      focus(id);
      return;
    }
    const li = ev.target.closest('[role="treeitem"]');
    if (li && li.dataset.folderKey !== 'new-folder') select(viewOf(li.dataset.folderKey));
  });

  function visibleItems() {
    return [...tree.querySelectorAll('[role="treeitem"]')].filter(
      (el) => !el.parentElement.closest('[role="group"][hidden]'),
    );
  }

  tree.addEventListener('keydown', (ev) => {
    const li = ev.target.closest('[role="treeitem"]');
    const key = li.dataset.folderKey;
    const items = visibleItems();
    const at = items.indexOf(li);
    const id = viewOf(key);
    const real = id !== ALL && id !== UNFILED;
    const moveFocus = (target) => {
      ev.preventDefault();
      if (target) focus(target.dataset.folderKey);
    };
    switch (ev.key) {
      case 'ArrowDown':
        return moveFocus(items[at + 1]);
      case 'ArrowUp':
        return moveFocus(items[at - 1]);
      case 'Home':
        return moveFocus(items[0]);
      case 'End':
        return moveFocus(items[items.length - 1]);
      case 'ArrowRight':
        if (li.getAttribute('aria-expanded') === 'false') {
          expanded.add(id);
          render();
          return moveFocus(treeItem(key));
        }
        return moveFocus(
          li.getAttribute('aria-expanded') === 'true'
            ? li.querySelector('[role="group"] > [role="treeitem"]')
            : null,
        );
      case 'ArrowLeft':
        if (li.getAttribute('aria-expanded') === 'true') {
          expanded.delete(id);
          render();
          return moveFocus(treeItem(key));
        }
        return moveFocus(li.parentElement.closest('[role="treeitem"]'));
      case 'Enter':
      case ' ':
        ev.preventDefault();
        return select(id);
      case 'F2':
        if (canManage && real) {
          ev.preventDefault();
          renameFolder(id);
        }
        return;
      case 'Delete':
        if (canManage && real) {
          ev.preventDefault();
          removeFolder(id);
        }
        return;
      default:
    }
  });

  toolbar.addEventListener('click', (ev) => {
    const action = ev.target.closest('[data-folder-action]')?.dataset.folderAction;
    if (action === 'create') createFolder();
    else if (action === 'rename') renameFolder(selected);
    else if (action === 'move') moveWithDialog({ kind: 'folder', id: selected });
    else if (action === 'remove') removeFolder(selected);
  });

  // Dragging: documents from the table, and folders within the tree.
  function clearDrag() {
    drag = null;
    for (const el of tree.querySelectorAll('.keen-folder-drop'))
      el.classList.remove('keen-folder-drop');
  }
  function startDrag(ev, moving, label) {
    drag = moving;
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('text/plain', label);
  }
  tree.addEventListener('dragstart', (ev) => {
    // Only folders drag; selected text dragged from elsewhere in the tree does not.
    const row = ev.target.closest?.('[draggable="true"]');
    if (!row) return;
    const id = row.dataset.folderDrop;
    startDrag(ev, { kind: 'folder', id }, byId.get(id).name);
  });
  tree.addEventListener('dragend', clearDrag);
  tree.addEventListener('dragover', (ev) => {
    const row = ev.target.closest('[data-folder-drop]');
    if (!row || !accepts(row.dataset.folderDrop)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    row.classList.add('keen-folder-drop');
  });
  tree.addEventListener('dragleave', (ev) => {
    const row = ev.target.closest('[data-folder-drop]');
    if (row && !row.contains(ev.relatedTarget)) row.classList.remove('keen-folder-drop');
  });
  tree.addEventListener('drop', async (ev) => {
    const row = ev.target.closest('[data-folder-drop]');
    const moving = drag;
    clearDrag();
    if (!row || !accepts(row.dataset.folderDrop, moving)) return;
    ev.preventDefault();
    await move(moving, row.dataset.folderDrop);
  });

  return {
    load,
    pathOf,
    trail,
    selected: () => selected,
    select,
    folders: () => folders,
    // The documents the table holds, for counts and moves.
    setDocuments(list) {
      documents = list;
      render();
    },
    // Rows marked data-doc-drag in `rows` can be dragged onto a folder.
    attachDocumentRows(rows) {
      rows.addEventListener('dragstart', (ev) => {
        const row = ev.target.closest?.('[data-doc-drag]');
        if (!row) return;
        const doc = documents.find((item) => item.id === row.dataset.docDrag);
        startDrag(ev, { kind: 'document', id: doc.id }, doc.title);
      });
      rows.addEventListener('dragend', clearDrag);
    },
    moveDocument: (id) => moveWithDialog({ kind: 'document', id }),
  };
}
