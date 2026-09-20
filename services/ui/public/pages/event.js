import {initNavbar, apiGet, apiPatch, apiPost, apiDelete, apiPostForm, esc, fmtTs, fmtTsFilename, toast, qs, getSourceMeta, sourceBadgeHtml, sourceLabel, safeExternalHref, getCurrentFramework, withFramework, isAuditActive} from '/app.js';

const PRINT_MODE = (() => {
  const v = String(qs('print') || '').toLowerCase();
  return v === '1' || v === 'true';
})();

let me;
if (PRINT_MODE) {
  document.body.classList.add('print-view');
  // Load /me but avoid rendering the fixed navbar in the print-only window.
  me = await apiGet('/api/v1/me');
  window.keenMe = me;
  window.keenDefaultTimezone = me?.default_timezone || 'Etc/UTC';
  window.keenPreferences = me?.preferences || {};
  window.keenAvailableThemes = me?.available_themes || null;
} else {
  me = await initNavbar();
}


const canDeleteQuestions = !!(me?.is_admin || me?.can_delete_questions);

const _eventDataMaskingMode = String(me?.event_data_masking || 'false').trim().toLowerCase();
function _maskSampleExportsEnabled() {
  return _eventDataMaskingMode === 'samples' || _eventDataMaskingMode === 'true';
}
function _isValidIpv4Literal(value) {
  return /^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/.test(String(value || ''));
}
function _isMaskableIpv6Candidate(raw) {
  const candidate = String(raw || '').split('%', 1)[0];
  if (!candidate || !candidate.includes(':')) return false;
  if (/[^0-9A-Fa-f:.]/.test(candidate)) return false;
  const compressionMatches = candidate.match(/::/g) || [];
  if (compressionMatches.length > 1) return false;
  const hasCompression = compressionMatches.length === 1;
  const colonCount = (candidate.match(/:/g) || []).length;
  // Avoid treating ordinary times like 13:40:19 as IPv6 addresses.
  if (!hasCompression && colonCount !== 7) return false;
  if (hasCompression && colonCount < 2) return false;

  const parts = candidate.split(':');
  const last = parts[parts.length - 1] || '';
  const hasIpv4Tail = last.includes('.');
  if (parts.some((part, idx) => part.includes('.') && idx !== parts.length - 1)) return false;
  if (hasIpv4Tail && !_isValidIpv4Literal(last)) return false;

  const hexParts = hasIpv4Tail ? parts.slice(0, -1) : parts;
  if (!hasCompression && hexParts.some((part) => part === '')) return false;
  for (const part of hexParts) {
    if (!part) continue;
    if (!/^[0-9A-Fa-f]{1,4}$/.test(part)) return false;
  }

  const groupCount = hexParts.filter(Boolean).length + (hasIpv4Tail ? 2 : 0);
  return hasCompression ? groupCount < 8 : groupCount === 8;
}
function maskEventDataString(value) {
  if (value == null) return value;
  let txt = String(value);
  txt = txt.replace(/(^|[^A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,128})@([A-Za-z0-9.-]+\.[A-Za-z]{2,63})(?![A-Za-z0-9._%+-])/g, '$1[MASKED_EMAIL]');
  txt = txt.replace(/(^|[^\w:.])([A-Fa-f0-9:.]+(?::[A-Fa-f0-9:.]*)+(?:%[A-Za-z0-9_.-]+)?)(?![\w:])/g, (match, prefix, raw) => {
    if (!_isMaskableIpv6Candidate(raw)) return match;
    return `${prefix}[MASKED_IP]`;
  });
  txt = txt.replace(/(^|[^\w.])((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})(?![\w.])/g, '$1[MASKED_IP]');
  return txt;
}
function _maskExportDom(root) {
  if (!root || !_maskSampleExportsEnabled()) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node?.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  for (const node of textNodes) node.nodeValue = maskEventDataString(node.nodeValue);
  root.querySelectorAll('input, textarea').forEach((el) => {
    if ('value' in el) el.value = maskEventDataString(el.value);
  });
  root.querySelectorAll('[title], [alt], [aria-label]').forEach((el) => {
    for (const attr of ['title', 'alt', 'aria-label']) {
      if (el.hasAttribute(attr)) el.setAttribute(attr, maskEventDataString(el.getAttribute(attr) || ''));
    }
  });
}
function _sampleExportUrl(url) {
  if (!_maskSampleExportsEnabled()) return url;
  const sep = String(url).includes('?') ? '&' : '?';
  return `${url}${sep}sample_export=1`;
}

// Printable header/footer (shown only in @media print when enabled).
const footerEl = document.getElementById('pdfFooter');
const headerEl = document.getElementById('pdfHeader');

function _disablePrintableChrome() {
  if (footerEl) {
    footerEl.innerHTML = '';
    try {
      delete footerEl.dataset.enabled;
    } catch {}
    footerEl.style.display = 'none';
  }
  if (headerEl) {
    headerEl.textContent = '';
    try {
      delete headerEl.dataset.enabled;
    } catch {}
    headerEl.style.display = 'none';
  }
}

function _enablePrintableChrome(headerText, footerText) {
  if (headerEl) {
    headerEl.textContent = headerText || '';
    headerEl.dataset.enabled = '1';
    headerEl.style.display = '';
  }

  if (footerEl) {
    footerEl.innerHTML = '';
    const inner = document.createElement('div');
    inner.className = 'pdf-footer-inner';

    const left = document.createElement('span');
    left.className = 'pdf-footer-text';
    left.textContent = footerText || '';

    inner.appendChild(left);

    footerEl.appendChild(inner);

    footerEl.dataset.enabled = '1';
    footerEl.style.display = '';
  }
}

// Keep these off unless we're about to print.
_disablePrintableChrome();

const id = qs('id');
const status = document.getElementById('status');
let pageFramework = qs('framework', getCurrentFramework());

let sourceMeta = {};


// ------------------------------------------------------------------
// Evidence file naming (PDF/ZIP)
// ------------------------------------------------------------------
const _evidenceNamePrefix = (me?.evidence_filename_prefix || '').trim();

function _sanitizeFilename(s) {
  // Make a filename component safe across Windows/macOS/Linux.
  const str = String(s || '');
  return str
      .replace(/[\\/]+/g, ' ')
      .replace(/[<>"\?\*\|]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
}

function evidenceFileStem(e) {
  if (!e) return 'Keen Event';
  const src = e.source ? (sourceLabel(e.source, sourceMeta) || e.source) : 'unknown';
  const ts = e.timestamp ? fmtTsFilename(e.timestamp) : '';
  const parts = [];
  if (_evidenceNamePrefix) parts.push(_evidenceNamePrefix);
  if (e.id) parts.push(String(e.id));
  if (src) parts.push(String(src));
  if (ts) parts.push(String(ts));
  const tail = parts.filter(Boolean).join(' ');
  const stem = tail ? ('Keen Event - ' + tail) : 'Keen Event';

  let out = _sanitizeFilename(stem);
  // Avoid extremely long default names in some browsers/filesystems.
  if (out.length > 180) out = out.slice(0, 180).trim();
  return out || 'Keen Event';
}

function _sanitizePlainText(value, maxChars) {
  let txt = String(value || '');
  txt = txt.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  txt = txt.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '');
  txt = txt.trim();
  const max = Number(maxChars || 0);
  if (max > 0 && txt.length > max) txt = txt.slice(0, max);
  return txt;
}

// ------------------------------------------------------------------
// Client-side evidence PDF generation
// ------------------------------------------------------------------

const _A4_PORTRAIT = {w: 595.28, h: 841.89};
const _A4_LANDSCAPE = {w: 841.89, h: 595.28};

function _pageSize(orientation) {
  const o = String(orientation || 'portrait').toLowerCase();
  return (o === 'landscape') ? _A4_LANDSCAPE : _A4_PORTRAIT;
}

function _evidenceHeaderText(eventId, exportedIso) {
  const base = (me?.sample_pdf_header || '').trim() || 'Evidence export';
  const stamp = fmtTsFilename(exportedIso || new Date().toISOString());
  return `${base} ${stamp} - ${eventId}`;
}

function _triggerDownload(blob, filename) {
  const obj = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = obj;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => {
    try {
      URL.revokeObjectURL(obj);
    } catch {}
  }, 15_000);
}

async function _waitForImages(root, timeoutMs = 15_000) {
  if (!root) return;
  const imgs = Array.from(root.querySelectorAll('img'));
  const pending = imgs
      .filter((img) => img && img.src)
      .map((img) => {
        if (img.complete && img.naturalWidth > 0) return Promise.resolve();
        return new Promise((resolve) => {
          const done = () => {
            img.removeEventListener('load', done);
            img.removeEventListener('error', done);
            resolve();
          };
          img.addEventListener('load', done);
          img.addEventListener('error', done);
        });
      });

  if (!pending.length) return;
  await Promise.race([
    Promise.all(pending),
    new Promise((r) => setTimeout(r, timeoutMs)),
  ]);
}

function _makeExportContainer(nodes, widthPx) {
  const wrap = document.createElement('div');
  wrap.className = 'container container-tight py-4 export-capture';
  wrap.style.position = 'fixed';
  wrap.style.left = '-100000px';
  wrap.style.top = '0';
  wrap.style.width = `${Math.round(widthPx || 900)}px`;
  wrap.style.background = '#fff';

  for (const n of nodes) {
    if (!n) continue;
    const clone = n.cloneNode(true);
    // Remove UI-only elements (buttons, nav links, etc.)
    clone.querySelectorAll('.no-print').forEach((el) => el.remove());
    wrap.appendChild(clone);
  }

  _maskExportDom(wrap);
  document.body.appendChild(wrap);
  return wrap;
}

async function _canvasToJpegBytes(canvas, quality = 0.92) {
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
  if (!blob) throw new Error('Failed to encode PDF page image');
  const ab = await blob.arrayBuffer();
  return new Uint8Array(ab);
}

