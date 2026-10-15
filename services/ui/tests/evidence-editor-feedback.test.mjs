import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../public/pages/evidence-config.js', import.meta.url), 'utf8');
function segment(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}

test('success feedback expires and a previous timer cannot hide a newer error', () => {
  const timers = new Map();
  let sequence = 0;
  const element = { setAttribute() {} };
  const context = vm.createContext({
    $: () => element,
    setTimeout(fn, delay) {
      timers.set(++sequence, { fn, delay });
      return sequence;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  });
  vm.runInContext(segment('let statusTimer', 'const conditions'), context);
  vm.runInContext("status('Saved', 'success')", context);
  assert.equal(element.hidden, false);
  assert.equal(timers.get(1).delay, 6000);
  timers.get(1).fn();
  assert.equal(element.hidden, true);
  vm.runInContext("status('Saved again', 'success'); status('Failure')", context);
  assert.equal(timers.size, 0);
  assert.equal(element.hidden, false);
  assert.equal(element.textContent, 'Failure');
});

test('backfill completion flashes once and ignores previously completed jobs', async () => {
  const flashes = [],
    queue = [];
  let result = { status: 'completed', examined: 20, matched: 4, created_mappings: 3 };
  const context = vm.createContext({
    apiRoot: '/api',
    apiGet: async () => result,
    showBackfill() {},
    status: (...args) => flashes.push(args),
    setTimeout: (fn) => queue.push(fn),
  });
  vm.runInContext(
    'let pollingJobId = null; const activeJobs = new Set(); const notifiedJobs = new Set();' +
      segment('async function pollBackfill(', 'async function loadRecentBackfill'),
    context,
  );
  await vm.runInContext("pollBackfill('old')", context);
  assert.equal(flashes.length, 0);
  await vm.runInContext("pollBackfill('new', true)", context);
  assert.equal(flashes.length, 1);
  assert.match(flashes[0][0], /checked 20 events; 4 matched, 3 new mappings/);
  await vm.runInContext("pollBackfill('new')", context);
  assert.equal(flashes.length, 1);
  result = { status: 'running' };
  await vm.runInContext("pollBackfill('running')", context);
  assert.equal(queue.length, 1);
  result = { status: 'completed', examined: 30 };
  await vm.runInContext("pollBackfill('running')", context);
  assert.equal(flashes.length, 2);
});

test('default blank summary is omitted; populated Summary serialises canonical event scope', () => {
  const row = {
    optional: true,
    fieldInputs: {
      path: { value: 'event:summary' },
      op: { value: 'contains' },
      value: { value: '' },
    },
  };
  const context = vm.createContext({ $: () => ({ children: [row] }) });
  vm.runInContext(segment('function readFieldConditions()', "$('field-add').onclick"), context);
  assert.equal(vm.runInContext('readFieldConditions().length', context), 0);
  row.fieldInputs.value.value = 'gills';
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(readFieldConditions())', context)), [
    { scope: 'event', path: ['summary'], operator: 'contains', value: 'gills' },
  ]);
  row.fieldInputs.path.value = '["$event","summary"]';
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(readFieldConditions())', context)), [
    { path: ['$event', 'summary'], operator: 'contains', value: 'gills' },
  ]);
});

test('unchanged legacy Summary regex saves without consuming a structured condition slot', () => {
  const row = {
    legacySummary: true,
    fieldInputs: {
      path: { value: 'event:summary' },
      op: { value: 'regex' },
      value: { value: '^$' },
    },
  };
  const elements = {
    'field-rows': { children: [row] },
    description: { value: 'Rule' },
    'rule-id': { value: 'r' },
    'when-source': { value: 'source' },
    confidence: { value: '0.8' },
    enabled: { checked: true },
  };
  const context = vm.createContext({
    $: (key) => elements[key],
    definitionFromForm: () => ({ section: 'source', adapter: 'source' }),
    readFieldConditions: () => [
      { scope: 'event', path: ['summary'], operator: row.fieldInputs.op.value, value: '^$' },
    ],
    conditions: [],
    regexConditions: [],
    bookstackConditions: [],
    targets: [],
    structuredClone,
  });
  vm.runInContext(segment('function readRule()', 'async function newRule('), context);
  const when = JSON.parse(vm.runInContext('JSON.stringify(readRule().when)', context));
  assert.deepEqual(when, { summary_regex: '^$', source: 'source' });
  row.fieldInputs.op.value = 'contains';
  const changed = JSON.parse(vm.runInContext('JSON.stringify(readRule().when)', context));
  assert.equal(changed.summary_regex, undefined);
  assert.equal(changed.fields[0].operator, 'contains');
});
