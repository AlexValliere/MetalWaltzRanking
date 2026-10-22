const INDEX_SCHEMA = 'metal-waltz.rankings-index.v1';
const SNAPSHOT_SCHEMAS = new Set([
  'maiden-waltz.public-ranking-snapshot.v1',
  'metal-waltz.public-ranking-snapshot.v1',
]);
const PAGE_SIZES = new Set([25, 50, 100, 200]);
const numberFormat = new Intl.NumberFormat('en-US');
const VEHICLE_STATUS = new Set(['resolved', 'unavailable', 'not_collected']);

function unsignedInteger(value, maximum = 0xffffffff) {
  return Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}

export function safePortraitPath(path) {
  if (typeof path !== 'string' || !/^\.\/assets\/vehicles\/[a-f0-9]{64}\.png$/.test(path))
    throw new Error('Invalid vehicle portrait path.');
  return path;
}

export function validateVehicleCatalog(data) {
  if (
    data?.schema !== 'metal-waltz.vehicle-art.v1' ||
    !data.models || Array.isArray(data.models) || typeof data.models !== 'object' ||
    !data.units || Array.isArray(data.units) || typeof data.units !== 'object'
  ) throw new Error('The vehicle artwork catalog has an unsupported format.');
  for (const [id, model] of Object.entries(data.models)) {
    if (!/^[1-9]\d*$/.test(id) || !unsignedInteger(Number(id)) || !model ||
        !['native-square', 'live2d-generated', 'unavailable'].includes(model.portraitKind) ||
        (model.portrait !== null && typeof model.portrait !== 'string') ||
        (model.portraitKind === 'unavailable') !== (model.portrait === null))
      throw new Error('The vehicle artwork catalog contains an invalid model.');
    if (model.portrait !== null) safePortraitPath(model.portrait);
  }
  for (const [id, unit] of Object.entries(data.units)) {
    if (!/^[1-9]\d*$/.test(id) || !unsignedInteger(Number(id)) || !unit?.phases ||
        typeof unit.phases !== 'object' || Array.isArray(unit.phases))
      throw new Error('The vehicle artwork catalog contains an invalid vehicle.');
    for (const [phase, detail] of Object.entries(unit.phases)) {
      if (!/^[0-9]+$/.test(phase) || !unsignedInteger(Number(phase), 255) ||
          typeof detail?.name !== 'string' || !detail.name.trim() ||
          typeof detail.maidenName !== 'string' ||
          !(detail.defaultModel === null || unsignedInteger(detail.defaultModel)))
        throw new Error('The vehicle artwork catalog contains an invalid vehicle phase.');
    }
  }
  return data;
}

export function publicVehicles(row) {
  const status = row?.vehiclesStatus ?? 'not_collected';
  return { status, vehicles: status === 'resolved' ? row.vehicles : [] };
}

export function vehicleDisplayData(vehicle, catalog) {
  // Public vehicle details do not include the selected costume. The native public
  // view uses the phase's default model; never infer a cosmetic from ownership.
  const phase = catalog?.units?.[String(vehicle.capsuleTypeId)]?.phases?.[String(vehicle.phase)];
  const model = phase ? catalog?.models?.[String(phase.defaultModel)] : null;
  return {
    name: phase?.name || 'Unidentified vehicle',
    maidenName: phase?.maidenName || '',
    portrait: model?.portrait || null,
    portraitKind: model?.portraitKind || 'unavailable',
    level: vehicle.level,
    fate: vehicle.fate,
  };
}

function validatePublicVehicles(row) {
  const hasStatus = Object.hasOwn(row, 'vehiclesStatus');
  const hasVehicles = Object.hasOwn(row, 'vehicles');
  if (!hasStatus && !hasVehicles) return;
  if (!hasStatus || !hasVehicles || !VEHICLE_STATUS.has(row.vehiclesStatus) ||
      (row.vehiclesStatus === 'resolved' ? !Array.isArray(row.vehicles) : row.vehicles !== null))
    throw new Error('The snapshot contains an invalid public vehicle list.');
  if (row.vehiclesStatus !== 'resolved') return;
  if (row.vehicles.length > 255)
    throw new Error('The public vehicle list exceeds its supported size.');
  const types = new Set();
  for (const vehicle of row.vehicles) {
    if (!vehicle || !unsignedInteger(vehicle.capsuleTypeId) || vehicle.capsuleTypeId === 0 ||
        types.has(vehicle.capsuleTypeId) ||
        !unsignedInteger(vehicle.phase, 255) ||
        !(vehicle.level === null || unsignedInteger(vehicle.level, 0x7fffffff)) ||
        typeof vehicle.fate !== 'boolean')
      throw new Error('The snapshot contains an invalid public vehicle.');
    types.add(vehicle.capsuleTypeId);
  }
}

