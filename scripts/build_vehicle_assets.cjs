// Offline asset build. The isolated Maiden viewer reads public local artwork only.
// No account bridge, profile, saved credentials, network, or game session is used.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const hash = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const check = (value, message) => { if (!value) throw Error(message); };
const recipe = Object.freeze({ width: 256, height: 256, dpr: 1, seed: 12345,
  mode: 'maiden-production-first-frame-eye-focus', eyeX: 0.5, eyeY: 0.4 });
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = argv.indexOf(name);
  return i < 0 ? fallback : argv[i + 1];
};
const project = path.resolve(__dirname, '..');
const output = path.resolve(arg('--output', path.join(project, 'site/assets/vehicles')));
const maiden = path.resolve(arg('--maiden-root', path.join(project, '..', 'MaidenWaltz')));
const force = argv.includes('--force');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function bounded(operation, message, milliseconds = 25000) {
  let timer;
  try { return await Promise.race([operation, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(message)), milliseconds); })]); }
  finally { clearTimeout(timer); }
}
async function json(file) { return JSON.parse(await fs.readFile(file, 'utf8')); }
async function atomic(file, bytes) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const temporary = file + '.tmp-' + process.pid;
  try { await fs.writeFile(temporary, bytes); await fs.rename(temporary, file); }
  finally { await fs.rm(temporary, { force: true }); }
}
function size(bytes) {
  check(bytes.length >= 33 && bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])), 'Invalid PNG');
  const width = bytes.readUInt32BE(16), height = bytes.readUInt32BE(20);
  check(width > 0 && height > 0 && width <= 1024 && height <= 1024, 'Unexpected PNG dimensions');
  return { width, height };
}
async function launcher() {
  const proofFile = path.join(project, 'docs/research/vehicle-source-proof.json');
  const python = path.resolve(arg('--python', path.join(maiden, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')));
  const verifier = path.join(__dirname, 'verify_vehicle_sources.py');
  const proofChild = spawn(python, ['-B', verifier, '--maiden-root', maiden, '--output', proofFile], {
    shell: false, windowsHide: true, stdio: 'inherit',
  });
  const proofCode = await new Promise((resolve, reject) => { proofChild.once('error', reject); proofChild.once('exit', resolve); });
  check(proofCode === 0, 'Pinned vehicle source verification failed');
  const proof = await json(proofFile);
  const catalogPath = path.join(maiden, 'renderer/catalog.json');
  const catalogBytes = await fs.readFile(catalogPath);
  const catalog = JSON.parse(catalogBytes);
  const baseline = await json(path.join(maiden, 'baseline/catalog-manifest.json'));
  check(hash(catalogBytes) === proof.catalogSha256, 'Verified vehicle catalog changed');
  check(catalog.dataCommit === baseline.dataCommit, 'Maiden data pin mismatch');
  const { plan } = require(path.join(maiden, 'tools/costume-poster-build.cjs'));
  const inputs = await plan(maiden);
  check(inputs.dataCommit === catalog.dataCommit, 'Model source pin mismatch');
  const generatorSha256 = hash(JSON.stringify({ renderer: hash(await fs.readFile(__filename)), verifier: hash(await fs.readFile(verifier)) }));
  const manifestFile = path.join(project, 'docs/research/vehicle-art-build.json');
  let previous;
  try { previous = await json(manifestFile); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const live = {}, pending = [];
  for (const [model, entry] of Object.entries(inputs.models)) {
    const inputSha256 = hash(JSON.stringify({ recipe, generatorSha256, input: entry.inputSha256 }));
    const prior = previous?.live2d?.[model];
    let cached = false;
    if (!force && prior?.inputSha256 === inputSha256 && /^[a-f0-9]{64}\.png$/.test(prior.png?.file)) {
      try {
        const bytes = await fs.readFile(path.join(output, prior.png.file));
        cached = hash(bytes) === prior.png.sha256 && bytes.length === prior.png.bytes &&
          size(bytes).width === prior.png.width && size(bytes).height === prior.png.height;
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    if (cached) live[model] = prior;
    else pending.push(model);
    if (!cached) live[model] = { inputSha256, sources: entry.sources };
  }
  let profile;
  try {
    if (pending.length) {
      profile = path.resolve(await fs.mkdtemp(path.join(os.tmpdir(), 'ranking-vehicle-art-')));
      check(path.dirname(profile) === path.resolve(os.tmpdir()) && path.basename(profile).startsWith('ranking-vehicle-art-'), 'Unexpected profile path');
      await fs.writeFile(path.join(profile, 'job.json'), JSON.stringify({ maiden, models: pending, recipe }));
      const environment = { ...process.env, RANKING_VEHICLE_ART_BUILD: profile };
      delete environment.ELECTRON_RUN_AS_NODE;
      const child = spawn(require(path.join(maiden, 'node_modules/electron')), [__filename], {
        cwd: profile, env: environment, shell: false, windowsHide: true, stdio: 'inherit',
      });
      const timer = setTimeout(() => child.kill(), 600000);
      try {
        const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
        check(code === 0, 'Offline portrait rendering failed; previous catalog retained');
      } finally { clearTimeout(timer); }
      const built = await json(path.join(profile, 'result.json'));
      for (const model of pending) {
        const entry = built[model];
        check(entry && /^[a-f0-9]{64}\.png$/.test(entry.png?.file), 'Missing generated portrait');
        const bytes = await fs.readFile(path.join(profile, entry.png.file));
        check(hash(bytes) === entry.png.sha256, 'Generated portrait hash mismatch');
        check(JSON.stringify(size(bytes)) === JSON.stringify({ width: entry.png.width, height: entry.png.height }), 'Generated portrait size mismatch');
        await atomic(path.join(output, entry.png.file), bytes);
        live[model] = { ...live[model], ...entry };
      }
    }
    check(JSON.stringify(await plan(maiden)) === JSON.stringify(inputs), 'Model sources changed during build');
    const assets = {}, models = {}, native = {};
    const artwork = await json(path.join(maiden, 'baseline/artwork-manifest.json'));
    const sourceByHash = new Map();
    for (const selection of Object.values(artwork.selections)) {
      const file = selection.selectedPath, digest = selection.files?.[file];
      if (file && digest) sourceByHash.set(digest, { platform: selection.platform, file, sha256: digest });
    }
    const publish = async (bytes) => {
      const digest = hash(bytes), file = digest + '.png';
      assets[file] = { sha256: digest, ...size(bytes), bytes: bytes.length };
      await atomic(path.join(output, file), bytes);
      return './assets/vehicles/' + file;
    };
    for (const [id, model] of Object.entries(catalog.vehicleDetails.models)) {
      if (model.live2d) {
        const entry = live[model.live2d];
        check(entry?.png, 'Missing Live2D portrait for model ' + id);
        const bytes = await fs.readFile(path.join(output, entry.png.file));
        models[id] = { portrait: await publish(bytes), portraitKind: 'live2d-generated' };
      } else if (model.portrait) {
        check(/^\.\/portraits\/[a-f0-9]{24}\.png$/.test(model.portrait), 'Invalid native portrait path');
        const bytes = await fs.readFile(path.join(maiden, 'renderer/public', model.portrait.slice(2)));
        check(hash(bytes).startsWith(path.basename(model.portrait, '.png')), 'Native portrait fingerprint mismatch');
        const portrait = await publish(bytes), digest = hash(bytes);
        native[digest] = { portrait, original: model.portrait, source: sourceByHash.get(digest) ?? null };
        check(native[digest].source, 'Native source provenance missing');
        models[id] = { portrait, portraitKind: 'native-square' };
      } else models[id] = { portrait: null, portraitKind: 'unavailable' };
    }
    const units = Object.fromEntries(Object.entries(catalog.units).map(([type, unit]) => [type, {
      phases: Object.fromEntries(Object.entries(unit.phases).map(([phase, definition]) => {
        const defaultModel = definition.appearances[0]?.model;
        check(defaultModel && models[defaultModel], 'Missing phase default model');
        return [phase, { name: definition.displayName, maidenName: definition.maidenName ?? '', defaultModel }];
      })),
    }]));
    const publicCatalog = { schema: 'metal-waltz.vehicle-art.v1', dataCommit: catalog.dataCommit, models, units, assets };
    const encoded = Buffer.from(JSON.stringify(publicCatalog) + '\n');
    const manifest = {
      schema: 'metal-waltz.vehicle-art-build.v1', dataCommit: catalog.dataCommit,
      gameVersion: catalog.gameVersion, patchVersion: catalog.patchVersion,
      source: { repository: 'https://github.com/AlexValliere/MaidenWaltz',
        catalogSha256: hash(catalogBytes), catalogManifestSha256: hash(await fs.readFile(path.join(maiden, 'baseline/catalog-manifest.json'))),
        artworkManifestSha256: hash(await fs.readFile(path.join(maiden, 'baseline/artwork-manifest.json'))),
        sourceProofSha256: hash(await fs.readFile(proofFile)),
        artworkPins: await json(path.join(maiden, 'baseline/artwork-sources.json')) },
      generatorSha256, recipe, maidenGenerator: inputs.generator,
      live2d: Object.fromEntries(Object.keys(inputs.models).map((model) => [model, live[model]])),
      native, catalogSha256: hash(encoded),
      summary: { models: Object.keys(models).length, units: Object.keys(units).length,
        phases: Object.values(units).reduce((n, unit) => n + Object.keys(unit.phases).length, 0),
        live2dConfigurations: Object.keys(live).length, nativePortraits: Object.keys(native).length,
        uniquePngs: Object.keys(assets).length, pngBytes: Object.values(assets).reduce((n, entry) => n + entry.bytes, 0),
        unavailableModels: Object.values(models).filter((model) => !model.portrait).length },
    };
    await atomic(path.join(output, 'catalog.json'), encoded);
    await atomic(manifestFile, JSON.stringify(manifest, null, 2) + '\n');
    // Only remove stale assets named by the previous generated catalog manifest.
    const keep = new Set(Object.keys(assets));
    for (const entry of Object.values(previous?.live2d ?? {})) {
      const file = entry.png?.file;
      if (typeof file === 'string' && /^[a-f0-9]{64}\.png$/.test(file) && !keep.has(file))
        await fs.rm(path.join(output, file), { force: true });
    }
    console.log(JSON.stringify({ ...manifest.summary, rendered: pending.length, reused: Object.keys(live).length - pending.length, catalogSha256: manifest.catalogSha256 }));
  } finally {
    if (profile) await fs.rm(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 150 });
  }
}
async function render() {
  const { app, BrowserWindow, protocol, nativeImage } = require('electron');
  const profile = process.env.RANKING_VEHICLE_ART_BUILD;
  check(profile, 'Use Node with --maiden-root');
  app.setPath('userData', profile);
  app.commandLine.appendSwitch('force-device-scale-factor', '1');
  app.on('window-all-closed', () => {});
  protocol.registerSchemesAsPrivileged([{ scheme: 'maiden-art', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } }]);
  let window;
  try {
    const job = await json(path.join(profile, 'job.json'));
    check(JSON.stringify(job.recipe) === JSON.stringify(recipe), 'Build recipe mismatch');
    const publicRoot = path.join(job.maiden, 'renderer/public');
    const { artPath } = require(path.join(job.maiden, 'desktop/art-protocol.cjs'));
    await app.whenReady();
    protocol.handle('maiden-art', async (request) => {
      try {
        check(request.method === 'GET', 'Invalid request method');
        const url = new URL(request.url), file = artPath(publicRoot, request.url);
        const headers = { 'Content-Type': 'text/javascript', 'Cache-Control': 'no-store' };
        if (url.pathname === '/live2d/ranking-recorder.js') return new Response(
          `let seed=${recipe.seed};Math.random=()=>((seed=(Math.imul(seed,1664525)+1013904223)>>>0)/4294967296);window.__rankingCapture=null;addEventListener('message',e=>{if(e.source===window&&e.origin==='maiden-art://home'&&['maiden-waltz-art-poster','maiden-waltz-art-error'].includes(e.data?.type))window.__rankingCapture=e.data;});`, { headers });
        let bytes = await fs.readFile(file);
        if (url.pathname === '/live2d/viewer.html') bytes = Buffer.from(bytes.toString().replace('<head>', '<head><script src="./ranking-recorder.js"></script>'));
        const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.css': 'text/css', '.png': 'image/png', '.pn_': 'image/png' };
        return new Response(bytes, { headers: { ...headers, 'Content-Type': types[path.extname(file)] ?? 'application/octet-stream' } });
      } catch { return new Response(null, { status: 404 }); }
    });
    window = new BrowserWindow({ show: false, useContentSize: true, width: recipe.width, height: recipe.height,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false, offscreen: true } });
    window.webContents.on('paint', () => {});
    window.webContents.session.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith('maiden-art://home/') && details.url !== 'about:blank' }));
    const js = (code) => window.webContents.executeJavaScript(code, true);
    const result = {};
    for (const model of job.models) {
      console.log('Preparing portrait ' + (Object.keys(result).length + 1) + '/' + job.models.length + ': ' + model);
      await bounded(window.loadURL('maiden-art://home/live2d/viewer.html?poster=1&portrait=1'), 'Portrait viewer startup timed out');
      const framing = await bounded(js(`(async()=>{
        const {portraitFocus}=await import('./portrait-focus.mjs');
        const settings=await(await fetch(${JSON.stringify(model)})).json();
        if(settings.Layout)throw Error('Explicit model Layout needs review');
        const data=await(await fetch(new URL(settings.FileReferences.Moc,new URL(${JSON.stringify(model)},location.href)))).arrayBuffer();
        let moc,model;
        try{
          moc=Live2DCubismCore.Moc.fromArrayBuffer(data);model=Live2DCubismCore.Model.fromMoc(moc);
          const focus=portraitFocus(model);if(!focus)throw Error('No measurable eye focus');
          const frame={left:${recipe.eyeX}-focus.x*focus.zoom,top:${recipe.eyeY}-focus.y*focus.zoom,width:focus.zoom,height:focus.zoom};
          window.postMessage({type:'maiden-waltz-art-load',model:${JSON.stringify(model)},requestId:'portrait'},'maiden-art://home');
          window.postMessage({type:'maiden-waltz-art-configure',frame,active:true},'maiden-art://home');
          return {focus,frame};
        }finally{model?.release();moc?._release();}
      })()`), 'Eye-focus measurement timed out: ' + model);
      const deadline = Date.now() + 25000;
      let capture;
      while (!(capture = await js('window.__rankingCapture'))) { check(Date.now() < deadline, 'Portrait timed out: ' + model); await pause(20); }
      check(capture.type === 'maiden-waltz-art-poster' && capture.png?.startsWith('data:image/png;base64,'), 'Portrait failed: ' + model);
      const metrics = await js('window.__maidenPortrait');
      check(metrics.frames === 1 && metrics.posters === 1 && metrics.failures === 0 && metrics.modelsLoaded === 1 && metrics.modelsReleased === 1 && metrics.texturesLoaded === metrics.texturesReleased && metrics.textureBytes === 0, 'Portrait ownership not released');
      const bytes = Buffer.from(capture.png.split(',')[1], 'base64'), digest = hash(bytes), file = digest + '.png';
      const image = nativeImage.createFromBuffer(bytes);
      check(!image.isEmpty(), 'Portrait cannot be decoded');
      const pixels = image.toBitmap(); let visible = 0;
      for (let i = 3; i < pixels.length; i += 4) if (pixels[i] >= 24) visible++;
      check(visible >= 100, 'Blank portrait');
      await fs.writeFile(path.join(profile, file), bytes);
      result[model] = { ...framing, png: { file, sha256: digest, ...size(bytes), bytes: bytes.length }, optionalFailures: metrics.optionalFailures,
        renderedWith: { electron: process.versions.electron, chromium: process.versions.chrome, platform: process.platform } };
      console.log('Portrait ' + Object.keys(result).length + '/' + job.models.length + ': ' + model);
    }
    await fs.writeFile(path.join(profile, 'result.json'), JSON.stringify(result));
    window.destroy(); app.exit(0);
  } catch (error) { console.error(error.message); window?.destroy(); app.exit(1); }
}
// Electron's application bootstrap is the CommonJS main module; the supplied
// script therefore does not satisfy require.main === module there.
if (process.versions.electron || require.main === module) {
  (process.versions.electron ? render() : launcher()).catch((error) => {
    console.error(error.message);
    if (process.versions.electron) require('electron').app.exit(1); else process.exitCode = 1;
  });
}
module.exports = { recipe, size };
