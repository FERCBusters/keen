// Graph label layout; only the renderer requires a D3 selection.

export function truncateMiddleText(value, maxChars = 64) {
  const s = String(value || '').trim();
  const n = Number(maxChars || 0);
  if (!n || s.length <= n) return s;
  if (n <= 4) return s.slice(0, n);
  const left = Math.ceil((n - 1) / 2);
  const right = Math.floor((n - 1) / 2);
  return `${s.slice(0, left)}…${s.slice(s.length - right)}`;
}

export function splitLongToken(token, maxChars) {
  const out = [];
  let s = String(token || '');
  const n = Math.max(4, Number(maxChars || 12));
  while (s.length > n) {
    out.push(s.slice(0, n));
    s = s.slice(n);
  }
  if (s) out.push(s);
  return out;
}

export function wrapLabelLines(value, maxCharsPerLine, maxLines = 3) {
  const raw = String(value || '').replace(/\s+/g, ' ').trim();
  const perLine = Math.max(4, Number(maxCharsPerLine || 18));
  const lineLimit = Math.max(1, Number(maxLines || 1));
  if (!raw) return [];

  const tokens = raw.split(' ').flatMap((w) => w.length > perLine ? splitLongToken(w, perLine) : [w]);
  const lines = [];
  let line = '';
  for (const token of tokens) {
    const next = line ? `${line} ${token}` : token;
    if (next.length <= perLine) {
      line = next;
      continue;
    }
    if (line) lines.push(line);
    line = token;
    if (lines.length >= lineLimit) break;
  }
  if (line && lines.length < lineLimit) lines.push(line);

  if (tokens.join(' ').length > lines.join(' ').length && lines.length) {
    lines[lines.length - 1] = truncateMiddleText(lines[lines.length - 1], Math.max(4, perLine - 1));
    if (!lines[lines.length - 1].endsWith('…')) lines[lines.length - 1] = `${lines[lines.length - 1]}…`;
  }
  return lines;
}

export function renderWrappedNodeLabel(textSel, value, opts = {}) {
  if (!textSel) return;
  const width = Number(opts.width || 160);
  const height = Number(opts.height || 28);
  const x = Number(opts.x ?? width / 2);
  const y = Number(opts.y ?? height / 2);
  const linePx = Number(opts.linePx || 14);
  const charPx = Number(opts.charPx || 7);
  const maxLines = Math.max(1, Math.min(Number(opts.maxLines || 3), Math.floor(Math.max(1, height - 8) / linePx) || 1));
  const maxChars = Math.max(4, Math.floor(Math.max(20, width - 16) / charPx));
  const lines = wrapLabelLines(value, maxChars, maxLines);
  const totalHeight = Math.max(linePx, lines.length * linePx);
  const startY = y - (totalHeight / 2) + (linePx / 2);

  textSel.text(null);
  lines.forEach((line, idx) => {
    textSel
        .append('tspan')
        .attr('x', x)
        .attr('y', startY + (idx * linePx))
        .text(line);
  });
}
