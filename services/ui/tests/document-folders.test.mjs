// The folder tree beside Policies & processes: exploring it with the mouse and
// the keyboard, creating, renaming, moving and deleting folders in place, and
// filing documents by dragging them onto a folder or through the Move dialog.
// The API answers as services/api/app/services/isms/documents.py does, and the
// register reloads after each change, so the tree and the table show what the
// server holds.
import test from 'node:test';
import assert from 'node:assert/strict';
import { documentRecord, register, settle } from './support/isms-register-fixture.mjs';

const hostile = '<img src=x onerror=alert(1)>';
const folder = (id, name, parent_id = null) => ({
  id,
  name,
  parent_id,
  created_at: '2026-03-01T09:00:00',
  updated_at: '2026-03-01T09:00:00',
});

// Governance › Policies › Standards, and People beside Governance.
const tree = () => [
  folder('f1', 'Governance'),
  folder('f2', 'Policies', 'f1'),
  folder('f3', 'Standards', 'f2'),
  folder('f4', 'People'),
];
const docs = () => [
  documentRecord('d1', 'Access policy', { folder_id: 'f2' }),
  documentRecord('d2', 'Backup process', { folder_id: null }),
  documentRecord('d3', `${hostile}Screening`, { folder_id: 'f4' }),
];

// jsdom has no modal dialog machinery; its observable state is enough.
function dialogs(window) {
  window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
}

async function panel(t, { me, folders = tree(), documents = docs(), refuse = {}, confirm } = {}) {
  const db = { folders: structuredClone(folders) };
  const records = { documents: structuredClone(documents) };
  const counted = () =>
    db.folders.map((f) => ({
      ...f,
      document_count: records.documents.filter((d) => d.folder_id === f.id).length,
    }));
  const ui = await register(t, {
    tab: 'documents',
    me,
    confirm,
    records,
    setup: dialogs,
    get: (u) => (u.pathname === '/api/v1/isms/document-folders' ? counted() : undefined),
    respond: (method, u, body) => {
      const path = u.pathname;
      if (path === '/api/v1/isms/document-folders' && method === 'POST') {
        if (refuse.create) return new Error(refuse.create);
        const made = folder(`f${db.folders.length + 10}`, body.name, body.parent_id);
        db.folders.push(made);
        return { ...made, document_count: 0 };
      }
      if (path.startsWith('/api/v1/isms/document-folders/')) {
        const id = path.split('/').pop();
        if (method === 'DELETE') {
          if (refuse.remove) return new Error(refuse.remove);
          db.folders = db.folders.filter((f) => f.id !== id);
          return { ok: true };
        }
        if (refuse.update) return new Error(refuse.update);
        Object.assign(
          db.folders.find((f) => f.id === id),
          body,
        );
        return { id, ...body };
      }
      if (path.startsWith('/api/v1/isms/documents/') && method === 'PATCH') {
        if (refuse.document) return new Error(refuse.document);
        records.documents.find((d) => d.id === path.split('/').pop()).folder_id = body.folder_id;
        return { ok: true };
      }
      return undefined;
    },
  });
  await settle(() => ui.el('documentsRows').querySelector('[data-isms-row="document"]'));
  // The folder choices are filled once the folders have loaded.
  await settle(() => ui.el('docFolder').options.length === db.folders.length + 1);
  const item = (key) => ui.el('docFolderPanel').querySelector(`[data-folder-key="${key}"]`);
  const row = (key) => item(key).querySelector(':scope > .keen-folder-row');
  const key = (target, name) =>
    target.dispatchEvent(
      new ui.window.KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }),
    );
  const focused = () => ui.document.activeElement?.dataset?.folderKey;
  const shown = () =>
    [...ui.el('documentsRows').querySelectorAll('[data-isms-row="document"]')].map(
      (tr) => tr.querySelector('a').textContent,
    );
  const names = () =>
    [...ui.el('docFolderPanel').querySelectorAll('[role="treeitem"]')]
      .filter((li) => !li.parentElement.closest('[role="group"][hidden]'))
      .map((li) => li.querySelector('[data-folder-name]').textContent);
  const tool = (action) =>
    ui.el('docFolderPanel').querySelector(`[data-folder-action="${action}"]`);
  const editor = () => ui.el('docFolderPanel').querySelector('.keen-folder-input');
  const type = async (text, finish = 'Enter') => {
    const input = editor();
    input.value = text;
    key(input, finish);
  };
  const dialog = () => ui.document.querySelector('dialog.keen-confirm');
  const choose = async (value) => {
    await settle(() => dialog()?.open);
    if (value === null) dialog().querySelector('[data-move-cancel]').click();
    else {
      dialog().querySelector('select').value = value;
      dialog().querySelector('[data-move-confirm]').click();
    }
  };
  // A drag of `source` onto the folder row `target`, as the browser reports it.
  const drag = (source, target, { drop = true } = {}) => {
    const transfer = { data: {}, setData: (type, value) => (transfer.data[type] = value) };
    const fire = (el, type) => {
      const ev = new ui.window.Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(ev, 'dataTransfer', { value: transfer });
      el.dispatchEvent(ev);
      return ev;
    };
    fire(source, 'dragstart');
    const over = fire(target, 'dragover');
    const dropped = drop ? fire(target, 'drop') : null;
    return { transfer, over, dropped, fire };
  };
  return {
    ui,
    db,
    records,
    item,
    row,
    key,
    focused,
    shown,
    names,
    tool,
    editor,
    type,
    dialog,
    choose,
    drag,
  };
}

