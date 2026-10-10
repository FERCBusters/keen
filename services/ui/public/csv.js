// Protect spreadsheet exports while preserving numeric values.
export function csvEscape(value) {
  if (value == null) return '';
  let text = String(value);
  if (typeof value === 'string' && (/^[ \t\r\n\v\f\x00\ufeff]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text))) {
    text = "'" + text;
  }
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}
