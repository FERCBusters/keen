/** Resolve post-authentication navigation using the browser's URL rules. */
export function safeNext(raw, origin = location.origin) {
  const value = String(raw || '').trim();
  if (!value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u001f\u007f]/.test(value)) return '/';
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin || ['/login.html', '/mfa.html'].includes(url.pathname)) return '/';
    return url.pathname + url.search + url.hash;
  } catch {
    return '/';
  }
}