async function _sliceCanvasToA4JpegPages(canvas, header, footerText, orientation, avoidRanges = []) {
  const {w: pageW, h: pageH} = _pageSize(orientation);

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
  // If a page cut would land through an evidence thumbnail, nudge the cut
  // to start before/after the image so thumbnails aren't split across pages.
  const ranges = Array.isArray(avoidRanges) ? avoidRanges.slice() : [];
  ranges.sort((a, b) => (a?.top ?? 0) - (b?.top ?? 0));

  const cuts = [];
  let y = 0;
  const minSlice = Math.max(80, Math.round(0.08 * contentHeightPx));

  const findRange = (pos) => {
    for (const r of ranges) {
      if (!r) continue;
      const top = Number(r.top);
      const bottom = Number(r.bottom);
      if (!Number.isFinite(top) || !Number.isFinite(bottom)) continue;
      if (top < pos && pos < bottom) return {top, bottom};
    }
    return null;
  };

  while (y < canvas.height) {
    let desired = y + contentHeightPx;

    if (desired >= canvas.height) {
      cuts.push({y, h: canvas.height - y});
      break;
    }

    const r = findRange(desired);
    if (r) {
      const before = r.top;
      const after = r.bottom;

      // Prefer breaking BEFORE the thumbnail (even if it makes a short page),
      // because splitting thumbnails looks messy. Only fall back to AFTER if the
      // thumbnail starts right at the top of the page.
      if (before > y + 10) {
        desired = before;
      } else if (after - y <= contentHeightPx) {
        desired = after;
      }
    }

    let sliceH = Math.max(1, Math.min(contentHeightPx, desired - y));
    // Guard against no progress.
    if (sliceH <= 1) sliceH = Math.min(contentHeightPx, canvas.height - y);

    cuts.push({y, h: sliceH});
    y += sliceH;
  }

  const totalPages = cuts.length || 1;

  for (let i = 0; i < cuts.length; i++) {
    const {y: y0, h: sliceH} = cuts[i];

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
    pages.push({bytes, w, h: pageHeightPx});
  }

  return pages;
}


function _buildPdfFromJpegPages(pages, orientation) {
  if (!pages.length) throw new Error('No pages to export');
  const {w: pageW, h: pageH} = _pageSize(orientation);

  const enc = new TextEncoder();
  const parts = [];
  let offset = 0;
  const offsets = [0];

  const push = (u8) => {
    parts.push(u8); offset += u8.length;
  };
  const pushStr = (s) => push(enc.encode(s));

  // PDF header + binary comment
  pushStr('%PDF-1.4\n');
  push(new Uint8Array([0x25, 0xE2, 0xE3, 0xCF, 0xD3, 0x0A]));

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
    pushStr(`/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.bytes.length} >>\n`);
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

  return new Blob(parts, {type: 'application/pdf'});
}

async function buildEvidencePdfBlob(e, orientation = 'portrait') {
  const main = document.querySelector('main');
  if (!main) throw new Error('Page layout missing');
  const w = main.getBoundingClientRect().width || 900;

  const exportedIso = new Date().toISOString();
  const header = _evidenceHeaderText(e?.id || '', exportedIso);
  const footer = (me?.sample_pdf_footer || '').trim();

  const topRow = main.querySelector('.d-flex.flex-wrap.gap-2.align-items-center.justify-content-between.mb-3');
  const evidenceCard = document.getElementById('evidenceCard');
  const normalizedCard = document.getElementById('normalized')?.closest('.card');
  const rawCard = document.getElementById('raw')?.closest('.card');

  // Capture everything as one continuous flow to avoid forcing a page break after Artifacts.
  const nodes = [topRow, evidenceCard, normalizedCard, rawCard].filter(Boolean);
  const segments = nodes.length ? [nodes] : [];

  const allPages = [];
  const h2c = window.html2canvas || globalThis.html2canvas;
  if (!h2c) throw new Error('html2canvas is not available');

  for (const seg of segments) {
    const wrap = _makeExportContainer(seg, w);
    try {
      await _waitForImages(wrap);

      // Avoid cutting evidence thumbnails across pages by nudging page breaks
      // to occur before/after thumbnails when slicing the rendered canvas.
      const scale = 2;
      const wr = wrap.getBoundingClientRect();
      const avoidRanges = [];

      const addRangeForEl = (el, pad = 8) => {
        if (!el) return;
        try {
          const r = el.getBoundingClientRect();
          if (!r || r.height < 8) return;
          const top = Math.max(0, Math.round((r.top - wr.top - pad) * scale));
          const bottom = Math.max(0, Math.round((r.bottom - wr.top + pad) * scale));
          if (bottom > top + 20) avoidRanges.push({top, bottom});
        } catch {}
      };

      // Keep certain blocks together when possible (prevents ugly mid-card splits).
      addRangeForEl(wrap.querySelector('#normalized')?.closest('.card'), 10);
      addRangeForEl(wrap.querySelector('#raw')?.closest('.card'), 10);

      // Bonus: keep the "Attached files" heading with the first thumbnail row.
      const previewsWrap = wrap.querySelector('#imagePreviews');
      const attachedHeading = previewsWrap?.querySelector('.pdf-keep-with-next');
      const firstPreviewImg = previewsWrap?.querySelector('img.evidence-img');

      for (const img of Array.from(wrap.querySelectorAll('img.evidence-img'))) {
        try {
          const r = img.getBoundingClientRect();
          if (!r || r.height < 10) continue;
          const pad = 6;

          let top = Math.max(0, Math.round((r.top - wr.top - pad) * scale));
          const bottom = Math.max(0, Math.round((r.bottom - wr.top + pad) * scale));

          // If we're about to break right at the start of the first thumbnail,
          // push the break up to the heading so the heading doesn't get stranded
          // at the bottom of the previous page.
          if (attachedHeading && firstPreviewImg === img) {
            try {
              const hr = attachedHeading.getBoundingClientRect();
              const hTop = Math.max(0, Math.round((hr.top - wr.top - pad) * scale));
              if (Number.isFinite(hTop)) top = Math.min(top, hTop);
            } catch {}
          }

          if (bottom > top + 20) avoidRanges.push({top, bottom});
        } catch {}
      }

      const canvas = await h2c(wrap, {
        scale: 2,
        backgroundColor: '#fff',
        useCORS: true,
      });
      const pages = await _sliceCanvasToA4JpegPages(canvas, header, footer, orientation, avoidRanges);
      allPages.push(...pages);
    } finally {
      try {
        wrap.remove();
      } catch {}
    }
  }

  return _buildPdfFromJpegPages(allPages, orientation);
}

// ------------------------------------------------------------------
// Evidence PDF options modal
// ------------------------------------------------------------------

let _lastPdfOrientation = (() => {
  try {
    return localStorage.getItem('keen_pdf_orientation') || 'portrait';
  } catch {
    return 'portrait';
  }
})();

const _pdfPreviewCache = new Map();

function _previewKey(e, orientation) {
  const id = e?.id || '';
  const ts = e?.timestamp || '';
  const n = Array.isArray(e?.artifacts) ? e.artifacts.length : 0;
  return `${id}|${ts}|${n}|${String(orientation || 'portrait')}`;
}

function _revokeObjectUrl(url) {
  if (!url) return;
  try {
    URL.revokeObjectURL(url);
  } catch {}
}

function _setPreviewSelected(modalEl, orientation) {
  const o = String(orientation || 'portrait').toLowerCase();
  modalEl.querySelectorAll('.pdf-preview-option').forEach((opt) => {
    opt.classList.toggle('selected', String(opt.dataset.orientation || '').toLowerCase() === o);
  });
}

async function _canvasToJpegBlob(canvas, quality = 0.85) {
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
  if (!blob) throw new Error('Failed to encode preview');
  return blob;
}

function _computeAvoidRangesForWrap(wrap, scale) {
  const wr = wrap.getBoundingClientRect();
  const avoidRanges = [];

  const addRangeForEl = (el, pad = 8) => {
    if (!el) return;
    try {
      const r = el.getBoundingClientRect();
      if (!r || r.height < 8) return;
      const top = Math.max(0, Math.round((r.top - wr.top - pad) * scale));
      const bottom = Math.max(0, Math.round((r.bottom - wr.top + pad) * scale));
      if (bottom > top + 20) avoidRanges.push({top, bottom});
    } catch {}
  };

  // Keep these cards together when possible.
  addRangeForEl(wrap.querySelector('#normalized')?.closest('.card'), 10);
  addRangeForEl(wrap.querySelector('#raw')?.closest('.card'), 10);

  const previewsWrap = wrap.querySelector('#imagePreviews');
  const attachedHeading = previewsWrap?.querySelector('.pdf-keep-with-next');
  const firstPreviewImg = previewsWrap?.querySelector('img.evidence-img');

  for (const img of Array.from(wrap.querySelectorAll('img.evidence-img'))) {
    try {
      const r = img.getBoundingClientRect();
      if (!r || r.height < 10) continue;
      const pad = 6;
      let top = Math.max(0, Math.round((r.top - wr.top - pad) * scale));
      const bottom = Math.max(0, Math.round((r.bottom - wr.top + pad) * scale));

      if (attachedHeading && firstPreviewImg === img) {
        try {
          const hr = attachedHeading.getBoundingClientRect();
          const hTop = Math.max(0, Math.round((hr.top - wr.top - pad) * scale));
          if (Number.isFinite(hTop)) top = Math.min(top, hTop);
        } catch {}
      }

      if (bottom > top + 20) avoidRanges.push({top, bottom});
    } catch {}
  }

  avoidRanges.sort((a, b) => (a.top ?? 0) - (b.top ?? 0));
  return avoidRanges;
}

