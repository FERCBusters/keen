// Archive encoding, download filename metadata and browser downloads; no page state.
export function parseContentDispositionFilename(header) {
  const h = String(header || '');
  if (!h) return '';
  // Try RFC 5987: filename*=UTF-8''...
  const mStar = h.match(/filename\*\s*=\s*([^;]+)/i);
  if (mStar) {
    let v = mStar[1].trim();
    // Strip quotes if present
    v = v.replace(/^"|"$/g, '');
    const parts = v.split("''");
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

// Starts a browser download of a blob and releases its object URL afterwards.
export function downloadBlob(blob, filename) {
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

// Bound actual downloaded bytes, including responses without trustworthy metadata.
export const MAX_ZIP_EXPORT_BYTES = 100 * 1024 * 1024;
export const MAX_ZIP_EXPORT_ARTIFACTS = 100;

export async function readBoundedExportResponse(response, remainingBytes) {
  const declared = Number(response.headers.get('Content-Length'));
  if (Number.isFinite(declared) && declared > remainingBytes) {
    await response.body?.cancel();
    throw new Error('ZIP export exceeds the 100 MiB limit. Download artifacts individually.');
  }
  if (!response.body) throw new Error('Artifact response has no readable body');
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  let complete = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        complete = true;
        break;
      }
      size += value.byteLength;
      if (size > remainingBytes)
        throw new Error('ZIP export exceeds the 100 MiB limit. Download artifacts individually.');
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes;
  } finally {
    if (!complete) {
      try {
        await reader.cancel();
      } catch {}
    }
    reader.releaseLock();
  }
}

export function extFromContentType(ct) {
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
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    tbl[n] = c >>> 0;
  }
  return tbl;
})();

function _crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = _crcTable[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function _dosTimeDate(d) {
  const dt = d instanceof Date && !Number.isNaN(d.valueOf()) ? d : new Date();
  let year = dt.getFullYear();
  if (year < 1980) year = 1980;
  const month = dt.getMonth() + 1;
  const day = dt.getDate();
  const hours = dt.getHours();
  const minutes = dt.getMinutes();
  const seconds = Math.floor(dt.getSeconds() / 2);
  const time = (hours << 11) | (minutes << 5) | seconds;
  const date = ((year - 1980) << 9) | (month << 5) | day;
  return { time, date };
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

export function buildZipStore(files) {
  const parts = [];
  const central = [];
  let offset = 0;
  let cdSize = 0;

  for (const f of files) {
    const nameBytes = _utf8.encode(String(f.name || 'file'));
    const data = f.data instanceof Uint8Array ? f.data : new Uint8Array(f.data || []);
    const crc = _crc32(data);
    const { time, date } = _dosTimeDate(f.mtime);

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
  return new Blob(parts, { type: 'application/zip' });
}
