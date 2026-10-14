import { buildZipStore } from '/pages/evidence-export/archive.js';
import { buildPdfFromJpegPages } from '/pages/evidence-export/pdf.js';

export const exportKinds = ['strength', 'weakness', 'opportunity', 'threat'];
const names = ['Strengths', 'Weaknesses', 'Opportunities', 'Threats'];
const relatedTypes = ['objective', 'document', 'asset', 'risk', 'person', 'vendor'];
const heading = (value) => value.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase());
const xml = (value) =>
  String(value ?? '')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g, '')
    .replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[c],
    );

export function exportRecord(item, framework, owners = new Map()) {
  const record = {};
  for (const key of [
    'id',
    'title',
    'kind',
    'significance',
    'status',
    'description',
    'response',
    'parent_id',
    'owner_user_id',
    'start_date',
    'target_date',
    'version',
    'created_at',
    'updated_at',
  ])
    record[heading(key)] = item[key] ?? '';
  record.Owner = owners.get(item.owner_user_id) || '';
  record.Framework = framework;
  for (const key of ['controls', 'clauses'])
    record[heading(key)] = (item[key] || [])
      .map((entry) => `${entry.ref} ${entry.title || ''}`.trim())
      .join('\n');
  for (const type of relatedTypes)
    record[heading(type) + ' links'] = (item.links || [])
      .filter((entry) => entry.target_type === type)
      .map((entry) => `${entry.title} [${entry.target_id}]`)
      .join('\n');
  return record;
}

export async function collectExport(
  apiGet,
  base,
  filters,
  cancelled = () => false,
  progress = () => {},
) {
  const check = () => {
    if (cancelled()) throw new Error('Export cancelled.');
  };
  const ids = [];
  for (let offset = 0; ; offset += 200) {
    check();
    const query = new URLSearchParams(filters);
    query.set('limit', '200');
    query.set('offset', String(offset));
    const data = await apiGet(`${base}?${query}`);
    if (data.total > 500)
      throw new Error('Export supports up to 500 items. Narrow the filters and try again.');
    for (const item of data.items) if (!ids.includes(item.id)) ids.push(item.id);
    if (offset + data.items.length >= data.total) break;
    if (!data.items.length || offset >= 400)
      throw new Error('Items changed during export. Please retry.');
  }
  const items = [];
  let bytes = 0;
  const encoder = new TextEncoder();
  for (let i = 0; i < ids.length; i += 4) {
    check();
    const batch = await Promise.all(
      ids
        .slice(i, i + 4)
        .map((id) =>
          apiGet(
            `${base}/${encodeURIComponent(id)}?framework=${encodeURIComponent(filters.get('framework'))}`,
          ),
        ),
    );
    check();
    for (const item of batch) {
      bytes += encoder.encode(JSON.stringify(item)).length;
      if (bytes > 10 * 1024 * 1024)
        throw new Error('Export exceeds 10 MB of text. Narrow the filters and try again.');
      items.push(item);
    }
    progress(items.length, ids.length);
  }
  check();
  return items;
}

function column(index) {
  let result = '';
  for (let n = index + 1; n; n = Math.floor((n - 1) / 26))
    result = String.fromCharCode(65 + ((n - 1) % 26)) + result;
  return result;
}

function cellParts(value) {
  const parts = [];
  let remaining = String(value);
  while (remaining.length > 30000) {
    let end = 30000;
    const last = remaining.charCodeAt(end - 1);
    if (last >= 0xd800 && last <= 0xdbff) end--;
    parts.push(remaining.slice(0, end));
    remaining = remaining.slice(end);
  }
  parts.push(remaining);
  return parts;
}