test('the tree lists every view and folder with its count, names as text', async (t) => {
  const p = await panel(t, { folders: [...tree(), folder('f9', hostile, 'gone')] });
  // A folder whose parent no longer exists is shown at the top level.
  assert.deepEqual(p.names(), ['All documents', 'Unfiled', hostile, 'Governance', 'People']);
  assert.equal(p.ui.el('docFolderPanel').querySelector('img'), null);
  const count = (key) => p.row(key).querySelector('.badge').textContent;
  assert.deepEqual(['all', 'unfiled', 'f1', 'f2', 'f4'].map(count), ['3', '1', '0', '1', '1']);
  // A folder with folders inside it opens and closes; one without has no toggle.
  assert.equal(p.item('f1').getAttribute('aria-expanded'), 'false');
  assert.equal(p.item('f4').hasAttribute('aria-expanded'), false);
  assert.equal(p.row('f4').querySelector('[data-folder-toggle]'), null);
  assert.equal(p.item('all').getAttribute('aria-selected'), 'true');
  assert.equal(p.item('all').tabIndex, 0);
  // Managers drag folders and document rows, and move documents from their actions.
  assert.equal(p.row('f1').getAttribute('draggable'), 'true');
  assert.equal(p.row('all').hasAttribute('draggable'), false);
  assert.equal(
    p.ui.el('documentsRows').querySelector('[data-doc-drag="d1"]').getAttribute('draggable'),
    'true',
  );
  assert.ok(p.ui.el('documentsRows').querySelector('[data-doc-move="d1"]'));
  assert.match(p.ui.el('docFolderHint').textContent, /Drag documents and folders/);
  // In All documents each filed document shows where it is filed.
  assert.match(
    p.ui.el('documentsRows').querySelector('[data-doc-drag="d1"]').textContent,
    /Governance › Policies/,
  );
  assert.equal(p.tool('rename').disabled, true);
  assert.equal(p.tool('move').disabled, true);
  assert.equal(p.tool('remove').disabled, true);
});

test('without folders the tree holds All documents and Unfiled, and the first folder starts it', async (t) => {
  const p = await panel(t, { folders: [] });
  assert.deepEqual(p.names(), ['All documents', 'Unfiled']);
  assert.deepEqual(
    [...p.ui.el('docFolder').options].map((o) => o.textContent),
    ['Unfiled'],
  );
  p.tool('create').click();
  await p.type('Governance');
  await settle(() => p.names().includes('Governance'));
  assert.deepEqual(p.names(), ['All documents', 'Unfiled', 'Governance']);
});

