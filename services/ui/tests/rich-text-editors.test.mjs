import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { firstPartyScripts } from './support/first-party-scripts.mjs';

// The shared rich-text editor integration: one Wysi editor per marked
// textarea, content conversion in both directions and read-only states.
const FORM = `<form id="form">
  <div class="rich-text-editor"><textarea id="scope" data-rich-text="1" data-rich-text-height="120"># Scope

- Offices</textarea></div>
  <div class="rich-text-editor"><textarea id="locked" data-rich-text="1" disabled>&lt;p&gt;Approved&lt;/p&gt;</textarea></div>
</form>`;

// A stand-in for the vendored Wysi editor: a toolbar and an editable region
// placed after the textarea, as the editor builds them. Like Wysi 0.5.1, it
// starts from the textarea's value and wraps top-level text in a paragraph by
// assigning the text as HTML, in the live document, so escaped markup it is
// given becomes elements; it does the same to the editor's own content on
// every input event, from a bubbling listener on the document. The elements
// that re-parsing creates are recorded in editors.reparsed: a browser loads
// an image created this way, and runs its handlers, even though the element
// is never attached to the page.
function installWysi(window, { fail = () => false } = {}) {
  const editors = [];
  editors.reparsed = [];
  const doc = window.document;
  const wysiContent = (raw) => {
    const template = doc.createElement('template');
    template.innerHTML = String(raw || '').trim();
    for (const node of Array.from(template.content.childNodes)) {
      if (node.nodeType !== window.Node.TEXT_NODE) continue;
      const p = doc.createElement('p');
      p.innerHTML = node.textContent;
      editors.reparsed.push(...[...p.querySelectorAll('*')].map((el) => el.tagName));
      node.replaceWith(p);
    }
    const out = doc.createElement('div');
    out.append(template.content);
    return out.innerHTML;
  };
  editors.wysiContent = wysiContent;
  const build = (options, editor) => {
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
    editor.started = options.el.value;
    editable.innerHTML = wysiContent(options.el.value);
    editor.editable = editable;
    // Wysi reports edits with its own re-parsed copy of the editor content,
    // read in a bubbling input listener on the document.
    doc.addEventListener('input', (event) => {
      if (event.target === editable) options.onChange(wysiContent(editable.innerHTML));
    });
    editor.input = (init = {}) =>
      editable.dispatchEvent(new window.InputEvent('input', { bubbles: true, ...init }));
  };
  window.Wysi = (options) => {
    if (fail(options.el)) throw new Error('Editor failed to start');
    const editor = { options, editable: null, started: null };
    editors.push(editor);
    if (doc.readyState === 'loading') {
      doc.addEventListener('DOMContentLoaded', () => build(options, editor));
    } else {
      build(options, editor);
    }
  };
  // Wysi cleans pasted HTML in a bubbling listener on the document.
  editors.wysiPastes = [];
  doc.addEventListener('paste', (event) => {
    editors.wysiPastes.push(event);
    event.target.insertAdjacentHTML?.(
      'beforeend',
      wysiContent(event.clipboardData.getData('text/html')),
    );
  });
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
  // Saved plain text becomes escaped paragraphs; it is not read as Markdown.
  const html = '<p># Scope</p>\n<p>- Offices</p>';
  assert.equal(ui.el('scope').value, html);
  assert.equal(scope.editable.innerHTML, html);
  // Wysi itself is always handed an empty field.
  assert.equal(scope.started, '');
  assert.equal(locked.started, '');
  assert.equal(locked.editable.innerHTML, '<p>Approved</p>');
  for (const id of ['scope', 'locked']) {
    const holder = ui.el(id).closest('.rich-text-editor');
    assert.equal(holder.dataset.richTextReady, '1');
    assert.equal(ui.el(id).dataset.wysiFallback, '1');
  }

  // Edits flow back into the form field as the editor shows them.
  scope.editable.innerHTML = '<p>Edited</p>';
  scope.input();
  assert.equal(ui.el('scope').value, '<p>Edited</p>');
  assert.deepEqual(changes, [['scope', '<p>Edited</p>']]);
  scope.editable.innerHTML = '';
  scope.input();
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
  assert.equal(ui.el('locked').value, '<p>**Reviewed**</p>');
  assert.equal(locked.editable.innerHTML, '<p>**Reviewed**</p>');
});

