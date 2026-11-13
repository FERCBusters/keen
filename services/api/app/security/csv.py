"""Keep untrusted CSV text from becoming spreadsheet formulas."""

from app.security.regex import scan_pattern

# A spreadsheet that takes semicolons or tabs as its column separator, as
# several locales do when a .csv file is opened directly, splits a cell there
# whatever the file's own delimiter and quoting. Such a reader does not see
# the quote that opens a comma-separated field either, so a line break inside
# a value can end its row and begin another. A formula could so begin in the
# middle of a cell Keen wrote.
_FORMULA_AFTER_SEPARATOR = scan_pattern(r"([;\t\r\n][ \t]*+)(?=[=+\-@])")


def spreadsheet_cell(value):
    if not isinstance(value, str):
        return value
    # Each part such a reader would make into a cell of its own, after a
    # separator or a line break, is guarded like the start of the cell.
    value = _FORMULA_AFTER_SEPARATOR.sub(r"\1'", value)
    # Importers differ in their handling of leading whitespace/control bytes.
    probe = value.lstrip(" \t\r\n\v\f\x00\ufeff")
    if probe.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value
