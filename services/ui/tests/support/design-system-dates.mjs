// Timestamp and date helpers that read the same window globals, at call time,
// as the pinned design system (FERCBusters/mosp-design-system 58c7da37,
// src/js/110-dates.js): mospPreferences (use_local_timezone, timezone,
// date_format), mospDefaultTimezone and mospDefaultDateFormat. Other UI tests
// stub these helpers, so only tests using this double notice a page that sets
// other globals.
function normalizeDateFormat(raw, fallback = 'ymd') {
  const v = String(raw || '')
    .trim()
    .toLowerCase();
  if (v === 'dd/mm/yyyy' || v === 'dd-mm-yyyy' || v === 'dmy') return 'dmy';
  if (v === 'yyyy-mm-dd' || v === 'yyyy/mm/dd' || v === 'iso' || v === 'ymd') return 'ymd';
  if (fallback === '') return '';
  return fallback === 'dmy' ? 'dmy' : 'ymd';
}

export function designSystemDates(getWindow) {
  const preferences = () => getWindow().mospPreferences || {};
  const dateFormat = () => {
    const prefs = preferences();
    const raw = String(prefs.date_format || prefs.dateFormat || '')
      .trim()
      .toLowerCase();
    const pref = raw === 'default' ? '' : normalizeDateFormat(raw, '');
    if (pref) return pref;
    const globalDefault = getWindow().mospDefaultDateFormat;
    return globalDefault ? normalizeDateFormat(globalDefault) : 'ymd';
  };
  const timezone = () => {
    const prefs = preferences();
    if (prefs.use_local_timezone) {
      return String(prefs.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone).trim();
    }
    return String(getWindow().mospDefaultTimezone || '').trim();
  };
  const isoDateToDisplay = (iso) => {
    const m = String(iso || '').match(/^\s*(\d{4})-(\d{2})-(\d{2})\s*$/);
    if (!m) return '';
    return dateFormat() === 'dmy' ? `${m[3]}/${m[2]}/${m[1]}` : `${m[1]}-${m[2]}-${m[3]}`;
  };
  const fmtTs = (iso) => {
    if (!iso) return '';
    let s = String(iso).trim();
    if (!/[zZ]$/.test(s) && !/[+-]\d{2}:\d{2}$/.test(s) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s))
      s += 'Z';
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return String(iso);
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: timezone() || undefined,
      calendar: 'gregory',
      numberingSystem: 'latn',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(d);
    const get = (type) => parts.find((p) => p.type === type)?.value || '';
    return `${isoDateToDisplay(`${get('year')}-${get('month')}-${get('day')}`)} ${get('hour')}:${get('minute')}:${get('second')}`;
  };
  return { fmtTs, isoDateToDisplay };
}
