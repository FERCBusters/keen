// Builds Help's search index from the Help pages, so the two cannot drift.
//
// Every chapter is read in the order its handbook lists it. Each h2 or h3 with
// an id starts a section that runs to the next one, and the section's entry
// holds its heading, its handbook, its address and its words.
//
//   bash build-help-index.sh
//
// writes public/help/search-index.js; help-site.test.mjs fails when the file
// on disk is not what the pages produce.
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const helpRoot = new URL('../../public/help/', import.meta.url);
const BOOKS = [
  ['using.html', 'Using Keen'],
  ['administrator.html', 'Administering Keen'],
  ['developer.html', 'Developing Keen'],
];

async function page(name) {
  return new JSDOM(await readFile(new URL(name, helpRoot), 'utf8')).window.document;
}

const startsSection = (node) =>
  node.nodeType === 1 && /^H[23]$/.test(node.tagName) && node.hasAttribute('id');

// Page furniture that says nothing about a section: the chapter pager and the
// links that open a diagram at full size.
const FURNITURE = 'nav, a[href^="diagrams/"]';

// An element's words with one space between them, whatever markup separates
// them, so words in neighbouring cells or list items never run together.
function words(node) {
  const walker = node.ownerDocument.createTreeWalker(node, 4);
  const parts = [];
  while (walker.nextNode())
    if (!walker.currentNode.parentElement.closest(FURNITURE))
      parts.push(walker.currentNode.nodeValue);
  return parts.join(' ').split(/\s+/).filter(Boolean).join(' ');
}

export async function buildHelpIndex() {
  const index = [];
  for (const [bookPage, book] of BOOKS) {
    const chapters = [...(await page(bookPage)).querySelectorAll('.chapter-cards a[href]')].map(
      (link) => link.getAttribute('href'),
    );
    for (const chapter of chapters) {
      const document = await page(chapter);
      for (const heading of [...document.querySelectorAll('main h2[id], main h3[id]')]) {
        const text = [];
        for (let node = heading.nextSibling; node && !startsSection(node); node = node.nextSibling)
          text.push(words(node));
        index.push({
          title: words(heading),
          book,
          url: `${chapter}#${heading.id}`,
          text: text.filter(Boolean).join(' '),
        });
      }
    }
  }
  return index;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const index = await buildHelpIndex();
  await writeFile(
    new URL('search-index.js', helpRoot),
    `window.KEEN_DOC_INDEX = ${JSON.stringify(index, null, 2)};\n`,
  );
  console.log(`Wrote ${index.length} sections to search-index.js.`);
}
