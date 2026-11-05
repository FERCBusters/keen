import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// The SWOT export's refusals and PDF page layout, driven through its module API.
const filters = () => new URLSearchParams({ framework: 'F1' });

async function exportModule(t, canvas) {
  const ui = await loadUi('pages/swot-export.js', {
    globals: { TextEncoder, Blob },
    setup(window) {
      if (!canvas) return;
      const create = window.document.createElement.bind(window.document);
      window.document.createElement = (name) => (name === 'canvas' ? canvas : create(name));
    },
  });
  t.after(() => ui.close());
  return ui.api;
}

// A canvas that records each line of text with its page and height on the page,
// at 11 units a character, and turns each page into a small JPEG.
function recordingCanvas({ blob = () => new Blob([new Uint8Array([255, 216, 255, 217])]) } = {}) {
  const drawn = [];
  let page = 1;
  const ctx = {
    fillRect() {},
    fillText(text, x, y) {
      drawn.push({ text, page, y });
    },
    measureText: (text) => ({ width: Array.from(text).length * 11 }),
  };
  return {
    drawn,
    getContext: () => ctx,
    toBlob(done) {
      page += 1;
      done(blob());
    },
  };
}
const lines = (n) => Array.from({ length: n }, (_, i) => `Line ${i}`).join('\n');

test('a control or clause without a title is exported as its reference', async (t) => {
  const api = await exportModule(t);
  const record = api.exportRecord(
    {
      id: 's1',
      title: 'Flood',
      kind: 'threat',
      controls: [
        { ref: 'A.5', title: null },
        { ref: 'A.8', title: 'Assets' },
      ],
      clauses: [{ ref: '4.1', title: '' }],
      links: [],
    },
    'F1',
  );
  assert.equal(record.Controls, 'A.5\nA.8 Assets');
  assert.equal(record.Clauses, '4.1');
});

test('an export stops when the item list changes while its pages are read', async (t) => {
  const api = await exportModule(t);
  // The second page comes back empty: items were deleted after the first was read.
  const shrinking = async (url) => {
    const offset = Number(new URL(url, 'https://keen.example').searchParams.get('offset'));
    return offset
      ? { items: [], total: 300 }
      : { items: Array.from({ length: 200 }, (_, i) => ({ id: 's' + i })), total: 300 };
  };
  await assert.rejects(
    api.collectExport(shrinking, '/api/v1/isms/swot', filters()),
    /Items changed during export\. Please retry\./,
  );
  // The last of three pages falls short of the count reported with it.
  const pages = [];
  const short = async (url) => {
    const offset = Number(new URL(url, 'https://keen.example').searchParams.get('offset'));
    pages.push(offset);
    const count = offset === 400 ? 50 : 200;
    return {
      items: Array.from({ length: count }, (_, i) => ({ id: `s${offset + i}` })),
      total: 500,
    };
  };
  await assert.rejects(
    api.collectExport(short, '/api/v1/isms/swot', filters()),
    /Items changed during export\. Please retry\./,
  );
  assert.deepEqual(pages, [0, 200, 400]);
});

test('an export larger than 10 MB of text is refused', async (t) => {
  const api = await exportModule(t);
  const big = 'x'.repeat(4 * 1024 * 1024);
  const progress = [];
  await assert.rejects(
    api.collectExport(
      async (url) =>
        url.startsWith('/api/v1/isms/swot?')
          ? { items: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], total: 3 }
          : { id: url.split('/').pop().split('?')[0], description: big, links: [] },
      '/api/v1/isms/swot',
      filters(),
      () => false,
      (done, count) => progress.push([done, count]),
    ),
    /Export exceeds 10 MB of text\. Narrow the filters and try again\./,
  );
  // The limit is checked as each item arrives, before any progress is reported.
  assert.deepEqual(progress, []);

  // Two such items fit.
  const items = await api.collectExport(
    async (url) =>
      url.startsWith('/api/v1/isms/swot?')
        ? { items: [{ id: 'a' }, { id: 'b' }], total: 2 }
        : { id: 'a', description: big, links: [] },
    '/api/v1/isms/swot',
    filters(),
  );
  assert.equal(items.length, 2);
});

test('a PDF export needs a canvas the browser lets it draw on', async (t) => {
  const api = await exportModule(t, { getContext: () => null });
  await assert.rejects(
    api.buildSwotPdf([{ id: 's1', title: 'Flood', kind: 'threat' }], 'F1'),
    /PDF rendering is unavailable in this browser\./,
  );
});

test('a PDF page the browser cannot encode stops the export', async (t) => {
  const api = await exportModule(t, recordingCanvas({ blob: () => null }));
  await assert.rejects(
    api.buildSwotPdf([{ id: 's1', title: 'Flood', kind: 'threat' }], 'F1'),
    /Could not render PDF page\./,
  );
});

test('cancelling while a PDF page is being finished stops the export', async (t) => {
  const canvas = recordingCanvas();
  const api = await exportModule(t, canvas);
  // With no items, the only checks are those made as each page is finished.
  await assert.rejects(
    api.buildSwotPdf([], 'F1', new Map(), () => true),
    /Export cancelled\./,
  );
  assert.ok(canvas.drawn.some((d) => d.text === 'No matching items.'));
  const finished = await api.buildSwotPdf([], 'F1', new Map(), () => false);
  assert.equal(finished.type, 'application/pdf');
});

test('a PDF longer than 150 pages is refused', async (t) => {
  const canvas = recordingCanvas();
  const api = await exportModule(t, canvas);
  // 54 lines fit on a page.
  await assert.rejects(
    api.buildSwotPdf(
      [{ id: 's1', title: 'Flood', kind: 'threat', description: lines(8200) }],
      'F1',
    ),
    /PDF exceeds 150 pages\. Narrow the filters and try again\./,
  );
  assert.equal(Math.max(...canvas.drawn.map((d) => d.page)), 151);
});

test('a quadrant heading or item that would start near the foot of a page starts the next page', async (t) => {
  const canvas = recordingCanvas();
  const api = await exportModule(t, canvas);
  // Each item draws its title, Id, Kind, Framework and one line per description line.
  await api.buildSwotPdf(
    [
      { id: 's1', title: 'Talent', kind: 'strength', description: lines(45) },
      { id: 's2', title: 'Legacy systems', kind: 'weakness', description: lines(44) },
      { id: 's3', title: 'Single supplier', kind: 'weakness', description: 'Short' },
    ],
    'F1',
  );
  const at = (text) => canvas.drawn.find((d) => d.text === text);
  // The Strengths section ends low on page 1, so Weaknesses opens page 2.
  assert.equal(at('Description: Line 0').page, 1);
  assert.ok(canvas.drawn.filter((d) => d.page === 1).at(-1).y > 1550);
  assert.deepEqual(at('Weaknesses'), { text: 'Weaknesses', page: 2, y: 130 });
  // The first weakness ends low on page 2, so the next one opens page 3.
  assert.equal(at('Legacy systems').page, 2);
  assert.deepEqual(at('Single supplier'), { text: 'Single supplier', page: 3, y: 142 });
});
