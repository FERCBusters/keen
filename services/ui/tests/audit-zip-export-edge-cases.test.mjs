// Audit ZIP export: names, counts, refusals and cancellation beyond the
// common path covered in audit-page.test.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { settle } from './support/page-fixture.mjs';
import { apiAudit, auditPage, eventEvidence, finalReport } from './support/audit-pages.mjs';

function readZip(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const end = bytes.length - 22;
  assert.equal(view.getUint32(end, true), 0x06054b50);
  const count = view.getUint16(end + 10, true);
  let cd = view.getUint32(end + 16, true);
  const names = [];
  for (let i = 0; i < count; i++) {
    const nameLen = view.getUint16(cd + 28, true);
    names.push(new TextDecoder().decode(bytes.slice(cd + 46, cd + 46 + nameLen)));
    cd += 46 + nameLen + view.getUint16(cd + 30, true) + view.getUint16(cd + 32, true);
  }
  return names;
}

const artifact = (id) => ({
  id,
  kind: 'log_line',
  sha256: 'x',
  content_type: 'text/plain',
  size_bytes: 3,
  captured_at: '2026-01-02T00:00:00',
});
const eventDetail = (artifacts) => ({
  id: 'ev1',
  timestamp: '2026-01-02T00:00:00',
  source: 'auth',
  artifacts,
});
const download = (name, body = 'log') =>
  new Response(body, {
    headers: {
      'Content-Type': 'text/plain',
      'Content-Disposition': `attachment; filename="${name}"; filename*=UTF-8''${encodeURIComponent(name)}`,
    },
  });

// An audit page ready to export, recording page renders, downloads and fetches.
async function exportPage(t, { audit, events = {}, fetch, jpeg, setup, responses } = {}) {
  const run = { renders: [], downloads: [], fetched: [], blobs: [] };
  const ctx = new Proxy({}, { get: (_, k) => (k in {} ? undefined : () => {}), set: () => true });
  class CaptureUrl extends URL {
    static createObjectURL(blob) {
      run.blobs.push(blob);
      return 'blob:keen';
    }
    static revokeObjectURL() {}
  }
  const page = await auditPage(t, {
    audit,
    events,
    responses,
    vendor: {
      fmtTsFilename: (iso) => String(iso).slice(0, 19).replace('T', '_').replaceAll(':', '-'),
    },
    globals: {
      URL: CaptureUrl,
      fetch: async (url, init) => {
        run.fetched.push(url);
        return fetch(url, init);
      },
    },
    setup(window) {
      window.HTMLCanvasElement.prototype.getContext = () => ctx;
      window.HTMLCanvasElement.prototype.toBlob = (cb) =>
        cb(new Blob([jpeg || new Uint8Array([0xff, 0xd8, 0xff])]));
      window.HTMLAnchorElement.prototype.click = function () {
        if (this.download) run.downloads.push(this.download);
      };
      window.html2canvas = async (node) => {
        run.renders.push(node.textContent);
        return { width: 1000, height: 1000 };
      };
      setup?.(window);
    },
  });
  const zipNames = async () => readZip(new Uint8Array(await run.blobs[0].arrayBuffer()));
  return { ...page, run, zipNames };
}

test('an audit ZIP is named from its last update and keeps clashing names apart', async (t) => {
  const audit = apiAudit({
    final_report: finalReport,
    evidence: [eventEvidence('e1', 'ev1')],
  });
  const { ui, run, zipNames } = await exportPage(t, {
    audit,
    events: { ev1: eventDetail([artifact('art1'), artifact('art2'), artifact('art3')]) },
    fetch: async (url) =>
      url === '/api/v1/audits/a1/report'
        ? download('report.pdf', 'final')
        : download(url.includes('art3') ? '<?>' : 'log.txt'),
  });
  // A selected entry is ticked in the captured page.
  ui.document
    .querySelector('[data-select-evidence][value="e1"]')
    .dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  ui.el('btnAuditZip').click();
  await settle(() => run.blobs.length === 1);
  assert.deepEqual(run.downloads, ['Keen Audit - a1 - Annual audit - 2026-01-05_10-30-00.zip']);
  assert.deepEqual(await zipNames(), [
    'Keen Audit - a1 - Annual audit - 2026-01-05_10-30-00-audit-page.pdf',
    'final-report/report.pdf',
    'sampled-evidence/001 2026-01-02_00-00-00 auth ev1/log.txt',
    'sampled-evidence/001 2026-01-02_00-00-00 auth ev1/log (2).txt',
    // A name made only of characters a ZIP path cannot hold becomes a plain file name.
    'sampled-evidence/001 2026-01-02_00-00-00 auth ev1/file',
  ]);
  assert.match(run.renders[0], /☑/);
  assert.doesNotMatch(run.renders[0], /☐/);
});