async function _sliceCanvasToFirstA4PreviewBlob(canvas, header, footerText, orientation, avoidRanges = []) {
  const {w: pageW, h: pageH} = _pageSize(orientation);

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
      if (top < pos && pos < bottom) return {top, bottom};
    }
    return null;
  };

  let desired = contentHeightPx;
  if (desired < canvas.height) {
    const r = findRange(desired);
    if (r) {
      const before = r.top;
      const after = r.bottom;
      if (before > 10) desired = before;
      else if (after <= contentHeightPx) desired = after;
    }
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

async function buildEvidencePreviewUrl(e, orientation, signal = null) {
  const key = _previewKey(e, orientation);
  const cached = _pdfPreviewCache.get(key);
  if (cached?.url) return cached.url;

  if (signal?.aborted) throw new Error('Preview cancelled');

  const main = document.querySelector('main');
  if (!main) throw new Error('Page layout missing');
  const w = main.getBoundingClientRect().width || 900;

  const exportedIso = new Date().toISOString();
  const header = _evidenceHeaderText(e?.id || '', exportedIso);
  const footer = (me?.sample_pdf_footer || '').trim();

  const topRow = main.querySelector('.d-flex.flex-wrap.gap-2.align-items-center.justify-content-between.mb-3');
  const evidenceCard = document.getElementById('evidenceCard');
  const normalizedCard = document.getElementById('normalized')?.closest('.card');
  const rawCard = document.getElementById('raw')?.closest('.card');

  const nodes = [topRow, evidenceCard, normalizedCard, rawCard].filter(Boolean);
  const h2c = window.html2canvas || globalThis.html2canvas;
  if (!h2c) throw new Error('html2canvas is not available');

  const wrap = _makeExportContainer(nodes, w);
  let url = '';
  try {
    if (signal?.aborted) throw new Error('Preview cancelled');

    // Shorter timeout for previews.
    await _waitForImages(wrap, 8000);
    if (signal?.aborted) throw new Error('Preview cancelled');

    const scale = 0.85;
    const avoidRanges = _computeAvoidRangesForWrap(wrap, scale);

    const canvas = await h2c(wrap, {
      scale,
      backgroundColor: '#fff',
      useCORS: true,
    });

    if (signal?.aborted) throw new Error('Preview cancelled');

    const blob = await _sliceCanvasToFirstA4PreviewBlob(canvas, header, footer, orientation, avoidRanges);
    url = URL.createObjectURL(blob);

    _pdfPreviewCache.set(key, {url, createdAt: Date.now()});
    return url;
  } catch (err) {
    if (url) _revokeObjectUrl(url);
    throw err;
  } finally {
    try {
      wrap.remove();
    } catch {}
  }
}

async function choosePdfOrientation(eventForPreview) {
  const modalEl = document.getElementById('pdfOptionsModal');
  const bs = globalThis.bootstrap;
  if (!modalEl || !bs?.Modal) {
    return _lastPdfOrientation || 'portrait';
  }

  const portrait = modalEl.querySelector('#pdfOptPortrait');
  const landscape = modalEl.querySelector('#pdfOptLandscape');
  const genBtn = modalEl.querySelector('#pdfOptionsGenerate');
  const statusEl = modalEl.querySelector('#pdfPreviewStatus');

  const portraitImg = modalEl.querySelector('#pdfPreviewPortraitImg');
  const landscapeImg = modalEl.querySelector('#pdfPreviewLandscapeImg');
  const portraitFrame = portraitImg?.closest('.pdf-preview-frame');
  const landscapeFrame = landscapeImg?.closest('.pdf-preview-frame');

  if (portrait && landscape) {
    const last = String(_lastPdfOrientation || 'portrait').toLowerCase();
    portrait.checked = last !== 'landscape';
    landscape.checked = last === 'landscape';
  }

  const setOrientation = (val) => {
    const v = String(val || 'portrait').toLowerCase();
    if (portrait && landscape) {
      portrait.checked = v !== 'landscape';
      landscape.checked = v === 'landscape';
    }
    _setPreviewSelected(modalEl, v);
  };

  // Bind preview-card click/keyboard handlers once.
  if (!modalEl.dataset.previewBound) {
    modalEl.dataset.previewBound = '1';
    modalEl.querySelectorAll('.pdf-preview-option').forEach((opt) => {
      const orient = String(opt.dataset.orientation || 'portrait').toLowerCase();
      const click = () => setOrientation(orient);
      opt.addEventListener('click', click);
      opt.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          click();
        }
      });
    });
    portrait?.addEventListener('change', () => {
      if (portrait.checked) setOrientation('portrait');
    });
    landscape?.addEventListener('change', () => {
      if (landscape.checked) setOrientation('landscape');
    });
  }

  // Ensure the selected styling matches the initial radio choice.
  setOrientation((landscape && landscape.checked) ? 'landscape' : 'portrait');

  const inst = bs.Modal.getOrCreateInstance(modalEl, {backdrop: 'static', keyboard: true});

  return await new Promise((resolve) => {
    let resolved = false;
    const controller = new AbortController();

    const done = (val) => {
      if (resolved) return;
      resolved = true;
      try {
        if (val) {
          _lastPdfOrientation = val;
          try {
            localStorage.setItem('keen_pdf_orientation', val);
          } catch {}
        }
      } catch {}
      resolve(val);
    };

    const onHidden = () => {
      try {
        controller.abort();
      } catch {}
      modalEl.removeEventListener('hidden.bs.modal', onHidden);
      if (!resolved) done(null);
    };

    const onGenerate = () => {
      const val = (landscape && landscape.checked) ? 'landscape' : 'portrait';
      done(val);
      try {
        inst.hide();
      } catch {}
    };

    const onShown = async () => {
      modalEl.removeEventListener('shown.bs.modal', onShown);

      // Reset frames each time.
      portraitFrame?.classList.remove('has-img');
      landscapeFrame?.classList.remove('has-img');
      if (portraitImg) portraitImg.removeAttribute('src');
      if (landscapeImg) landscapeImg.removeAttribute('src');

      if (statusEl) statusEl.style.display = '';
      try {
        genBtn.disabled = false;
      } catch {}

      const e = eventForPreview;
      if (!e) {
        if (statusEl) statusEl.style.display = 'none';
        return;
      }

      try {
        const [p, l] = await Promise.allSettled([
          buildEvidencePreviewUrl(e, 'portrait', controller.signal),
          buildEvidencePreviewUrl(e, 'landscape', controller.signal),
        ]);

        if (controller.signal.aborted) return;

        if (p.status === 'fulfilled' && portraitImg) {
          portraitImg.src = p.value;
          portraitFrame?.classList.add('has-img');
        }
        if (l.status === 'fulfilled' && landscapeImg) {
          landscapeImg.src = l.value;
          landscapeFrame?.classList.add('has-img');
        }
      } catch {
        // Non-fatal: previews are best-effort.
      } finally {
        if (statusEl) statusEl.style.display = 'none';
      }
    };

    modalEl.addEventListener('hidden.bs.modal', onHidden, {once: true});
    modalEl.addEventListener('shown.bs.modal', onShown, {once: true});
    genBtn?.addEventListener('click', onGenerate, {once: true});

    inst.show();
  });
}
function pretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

async function downloadArtifactsZip() {
  const e = currentEvent;
  const btn = document.getElementById('btnZip');
  if (!e) {
    toast(status, 'Event not loaded yet', 'warning');
    return;
  }
  const arts = Array.isArray(e.artifacts) ? e.artifacts : [];

  // Use the same stem for the ZIP filename and every file inside the ZIP.
  // (This stem already includes the configured evidence prefix, event id,
  // source tag and timestamp when available.)
  const zipStem = evidenceFileStem(e);
  if (arts.length === 0) {
    toast(status, 'No artifacts to download', 'warning');
    return;
  }

  const prevLabel = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Preparing ZIP…';
  }

  try {
    const files = [];

    // Include a PDF evidence report alongside the raw artifacts.
    toast(status, 'Rendering evidence PDF…', 'info');
    const pdfBlob = await buildEvidencePdfBlob(e, _lastPdfOrientation || 'portrait');
    const pdfBytes = new Uint8Array(await pdfBlob.arrayBuffer());
    files.push({
      name: `${zipStem}-evidence.pdf`,
      data: pdfBytes,
      mtime: new Date(),
    });

    toast(status, `Collecting ${arts.length} artifact(s)…`, 'info');
    const seenNames = new Map();

    for (let i = 0; i < arts.length; i++) {
      const a = arts[i];
      const url = _sampleExportUrl(`/api/v1/artifacts/${encodeURIComponent(a.id)}/download`);
      toast(status, `Downloading ${i + 1}/${arts.length}…`, 'info');

      const res = await fetch(url, {credentials: 'include'});
      if (!res.ok) throw new Error(`Failed to download artifact ${a.id} (HTTP ${res.status})`);
      const disp = res.headers.get('Content-Disposition') || '';
      // Prefer the server-provided filename extension (if any), otherwise
      // fall back to the artifact's content-type.
      const dispName = _parseContentDispositionFilename(disp);
      let ext = '';
      if (dispName) {
        const m = String(dispName).trim().match(/(\.[A-Za-z0-9]{1,16})$/);
        if (m) ext = m[1];
      }
      if (!ext) ext = _extFromContentType(a.content_type);

      // Name every artifact with the same prefix as the ZIP itself.
      // e.g. "<stem>-artifact-<artifact-uuid>.json"
      let name = `${zipStem}-artifact-${a.id}${ext}`;

      // Basic filesystem-safe cleanup
      name = String(name).replace(/[\\/\0]+/g, '_').replace(/\s+/g, ' ').trim();
      if (!name) name = `artifact-${a.id}${_extFromContentType(a.content_type)}`;

      // Ensure uniqueness inside the zip
      const base = name;
      const n = seenNames.get(base) || 0;
      if (n > 0) {
        const dot = base.lastIndexOf('.');
        if (dot > 0) name = `${base.slice(0, dot)} (${n + 1})${base.slice(dot)}`;
        else name = `${base} (${n + 1})`;
      }
      seenNames.set(base, n + 1);

      const buf = new Uint8Array(await res.arrayBuffer());
      const mtime = a.captured_at ? new Date(a.captured_at) : new Date();
      files.push({name, data: buf, mtime});
    }

    toast(status, 'Building ZIP…', 'info');
    const zipBlob = buildZipStore(files);

    const zipName = `${zipStem}.zip`;

    const obj = URL.createObjectURL(zipBlob);
    const a = document.createElement('a');
    a.href = obj;
    a.download = zipName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => {
      try {
        URL.revokeObjectURL(obj);
      } catch {}
    }, 10_000);

    toast(status, 'ZIP download started', 'success', 2500);
    // Ensure the banner disappears even if other async UI updates happen.
    setTimeout(() => {
      try {
        status.style.display = 'none';
      } catch {}
    }, 2800);
  } catch (err) {
    toast(status, String(err), 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prevLabel || 'Download all artifacts as evidence';
    }
  }
}

