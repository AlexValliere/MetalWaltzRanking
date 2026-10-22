import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../site/app.js', import.meta.url), 'utf8');
const {
  rankingRouteFromHash,
  normalizeSearch,
  hasName,
  filterRows,
  pageRows,
  pageForRank,
  safeSnapshotPath,
  rowsToCsv,
  validateIndex,
  validateSnapshot,
} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const rows = [
  { rank: 1, name: 'Ａｌｅｘ', grandeur: 900, nameStatus: 'resolved' },
  { rank: 2, name: 'タンク少女', grandeur: 800, nameStatus: 'resolved' },
  { rank: 3, name: 'Café', grandeur: 700, nameStatus: 'resolved' },
  { rank: 4, name: null, grandeur: 600, nameStatus: 'unavailable' },
];

test('ranking routes preserve encoded searches while native anchors stay outside the router', () => {
  for (const hash of ['#about', '#standings', '', '#about?server=Bonn', '#q=Paladin']) {
    assert.equal(rankingRouteFromHash(hash), null, hash);
  }
  assert.deepEqual(rankingRouteFromHash('#server=YaleIOS&q=Paladin'), {
    key: 'YaleIOS',
    query: 'Paladin',
  });
  assert.deepEqual(rankingRouteFromHash('#server=JP&q=%E5%B0%91%E5%A5%B3%20%26%20%E9%87%91'), {
    key: 'JP',
    query: '少女 & 金',
  });
  assert.deepEqual(rankingRouteFromHash('#server=KOREA'), { key: 'KOREA', query: '' });
  assert.equal(rankingRouteFromHash(`#server=Bonn&q=${'x'.repeat(600)}`).query.length, 512);
});

test('search normalizes Unicode width, canonical accents and letter case', () => {
  assert.equal(normalizeSearch('ＡＬＥＸ'), 'alex');
  assert.deepEqual(filterRows(rows, 'alex'), [rows[0]]);
  assert.deepEqual(filterRows(rows, '少女'), [rows[1]]);
  assert.deepEqual(filterRows(rows, 'CAFE\u0301'), [rows[2]]);
  assert.deepEqual(filterRows(rows, 'missing'), []);
  assert.equal(filterRows(rows, '  '), rows);
  assert.equal(hasName({ name: '  ' }), false);
  assert.equal(hasName(rows[3]), false);
});

test('pagination preserves server ranks and clamps bounds', () => {
  const many = Array.from({ length: 251 }, (_, i) => ({
    rank: i + 1,
    name: `Commander ${i}`,
    grandeur: 1000 - i,
    nameStatus: 'resolved',
  }));
  assert.equal(pageRows(many, 1).rows[0].rank, 101);
  assert.equal(pageRows(many, 999).rows.length, 51);
  assert.equal(pageRows(many, -4).page, 0);
  assert.equal(pageForRank(many, 201), 2);
  assert.equal(pageForRank(many, 252), -1);
  assert.equal(pageForRank(many, 1.5), -1);
  assert.deepEqual(pageRows([], 9), { rows: [], page: 0, pages: 1, start: 0 });
  assert.throws(() => pageRows(many, 0, 1));
});

test('snapshot fetch paths cannot escape the local data directory', () => {
  assert.equal(safeSnapshotPath('Bonn.rankings.json'), './data/Bonn.rankings.json');
  for (const path of [
    '../secret.json',
    'foo/rows.json',
    'https://example.com/a.json',
    '//example.com/a.json',
    'a\\b.json',
    '%2e%2e.json',
    'file.json?q=x',
    'data.json#x',
    '..json',
    '.hidden.json',
    'rows.html',
    null,
  ]) {
    assert.throws(() => safeSnapshotPath(path), String(path));
  }
});

