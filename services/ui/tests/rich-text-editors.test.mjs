import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// The shared rich-text editor integration: one Wysi editor per marked
// textarea, content conversion in both directions and read-only states.
const FORM = `<form id="form">
  <div class="rich-text-editor"><textarea id="scope" data-rich-text="1" data-rich-text-height="120"># Scope

- Offices</textarea></div>
  <div class="rich-text-editor"><textarea id="locked" data-rich-text="1" disabled>&lt;p&gt;Approved&lt;/p&gt;</textarea></div>
</form>`;

// A stand-in for the vendored Wysi editor: a toolbar and an editable region
// placed after the textarea, as the editor builds them.
function installWysi(window, { fail = () => false } = {}) {
  const editors = [];
  window.Wysi = (options) => {
    if (fail(options.el)) throw new Error('Editor failed to start');
    const doc = options.el.ownerDocument;
    const wrapper = doc.createElement('div');
    const toolbar = doc.createElement('div');
    for (const tool of options.tools.filter((tool) => tool !== '|')) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.dataset.tool = tool;
      toolbar.append(button);
    }
    const editable = doc.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    wrapper.append(toolbar, editable);
    options.el.after(wrapper);
    const editor = {
      options,
      editable,
      content: [],
      setContent(html) {
        this.content.push(html);
      },
    };
    editors.push(editor);
    return editor;
  };
  return editors;
}