function setText(id, val) {
  const el = document.getElementById(id);
  el.textContent = val ?? '—';
}

let currentEvent = null;

// ------------------------------------------------------------------
// Evidence helpers
// ------------------------------------------------------------------

function _parseContentDispositionFilename(header) {
  const h = String(header || '');
  if (!h) return '';
  // Try RFC 5987: filename*=UTF-8''...
  const mStar = h.match(/filename\*\s*=\s*([^;]+)/i);
  if (mStar) {
    let v = mStar[1].trim();
    // Strip quotes if present
    v = v.replace(/^"|"$/g, '');
    const parts = v.split('\'\'');
    if (parts.length === 2) {
      try {
        return decodeURIComponent(parts[1]);
      } catch {
        return parts[1];
      }
    }
    return v;
  }
  // Fallback: filename="..."
  const m = h.match(/filename\s*=\s*"?([^";]+)"?/i);
  return m ? m[1].trim() : '';
}

function _extFromContentType(ct) {
  const t = String(ct || '').toLowerCase();
  if (t.includes('application/pdf')) return '.pdf';
  if (t.includes('application/json')) return '.json';
  if (t.includes('text/plain')) return '.txt';
  if (t.includes('text/html')) return '.html';
  if (t.includes('text/csv')) return '.csv';
  if (t.includes('image/jpeg')) return '.jpg';
  if (t.includes('image/png')) return '.png';
  if (t.includes('image/gif')) return '.gif';
  if (t.includes('image/webp')) return '.webp';
  return '';
}

// Minimal ZIP builder (store/no-compression) so we don't need extra dependencies.
// Produces a standard .zip that most tools can open.
const _crcTable = (() => {
  const tbl = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    tbl[n] = c >>> 0;
  }
  return tbl;
})();

function _crc32(buf) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) c = _crcTable[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function _dosTimeDate(d) {
  const dt = (d instanceof Date && !Number.isNaN(d.valueOf())) ? d : new Date();
  let year = dt.getFullYear();
  if (year < 1980) year = 1980;
  const month = dt.getMonth() + 1;
  const day = dt.getDate();
  const hours = dt.getHours();
  const minutes = dt.getMinutes();
  const seconds = Math.floor(dt.getSeconds() / 2);
  const time = (hours << 11) | (minutes << 5) | seconds;
  const date = ((year - 1980) << 9) | (month << 5) | day;
  return {time, date};
}

const _utf8 = new TextEncoder();

function _u8(viewBuf) {
  return new Uint8Array(viewBuf);
}

function _zipLocalHeader(nameLen, crc, size, time, date) {
  const buf = new ArrayBuffer(30);
  const dv = new DataView(buf);
  dv.setUint32(0, 0x04034b50, true);
  dv.setUint16(4, 20, true); // version needed
  dv.setUint16(6, 0x0800, true); // UTF-8
  dv.setUint16(8, 0, true); // store
  dv.setUint16(10, time, true);
  dv.setUint16(12, date, true);
  dv.setUint32(14, crc, true);
  dv.setUint32(18, size, true);
  dv.setUint32(22, size, true);
  dv.setUint16(26, nameLen, true);
  dv.setUint16(28, 0, true);
  return _u8(buf);
}

function _zipCentralHeader(nameLen, crc, size, time, date, localOffset) {
  const buf = new ArrayBuffer(46);
  const dv = new DataView(buf);
  dv.setUint32(0, 0x02014b50, true);
  dv.setUint16(4, 20, true); // version made by
  dv.setUint16(6, 20, true); // version needed
  dv.setUint16(8, 0x0800, true); // UTF-8
  dv.setUint16(10, 0, true); // store
  dv.setUint16(12, time, true);
  dv.setUint16(14, date, true);
  dv.setUint32(16, crc, true);
  dv.setUint32(20, size, true);
  dv.setUint32(24, size, true);
  dv.setUint16(28, nameLen, true);
  dv.setUint16(30, 0, true); // extra
  dv.setUint16(32, 0, true); // comment
  dv.setUint16(34, 0, true); // disk
  dv.setUint16(36, 0, true); // internal attrs
  dv.setUint32(38, 0, true); // external attrs
  dv.setUint32(42, localOffset, true);
  return _u8(buf);
}

function _zipEndRecord(count, cdSize, cdOffset) {
  const buf = new ArrayBuffer(22);
  const dv = new DataView(buf);
  dv.setUint32(0, 0x06054b50, true);
  dv.setUint16(4, 0, true);
  dv.setUint16(6, 0, true);
  dv.setUint16(8, count, true);
  dv.setUint16(10, count, true);
  dv.setUint32(12, cdSize, true);
  dv.setUint32(16, cdOffset, true);
  dv.setUint16(20, 0, true);
  return _u8(buf);
}

function buildZipStore(files) {
  const parts = [];
  const central = [];
  let offset = 0;
  let cdSize = 0;

  for (const f of files) {
    const nameBytes = _utf8.encode(String(f.name || 'file'));
    const data = (f.data instanceof Uint8Array) ? f.data : new Uint8Array(f.data || []);
    const crc = _crc32(data);
    const {time, date} = _dosTimeDate(f.mtime);

    const local = _zipLocalHeader(nameBytes.length, crc, data.length, time, date);
    parts.push(local, nameBytes, data);

    const localOffset = offset;
    offset += local.length + nameBytes.length + data.length;

    const cd = _zipCentralHeader(nameBytes.length, crc, data.length, time, date, localOffset);
    central.push(cd, nameBytes);
    cdSize += cd.length + nameBytes.length;
  }

  const cdOffset = offset;
  parts.push(...central);
  parts.push(_zipEndRecord(files.length, cdSize, cdOffset));
  return new Blob(parts, {type: 'application/zip'});
}


// PDF previews are served as PNG thumbnails via /api/v1/artifacts/{id}/preview.

function renderDiaryEvidence(e) {
  const block = document.getElementById('diaryBlock');
  if (!block) return;

  if ((e?.source || '') !== 'diary') {
    block.style.display = 'none';
    block.innerHTML = '';
    return;
  }

  const np = e?.normalized_payload || {};
  const details = (np && typeof np === 'object') ? (np.details || {}) : {};
  const notes = (details && typeof details === 'object' && typeof details.notes === 'string') ? details.notes : '';
  const links = Array.isArray(np?.links) ? np.links : [];

  let html = '';

  if (notes && notes.trim()) {
    html += `
          <div class="fw-bold mb-2">Diary notes</div>
          <div class="mb-3">${esc(notes).replaceAll('\n', '<br>')}</div>
        `;
  } else if (details && typeof details === 'object' && Object.keys(details).length) {
    const pretty = (() => {
      try {
        return JSON.stringify(details, null, 2);
      } catch {
        return String(details);
      }
    })();
    html += `
          <div class="fw-bold mb-2">Diary details</div>
          <pre class="pretty mono mb-3">${esc(pretty)}</pre>
        `;
  }

  const linkItems = links
      .map((l) => {
        const rawUrl = String(l?.url || '').trim();
        if (!rawUrl) return '';
        // Validate scheme: drop javascript:/data:/protocol-relative URLs so a
        // diary link cannot become a click-to-XSS vector. esc() alone does not
        // stop javascript: (it contains no HTML metacharacters).
        const url = safeExternalHref(rawUrl);
        if (!url) return '';
        const label = String(l?.text || '').trim() || rawUrl;
        return `<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer"> ${esc(label)}</a></li>`;
      })
      .filter(Boolean)
      .join('');

  if (linkItems) {
    html += `
          <div class="fw-bold mb-2">Links</div>
          <ul class="mb-0 ps-3">${linkItems}</ul>
        `;
  }

  if (!html.trim()) {
    block.style.display = 'none';
    block.innerHTML = '';
    return;
  }

  block.style.display = '';
  block.innerHTML = html + '<hr class="my-3">';
}

// ------------------------------------------------------------------
// Diary editor modal (admin-only)
// ------------------------------------------------------------------

let editAllControls = null;
let editAllControlsFramework = null;
let editSelectedRefs = [];

async function loadAllControlsOnce(frameworkSlug = null) {
  const fw = String(frameworkSlug || pageFramework || getCurrentFramework() || '').trim() || 'ISO27001:2022';
  if (editAllControls && editAllControlsFramework === fw) return editAllControls;
  try {
    const res = await apiGet(`/api/v1/controls?framework=${encodeURIComponent(fw)}&limit=5000`);
    editAllControls = (res.items || []).filter((it) => it.ref);
    editAllControlsFramework = fw;
  } catch {
    editAllControls = [];
    editAllControlsFramework = fw;
  }
  return editAllControls;
}

const editLinksWrap = document.getElementById('editDiaryLinks');

function editAddLinkRow(url = '', text = '') {
  if (!editLinksWrap) return;
  const row = document.createElement('div');
  row.className = 'row g-2 align-items-end';
  row.setAttribute('data-link-row', '1');
  row.innerHTML = `
        <div class="col-12 col-lg-6">
          <label class="form-label small-muted mb-1">URL</label>
          <input class="form-control" data-link-url value="${esc(url)}" placeholder="https://…" />
        </div>
        <div class="col-10 col-lg-5">
          <label class="form-label small-muted mb-1">Link text</label>
          <input class="form-control" data-link-text value="${esc(text)}" placeholder="e.g. Change request #123" />
        </div>
        <div class="col-2 col-lg-1 d-grid">
          <button type="button" class="btn btn-outline-danger" data-link-remove aria-label="Remove">×</button>
        </div>
      `;
  editLinksWrap.appendChild(row);
      row.querySelector('[data-link-remove]')?.addEventListener('click', () => row.remove());
}

function editReadLinks() {
  if (!editLinksWrap) return [];
  const links = [];
  editLinksWrap.querySelectorAll('[data-link-row]').forEach((row) => {
    const url = (row.querySelector('[data-link-url]')?.value || '').trim();
    const text = (row.querySelector('[data-link-text]')?.value || '').trim();
    if (url) links.push({url, text});
  });
  return links;
}

function editRenderSelected() {
  const wrap = document.getElementById('editControlSelected');
  if (!wrap) return;
  wrap.innerHTML = editSelectedRefs.map((ref) => `
        <span class="badge badge-soft">
          ${esc(ref)}
          <button type="button" class="btn btn-sm btn-link link-danger p-0 ms-1" data-remove="${esc(ref)}" aria-label="Remove">×</button>
        </span>
      `).join('');

  wrap.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ref = btn.getAttribute('data-remove');
      editSelectedRefs = editSelectedRefs.filter((r) => r !== ref);
      editRenderSelected();
    });
  });
}

