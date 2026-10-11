import { fmtTsFilename, sourceLabel, toast } from '/app.js';
import { parseContentDispositionFilename, extFromContentType, buildZipStore } from './archive.js';
import {
  sliceCanvasToA4JpegPages,
  buildPdfFromJpegPages,
  sliceCanvasToFirstA4PreviewBlob,
} from './pdf.js';

// Export preferences and preview state belong to this page instance.
export function createEvidenceExporter({ preferences: me, getSourceMeta, status }) {
  const _eventDataMaskingMode = String(me?.event_data_masking || 'false')
    .trim()
    .toLowerCase();
  function _maskSampleExportsEnabled() {
    return _eventDataMaskingMode === 'samples' || _eventDataMaskingMode === 'true';
  }
  function _isValidIpv4Literal(value) {
    return /^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/.test(
      String(value || ''),
    );
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
    txt = txt.replace(
      /(^|[^A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,128})@([A-Za-z0-9.-]+\.[A-Za-z]{2,63})(?![A-Za-z0-9._%+-])/g,
      '$1[MASKED_EMAIL]',
    );
    txt = txt.replace(
      /(^|[^\w:.])([A-Fa-f0-9:.]+(?::[A-Fa-f0-9:.]*)+(?:%[A-Za-z0-9_.-]+)?)(?![\w:])/g,
      (match, prefix, raw) => {
        if (!_isMaskableIpv6Candidate(raw)) return match;
        return `${prefix}[MASKED_IP]`;
      },
    );
    txt = txt.replace(
      /(^|[^\w.])((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})(?![\w.])/g,
      '$1[MASKED_IP]',
    );
    return txt;
  }
  function _maskExportDom(root) {
    if (!root || !_maskSampleExportsEnabled()) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node?.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName))
          return NodeFilter.FILTER_REJECT;
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
        if (el.hasAttribute(attr))
          el.setAttribute(attr, maskEventDataString(el.getAttribute(attr) || ''));
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
    const src = e.source ? sourceLabel(e.source, getSourceMeta()) || e.source : 'unknown';
    const ts = e.timestamp ? fmtTsFilename(e.timestamp) : '';
    const parts = [];
    if (_evidenceNamePrefix) parts.push(_evidenceNamePrefix);
    if (e.id) parts.push(String(e.id));
    if (src) parts.push(String(src));
    if (ts) parts.push(String(ts));
    const tail = parts.filter(Boolean).join(' ');
    const stem = tail ? 'Keen Event - ' + tail : 'Keen Event';

    let out = _sanitizeFilename(stem);
    // Avoid extremely long default names in some browsers/filesystems.
    if (out.length > 180) out = out.slice(0, 180).trim();
    return out || 'Keen Event';
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
    await Promise.race([Promise.all(pending), new Promise((r) => setTimeout(r, timeoutMs))]);
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

  async function buildEvidencePdfBlob(e, orientation = 'portrait') {
    const main = document.querySelector('main');
    if (!main) throw new Error('Page layout missing');
    const w = main.getBoundingClientRect().width || 900;

    const exportedIso = new Date().toISOString();
    const header = _evidenceHeaderText(e?.id || '', exportedIso);
    const footer = (me?.sample_pdf_footer || '').trim();

    const topRow = main.querySelector(
      '.d-flex.flex-wrap.gap-2.align-items-center.justify-content-between.mb-3',
    );
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
            if (bottom > top + 20) avoidRanges.push({ top, bottom });
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

            if (bottom > top + 20) avoidRanges.push({ top, bottom });
          } catch {}
        }

        const canvas = await h2c(wrap, {
          scale: 2,
          backgroundColor: '#fff',
          useCORS: true,
        });
        const pages = await sliceCanvasToA4JpegPages(
          canvas,
          header,
          footer,
          orientation,
          avoidRanges,
        );
        allPages.push(...pages);
      } finally {
        try {
          wrap.remove();
        } catch {}
      }
    }

    return buildPdfFromJpegPages(allPages, orientation);
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
        if (bottom > top + 20) avoidRanges.push({ top, bottom });
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

        if (bottom > top + 20) avoidRanges.push({ top, bottom });
      } catch {}
    }

    avoidRanges.sort((a, b) => (a.top ?? 0) - (b.top ?? 0));
    return avoidRanges;
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

    const topRow = main.querySelector(
      '.d-flex.flex-wrap.gap-2.align-items-center.justify-content-between.mb-3',
    );
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

      const blob = await sliceCanvasToFirstA4PreviewBlob(
        canvas,
        header,
        footer,
        orientation,
        avoidRanges,
      );
      url = URL.createObjectURL(blob);

      _pdfPreviewCache.set(key, { url, createdAt: Date.now() });
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
    setOrientation(landscape && landscape.checked ? 'landscape' : 'portrait');

    const inst = bs.Modal.getOrCreateInstance(modalEl, { backdrop: 'static', keyboard: true });

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
        modalEl.removeEventListener('shown.bs.modal', onShown);
        genBtn?.removeEventListener('click', onGenerate);
        if (!resolved) done(null);
      };

      const onGenerate = () => {
        const val = landscape && landscape.checked ? 'landscape' : 'portrait';
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

      modalEl.addEventListener('hidden.bs.modal', onHidden, { once: true });
      modalEl.addEventListener('shown.bs.modal', onShown, { once: true });
      genBtn?.addEventListener('click', onGenerate, { once: true });

      inst.show();
    });
  }

  async function downloadArtifactsZip(e) {
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

        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) throw new Error(`Failed to download artifact ${a.id} (HTTP ${res.status})`);
        const disp = res.headers.get('Content-Disposition') || '';
        // Prefer the server-provided filename extension (if any), otherwise
        // fall back to the artifact's content-type.
        const dispName = parseContentDispositionFilename(disp);
        let ext = '';
        if (dispName) {
          const m = String(dispName)
            .trim()
            .match(/(\.[A-Za-z0-9]{1,16})$/);
          if (m) ext = m[1];
        }
        if (!ext) ext = extFromContentType(a.content_type);

        // Name every artifact with the same prefix as the ZIP itself.
        // e.g. "<stem>-artifact-<artifact-uuid>.json"
        let name = `${zipStem}-artifact-${a.id}${ext}`;

        // Basic filesystem-safe cleanup
        name = String(name)
          .replace(/[\\/\0]+/g, '_')
          .replace(/\s+/g, ' ')
          .trim();
        if (!name) name = `artifact-${a.id}${extFromContentType(a.content_type)}`;

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
        files.push({ name, data: buf, mtime });
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

  // Dedicated print-window flow (opened via ?print=1)
  let _autoPrinted = false;
  async function autoPrintEvidence(e) {
    if (_autoPrinted) return;
    _autoPrinted = true;

    const exportedIso = new Date().toISOString();
    const headerText = _evidenceHeaderText(e?.id || '', exportedIso);
    const footerText = (me?.sample_pdf_footer || '').trim();
    _enablePrintableChrome(headerText, footerText);

    // Use document title as the default PDF name in most browsers.
    document.title = evidenceFileStem(e);

    _maskExportDom(document.body);

    // Ensure thumbnails load before the print dialog opens.
    document.querySelectorAll("img[loading='lazy']").forEach((img) => {
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
    window.addEventListener(
      'afterprint',
      () => {
        try {
          window.close();
        } catch {}
      },
      { once: true },
    );
  }

  async function downloadPdf(e) {
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
  }

  return { downloadPdf, downloadArtifactsZip, autoPrintEvidence };
}