test('one attachment is counted in the singular and events without attachments add nothing', async (t) => {
  const quiet = eventEvidence('e2', 'ev2');
  const { ui, run, zipNames } = await exportPage(t, {
    audit: apiAudit({ evidence: [eventEvidence('e1', 'ev1'), quiet] }),
    events: { ev1: eventDetail([artifact('art1')]), ev2: { ...eventDetail([]), id: 'ev2' } },
    fetch: async () => download('log.txt'),
  });
  ui.el('btnAuditZip').click();
  await settle(() => run.blobs.length === 1);
  assert.equal((await zipNames()).length, 2);
  assert.deepEqual(run.fetched, ['/api/v1/artifacts/art1/download']);
  assert.equal(
    ui.el('status').textContent,
    'Audit ZIP download started (1 sampled evidence attachment)',
  );
});

test('an audit with nothing attached exports the audit page alone', async (t) => {
  const link = {
    ...eventEvidence('e1', null),
    event_id: null,
    event: null,
    evidence_url: 'https://evidence.example/item',
  };
  const { ui, run, zipNames } = await exportPage(t, {
    audit: apiAudit({ evidence: [link] }),
    fetch: async () => assert.fail('Nothing is downloaded'),
  });
  ui.el('btnAuditZip').click();
  await settle(() => run.blobs.length === 1);
  assert.equal((await zipNames()).length, 1);
  assert.equal(ui.el('status').textContent, 'Audit ZIP download started');
  assert.equal(ui.el('status').dataset.alert, 'success');
});

test('a final report the server refuses stops the export with the reason', async (t) => {
  const cancelled = [];
  const { ui, run } = await exportPage(t, {
    audit: apiAudit({ final_report: finalReport }),
    fetch: async () =>
      new Response(
        new ReadableStream({
          pulls: 0,
          pull(controller) {
            if (this.pulls++ < 3) controller.enqueue(new Uint8Array([1]));
            else controller.close();
          },
          cancel() {
            cancelled.push(true);
          },
        }),
        { status: 404 },
      ),
  });
  ui.el('btnAuditZip').click();
  await settle(() => /Failed to download/.test(ui.el('status').textContent));
  assert.equal(
    ui.el('status').textContent,
    'Failed to download /api/v1/audits/a1/report (HTTP 404)',
  );
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.deepEqual(cancelled, [true]);
  assert.deepEqual(run.blobs, []);
  assert.equal(ui.el('btnAuditZip').disabled, false);
});

test('cancelling while evidence is collected stops before the page is rendered', async (t) => {
  let release;
  const { ui, run, state } = await exportPage(t, {
    audit: apiAudit({ final_report: finalReport, evidence: [eventEvidence('e1', 'ev1')] }),
    events: {
      ev1: (u) =>
        u.search.includes('framework=A')
          ? new Promise((resolve) => (release = () => resolve(eventDetail([artifact('art1')]))))
          : assert.fail('The event is read for the audit framework'),
    },
    fetch: async () => assert.fail('Nothing is downloaded'),
  });
  ui.el('btnAuditZip').click();
  await settle(() => release);
  assert.equal(ui.el('status').textContent, 'Collecting sampled evidence 1/1');
  ui.el('btnAuditZipCancel').click();
  release();
  await settle(() => ui.el('status').textContent === 'Audit ZIP export cancelled.');
  assert.deepEqual(run.renders, []);
  assert.deepEqual(run.blobs, []);
  assert.ok(state.gets.includes('/api/v1/events/ev1?framework=A'));
  assert.equal(ui.el('btnAuditZipCancel').hidden, true);
});

test('an audit page PDF over 100 MiB is refused before anything is downloaded', async (t) => {
  const { ui, run } = await exportPage(t, {
    audit: apiAudit({ final_report: finalReport }),
    jpeg: new Uint8Array(100 * 1024 * 1024 + 1),
    fetch: async () => assert.fail('Nothing is downloaded'),
  });
  ui.el('btnAuditZip').click();
  await settle(() => /100 MiB/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Audit page PDF exceeds the 100 MiB ZIP export limit.');
  assert.deepEqual(run.fetched, []);
  assert.deepEqual(run.blobs, []);
});

test('export pressed while the audit is still loading asks the user to wait', async (t) => {
  let win;
  let shown = null;
  const { run } = await exportPage(t, {
    setup(window) {
      win = window;
    },
    responses: {
      // The attendee list loads before the audit itself.
      '/api/v1/audits/users': () => {
        win.document.getElementById('btnAuditZip').click();
        const el = win.document.getElementById('status');
        shown = [el.textContent, el.dataset.alert];
        return { items: [] };
      },
    },
    fetch: async () => assert.fail('Nothing is downloaded'),
  });
  assert.deepEqual(shown, ['Audit not loaded yet', 'warning']);
  assert.deepEqual(run.renders, []);
});
