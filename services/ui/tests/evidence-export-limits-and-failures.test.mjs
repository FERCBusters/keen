// Evidence exports at their limits: a layout dialog closed while previews wait,
// previews and pages the browser cannot encode, empty and oversized captures,
// artifacts without a body, and the names and dates each artifact gets in a ZIP.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { loadUi } from './support/load-ui.mjs';

const html = await readFile(new URL('../public/event.html', import.meta.url), 'utf8');
const event = { id: 'e1', source: 'rss', timestamp: '2026-10-01T10:00:00Z', artifacts: [] };
const stem = 'Keen Event - e1 RSS feed 2026-10-01T10-00-00Z';
const pause = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

// A dialog that opens on the next tick and closes when asked, as Bootstrap's does.
const dialog = () => ({
  Modal: {
    getOrCreateInstance: (element) => ({
      show: () =>
        setTimeout(
          () =>
            element.dispatchEvent(new element.ownerDocument.defaultView.Event('shown.bs.modal')),
          0,
        ),
      hide: () =>
        element.dispatchEvent(new element.ownerDocument.defaultView.Event('hidden.bs.modal')),
    }),
  },
});

// The event page with its renderer, canvas, object URLs and downloads
// substituted. `encode(canvas, quality)` returns the blob toBlob hands back.
async function exportPage(t, { preferences = {}, bootstrap, fetch, encode, Blob: BlobClass } = {}) {
  const notices = [],
    downloads = [],
    blobs = [],
    renders = [];
  class TestURL extends URL {
    static createObjectURL(blob) {
      blobs.push(blob);
      return 'blob:keen/' + blobs.length;
    }
    static revokeObjectURL() {}
  }
  const ui = await loadUi('pages/evidence-export/index.js', {
    html,
    url: 'https://keen.example/event.html?id=e1',
    setup(window) {
      window.HTMLElement.prototype.getBoundingClientRect = function () {
        const top = Number(this.dataset.top || 0),
          height = Number(this.dataset.height || 0),
          width = this.dataset.width === undefined ? 900 : Number(this.dataset.width);
        return { top, bottom: top + height, height, left: 0, right: width, width };
      };
      window.HTMLCanvasElement.prototype.getContext = () =>
        new Proxy({}, { get: () => () => {}, set: () => true });
      window.HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
        callback(
          encode
            ? encode(this, quality)
            : new Blob([`jpeg ${this.width}x${this.height}`], { type }),
        );
      };
      window.HTMLAnchorElement.prototype.click = function () {
        downloads.push([this.download, this.getAttribute('href')]);
      };
      Object.defineProperty(window.HTMLImageElement.prototype, 'complete', {
        get() {
          return !this.dataset.pending;
        },
      });
      Object.defineProperty(window.HTMLImageElement.prototype, 'naturalWidth', {
        get() {
          return this.dataset.pending ? 0 : 40;
        },
      });
    },
    globals: {
      TextEncoder,
      Blob: BlobClass ?? Blob,
      AbortController,
      NodeFilter: { SHOW_TEXT: 4, FILTER_REJECT: 2, FILTER_ACCEPT: 1 },
      URL: TestURL,
      bootstrap,
      fetch: fetch ?? (async () => assert.fail('No artifact download expected')),
      // Image timeouts and clean-up timers wait for the test; short ones run.
      setTimeout: (fn, ms, ...args) => (ms < 1000 ? setTimeout(fn, ms, ...args) : 0),
    },
    vendor: {
      sourceLabel: (value) => ({ rss: 'RSS feed' })[value] || '',
      fmtTsFilename: (value) => String(value).replace(/[:.]/g, '-'),
      toast: (_element, message, kind) => notices.push([message, kind]),
    },
  });
  t.after(ui.close);
  const exporter = ui.api.createEvidenceExporter({
    preferences,
    getSourceMeta: () => ({}),
    status: ui.document.getElementById('status'),
  });
  let canvasHeight = 600;
  ui.window.html2canvas = async (wrap, options) => {
    renders.push({ width: wrap.style.width, scale: options.scale });
    return { width: 900, height: canvasHeight };
  };
  return {
    ...ui,
    $: (id) => ui.document.getElementById(id),
    exporter,
    notices,
    downloads,
    blobs,
    renders,
    setCanvasHeight: (value) => (canvasHeight = value),
  };
}
const until = async (check) => {
  for (let i = 0; i < 200 && !check(); i++) await pause(2);
  assert.ok(check(), 'The export did not reach the expected state');
};

