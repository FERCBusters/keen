// Text nodes keep evidence inert. Bound both scanning and additional DOM nodes.
export const MAX_HIGHLIGHT_CHARS = 100000;
const MAX_TOKENS = 4000;
export function renderJson(element, text) {
  text = String(text ?? '');
  if (text.length > MAX_HIGHLIGHT_CHARS) {
    element.textContent = text;
    return;
  }
  const doc = element.ownerDocument;
  const fragment = doc.createDocumentFragment();
  // Scan strings once, including escapes; avoid repeated searches on malformed text.
  const scalar = /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/y;
  function* tokens() {
    for (let i = 0; i < text.length;) {
      const start = i;
      if (text[i] === '"') {
        i++;
        while (i < text.length && text[i] !== '"') {
          i += text[i] === '\\' ? 2 : 1;
        }
        i = Math.min(text.length, i + 1);
        yield {0: text.slice(start, i), index: start};
      } else {
        scalar.lastIndex = i;
        const match = scalar.exec(text);
        if (match) { i = scalar.lastIndex; yield match; }
        else i++;
      }
    }
  }
  let end = 0, count = 0;
  for (const match of tokens()) {
    if (++count > MAX_TOKENS) {
      element.textContent = text;
      return;
    }
    fragment.append(doc.createTextNode(text.slice(end, match.index)));
    const span = doc.createElement('span');
    const token = match[0];
    let kind = 'literal';
    if (token.startsWith('"')) {
      let next = match.index + token.length;
      while (next < text.length && /\s/.test(text[next])) next++;
      kind = text[next] === ':' ? 'key' : 'string';
    } else if (/^-?\d/.test(token)) kind = 'number';
    span.className = 'json-' + kind;
    span.textContent = token;
    fragment.append(span);
    end = match.index + token.length;
  }
  fragment.append(doc.createTextNode(text.slice(end)));
  element.replaceChildren(fragment);
}
