// Masks e-mail and IP addresses in text that leaves Keen in a sample export.
// Evidence and audit exports share these rules. They follow mask_event_data_str
// in services/api/app/security/redaction.py, so a capture made in the browser
// hides what a server-side sample export hides.

function isValidIpv4Literal(value) {
  return /^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/.test(
    value,
  );
}

// Reads an IPv6 address as Python's ipaddress module does: eight groups of one
// to four hexadecimal digits, where "::" stands for one or more zero groups and
// an IPv4 address may take the place of the last two.
function isIpv6Address(text) {
  const halves = text.split('::');
  if (halves.length > 2) return false;
  const groups = halves.flatMap((half) => (half ? half.split(':') : []));
  let count = groups.length;
  const last = halves[halves.length - 1].split(':').pop();
  if (last.includes('.')) {
    if (!isValidIpv4Literal(last)) return false;
    groups.pop();
    count += 1;
  }
  if (!groups.every((group) => /^[0-9A-Fa-f]{1,4}$/.test(group))) return false;
  return halves.length === 2 ? count < 8 : count === 8;
}

// A mailbox as address headers write it: a display name before the address in
// angle brackets. The name identifies the person as much as the address does,
// so the whole mailbox is masked. A name is up to five words of letters, which
// may carry apostrophes, dots and hyphens, or one quoted string; every
// repetition is bounded.
const NAME_WORD = "\\p{L}{1,40}(?:['.-]\\p{L}{1,40}){0,3}\\.?";
const MAILBOX = new RegExp(
  `(?:(^|[^\\w"'.-])("[^"\\n]{1,100}"|${NAME_WORD}(?:[ \\t]+${NAME_WORD}){0,4})[ \\t]*)?` +
    '<[ \\t]*[A-Za-z0-9._%+-]{1,128}@[A-Za-z0-9.-]+\\.[A-Za-z]{2,63}[ \\t]*>',
  'giu',
);

export function maskEventDataString(value) {
  if (value == null) return value;
  let txt = String(value);
  txt = txt.replace(MAILBOX, (match, prefix) => `${prefix || ''}[MASKED_EMAIL]`);
  // A full stop after an address ends the sentence; ".<letter or digit>"
  // continues the address.
  txt = txt.replace(
    /(^|[^A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,128})@([A-Za-z0-9.-]+\.[A-Za-z]{2,63})(?![A-Za-z0-9_%+-]|\.[A-Za-z0-9])/g,
    '$1[MASKED_EMAIL]',
  );
  // Bounded repetitions keep the scan linear on long runs of colons, dots and
  // digits. The longest IPv6 address, with its punctuation, fits within them.
  txt = txt.replace(
    /(^|[^\w:.])((?=[A-Fa-f0-9.]{0,63}:)[A-Fa-f0-9:.]{2,64}(?:%[A-Za-z0-9_.-]{1,32})?)(?![\w:])/g,
    (match, prefix, candidate) => {
      // Punctuation after an address stays visible, as in "from fe80::1." and
      // "address 2001:db8::1: not known".
      let raw = candidate.replace(/\.+$/, '');
      if (/[^:]:$/.test(raw)) raw = raw.slice(0, -1);
      if (!isIpv6Address(raw.split('%', 1)[0])) return match;
      return `${prefix}[MASKED_IP]${candidate.slice(raw.length)}`;
    },
  );
  txt = txt.replace(
    /(^|[^\w.])((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})(?!\w|\.\d)/g,
    '$1[MASKED_IP]',
  );
  return txt;
}

const MASKED_ATTRIBUTES = ['title', 'alt', 'aria-label', 'placeholder'];

// Masks the text, form values and descriptive attributes under root.
export function maskDomText(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(node.parentElement?.tagName)
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT;
    },
  });
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  for (const node of textNodes) node.nodeValue = maskEventDataString(node.nodeValue);
  root.querySelectorAll('input, textarea').forEach((el) => {
    el.value = maskEventDataString(el.value);
  });
  root.querySelectorAll(MASKED_ATTRIBUTES.map((attr) => `[${attr}]`).join(', ')).forEach((el) => {
    for (const attr of MASKED_ATTRIBUTES) {
      if (el.hasAttribute(attr)) el.setAttribute(attr, maskEventDataString(el.getAttribute(attr)));
    }
  });
}
