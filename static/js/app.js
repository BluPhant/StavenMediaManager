// ─────────────────────────────────────────────
// API helpers
// ─────────────────────────────────────────────
const API = {
  async get(path) {
    const r = await fetch('/api' + path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async post(path, body) {
    const r = await fetch('/api' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      let msg = await r.text();
      try { msg = JSON.parse(msg).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  },
  async del(path) {
    const r = await fetch('/api' + path, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
};

// ─────────────────────────────────────────────
// Category → icon mapping
// ─────────────────────────────────────────────
const ICONS = [
  [/audiobook/i, 'bi-book-half',         'text-warning'],
  [/music/i,     'bi-music-note-beamed', 'text-danger'],
  [/movie/i,     'bi-film',             'text-info'],
  [/switch/i,    'bi-joystick',         'text-success'],
  [/pc.?game/i,  'bi-pc-display',       'text-primary'],
  [/game/i,      'bi-controller',       'text-success'],
  [/tv|show/i,   'bi-tv',               'text-info'],
  [/comic/i,     'bi-book',             'text-warning'],
  [/ebook/i,     'bi-journal-text',     'text-warning'],
  [/software/i,  'bi-floppy',           'text-secondary'],
];

function categoryIcon(name) {
  for (const [re, icon, color] of ICONS) {
    if (re.test(name)) return { icon, color };
  }
  return { icon: 'bi-folder2-open', color: 'text-secondary' };
}

// ─────────────────────────────────────────────
// File extension → icon
// ─────────────────────────────────────────────
const FILE_ICONS = {
  mkv:  'bi-file-play text-success',
  mp4:  'bi-file-play text-success',
  avi:  'bi-file-play text-success',
  mov:  'bi-file-play text-success',
  mp3:  'bi-file-music text-danger',
  flac: 'bi-file-music text-danger',
  m4a:  'bi-file-music text-danger',
  m4b:  'bi-file-music text-warning',
  aac:  'bi-file-music text-danger',
  rar:  'bi-file-zip text-info',
  zip:  'bi-file-zip text-info',
  '7z': 'bi-file-zip text-info',
  nfo:  'bi-file-text text-secondary',
  jpg:  'bi-file-image',
  jpeg: 'bi-file-image',
  png:  'bi-file-image',
  xci:  'bi-sd-card text-success',
  nsp:  'bi-sd-card text-success',
  iso:  'bi-disc text-primary',
  exe:  'bi-file-binary text-warning',
};

function fileIcon(name, isDir) {
  if (isDir) return 'bi-folder text-warning';
  const ext = name.split('.').pop().toLowerCase();
  return FILE_ICONS[ext] || 'bi-file text-secondary';
}

// ─────────────────────────────────────────────
// Views
// ─────────────────────────────────────────────
const Views = {
  _setApp(html) {
    document.getElementById('app').innerHTML = html;
  },

  _loading() {
    this._setApp('<div class="text-center py-5"><div class="spinner-border text-secondary"></div></div>');
  },

  async home() {
    this._loading();
    let cats, history, syncStatus;
    try {
      [cats, history, syncStatus] = await Promise.all([
        API.get('/categories'),
        API.get('/jobs?job_type=move&status=done&limit=20'),
        API.get('/sources/status').catch(() => null),
      ]);
    } catch (e) {
      this._setApp(`<div class="alert alert-danger mt-2">Failed to load: ${esc(e.message)}</div>`);
      return;
    }

    // ── Sync bar (shown only when rTorrent is configured) ──────────────────
    let syncHtml = '';
    if (syncStatus && syncStatus.rtorrent && syncStatus.rtorrent.configured) {
      const rt = syncStatus.rtorrent;
      syncHtml = `
        <div class="d-flex align-items-center gap-3 mb-4 p-3 rounded border border-secondary"
             style="background:rgba(255,255,255,.03)">
          <i class="bi bi-cloud-download text-info fs-5"></i>
          <div class="flex-grow-1">
            <span class="fw-semibold small">Seedbox Sync</span>
            <span class="text-secondary small ms-2">
              tag: <code class="text-info">${esc(rt.tag)}</code>
              &nbsp;·&nbsp; ${esc(rt.ssh_host)}
            </span>
          </div>
          <button class="btn btn-sm btn-outline-info" onclick="Actions.sync()">
            <i class="bi bi-arrow-repeat me-1"></i>Sync Now
          </button>
          <button class="btn btn-sm btn-outline-secondary" onclick="Actions.previewSync()">
            <i class="bi bi-eye me-1"></i>Preview
          </button>
        </div>`;
    }

    // ── Category grid ──────────────────────────────────────────────────────
    let catHtml = '';
    if (!cats.length) {
      catHtml = `
        <div class="text-center py-5 empty-state">
          <i class="bi bi-folder-x"></i>
          <p class="mt-3">No categories found in the incoming directory.<br>
          <small class="text-secondary">Create subdirectories inside the mounted incoming path.</small></p>
        </div>`;
    } else {
      const cards = cats.map(c => {
        const { icon, color } = categoryIcon(c.name);
        return `
          <div class="col-6 col-sm-4 col-md-3 col-xl-2">
            <div class="card category-card text-center p-3 h-100"
                 onclick="Router.go('/category/${enc(c.name)}')">
              <div class="category-icon ${color}"><i class="bi ${icon}"></i></div>
              <div class="fw-semibold mt-2">${esc(c.name)}</div>
              <div class="text-secondary small mt-1">${c.item_count} item${c.item_count !== 1 ? 's' : ''}</div>
            </div>
          </div>`;
      }).join('');
      catHtml = `
        <h6 class="text-secondary mb-3 text-uppercase" style="letter-spacing:.08em">Incoming Categories</h6>
        <div class="row g-3 mb-4">${cards}</div>`;
    }

    // ── Processed history ──────────────────────────────────────────────────
    let histHtml = '';
    if (history.length) {
      const rows = history.map(j => {
        const title = j.dest_path ? j.dest_path.split('/').filter(Boolean).pop() : j.item_name;
        const dest  = j.dest_path || '—';
        const date  = j.created_at ? new Date(j.created_at).toLocaleDateString() : '—';
        return `
          <tr>
            <td class="fw-semibold">${esc(title)}</td>
            <td class="text-secondary text-capitalize">${esc(j.category)}</td>
            <td class="text-secondary font-monospace small">${esc(dest)}</td>
            <td class="text-secondary text-nowrap">${date}</td>
            <td>
              <button class="btn btn-sm btn-link text-secondary p-0"
                      onclick="JobsPanel.remove(${j.id})" title="Remove from history">
                <i class="bi bi-x-lg"></i>
              </button>
            </td>
          </tr>`;
      }).join('');
      histHtml = `
        <h6 class="text-secondary mb-3 text-uppercase" style="letter-spacing:.08em">
          <i class="bi bi-check2-circle me-2"></i>Processed
        </h6>
        <div class="table-responsive">
          <table class="table table-dark table-hover file-table">
            <thead class="text-secondary">
              <tr><th>Title</th><th>Category</th><th>Destination</th><th>Date</th><th></th></tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    }

    this._setApp(syncHtml + catHtml + histHtml);
  },

  async category(name) {
    this._loading();

    // Fetch items; for movie categories also fetch existing matches in parallel
    const isMovies = /movie/i.test(name);
    let items, matchMap = {};
    try {
      const fetches = [API.get(`/categories/${enc(name)}/items`)];
      if (isMovies) fetches.push(API.get(`/movies/matches?category=${enc(name)}`));
      [items, matchMap = {}] = await Promise.all(fetches);
    } catch (e) {
      this._setApp(`<div class="alert alert-danger mt-2">Failed to load items: ${esc(e.message)}</div>`);
      return;
    }

    const crumb = breadcrumb([['Home', '#/'], [esc(name)]]);

    if (!items.length) {
      this._setApp(crumb + `
        <div class="text-center py-5 empty-state">
          <i class="bi bi-folder-x"></i>
          <p class="mt-3">No item folders found in <strong>${esc(name)}</strong>.</p>
        </div>`);
      return;
    }

    const rows = items.map(it => {
      const match = matchMap[it.name];
      return `
        <tr class="item-row" onclick="Router.go('/category/${enc(name)}/${enc(it.name)}')">
          <td>
            <i class="bi bi-folder me-2 text-warning"></i>${esc(it.name)}
            ${match ? `<span class="ms-2 text-success small" title="${esc(match.formatted_name)}"><i class="bi bi-check-circle-fill"></i></span>` : ''}
          </td>
          <td class="text-secondary text-nowrap">${it.size_human}</td>
          <td class="text-nowrap">
            ${it.has_rar ? '<span class="badge bg-info badge-rar me-1"><i class="bi bi-archive me-1"></i>RAR</span>' : ''}
            ${match ? `<span class="badge bg-success badge-rar">${esc(match.formatted_name)}</span>` : ''}
          </td>
        </tr>`;
    }).join('');

    this._setApp(crumb + `
      <div class="d-flex align-items-baseline gap-2 mb-3">
        <h5 class="mb-0">${esc(name)}</h5>
        <span class="text-secondary small">${items.length} item${items.length !== 1 ? 's' : ''}</span>
      </div>
      <div class="table-responsive">
        <table class="table table-hover table-dark file-table">
          <thead><tr class="text-secondary">
            <th>Name</th><th>Size</th><th></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  },

  async item(category, itemName) {
    this._loading();
    const isMovies = /movie/i.test(category);
    let detail, matchData;
    try {
      const fetches = [API.get(`/categories/${enc(category)}/items/${enc(itemName)}`)];
      if (isMovies) fetches.push(API.get(`/movies/match?category=${enc(category)}&item=${enc(itemName)}`));
      [detail, matchData] = await Promise.all(fetches);
    } catch (e) {
      this._setApp(`<div class="alert alert-danger mt-2">Failed to load item: ${esc(e.message)}</div>`);
      return;
    }

    const crumb = breadcrumb([
      ['Home', '#/'],
      [esc(category), `#/category/${enc(category)}`],
      [esc(itemName)],
    ]);

    const hasMatch = !!(matchData && matchData.match);
    const actionBtns = [];
    if (detail.has_rar) {
      actionBtns.push(`
        <button class="btn btn-primary btn-sm"
                onclick="Actions.extract(${jsStr(category)}, ${jsStr(itemName)})">
          <i class="bi bi-archive me-1"></i>Extract RAR
        </button>`);
    }
    if (isMovies) {
      actionBtns.push(`
        <button class="btn btn-success btn-sm" id="btn-move-library"
                ${!hasMatch ? 'disabled title="Save an IMDB match first"' : ''}
                onclick="Actions.move(${jsStr(category)}, ${jsStr(itemName)})">
          <i class="bi bi-box-arrow-right me-1"></i>Move to Library
        </button>`);
    }
    const actionsHtml = actionBtns.length ? `
      <div class="mb-3 d-flex gap-2 flex-wrap">${actionBtns.join('')}</div>` : '';

    const fileRows = detail.files.map(f => `
      <tr>
        <td><i class="bi ${fileIcon(f.name, f.is_dir)} me-2"></i>${esc(f.name)}</td>
        <td class="text-secondary text-nowrap">${f.is_dir ? '—' : f.size_human}</td>
      </tr>`).join('');

    this._setApp(crumb + (isMovies ? _matchPanelHtml() : '') + actionsHtml + `
      <div class="table-responsive">
        <table class="table table-hover table-dark file-table">
          <thead><tr class="text-secondary"><th>File</th><th>Size</th></tr></thead>
          <tbody>${fileRows}</tbody>
        </table>
      </div>`);

    if (isMovies) {
      MovieMatch.init(category, itemName, matchData);
    }
  },
};

// ─────────────────────────────────────────────
// Movie match panel HTML template
// ─────────────────────────────────────────────
function _matchPanelHtml() {
  return `
    <div class="card border-secondary mt-4" id="match-panel">
      <div class="card-header d-flex justify-content-between align-items-center py-2">
        <span class="small fw-semibold"><i class="bi bi-film me-2 text-info"></i>IMDB Match</span>
        <div id="match-status"></div>
      </div>
      <div class="card-body pb-2">
        <div id="current-match"></div>
        <div class="input-group input-group-sm mt-2">
          <input type="text" id="match-query" class="form-control bg-dark text-white border-secondary"
                 placeholder="Title to search…"
                 onkeydown="if(event.key==='Enter') MovieMatch.search()">
          <input type="number" id="match-year" class="form-control bg-dark text-white border-secondary"
                 placeholder="Year" style="max-width:90px"
                 onkeydown="if(event.key==='Enter') MovieMatch.search()">
          <button class="btn btn-outline-secondary" onclick="MovieMatch.search()">
            <i class="bi bi-search me-1"></i>Search
          </button>
        </div>
        <div id="match-results" class="row g-2 mt-2"></div>
      </div>
    </div>`;
}

// ─────────────────────────────────────────────
// MovieMatch — IMDB/TMDb lookup & persistence
// ─────────────────────────────────────────────
const MovieMatch = {
  _category: null,
  _item: null,

  async init(category, item, prefetchedData) {
    this._category = category;
    this._item = item;

    let data = prefetchedData;
    if (!data) {
      try {
        data = await API.get(`/movies/match?category=${enc(category)}&item=${enc(item)}`);
      } catch (e) {
        _matchEl('match-results').innerHTML =
          `<div class="col-12"><div class="text-danger small">${esc(e.message)}</div></div>`;
        return;
      }
    }

    _matchEl('match-query').value = data.suggested_query || '';
    if (data.suggested_year) _matchEl('match-year').value = data.suggested_year;

    if (data.match) {
      this._renderMatch(data.match);
    } else if (data.suggested_query) {
      this.search();
    }
  },

  async search() {
    const q = (_matchEl('match-query').value || '').trim();
    if (!q) return;
    const year = _matchEl('match-year').value;

    const resultsEl = _matchEl('match-results');
    resultsEl.innerHTML = `
      <div class="col-12 text-secondary small py-2">
        <span class="spinner-border spinner-border-sm me-2"></span>Searching TMDb…
      </div>`;

    let results;
    try {
      results = await API.get(`/movies/search?q=${enc(q)}${year ? `&year=${enc(year)}` : ''}`);
    } catch (e) {
      resultsEl.innerHTML =
        `<div class="col-12"><div class="text-danger small">${esc(e.message)}</div></div>`;
      return;
    }

    if (!results.length) {
      resultsEl.innerHTML = '<div class="col-12 text-secondary small">No results found. Try adjusting the title or year.</div>';
      return;
    }

    resultsEl.innerHTML = results.map(r => `
      <div class="col-6 col-md-4 col-lg-3 col-xl-2">
        <div class="card h-100 match-result-card"
             onclick="MovieMatch.select(${r.tmdb_id}, ${jsStr(r.title)}, ${r.year ?? 'null'},
                      ${jsStr(r.poster_url || '')}, ${jsStr(r.overview || '')}, ${jsStr(r.formatted_name)})">
          ${r.poster_url
            ? `<img src="${esc(r.poster_url)}" class="card-img-top" alt=""
                    style="aspect-ratio:2/3;object-fit:cover">`
            : `<div class="card-img-top d-flex align-items-center justify-content-center bg-dark"
                    style="aspect-ratio:2/3"><i class="bi bi-film text-secondary" style="font-size:2rem"></i></div>`
          }
          <div class="card-body p-2">
            <div class="small fw-semibold lh-sm">${esc(r.title)}</div>
            <div class="text-secondary" style="font-size:.75rem">${r.year ?? '—'}</div>
          </div>
        </div>
      </div>`).join('');
  },

  async select(tmdbId, title, year, posterUrl, overview, formattedName) {
    try {
      const match = await API.post('/movies/match', {
        category: this._category,
        item_name: this._item,
        tmdb_id: tmdbId,
        title,
        year,
        poster_url: posterUrl || null,
        overview: overview || null,
        formatted_name: formattedName,
      });
      this._renderMatch(match);
      _matchEl('match-results').innerHTML = '';
      toast(`Matched: ${formattedName}`, 'success');
    } catch (e) {
      toast(`Failed to save match: ${e.message}`, 'danger');
    }
  },

  async clear() {
    try {
      await API.del(`/movies/match?category=${enc(this._category)}&item=${enc(this._item)}`);
      _matchEl('current-match').innerHTML = '';
      _matchEl('match-status').innerHTML = '';
      const moveBtn = document.getElementById('btn-move-library');
      if (moveBtn) { moveBtn.disabled = true; moveBtn.title = 'Save an IMDB match first'; }
      toast('Match cleared', 'secondary');
    } catch (e) {
      toast(e.message, 'danger');
    }
  },

  _renderMatch(match) {
    _matchEl('match-status').innerHTML =
      '<span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>Matched</span>';
    const moveBtn = document.getElementById('btn-move-library');
    if (moveBtn) { moveBtn.disabled = false; moveBtn.title = ''; }

    _matchEl('current-match').innerHTML = `
      <div class="d-flex gap-3 align-items-start p-2 rounded mb-2
                  bg-success bg-opacity-10 border border-success border-opacity-25">
        ${match.poster_url
          ? `<img src="${esc(match.poster_url)}" alt="" class="flex-shrink-0 rounded"
                  style="width:54px;object-fit:cover">`
          : `<div class="flex-shrink-0 rounded bg-dark d-flex align-items-center justify-content-center"
                  style="width:54px;height:81px"><i class="bi bi-film text-secondary"></i></div>`
        }
        <div class="flex-grow-1 min-w-0">
          <div class="fw-semibold">${esc(match.formatted_name)}</div>
          ${match.overview
            ? `<div class="text-secondary mt-1" style="font-size:.8rem;line-height:1.4">${esc(match.overview)}</div>`
            : ''}
          <div class="mt-2">
            <code class="small text-success">${esc(match.formatted_name)}</code>
            <span class="text-secondary small ms-1">← directory name to use</span>
          </div>
        </div>
        <button class="btn btn-sm btn-outline-danger flex-shrink-0"
                onclick="MovieMatch.clear()" title="Clear match">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>`;
  },
};

function _matchEl(id) { return document.getElementById(id); }

// ─────────────────────────────────────────────
// Actions
// ─────────────────────────────────────────────
const Actions = {
  async extract(category, itemName) {
    try {
      const job = await API.post('/jobs/extract', { category, item_name: itemName });
      JobPoller.track(job.id, { type: 'extract', category, itemName });
      JobsPanel.open();
      toast(`Extraction started — Job #${job.id}`, 'success');
    } catch (e) {
      toast(`Could not start extraction: ${e.message}`, 'danger');
    }
  },

  async move(category, itemName) {
    try {
      const job = await API.post('/jobs/move', { category, item_name: itemName });
      JobPoller.track(job.id, { type: 'move', category, itemName });
      JobsPanel.open();
      toast(`Move started — Job #${job.id}`, 'success');
    } catch (e) {
      toast(`Could not start move: ${e.message}`, 'danger');
    }
  },

  async sync() {
    try {
      const job = await API.post('/sources/sync', {});
      JobPoller.track(job.id, { type: 'sync' });
      JobsPanel.open();
      toast(`Sync started — Job #${job.id}`, 'info');
    } catch (e) {
      toast(`Could not start sync: ${e.message}`, 'danger');
    }
  },

  async previewSync() {
    try {
      const items = await API.get('/sources/preview');
      if (!items.length) { toast('Nothing ready to import.', 'secondary'); return; }
      const rows = items.map(it => `
        <tr>
          <td>${esc(it.name)}</td>
          <td><span class="badge bg-secondary">${esc(it.suggested_type)}</span></td>
          <td class="text-secondary small">${esc(it.label)}</td>
          <td class="text-secondary small text-nowrap">${_humanSize(it.size_bytes)}</td>
        </tr>`).join('');
      const html = `
        <div class="modal fade" id="preview-modal" tabindex="-1">
          <div class="modal-dialog modal-lg modal-dialog-scrollable">
            <div class="modal-content bg-dark text-white border-secondary">
              <div class="modal-header border-secondary py-2">
                <h6 class="modal-title mb-0"><i class="bi bi-cloud-download me-2 text-info"></i>Ready to import (${items.length})</h6>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
              </div>
              <div class="modal-body p-0">
                <table class="table table-dark table-hover file-table mb-0">
                  <thead class="text-secondary"><tr><th>Name</th><th>Type</th><th>Label</th><th>Size</th></tr></thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>
              <div class="modal-footer border-secondary py-2">
                <button class="btn btn-info btn-sm" onclick="Actions.sync(); bootstrap.Modal.getInstance(document.getElementById('preview-modal')).hide()">
                  <i class="bi bi-arrow-repeat me-1"></i>Sync Now
                </button>
                <button class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>
              </div>
            </div>
          </div>
        </div>`;
      document.getElementById('app').insertAdjacentHTML('beforeend', html);
      const modal = new bootstrap.Modal(document.getElementById('preview-modal'));
      document.getElementById('preview-modal').addEventListener('hidden.bs.modal', e => e.target.remove());
      modal.show();
    } catch (e) {
      toast(`Preview failed: ${e.message}`, 'danger');
    }
  },
};

// ─────────────────────────────────────────────
// Jobs panel
// ─────────────────────────────────────────────
const JobsPanel = {
  open() {
    document.getElementById('jobs-panel-body').style.display = 'block';
    document.getElementById('jobs-chevron').className = 'bi bi-chevron-down';
  },

  toggle() {
    const body = document.getElementById('jobs-panel-body');
    const chevron = document.getElementById('jobs-chevron');
    const isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : 'block';
    chevron.className = isOpen ? 'bi bi-chevron-up' : 'bi bi-chevron-down';
  },

  async refresh() {
    let jobs;
    try {
      jobs = await API.get('/jobs?limit=30');
    } catch (_) { return; }

    const active = jobs.filter(j => j.status === 'pending' || j.status === 'running');
    const badge = document.getElementById('jobs-badge');
    document.getElementById('active-job-count').textContent = active.length;
    badge.classList.toggle('d-none', active.length === 0);

    const list = document.getElementById('jobs-list');
    if (!jobs.length) {
      list.innerHTML = '<p class="text-secondary small my-2">No recent jobs.</p>';
      return;
    }

    const statusColor = {
      pending: 'warning', running: 'info', done: 'success', error: 'danger', cancelled: 'secondary',
    };

    list.innerHTML = jobs.map(j => {
      const color = statusColor[j.status] || 'secondary';
      const isActive = j.status === 'pending' || j.status === 'running';
      return `
        <div class="job-item" id="job-item-${j.id}">
          <div class="d-flex justify-content-between align-items-start gap-2">
            <div class="flex-grow-1 min-width-0">
              <span class="badge bg-${color} me-1">${j.status}</span>
              <span class="small fw-semibold">${esc(j.item_name)}</span>
              <span class="text-secondary small ms-1">[${esc(j.type)}]</span>
            </div>
            ${!isActive ? `
              <button class="btn btn-sm btn-link text-secondary p-0 flex-shrink-0"
                      onclick="JobsPanel.remove(${j.id})" title="Dismiss">
                <i class="bi bi-x-lg"></i>
              </button>` : ''}
          </div>
          ${isActive ? `
            <div class="progress mt-2">
              <div class="progress-bar progress-bar-striped progress-bar-animated bg-${color}"
                   style="width:${j.progress}%" role="progressbar"></div>
            </div>
            <small class="text-secondary d-block mt-1">
              ${j.progress}%${j.message ? ' — ' + esc(j.message) : ''}
            </small>` : (j.message ? `<small class="text-secondary d-block mt-1">${esc(j.message)}</small>` : '')}
        </div>`;
    }).join('');
  },

  async remove(id) {
    try {
      await API.del(`/jobs/${id}`);
      this.refresh();
    } catch (e) {
      toast(e.message, 'danger');
    }
  },
};

// ─────────────────────────────────────────────
// Job poller
// ─────────────────────────────────────────────
const JobPoller = {
  _fastTimer: null,
  _tracked: new Map(), // id → {type, category, itemName}

  track(jobId, ctx = {}) {
    this._tracked.set(jobId, ctx);
    this._startFast();
  },

  init() {
    setInterval(() => JobsPanel.refresh(), 8000);
  },

  _startFast() {
    if (this._fastTimer) return;
    this._fastTimer = setInterval(() => this._tick(), 2000);
  },

  async _tick() {
    await JobsPanel.refresh();
    if (!this._tracked.size) return;
    try {
      const active = await API.get('/jobs?active_only=true');
      const activeIds = new Set(active.map(j => j.id));
      for (const [id, ctx] of [...this._tracked]) {
        if (!activeIds.has(id)) {
          this._tracked.delete(id);
          this._onComplete(id, ctx);
        }
      }
    } catch (_) {}
    if (!this._tracked.size) {
      clearInterval(this._fastTimer);
      this._fastTimer = null;
      await JobsPanel.refresh();
    }
  },

  async _onComplete(id, ctx) {
    try {
      const job = await API.get(`/jobs/${id}`);
      if (job.status !== 'done') return;
      const itemHash = `#/category/${enc(ctx.category)}/${enc(ctx.itemName)}`;
      if (location.hash !== itemHash) return;
      if (ctx.type === 'extract') {
        Router.refresh();
      } else if (ctx.type === 'move') {
        Router.go(`/category/${enc(ctx.category)}`);
      }
    } catch (_) {}
  },
};

// ─────────────────────────────────────────────
// Hash router
// ─────────────────────────────────────────────
const Router = {
  async route() {
    const hash = (location.hash || '#/').slice(1);
    const parts = hash.split('/').filter(Boolean);

    if (!parts.length || parts[0] !== 'category') {
      await Views.home();
    } else if (parts.length === 2) {
      await Views.category(decodeURIComponent(parts[1]));
    } else if (parts.length >= 3) {
      await Views.item(decodeURIComponent(parts[1]), decodeURIComponent(parts[2]));
    } else {
      await Views.home();
    }
  },

  go(path) { location.hash = path; },
  refresh() { this.route(); },
};

// ─────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────
function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function enc(str) { return encodeURIComponent(str); }

function jsStr(str) {
  return `'${String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

function _humanSize(bytes) {
  if (!bytes) return '—';
  const units = ['B','KB','MB','GB','TB'];
  let i = 0; let v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function breadcrumb(crumbs) {
  const items = crumbs.map(([label, href], i) => {
    if (i === crumbs.length - 1) return `<li class="breadcrumb-item active">${label}</li>`;
    return `<li class="breadcrumb-item"><a href="${href}">${label}</a></li>`;
  }).join('');
  return `<nav aria-label="breadcrumb" class="mb-3"><ol class="breadcrumb">${items}</ol></nav>`;
}

function toast(msg, type = 'info') {
  const id = 'toast-' + Date.now();
  document.getElementById('toast-container').insertAdjacentHTML('beforeend', `
    <div id="${id}" class="toast align-items-center text-bg-${type} border-0 show" role="alert">
      <div class="d-flex">
        <div class="toast-body">${esc(msg)}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto"
                data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
    </div>`);
  const el = document.getElementById(id);
  new bootstrap.Toast(el, { delay: 4500 }).show();
  el.addEventListener('hidden.bs.toast', () => el.remove());
}

// ─────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────
window.addEventListener('hashchange', () => Router.route());
window.addEventListener('load', () => {
  Router.route();
  JobsPanel.refresh();
  JobPoller.init();
});