function editRenderResults(items) {
  const resEl = document.getElementById('editControlResults');
  if (!resEl) return;
  if (!items.length) {
    resEl.innerHTML = '';
    return;
  }
  resEl.innerHTML = items.map((it) => {
    const title = it.title ? ` <span class="small-muted">${esc(it.title)}</span>` : '';
    return `
          <button type="button" class="list-group-item list-group-item-action" data-add="${esc(it.ref)}">
            <span class="mono">${esc(it.ref)}</span>${title}
          </button>
        `;
  }).join('');

  resEl.querySelectorAll('[data-add]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ref = btn.getAttribute('data-add');
      if (!ref) return;
      if (!editSelectedRefs.includes(ref)) editSelectedRefs.push(ref);
      editSelectedRefs.sort();
      editRenderSelected();
    });
  });
}

async function openEditDiaryModal() {
  const e = currentEvent;
  if (!e || (e.source || '') !== 'diary') return;
  if (!me?.is_admin) return;

  const modalEl = document.getElementById('editDiaryModal');
  if (!modalEl) return;

  // Prefill fields
  const np = e.normalized_payload || {};
  const sum = (np.summary || '').trim() || String(e.summary || '').replace(/^diary:\s*/i, '').trim();
  document.getElementById('editDiarySummary').value = sum;

  const details = np.details || {};
  const notes = (details && typeof details === 'object' && typeof details.notes === 'string') ? details.notes : '';
  const detailsEl = document.getElementById('editDiaryDetails');
  if (detailsEl) {
    if (notes) detailsEl.value = notes;
    else if (details && typeof details === 'object' && Object.keys(details).length) {
      try {
        detailsEl.value = JSON.stringify(details, null, 2);
      } catch {
        detailsEl.value = String(details);
      }
    } else {
      detailsEl.value = '';
    }
  }

  // Timestamp placeholder (do not auto-set value to avoid accidental changes)
  const tsEl = document.getElementById('editDiaryTs');
  if (tsEl) {
    tsEl.value = '';
    tsEl.placeholder = fmtTs(e.timestamp);
  }

  // Links
  if (editLinksWrap) {
    editLinksWrap.innerHTML = '';
    const links = Array.isArray(np.links) ? np.links : [];
    if (links.length) {
      for (const l of links) editAddLinkRow(String(l?.url || ''), String(l?.text || ''));
    }
    if (editLinksWrap.children.length === 0) editAddLinkRow();
  }

  // Controls selection
  editSelectedRefs = (e.controls || []).map((c) => c.ref).filter(Boolean);
  editSelectedRefs = Array.from(new Set(editSelectedRefs)).sort();
  editRenderSelected();


  // Wire up control search
  await loadAllControlsOnce(pageFramework);
  const searchEl = document.getElementById('editControlSearch');
  if (searchEl && !searchEl.dataset.bound) {
    searchEl.dataset.bound = '1';
    searchEl.addEventListener('input', (ev) => {
      const q = (ev.target.value || '').trim().toLowerCase();
      if (!q || !Array.isArray(editAllControls) || editAllControls.length === 0) {
        editRenderResults([]);
        return;
      }
      const hits = [];
      for (const c of editAllControls) {
        const hay = `${c.ref} ${c.title || ''}`.toLowerCase();
        if (hay.includes(q)) {
          hits.push(c);
          if (hits.length >= 25) break;
        }
      }
      editRenderResults(hits);
    });
  }

  // Add link button
  const addLinkBtn = document.getElementById('editDiaryAddLink');
  if (addLinkBtn && !addLinkBtn.dataset.bound) {
    addLinkBtn.dataset.bound = '1';
    addLinkBtn.addEventListener('click', () => editAddLinkRow());
  }

  // Save button
  const saveBtn = document.getElementById('editDiarySave');
  const modalStatus = document.getElementById('editDiaryStatus');
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = '1';
    saveBtn.addEventListener('click', async () => {
      if (modalStatus) {
        modalStatus.style.display = 'none'; modalStatus.textContent = '';
      }
      try {
        const summary = document.getElementById('editDiarySummary').value.trim();
        if (!summary) throw new Error('Missing summary');

        const detailsText = (document.getElementById('editDiaryDetails').value || '').trim();
        let detailsPayload = detailsText;
        if (detailsText && (detailsText.startsWith('{') || detailsText.startsWith('['))) {
          try {
            detailsPayload = JSON.parse(detailsText);
          } catch {
            detailsPayload = detailsText;
          }
        }

        const payload = {
          summary,
          details: detailsPayload,
          links: editReadLinks(),
          controls: editSelectedRefs.slice(),
          framework: pageFramework,
        };


        const tsVal = document.getElementById('editDiaryTs').value;
        if (tsVal) {
          const dt = new Date(tsVal);
          if (!Number.isNaN(dt.getTime())) payload.timestamp = dt.toISOString();
        }

        const res = await apiPatch(`/api/v1/admin/diary/${encodeURIComponent(e.id)}`, payload);

        // Close modal + refresh
        try {
          const inst = window.bootstrap?.Modal?.getOrCreateInstance(modalEl);
              inst?.hide();
        } catch {}

        toast(status, 'Diary entry updated', 'success');
        await load();
      } catch (err) {
        const msg = String(err);
        if (modalStatus) {
          modalStatus.className = 'alert alert-danger';
          modalStatus.textContent = msg;
          modalStatus.style.display = '';
        } else {
          toast(status, msg, 'danger');
        }
      }
    });
  }

  // Show modal
  try {
    const inst = window.bootstrap?.Modal?.getOrCreateInstance(modalEl);
        inst?.show();
  } catch {
    modalEl.style.display = '';
  }
}

// Dedicated print-window flow (opened via ?print=1)
let _autoPrinted = false;
async function autoPrintEvidence(e) {
  if (_autoPrinted) return;
  _autoPrinted = true;

  const exportedIso = new Date().toISOString();
  const headerText = _evidenceHeaderText(e?.id || id || '', exportedIso);
  const footerText = (me?.sample_pdf_footer || '').trim();
  _enablePrintableChrome(headerText, footerText);

  // Use document title as the default PDF name in most browsers.
  document.title = evidenceFileStem(e);

  _maskExportDom(document.body);

  // Ensure thumbnails load before the print dialog opens.
  document.querySelectorAll('img[loading=\'lazy\']').forEach((img) => {
    try {
      img.loading = 'eager';
    } catch {}
  });
  await _waitForImages(document.body, 20_000);

  // Give the browser a tick to lay out fixed headers/footers.
  setTimeout(() => {
    try {
      window.print();
    } catch {}
  }, 150);

  // Close the print window after printing/cancelling (best effort).
  window.addEventListener('afterprint', () => {
    try {
      window.close();
    } catch {}
  }, {once: true});
}

function renderIncidents(items) {
  const card = document.getElementById('incidentsCard');
  const body = document.getElementById('incidents');
  if (!card || !body) return;
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) {
    card.style.display = 'none';
    body.innerHTML = '—';
    return;
  }
  card.style.display = '';
  const canDelete = !!(me?.is_admin || me?.can_delete_incidents);
  body.innerHTML = arr.map((i) => {
    const who = i.created_by ? ` by ${esc(i.created_by)}` : '';
    const when = i.created_at ? fmtTs(i.created_at) : '—';
    const text = String(i.text || '').trim();
    const shortText = text.length > 350 ? `${text.slice(0, 350)}…` : text;
    const deleteBtn = canDelete ? `
          <button class="btn btn-sm btn-outline-danger" type="button" data-incident-delete="${esc(i.id || '')}" title="Delete incident">
            <i class="bi bi-trash" aria-hidden="true"></i> Delete
          </button>` : '';
    return `
      <div class="incident-ref border rounded-3 p-3 mb-2">
        <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between">
          <div class="fw-bold">${esc(i.title || 'Incident')}</div>
          <div class="d-flex flex-wrap gap-2 align-items-center">
            <div class="small-muted">${esc(when)}${who}</div>
            ${deleteBtn}
          </div>
        </div>
        ${shortText ? `<div class="small-muted mt-2 whitespace-pre-wrap">${esc(shortText)}</div>` : ''}
      </div>
    `;
  }).join('');
}

async function deleteIncident(incidentId) {
  if (!currentEvent || !incidentId) return;
  if (!(me?.is_admin || me?.can_delete_incidents)) {
    toast(status, 'incident.delete permission required.', 'danger');
    return;
  }
  const ok = window.confirm('Delete this incident reference from KEEN?');
  if (!ok) return;
  try {
    await apiDelete(`/api/v1/events/${encodeURIComponent(currentEvent.id)}/incidents/${encodeURIComponent(incidentId)}`);
    currentEvent.incidents = (currentEvent.incidents || []).filter((item) => String(item.id || '') !== String(incidentId));
    renderIncidents(currentEvent.incidents);
    toast(status, 'Incident deleted.', 'success');
  } catch (err) {
    toast(status, `Failed to delete incident: ${String(err)}`, 'danger');
  }
}