test('hostile HTML-like values never reach the editable as markup', async (t) => {
  const ui = await editorPage(t);
  ui.api.initRichTextEditors(ui.document);
  const [, locked] = ui.editors();

  ui.api.setRichTextEditorValue(
    'locked',
    '<p onclick="alert(1)">Reviewed</p><img src=x onerror=alert(1)>' +
      '<a href="javascript:alert(1)">link</a>',
  );

  const editable = locked.editable;
  assert.equal(editable.querySelector('img'), null);
  assert.equal(editable.innerHTML.includes('onclick'), false);
  assert.equal(editable.innerHTML.includes('onerror'), false);
  assert.equal(editable.querySelector('a').hasAttribute('href'), false);
  assert.equal(editable.textContent, 'Reviewedlink');
  // The textarea holds the same sanitised value the editor shows.
  assert.equal(ui.el('locked').value, editable.innerHTML);
});

// Escaped markup is text in a saved value; Wysi would turn it into elements.
const ESCAPED =
  '&lt;meta http-equiv="refresh" content="0;url=https://evil.example/"&gt;' +
  '&lt;img src="/x" id="probe"&gt;';

test('escaped markup in a saved value stays text when the editor starts', async (t) => {
  const ui = await loadUi('app.js', {
    html: `<div class="rich-text-editor"><textarea id="notes" data-rich-text="1">${ESCAPED.replaceAll('&', '&amp;')}</textarea></div>`,
    setup(window) {
      window.editors = installWysi(window);
    },
  });
  t.after(ui.close);
  ui.api.initRichTextEditors(ui.document);
  const [notes] = ui.window.editors;
  assert.equal(notes.started, '');
  assert.equal(ui.document.querySelector('meta'), null);
  assert.equal(ui.document.getElementById('probe'), null);
  // A saved value without markup is plain text, shown exactly as stored.
  assert.equal(notes.editable.textContent, ESCAPED);
  // The user's first edit does not re-parse it either.
  notes.input();
  assert.equal(ui.document.querySelector('meta'), null);
  assert.equal(ui.document.getElementById('notes').value, notes.editable.innerHTML);
});

// Saved rich text whose top-level text holds escaped markup: Wysi re-parses
// that text on every edit, so it must sit inside a paragraph.
const HOSTILE_RICH = '&lt;img src="/x" id="probe" onerror="x()"&gt;<br>';

test('escaped markup in saved rich text stays text while the user edits', async (t) => {
  const ui = await loadUi('app.js', {
    html: '<div class="rich-text-editor"><textarea id="notes" data-rich-text="1"></textarea></div>',
    setup(window) {
      window.editors = installWysi(window);
    },
  });
  t.after(ui.close);
  ui.api.initRichTextEditors(ui.document);
  const [notes] = ui.window.editors;

  ui.api.setRichTextEditorValue('notes', HOSTILE_RICH);

  assert.equal(notes.editable.innerHTML, `<p>${HOSTILE_RICH}</p>`);
  assert.equal(ui.document.getElementById('notes').value, notes.editable.innerHTML);
  // The user types into the paragraph; Wysi's re-parse finds no top-level text.
  notes.editable.firstChild.firstChild.appendData('z');
  notes.input();
  assert.deepEqual(ui.window.editors.reparsed, []);
  assert.equal(ui.document.getElementById('probe'), null);
  assert.equal(notes.editable.textContent, '<img src="/x" id="probe" onerror="x()">z');
});