test('CSV preserves names, quotes cells, and neutralizes formula-like names', () => {
  const csv = rowsToCsv([
    ...rows,
    { rank: 5, name: '=HYPERLINK("https://example.com")', grandeur: 500, nameStatus: 'resolved' },
    { rank: 6, name: '  +cmd', grandeur: 400, nameStatus: 'resolved' },
    { rank: 7, name: '@formula', grandeur: 300, nameStatus: 'resolved' },
    { rank: 8, name: 'comma, quote"\nnewline', grandeur: 200, nameStatus: 'resolved' },
  ]);
  assert.ok(csv.startsWith('\uFEFFrank,commander,grandeur,name_status\r\n'));
  assert.ok(csv.includes('"4","","600","unavailable"'));
  assert.ok(csv.includes('"\'=HYPERLINK(""https://example.com"")"'));
  assert.ok(csv.includes('"\'  +cmd"'));
  assert.ok(csv.includes('"\'@formula"'));
  assert.ok(csv.includes('"comma, quote""\nnewline"'));
  assert.ok(csv.includes('"タンク少女"'));
});

const server = {
  key: 'Bonn',
  name: 'Europe №1',
  aliases: ['Bonn'],
  file: 'Bonn.rankings.json',
  rows: 4,
  namesResolved: 3,
  namesMissing: 1,
  startedAtUtc: '2026-10-22T19:00:00Z',
  completedAtUtc: '2026-10-22T19:01:00Z',
  refreshStatus: 'seed',
  sha256: '0'.repeat(64),
};
const index = {
  schema: 'metal-waltz.rankings-index.v1',
  generatedAtUtc: '2026-10-22T20:00:00Z',
  servers: [server],
};
const snapshot = {
  schema: 'maiden-waltz.public-ranking-snapshot.v1',
  serverKey: 'Bonn',
  fund: 5,
  startedAtUtc: server.startedAtUtc,
  completedAtUtc: server.completedAtUtc,
  rankingEnumerationComplete: true,
  namesComplete: false,
  rows,
};

test('manifest and snapshot validation catch mixed or misleading data', () => {
  assert.equal(validateIndex(index), index);
  assert.equal(validateSnapshot(snapshot, server), snapshot);
  assert.throws(() => validateIndex({ ...index, servers: [server, server] }));
  assert.throws(() => validateIndex({ ...index, servers: [{ ...server, namesMissing: 2 }] }));
  assert.throws(() =>
    validateIndex({ ...index, servers: [{ ...server, file: '../private.json' }] }),
  );
  assert.throws(() => validateSnapshot({ ...snapshot, serverKey: 'KOREA' }, server));
  assert.throws(() => validateSnapshot({ ...snapshot, rows: [...rows].reverse() }, server));
  assert.throws(() => validateSnapshot(snapshot, { ...server, namesResolved: 4 }));
});

test('an older snapshot with matching row counts cannot inherit a newer index timestamp', () => {
  assert.throws(() =>
    validateSnapshot({ ...snapshot, startedAtUtc: '2026-10-21T19:00:00Z' }, server),
  );
  assert.throws(() =>
    validateSnapshot({ ...snapshot, completedAtUtc: '2026-10-21T19:01:00Z' }, server),
  );
  assert.throws(() =>
    validateIndex({
      ...index,
      servers: [{ ...server, startedAtUtc: '2026-10-23T19:00:00Z' }],
    }),
  );
});

test('only complete PvP snapshots with consistent name flags and server score order are shown', () => {
  for (const change of [
    { fund: 4 },
    { rankingEnumerationComplete: false },
    { namesComplete: true },
  ])
    assert.throws(() => validateSnapshot({ ...snapshot, ...change }, server));
  for (const [index, change] of [
    [0, { nameStatus: 'unavailable' }],
    [3, { nameStatus: 'resolved' }],
    [3, { nameStatus: 'unknown' }],
    [1, { grandeur: 901 }],
  ]) {
    const changed = rows.map((row, i) => (i === index ? { ...row, ...change } : row));
    assert.throws(() => validateSnapshot({ ...snapshot, rows: changed }, server));
  }
  const tied = rows.map((row, i) => (i === 1 ? { ...row, grandeur: 900 } : row));
  assert.equal(validateSnapshot({ ...snapshot, rows: tied }, server).rows, tied);
});