document.getElementById('incidents')?.addEventListener('click', (ev) => {
  const btn = ev.target?.closest?.('[data-incident-delete]');
  if (!btn) return;
  deleteIncident(btn.getAttribute('data-incident-delete') || '');
});

async function load() {
  if (!id) {
    toast(status, 'Missing event id.', 'danger');
    return;
  }
  status.style.display = 'none';

  try {
    sourceMeta = await getSourceMeta();
    const e = await apiGet(`/api/v1/events/${encodeURIComponent(id)}?framework=${encodeURIComponent(pageFramework)}`);
    currentEvent = e;

    document.getElementById('summary').textContent = e.summary || '(no summary)';
    const srcLabel = e.source ? (sourceLabel(e.source, sourceMeta) || e.source) : 'unknown';
    document.getElementById('meta').textContent = `${srcLabel} • ${fmtTs(e.timestamp)}`;

    setText('ts', fmtTs(e.timestamp));
    setText('system', e.system || '—');
    setText('actor', e.actor || '—');
    setText('action', e.action || '—');
    setText('outcome', e.outcome || '—');
    setText('severity', e.severity || '—');

    // Source badge + sideways navigation
    if (e.source) {
      const sourceHref = withFramework(`/source.html?source=${encodeURIComponent(e.source)}`, pageFramework);
      document.getElementById('srcBadge').innerHTML = sourceBadgeHtml(e.source, sourceMeta, sourceHref);
      document.getElementById('bySource').href = sourceHref;
    } else {
      document.getElementById('srcBadge').textContent = '—';
      document.getElementById('bySource').href = withFramework('/events.html', pageFramework);
    }

    // Open in source
    if (e.source_url) {
      const safeSource = safeExternalHref(e.source_url);
      if (safeSource) {
        const a = document.getElementById('openSource');
        a.href = safeSource;
        a.style.display = '';
      }
    }

    // Controls
    const controlsEl = document.getElementById('controls');
    const rationaleEl = document.getElementById('rationale');

    const controls = e.controls || [];
    if (!qs('framework')) {
      const inferred = String(controls?.[0]?.framework || '').trim();
      if (inferred) pageFramework = inferred;
    }

    if (controls.length === 0) {
      controlsEl.innerHTML = `<span class="badge text-bg-light">unmapped</span>`;
      if (rationaleEl) rationaleEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
    } else {
      // Render mapped controls list
      controlsEl.innerHTML = controls.map((c) => {
        const titleAttr = c.title ? ` title="${esc(c.title)}"` : '';
        const upstream = (c.upstream_url || '').trim();
        const titleHtml = c.title ?
              (upstream ?
                  `<a class="link-subtle small-muted" href="${esc(upstream)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-info-circle ms-1" aria-hidden="true"></i> ${esc(c.title)}</a>` :
                  `<span class="small-muted">${esc(c.title)}</span>`) :
              '';
        return `
              <div class="mb-2">
                <a class="badge badge-soft me-2" href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, c.framework || pageFramework)}"${titleAttr}>${esc(c.ref)}</a>
                ${titleHtml}
              </div>
            `;
      }).join('');
      // Render rationale
      if (rationaleEl) {
        const uniq = [];
        const seen = new Set();

        for (const c of controls) {
          const r = (c.rationale || '').trim();
          if (!r) continue;
          if (seen.has(r)) continue;
          seen.add(r);
          uniq.push(r);
        }

        if (uniq.length === 0) {
          rationaleEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
        } else if (uniq.length === 1) {
          rationaleEl.innerHTML = `<div class="mapping-rationale">${esc(uniq[0])}</div>`;
        } else {
          rationaleEl.innerHTML = `<ul class="mb-0 ps-3">
                ${uniq.map((r) => `<li class="mapping-rationale">${esc(r)}</li>`).join('')}
              </ul>`;
        }
      }
    }

    // Diary notes + links (if present)
    renderDiaryEvidence(e);

    // Show diary edit button for admins
    const editBtn = document.getElementById('btnEditDiary');
    if (editBtn && me?.is_admin && (e.source || '') === 'diary') {
      editBtn.style.display = '';
    } else if (editBtn) {
      editBtn.style.display = 'none';
    }

    // Show audit sampling button for users with audits.manage/admins.
    const auditBtn = document.getElementById('btnSampleAudit');
    if (auditBtn && (me?.can_manage_audits || me?.is_admin)) {
      auditBtn.style.display = '';
    } else if (auditBtn) {
      auditBtn.style.display = 'none';
    }

    const incidentBtn = document.getElementById('btnCreateIncident');
    if (incidentBtn && me?.can_create_incidents) {
      incidentBtn.style.display = '';
    } else if (incidentBtn) {
      incidentBtn.style.display = 'none';
    }

    renderIncidents(e.incidents || []);

    await refreshAuditSamples(e.id);

    // Artifacts list
    const arts = e.artifacts || [];
    const artsEl = document.getElementById('artifacts');

    // Show/hide the ZIP evidence button depending on whether we have artifacts.
    const zipBtn = document.getElementById('btnZip');
    if (zipBtn) zipBtn.style.display = arts.length ? '' : 'none';

    // Inline previews for attached images + PDFs (useful for diary evidence)
    const previewsEl = document.getElementById('imagePreviews');
    const imgArts = arts.filter((a) => ((a.content_type || '').toLowerCase()).startsWith('image/'));
    const pdfArts = arts.filter((a) => ((a.content_type || '').toLowerCase()).includes('application/pdf'));

    if (imgArts.length || pdfArts.length) {
      previewsEl.style.display = '';

      const imgHtml = imgArts.map((a) => {
        const src = `/api/v1/artifacts/${encodeURIComponent(a.id)}/download`;
        return `
              <div class="col-12 col-md-6">
                <a href="${src}" class="d-block" target="_blank" rel="noopener noreferrer">
                  <img class="evidence-img" src="${src}" alt="attachment" loading="${PRINT_MODE ? 'eager' : 'lazy'}" />
                </a>
              </div>
            `;
      }).join('');

      const pdfHtml = pdfArts.map((a) => {
        const src = `/api/v1/artifacts/${encodeURIComponent(a.id)}/download`;
        const prev = `/api/v1/artifacts/${encodeURIComponent(a.id)}/preview`;
        return `
              <div class="col-12 col-md-6">
                <div class="pdf-preview-card">
                  <div class="d-flex align-items-center justify-content-between mb-2">
                    <div class="small-muted">
                      <i class="bi bi-file-earmark-pdf"></i>
                      PDF document
                    </div>
                    <a class="btn btn-sm btn-outline-primary" href="${src}" target="_blank" rel="noopener noreferrer">Download</a>
                  </div>
                  <a href="${src}" class="d-block" target="_blank" rel="noopener noreferrer">
                    <img class="evidence-img evidence-pdf-thumb" src="${prev}" alt="PDF preview" loading="${PRINT_MODE ? 'eager' : 'lazy'}" />
                  </a>
                </div>
              </div>
            `;
      }).join('');

      previewsEl.innerHTML = `
            <div class="fw-bold mb-2 pdf-keep-with-next">Attached files</div>
            <div class="row g-2">
              ${imgHtml}
              ${pdfHtml}
            </div>
            <hr class="my-3">
          `;
      // PDF previews are thumbnails rendered server-side.
    } else {
      previewsEl.style.display = 'none';
      previewsEl.innerHTML = '';
    }
    if (arts.length === 0) {
      artsEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
    } else {
      artsEl.innerHTML = `
            <div class="table-responsive">
              <table class="table table-sm align-middle mb-0">
                <thead>
                  <tr>
                    <th>Captured</th>
                    <th>Kind</th>
                    <th>Type</th>
                    <th>Size</th>
                    <th class="no-print">Download</th>
                  </tr>
                </thead>
                <tbody>
                  ${arts.map((a) => `
                    <tr>
                      <td class="small-muted">${esc(fmtTs(a.captured_at))}</td>
                      <td><span class="badge badge-chip">${esc(a.kind || 'artifact')}</span></td>
                      <td class="small-muted">${esc(a.content_type || 'application/octet-stream')}</td>
                      <td class="small-muted">${esc(a.size_bytes ?? '—')}</td>
                      <td class="no-print"><a class="btn btn-sm btn-outline-primary" href="/api/v1/artifacts/${encodeURIComponent(a.id)}/download">Download</a></td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          `;
    }

    // Questions (auditor Q&A)
    await refreshQuestions(e.id);

    // JSON panes
    document.getElementById('normalized').textContent = pretty(e.normalized_payload ?? {});
    document.getElementById('raw').textContent = pretty(e.raw_pointer ?? {});

    // If this is the dedicated print window, trigger the native print dialog
    // after the event + thumbnails are rendered.
    if (PRINT_MODE) {
      await autoPrintEvidence(e);
    }
  } catch (err) {
    toast(status, `Failed to load event: ${String(err)}`, 'danger');
  }
}

document.getElementById('btnPdf').addEventListener('click', async () => {
  const e = currentEvent;
  const btn = document.getElementById('btnPdf');
  if (!e) {
    toast(status, 'Event not loaded yet', 'warning');
    return;
  }

  const prevLabel = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Preparing PDF…';
  }

  try {
    const orientation = await choosePdfOrientation(e);
    if (!orientation) {
      toast(status, 'PDF export cancelled', 'info', 2000);
      return;
    }

    toast(status, 'Rendering evidence PDF…', 'info');
    const pdfBlob = await buildEvidencePdfBlob(e, orientation);
    _triggerDownload(pdfBlob, evidenceFileStem(e) + '.pdf');

    toast(status, 'PDF download started', 'success', 2500);
    setTimeout(() => {
      try {
        status.style.display = 'none';
      } catch {}
    }, 2800);
  } catch (err) {
    toast(status, String(err), 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prevLabel || 'Sample as PDF evidence';
    }
  }
});

    document.getElementById('btnEditDiary')?.addEventListener('click', () => openEditDiaryModal());

    document.getElementById('btnZip')?.addEventListener('click', () => downloadArtifactsZip());



// ------------------------------------------------------------------
// Audit engagement sampling
// ------------------------------------------------------------------

const auditSampleModalEl = document.getElementById('auditSampleModal');
const auditSampleSelect = document.getElementById('auditSampleSelect');
const auditSampleNotes = document.getElementById('auditSampleNotes');
const auditSampleStatus = document.getElementById('auditSampleStatus');
const auditSampleSubmit = document.getElementById('auditSampleSubmit');
const auditSampleUseCurrentUrl = document.getElementById('auditSampleUseCurrentUrl');
const auditSampleCreate = document.getElementById('auditSampleCreate');

function _currentEventRelativeUrl() {
  const fw = pageFramework || getCurrentFramework();
  return withFramework(`/event.html?id=${encodeURIComponent(currentEvent?.id || id)}`, fw);
}

function canViewAuditEngagements() {
  return Boolean(me?.can_view_audits || me?.can_manage_audits || me?.is_admin);
}

function renderAuditSamples(items) {
  const card = document.getElementById('auditSamplesCard');
  const body = document.getElementById('auditSamples');
  const arr = Array.isArray(items) ? items : [];
  if (!card || !body) return;
  if (!arr.length) {
    card.style.display = 'none';
    body.innerHTML = '—';
    return;
  }

  card.style.display = '';
  body.innerHTML = `<div class="list-group">${arr.map((x) => {
    const a = x.audit || {};
    const ev = x.evidence || {};
    const url = withFramework(`/audit.html?id=${encodeURIComponent(a.id || '')}`, a.framework_slug || pageFramework || getCurrentFramework());
    const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ');
    const meta = [a.framework_slug, dates, ev.added_at ? `sampled ${fmtTs(ev.added_at)}` : '', ev.added_by_username ? `by ${ev.added_by_username}` : ''].filter(Boolean).join(' · ');
    const notes = (ev.notes || '').trim();
    return `<div class="list-group-item">
      <div class="d-flex flex-wrap gap-2 justify-content-between align-items-start">
        <div>
          <a class="fw-semibold" href="${esc(url)}">${esc(a.title || 'Audit')}</a>
          <div class="small-muted">${esc(meta || 'Sampled into this audit')}</div>
          ${notes ? `<div class="mt-2 small">${esc(notes).replaceAll('\n', '<br>')}</div>` : ''}
        </div>
        <span class="badge text-bg-light">${esc((a.status || 'open').replaceAll('_', ' '))}</span>
      </div>
    </div>`;
  }).join('')}</div>`;
}

async function refreshAuditSamples(eventId) {
  const card = document.getElementById('auditSamplesCard');
  if (!canViewAuditEngagements()) {
    if (card) card.style.display = 'none';
    return;
  }
  try {
    const data = await apiGet(`/api/v1/audits/by-event/${encodeURIComponent(eventId)}`);
    renderAuditSamples(data?.items || []);
  } catch (e) {
    // Do not block the evidence view if audit visibility is unavailable.
    if (card) card.style.display = 'none';
  }
}

async function openAuditSampleModal() {
  if (!currentEvent) {
    toast(status, 'Event not loaded yet', 'warning');
    return;
  }
  if (!(me?.can_manage_audits || me?.is_admin)) {
    toast(status, 'audits.manage permission required.', 'danger');
    return;
  }

  if (auditSampleStatus) auditSampleStatus.style.display = 'none';
  if (auditSampleNotes) { auditSampleNotes.value = ''; auditSampleNotes.disabled = false; }
  if (auditSampleUseCurrentUrl) auditSampleUseCurrentUrl.disabled = false;
  if (auditSampleSubmit) auditSampleSubmit.disabled = false;
  if (auditSampleCreate) auditSampleCreate.href = withFramework('/audits.html?new=1', pageFramework || getCurrentFramework());

  if (auditSampleSelect) {
    auditSampleSelect.innerHTML = '<option value="">Loading audits…</option>';
    try {
      const fw = pageFramework || getCurrentFramework();
      const data = await apiGet(`/api/v1/audits?framework=${encodeURIComponent(fw)}&limit=500`);
      const items = (data?.items || []).filter((a) => isAuditActive(a));
      auditSampleSelect.innerHTML = items.map((a) => {
        const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ');
        const suffix = dates ? ` · ${dates}` : '';
        const statusSuffix = (a.status || 'open') === 'in_progress' ? ' · In progress' : ' · Open';
        return `<option value="${esc(a.id)}">${esc(a.title || 'Audit')}${esc(suffix)}${esc(statusSuffix)}</option>`;
      }).join('') || '<option value="">No open or in-progress audits — start one first</option>';
      if (auditSampleSubmit) auditSampleSubmit.disabled = !items.length;
      if (auditSampleNotes) auditSampleNotes.disabled = !items.length;
      if (auditSampleUseCurrentUrl) auditSampleUseCurrentUrl.disabled = !items.length;
      if (!items.length) {
        toast(auditSampleStatus || status, 'No open or in-progress audits are available. Start an Audit first, then sample this event into it.', 'warning');
      }
    } catch (e) {
      auditSampleSelect.innerHTML = '<option value="">Failed to load audits</option>';
      if (auditSampleSubmit) auditSampleSubmit.disabled = true;
      if (auditSampleNotes) auditSampleNotes.disabled = true;
      if (auditSampleUseCurrentUrl) auditSampleUseCurrentUrl.disabled = true;
      toast(auditSampleStatus || status, `Failed to load audits: ${String(e)}`, 'danger');
    }
  }

  if (window.bootstrap?.Modal && auditSampleModalEl) {
    window.bootstrap.Modal.getOrCreateInstance(auditSampleModalEl).show();
  }
}

async function submitAuditSample() {
  const auditId = (auditSampleSelect?.value || '').trim();
  if (!auditId) {
    toast(auditSampleStatus || status, 'Start an Audit first, then sample this event into it.', 'warning');
    return;
  }
  const btn = auditSampleSubmit;
  const prev = btn?.textContent || '';
  if (btn) { btn.disabled = true; btn.textContent = 'Sampling…'; }
  try {
    const evidence_url = auditSampleUseCurrentUrl?.checked ? _currentEventRelativeUrl() : null;
    const body = {
      event_id: currentEvent.id,
      evidence_url,
      notes: auditSampleNotes?.value || '',
      title: currentEvent.summary || 'Keen event evidence',
    };
    await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence`, body);
    toast(status, 'Evidence sampled into audit', 'success');
    await refreshAuditSamples(currentEvent.id);
    if (window.bootstrap?.Modal && auditSampleModalEl) {
      window.bootstrap.Modal.getOrCreateInstance(auditSampleModalEl).hide();
    }
  } catch (e) {
    toast(auditSampleStatus || status, `Failed: ${String(e)}`, 'danger');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prev || 'Sample into audit'; }
  }
}

