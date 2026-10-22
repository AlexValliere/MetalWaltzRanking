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
  fetchServerSnapshot,
  safePortraitPath,
  validateVehicleCatalog,
  publicVehicles,
  vehicleDisplayData,
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

test('retry loads a fresh index and a matching snapshot without mutating the displayed index', async () => {
  const freshServer = {
    ...server,
    startedAtUtc: '2026-10-23T19:00:00Z',
    completedAtUtc: '2026-10-23T19:01:00Z',
    refreshStatus: 'ok',
  };
  const freshIndex = { ...index, generatedAtUtc: '2026-10-23T20:00:00Z', servers: [freshServer] };
  const freshSnapshot = {
    ...snapshot,
    startedAtUtc: freshServer.startedAtUtc,
    completedAtUtc: freshServer.completedAtUtc,
  };
  const requests = [];
  const controller = new AbortController();
  const fetchData = async (path, signal) => {
    requests.push(path);
    assert.equal(signal, controller.signal);
    return path === './data/index.json' ? freshIndex : freshSnapshot;
  };
  // A deployment changed the file after this tab had loaded the earlier index.
  await assert.rejects(
    fetchServerSnapshot(index, server.key, fetchData, { signal: controller.signal }),
  );
  requests.length = 0;
  const result = await fetchServerSnapshot(index, server.key, fetchData, {
    refreshIndex: true,
    signal: controller.signal,
  });
  assert.deepEqual(requests, ['./data/index.json', './data/Bonn.rankings.json']);
  assert.equal(result.index, freshIndex);
  assert.equal(result.server, freshServer);
  assert.equal(result.snapshot, freshSnapshot);
  assert.equal(index.servers[0].completedAtUtc, '2026-10-22T19:01:00Z');

  await assert.rejects(
    fetchServerSnapshot(
      index,
      server.key,
      async (path) => (path === './data/index.json' ? freshIndex : snapshot),
      { refreshIndex: true },
    ),
  );
  assert.equal(index.servers[0], server);
});

const publicVehicle = { capsuleTypeId: 101, phase: 3, level: 80, fate: true };
const portrait = `./assets/vehicles/${'a'.repeat(64)}.png`;
const vehicleCatalog = {
  schema: 'metal-waltz.vehicle-art.v1',
  models: {
    501: { portrait, portraitKind: 'live2d-generated' },
    502: { portrait: null, portraitKind: 'unavailable' },
  },
  units: {
    101: { phases: {
      3: { name: 'T-34/57', maidenName: 'Larissa Korovina', defaultModel: 501 },
      2: { name: 'T-34', maidenName: 'Larissa Korovina', defaultModel: 502 },
    } },
  },
};

test('public vehicle states distinguish an empty observed list from unavailable and legacy data', () => {
  assert.deepEqual(publicVehicles(rows[0]), { status: 'not_collected', vehicles: [] });
  assert.deepEqual(publicVehicles({ vehiclesStatus: 'unavailable', vehicles: null }), {
    status: 'unavailable', vehicles: [],
  });
  assert.deepEqual(publicVehicles({ vehiclesStatus: 'not_collected', vehicles: null }), {
    status: 'not_collected', vehicles: [],
  });
  assert.deepEqual(publicVehicles({ vehiclesStatus: 'resolved', vehicles: [] }), {
    status: 'resolved', vehicles: [],
  });
  const vehicles = Array.from({ length: 9 }, (_, index) => ({ ...publicVehicle, level: index }));
  assert.equal(publicVehicles({ vehiclesStatus: 'resolved', vehicles }).vehicles, vehicles);
});