test('choosing a folder lists what is filed in it, and the path above the table opens folders', async (t) => {
  const p = await panel(t);
  p.row('f1').querySelector('[data-folder-toggle]').click();
  assert.equal(p.item('f1').getAttribute('aria-expanded'), 'true');
  assert.equal(p.focused(), 'f1');
  p.item('f2').querySelector('.keen-folder-name').click();
  assert.deepEqual(p.shown(), ['Access policy']);
  assert.equal(p.item('f2').getAttribute('aria-selected'), 'true');
  assert.equal(p.tool('rename').disabled, false);
  const crumbs = () =>
    [...p.ui.el('docFolderPath').querySelectorAll('li')].map((li) => li.textContent);
  assert.deepEqual(crumbs(), ['All documents', 'Governance', 'Policies']);
  assert.equal(p.ui.el('docFolderPath').querySelector('[aria-current]').textContent, 'Policies');
  // The path opens a folder further up.
  p.ui.el('docFolderPath').querySelectorAll('button')[1].click();
  assert.equal(p.item('f1').getAttribute('aria-selected'), 'true');
  assert.equal(p.ui.el('documentsRows').textContent.includes('No documents are filed here.'), true);
  p.item('unfiled').click();
  assert.deepEqual(p.shown(), ['Backup process']);
  assert.deepEqual(crumbs(), ['All documents', 'Unfiled']);
  p.ui.el('docFolderPath').querySelector('button').click();
  assert.equal(p.shown().length, 3);
  // A folder opened deep in the tree opens the folders above it.
  p.row('f1').querySelector('[data-folder-toggle]').click();
  assert.equal(p.item('f1').getAttribute('aria-expanded'), 'false');
  p.row('f3').click();
  assert.equal(p.item('f1').getAttribute('aria-expanded'), 'true');
  assert.equal(p.item('f2').getAttribute('aria-expanded'), 'true');
});

test('the arrow keys, Home, End, Enter and Space move through the tree', async (t) => {
  const p = await panel(t);
  const at = (k) => p.item(k);
  p.key(at('all'), 'ArrowDown');
  assert.equal(p.focused(), 'unfiled');
  p.key(at('unfiled'), 'ArrowUp');
  assert.equal(p.focused(), 'all');
  p.key(at('all'), 'ArrowUp');
  assert.equal(p.focused(), 'all');
  p.key(at('all'), 'End');
  assert.equal(p.focused(), 'f4');
  p.key(at('f4'), 'Home');
  assert.equal(p.focused(), 'all');
  // Right opens a closed folder, then moves into it; Left closes, then moves up.
  p.key(at('f1'), 'ArrowRight');
  assert.equal(at('f1').getAttribute('aria-expanded'), 'true');
  assert.equal(p.focused(), 'f1');
  p.key(at('f1'), 'ArrowRight');
  assert.equal(p.focused(), 'f2');
  p.key(at('f2'), 'ArrowRight');
  p.key(at('f2'), 'ArrowRight');
  assert.equal(p.focused(), 'f3');
  // A folder without folders inside it goes no further.
  p.key(at('f3'), 'ArrowRight');
  assert.equal(p.focused(), 'f3');
  p.key(at('f3'), 'ArrowLeft');
  assert.equal(p.focused(), 'f2');
  p.key(at('f2'), 'ArrowLeft');
  assert.equal(at('f2').getAttribute('aria-expanded'), 'false');
  p.key(at('all'), 'ArrowLeft');
  assert.equal(p.focused(), 'f2');
  p.key(at('f2'), 'Enter');
  assert.deepEqual(p.shown(), ['Access policy']);
  p.key(at('f4'), ' ');
  assert.equal(at('f4').getAttribute('aria-selected'), 'true');
  // Other keys are left to the browser.
  const tab = new p.ui.window.KeyboardEvent('keydown', {
    key: 'Tab',
    bubbles: true,
    cancelable: true,
  });
  at('f4').dispatchEvent(tab);
  assert.equal(tab.defaultPrevented, false);
  // F2 and Delete do nothing on a view.
  p.key(at('all'), 'F2');
  p.key(at('unfiled'), 'Delete');
  assert.equal(p.editor(), null);
  assert.equal(p.ui.el('status').textContent, '');
});

test('an open folder inside a closed one leaves the closed folder reachable by Tab', async (t) => {
  const p = await panel(t, {
    documents: [documentRecord('d1', 'Coding standard', { folder_id: 'f3', tags: ['Core'] })],
  });
  p.row('f3').click();
  assert.equal(p.item('f3').tabIndex, 0);
  // Closing Governance hides Standards, which stays open in the table.
  p.row('f1').querySelector('[data-folder-toggle]').click();
  assert.equal(p.focused(), 'f1');
  // A redraw, here from filtering by tag, keeps the closed folder the one Tab reaches.
  p.ui.el('docTagCloud').querySelector('[data-filter-doc-tag="Core"]').click();
  assert.equal(p.item('f3').getAttribute('aria-selected'), 'true');
  assert.deepEqual(
    [...p.ui.el('docFolderPanel').querySelectorAll('[role="treeitem"]')]
      .filter((li) => li.tabIndex === 0)
      .map((li) => li.dataset.folderKey),
    ['f1'],
  );
  assert.deepEqual(p.shown(), ['Coding standard']);
});