document.getElementById('btnSampleAudit')?.addEventListener('click', openAuditSampleModal);
auditSampleSubmit?.addEventListener('click', submitAuditSample);

// ------------------------------------------------------------------
// Incident creation webhook
// ------------------------------------------------------------------
const incidentModalEl = document.getElementById('incidentModal');
const incidentTitleEl = document.getElementById('incidentTitle');
const incidentTextEl = document.getElementById('incidentText');
const incidentTextCountEl = document.getElementById('incidentTextCount');
const incidentStatusEl = document.getElementById('incidentStatus');
const incidentSubmitBtn = document.getElementById('incidentSubmit');

function updateIncidentTextCount() {
  if (!incidentTextEl || !incidentTextCountEl) return;
  incidentTextCountEl.textContent = String(incidentTextEl.value.length || 0);
}

function openIncidentModal() {
  if (!me?.can_create_incidents) {
    toast(status, 'incident.create permission required.', 'danger');
    return;
  }
  if (!currentEvent) {
    toast(status, 'Event not loaded yet.', 'warning');
    return;
  }
  if (incidentStatusEl) incidentStatusEl.style.display = 'none';
  if (incidentTitleEl) {
    const summary = String(currentEvent.summary || 'Keen event incident').trim();
    const prefix = summary.toLowerCase().startsWith('incident') ? '' : 'Incident: ';
    incidentTitleEl.value = `${prefix}${summary}`.slice(0, 256);
  }
  if (incidentTextEl) incidentTextEl.value = '';
  updateIncidentTextCount();
  if (window.bootstrap?.Modal && incidentModalEl) {
    window.bootstrap.Modal.getOrCreateInstance(incidentModalEl).show();
  }
}

async function submitIncident() {
  if (!currentEvent) {
    toast(incidentStatusEl || status, 'Event not loaded yet.', 'warning');
    return;
  }
  const title = _sanitizePlainText(incidentTitleEl?.value || '', 256);
  const text = _sanitizePlainText(incidentTextEl?.value || '', 5000);
  if (!title) {
    toast(incidentStatusEl || status, 'Title is required.', 'warning');
    return;
  }
  const btn = incidentSubmitBtn;
  const prev = btn?.textContent || '';
  if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }
  try {
    const res = await apiPost(`/api/v1/events/${encodeURIComponent(currentEvent.id)}/incident`, {title, text});
    const incident = res?.incident;
    if (incident) {
      currentEvent.incidents = [incident, ...(currentEvent.incidents || [])];
      renderIncidents(currentEvent.incidents);
    }
    toast(status, 'Incident created from event.', 'success');
    if (window.bootstrap?.Modal && incidentModalEl) {
      window.bootstrap.Modal.getOrCreateInstance(incidentModalEl).hide();
    }
  } catch (e) {
    toast(incidentStatusEl || status, `Failed to create incident: ${String(e)}`, 'danger');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prev || 'Create Incident'; }
  }
}

document.getElementById('btnCreateIncident')?.addEventListener('click', openIncidentModal);
incidentTextEl?.addEventListener('input', updateIncidentTextCount);
incidentSubmitBtn?.addEventListener('click', submitIncident);

// ------------------------------------------------------------------
// Questions (auditor Q&A)
// ------------------------------------------------------------------

const questionsEl = document.getElementById('questions');
const btnAskQuestion = document.getElementById('btnAskQuestion');