test('snapshot validation requires a complete, truthful public vehicle state pair', () => {
  const withVehicle = (change) => ({
    ...snapshot,
    rows: snapshot.rows.map((row, index) => index ? row : { ...row, ...change }),
  });
  for (const change of [
    { vehiclesStatus: 'resolved', vehicles: [publicVehicle] },
    { vehiclesStatus: 'resolved', vehicles: [] },
    { vehiclesStatus: 'unavailable', vehicles: null },
    { vehiclesStatus: 'not_collected', vehicles: null },
  ]) assert.doesNotThrow(() => validateSnapshot(withVehicle(change), server));
  for (const change of [
    { vehiclesStatus: 'resolved' },
    { vehicles: [] },
    { vehiclesStatus: 'resolved', vehicles: null },
    { vehiclesStatus: 'unavailable', vehicles: [] },
    { vehiclesStatus: 'not_collected', vehicles: [publicVehicle] },
    { vehiclesStatus: 'guessed', vehicles: [publicVehicle] },
  ]) assert.throws(() => validateSnapshot(withVehicle(change), server));
  for (const change of [
    { capsuleTypeId: 0 }, { capsuleTypeId: 0x100000000 }, { capsuleTypeId: 2.5 },
    { phase: -1 }, { phase: 256 }, { level: -1 }, { level: 1.2 },
    { level: 0x80000000 }, { fate: 1 },
  ]) assert.throws(() => validateSnapshot(withVehicle({
    vehiclesStatus: 'resolved', vehicles: [{ ...publicVehicle, ...change }],
  }), server));
  assert.doesNotThrow(() => validateSnapshot(withVehicle({
    vehiclesStatus: 'resolved', vehicles: [{ ...publicVehicle, phase: 0, level: null }],
  }), server));
  assert.throws(() => validateSnapshot(withVehicle({
    vehiclesStatus: 'resolved', vehicles: [publicVehicle, { ...publicVehicle, phase: 2 }],
  }), server));
  const allVehicles = Array.from({ length: 255 }, (_, i) => ({
    ...publicVehicle, capsuleTypeId: i + 1,
  }));
  assert.doesNotThrow(() => validateSnapshot(withVehicle({
    vehiclesStatus: 'resolved', vehicles: allVehicles,
  }), server));
  assert.throws(() => validateSnapshot(withVehicle({
    vehiclesStatus: 'resolved', vehicles: [...allVehicles, { ...publicVehicle, capsuleTypeId: 256 }],
  }), server));
});

test('vehicle artwork stays local and supports generated Live2D portraits without guessing an appearance', () => {
  assert.equal(safePortraitPath(portrait), portrait);
  for (const path of [
    'https://example.com/avatar.png', '//example.com/avatar.png',
    './assets/vehicles/../secret.png', './assets/vehicles/avatar.svg',
    `./assets/vehicles/${'a'.repeat(64)}.png?x=1`,
    `./assets/vehicles/${'a'.repeat(64)}.png#x`, null,
  ]) assert.throws(() => safePortraitPath(path));
  assert.equal(validateVehicleCatalog(vehicleCatalog), vehicleCatalog);
  assert.deepEqual(vehicleDisplayData(publicVehicle, vehicleCatalog), {
    name: 'T-34/57', maidenName: 'Larissa Korovina', portrait,
    portraitKind: 'live2d-generated', level: 80, fate: true,
  });
  assert.equal(vehicleDisplayData({ ...publicVehicle, phase: 2 }, vehicleCatalog).portrait, null);
  const unknownPhase = vehicleDisplayData({ ...publicVehicle, phase: 0 }, vehicleCatalog);
  assert.equal(unknownPhase.name, 'Unidentified vehicle');
  assert.equal(unknownPhase.portrait, null);
  assert.equal(vehicleDisplayData(publicVehicle, null).portrait, null);
  for (const change of [
    { models: { 501: { portrait: 'https://example.com/a.png', portraitKind: 'native-square' } } },
    { models: { 501: { portrait: null, portraitKind: 'live2d-generated' } } },
    { models: { 501: { portrait, portraitKind: 'guessed' } } },
    { units: { 101: { phases: { 256: { name: 'Unknown', maidenName: '', defaultModel: 501 } } } } },
  ]) assert.throws(() => validateVehicleCatalog({ ...vehicleCatalog, ...change }));
});

test('name search, rank paging and CSV keep their behavior when public vehicle details are present', () => {
  const enriched = rows.map((row) => ({
    ...row, vehiclesStatus: 'resolved', vehicles: [publicVehicle],
  }));
  assert.deepEqual(filterRows(enriched, 'alex'), [enriched[0]]);
  assert.equal(pageForRank(enriched, 4), 0);
  assert.equal(rowsToCsv(enriched), rowsToCsv(rows));
});