test('a new folder is named in place: Enter saves, and it opens inside the folder chosen', async (t) => {
  const p = await panel(t);
  // From All documents a folder goes at the top level.
  p.tool('create').click();
  assert.equal(p.editor().value, 'New folder');
  assert.equal(p.ui.document.activeElement, p.editor());
  // Clicks inside the name box, or on the draft, select nothing.
  p.editor().click();
  p.editor().closest('[role="treeitem"]').querySelector('.keen-folder-toggle').click();
  await p.type('Records');
  await settle(() => p.ui.el('status').textContent === 'Folder “Records” created.');
  assert.deepEqual(p.ui.writes()[0], {
    method: 'POST',
    url: '/api/v1/isms/document-folders',
    body: { name: 'Records', parent_id: null },
  });
  await settle(() => p.item('f14')?.getAttribute('aria-selected') === 'true');
  // Inside a folder without folders, the new one opens a group of its own.
  p.tool('create').click();
  assert.ok(p.item('f14').querySelector('[role="group"] .keen-folder-input'));
  await p.type('  Signed  ');
  await settle(() => p.ui.el('status').textContent === 'Folder “Signed” created.');
  assert.deepEqual(p.ui.writes()[1].body, { name: 'Signed', parent_id: 'f14' });
  await settle(() => p.item('f15')?.getAttribute('aria-selected') === 'true');
  assert.deepEqual(
    [...p.ui.el('docFolder').options].map((o) => o.textContent),
    [
      'Unfiled',
      'Governance',
      'Governance / Policies',
      'Governance / Policies / Standards',
      'People',
      'Records',
      'Records / Signed',
    ],
  );
  // Inside a folder that has folders, the new one joins them; the suggested name is kept.
  p.row('f1').click();
  p.tool('create').click();
  assert.ok(p.item('f1').querySelector('[role="group"] .keen-folder-input'));
  p.editor().dispatchEvent(new p.ui.window.Event('blur'));
  await settle(() => p.ui.el('status').textContent === 'Folder “New folder” created.');
  assert.deepEqual(p.ui.writes()[2].body, { name: 'New folder', parent_id: 'f1' });
});

test('Escape or a blank name creates nothing, and a refused folder is reported', async (t) => {
  const p = await panel(t, { refuse: { create: 'A folder with that name is already here' } });
  p.tool('create').click();
  const draft = p.editor();
  await p.type('Draft', 'Escape');
  assert.equal(p.editor(), null);
  assert.equal(p.focused(), 'all');
  // The name box leaving the page afterwards saves nothing.
  draft.dispatchEvent(new p.ui.window.Event('blur'));
  p.tool('create').click();
  await p.type('   ');
  assert.equal(p.editor(), null);
  // Other keys type into the name, never move through the tree.
  p.tool('create').click();
  p.key(p.editor(), 'ArrowDown');
  assert.ok(p.editor());
  await p.type('Governance');
  await settle(() => /already here/.test(p.ui.el('status').textContent));
  assert.equal(
    p.ui.el('status').textContent,
    'Could not create the folder: A folder with that name is already here',
  );
  assert.equal(p.ui.el('status').dataset.alert, 'danger');
  assert.equal(p.editor(), null);
  assert.equal(p.ui.writes().length, 1);
});

test('a folder is renamed in place with F2 or the toolbar, and a refused name is reported', async (t) => {
  const p = await panel(t, { refuse: {} });
  p.row('f4').click();
  p.key(p.item('f4'), 'F2');
  assert.equal(p.editor().value, 'People');
  // The same name is no change.
  await p.type('People');
  assert.equal(p.ui.writes().length, 0);
  p.tool('rename').click();
  await p.type('Personnel');
  await settle(() => p.ui.el('status').textContent === 'Folder renamed to “Personnel”.');
  assert.deepEqual(p.ui.writes()[0], {
    method: 'PATCH',
    url: '/api/v1/isms/document-folders/f4',
    body: { name: 'Personnel', parent_id: null },
  });
  await settle(() => p.focused() === 'f4');
  assert.equal(p.item('f4').querySelector('[data-folder-name]').textContent, 'Personnel');

  const refused = await panel(t, { refuse: { update: 'A folder with that name is already here' } });
  refused.row('f4').click();
  refused.tool('rename').click();
  await refused.type('Governance');
  await settle(() => /already here/.test(refused.ui.el('status').textContent));
  assert.equal(
    refused.ui.el('status').textContent,
    'Could not rename the folder: A folder with that name is already here',
  );
  assert.equal(refused.item('f4').querySelector('[data-folder-name]').textContent, 'People');
});

