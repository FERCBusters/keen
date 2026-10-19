// Canvas pagination and PDF encoding; caller supplies content and labels.

// ------------------------------------------------------------------
// Client-side evidence PDF generation
// ------------------------------------------------------------------

const _A4_PORTRAIT = { w: 595.28, h: 841.89 };
const _A4_LANDSCAPE = { w: 841.89, h: 595.28 };

function _pageSize(orientation) {
  const o = String(orientation || 'portrait').toLowerCase();
  return o === 'landscape' ? _A4_LANDSCAPE : _A4_PORTRAIT;
}

async function _canvasToJpegBytes(canvas, quality = 0.92) {
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
  if (!blob) throw new Error('Failed to encode PDF page image');
  const ab = await blob.arrayBuffer();
  return new Uint8Array(ab);
}

export async function sliceCanvasToA4JpegPages(
  canvas,
  header,
  footerText,
  orientation,
  avoidRanges = [],
) {
  const { w: pageW, h: pageH } = _pageSize(orientation);

  const pages = [];
  const w = canvas.width;
  const ratio = pageW / w; // points per pixel
  const pageHeightPx = Math.max(1, Math.round(pageH / ratio));

  const headerPt = header ? 24 : 0;
  const footerPt = 24; // always reserve space for page numbers

  const headerPx = Math.round(headerPt / ratio);
  const footerPx = Math.round(footerPt / ratio);
  const contentHeightPx = Math.max(1, pageHeightPx - headerPx - footerPx);
  const fontPx = Math.max(10, Math.round(10 / ratio));
  // Precompute slice offsets so we know total page count.
  const ranges = Array.isArray(avoidRanges) ? avoidRanges.slice() : [];
  ranges.sort((a, b) => (a?.top ?? 0) - (b?.top ?? 0));

  const cuts = [];
  let y = 0;

  const findRange = (pos) => {
    for (const r of ranges) {
      if (!r) continue;
      const top = Number(r.top);
      const bottom = Number(r.bottom);
      if (!Number.isFinite(top) || !Number.isFinite(bottom)) continue;
      if (top < pos && pos < bottom) return { top, bottom };
    }
    return null;
  };

  while (y < canvas.height) {
    let desired = y + contentHeightPx;

    if (desired >= canvas.height) {
      cuts.push({ y, h: canvas.height - y });
      break;
    }

    // Break before a thumbnail or card that the page end would cut, even when
    // that makes a short page. One starting at the top of the page is taller
    // than the page, so the break stays at the page end.
    const r = findRange(desired);
    if (r && r.top > y + 10) desired = r.top;

    let sliceH = Math.max(1, Math.min(contentHeightPx, desired - y));
    // Guard against no progress.
    if (sliceH <= 1) sliceH = Math.min(contentHeightPx, canvas.height - y);

    cuts.push({ y, h: sliceH });
    y += sliceH;
  }

  const totalPages = cuts.length || 1;

  for (let i = 0; i < cuts.length; i++) {
    const { y: y0, h: sliceH } = cuts[i];

    const page = document.createElement('canvas');
    page.width = w;
    page.height = pageHeightPx;
    const ctx = page.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, w, pageHeightPx);

    if (headerPx > 0) {
      ctx.strokeStyle = '#ddd';
      ctx.beginPath();
      ctx.moveTo(0, headerPx - 0.5);
      ctx.lineTo(w, headerPx - 0.5);
      ctx.stroke();

      ctx.fillStyle = '#666';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = `${fontPx}px system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;
      ctx.fillText(String(header), w / 2, headerPx / 2);
    }

    ctx.drawImage(canvas, 0, y0, w, sliceH, 0, headerPx, w, sliceH);

    // Footer: left text + right page numbers
    const top = pageHeightPx - footerPx;
    ctx.strokeStyle = '#ddd';
    ctx.beginPath();
    ctx.moveTo(0, top + 0.5);
    ctx.lineTo(w, top + 0.5);
    ctx.stroke();

    ctx.fillStyle = '#666';
    ctx.textBaseline = 'middle';
    ctx.font = `${fontPx}px system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;

    ctx.textAlign = 'left';
    ctx.fillText(String(footerText || ''), 8, top + footerPx / 2);

    ctx.textAlign = 'right';
    ctx.fillText(`Page ${i + 1} of ${totalPages}`, w - 8, top + footerPx / 2);

    const bytes = await _canvasToJpegBytes(page, 0.92);
    pages.push({ bytes, w, h: pageHeightPx });
  }

  return pages;
}

