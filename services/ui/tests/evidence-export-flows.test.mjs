// Evidence PDF, preview, ZIP and print exports on the event page markup. The
// canvas, renderer and network are substituted; KEEN's pagination, masking and
// naming run unchanged.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { loadUi } from './support/load-ui.mjs';
const html = await readFile(new URL('../public/event.html', import.meta.url), 'utf8');
const event = { id: 'e1', source: 'rss', timestamp: '2026-10-01T10:00:00Z', artifacts: [] };
const stem = 'Keen Event - e1 RSS feed 2026-10-01T10-00-00Z';

async function exportPage(t, preferences = {}, { bootstrap, fetch, storage } = {}) {
  const notices = [],
    downloads = [],
    blobs = [],
    revoked = [],
    draws = [],
    renders = [],
    timers = [];
  let prints = 0,
    closes = 0;
  class TestURL extends URL {
    static createObjectURL(blob) {
      blobs.push(blob);
      return 'blob:keen/' + blobs.length;
    }
    static revokeObjectURL(url) {
      revoked.push(url);
    }
  }
  const ui = await loadUi('pages/evidence-export/index.js', {
    html,
    url: 'https://keen.example/event.html?id=e1',
    setup(window) {
      // Layout comes from data attributes so cloned capture nodes keep it.
      window.HTMLElement.prototype.getBoundingClientRect = function () {
        const top = Number(this.dataset.top || 0),
          height = Number(this.dataset.height || 0);
        return { top, bottom: top + height, height, left: 0, right: 900, width: 900 };
      };
      window.HTMLCanvasElement.prototype.getContext = function () {
        return new Proxy(
          {},
          {
            get: (_target, name) =>
              name === 'fillText' || name === 'drawImage'
                ? (...args) => draws.push([name, ...args])
                : () => {},
            set: () => true,
          },
        );
      };
      window.HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
        callback(new Blob([`jpeg ${this.width}x${this.height} ${quality}`], { type }));
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
      window.print = () => prints++;
      const close = window.close;
      window.close = function () {
        closes++;
        return close.call(this);
      };
    },
    globals: {
      TextEncoder,
      Blob,
      AbortController,
      NodeFilter: { SHOW_TEXT: 4, FILTER_REJECT: 2, FILTER_ACCEPT: 1 },
      URL: TestURL,
      bootstrap,
      fetch,
      localStorage: storage,
      // Clean-up and image timeouts run when a test advances them.
      setTimeout: (fn, ms, ...args) => {
        if (ms < 1000) return setTimeout(fn, ms, ...args);
        timers.push({ fn, ms });
        return 0;
      },
    },
    vendor: {
      sourceLabel: (value) => ({ rss: 'RSS feed' })[value] || '',
      fmtTsFilename: (value) => String(value).replace(/[:.]/g, '-'),
      toast: (element, message, kind) => {
        notices.push([message, kind]);
        if (element) element.style.display = '';
      },
    },
  });
  t.after(ui.close);
  const exporter = ui.api.createEvidenceExporter({
    preferences,
    getSourceMeta: () => ({}),
    status: ui.document.getElementById('status'),
  });
  // The renderer returns a canvas of the requested height for each capture.
  let canvasHeight = 3000;
  ui.window.html2canvas = async (wrap, options) => {
    renders.push({ options, text: wrap.textContent, wrap });
    return { width: 900, height: canvasHeight };
  };
  return {
    ...ui,
    exporter,
    notices,
    downloads,
    blobs,
    revoked,
    draws,
    renders,
    timers,
    prints: () => prints,
    closes: () => closes,
    setCanvasHeight: (value) => (canvasHeight = value),
    runTimers(ms) {
      for (const timer of timers.filter((item) => item.ms === ms && !item.ran)) {
        timer.ran = true;
        timer.fn();
      }
    },
  };
}
const pdfText = async (blob) => Buffer.from(await blob.arrayBuffer()).toString('latin1');
// Poppler reads the document structure, cross-reference table included.
async function pdfInfo(blob) {
  const dir = await mkdtemp(join(tmpdir(), 'keen-pdf-'));
  try {
    const file = join(dir, 'export.pdf');
    await writeFile(file, Buffer.from(await blob.arrayBuffer()));
    const result = spawnSync('pdfinfo', [file], { timeout: 5000 });
    assert.equal(result.status, 0, result.error?.message || result.stderr.toString());
    assert.equal(result.stderr.toString(), '');
    return Object.fromEntries(
      result.stdout
        .toString()
        .split('\n')
        .filter(Boolean)
        .map((line) => [
          line.slice(0, line.indexOf(':')),
          line.slice(line.indexOf(':') + 1).trim(),
        ]),
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}
const pageCount = (text) => (text.match(/\/Type \/Page /g) || []).length;
const slices = (ui) =>
  ui.draws.filter(([name]) => name === 'drawImage').map(([, , , y, , height]) => [y, height]);
const texts = (ui) => ui.draws.filter(([name]) => name === 'fillText').map(([, text]) => text);
function attachedFiles(document, { heading = 530, image = 560 } = {}) {
  const previews = document.getElementById('imagePreviews');
  previews.innerHTML = `<div class="fw-bold mb-2 pdf-keep-with-next" data-top="${heading}" data-height="20">Attached files</div><img class="evidence-img" src="/a.png" alt="attachment" data-top="${image}" data-height="100">`;
}

test('a PDF export keeps the attachment heading with its thumbnail and numbers every page', async (t) => {
  const ui = await exportPage(t, {
    sample_pdf_header: 'Board pack',
    sample_pdf_footer: 'Confidential',
  });
  attachedFiles(ui.document);

  await ui.exporter.downloadPdf(event);

  // A4 portrait at 900px is 1273px per page, 1201px of it content. The first
  // break moves up to the heading so it stays with the thumbnail.
  assert.deepEqual(slices(ui), [
    [0, 1048],
    [1048, 1201],
    [2249, 751],
  ]);
  const pdf = await pdfText(ui.blobs.at(-1));
  assert.equal(pageCount(pdf), 3);
  assert.match(pdf, /^%PDF-1\.4/);
  const info = await pdfInfo(ui.blobs.at(-1));
  assert.equal(info.Pages, '3');
  assert.match(info['Page size'], /^595\.28 x 841\.89 pts/);
  const drawn = texts(ui);
  assert.deepEqual(
    drawn.filter((text) => text.startsWith('Page ')),
    ['Page 1 of 3', 'Page 2 of 3', 'Page 3 of 3'],
  );
  assert.equal(drawn.filter((text) => text === 'Confidential').length, 3);
  assert.match(drawn[0], /^Board pack \d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-\d{3}Z - e1$/);
  assert.deepEqual(
    { ...ui.renders[0].options },
    { scale: 2, backgroundColor: '#fff', useCORS: true },
  );
  // Buttons and other screen-only controls stay out of the capture.
  assert.equal(ui.renders[0].wrap.querySelector('.no-print'), null);
  assert.equal(ui.document.querySelector('.export-capture'), null);

  assert.deepEqual(ui.downloads, [[stem + '.pdf', 'blob:keen/1']]);
  assert.deepEqual(ui.notices, [
    ['Rendering evidence PDF…', 'info'],
    ['PDF download started', 'success'],
  ]);
  const button = ui.document.getElementById('btnPdf');
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Sample as PDF evidence');
  ui.runTimers(2800);
  ui.runTimers(15000);
  assert.equal(ui.document.getElementById('status').style.display, 'none');
  assert.deepEqual(ui.revoked, ['blob:keen/1']);
});

test('cards move to the next page and a thumbnail taller than a page is cut at its end', async (t) => {
  const ui = await exportPage(t);
  // The thumbnail starts the capture and runs past the first page end.
  ui.document.getElementById('imagePreviews').innerHTML =
    '<img class="evidence-img" src="/a.png" data-top="0" data-height="700">';
  const card = ui.document.getElementById('normalized').closest('.card');
  card.dataset.top = '1150';
  card.dataset.height = '100';
  ui.setCanvasHeight(2600);

  await ui.exporter.downloadPdf(event);

  // The thumbnail covers 0..1412 and the card 2280..2520 at capture scale.
  assert.deepEqual(slices(ui), [
    [0, 1201],
    [1201, 1079],
    [2280, 320],
  ]);
  // Without configured text the header names the export and the footer is blank.
  assert.match(texts(ui)[0], /^Evidence export \S+ - e1$/);
  assert.equal(texts(ui)[1], '');
});

test('a card starting just below a page break stays on its page', async (t) => {
  const ui = await exportPage(t);
  const card = ui.document.getElementById('normalized').closest('.card');
  // At capture scale the card covers 1206..2446, five pixels into page two.
  card.dataset.top = '613';
  card.dataset.height = '600';

  await ui.exporter.downloadPdf(event);

  assert.deepEqual(slices(ui), [
    [0, 1201],
    [1201, 1201],
    [2402, 598],
  ]);
});

test('file names carry the configured prefix and stay short and portable', async (t) => {
  const ui = await exportPage(t, { evidence_filename_prefix: ' ACME/Audit <draft>? ' });
  await ui.exporter.downloadPdf({ ...event, source: 'unlabelled' });
  assert.equal(
    ui.downloads[0][0],
    'Keen Event - ACME Audit draft e1 unlabelled 2026-10-01T10-00-00Z.pdf',
  );

  const long = await exportPage(t, { evidence_filename_prefix: 'x'.repeat(300) });
  await long.exporter.downloadPdf({ id: 'e2' });
  const [name] = long.downloads[0];
  assert.equal(name.length, 184);
  assert.ok(name.startsWith('Keen Event - xxx') && name.endsWith('x.pdf'));
});

test('exports wait for an event and a renderer', async (t) => {
  const ui = await exportPage(t);
  await ui.exporter.downloadPdf(null);
  await ui.exporter.downloadArtifactsZip(null);
  await ui.exporter.downloadArtifactsZip({ ...event, artifacts: [] });
  assert.deepEqual(ui.notices, [
    ['Event not loaded yet', 'warning'],
    ['Event not loaded yet', 'warning'],
    ['No artifacts to download', 'warning'],
  ]);

  delete ui.window.html2canvas;
  await ui.exporter.downloadPdf(event);
  assert.deepEqual(ui.notices.at(-1), ['Error: html2canvas is not available', 'danger']);
  assert.equal(ui.document.getElementById('btnPdf').disabled, false);
});

test('captures wait for thumbnails to load or fail before rendering', async (t) => {
  const ui = await exportPage(t);
  ui.document.getElementById('imagePreviews').innerHTML =
    '<img class="evidence-img" src="/slow.png" data-pending="1">';
  const observer = new ui.window.MutationObserver(() => {
    const image = ui.document.querySelector('.export-capture img');
    if (image && !image.dataset.signalled) {
      image.dataset.signalled = '1';
      setTimeout(() => image.dispatchEvent(new ui.window.Event('error')), 5);
    }
  });
  observer.observe(ui.document.body, { childList: true });

  await ui.exporter.downloadPdf(event);
  observer.disconnect();
  assert.equal(ui.renders.length, 1);
  assert.equal(ui.downloads.length, 1);
});

test('the layout dialog previews both orientations and remembers the choice', async (t) => {
  const stored = new Map();
  const storage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
  };
  let instance;
  const bootstrap = {
    Modal: {
      getOrCreateInstance(element, options) {
        instance ||= {
          options,
          shows: 0,
          show() {
            this.shows++;
            setTimeout(
              () =>
                element.dispatchEvent(
                  new element.ownerDocument.defaultView.Event('shown.bs.modal'),
                ),
              0,
            );
          },
          hide() {
            element.dispatchEvent(new element.ownerDocument.defaultView.Event('hidden.bs.modal'));
          },
        };
        return instance;
      },
    },
  };
  const ui = await exportPage(t, {}, { bootstrap, storage });
  const $ = (id) => ui.document.getElementById(id);
  const modal = $('pdfOptionsModal');
  const option = (orientation) =>
    modal.querySelector(`.pdf-preview-option[data-orientation="${orientation}"]`);
  const previewsShown = async () => {
    for (let i = 0; i < 100 && $('pdfPreviewStatus').style.display !== 'none'; i++)
      await new Promise((resolve) => setTimeout(resolve, 2));
  };

  // At preview scale the thumbnail covers 1100..1280 of the capture.
  ui.document.getElementById('imagePreviews').innerHTML =
    '<img class="evidence-img" src="/a.png" data-top="1300" data-height="200">';
  const first = ui.exporter.downloadPdf(event);
  await new Promise((resolve) => setTimeout(resolve, 5));
  await previewsShown();
  assert.equal($('pdfPreviewStatus').style.display, 'none');
  // Each preview shows its first page; the portrait page ends above the thumbnail.
  assert.deepEqual(
    slices(ui).sort((a, b) => a[1] - b[1]),
    [
      [0, 584],
      [0, 1100],
    ],
  );
  assert.deepEqual({ ...instance.options }, { backdrop: 'static', keyboard: true });
  assert.deepEqual(
    ui.renders.map((render) => render.options.scale),
    [0.85, 0.85],
  );
  assert.ok($('pdfPreviewPortraitImg').src.startsWith('blob:keen/'));
  assert.ok($('pdfPreviewLandscapeImg').src.startsWith('blob:keen/'));
  assert.ok(
    $('pdfPreviewLandscapeImg').closest('.pdf-preview-frame').classList.contains('has-img'),
  );
  assert.equal(option('portrait').classList.contains('selected'), true);

  option('landscape').click();
  assert.equal($('pdfOptLandscape').checked, true);
  assert.equal(option('landscape').classList.contains('selected'), true);
  assert.equal(option('portrait').classList.contains('selected'), false);
  option('portrait').dispatchEvent(
    new ui.window.KeyboardEvent('keydown', { key: 'Enter', cancelable: true }),
  );
  assert.equal($('pdfOptPortrait').checked, true);
  $('pdfOptLandscape').checked = true;
  $('pdfOptLandscape').dispatchEvent(new ui.window.Event('change'));
  assert.equal(option('landscape').classList.contains('selected'), true);
  $('pdfOptionsGenerate').click();
  await first;

  assert.match((await pdfInfo(ui.blobs.at(-1)))['Page size'], /^841\.89 x 595\.28 pts/);
  assert.equal(stored.get('keen_pdf_orientation'), 'landscape');
  // Each orientation's preview is cached for this event.
  const before = ui.renders.length;
  const second = ui.exporter.downloadPdf(event);
  await new Promise((resolve) => setTimeout(resolve, 5));
  await previewsShown();
  assert.equal($('pdfOptLandscape').checked, true, 'the last layout is preselected');
  assert.equal(ui.renders.length, before);
  $('pdfOptionsGenerate').click();
  await second;
  assert.equal(instance.shows, 2);

  // A later page instance starts from the stored layout.
  const later = await exportPage(t, {}, { storage });
  await later.exporter.downloadPdf(event);
  assert.match(await pdfText(later.blobs.at(-1)), /\/MediaBox \[0 0 841\.89 595\.28\]/);
});

test('closing the layout dialog during previews cancels the export', async (t) => {
  let release;
  const bootstrap = {
    Modal: {
      getOrCreateInstance: (element) => ({
        show: () =>
          setTimeout(
            () =>
              element.dispatchEvent(new element.ownerDocument.defaultView.Event('shown.bs.modal')),
            0,
          ),
        hide: () => {},
      }),
    },
  };
  const ui = await exportPage(t, {}, { bootstrap });
  const rendered = new Promise((resolve) => (release = resolve));
  ui.window.html2canvas = async () => {
    await rendered;
    return { width: 900, height: 100 };
  };
  const pending = ui.exporter.downloadPdf(event);
  await new Promise((resolve) => setTimeout(resolve, 10));
  ui.document
    .getElementById('pdfOptionsModal')
    .dispatchEvent(new ui.window.Event('hidden.bs.modal'));
  release();
  await pending;
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.deepEqual(ui.notices, [['PDF export cancelled', 'info']]);
  assert.equal(ui.document.getElementById('pdfPreviewPortraitImg').hasAttribute('src'), false);
  assert.equal(ui.blobs.length, 0, 'no preview or PDF is kept');
  assert.equal(ui.document.querySelector('.export-capture'), null);
});

test('preview failures leave the layout dialog usable', async (t) => {
  const bootstrap = {
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
  };
  const ui = await exportPage(t, {}, { bootstrap });
  let renders = 0;
  const render = ui.window.html2canvas;
  ui.window.html2canvas = async (...args) => {
    if (renders++ < 2) throw new Error('Preview renderer failed');
    return render(...args);
  };
  const pending = ui.exporter.downloadPdf(event);
  const status = ui.document.getElementById('pdfPreviewStatus');
  // Both previews are attempted before the dialog settles.
  for (let i = 0; i < 100 && (renders < 2 || status.style.display !== 'none'); i++)
    await new Promise((resolve) => setTimeout(resolve, 2));
  assert.equal(renders, 2);
  assert.equal(ui.document.getElementById('pdfPreviewPortraitImg').hasAttribute('src'), false);
  ui.document.getElementById('pdfOptionsGenerate').click();
  await pending;
  assert.equal(ui.downloads.length, 1);
  assert.deepEqual(ui.notices.at(-1), ['PDF download started', 'success']);
});

test('PDF capture masks addresses but leaves times, versions and invalid addresses', async (t) => {
  const ui = await exportPage(t, { event_data_masking: 'TRUE' });
  const card = ui.document.getElementById('evidenceCard');
  const values = {
    email: 'alice.ops+audit@example.co.uk',
    loopback: '::1',
    scoped: 'fe80::1%eth0',
    full: '2001:db8:0:0:0:0:2:1',
    mapped: '::ffff:192.0.2.1',
    ipv4: '198.51.100.7',
    time: '13:40:19',
    version: 'v1.2.3.4.5',
    octets: '999.1.1.1',
    pair: 'dead:beef',
    nine: '1:2:3:4:5:6:7:8:9',
    double: 'a::b::c',
    badTail: '::ffff:300.1.1.1',
    longGroup: '12345::1',
    emptyGroup: '1:2:3:4:5:6:7:',
    eightGroups: '1:2:3:4::5:6:7:8',
    dotted: '1.2.3.4.5',
    nat64: '64:ff9b:0:0:0:0:192.0.2.1',
    sevenGroups: '1:2:3:4:5:6:7',
  };
  card.textContent = Object.entries(values)
    .map(([name, value]) => `${name}=${value}`)
    .join(' ; ');
  ui.window.html2canvas = async (wrap) => {
    ui.renders.push(wrap.querySelector('#evidenceCard').textContent);
    throw new Error('Stop capture');
  };
  await ui.exporter.downloadPdf(event);

  const captured = Object.fromEntries(
    ui.renders[0]
      .trim()
      .split(' ; ')
      .map((pair) => pair.split('=')),
  );
  for (const name of ['loopback', 'scoped', 'full', 'mapped', 'nat64', 'ipv4'])
    assert.equal(captured[name], '[MASKED_IP]', name);
  assert.equal(captured.email, '[MASKED_EMAIL]');
  for (const name of [
    'time',
    'version',
    'octets',
    'pair',
    'nine',
    'double',
    'badTail',
    'longGroup',
    'emptyGroup',
    'eightGroups',
    'dotted',
    'sevenGroups',
  ])
    assert.equal(captured[name], values[name], name);
});

test('PDF capture masks addresses that end a sentence', async (t) => {
  const ui = await exportPage(t, { event_data_masking: 'samples' });
  ui.document.getElementById('evidenceCard').textContent =
    'Login from 192.0.2.1. Contact dana@example.test. Peer fe80::1. Mapped 0:0:0:0:0:ffff:198.51.100.7.';
  ui.window.html2canvas = async (wrap) => {
    ui.renders.push(wrap.querySelector('#evidenceCard').textContent);
    throw new Error('Stop capture');
  };

  await ui.exporter.downloadPdf(event);

  assert.equal(
    ui.renders[0].trim(),
    'Login from [MASKED_IP]. Contact [MASKED_EMAIL]. Peer [MASKED_IP]. Mapped [MASKED_IP].',
  );
});

test('a ZIP export bundles the PDF and every artifact under the event’s name', async (t) => {
  const requests = [];
  const ui = await exportPage(
    t,
    { event_data_masking: 'samples' },
    {
      fetch: async (url, options) => {
        requests.push([url, options.credentials]);
        const id = url.split('/')[4];
        const body = { a1: '{"ok":true}', a2: 'PNG', a3: 'plain' }[id];
        const headers = {
          a1: { 'Content-Disposition': 'attachment; filename="report.ndjson"' },
          a2: {},
          a3: { 'Content-Disposition': "attachment; filename*=UTF-8''notes" },
        }[id];
        return new Response(new TextEncoder().encode(body), { headers });
      },
    },
  );
  const button = ui.document.getElementById('btnZip');
  await ui.exporter.downloadArtifactsZip({
    ...event,
    artifacts: [
      { id: 'a1', content_type: 'application/json', captured_at: '2026-09-30T08:00:00Z' },
      { id: 'a2', content_type: 'image/png' },
      { id: 'a1', content_type: 'application/json' },
      { id: 'a3', content_type: 'text/plain' },
    ],
  });

  // Masked exports ask the server for sample-safe artifact content.
  assert.deepEqual(requests, [
    ['/api/v1/artifacts/a1/download?sample_export=1', 'include'],
    ['/api/v1/artifacts/a2/download?sample_export=1', 'include'],
    ['/api/v1/artifacts/a1/download?sample_export=1', 'include'],
    ['/api/v1/artifacts/a3/download?sample_export=1', 'include'],
  ]);
  assert.deepEqual(ui.downloads, [[stem + '.zip', 'blob:keen/1']]);
  const zip = Buffer.from(await ui.blobs.at(-1).arrayBuffer());
  const listing = spawnSync(
    'python3',
    [
      '-c',
      `import io,json,sys,zipfile
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:
 assert z.testzip() is None
 print(json.dumps([[i.filename, i.file_size, z.read(i.filename)[:4].decode('latin1'), list(i.date_time[:3])] for i in z.infolist()]))
`,
    ],
    { input: zip, timeout: 5000 },
  );
  assert.equal(listing.status, 0, listing.stderr.toString());
  const entries = JSON.parse(listing.stdout.toString());
  assert.deepEqual(
    entries.map(([name, , start]) => [name, start]),
    [
      [stem + '-evidence.pdf', '%PDF'],
      [stem + '-artifact-a1.ndjson', '{"ok'],
      [stem + '-artifact-a2.png', 'PNG'],
      [stem + '-artifact-a1 (2).ndjson', '{"ok'],
      [stem + '-artifact-a3.txt', 'plai'],
    ],
  );
  assert.deepEqual(entries[1][3], [2026, 9, 30]);
  assert.deepEqual(
    ui.notices.map(([message]) => message),
    [
      'Rendering evidence PDF…',
      'Collecting 4 artifact(s)…',
      'Downloading 1/4…',
      'Downloading 2/4…',
      'Downloading 3/4…',
      'Downloading 4/4…',
      'Building ZIP…',
      'ZIP download started',
    ],
  );
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Download all artifacts as evidence');
  ui.runTimers(10000);
  assert.deepEqual(ui.revoked, ['blob:keen/1']);
});

test('a failed artifact download stops the ZIP and releases its body', async (t) => {
  let cancelled = 0;
  const ui = await exportPage(
    t,
    {},
    {
      fetch: async () => ({
        ok: false,
        status: 403,
        headers: new Headers(),
        body: { cancel: async () => cancelled++ },
      }),
    },
  );
  await ui.exporter.downloadArtifactsZip({ ...event, artifacts: [{ id: 'a1' }] });

  assert.deepEqual(ui.notices.at(-1), [
    'Error: Failed to download artifact a1 (HTTP 403)',
    'danger',
  ]);
  assert.equal(cancelled, 1);
  assert.deepEqual(ui.downloads, []);
  assert.equal(ui.document.getElementById('btnZip').disabled, false);
});

test('the print window masks the page, adds printable chrome and prints once', async (t) => {
  const ui = await exportPage(t, {
    event_data_masking: 'samples',
    sample_pdf_header: 'Audit sample',
    sample_pdf_footer: 'Internal use',
  });
  const card = ui.document.getElementById('evidenceCard');
  card.insertAdjacentHTML(
    'beforeend',
    '<p>Contact ops@example.com</p><img class="evidence-img" src="/slow.png" loading="lazy" data-pending="1">',
  );
  const header = ui.document.getElementById('pdfHeader');
  const footer = ui.document.getElementById('pdfFooter');
  // Printable chrome stays off until printing starts.
  assert.equal(header.style.display, 'none');
  assert.equal(footer.dataset.enabled, undefined);

  const printing = ui.exporter.autoPrintEvidence(event);
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(ui.prints(), 0, 'printing waits for thumbnails');
  assert.equal(card.querySelector('img').loading, 'eager');
  ui.runTimers(20000);
  await printing;
  await new Promise((resolve) => setTimeout(resolve, 200));

  assert.equal(ui.prints(), 1);
  assert.match(header.textContent, /^Audit sample \d{4}-.+ - e1$/);
  assert.equal(header.dataset.enabled, '1');
  assert.equal(header.style.display, '');
  assert.equal(
    footer.querySelector('.pdf-footer-inner .pdf-footer-text').textContent,
    'Internal use',
  );
  assert.equal(footer.dataset.enabled, '1');
  assert.equal(ui.document.title, stem);
  assert.match(card.textContent, /Contact \[MASKED_EMAIL\]/);

  await ui.exporter.autoPrintEvidence(event);
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(ui.prints(), 1, 'the print dialog opens once');
  ui.window.dispatchEvent(new ui.window.Event('afterprint'));
  assert.equal(ui.closes(), 1);
});