test('a folder is moved through the Move dialog, which offers no place inside itself', async (t) => {
  const p = await panel(t);
  p.row('f2').click();
  p.tool('move').click();
  await settle(() => p.dialog()?.open);
  assert.equal(p.dialog().querySelector('h2').textContent, 'Move “Policies”');
  const options = [...p.dialog().querySelectorAll('option')].map((o) => [
    o.textContent,
    o.disabled,
    o.selected,
  ]);
  assert.deepEqual(options, [
    ['Top level', false, false],
    ['Governance', false, true],
    ['Governance › Policies', true, false],
    ['Governance › Policies › Standards', true, false],
    ['People', false, false],
  ]);
  await p.choose('f4');
  await settle(() => p.ui.el('status').textContent === 'Moved “Policies” into “People”.');
  assert.equal(p.dialog(), null);
  assert.deepEqual(p.ui.writes()[0], {
    method: 'PATCH',
    url: '/api/v1/isms/document-folders/f2',
    body: { name: 'Policies', parent_id: 'f4' },
  });
  assert.equal(p.item('f4').getAttribute('aria-expanded'), 'true');
  // Moving to where it already is, or cancelling, sends nothing.
  p.tool('move').click();
  await p.choose('f4');
  p.tool('move').click();
  await p.choose(null);
  p.tool('move').click();
  await settle(() => p.dialog()?.open);
  p.dialog().dispatchEvent(new p.ui.window.Event('cancel', { cancelable: true }));
  await settle(() => !p.dialog());
  assert.equal(p.ui.writes().length, 1);
  // To the top level.
  p.tool('move').click();
  await p.choose('all');
  await settle(() => p.ui.el('status').textContent === 'Moved “Policies” to the top level.');
  assert.deepEqual(p.ui.writes()[1].body, { name: 'Policies', parent_id: null });
});

test('a refused folder move is reported', async (t) => {
  const p = await panel(t, { refuse: { update: 'Parent relationship would contain a cycle' } });
  p.row('f4').click();
  p.tool('move').click();
  await p.choose('f1');
  await settle(() => /cycle/.test(p.ui.el('status').textContent));
  assert.equal(
    p.ui.el('status').textContent,
    'Could not move the folder: Parent relationship would contain a cycle',
  );
});

test('only an empty folder is deleted, after confirming, and the tree opens its parent', async (t) => {
  const asked = [];
  const p = await panel(t, {
    confirm: (options) => {
      asked.push(options);
      return asked.length > 1;
    },
  });
  p.row('f3').click();
  p.key(p.item('f3'), 'Delete');
  await settle(() => asked.length === 1);
  assert.equal(asked[0].danger, true);
  assert.match(asked[0].message, /Delete the folder “Standards”\? Only an empty folder/);
  assert.equal(p.ui.writes().length, 0);
  p.tool('remove').click();
  await settle(() => p.ui.el('status').textContent === 'Folder “Standards” deleted.');
  assert.deepEqual(p.ui.writes()[0], {
    method: 'DELETE',
    url: '/api/v1/isms/document-folders/f3',
    body: null,
  });
  await settle(() => p.item('f2').getAttribute('aria-selected') === 'true');
  assert.equal(p.item('f3'), null);
  // A top-level folder's deletion returns to All documents.
  p.row('f4').click();
  p.tool('remove').click();
  await settle(() => p.ui.el('status').textContent === 'Folder “People” deleted.');
  await settle(() => p.item('all').getAttribute('aria-selected') === 'true');
});

test('a folder the server will not delete is reported and stays', async (t) => {
  const p = await panel(t, { refuse: { remove: 'Folder is not empty' } });
  p.row('f4').click();
  p.tool('remove').click();
  await settle(() => /not empty/.test(p.ui.el('status').textContent));
  assert.equal(p.ui.el('status').textContent, 'Could not delete the folder: Folder is not empty');
  assert.ok(p.item('f4'));
});