test('closing the layout dialog while previews wait for images captures nothing', async (t) => {
  const ui = await exportPage(t, { bootstrap: dialog() });
  ui.$('imagePreviews').innerHTML =
    '<img class="evidence-img" src="/slow.png" data-pending="1" data-height="40">';
  const pending = ui.exporter.downloadPdf(event);
  // Both previews copy the page and wait for its thumbnail.
  await until(() => ui.document.querySelectorAll('.export-capture').length === 2);
  ui.$('pdfOptionsModal').dispatchEvent(new ui.window.Event('hidden.bs.modal'));
  await pending;
  assert.deepEqual(ui.notices, [['PDF export cancelled', 'info']]);
  for (const image of ui.document.querySelectorAll('.export-capture img'))
    image.dispatchEvent(new ui.window.Event('error'));
  await until(() => ui.document.querySelector('.export-capture') === null);
  await pause();
  assert.deepEqual(ui.renders, []);
  assert.deepEqual(ui.blobs, []);
  assert.equal(ui.$('pdfPreviewPortraitImg').hasAttribute('src'), false);
});

test('previews of a page without a measured width are drawn 900 pixels wide and revealed once loaded', async (t) => {
  const ui = await exportPage(t, { bootstrap: dialog() });
  ui.document.querySelector('main').dataset.width = '0';
  // The portrait preview image is still decoding when its source is set.
  ui.$('pdfPreviewPortraitImg').dataset.pending = '1';
  const frame = (id) => ui.$(id).closest('.pdf-preview-frame');
  const pending = ui.exporter.downloadPdf(event);
  await until(() => ui.$('pdfPreviewStatus').style.display === 'none' && ui.renders.length === 2);
  assert.deepEqual(
    ui.renders.map((r) => [r.width, r.scale]),
    [
      ['900px', 0.85],
      ['900px', 0.85],
    ],
  );
  assert.ok(ui.$('pdfPreviewPortraitImg').src.startsWith('blob:keen/'));
  assert.equal(frame('pdfPreviewPortraitImg').classList.contains('has-img'), false);
  assert.equal(frame('pdfPreviewLandscapeImg').classList.contains('has-img'), true);
  delete ui.$('pdfPreviewPortraitImg').dataset.pending;
  ui.$('pdfPreviewPortraitImg').dispatchEvent(new ui.window.Event('load'));
  assert.equal(frame('pdfPreviewPortraitImg').classList.contains('has-img'), true);

  // The space bar chooses a layout as Enter and a click do.
  const landscape = ui.document.querySelector('.pdf-preview-option[data-orientation="landscape"]');
  const space = new ui.window.KeyboardEvent('keydown', { key: ' ', cancelable: true });
  landscape.dispatchEvent(space);
  assert.equal(space.defaultPrevented, true);
  assert.equal(ui.$('pdfOptLandscape').checked, true);
  assert.equal(landscape.classList.contains('selected'), true);
  const tab = new ui.window.KeyboardEvent('keydown', { key: 'Tab', cancelable: true });
  ui.document.querySelector('.pdf-preview-option[data-orientation="portrait"]').dispatchEvent(tab);
  assert.equal(tab.defaultPrevented, false);
  assert.equal(ui.$('pdfOptLandscape').checked, true);

  ui.$('pdfOptionsGenerate').click();
  await pending;
  assert.deepEqual(ui.renders.at(-1), { width: '900px', scale: 2 });
  assert.deepEqual(ui.downloads, [[stem + '.pdf', 'blob:keen/3']]);
});

test('a page the browser cannot encode stops the PDF with a reason', async (t) => {
  const ui = await exportPage(t, { encode: () => null });
  await ui.exporter.downloadPdf(event);
  assert.deepEqual(ui.notices.at(-1), ['Failed to encode PDF page image', 'danger']);
  assert.deepEqual(ui.downloads, []);
  assert.equal(ui.$('btnPdf').disabled, false);
  assert.equal(ui.$('btnPdf').textContent, 'Sample as PDF evidence');
});

test('previews the browser cannot encode are left out and the PDF still downloads', async (t) => {
  const ui = await exportPage(t, {
    bootstrap: dialog(),
    // Previews are encoded at quality 0.82 and PDF pages at 0.92.
    encode: (canvas, quality) => (quality === 0.82 ? null : new Blob(['jpeg'])),
  });
  const pending = ui.exporter.downloadPdf(event);
  await until(() => ui.$('pdfPreviewStatus').style.display === 'none' && ui.renders.length === 2);
  assert.equal(ui.$('pdfPreviewPortraitImg').hasAttribute('src'), false);
  assert.equal(ui.$('pdfPreviewLandscapeImg').hasAttribute('src'), false);
  assert.deepEqual(ui.blobs, []);
  ui.$('pdfOptionsGenerate').click();
  await pending;
  assert.deepEqual(ui.downloads, [[stem + '.pdf', 'blob:keen/1']]);
  assert.deepEqual(ui.notices.at(-1), ['PDF download started', 'success']);
});

test('a capture with no height has no pages to export', async (t) => {
  const ui = await exportPage(t);
  ui.setCanvasHeight(0);
  await ui.exporter.downloadPdf(event);
  assert.deepEqual(ui.notices.at(-1), ['No pages to export', 'danger']);
  assert.deepEqual(ui.downloads, []);
});

