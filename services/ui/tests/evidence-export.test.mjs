import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { loadUi } from './support/load-ui.mjs';
import {
  buildZipStore,
  parseContentDispositionFilename,
} from '../public/pages/evidence-export/archive.js';

const evidence = { id: 'e1', source: 'rss', artifacts: [] };
async function exporter(t, preferences = {}, extra = {}) {
  const notices = [];
  const ui = await loadUi('pages/evidence-export/index.js', {
    html: '<main><div id="evidenceCard">alice@example.com 192.0.2.1 2001:db8::1 <button class="no-print">Edit</button></div></main><button id="btnPdf">Export</button><div id="status"></div>',
    vendor: {
      sourceLabel: (v) => String(v || ''),
      fmtTsFilename: (v) => String(v || ''),
      toast: (_el, message) => notices.push(message),
    },
    globals: {
      TextEncoder,
      Blob,
      AbortController,
      NodeFilter: { SHOW_TEXT: 4, FILTER_REJECT: 2, FILTER_ACCEPT: 1 },
      ...extra.globals,
    },
    ...extra,
  });
  t.after(ui.close);
  return {
    ...ui,
    notices,
    exporter: ui.api.createEvidenceExporter({
      preferences,
      getSourceMeta: () => ({}),
      status: ui.document.getElementById('status'),
    }),
  };
}

test('PDF capture masks sample data, preserves the displayed event and cleans up after rendering failure', async (t) => {
  const ui = await exporter(t, { event_data_masking: 'samples' });
  let captured;
  ui.window.html2canvas = async (element) => {
    captured = element.textContent;
    throw new Error('Renderer failed');
  };
  await ui.exporter.downloadPdf(evidence);
  assert.match(captured, /\[MASKED_EMAIL\]/);
  assert.equal((captured.match(/\[MASKED_IP\]/g) || []).length, 2);
  assert.doesNotMatch(captured, /alice|192\.0\.2\.1|2001:db8|Edit/);
  assert.match(ui.document.getElementById('evidenceCard').textContent, /alice@example.com/);
  assert.equal(ui.document.querySelector('.export-capture'), null);
  assert.equal(ui.document.getElementById('btnPdf').disabled, false);
  assert.equal(ui.document.getElementById('btnPdf').textContent, 'Export');
  assert.match(ui.notices.at(-1), /Renderer failed/);
});

test('export preferences are isolated between factory instances', async (t) => {
  const ui = await exporter(t, { event_data_masking: 'false' });
  let captured;
  ui.window.html2canvas = async (element) => {
    captured = element.textContent;
    throw new Error('Stop capture');
  };
  await ui.exporter.downloadPdf(evidence);
  assert.match(captured, /alice@example.com 192\.0\.2\.1/);
});

test('cancelling the PDF modal removes pending generate and shown listeners', async (t) => {
  let hides = 0;
  const other = await exporter(
    t,
    {},
    {
      globals: {
        TextEncoder,
        Blob,
        AbortController,
        bootstrap: {
          Modal: {
            getOrCreateInstance: (el) => ({
              show() {},
              hide() {
                hides++;
                el.dispatchEvent(new el.ownerDocument.defaultView.Event('hidden.bs.modal'));
              },
            }),
          },
        },
      },
    },
  );
  const modal = other.document.createElement('div');
  modal.id = 'pdfOptionsModal';
  modal.innerHTML =
    '<input id="pdfOptPortrait" type="radio"><input id="pdfOptLandscape" type="radio"><button id="pdfOptionsGenerate">Generate</button>';
  other.document.body.append(modal);
  const dialog = other.document.getElementById('pdfOptionsModal');
  for (let i = 0; i < 3; i++) {
    const pending = other.exporter.downloadPdf(evidence);
    dialog.dispatchEvent(new other.window.Event('hidden.bs.modal'));
    await pending;
  }
  dialog.querySelector('button').click();
  assert.equal(hides, 0);
  assert.equal(other.document.getElementById('btnPdf').disabled, false);
});

test('ZIP exports round-trip binary content and Unicode names through Python zipfile', async () => {
  const zip = await buildZipStore([
    { name: '品質.json', data: new TextEncoder().encode('{"ok":true}') },
    { name: 'binary.bin', data: new Uint8Array([0, 255, 1, 128]) },
    { name: 'empty.txt', data: new Uint8Array() },
  ]).arrayBuffer();
  const decoded = spawnSync(
    'python3',
    [
      '-c',
      `import io,sys,zipfile
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:
 assert z.testzip() is None
 assert z.namelist() == ['品質.json','binary.bin','empty.txt']
 assert z.read('品質.json') == b'{"ok":true}'
 assert z.read('binary.bin') == bytes([0,255,1,128])
 assert z.read('empty.txt') == b''
`,
    ],
    { input: Buffer.from(zip), timeout: 5000 },
  );
  assert.equal(decoded.status, 0, decoded.error?.message || decoded.stderr.toString());
  assert.equal(
    parseContentDispositionFilename("attachment; filename*=UTF-8''%E5%93%81%E8%B3%AA.json"),
    '品質.json',
  );
});