test('a document is filed through its Move button, and unfiled the same way', async (t) => {
  const p = await panel(t);
  p.ui.el('documentsRows').querySelector('[data-doc-move="d2"]').click();
  await settle(() => p.dialog()?.open);
  assert.equal(p.dialog().querySelector('h2').textContent, 'Move “Backup process”');
  const options = [...p.dialog().querySelectorAll('option')];
  assert.deepEqual(
    options.map((o) => [o.value, o.disabled, o.selected]),
    [
      ['unfiled', false, true],
      ['f1', false, false],
      ['f2', false, false],
      ['f3', false, false],
      ['f4', false, false],
    ],
  );
  await p.choose('f3');
  await settle(() => /Moved “Backup process” into “Standards”/.test(p.ui.el('status').textContent));
  assert.deepEqual(p.ui.writes()[0], {
    method: 'PATCH',
    url: '/api/v1/isms/documents/d2?framework=A',
    body: { folder_id: 'f3' },
  });
  await settle(() => p.row('f3').querySelector('.badge').textContent === '1');
  p.ui.el('documentsRows').querySelector('[data-doc-move="d2"]').click();
  await p.choose('unfiled');
  await settle(() => p.ui.el('status').textContent === '“Backup process” is now unfiled.');
  assert.deepEqual(p.ui.writes()[1].body, { folder_id: null });
  // A move to where it is already filed sends nothing.
  p.ui.el('documentsRows').querySelector('[data-doc-move="d1"]').click();
  await p.choose('f2');
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(p.ui.writes().length, 2);
});

test('a folder deleted in another session closes, and the tree returns to All documents', async (t) => {
  const p = await panel(t);
  p.row('f4').click();
  assert.deepEqual(p.shown(), [`${hostile}Screening`]);
  // Another session empties and deletes People; the next reload drops it.
  p.records.documents[2].folder_id = null;
  p.db.folders = p.db.folders.filter((f) => f.id !== 'f4');
  p.ui.el('documentsRows').querySelector('[data-doc-move="d3"]').click();
  await p.choose('f1');
  await settle(() => p.item('f4') === null);
  assert.equal(p.item('all').getAttribute('aria-selected'), 'true');
  assert.equal(p.shown().length, 3);
});

test('a refused document move is reported and the document stays where it was', async (t) => {
  const p = await panel(t, { refuse: { document: 'Unknown folder' } });
  p.ui.el('documentsRows').querySelector('[data-doc-move="d1"]').click();
  await p.choose('f4');
  await settle(() => /Unknown folder/.test(p.ui.el('status').textContent));
  assert.equal(p.ui.el('status').textContent, 'Could not move the document: Unknown folder');
  assert.equal(p.records.documents[0].folder_id, 'f2');
});

test('documents and folders are dragged onto a folder, and only where they may go', async (t) => {
  const p = await panel(t);
  const docRow = (id) => p.ui.el('documentsRows').querySelector(`[data-doc-drag="${id}"]`);
  // A document carries its title and may be dropped on a folder or Unfiled.
  const onto = p.drag(docRow('d2').querySelector('td'), p.row('f4').querySelector('.badge'), {
    drop: false,
  });
  assert.equal(onto.transfer.data['text/plain'], 'Backup process');
  assert.equal(onto.transfer.effectAllowed, 'move');
  assert.equal(onto.over.defaultPrevented, true);
  assert.equal(onto.transfer.dropEffect, 'move');
  assert.ok(p.row('f4').classList.contains('keen-folder-drop'));
  // Leaving for a child of the row keeps the highlight; leaving the row clears it.
  const leave = (related) => {
    const ev = new p.ui.window.Event('dragleave', { bubbles: true });
    Object.defineProperty(ev, 'relatedTarget', { value: related });
    p.row('f4').dispatchEvent(ev);
  };
  leave(p.row('f4').querySelector('.badge'));
  assert.ok(p.row('f4').classList.contains('keen-folder-drop'));
  leave(p.row('all'));
  assert.equal(p.row('f4').classList.contains('keen-folder-drop'), false);
  onto.fire(p.row('f4'), 'drop');
  await settle(() => /Moved “Backup process” into “People”/.test(p.ui.el('status').textContent));
  assert.deepEqual(p.ui.writes()[0].body, { folder_id: 'f4' });
  // A document goes nowhere on All documents.
  const refusedAll = p.drag(docRow('d1'), p.row('all'));
  assert.equal(refusedAll.over.defaultPrevented, false);
  assert.equal(refusedAll.dropped.defaultPrevented, false);
  // Dropped on Unfiled it is taken out of its folder.
  p.drag(docRow('d1'), p.row('unfiled'));
  await settle(() => p.ui.el('status').textContent === '“Access policy” is now unfiled.');
  // A folder may not go inside itself or a folder within it, nor on Unfiled.
  p.row('f1').querySelector('[data-folder-toggle]').click();
  p.row('f2').querySelector('[data-folder-toggle]').click();
  for (const target of ['f1', 'f3', 'unfiled']) {
    const attempt = p.drag(p.row('f1'), p.row(target));
    assert.equal(attempt.over.defaultPrevented, false, target);
    assert.equal(attempt.dropped.defaultPrevented, false, target);
  }
  const folderDrag = p.drag(p.row('f3'), p.row('f4'));
  assert.equal(folderDrag.transfer.data['text/plain'], 'Standards');
  await settle(() => /Moved “Standards” into “People”/.test(p.ui.el('status').textContent));
  // Onto All documents a folder moves to the top level.
  p.drag(p.row('f2'), p.row('all'));
  await settle(() => /Moved “Policies” to the top level/.test(p.ui.el('status').textContent));
  assert.equal(p.ui.writes().length, 4);
});