export function rankingRouteFromHash(hash) {
  const parameters = new URLSearchParams(String(hash).replace(/^#/, ''));
  // Ordinary document anchors belong to the browser, not the ranking router.
  if (!parameters.has('server')) return null;
  return { key: parameters.get('server'), query: (parameters.get('q') || '').slice(0, 512) };
}

export function normalizeSearch(value) {
  return String(value ?? '')
    .normalize('NFKC')
    .toLocaleLowerCase('en-US');
}

export function hasName(row) {
  return typeof row?.name === 'string' && row.name.trim().length > 0;
}

export function filterRows(rows, query) {
  const needle = normalizeSearch(query).trim();
  return needle
    ? rows.filter((row) => hasName(row) && normalizeSearch(row.name).includes(needle))
    : rows;
}

export function pageRows(rows, requestedPage, pageSize = 100) {
  if (!PAGE_SIZES.has(pageSize)) throw new Error('Unsupported page size');
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const page = Math.min(Math.max(0, Math.trunc(requestedPage) || 0), pages - 1);
  const start = page * pageSize;
  return { rows: rows.slice(start, start + pageSize), page, pages, start };
}

export function pageForRank(rows, rank, pageSize = 100) {
  if (!Number.isSafeInteger(rank) || rank < 1 || !PAGE_SIZES.has(pageSize)) return -1;
  const index = rows.findIndex((row) => row.rank === rank);
  return index < 0 ? -1 : Math.floor(index / pageSize);
}

export function safeSnapshotPath(file) {
  if (
    typeof file !== 'string' ||
    !/^[A-Za-z0-9][A-Za-z0-9._-]*\.json$/.test(file) ||
    file.includes('..') ||
    file.length > 128
  ) {
    throw new Error('Invalid snapshot filename');
  }
  return `./data/${encodeURIComponent(file)}`;
}

function csvCell(value) {
  let text = String(value ?? '');
  // Keep names as text when a spreadsheet opens the exported CSV.
  if (/^[\s\u0000-\u001f]*[=+\-@]/u.test(text) || /^[\t\r\n]/u.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function rowsToCsv(rows) {
  const lines = ['rank,commander,grandeur,name_status'];
  for (const row of rows) {
    lines.push(
      [
        csvCell(row.rank),
        csvCell(hasName(row) ? row.name : ''),
        csvCell(row.grandeur),
        csvCell(hasName(row) ? 'resolved' : 'unavailable'),
      ].join(','),
    );
  }
  return `\uFEFF${lines.join('\r\n')}\r\n`;
}

function validTime(value) {
  return typeof value === 'string' && Number.isFinite(Date.parse(value));
}

export function validateIndex(data) {
  if (
    data?.schema !== INDEX_SCHEMA ||
    !validTime(data.generatedAtUtc) ||
    !Array.isArray(data.servers) ||
    !data.servers.length
  ) {
    throw new Error('The archive index has an unsupported format.');
  }
  const keys = new Set();
  for (const server of data.servers) {
    if (
      typeof server?.key !== 'string' ||
      !/^[A-Za-z0-9_-]{1,32}$/.test(server.key) ||
      keys.has(server.key) ||
      typeof server.name !== 'string' ||
      !server.name.trim() ||
      !Array.isArray(server.aliases) ||
      !server.aliases.every((alias) => typeof alias === 'string') ||
      !Number.isSafeInteger(server.rows) ||
      server.rows < 0 ||
      !Number.isSafeInteger(server.namesResolved) ||
      server.namesResolved < 0 ||
      !Number.isSafeInteger(server.namesMissing) ||
      server.namesMissing < 0 ||
      server.namesResolved + server.namesMissing !== server.rows ||
      !['ok', 'failed', 'seed'].includes(server.refreshStatus) ||
      !validTime(server.startedAtUtc) ||
      !validTime(server.completedAtUtc) ||
      Date.parse(server.startedAtUtc) > Date.parse(server.completedAtUtc) ||
      typeof server.sha256 !== 'string' ||
      !/^[a-f0-9]{64}$/i.test(server.sha256)
    )
      throw new Error('The archive index contains an invalid server record.');
    safeSnapshotPath(server.file);
    keys.add(server.key);
  }
  return data;
}

export function validateSnapshot(data, server) {
  if (
    !SNAPSHOT_SCHEMAS.has(data?.schema) ||
    data.serverKey !== server.key ||
    data.fund !== 5 ||
    data.rankingEnumerationComplete !== true ||
    data.startedAtUtc !== server.startedAtUtc ||
    data.completedAtUtc !== server.completedAtUtc ||
    data.namesComplete !== (server.namesMissing === 0) ||
    !Array.isArray(data.rows) ||
    data.rows.length !== server.rows
  ) {
    throw new Error('The snapshot does not match its archive index.');
  }
  let resolved = 0;
  for (let i = 0; i < data.rows.length; i++) {
    const row = data.rows[i];
    if (
      row?.rank !== i + 1 ||
      !Number.isSafeInteger(row.grandeur) ||
      (i > 0 && row.grandeur > data.rows[i - 1].grandeur) ||
      !(row.name === null || typeof row.name === 'string') ||
      row.nameStatus !== (hasName(row) ? 'resolved' : 'unavailable')
    )
      throw new Error('The snapshot contains an invalid ranking row.');
    if (hasName(row)) resolved++;
    validatePublicVehicles(row);
  }
  if (resolved !== server.namesResolved)
    throw new Error('The snapshot name count does not match its archive index.');
  return data;
}

export async function fetchServerSnapshot(index, key, fetchData, options = {}) {
  const nextIndex = options.refreshIndex
    ? validateIndex(await fetchData('./data/index.json', options.signal))
    : index;
  const server = nextIndex.servers.find((entry) => entry.key === key);
  if (!server) throw new Error('The requested server is absent from the archive index.');
  const snapshot = validateSnapshot(
    await fetchData(safeSnapshotPath(server.file), options.signal),
    server,
  );
  // Stage the matching pair together; the caller commits it only if still current.
  return { index: nextIndex, server, snapshot };
}

function utcDate(value, includeTime = false) {
  const options = { timeZone: 'UTC', day: '2-digit', month: 'short', year: 'numeric' };
  if (includeTime) Object.assign(options, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  return new Intl.DateTimeFormat('en-GB', options).format(new Date(value));
}

function utcClock(value) {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(value));
}

function browserApp() {
  const $ = (id) => document.getElementById(id);
  const state = {
    index: null,
    server: null,
    snapshot: null,
    filtered: [],
    page: 0,
    pageSize: 100,
    query: '',
    loading: false,
    sequence: 0,
    controller: null,
    searchTimer: null,
    catalog: null,
    tooltipButton: null,
  };
  const shortLabels = {
    Bonn: 'Europe №1',
    YaleAndroid: 'Harrisburg',
    YaleIOS: 'Columbus',
    JP: 'Japan №1',
    KOREA: 'Korea',
    JKMIX: 'Japan / Korea №2',
    BANGKOK: 'Thailand',
  };
  const inputIds = ['commander-search', 'page-size', 'rank-number', 'download-csv'];

  function setText(id, value) {
    $(id).textContent = value;
  }
  function showNotice(id, text, kind) {
    const node = $(id);
    node.replaceChildren();
    node.hidden = !text;
    node.className = kind ? `notice notice-${kind}` : 'notice';
    if (text) node.append(document.createTextNode(text));
  }

  function updateLocation() {
    if (!state.server) return;
    const parameters = new URLSearchParams({ server: state.server.key });
    if (state.query) parameters.set('q', state.query);
    history.replaceState(null, '', `${location.pathname}${location.search}#${parameters}`);
  }

  function setServerChoice(key) {
    $('server-select').value = key;
    for (const button of $('server-tabs').children)
      button.setAttribute('aria-current', String(button.dataset.key === key));
  }

  function renderServerChoices() {
    const tabs = document.createDocumentFragment();
    const options = document.createDocumentFragment();
    for (const server of state.index.servers) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'server-tab';
      button.dataset.key = server.key;
      button.setAttribute('aria-current', 'false');
      button.setAttribute(
        'aria-label',
        `${server.name}, ${numberFormat.format(server.rows)} ranking entries`,
      );
      const name = document.createElement('span');
      name.className = 'server-tab-name';
      name.textContent = shortLabels[server.key] || server.name;
      const detail = document.createElement('span');
      detail.className = 'server-tab-detail';
      detail.textContent = `${numberFormat.format(server.rows)} entries`;
      button.append(name, detail);
      button.addEventListener('click', () => loadServer(server.key));
      tabs.append(button);
      const option = document.createElement('option');
      option.value = server.key;
      option.textContent = server.name;
      options.append(option);
    }
    $('server-tabs').replaceChildren(tabs);
    $('server-select').replaceChildren(options);
    $('server-select').disabled = false;
  }

  function renderIndex() {
    setText('backend-count', numberFormat.format(state.index.servers.length));
    const allSeed = state.index.servers.every((server) => server.refreshStatus === 'seed');
    const failed = state.index.servers.filter((server) => server.refreshStatus === 'failed').length;
    setText(
      'collection-status',
      allSeed
        ? 'INITIAL VERIFIED SNAPSHOTS'
        : failed
          ? 'ARCHIVE / REFRESH INCOMPLETE'
          : 'PUBLIC RANKING ARCHIVE',
    );
    setText(
      'index-freshness',
      `Index updated ${utcDate(state.index.generatedAtUtc, true)} UTC. Snapshot times are shown per server.`,
    );
    renderServerChoices();
  }

  function renderSummary() {
    const server = state.server;
    const nameCoverage = server.rows ? (server.namesResolved / server.rows) * 100 : 100;
    setText('standings-title', server.name);
    setText(
      'server-description',
      server.aliases.length > 1
        ? 'One shared leaderboard for Japan №2 and Korea №2.'
        : 'PvP standings, ordered by Grandeur.',
    );
    setText('entries-count', numberFormat.format(server.rows));
    setText('names-count', numberFormat.format(server.namesResolved));
    setText(
      'names-detail',
      server.namesMissing
        ? `${nameCoverage.toFixed(1)}% coverage · ${numberFormat.format(server.namesMissing)} unavailable`
        : '100% name coverage',
    );
    $('names-detail').classList.toggle('warning', server.namesMissing > 0);
    setText('snapshot-date', utcDate(server.completedAtUtc));
    const statusText =
      server.refreshStatus === 'seed'
        ? 'Initial verified snapshot'
        : server.refreshStatus === 'failed'
          ? 'Latest refresh failed · previous snapshot retained'
          : 'Successful collection';
    setText('snapshot-detail', `${utcClock(server.completedAtUtc)} UTC · ${statusText}`);
    $('snapshot-detail').classList.toggle('warning', server.refreshStatus === 'failed');
    $('snapshot-date').title = `${utcDate(server.completedAtUtc, true)} UTC`;
    $('download-json').href = safeSnapshotPath(server.file);
    $('download-json').download = server.file;
    $('download-json').hidden = false;
    setText(
      'table-caption',
      `${server.name} PvP standings, ordered by Grandeur, collected ${utcDate(server.completedAtUtc, true)} UTC`,
    );
    $('rank-number').max = String(server.rows);
    setText(
      'missing-note',
      server.namesMissing
        ? `${numberFormat.format(server.namesMissing)} entries are visible without a commander name. See “Why are some names unavailable?” below.`
        : '',
    );
    $('missing-note').hidden = !server.namesMissing;
    const vehicleLists = state.snapshot.rows.filter((row) => row.vehiclesStatus === 'resolved').length;
    const vehicleCoverage = vehicleLists
      ? `Public vehicle lists available for ${numberFormat.format(vehicleLists)} of ${numberFormat.format(server.rows)} entries. `
      : 'Public vehicle lists are not yet available in this snapshot. ';
    setText('vehicle-note', `${vehicleCoverage}Vehicles follow the returned server order; this does not establish a current attack or defence formation.${vehicleLists && !state.catalog ? ' Vehicle names and artwork could not be loaded.' : ''}`);
    document.title = `${server.name} · Metal Waltz Rankings`;
    if (server.refreshStatus === 'failed') {
      showNotice(
        'load-notice',
        `The latest scheduled refresh for ${server.name} failed. These rankings are the last successful snapshot, collected ${utcDate(server.completedAtUtc, true)} UTC.`,
        'warning',
      );
    } else if (server.refreshStatus === 'seed') {
      showNotice(
        'load-notice',
        'Initial verified snapshot. Scheduled collection has not yet replaced this server’s first export.',
      );
    } else showNotice('load-notice', '');
  }

  function messageRow(title, detail = '') {
    const tr = document.createElement('tr');
    tr.className = 'table-message';
    const td = document.createElement('td');
    td.colSpan = 4;
    td.textContent = title;
    if (detail) {
      const paragraph = document.createElement('p');
      paragraph.textContent = detail;
      td.append(paragraph);
    }
    tr.append(td);
    return tr;
  }

  function hideVehicleTooltip() {
    $('vehicle-tooltip').hidden = true;
    state.tooltipButton?.removeAttribute('aria-describedby');
    state.tooltipButton = null;
  }

  function showVehicleTooltip(button, vehicle) {
    hideVehicleTooltip();
    const tooltip = $('vehicle-tooltip');
    tooltip.textContent = [
      vehicle.name, vehicle.maidenName,
      vehicle.level === null ? null : `Level ${numberFormat.format(vehicle.level)}`,
    ].filter(Boolean).join(' · ');
    tooltip.hidden = false;
    const anchor = button.getBoundingClientRect();
    const bounds = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.max(12, Math.min(innerWidth - bounds.width - 12, anchor.left + anchor.width / 2 - bounds.width / 2))}px`;
    tooltip.style.top = `${anchor.top > bounds.height + 16 ? anchor.top - bounds.height - 8 : anchor.bottom + 8}px`;
    state.tooltipButton = button;
    button.setAttribute('aria-describedby', 'vehicle-tooltip');
  }

  function vehicleArt(vehicle) {
    const art = document.createElement('span');
    art.className = 'vehicle-portrait';
    const fallback = document.createElement('span');
    fallback.className = 'vehicle-fallback';
    fallback.textContent = vehicle.name;
    if (vehicle.portrait) {
      const image = document.createElement('img');
      image.src = vehicle.portrait;
      image.alt = '';
      image.width = 116;
      image.height = 108;
      image.loading = 'lazy';
      image.decoding = 'async';
      image.addEventListener('error', () => image.replaceWith(fallback), { once: true });
      art.append(image);
    } else art.append(fallback);
    if (Number.isInteger(vehicle.level) && vehicle.level >= 1 && vehicle.level <= 99) {
      const level = document.createElement('span');
      level.className = 'vehicle-level';
      level.textContent = String(vehicle.level);
      level.setAttribute('aria-hidden', 'true');
      art.append(level);
    }
    return art;
  }

  function openVehicleDialog(row, vehicles, selectedIndex) {
    hideVehicleTooltip();
    const dialog = $('vehicle-dialog');
    dialog.querySelector('.vehicle-gallery')?.remove();
    const commander = hasName(row) ? row.name : `Rank ${numberFormat.format(row.rank)}`;
    const select = (index) => {
      const detail = vehicleDisplayData(vehicles[index], state.catalog);
      setText('vehicle-dialog-name', detail.name);
      setText('vehicle-dialog-maiden', detail.maidenName);
      const information = [
        `${commander} · Public vehicle ${index + 1} of ${vehicles.length}`,
        detail.level === null ? null : `Level ${numberFormat.format(detail.level)}`,
        detail.fate ? 'Fate Pin' : null,
      ].filter(Boolean);
      setText('vehicle-dialog-context', `${information.join(' · ')}. Default vehicle artwork; the equipped costume is not supplied.`);
      $('vehicle-dialog-art').replaceChildren(vehicleArt(detail));
      for (const button of dialog.querySelectorAll('.vehicle-gallery button'))
        button.setAttribute('aria-pressed', String(Number(button.dataset.index) === index));
    };
    if (vehicles.length > 6) {
      const gallery = document.createElement('div');
      gallery.className = 'vehicle-gallery';
      gallery.setAttribute('role', 'group');
      gallery.setAttribute('aria-label', 'All returned public vehicles');
      vehicles.forEach((vehicle, index) => {
        const detail = vehicleDisplayData(vehicle, state.catalog);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'vehicle-button';
        button.dataset.index = String(index);
        button.setAttribute('aria-label', `Public vehicle ${index + 1}: ${detail.name}${detail.level === null ? '' : `, level ${numberFormat.format(detail.level)}`}`);
        button.append(vehicleArt(detail));
        button.addEventListener('click', () => select(index));
        gallery.append(button);
      });
      dialog.append(gallery);
    }
    select(selectedIndex);
    dialog.showModal();
  }

  function renderVehicles(row) {
    const cell = document.createElement('td');
    cell.className = 'vehicles';
    const { status, vehicles } = publicVehicles(row);
    if (status !== 'resolved' || vehicles.length === 0) {
      const message = document.createElement('span');
      message.className = 'vehicles-unavailable';
      message.textContent = status === 'resolved' ? 'No public vehicles' :
        status === 'unavailable' ? 'Vehicles unavailable' : 'Vehicles not collected';
      cell.append(message);
      return cell;
    }
    const list = document.createElement('div');
    list.className = 'vehicle-list';
    list.setAttribute('role', 'group');
    list.setAttribute('aria-label', `${vehicles.length} public vehicles, in server order`);
    vehicles.slice(0, 6).forEach((vehicle, index) => {
      const detail = vehicleDisplayData(vehicle, state.catalog);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'vehicle-button';
      button.setAttribute('aria-label', `Public vehicle ${index + 1}: ${detail.name}${detail.maidenName ? `, ${detail.maidenName}` : ''}${detail.level === null ? '' : `, level ${numberFormat.format(detail.level)}`}`);
      button.setAttribute('aria-haspopup', 'dialog');
      button.append(vehicleArt(detail));
      button.addEventListener('pointerenter', (event) => {
        if (event.pointerType !== 'touch') showVehicleTooltip(button, detail);
      });
      button.addEventListener('pointerleave', () => {
        if (document.activeElement !== button) hideVehicleTooltip();
      });
      button.addEventListener('focus', () => showVehicleTooltip(button, detail));
      button.addEventListener('blur', hideVehicleTooltip);
      button.addEventListener('click', () => openVehicleDialog(row, vehicles, index));
      list.append(button);
    });
    for (let index = vehicles.length; index < 6; index++) {
      const empty = document.createElement('span');
      empty.className = 'vehicle-empty';
      empty.setAttribute('aria-hidden', 'true');
      const art = document.createElement('span');
      art.className = 'vehicle-portrait';
      art.textContent = '–';
      empty.append(art);
      list.append(empty);
    }
    cell.append(list);
    if (vehicles.length > 6) {
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'vehicles-more';
      more.textContent = `+${numberFormat.format(vehicles.length - 6)}`;
      more.setAttribute('aria-label', `View all ${vehicles.length} public vehicles`);
      more.setAttribute('aria-haspopup', 'dialog');
      more.addEventListener('click', () => openVehicleDialog(row, vehicles, 6));
      cell.append(more);
    }
    return cell;
  }

  function renderRows() {
    hideVehicleTooltip();
    if ($('vehicle-dialog').open) $('vehicle-dialog').close();
    const result = pageRows(state.filtered, state.page, state.pageSize);
    state.page = result.page;
    const fragment = document.createDocumentFragment();
    for (const row of result.rows) {
      const tr = document.createElement('tr');
      if (row.rank <= 3) tr.className = 'top-rank';
      const rank = document.createElement('td');
      rank.className = 'rank';
      if (row.rank <= 3) {
        const badge = document.createElement('span');
        badge.className = 'rank-badge';
        badge.textContent = numberFormat.format(row.rank);
        rank.append(badge);
      } else rank.textContent = numberFormat.format(row.rank);
      const name = document.createElement('td');
      name.className = 'commander';
      if (hasName(row)) name.textContent = row.name;
      else {
        const unavailable = document.createElement('span');
        unavailable.className = 'name-unavailable';
        unavailable.textContent = 'Name unavailable';
        name.append(unavailable);
      }
      const grandeur = document.createElement('td');
      grandeur.className = 'score';
      grandeur.textContent = numberFormat.format(row.grandeur);
      tr.append(rank, name, renderVehicles(row), grandeur);
      fragment.append(tr);
    }
    if (!result.rows.length)
      fragment.append(
        messageRow(
          state.query.trim() ? 'No commanders found.' : 'No ranking entries in this snapshot.',
          state.query.trim()
            ? 'Try a shorter name or clear the search to see all standings.'
            : 'The server returned an empty ranking list.',
        ),
      );
    $('ranking-rows').replaceChildren(fragment);
    const end = result.start + result.rows.length;
    const total = numberFormat.format(state.filtered.length);
    setText(
      'results-label',
      state.filtered.length
        ? `Showing ${numberFormat.format(result.start + 1)}–${numberFormat.format(end)} of ${total}${state.query.trim() ? ' matching entries' : ' entries'}`
        : '0 matching entries',
    );
    setText(
      'page-label',
      `Page ${numberFormat.format(result.page + 1)} of ${numberFormat.format(result.pages)}`,
    );
    setText('current-page', numberFormat.format(result.page + 1));
    $('current-page').setAttribute('aria-label', `Current page, ${result.page + 1}`);
    $('first-page').disabled = result.page === 0;
    $('previous-page').disabled = result.page === 0;
    $('next-page').disabled = result.page >= result.pages - 1;
    $('last-page').disabled = result.page >= result.pages - 1;
    $('download-csv').disabled = !state.filtered.length;
    $('download-csv').textContent = state.query.trim() ? 'Export matches ↓' : 'Export CSV ↓';
  }

  function applySearch() {
    state.query = $('commander-search').value.slice(0, 512);
    state.filtered = filterRows(state.snapshot.rows, state.query);
    state.page = 0;
    renderRows();
    updateLocation();
  }

  async function fetchJson(path, signal) {
    const response = await fetch(path, {
      cache: 'no-cache',
      signal,
      credentials: 'omit',
      redirect: 'error',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function loadServer(key, query, refreshIndex = false) {
    const requestedServer = state.index.servers.find((entry) => entry.key === key);
    if (!requestedServer) return;
    if (state.server?.key === key && !state.loading && !refreshIndex) {
      if (query !== undefined) {
        $('commander-search').value = query;
        applySearch();
      }
      return;
    }
    state.controller?.abort();
    const controller = new AbortController();
    state.controller = controller;
    const sequence = ++state.sequence;
    const hadSnapshot = Boolean(state.snapshot);
    state.loading = true;
    setServerChoice(key);
    $('rankings-panel').setAttribute('aria-busy', 'true');
    showNotice('error-notice', '');
    showNotice(
      'load-notice',
      `Loading ${requestedServer.name}…${hadSnapshot ? ` The displayed standings are still ${state.server.name}.` : ''}`,
    );
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const result = await fetchServerSnapshot(state.index, key, fetchJson, {
        refreshIndex,
        signal: controller.signal,
      });
      if (sequence !== state.sequence) return;
      if (result.index !== state.index) {
        state.index = result.index;
        renderIndex();
      }
      state.server = result.server;
      state.snapshot = result.snapshot;
      setServerChoice(key);
      state.query = query !== undefined ? query : $('commander-search').value.slice(0, 512);
      $('commander-search').value = state.query;
      state.filtered = filterRows(result.snapshot.rows, state.query);
      state.page = 0;
      inputIds.forEach((id) => {
        $(id).disabled = false;
      });
      $('rank-jump').querySelector('button').disabled = false;
      renderSummary();
      renderRows();
      updateLocation();
    } catch (error) {
      if (sequence !== state.sequence) return;
      const suffix = hadSnapshot
        ? ` Showing the last loaded ${state.server.name} snapshot.`
        : ' Please try again shortly.';
      showNotice('error-notice', `Could not load ${requestedServer.name}.${suffix}`, 'error');
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = 'Retry';
      retry.addEventListener('click', () => loadServer(key, query, true));
      $('error-notice').append(retry);
      if (hadSnapshot) {
        setServerChoice(state.server.key);
        renderSummary();
      } else {
        showNotice('load-notice', '');
        setText('standings-title', 'Standings unavailable');
        setText('results-label', 'Snapshot could not be loaded');
        $('ranking-rows').replaceChildren(
          messageRow(
            'The snapshot could not be loaded.',
            'Use Retry above, or choose another server.',
          ),
        );
      }
    } finally {
      clearTimeout(timeout);
      if (sequence === state.sequence) {
        state.loading = false;
        $('rankings-panel').setAttribute('aria-busy', 'false');
      }
    }
  }

  function changePage(page) {
    if (!state.snapshot) return;
    state.page = page;
    renderRows();
    $('rankings-panel').scrollIntoView({
      block: 'start',
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    });
  }

  $('server-select').addEventListener('change', (event) => loadServer(event.target.value));
  $('commander-search').addEventListener('input', () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(applySearch, 100);
  });
  $('page-size').addEventListener('change', (event) => {
    const size = Number(event.target.value);
    if (PAGE_SIZES.has(size)) {
      state.pageSize = size;
      state.page = 0;
      renderRows();
    }
  });
  $('rank-jump').addEventListener('submit', (event) => {
    event.preventDefault();
    const input = $('rank-number');
    const rank = Number(input.value);
    const page = pageForRank(state.snapshot?.rows || [], rank, state.pageSize);
    if (page < 0) {
      input.setCustomValidity('Enter a rank included in this server’s snapshot.');
      input.reportValidity();
      return;
    }
    input.setCustomValidity('');
    // A rank is an absolute server position, so a jump clears the name filter.
    clearTimeout(state.searchTimer);
    $('commander-search').value = '';
    state.query = '';
    state.filtered = state.snapshot.rows;
    updateLocation();
    changePage(page);
  });
  $('rank-number').addEventListener('input', (event) => event.target.setCustomValidity(''));
  $('first-page').addEventListener('click', () => changePage(0));
  $('previous-page').addEventListener('click', () => changePage(state.page - 1));
  $('next-page').addEventListener('click', () => changePage(state.page + 1));
  $('last-page').addEventListener('click', () =>
    changePage(Math.ceil(state.filtered.length / state.pageSize) - 1),
  );
  $('download-csv').addEventListener('click', () => {
    if (!state.server || !state.filtered.length) return;
    const blob = new Blob([rowsToCsv(state.filtered)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${state.server.key}${state.query.trim() ? '.filtered' : ''}.rankings.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hideVehicleTooltip();
    if (
      event.key === '/' &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey &&
      !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) &&
      !document.activeElement?.isContentEditable &&
      !$('commander-search').disabled
    ) {
      event.preventDefault();
      $('commander-search').focus();
    }
  });
  $('vehicle-dialog-close').addEventListener('click', () => $('vehicle-dialog').close());
  $('vehicle-dialog').addEventListener('click', (event) => {
    if (event.target !== $('vehicle-dialog')) return;
    const bounds = $('vehicle-dialog').getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom)
      $('vehicle-dialog').close();
  });
  window.addEventListener('scroll', hideVehicleTooltip, { capture: true, passive: true });
  window.addEventListener('resize', hideVehicleTooltip);
  window.addEventListener('hashchange', () => {
    if (!state.index) return;
    const locationState = rankingRouteFromHash(location.hash);
    if (locationState) loadServer(locationState.key, locationState.query);
  });

  async function initialize() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const [indexResult, catalogResult] = await Promise.allSettled([
        fetchJson('./data/index.json', controller.signal).then(validateIndex),
        fetchJson('./assets/vehicles/catalog.json', controller.signal).then(validateVehicleCatalog),
      ]);
      if (indexResult.status !== 'fulfilled') throw indexResult.reason;
      state.index = indexResult.value;
      state.catalog = catalogResult.status === 'fulfilled' ? catalogResult.value : null;
      clearTimeout(timeout);
      renderIndex();
      const locationState = rankingRouteFromHash(location.hash) || { key: null, query: '' };
      const initial =
        state.index.servers.find((entry) => entry.key === locationState.key)?.key ||
        state.index.servers.find((entry) => entry.key === 'Bonn')?.key ||
        state.index.servers[0].key;
      await loadServer(initial, locationState.query);
    } catch {
      setText('collection-status', 'ARCHIVE UNAVAILABLE');
      setText('index-freshness', 'The archive index could not be loaded.');
      setText('standings-title', 'Archive unavailable');
      setText('results-label', 'The data index could not be loaded');
      $('ranking-rows').replaceChildren(
        messageRow(
          'The archive index could not be loaded.',
          'Check your connection and reload this page.',
        ),
      );
      $('rankings-panel').setAttribute('aria-busy', 'false');
      showNotice(
        'error-notice',
        'The ranking archive could not be loaded. Please reload this page to try again.',
        'error',
      );
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = 'Reload';
      retry.addEventListener('click', () => location.reload());
      $('error-notice').append(retry);
    } finally {
      clearTimeout(timeout);
    }
  }
  initialize();
}

if (typeof document !== 'undefined') browserApp();