export function workbookFiles(items, framework, owners) {
  // Inline strings keep titles such as =HYPERLINK(...) as text, never formulas.
  const records = items.map((item) => exportRecord(item, framework, owners));
  const template = exportRecord({}, framework, owners);
  const columns = Object.keys(template).flatMap((key) => {
    const count = Math.max(1, ...records.map((record) => cellParts(record[key]).length));
    return Array.from({ length: count }, (_, part) => ({
      key,
      part,
      title: part ? `${key} (continued ${part + 1})` : key,
    }));
  });
  const sheets = exportKinds.map((kind, index) => {
    const rows = [
      columns.map((col) => col.title),
      ...records
        .filter((record) => record.Kind === kind)
        .map((record) => columns.map((col) => cellParts(record[col.key])[col.part] || '')),
    ];
    const data = rows
      .map(
        (values, r) =>
          `<row r="${r + 1}">${values.map((value, c) => `<c r="${column(c)}${r + 1}" t="inlineStr" s="${r ? 0 : 1}"><is><t xml:space="preserve">${xml(value)}</t></is></c>`).join('')}</row>`,
      )
      .join('');
    return {
      name: `xl/worksheets/sheet${index + 1}.xml`,
      text: `<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="${columns.length}" width="32" customWidth="1"/></cols><sheetData>${data}</sheetData><autoFilter ref="A1:${column(columns.length - 1)}${rows.length}"/></worksheet>`,
    };
  });
  return [
    {
      name: '[Content_Types].xml',
      text: `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheets.map((s) => `<Override PartName="/${s.name}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`,
    },
    {
      name: '_rels/.rels',
      text: '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    },
    {
      name: 'xl/workbook.xml',
      text: `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${names.map((name, i) => `<sheet name="${name}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('')}</sheets></workbook>`,
    },
    {
      name: 'xl/_rels/workbook.xml.rels',
      text: `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((s, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join('')}<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`,
    },
    {
      name: 'xl/styles.xml',
      text: '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" applyAlignment="1"><alignment wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
    },
    ...sheets,
  ];
}
export function buildWorkbook(items, framework, owners) {
  const encoder = new TextEncoder();
  const zip = buildZipStore(
    workbookFiles(items, framework, owners).map((file) => ({
      name: file.name,
      data: encoder.encode(file.text),
    })),
  );
  return new Blob([zip], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

export async function buildSwotPdf(items, framework, owners, cancelled = () => false) {
  const pages = [];
  const canvas = document.createElement('canvas');
  canvas.width = 1240;
  canvas.height = 1754;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('PDF rendering is unavailable in this browser.');
  let y;
  function newPage() {
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, 1240, 1754);
    ctx.fillStyle = '#222';
    ctx.font = 'bold 28px sans-serif';
    ctx.fillText('SWOT analysis', 60, 55);
    ctx.font = '18px sans-serif';
    ctx.fillText(
      `Framework: ${framework} · ${items.length} items · Page ${pages.length + 1}`,
      60,
      85,
    );
    y = 130;
  }
  async function finish() {
    if (cancelled()) throw new Error('Export cancelled.');
    if (pages.length >= 150)
      throw new Error('PDF exceeds 150 pages. Narrow the filters and try again.');
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
    if (!blob) throw new Error('Could not render PDF page.');
    pages.push({ bytes: new Uint8Array(await blob.arrayBuffer()), w: 1240, h: 1754 });
    newPage();
  }
  async function line(text, bold = false) {
    if (y > 1670) await finish();
    ctx.font = `${bold ? 'bold ' : ''}21px sans-serif`;
    ctx.fillText(text, 60, y);
    y += 29;
  }
  async function paragraph(text, bold = false) {
    ctx.font = `${bold ? 'bold ' : ''}21px sans-serif`;
    for (const original of String(text).split('\n')) {
      let part = '';
      for (const character of original) {
        if (ctx.measureText(part + character).width > 1120) {
          const space = part.lastIndexOf(' ');
          if (space > 0) {
            await line(part.slice(0, space), bold);
            part = part.slice(space + 1);
          } else {
            await line(part, bold);
            part = '';
          }
        }
        part += character;
      }
      await line(part, bold);
    }
  }
  newPage();
  for (const [index, kind] of exportKinds.entries()) {
    if (y > 1550) await finish();
    await paragraph(names[index], true);
    const group = items.filter((item) => item.kind === kind);
    if (!group.length) await line('No matching items.');
    for (const item of group) {
      if (cancelled()) throw new Error('Export cancelled.');
      if (y > 1550) await finish();
      y += 12;
      await paragraph(item.title, true);
      for (const [key, value] of Object.entries(exportRecord(item, framework, owners))) {
        if (key !== 'Title' && value !== '') await paragraph(`${key}: ${value}`);
      }
    }
    y += 25;
  }
  await finish();
  return buildPdfFromJpegPages(pages, 'portrait');
}