async function editorPage(t, { wysi = true, fail, warnings = [] } = {}) {
  let editors = [];
  const ui = await loadUi('app.js', {
    html: FORM,
    globals: { console: { ...console, warn: (...args) => warnings.push(args) } },
    setup(window) {
      if (wysi) editors = installWysi(window, { fail });
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  return { ...ui, editors: () => editors, el, warnings };
}

test('each marked textarea gets one editor that starts from its saved content', async (t) => {
  const ui = await editorPage(t);
  const changes = [];

  const started = ui.api.initRichTextEditors(ui.document, {
    onChange: (textarea, value) => changes.push([textarea.id, value]),
  });

  assert.deepEqual(
    [...started].map((textarea) => textarea.id),
    ['scope', 'locked'],
  );
  const [scope, locked] = ui.editors();
  assert.equal(scope.options.el, ui.el('scope'));
  assert.equal(scope.options.height, 120);
  assert.equal(locked.options.height, 260);
  assert.equal(scope.options.autoGrow, true);
  assert.equal(scope.options.autoHide, false);
  assert.deepEqual(
    [...scope.options.tools],
    [
      'format',
      '|',
      'bold',
      'italic',
      'underline',
      'strike',
      '|',
      'ul',
      'ol',
      '|',
      'link',
      'hr',
      'quote',
      '|',
      'removeFormat',
    ],
  );
  // Saved Markdown becomes the editor's HTML.
  const html = '<h1>Scope</h1>\n<ul><li>Offices</li></ul>';
  assert.equal(ui.el('scope').value, html);
  assert.deepEqual(scope.content, [html]);
  assert.equal(scope.editable.innerHTML, html);
  assert.equal(locked.editable.innerHTML, '<p>Approved</p>');
  for (const id of ['scope', 'locked']) {
    const holder = ui.el(id).closest('.rich-text-editor');
    assert.equal(holder.dataset.richTextReady, '1');
    assert.equal(ui.el(id).dataset.wysiFallback, '1');
  }

  // Edits flow back into the form field.
  scope.options.onChange('<p>Edited</p>');
  assert.equal(ui.el('scope').value, '<p>Edited</p>');
  assert.deepEqual(changes, [['scope', '<p>Edited</p>']]);
  scope.options.onChange(null);
  assert.equal(ui.el('scope').value, '');

  // Starting again leaves existing editors alone.
  assert.equal(ui.api.initRichTextEditors(ui.document).length, 0);
  assert.equal(ui.editors().length, 2);
});

test('read-only fields show read-only editors with inactive toolbars', async (t) => {
  const ui = await editorPage(t);
  ui.api.initRichTextEditors(ui.document);
  const [scope, locked] = ui.editors();
  const state = (editor) => ({
    holder: editor.options.el.closest('.rich-text-editor').dataset.richTextReadonly,
    editable: editor.editable.getAttribute('contenteditable'),
    aria: editor.editable.getAttribute('aria-readonly'),
    tools: [...editor.editable.parentElement.firstChild.children].map((b) => [
      b.disabled,
      b.tabIndex,
    ]),
  });

  assert.deepEqual(state(locked).holder, '1');
  assert.equal(state(locked).editable, 'false');
  assert.equal(state(locked).aria, 'true');
  assert.ok(state(locked).tools.every(([disabled, tab]) => disabled && tab === -1));
  assert.equal(state(scope).editable, 'true');
  assert.ok(state(scope).tools.every(([disabled, tab]) => !disabled && tab === 0));
  // Controls inside the content, such as a task-list checkbox, belong to the
  // document and keep their own state.
  locked.editable.insertAdjacentHTML('beforeend', '<input type="checkbox">');
  ui.api.refreshRichTextEditorStates(ui.document);
  assert.equal(locked.editable.querySelector('input').disabled, false);
  assert.equal(locked.editable.querySelector('input').tabIndex, 0);

  ui.el('scope').readOnly = true;
  ui.el('locked').disabled = false;
  ui.api.refreshRichTextEditorStates(ui.document);
  assert.equal(state(scope).editable, 'false');
  assert.equal(state(scope).holder, '1');
  assert.equal(state(locked).editable, 'true');
  assert.equal(state(locked).aria, 'false');
});

test('editor content is copied into the form before saving and replaced on load', async (t) => {
  const ui = await editorPage(t);
  ui.api.initRichTextEditors(ui.document);
  const [scope, locked] = ui.editors();
  scope.editable.innerHTML = '<p>Typed in the editor</p>';

  ui.api.syncRichTextEditors(ui.el('form'));

  assert.equal(ui.el('scope').value, '<p>Typed in the editor</p>');
  ui.api.setRichTextEditorValue('locked', '**Reviewed**');
  assert.equal(ui.el('locked').value, '<p><strong>Reviewed</strong></p>');
  assert.equal(locked.content.at(-1), '<p><strong>Reviewed</strong></p>');
  assert.equal(locked.editable.innerHTML, '<p><strong>Reviewed</strong></p>');
});

test('caller options replace the editor defaults', async (t) => {
  const ui = await editorPage(t);
  ui.api.initRichTextEditors(ui.el('scope').parentElement, {
    height: 400,
    autoGrow: false,
    autoHide: true,
    tools: ['bold'],
  });
  const [scope] = ui.editors();
  assert.equal(ui.editors().length, 1);
  assert.deepEqual(
    [
      scope.options.height,
      scope.options.autoGrow,
      scope.options.autoHide,
      [...scope.options.tools],
    ],
    [400, false, true, ['bold']],
  );
});

test('an editor that fails to start leaves its plain textarea and the others work', async (t) => {
  const warnings = [];
  const ui = await editorPage(t, {
    warnings,
    fail: (textarea) => textarea.id === 'scope',
  });

  const started = ui.api.initRichTextEditors(ui.document);

  assert.deepEqual(
    [...started].map((textarea) => textarea.id),
    ['locked'],
  );
  assert.equal(ui.el('scope').closest('.rich-text-editor').dataset.richTextReady, undefined);
  assert.equal(ui.el('scope').dataset.wysiFallback, '1');
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0][0], 'Failed to initialise rich text editor');
});

test('without the editor script the fields stay plain textareas', async (t) => {
  const ui = await editorPage(t, { wysi: false });
  assert.equal(ui.api.initRichTextEditors(ui.document).length, 0);
  assert.equal(ui.el('scope').dataset.wysiFallback, undefined);
  assert.match(ui.el('scope').value, /^# Scope/);
});
