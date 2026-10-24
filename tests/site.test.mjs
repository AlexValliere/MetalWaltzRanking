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
  serverDescription,
  displayServerName,
  browserApp,
} = await import(`data:text/javascript;base64,${Buffer.from(`${source}\nexport { browserApp };`).toString('base64')}`);
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
  assert.equal(hasName({ name: '  ' }), true);
  assert.equal(hasName({ name: '' }), false);
  assert.equal(hasName({}), false);
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

test('a resolved ideographic-space name keeps its exact value and resolved count', () => {
  const nativeRow = { rank: 1, name: '\u3000', grandeur: 900, nameStatus: 'resolved' };
  const nativeServer = { ...server, rows: 1, namesResolved: 1, namesMissing: 0 };
  const nativeSnapshot = { ...snapshot, namesComplete: true, rows: [nativeRow] };
  assert.equal(hasName(nativeRow), true);
  assert.equal(validateSnapshot(nativeSnapshot, nativeServer), nativeSnapshot);
  assert.equal(filterRows(nativeSnapshot.rows, ''), nativeSnapshot.rows);
  assert.deepEqual(filterRows(nativeSnapshot.rows, 'missing'), []);
  assert.equal(rowsToCsv(nativeSnapshot.rows),
    '\uFEFFrank,commander,grandeur,name_status\r\n"1","\u3000","900","resolved"\r\n');
  for (const name of ['', null, undefined]) {
    assert.equal(hasName({ name }), false);
    assert.throws(() => validateSnapshot({
      ...nativeSnapshot, rows: [{ ...nativeRow, name }],
    }, nativeServer));
  }
});

const unavailableServer = {
  key: 'SteamBonn',
  name: 'Steam Europe',
  aliases: ['SteamBonn'],
  file: null,
  rows: 0,
  namesResolved: 0,
  namesMissing: 0,
  startedAtUtc: null,
  completedAtUtc: null,
  refreshStatus: 'failed',
  errorCode: 'snapshot_unavailable',
  sha256: null,
};
const unavailableSteamAsia = {
  ...unavailableServer,
  key: 'SteamJP',
  name: 'Steam / Asia / Steam / Asia (Hong Kong)',
  aliases: ['SteamJP', 'SteamTEST'],
};

test('Steam Asia has a concise display name without changing native server metadata', () => {
  const shared = Object.freeze({ ...unavailableSteamAsia });
  assert.equal(displayServerName(shared), 'Steam Asia / Hong Kong');
  assert.equal(shared.name, 'Steam / Asia / Steam / Asia (Hong Kong)');
  for (const native of [server, unavailableServer, { key: 'JP', name: 'N°1 イジェフスク' }])
    assert.equal(displayServerName(native), native.name);
});

test('shared Steam and mobile backends describe their verified regional aliases separately', () => {
  assert.equal(serverDescription({ key: 'SteamJP', aliases: ['SteamJP', 'SteamTEST'] }),
    'One shared Steam leaderboard for Asia and Hong Kong.');
  assert.equal(serverDescription({ key: 'JKMIX', aliases: ['JKMIX', 'MIXKJ'] }),
    'One shared leaderboard for Japan №2 and Korea №2.');
  for (const key of ['Bonn', 'SteamBonn', 'SteamUSAVA', 'future'])
    assert.equal(serverDescription({ key, aliases: [key, 'alternate'] }),
      'PvP standings, ordered by Grandeur.');
});

test('a backend without a snapshot requires explicit failure and entirely absent snapshot metadata', () => {
  const unavailableIndex = { ...index, servers: [server, unavailableServer] };
  assert.equal(validateIndex(unavailableIndex), unavailableIndex);
  for (const change of [
    { refreshStatus: 'ok' }, { refreshStatus: 'seed' },
    { rows: 1, namesMissing: 1 }, { rows: 1, namesResolved: 1 },
    { file: 'SteamBonn.rankings.json' }, { file: undefined },
    { sha256: '0'.repeat(64) }, { sha256: undefined },
    { startedAtUtc: server.startedAtUtc }, { startedAtUtc: undefined },
    { completedAtUtc: server.completedAtUtc }, { completedAtUtc: undefined },
  ]) assert.throws(() => validateIndex({
    ...index, servers: [{ ...unavailableServer, ...change }],
  }), JSON.stringify(change));
  // A successfully collected empty leaderboard remains a real snapshot.
  assert.doesNotThrow(() => validateIndex({
    ...index, servers: [{ ...server, rows: 0, namesResolved: 0, namesMissing: 0 }],
  }));
});

