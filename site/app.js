const INDEX_SCHEMA = 'metal-waltz.rankings-index.v1';
const SNAPSHOT_SCHEMAS = new Set([
  'maiden-waltz.public-ranking-snapshot.v1',
  'metal-waltz.public-ranking-snapshot.v1',
]);
const PAGE_SIZES = new Set([25, 50, 100, 200]);
const numberFormat = new Intl.NumberFormat('en-US');

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
      !(row.name === null || typeof row.name === 'string') ||
      typeof row.nameStatus !== 'string'
    )
      throw new Error('The snapshot contains an invalid ranking row.');
    if (hasName(row)) resolved++;
  }
  if (resolved !== server.namesResolved)
    throw new Error('The snapshot name count does not match its archive index.');
  return data;
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

  function queryFromLocation() {
    const parameters = new URLSearchParams(location.hash.slice(1));
    return { key: parameters.get('server'), query: (parameters.get('q') || '').slice(0, 512) };
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
    td.colSpan = 3;
    td.textContent = title;
    if (detail) {
      const paragraph = document.createElement('p');
      paragraph.textContent = detail;
      td.append(paragraph);
    }
    tr.append(td);
    return tr;
  }

  function renderRows() {
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
      tr.append(rank, name, grandeur);
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

  async function loadServer(key, query) {
    const server = state.index.servers.find((entry) => entry.key === key);
    if (!server) return;
    if (state.server?.key === key && !state.loading) {
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
      `Loading ${server.name}…${hadSnapshot ? ` The displayed standings are still ${state.server.name}.` : ''}`,
    );
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const snapshot = validateSnapshot(
        await fetchJson(safeSnapshotPath(server.file), controller.signal),
        server,
      );
      if (sequence !== state.sequence) return;
      state.server = server;
      state.snapshot = snapshot;
      state.query = query !== undefined ? query : $('commander-search').value.slice(0, 512);
      $('commander-search').value = state.query;
      state.filtered = filterRows(snapshot.rows, state.query);
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
      showNotice('error-notice', `Could not load ${server.name}.${suffix}`, 'error');
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = 'Retry';
      retry.addEventListener('click', () => loadServer(key, query));
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
  window.addEventListener('hashchange', () => {
    if (!state.index) return;
    const locationState = queryFromLocation();
    loadServer(locationState.key || 'Bonn', locationState.query);
  });

  async function initialize() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      state.index = validateIndex(await fetchJson('./data/index.json', controller.signal));
      clearTimeout(timeout);
      renderIndex();
      const locationState = queryFromLocation();
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
