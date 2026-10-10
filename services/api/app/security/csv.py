"""Keep untrusted CSV text from becoming spreadsheet formulas."""


def spreadsheet_cell(value):
    if not isinstance(value, str):
        return value
    # Importers differ in their handling of leading whitespace/control bytes.
    probe = value.lstrip(" \t\r\n\v\f\x00\ufeff")
    if probe.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value
