import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
const ui = await loadUi('pages/swot-export.js', { globals: { TextEncoder, Blob } });
test.after(() => ui.close());

test('Excel has four type sheets, literal cells, complete attributes and split long relations', () => {
  const item = {
    id: 's1',
    title: '=HYPERLINK("https://invalid.test")',
    kind: 'strength',
    description: '<xml>& context',
    significance: 5,
    links: [
      { target_type: 'risk', title: 'x'.repeat(29999) + '😀' + 'x'.repeat(1000), target_id: 'r1' },
    ],
    controls: [{ ref: 'A.1', title: 'Control' }],
  };
  const files = ui.api.workbookFiles([item], 'F1');
  const workbook = files.find((file) => file.name === 'xl/workbook.xml').text;
  assert.match(workbook, /Strengths/);
  assert.match(workbook, /Weaknesses/);
  assert.match(workbook, /Opportunities/);
  assert.match(workbook, /Threats/);
  const sheet = files.find((file) => file.name === 'xl/worksheets/sheet1.xml').text;
  assert.match(sheet, /=HYPERLINK/);
  assert.doesNotMatch(sheet, /<f[ >]/);
  assert.match(sheet, /&lt;xml&gt;&amp; context/);
  assert.match(sheet, /Risk links \(continued 2\)/);
  assert.match(sheet, /😀/);
  assert.match(sheet, /A.1 Control/);
  assert.match(sheet, /Parent id/);
  assert.doesNotMatch(sheet, /Changelog/);
  const doc = new ui.window.DOMParser().parseFromString(sheet, 'application/xml');
  assert.equal(doc.querySelector('parsererror'), null);
  for (const cell of doc.querySelectorAll('t')) assert.ok(cell.textContent.length <= 32767);
  assert.equal(
    ui.api.buildWorkbook([item], 'F1').type,
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  );
});

test('exports collect every filtered page and use protected detail responses for relationships', async () => {
  const calls = [];
  const apiGet = async (url) => {
    calls.push(url);
    const parsed = new URL(url, 'https://keen.example');
    if (parsed.pathname === '/api/v1/isms/swot') {
      assert.equal(parsed.searchParams.get('kind'), 'threat');
      const offset = Number(parsed.searchParams.get('offset'));
      return {
        total: 201,
        items: Array.from({ length: offset ? 1 : 200 }, (_, i) => ({ id: String(offset + i) })),
      };
    }
    return { id: parsed.pathname.split('/').at(-1), links: [] };
  };
  const items = await ui.api.collectExport(
    apiGet,
    '/api/v1/isms/swot',
    new URLSearchParams({ framework: 'F1', kind: 'threat' }),
  );
  assert.equal(items.length, 201);
  assert.equal(calls.length, 203);
});

test('export bounds and cancellation fail without producing a partial file', async () => {
  const filters = new URLSearchParams({ framework: 'F1' });
  await assert.rejects(
    ui.api.collectExport(async () => ({ total: 501, items: [] }), '/swot', filters),
    /500 items/,
  );
  await assert.rejects(
    ui.api.collectExport(
      () => {
        throw Error('Must not fetch');
      },
      '/swot',
      filters,
      () => true,
    ),
    /cancelled/,
  );
  await assert.rejects(
    ui.api.collectExport(
      async (url) =>
        url.startsWith('/swot?')
          ? { total: 1, items: [{ id: '1' }] }
          : Promise.reject(Error('403')),
      '/swot',
      filters,
    ),
    /403/,
  );
});

test('PDF paginates long text and preserves the final lines', async () => {
  const drawn = [];
  const ctx = {
    fillRect() {},
    fillText(text) {
      drawn.push(text);
    },
    measureText(text) {
      return { width: Array.from(text).length * 11 };
    },
  };
  const pdfUi = await loadUi('pages/swot-export.js', {
    globals: { TextEncoder, Blob },
    setup(window) {
      const create = window.document.createElement.bind(window.document);
      window.document.createElement = (name) =>
        name === 'canvas'
          ? {
              getContext: () => ctx,
              toBlob: (callback) => callback(new Blob([new Uint8Array([255, 216, 255, 217])])),
            }
          : create(name);
    },
  });
  try {
    const blob = await pdfUi.api.buildSwotPdf(
      [
        {
          id: '1',
          title: 'Long report',
          kind: 'threat',
          description: Array.from({ length: 110 }, (_, i) => `Line ${i} with words`).join('\n'),
        },
      ],
      'F1',
    );
    assert.equal(blob.type, 'application/pdf');
    assert.ok(drawn.some((line) => line.includes('Page 2')));
    assert.ok(drawn.includes('Line 109 with words'));
    assert.ok(drawn.every((line) => line.length <= 120));
    await assert.rejects(
      pdfUi.api.buildSwotPdf([{ kind: 'threat', title: 'Cancelled' }], 'F1', undefined, () => true),
      /cancelled/,
    );
  } finally {
    pdfUi.close();
  }
});
