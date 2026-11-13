// Protect spreadsheet exports while preserving numeric values.
export function csvEscape(value) {
  if (value == null) return '';
  let text = String(value);
  if (typeof value === 'string') {
    // A spreadsheet that splits on semicolons or tabs starts a cell after each
    // one, and after a line break, whatever this file's delimiter and quoting;
    // security/csv.py guards the server's exports the same way.
    text = text.replace(/([;\t\r\n][ \t]*)(?=[=+@-])/g, "$1'");
    if (/^[ \t\r\n\v\f\x00\ufeff]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) {
      text = "'" + text;
    }
  }
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}
