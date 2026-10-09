// UTC date ranges and histogram buckets; independent of page state.

export const MS_PER_DAY = 24 * 60 * 60 * 1000;

export function histStepMs(interval) {
  const it = String(interval || 'day').trim().toLowerCase();
  if (it === 'second') return 1000;
  if (it === 'minute') return 60 * 1000;
  if (it === 'hour') return 60 * 60 * 1000;
  return MS_PER_DAY;
}

export function histNextFinerInterval(interval) {
  const it = String(interval || 'day').trim().toLowerCase();
  if (it === 'day') return 'hour';
  if (it === 'hour') return 'minute';
  if (it === 'minute') return 'second';
  return 'second';
}

export function histBucketToUtcMs(bucket, interval) {
  const b = String(bucket || '').trim();
  if (!b) return NaN;
  if (String(interval || 'day').trim().toLowerCase() === 'day') {
    // Ensure date-only buckets are interpreted in UTC.
    return Date.parse(`${b}T00:00:00Z`);
  }
  // Backend returns timestamps with a trailing Z.
  return Date.parse(b);
}

export function isIsoDate(v) {
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v);
}

export function utcMsFromIsoDate(v) {
  if (!isIsoDate(v)) return NaN;
  const [y, m, d] = v.split('-').map((x) => Number(x));
  if (!y || !m || !d) return NaN;
  return Date.UTC(y, m - 1, d);
}

export function isoDateFromUtcMs(ms) {
  const dt = new Date(ms);
  // Use UTC getters to avoid local timezone shifting.
  const y = dt.getUTCFullYear();
  const m = String(dt.getUTCMonth() + 1).padStart(2, '0');
  const d = String(dt.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function isoTodayUtc() {
  const now = new Date();
  return isoDateFromUtcMs(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

export function isoAddDays(iso, deltaDays) {
  const base = utcMsFromIsoDate(iso);
  if (!Number.isFinite(base)) return null;
  return isoDateFromUtcMs(base + deltaDays * MS_PER_DAY);
}

export function diffDaysInclusive(startIso, endIso) {
  const a = utcMsFromIsoDate(startIso);
  const b = utcMsFromIsoDate(endIso);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.floor((b - a) / MS_PER_DAY) + 1;
}