test('an unavailable backend reports its state without requesting a null snapshot path', async () => {
  const unavailableIndex = { ...index, servers: [server, unavailableServer] };
  let requests = 0;
  await assert.rejects(fetchServerSnapshot(unavailableIndex, unavailableServer.key, async () => {
    requests++;
    throw new Error('No snapshot fetch should occur');
  }), (error) => {
    assert.match(error.message, /No complete ranking snapshot.*Steam Europe/);
    assert.equal(error.server, unavailableServer);
    assert.equal(error.index, unavailableIndex);
    return true;
  });
  assert.equal(requests, 0);
  const paths = [];
  await assert.rejects(fetchServerSnapshot(index, unavailableServer.key, async (path) => {
    paths.push(path);
    return unavailableIndex;
  }, { refreshIndex: true }), /No complete ranking snapshot/);
  assert.deepEqual(paths, ['./data/index.json']);
});

test('the unavailable Steam Asia error uses its display name and preserves the source entry', async () => {
  const unavailableIndex = { ...index, servers: [unavailableSteamAsia] };
  await assert.rejects(fetchServerSnapshot(unavailableIndex, 'SteamJP', async () => {
    assert.fail('An unavailable snapshot must not be requested');
  }), (error) => {
    assert.equal(error.message, 'No complete ranking snapshot is available for Steam Asia / Hong Kong yet.');
    assert.equal(error.server, unavailableSteamAsia);
    assert.equal(error.server.name, 'Steam / Asia / Steam / Asia (Hong Kong)');
    return true;
  });
});