test('the stand-in editor detects a re-parse of top-level text', async (t) => {
  // The check above would fail without the paragraphs: given the same saved
  // value unwrapped, the stand-in's re-parse creates the image.
  const ui = await loadUi('app.js', {
    html: '<div></div>',
    setup(window) {
      window.editors = installWysi(window);
    },
  });
  t.after(ui.close);
  ui.window.editors.wysiContent(HOSTILE_RICH);
  assert.deepEqual(ui.window.editors.reparsed, ['IMG']);
});

test('text that reaches the top level is moved into a paragraph before Wysi reads it', async (t) => {
  const ui = await loadUi('app.js', {
    html: '<div class="rich-text-editor"><textarea id="notes" data-rich-text="1"></textarea></div>',
    setup(window) {
      window.editors = installWysi(window);
    },
  });
  t.after(ui.close);
  ui.api.initRichTextEditors(ui.document);
  const [notes] = ui.window.editors;
  const { editable } = notes;
  // Text typed into an empty editor, or pasted as plain text, lands at the top
  // level, here beside a phrasing element and between blocks.
  editable.innerHTML = '<p>Kept</p>\n<ul><li>Item</li></ul>';
  const typed = ui.document.createTextNode('<img src="/x" id="probe" onerror="x()">');
  const bold = ui.document.createElement('strong');
  bold.textContent = 'bold';
  editable.append(typed, bold);
  const selection = ui.window.getSelection();
  selection.setBaseAndExtent(typed, 4, typed, 4);

  notes.input({ inputType: 'insertText', data: 'x' });

  assert.equal(
    editable.innerHTML,
    '<p>Kept</p>\n<ul><li>Item</li></ul><p>&lt;img src="/x" id="probe" onerror="x()"&gt;<strong>bold</strong></p>',
  );
  assert.deepEqual(ui.window.editors.reparsed, []);
  // The caret stays where the user was typing.
  assert.equal(selection.anchorNode, typed);
  assert.equal(selection.anchorOffset, 4);
  // Text composed through an input method is left until it is committed.
  const composing = ui.document.createTextNode('<b>');
  editable.append(composing);
  editable.dispatchEvent(new ui.window.InputEvent('input', { bubbles: true, isComposing: true }));
  assert.equal(editable.lastChild, composing);
});

test('a caret between top-level nodes keeps its place when text is wrapped', async (t) => {
  const ui = await loadUi('app.js', {
    html: '<div class="rich-text-editor"><textarea id="notes" data-rich-text="1"></textarea></div>',
    setup(window) {
      window.editors = installWysi(window);
    },
  });
  t.after(ui.close);
  ui.api.initRichTextEditors(ui.document);
  const [notes] = ui.window.editors;
  const { editable } = notes;
  editable.innerHTML = 'one<hr>two';
  const selection = ui.window.getSelection();
  // Between "one" and the rule, and at the end of the editor.
  selection.setBaseAndExtent(editable, 1, editable, 3);

  notes.input();

  assert.equal(editable.innerHTML, '<p>one</p><hr><p>two</p>');
  assert.deepEqual(
    [selection.anchorNode, selection.anchorOffset, selection.focusNode, selection.focusOffset],
    [editable, 1, editable, 3],
  );
});