export function buildPdfFromJpegPages(pages, orientation) {
  if (!pages.length) throw new Error('No pages to export');
  const { w: pageW, h: pageH } = _pageSize(orientation);

  const enc = new TextEncoder();
  const parts = [];
  let offset = 0;
  const offsets = [0];

  const push = (u8) => {
    parts.push(u8);
    offset += u8.length;
  };
  const pushStr = (s) => push(enc.encode(s));

  // PDF header + binary comment
  pushStr('%PDF-1.4\n');
  push(new Uint8Array([0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a]));

  let nextObj = 1;
  const beginObj = (id) => {
    offsets[id] = offset;
    pushStr(`${id} 0 obj\n`);
  };
  const endObj = () => pushStr('\nendobj\n');

  const catalogObj = nextObj++;
  const pagesObj = nextObj++;

  const pageObjs = [];
  const imgObjs = [];
  const contentObjs = [];

  for (let i = 0; i < pages.length; i++) {
    pageObjs.push(nextObj++);
    imgObjs.push(nextObj++);
    contentObjs.push(nextObj++);
  }

  // Catalog
  beginObj(catalogObj);
  pushStr(`<< /Type /Catalog /Pages ${pagesObj} 0 R >>`);
  endObj();

  // Pages tree
  beginObj(pagesObj);
  pushStr(`<< /Type /Pages /Count ${pages.length} /Kids [`);
  for (const po of pageObjs) pushStr(` ${po} 0 R`);
  pushStr(` ] >>`);
  endObj();

  for (let i = 0; i < pages.length; i++) {
    const p = pages[i];
    const pageObj = pageObjs[i];
    const imgObj = imgObjs[i];
    const contentObj = contentObjs[i];
    const imgName = `Im${i + 1}`;

    // Image object
    beginObj(imgObj);
    pushStr(`<< /Type /XObject /Subtype /Image /Width ${p.w} /Height ${p.h} `);
    pushStr(
      `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.bytes.length} >>\n`,
    );
    pushStr('stream\n');
    push(p.bytes);
    pushStr('\nendstream');
    endObj();

    // Content stream draws the image across the full page.
    const content = `q ${pageW} 0 0 ${pageH} 0 0 cm /${imgName} Do Q`;
    const contentBytes = enc.encode(content);
    beginObj(contentObj);
    pushStr(`<< /Length ${contentBytes.length} >>\nstream\n`);
    push(contentBytes);
    pushStr('\nendstream');
    endObj();

    // Page object
    beginObj(pageObj);
    pushStr(`<< /Type /Page /Parent ${pagesObj} 0 R `);
    pushStr(`/Resources << /XObject << /${imgName} ${imgObj} 0 R >> >> `);
    pushStr(`/MediaBox [0 0 ${pageW} ${pageH}] /Contents ${contentObj} 0 R >>`);
    endObj();
  }

  const xrefOffset = offset;
  pushStr('xref\n');
  pushStr(`0 ${nextObj}\n`);
  pushStr('0000000000 65535 f \n');
  for (let i = 1; i < nextObj; i++) {
    const off = offsets[i] || 0;
    pushStr(`${String(off).padStart(10, '0')} 00000 n \n`);
  }
  pushStr('trailer\n');
  pushStr(`<< /Size ${nextObj} /Root ${catalogObj} 0 R >>\n`);
  pushStr('startxref\n');
  pushStr(`${xrefOffset}\n%%EOF`);

  return new Blob(parts, { type: 'application/pdf' });
}

async function _canvasToJpegBlob(canvas, quality = 0.85) {
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
  if (!blob) throw new Error('Failed to encode preview');
  return blob;
}

export async function sliceCanvasToFirstA4PreviewBlob(
  canvas,
  header,
  footerText,
  orientation,
  avoidRanges = [],
) {
  const { w: pageW, h: pageH } = _pageSize(orientation);

  const w = canvas.width;
  const ratio = pageW / w; // points per pixel
  const pageHeightPx = Math.max(1, Math.round(pageH / ratio));

  const headerPt = header ? 24 : 0;
  const footerPt = 24;

  const headerPx = Math.round(headerPt / ratio);
  const footerPx = Math.round(footerPt / ratio);
  const contentHeightPx = Math.max(1, pageHeightPx - headerPx - footerPx);
  const fontPx = Math.max(10, Math.round(10 / ratio));

  const ranges = Array.isArray(avoidRanges) ? avoidRanges.slice() : [];
  const findRange = (pos) => {
    for (const r of ranges) {
      if (!r) continue;
      const top = Number(r.top);
      const bottom = Number(r.bottom);
      if (!Number.isFinite(top) || !Number.isFinite(bottom)) continue;
      if (top < pos && pos < bottom) return { top, bottom };
    }
    return null;
  };

  let desired = contentHeightPx;
  if (desired < canvas.height) {
    const r = findRange(desired);
    if (r && r.top > 10) desired = r.top;
  }

  const sliceH = Math.max(1, Math.min(canvas.height, Math.min(contentHeightPx, desired)));

  const page = document.createElement('canvas');
  page.width = w;
  page.height = pageHeightPx;
  const ctx = page.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, w, pageHeightPx);

  if (headerPx > 0) {
    ctx.strokeStyle = '#ddd';
    ctx.beginPath();
    ctx.moveTo(0, headerPx - 0.5);
    ctx.lineTo(w, headerPx - 0.5);
    ctx.stroke();

    ctx.fillStyle = '#666';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = `${fontPx}px system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;
    ctx.fillText(String(header), w / 2, headerPx / 2);
  }

  ctx.drawImage(canvas, 0, 0, w, sliceH, 0, headerPx, w, sliceH);

  // Footer (no total page count in preview)
  const top = pageHeightPx - footerPx;
  ctx.strokeStyle = '#ddd';
  ctx.beginPath();
  ctx.moveTo(0, top + 0.5);
  ctx.lineTo(w, top + 0.5);
  ctx.stroke();

  ctx.fillStyle = '#666';
  ctx.textBaseline = 'middle';
  ctx.font = `${fontPx}px system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;

  ctx.textAlign = 'left';
  ctx.fillText(String(footerText || ''), 8, top + footerPx / 2);

  ctx.textAlign = 'right';
  ctx.fillText('Page 1', w - 8, top + footerPx / 2);

  return await _canvasToJpegBlob(page, 0.82);
}