for (const unavailable of [unavailableServer, unavailableSteamAsia]) {
  const displayName = unavailable.key === 'SteamJP' ? 'Steam Asia / Hong Kong' : 'Steam Europe';
  test(`selecting uncollected ${unavailable.key} clears prior standings and can load its first snapshot later`, async () => {
    class Element {
      constructor(tagName = 'div') {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.attributes = new Map();
        this.listeners = new Map();
        this.dataset = {};
        this.value = '';
        this.text = '';
        this.classList = { toggle() {} };
      }
      set textContent(text) { this.text = String(text); this.children = []; }
      get textContent() { return this.text + this.children.map((child) => child.textContent).join(''); }
      append(...children) {
        for (const child of children)
          this.children.push(...(child.tagName === '#FRAGMENT' ? child.children : [child]));
      }
      replaceChildren(...children) { this.text = ''; this.children = []; this.append(...children); }
      setAttribute(key, value) { this.attributes.set(key, String(value)); }
      getAttribute(key) { return this.attributes.get(key) ?? null; }
      removeAttribute(key) { this.attributes.delete(key); delete this[key]; }
      addEventListener(type, listener) { this.listeners.set(type, listener); }
      async dispatch(type) { await this.listeners.get(type)?.({ target: this }); }
      querySelector() { return this.button ??= new Element('button'); }
    }
    const elements = new Map();
    const element = (id) => {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    };
    const location = { hash: '#server=Bonn', pathname: '/', search: '' };
    const document = {
      getElementById: element,
      createElement: (tag) => new Element(tag),
      createDocumentFragment: () => new Element('#fragment'),
      createTextNode: (text) => { const node = new Element('#text'); node.textContent = text; return node; },
      addEventListener() {},
    };
    const loadedSnapshot = {
      ...snapshot,
      rows: rows.map((row) => row.rank === 3 ? { ...row, name: '\u3000' } : row),
    };
    let currentIndex = { ...index, servers: [server, unavailable] };
    let failNextSnapshot = false;
    let rejectSnapshot;
    const paths = [];
    const replacements = {
      document, location,
      window: { addEventListener() {} },
      history: { replaceState(_state, _title, url) { location.hash = url.slice(url.indexOf('#')); } },
      fetch: async (path) => {
        paths.push(path);
        if (failNextSnapshot && path === './data/Bonn.rankings.json')
          return new Promise((_resolve, reject) => { rejectSnapshot = reject; });
        const value = path === './data/index.json' ? currentIndex :
          path === './assets/vehicles/catalog.json' ? {
            schema: 'metal-waltz.vehicle-art.v1', models: {}, units: {},
          } : path === './data/Bonn.rankings.json' ? loadedSnapshot :
            path === `./data/${unavailable.key}.rankings.json` ? { ...loadedSnapshot, serverKey: unavailable.key } : null;
        assert.notEqual(value, null, `Unexpected request: ${path}`);
        return { ok: true, json: async () => value };
      },
    };
    const previous = Object.fromEntries(Object.keys(replacements).map((key) => [
      key, Object.getOwnPropertyDescriptor(globalThis, key),
    ]));
    try {
      for (const [key, value] of Object.entries(replacements))
        Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
      await browserApp();
      assert.equal(element('entries-count').textContent, '4');
      assert.equal(element('names-count').textContent, '3');
      assert.equal(element('ranking-rows').children[2].children[1].textContent, '\u3000');
      assert.equal(element('ranking-rows').children[3].children[1].textContent, 'Name unavailable');
      assert.equal(element('download-json').href, './data/Bonn.rankings.json');
      const steamTab = element('server-tabs').children.find((button) => button.dataset.key === unavailable.key);
      assert.equal(steamTab.textContent, `${displayName}Not collected`);
      assert.equal(steamTab.getAttribute('aria-label'), `${displayName}, no snapshot available`);
      const option = element('server-select').children.find((choice) => choice.value === unavailable.key);
      assert.equal(option.textContent, `${displayName} · Not collected`);
      const selecting = steamTab.dispatch('click');
      assert.equal(element('load-notice').textContent, `Loading ${displayName}… The displayed standings are still Europe №1.`);
      await selecting;
      assert.equal(element('standings-title').textContent, displayName);
      assert.equal(document.title, `${displayName} · Metal Waltz Rankings`);
      assert.equal(element('table-caption').textContent, `${displayName} ranking snapshot unavailable`);
      assert.match(element('load-notice').textContent, new RegExp(`No complete ranking snapshot is available for ${displayName} yet`));
      assert.equal(element('entries-count').textContent, '—');
      assert.equal(element('names-count').textContent, '—');
      assert.equal(element('snapshot-date').textContent, '—');
      assert.equal(element('results-label').textContent, 'No snapshot available');
      assert.match(element('ranking-rows').textContent, /not been collected/);
      assert.doesNotMatch(element('ranking-rows').textContent, /Ａｌｅｘ/);
      assert.equal(element('download-json').hidden, true);
      assert.equal(element('download-json').href, undefined);
      for (const id of ['commander-search', 'page-size', 'rank-number', 'download-csv', 'next-page'])
        assert.equal(element(id).disabled, true, id);
      assert.equal(steamTab.getAttribute('aria-current'), 'true');
      assert.equal(location.hash, `#server=${unavailable.key}`);
      assert.deepEqual(paths, [
        './data/index.json', './assets/vehicles/catalog.json', './data/Bonn.rankings.json',
      ]);

      currentIndex = { ...index, servers: [server, {
        ...server, key: unavailable.key, name: unavailable.name, aliases: unavailable.aliases,
        file: `${unavailable.key}.rankings.json`, refreshStatus: 'ok',
      }] };
      const checkAgain = element('load-notice').children.find((child) => child.tagName === 'BUTTON');
      await checkAgain.dispatch('click');
      assert.equal(element('standings-title').textContent, displayName);
      assert.equal(document.title, `${displayName} · Metal Waltz Rankings`);
      assert.ok(element('table-caption').textContent.startsWith(`${displayName} PvP standings,`));
      assert.equal(currentIndex.servers[1].name, unavailable.name);
      assert.equal(element('entries-count').textContent, '4');
      assert.equal(element('download-json').hidden, false);
      assert.equal(element('download-json').href, `./data/${unavailable.key}.rankings.json`);
      assert.equal(element('commander-search').disabled, false);
      assert.deepEqual(paths.slice(-2), ['./data/index.json', `./data/${unavailable.key}.rankings.json`]);

      failNextSnapshot = true;
      const bonnTab = element('server-tabs').children.find((button) => button.dataset.key === 'Bonn');
      const loading = bonnTab.dispatch('click');
      assert.equal(element('load-notice').textContent, `Loading Europe №1… The displayed standings are still ${displayName}.`);
      rejectSnapshot(new Error('Fixture snapshot request failed'));
      await loading;
      assert.match(element('error-notice').textContent, new RegExp(`Showing the last loaded ${displayName} snapshot`));
      assert.equal(element('standings-title').textContent, displayName);
    } finally {
      for (const [key, descriptor] of Object.entries(previous)) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else delete globalThis[key];
      }
    }
  });
}

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
