// Measurement examples: the source type a blank field sends, and copying an
// example when the clipboard is unavailable.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

async function examples(t, { values, clipboard }) {
  const ui = await loadUi('pages/measurement-examples.js', {
    url: 'https://keen.example/isms.html?framework=A',
    html: '<form id="effectivenessForm"></form>',
    globals: { navigator: { clipboard } },
  });
  t.after(ui.close);
  const form = ui.document.getElementById('effectivenessForm');
  const update = ui.api.mountMeasurementExamples(form, () => values);
  const details = form.querySelector('details');
  details.open = true;
  update();
  const copy = (label) =>
    [...details.querySelectorAll('button')].find((b) => b.textContent === 'Copy ' + label);
  const code = () => [...details.querySelectorAll('pre code')].map((c) => c.textContent);
  const status = () => details.querySelector(':scope > p[role="status"]').textContent;
  return { details, copy, code, status, update };
}

test('a blank source type sends measurements as from another source', async (t) => {
  for (const sourceType of ['', '   ']) {
    const { code } = await examples(t, {
      values: { key: 'mttr', framework: 'A', unit: 'hours', sourceType },
      clipboard: { writeText: async () => {} },
    });
    const [curl, python] = code();
    assert.match(curl, /"source_type": "other"/);
    assert.match(python, /\\"source_type\\": \\"other\\"/);
  }
  const { code } = await examples(t, {
    values: { key: 'mttr', framework: 'A', unit: 'hours', sourceType: ' github ' },
    clipboard: { writeText: async () => {} },
  });
  assert.match(code()[0], /"source_type": "github"/);
});

test('copying an example says what was copied, or how to copy it by hand', async (t) => {
  const copied = [];
  let refuse = false;
  const { copy, code, status } = await examples(t, {
    values: { key: 'mttr', framework: 'A', unit: 'hours', sourceType: 'github' },
    clipboard: {
      writeText: async (text) => {
        if (refuse) throw new Error('Clipboard blocked');
        copied.push(text);
      },
    },
  });
  await copy('curl').onclick();
  assert.equal(status(), 'Copied curl.');
  assert.deepEqual(copied, [code()[0]]);
  refuse = true;
  await copy('Python (standard library)').onclick();
  assert.equal(status(), 'Select and copy the example text below.');
  assert.equal(copied.length, 1);
});
