// Evidence and audit PDFs are drawn by html2canvas 1.4, which stops on colour
// functions newer than rgb() and hsl(). Captures give the copy it draws from
// colours in rgba() form first.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// What browsers report for colours written with newer CSS colour syntax.
const BADGE = 'oklch(0.403512 0.120949 252.068)';
const MIXED = 'color(srgb 1 0.5 0.5)';

// A two-dimensional context that reads a few colours, as a canvas would draw them.
function fakeCanvas(window, drawn) {
  const pixels = {
    [BADGE]: [10, 74, 140, 255],
    [MIXED]: [255, 128, 128, 128],
    '#010203': [1, 2, 3, 255],
  };
  window.HTMLCanvasElement.prototype.getContext = function () {
    let fill = '#000000',
      painted = [0, 0, 0, 0];
    return {
      get fillStyle() {
        return fill;
      },
      set fillStyle(value) {
        if (value in pixels) fill = value;
      },
      clearRect() {
        painted = [0, 0, 0, 0];
      },
      fillRect() {
        drawn.push(fill);
        painted = pixels[fill];
      },
      getImageData: () => ({ data: painted }),
    };
  };
}

async function page(t, html) {
  const drawn = [];
  const ui = await loadUi('pages/evidence-export/pdf.js', {
    html,
    setup(window) {
      fakeCanvas(window, drawn);
      // jsdom keeps no colours it does not know; report what a browser would.
      const computed = window.getComputedStyle.bind(window);
      window.getComputedStyle = (el) => {
        const style = computed(el);
        const reported = JSON.parse(el.dataset?.computed || '{}');
        return {
          getPropertyValue: (name) => reported[name] ?? style.getPropertyValue(name),
        };
      };
    },
  });
  t.after(() => ui.close());
  return { ...ui, drawn };
}

test('newer colour functions are replaced, everything else is kept', async (t) => {
  const { api } = await page(t, '');
  const mark = (colour) => `<${colour}>`;
  for (const [value, expected] of [
    [BADGE, `<${BADGE}>`],
    ['rgb(1, 2, 3)', 'rgb(1, 2, 3)'],
    ['rgba(0, 0, 0, 0) none repeat scroll 0% 0%', 'rgba(0, 0, 0, 0) none repeat scroll 0% 0%'],
    [
      `${MIXED} 0px 2px 4px 0px, rgba(0, 0, 0, 0.1) 0px 0px 1px 0px`,
      `<${MIXED}> 0px 2px 4px 0px, rgba(0, 0, 0, 0.1) 0px 0px 1px 0px`,
    ],
    [
      'linear-gradient(oklab(0.5 0.1 0.1), lab(50 20 30) 40%), url("x(color(y).png")',
      'linear-gradient(<oklab(0.5 0.1 0.1)>, <lab(50 20 30)> 40%), url("x(color(y).png")',
    ],
    [
      'color-mix(in srgb, oklch(0.5 0.1 20) 50%, white)',
      '<color-mix(in srgb, oklch(0.5 0.1 20) 50%, white)>',
    ],
    ['LCH(50 30 20) hwb(120 10% 20%)', '<LCH(50 30 20)> <hwb(120 10% 20%)>'],
    // Names that only end in a colour function, and unfinished functions, stay.
    ['background-color(x) mycolor(1)', 'background-color(x) mycolor(1)'],
    ['oklch(0.4 0.1', 'oklch(0.4 0.1'],
    ["url('a\\'b(') oklch(1 0 0)", "url('a\\'b(') <oklch(1 0 0)>"],
  ])
    assert.equal(api.drawableColours(value, mark), expected, value);
  // A colour the converter cannot read is left as it was.
  assert.equal(
    api.drawableColours(BADGE, () => null),
    BADGE,
  );
});

test('a capture gives the copy it draws colours html2canvas can read', async (t) => {
  const reported = (styles) => JSON.stringify(styles).replaceAll('"', '&quot;');
  const { api, window, document, drawn } = await page(
    t,
    `<div id="copy" data-computed="${reported({ 'background-color': MIXED })}">
       <span class="badge-source" data-computed="${reported({
         color: BADGE,
         'border-top-color': BADGE,
         'text-decoration-color': BADGE,
       })}">github</span>
       <span id="plain" data-computed="${reported({ color: 'rgb(1, 2, 3)' })}">plain</span>
       <svg id="chart" data-computed="${reported({ color: 'lab(1 2 3)' })}"></svg>
     </div>`,
  );
  document.body.dataset.computed = JSON.stringify({ 'background-color': BADGE });
  let options;
  window.html2canvas = async (element, given) => {
    options = given;
    // html2canvas hands over its copy of the page; here, the page itself.
    await given.onclone(document, element);
    return { width: 10, height: 10 };
  };

  const canvas = await api.captureCanvas(document.getElementById('copy'), { scale: 2 });

  assert.deepEqual({ ...canvas }, { width: 10, height: 10 });
  assert.equal(options.scale, 2);
  const badge = document.querySelector('.badge-source');
  for (const property of ['color', 'border-top-color', 'text-decoration-color']) {
    assert.equal(badge.style.getPropertyValue(property), 'rgb(10, 74, 140)', property);
    assert.equal(badge.style.getPropertyPriority(property), 'important');
  }
  // Half-transparent colours keep their transparency.
  assert.equal(
    document.getElementById('copy').style.getPropertyValue('background-color'),
    'rgba(255, 128, 128, 0.502)',
  );
  // The page's own background is read by html2canvas too.
  assert.equal(document.body.style.getPropertyValue('background-color'), 'rgb(10, 74, 140)');
  assert.equal(document.getElementById('plain').getAttribute('style'), null);
  // A colour the canvas cannot draw is left as it was.
  assert.equal(document.getElementById('chart').style.getPropertyValue('color'), '');
  // Each colour is drawn once, however often it appears.
  assert.deepEqual(drawn, [BADGE, MIXED]);
});

test('a capture without newer colours draws no conversion canvas', async (t) => {
  const { api, window, document } = await page(t, '<div id="copy"><b>plain</b></div>');
  let contexts = 0;
  const original = window.HTMLCanvasElement.prototype.getContext;
  window.HTMLCanvasElement.prototype.getContext = function (...args) {
    contexts++;
    return original.apply(this, args);
  };
  window.html2canvas = async (element, given) => {
    await given.onclone(document, element);
    return { width: 1, height: 1 };
  };
  await api.captureCanvas(document.getElementById('copy'));
  assert.equal(contexts, 0);
  delete window.html2canvas;
  await assert.rejects(
    api.captureCanvas(document.getElementById('copy')),
    /html2canvas is not available/,
  );
});
