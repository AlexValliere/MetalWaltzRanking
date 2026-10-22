import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { recipe, size } = require('../scripts/build_vehicle_assets.cjs');
const root = new URL('../', import.meta.url);
const load = async (file) => JSON.parse(await readFile(new URL(file, root), 'utf8'));
const catalog = await load('site/assets/vehicles/catalog.json');
const manifest = await load('docs/research/vehicle-art-build.json');
const proof = await load('docs/research/vehicle-source-proof.json');
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

test('vehicle art preserves verified type/generation identities and source pins', () => {
  assert.equal(catalog.schema, 'metal-waltz.vehicle-art.v1');
  assert.equal(catalog.dataCommit, 'fee198aee089d57ab4184f4ab0cad5f3ccf67e35');
  assert.equal(catalog.dataCommit, manifest.dataCommit);
  assert.equal(catalog.dataCommit, proof.dataCommit);
  assert.equal(manifest.source.catalogSha256, proof.catalogSha256);
  assert.equal(Object.keys(catalog.units).length, proof.units);
  let phases = 0;
  for (const [type, unit] of Object.entries(catalog.units)) {
    assert.match(type, /^[1-9]\d*$/);
    for (const [phase, definition] of Object.entries(unit.phases)) {
      assert.match(phase, /^[1-3]$/);
      assert.ok(definition.name.trim());
      assert.equal(typeof definition.maidenName, 'string');
      assert.ok(catalog.models[definition.defaultModel]);
      phases++;
    }
  }
  assert.equal(phases, proof.phases);
  // Observed public native generation 3 stays phase 3, without an offset.
  assert.ok(catalog.units['8311'].phases['3'].defaultModel);
});

test('every portrait is a bounded content-addressed PNG with exact recorded bytes', async () => {
  const used = new Set();
  for (const model of Object.values(catalog.models)) {
    assert.match(model.portraitKind, /^(native-square|live2d-generated|unavailable)$/);
    assert.ok(model.portrait, 'Current verified model coverage must remain complete');
    assert.match(model.portrait, /^\.\/assets\/vehicles\/[a-f0-9]{64}\.png$/);
    used.add(model.portrait.split('/').at(-1));
  }
  assert.deepEqual([...used].sort(), Object.keys(catalog.assets).sort());
  for (const [file, entry] of Object.entries(catalog.assets)) {
    const bytes = await readFile(new URL('site/assets/vehicles/' + file, root));
    assert.equal(file, sha256(bytes) + '.png');
    assert.equal(entry.sha256, sha256(bytes));
    assert.equal(entry.bytes, bytes.length);
    assert.deepEqual(size(bytes), { width: entry.width, height: entry.height });
  }
});

test('Live2D head portraits are real generated frames with native eye focus', () => {
  const liveModels = Object.values(catalog.models).filter((model) => model.portraitKind === 'live2d-generated');
  assert.equal(liveModels.length, proof.live2dModelRecords);
  assert.equal(Object.keys(manifest.live2d).length, 80);
  assert.equal(new Set(liveModels.map((model) => model.portrait)).size, 80);
  assert.deepEqual(manifest.recipe, recipe);
  for (const entry of Object.values(manifest.live2d)) {
    assert.ok(Number.isFinite(entry.focus.x) && Number.isFinite(entry.focus.y));
    assert.ok(entry.focus.zoom >= 2 && entry.focus.zoom <= 6);
    assert.equal(entry.frame.left, 0.5 - entry.focus.x * entry.focus.zoom);
    assert.equal(entry.frame.top, 0.4 - entry.focus.y * entry.focus.zoom);
    assert.ok(entry.png.width <= 256 && entry.png.height <= 256);
    assert.equal(entry.optionalFailures, 0);
    assert.ok(catalog.assets[entry.png.file]);
  }
});

test('native square portraits retain original source bytes and record provenance', () => {
  const nativeModels = Object.values(catalog.models).filter((model) => model.portraitKind === 'native-square');
  assert.equal(nativeModels.length, proof.nativeModelRecords);
  for (const [sha, entry] of Object.entries(manifest.native)) {
    assert.equal(entry.source.sha256, sha);
    assert.ok(['android', 'steam'].includes(entry.source.platform));
    assert.ok(proof.sources[entry.source.file] === sha);
  }
});

test('PNG metadata rejects malformed and unbounded input', () => {
  assert.throws(() => size(Buffer.alloc(33)), /Invalid PNG/);
  const invented = Buffer.alloc(33);
  Buffer.from([137,80,78,71,13,10,26,10]).copy(invented);
  invented.writeUInt32BE(2048, 16); invented.writeUInt32BE(1, 20);
  assert.throws(() => size(invented), /Unexpected PNG dimensions/);
});