test('an editor built after the document loads still starts empty and is filled safely', async (t) => {
  let readyState = 'loading';
  const ui = await loadUi('app.js', {
    html:
      `<div class="rich-text-editor"><textarea id="notes" data-rich-text="1" readonly>${ESCAPED.replaceAll('&', '&amp;')}</textarea></div>` +
      '<div id="later"><div class="rich-text-editor"><textarea id="summary" data-rich-text="1">Second</textarea></div></div>',
    setup(window) {
      Object.defineProperty(window.document, 'readyState', { get: () => readyState });
      window.editors = installWysi(window);
    },
  });
  t.after(ui.close);
  const textarea = ui.document.getElementById('notes');

  ui.api.initRichTextEditors(textarea.parentElement);
  ui.api.setRichTextEditorValue(textarea, ESCAPED);
  // A page that starts another editor later, before the document has loaded.
  ui.api.initRichTextEditors(ui.document.getElementById('later'));
  // Until Wysi builds its editor the field is empty and saving keeps the value.
  assert.equal(textarea.value, '');
  const shown = ui.api.richTextEditorValue(ESCAPED);
  assert.equal(ui.api.syncRichTextEditor(textarea), shown);

  readyState = 'interactive';
  ui.document.dispatchEvent(new ui.window.Event('DOMContentLoaded'));

  const [notes] = ui.window.editors;
  assert.equal(notes.started, '');
  assert.equal(ui.document.querySelector('meta'), null);
  assert.equal(ui.document.getElementById('probe'), null);
  assert.equal(notes.editable.textContent, ESCAPED);
  assert.equal(textarea.value, notes.editable.innerHTML);
  assert.equal(notes.editable.getAttribute('contenteditable'), 'false');
  const summary = ui.window.editors[1];
  assert.equal(summary.started, '');
  assert.equal(summary.editable.innerHTML, '<p>Second</p>');
  assert.equal(ui.document.getElementById('summary').value, '<p>Second</p>');
});

test('pasted HTML is sanitised before Wysi can re-parse it', async (t) => {
  const inserted = [];
  const ui = await loadUi('app.js', {
    html: '<div class="rich-text-editor"><textarea id="notes" data-rich-text="1"></textarea></div><p id="outside"></p>',
    setup(window) {
      window.editors = installWysi(window);
      window.document.execCommand = (command, _ui, value) => {
        inserted.push([command, value]);
        return true;
      };
    },
  });
  t.after(ui.close);
  ui.api.initRichTextEditors(ui.document);
  const [notes] = ui.window.editors;
  const paste = (target, data) => {
    const event = new ui.window.Event('paste', { bubbles: true, cancelable: true });
    event.clipboardData = {
      types: Object.keys(data),
      getData: (type) => data[type] || '',
    };
    target.dispatchEvent(event);
    return event;
  };

  const event = paste(notes.editable, {
    'text/html': `<strong onclick="x()">Bold</strong>${ESCAPED}<script>x()</script>`,
    'text/plain': 'Bold',
  });
  assert.equal(event.defaultPrevented, true);
  assert.equal(ui.window.editors.wysiPastes.length, 0);
  assert.equal(inserted.length, 1);
  const [command, html] = inserted[0];
  assert.equal(command, 'insertHTML');
  const probe = ui.document.createElement('div');
  probe.innerHTML = html;
  assert.deepEqual(
    [...probe.querySelectorAll('*')].map((el) => el.tagName),
    ['STRONG'],
  );
  assert.equal(probe.innerHTML.includes('onclick'), false);
  assert.equal(probe.textContent.includes('<meta http-equiv="refresh"'), true);

  // Markup that leaves nothing to keep falls back to the plain text.
  paste(notes.editable, { 'text/html': '<img src="/x">', 'text/plain': 'picture' });
  assert.deepEqual(inserted[1], ['insertText', 'picture']);

  // Plain-text pastes and pastes elsewhere keep their usual behaviour.
  assert.equal(paste(notes.editable, { 'text/plain': 'text' }).defaultPrevented, false);
  assert.equal(
    paste(ui.document.getElementById('outside'), { 'text/html': '<b>x</b>' }).defaultPrevented,
    false,
  );
  assert.equal(inserted.length, 2);
});

test('Wysi is started only by the shared rich-text helper', async () => {
  // Any other caller would hand Wysi content without the protections above.
  const scripts = await firstPartyScripts();
  assert.ok(scripts.some((script) => script.path === 'app.js'));
  const offenders = scripts
    .filter((script) => script.path !== 'app.js' && /\bWysi\b/.test(script.source))
    .map((script) => script.path);
  assert.deepEqual(offenders, []);
  // The check would find a page that starts Wysi itself.
  assert.match('window.Wysi({ el })', /\bWysi\b/);
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