const questionModalEl = document.getElementById('questionModal');
const questionBodyEl = document.getElementById('questionBody');
const questionAttachmentsEl = document.getElementById('questionAttachments');
const questionSubmitBtn = document.getElementById('questionSubmit');
const questionStatusEl = document.getElementById('questionStatus');

let questionModal = null;
try {
  if (questionModalEl && window.bootstrap?.Modal) {
    questionModal = new bootstrap.Modal(questionModalEl);
  }
} catch {}

const focusThreadId = String(qs('thread') || '').trim();
let didFocusThread = false;

function _qStatusBadge(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'answered') return `<span class="badge text-bg-success">answered</span>`;
  if (s === 'reviewing') return `<span class="badge text-bg-info">reviewing</span>`;
  return `<span class="badge text-bg-warning">unanswered</span>`;
}

function _renderAttLinks(atts) {
  const arr = Array.isArray(atts) ? atts : [];
  if (arr.length === 0) return '';
  const html = arr.map((a) => {
    const href = a?.download_url ? String(a.download_url) : '';
    if (!href) return '';
    const name = a?.filename ? esc(a.filename) : `attachment-${String(a?.artifact_id || '').slice(0, 8)}`;
    return `<a class="mono" href="${esc(href)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-paperclip"></i> ${name}</a>`;
  }).filter(Boolean).join(' ');
  return html ? `<div class="question-attachments small-muted mt-1">${html}</div>` : '';
}

function _renderThread(t) {
  const tid = t?.id || '';
  const createdBy = t?.created_by_username || '';
  const createdAt = t?.created_at ? fmtTs(t.created_at) : '—';
  const updatedAt = t?.updated_at ? fmtTs(t.updated_at) : '—';
  const statusVal = String(t?.status || 'unanswered').toLowerCase();
  const posts = Array.isArray(t?.posts) ? t.posts : [];

  const isAdmin = !!me?.is_admin;
  const isAuthor = (me?.user && createdBy && String(me.user) === String(createdBy));
  const canReply = isAdmin || isAuthor;

  const statusCtl = isAdmin ? `
        <select class="form-select form-select-sm" style="width:auto;" data-action="set-status" data-thread="${esc(tid)}">
          <option value="unanswered" ${statusVal === 'unanswered' ? 'selected' : ''}>unanswered</option>
          <option value="reviewing" ${statusVal === 'reviewing' ? 'selected' : ''}>reviewing</option>
          <option value="answered" ${statusVal === 'answered' ? 'selected' : ''}>answered</option>
        </select>
      ` : '';
  const deleteCtl = canDeleteQuestions ? `<button class="btn btn-sm btn-outline-danger" type="button" data-action="delete-question" data-thread="${esc(tid)}">Delete</button>` : '';

  const postsHtml = posts.map((p) => {
    const who = p?.author_username || '—';
    const when = p?.created_at ? fmtTs(p.created_at) : '—';
    const body = esc(p?.body || '').replace(/\n/g, '<br>');
    const atts = _renderAttLinks(p?.attachments);
    return `
          <div class="question-post">
            <div class="question-post-meta">
              <div class="small-muted"><span class="fw-bold">${esc(who)}</span> • ${esc(when)}</div>
            </div>
            <div class="mt-2">${body}</div>
            ${atts}
          </div>
        `;
  }).join('');

  const replyHtml = canReply ? `
        <div class="mt-3 no-print">
          <div class="small-muted mb-1">Reply</div>
          <textarea class="form-control" rows="3" id="replyBody-${esc(tid)}" placeholder="Write a response…"></textarea>
          <div class="d-flex flex-wrap gap-2 align-items-center mt-2">
            <input class="form-control form-control-sm" style="max-width: 360px;" type="file" multiple id="replyFiles-${esc(tid)}">
            <button class="btn btn-sm btn-primary" data-action="reply" data-thread="${esc(tid)}">Send reply</button>
          </div>
          <div class="form-text">Only admins or the original author can reply.</div>
        </div>
      ` : `<div class="small-muted mt-3">Only admins or the original author can reply.</div>`;

  return `
        <div class="question-thread" id="thread-${esc(tid)}">
          <div class="question-header">
            <div>
              <div class="question-title">Thread by <span class="mono">${esc(createdBy || '—')}</span></div>
              <div class="small-muted">Created ${esc(createdAt)} • Updated ${esc(updatedAt)}</div>
            </div>
            <div class="d-flex flex-wrap gap-2 align-items-center">
              ${_qStatusBadge(statusVal)}
              ${statusCtl}
              ${deleteCtl}
            </div>
          </div>
          ${postsHtml || `<div class="small-muted mt-2">No posts yet.</div>`}
          ${replyHtml}
        </div>
      `;
}

function _renderThreads(threads) {
  const arr = Array.isArray(threads) ? threads : [];
  if (!questionsEl) return;
  if (arr.length === 0) {
    questionsEl.innerHTML = `<span class="badge text-bg-light">none</span>`;
    return;
  }
  questionsEl.innerHTML = arr.map(_renderThread).join('');
}

async function refreshQuestions(eventId) {
  if (!questionsEl) return;
  try {
    const res = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}/questions`);
    _renderThreads(res?.threads || []);
    // Mark questions seen for the thread author so their bell clears immediately.
    if (!PRINT_MODE && !me?.is_admin) {
      try {
        await apiPost(`/api/v1/events/${encodeURIComponent(eventId)}/questions/mark-seen`, {});
      } catch {/* ignore */}
    }
    if (focusThreadId && !didFocusThread) {
      const el = document.getElementById(`thread-${focusThreadId}`);
      if (el) {
        didFocusThread = true;
        el.classList.add('question-thread-focus');
        try {
          el.scrollIntoView({behavior: 'smooth', block: 'start'});
        } catch {
          el.scrollIntoView();
        }
      }
    }
  } catch (e) {
    questionsEl.innerHTML = `<div class="text-danger small">Failed to load questions: ${esc(String(e))}</div>`;
  }
}

if (btnAskQuestion) {
  const canQuestion = !!(me?.is_admin || me?.can_question_events);
  btnAskQuestion.style.display = canQuestion ? '' : 'none';
  btnAskQuestion.addEventListener('click', () => {
    if (!questionModal) return;
    if (questionStatusEl) {
      questionStatusEl.style.display = 'none'; questionStatusEl.textContent = '';
    }
    if (questionBodyEl) questionBodyEl.value = '';
    if (questionAttachmentsEl) questionAttachmentsEl.value = '';
    questionModal.show();
  });
}

    questionSubmitBtn?.addEventListener('click', async () => {
      const e = currentEvent;
      if (!e?.id) {
        toast(status, 'Event not loaded yet', 'warning');
        return;
      }
      const body = String(questionBodyEl?.value || '').trim();
      if (!body) {
        toast(status, 'Please enter a question', 'warning');
        return;
      }

      if (questionStatusEl) {
        questionStatusEl.style.display = '';
        questionStatusEl.textContent = 'Submitting…';
      }

      const fd = new FormData();
      fd.append('body', body);
      const files = questionAttachmentsEl?.files ? Array.from(questionAttachmentsEl.files) : [];
      for (const f of files.slice(0, 10)) {
        fd.append('attachments', f, f.name);
      }

      try {
        await apiPostForm(`/api/v1/events/${encodeURIComponent(e.id)}/questions`, fd);
        questionModal?.hide();
        toast(status, 'Question submitted', 'success', 2500);
        await refreshQuestions(e.id);
      } catch (err) {
        const msg = String(err);
        if (questionStatusEl) {
          questionStatusEl.style.display = '';
          questionStatusEl.textContent = msg;
        } else {
          toast(status, msg, 'danger');
        }
      }
    });

    questionsEl?.addEventListener('click', async (ev) => {
      const delBtn = ev.target?.closest?.('[data-action="delete-question"]');
      if (delBtn) {
        const tid = delBtn.getAttribute('data-thread');
        if (!tid || !canDeleteQuestions) return;
        if (!confirm('Delete this question thread and all replies?')) return;
        delBtn.disabled = true;
        try {
          await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
          toast(status, 'Question deleted', 'success', 2000);
          if (currentEvent?.id) await refreshQuestions(currentEvent.id);
        } catch (err) {
          toast(status, String(err), 'danger');
          delBtn.disabled = false;
        }
        return;
      }

      const btn = ev.target?.closest?.('[data-action="reply"]');
      if (!btn) return;
      const tid = btn.getAttribute('data-thread');
      if (!tid) return;

      const bodyEl = document.getElementById(`replyBody-${tid}`);
      const fileEl = document.getElementById(`replyFiles-${tid}`);
      const body = String(bodyEl?.value || '').trim();
      if (!body) {
        toast(status, 'Please enter a reply', 'warning');
        return;
      }

      btn.disabled = true;
      const prev = btn.textContent;
      btn.textContent = 'Sending…';

      const fd = new FormData();
      fd.append('body', body);
      const files = fileEl?.files ? Array.from(fileEl.files) : [];
      for (const f of files.slice(0, 10)) {
        fd.append('attachments', f, f.name);
      }

      try {
        await apiPostForm(`/api/v1/questions/${encodeURIComponent(tid)}/posts`, fd);
        toast(status, 'Reply sent', 'success', 2000);
        if (currentEvent?.id) await refreshQuestions(currentEvent.id);
        if (bodyEl) bodyEl.value = '';
        if (fileEl) fileEl.value = '';
      } catch (err) {
        toast(status, String(err), 'danger');
      } finally {
        btn.disabled = false;
        btn.textContent = prev || 'Send reply';
      }
    });

    questionsEl?.addEventListener('change', async (ev) => {
      const sel = ev.target?.closest?.('[data-action="set-status"]');
      if (!sel) return;
      const tid = sel.getAttribute('data-thread');
      const newStatus = String(sel.value || '').trim();
      if (!tid || !newStatus) return;
      try {
        await apiPatch(`/api/v1/admin/questions/${encodeURIComponent(tid)}`, {status: newStatus});
        if (currentEvent?.id) await refreshQuestions(currentEvent.id);
      } catch (err) {
        toast(status, String(err), 'danger');
      }
    });

load();
