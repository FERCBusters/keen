/**
 * Return the value when it is a path on this site, otherwise ''.
 *
 * Browsers read a backslash as "/" and drop tabs and newlines while parsing,
 * so "/" followed by either can name another host. The value is returned as
 * given: a path the URL parser writes out again (pathname + search + hash) is
 * a new value, since resolved dot segments ("/.//host") can leave it starting
 * with "//", and needs checking again.
 */
export function localPath(raw) {
  const value = String(raw ?? '');
  if (!value.startsWith('/') || value.startsWith('//')) return '';
  return /[\\\u0000-\u001f\u007f]/.test(value) ? '' : value;
}

// nginx sends a request whose path begins with "//" home. A front proxy that
// rewrites the path hands nginx a merged one instead, so a Keen page can still
// open at a path such as "//host/", and a script that writes that path out
// again (pathname + search) sends the browser to the host. Every Keen page
// outside Help loads this module before its own code runs, so such a page
// gives way to the home page first.
if (globalThis.location?.pathname?.startsWith('//')) location.replace('/');

/** Resolve post-authentication navigation using the browser's URL rules. */
export function safeNext(raw, origin = location.origin) {
  const value = localPath(String(raw).trim());
  if (!value) return '/';
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin || ['/login.html', '/mfa.html'].includes(url.pathname)) return '/';
    // Never carry credentials accidentally placed in a login URL into redirects.
    if (url.searchParams.has('password')) {
      url.searchParams.delete('password');
      url.searchParams.delete('username');
    }
    // "/.//host", "/%2e//host" and "/x/..//host" resolve to the path "//host".
    return localPath(url.pathname + url.search + url.hash) || '/';
  } catch {
    return '/';
  }
}