test('an evidence PDF over the ZIP limit stops the ZIP before any artifact downloads', async (t) => {
  class LargePdfBlob extends Blob {
    get size() {
      return this.type === 'application/pdf' ? 100 * 1024 * 1024 + 1 : super.size;
    }
  }
  const ui = await exportPage(t, { Blob: LargePdfBlob });
  await ui.exporter.downloadArtifactsZip({ ...event, artifacts: [{ id: 'a1' }] });
  assert.deepEqual(ui.notices.at(-1), [
    'Evidence PDF exceeds the 100 MiB ZIP export limit.',
    'danger',
  ]);
  assert.deepEqual(ui.downloads, []);
  assert.equal(ui.$('btnZip').disabled, false);
  assert.equal(ui.$('btnZip').textContent, 'Download all artifacts as evidence');
});

test('an artifact response without a body stops the ZIP', async (t) => {
  const ui = await exportPage(t, {
    // A proxy answering 204 has no body to read.
    fetch: async () => ({ ok: true, status: 204, headers: new Headers(), body: null }),
  });
  await ui.exporter.downloadArtifactsZip({ ...event, artifacts: [{ id: 'a1' }] });
  assert.deepEqual(ui.notices.at(-1), ['Artifact response has no readable body', 'danger']);
  assert.deepEqual(ui.downloads, []);
});

test('ZIP entries take their extension from the content type and dates from 1980 on', async (t) => {
  const types = {
    pdf: 'application/pdf',
    json: 'application/json; charset=utf-8',
    html: 'text/html',
    csv: 'text/csv',
    jpg: 'image/jpeg',
    gif: 'image/gif',
  };
  const ui = await exportPage(t, {
    fetch: async () => new Response(new TextEncoder().encode('data'), { headers: {} }),
  });
  await ui.exporter.downloadArtifactsZip({
    ...event,
    artifacts: [
      ...Object.entries(types).map(([id, contentType]) => ({ id, content_type: contentType })),
      // An epoch capture time predates what a ZIP entry can record.
      { id: 'old', content_type: 'text/plain', captured_at: '1970-01-01T00:00:00Z' },
    ],
  });
  assert.deepEqual(ui.downloads, [[stem + '.zip', 'blob:keen/1']]);
  const listing = spawnSync(
    'python3',
    [
      '-c',
      `import io,json,sys,zipfile
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:
 assert z.testzip() is None
 print(json.dumps([[i.filename, i.date_time[0]] for i in z.infolist()]))
`,
    ],
    { input: Buffer.from(await ui.blobs.at(-1).arrayBuffer()), timeout: 5000 },
  );
  assert.equal(listing.status, 0, listing.stderr.toString());
  const entries = JSON.parse(listing.stdout.toString());
  assert.deepEqual(
    entries.map(([name]) => name),
    [
      stem + '-evidence.pdf',
      ...Object.keys(types).map((ext) => `${stem}-artifact-${ext}.${ext}`),
      stem + '-artifact-old.txt',
    ],
  );
  assert.equal(entries.at(-1)[1], 1980);
  assert.ok(entries[0][1] >= 2026);
});

test('elements without inline styles are passed over when colours are made drawable', async (t) => {
  const reported = (styles) => JSON.stringify(styles).replaceAll('"', '&quot;');
  const ui = await loadUi('pages/evidence-export/pdf.js', {
    // MathML elements carry no inline style in this browser model.
    html: `<div id="copy"><math id="formula" data-computed="${reported({ color: 'oklch(0.4 0.1 250)' })}"><mi>x</mi></math><span id="badge" data-computed="${reported({ color: 'oklch(0.4 0.1 250)' })}">x</span></div>`,
    setup(window) {
      window.HTMLCanvasElement.prototype.getContext = () => {
        let fill = '#000000';
        return {
          get fillStyle() {
            return fill;
          },
          set fillStyle(value) {
            fill = value;
          },
          clearRect() {},
          fillRect() {},
          getImageData: () => ({ data: [10, 20, 30, 255] }),
        };
      };
      const computed = window.getComputedStyle.bind(window);
      window.getComputedStyle = (el) => {
        const style = computed(el);
        const values = JSON.parse(el.getAttribute('data-computed') || '{}');
        return { getPropertyValue: (name) => values[name] ?? style.getPropertyValue(name) };
      };
    },
  });
  t.after(() => ui.close());
  ui.window.html2canvas = async (element, options) => {
    await options.onclone(ui.document, element);
    return { width: 1, height: 1 };
  };
  await ui.api.captureCanvas(ui.document.getElementById('copy'));
  assert.equal(
    ui.document.getElementById('badge').style.getPropertyValue('color'),
    'rgb(10, 20, 30)',
  );
  assert.equal(ui.document.getElementById('formula').style, undefined);
});