test('drags that carry nothing the tree knows, or end elsewhere, change nothing', async (t) => {
  const p = await panel(t);
  const transfer = { setData: () => {} };
  const fire = (el, type, extra = {}) => {
    const ev = new p.ui.window.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: transfer });
    for (const [name, value] of Object.entries(extra)) Object.defineProperty(ev, name, { value });
    el.dispatchEvent(ev);
    return ev;
  };
  // Text selected in the tree, or a cell outside a document row, starts no drag.
  fire(p.row('all').querySelector('.keen-folder-name'), 'dragstart');
  fire(p.ui.el('documentsRows'), 'dragstart');
  assert.equal(fire(p.row('f4'), 'dragover').defaultPrevented, false);
  assert.equal(fire(p.row('f4'), 'drop').defaultPrevented, false);
  // Beside the rows nothing is a target.
  fire(p.ui.el('documentsRows').querySelector('[data-doc-drag="d1"]'), 'dragstart');
  const tree = p.ui.el('docFolderPanel').querySelector('[role="tree"]');
  assert.equal(fire(tree, 'dragover').defaultPrevented, false);
  fire(tree, 'dragleave', { relatedTarget: null });
  assert.equal(fire(tree, 'drop').defaultPrevented, false);
  // A drag that ends without a drop forgets what it carried.
  fire(p.ui.el('documentsRows').querySelector('[data-doc-drag="d1"]'), 'dragstart');
  fire(p.row('f4'), 'dragover');
  fire(p.ui.el('documentsRows').querySelector('[data-doc-drag="d1"]'), 'dragend');
  assert.equal(p.row('f4').classList.contains('keen-folder-drop'), false);
  assert.equal(fire(p.row('f4'), 'drop').defaultPrevented, false);
  fire(p.row('f1'), 'dragstart');
  fire(p.row('f1'), 'dragend');
  assert.equal(fire(p.row('f4'), 'dragover').defaultPrevented, false);
  // Toolbar clicks between its buttons do nothing.
  p.ui.el('docFolderPanel').querySelector('[role="toolbar"]').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(p.ui.writes().length, 0);
});

test('readers explore the tree without its toolbar, dragging, renaming or moving', async (t) => {
  const p = await panel(t, { me: { can_view_isms: true } });
  assert.equal(p.ui.el('docFolderPanel').querySelector('[role="toolbar"]'), null);
  assert.equal(p.row('f1').hasAttribute('draggable'), false);
  assert.equal(p.ui.el('documentsRows').querySelector('[data-doc-drag]'), null);
  assert.equal(p.ui.el('documentsRows').querySelector('[data-doc-move]'), null);
  assert.match(
    p.ui.el('docFolderHint').textContent,
    /^Choose a folder to see what is filed in it\.$/,
  );
  p.row('f4').click();
  p.key(p.item('f4'), 'F2');
  p.key(p.item('f4'), 'Delete');
  assert.equal(p.editor(), null);
  assert.deepEqual(p.shown(), [`${hostile}Screening`]);
  assert.equal(p.ui.el('documentsRows').querySelector('img'), null);
});
