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

    // ── Sync bar + active torrents (shown only when rTorrent is configured) ──
    let syncHtml = '';
    if (syncStatus && syncStatus.rtorrent && syncStatus.rtorrent.configured) {
      const rt = syncStatus.rtorrent;

      // Fetch active (in-progress) torrents from seedbox
      let activeHtml = '';
      try {
        const active = await API.get('/sources/active');
        if (active.length) {
          const rows = active.map(t => {
            const pct    = t.pct;
            const done   = _humanSize(t.bytes_done);
            const total  = _humanSize(t.size_bytes);
            const speed  = t.down_rate > 0 ? `<span class="text-success small">${_humanSize(t.down_rate)}/s</span>` : '';
            const barCls = t.is_active ? 'bg-info progress-bar-animated progress-bar-striped' : 'bg-secondary';
            return `
              <div class="mb-2">
                <div class="d-flex justify-content-between align-items-baseline mb-1">
                  <span class="small text-truncate me-2" style="max-width:55%">${esc(t.name)}</span>
                  <span class="text-secondary small text-nowrap">${done} / ${total} &nbsp; ${speed}</span>
                  <button class="btn btn-sm btn-outline-danger ms-2 py-0 px-1" style="font-size:.7rem;line-height:1.4"
                          onclick="Actions.stopTorrent('${esc(t.hash)}', this)" title="Stop torrent">
                    <i class="bi bi-stop-fill"></i>
                  </button>
                </div>
                <div class="progress" style="height:6px">
                  <div class="progress-bar ${barCls}" style="width:${pct}%" role="progressbar"></div>
                </div>
              </div>`;
          }).join('');

          activeHtml = `
            <div class="mb-3 p-3 rounded border border-secondary" style="background:rgba(255,255,255,.02)">
              <div class="text-secondary small mb-2 text-uppercase fw-semibold" style="letter-spacing:.06em">
                <i class="bi bi-arrow-down-circle me-1 text-info"></i>Seedbox Downloading (${active.length})
              </div>
              ${rows}
            </div>`;
        }
      } catch (_) {}

      syncHtml = `
        <div class="d-flex align-items-center gap-3 mb-3 p-3 rounded border border-secondary"
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
        </div>
        ${activeHtml}`;
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

  toggleLowQ() {
    if (!this._cachedMovieResults) return;
    this._showLowQ = !this._showLowQ;
    const mf = _smartMovieFilter(this._cachedMovieResults, this._showLowQ);
    this._renderResults({
      results:    mf.results.map(r => ({ ...r, _source: 'ipt' })),
      _movieTier: mf.tierLabel,
      _lowQCount: mf.lowQCount,
      query_used: this._lastQuery,
      year:       null,
      attempts:   [this._lastQuery],
    }, 'movies');
  },

  async stopTorrent(hash, btn) {
    if (!confirm('Stop this torrent on the seedbox?')) return;
    btn.disabled = true;
    try {
      await API.post(`/sources/torrent/${encodeURIComponent(hash)}/stop`, {});
      toast('Torrent stopped.', 'warning');
      // Refresh the home page widget so the row updates
      const row = btn.closest('.mb-2');
      if (row) {
        row.style.opacity = '0.4';
        row.style.pointerEvents = 'none';
      }
    } catch (e) {
      toast(`Stop failed: ${e.message}`, 'danger');
      btn.disabled = false;
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
            ${isActive ? `
              <button class="btn btn-sm btn-link text-danger p-0 flex-shrink-0"
                      onclick="JobsPanel.cancel(${j.id})" title="Cancel job">
                <i class="bi bi-stop-circle"></i>
              </button>` : `
              <button class="btn btn-sm btn-link text-secondary p-0 flex-shrink-0"
                      onclick="JobsPanel.remove(${j.id})" title="Dismiss">
                <i class="bi bi-x-lg"></i>
              </button>`}
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

  async cancel(id) {
    try {
      await API.post(`/jobs/${id}/cancel`, {});
      toast('Cancel requested — job will stop shortly.', 'warning');
      this.refresh();
    } catch (e) {
      toast(e.message, 'danger');
    }
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
// IPTorrents search view
// ─────────────────────────────────────────────
const IPTSearch = {
  _lastQuery: '',
  _lastCat: 'ipt:all',
  _showLowQ: false,           // toggle: include CAM/TS/Screener in movie results
  _cachedMovieResults: null,  // raw results before movie filter (for toggling)

  // Dropdown options: value is "source:category"
  _catOptions() {
    const sel = this._lastCat;
    const o = (v, label) => `<option value="${v}"${sel===v?' selected':''}>${label}</option>`;
    return [
      o('ipt:all',        'All · IPT'),
      o('ipt:movies',     'Movies · IPT'),
      o('ipt:tv',         'TV · IPT'),
      o('btn:tv',         'TV · BTN'),
      o('ipt:music',      'Music · IPT'),
      o('ipt:audiobooks', 'Audiobooks · IPT'),
      o('ipt:games',      'Games · IPT'),
      o('ipt:ebooks',     'Ebooks · IPT'),
      o('ipt:software',   'Software · IPT'),
    ].join('');
  },

  async render() {
    Views._setApp(`
      <div class="d-flex align-items-center gap-2 mb-4">
        <i class="bi bi-search text-info fs-5"></i>
        <h5 class="mb-0">Torrent Search</h5>
      </div>
      <div class="row g-2 mb-3">
        <div class="col-12 col-md-6">
          <input type="text" id="ipt-query" class="form-control bg-dark text-white border-secondary"
                 placeholder="Title to search…"
                 value="${esc(this._lastQuery)}"
                 onkeydown="if(event.key==='Enter') IPTSearch.search()">
        </div>
        <div class="col-auto">
          <select id="ipt-cat" class="form-select bg-dark text-white border-secondary">
            ${this._catOptions()}
          </select>
        </div>
        <div class="col-auto">
          <button class="btn btn-info" onclick="IPTSearch.search()">
            <i class="bi bi-search me-1"></i>Search
          </button>
        </div>
      </div>
      <div id="ipt-results"></div>`);

    // Auto-search if we have a prior query
    if (this._lastQuery) this.search();
  },

  async search() {
    const q      = (document.getElementById('ipt-query')?.value || '').trim();
    const srcCat = document.getElementById('ipt-cat')?.value || 'ipt:all';
    this._lastQuery = q;
    this._lastCat   = srcCat;

    const [source, cat] = srcCat.split(':');

    const el = document.getElementById('ipt-results');
    if (!el) return;
    el.innerHTML = `<div class="text-center py-4"><span class="spinner-border text-info spinner-border-sm me-2"></span>Searching…</div>`;

    let data;
    try {
      if (source === 'btn') {
        const raw = await API.get(`/btn/search?q=${enc(q)}&limit=50`);
        // Normalise BTN results to the same display shape as IPT smart-search
        data = {
          results: raw.map(r => ({
            torrent_id:     r.torrent_id,
            title:          r.title,
            size_bytes:     r.size_bytes,
            seeders:        r.seeders,
            leechers:       r.leechers,
            ipt_category:   [r.source, r.resolution, r.codec].filter(Boolean).join(' · '),
            suggested_type: 'tv',
            torrent_url:    r.torrent_url,
            info_url:       r.info_url,
            pubdate:        r.pubdate,
            _source:        'btn',
          })),
          query_used: q || null,
          year:       null,
          attempts:   [q],
        };
      } else {
        data = await API.get(`/iptorrents/smart-search?q=${enc(q)}&cat=${enc(cat)}&limit=50`);
        data.results.forEach(r => { r._source = 'ipt'; });
        if (cat === 'movies' && data.results.length) {
          this._showLowQ = false;
          this._cachedMovieResults = data.results;
          const mf = _smartMovieFilter(data.results, false);
          data.results    = mf.results;
          data._movieTier = mf.tierLabel;
          data._lowQCount = mf.lowQCount;
        } else {
          this._cachedMovieResults = null;
        }
      }
    } catch (e) {
      el.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
      return;
    }

    this._renderResults(data, cat);
  },

  _renderResults(data, cat) {
    const el = document.getElementById('ipt-results');
    if (!el) return;

    const { results, query_used, year, attempts } = data;

    // ── Movie resolution filter badge ────────────────────────────────────────
    let infoHtml = '';
    if (cat === 'movies') {
      const tierColor = data._movieTier === '2160p' ? 'text-warning' : data._movieTier === '1080p' ? 'text-info' : 'text-secondary';
      const tierLabel = data._movieTier || 'all';
      const lowQCount = data._lowQCount || 0;
      const lowQNote  = lowQCount > 0 && !this._showLowQ
        ? `&nbsp;·&nbsp; <span class="text-secondary">${lowQCount} CAM/TS/Screener excluded</span>
           <button class="btn btn-link btn-sm text-secondary p-0 ms-1" style="font-size:.75rem"
                   onclick="IPTSearch.toggleLowQ()">show&nbsp;anyway</button>`
        : lowQCount > 0 && this._showLowQ
        ? `&nbsp;·&nbsp; <span class="text-warning">CAM/TS/Screener visible</span>
           <button class="btn btn-link btn-sm text-secondary p-0 ms-1" style="font-size:.75rem"
                   onclick="IPTSearch.toggleLowQ()">hide</button>`
        : '';
      infoHtml += `
        <div class="alert alert-secondary py-2 small mb-2" style="border-color:#444">
          <i class="bi bi-funnel-fill me-1 ${tierColor}"></i>
          ${data._movieTier
            ? `Filtered to <strong class="${tierColor}">${esc(tierLabel)}</strong> · sorted by size · best per GB`
            : 'No 2160p or 1080p found — showing all'}
          ${lowQNote}
        </div>`;
    }

    // ── Cascade fallback info ────────────────────────────────────────────────
    if (attempts && attempts.length > 1 && query_used) {
      const tried = attempts.slice(0, -1).map(a => `<code>${esc(a)}</code>`).join(' → ');
      infoHtml += `
        <div class="alert alert-secondary py-2 small mb-3" style="border-color:#444">
          <i class="bi bi-funnel me-1 text-info"></i>
          No results for ${tried} — showing results for <strong>${esc(query_used)}</strong>
          ${year ? `<span class="ms-2 badge bg-secondary">year: ${esc(year)}</span>` : ''}
        </div>`;
    } else if (query_used && year) {
      infoHtml += `<div class="text-secondary small mb-2">
        <i class="bi bi-calendar3 me-1"></i>Detected year: <span class="badge bg-secondary">${esc(year)}</span>
        — year-matching results sorted first
      </div>`;
    }

    if (!results.length) {
      const allTried = (attempts || [query_used]).map(a => `<code>${esc(a)}</code>`).join(', ');
      el.innerHTML = infoHtml + `
        <div class="text-secondary text-center py-4">
          No results found after trying: ${allTried}
        </div>`;
      return;
    }

    const rows = results.map(r => {
      const size  = _humanSize(r.size_bytes);
      const seeds = r.seeders  > 0 ? `<span class="text-success">${r.seeders}</span>`  : `<span class="text-secondary">—</span>`;
      const peers = r.leechers > 0 ? `<span class="text-warning">${r.leechers}</span>` : `<span class="text-secondary">—</span>`;
      const titleHtml = year
        ? esc(r.title).replace(new RegExp(`(${esc(year)})`, 'g'), '<mark class="bg-transparent text-warning fw-bold">$1</mark>')
        : esc(r.title);
      return `
        <tr>
          <td>
            <div class="fw-semibold lh-sm">${titleHtml}</div>
            <div class="text-secondary" style="font-size:.75rem">${esc(r.ipt_category||'')}${r.pubdate ? ' · ' + esc(_shortDate(r.pubdate)) : ''}</div>
          </td>
          <td class="text-nowrap text-secondary small">${size}</td>
          <td class="text-nowrap small">${seeds} / ${peers}</td>
          <td class="text-nowrap">
            <span class="badge bg-secondary">${esc(r.suggested_type)}</span>
          </td>
          <td class="text-nowrap">
            <button class="btn btn-sm btn-outline-info"
                    onclick="IPTSearch.grab(${jsStr(r.torrent_url)}, ${jsStr(r.title)}, ${jsStr(r.suggested_type)}, ${jsStr(r._source||'ipt')})"
                    title="Add to rTorrent seedbox">
              <i class="bi bi-cloud-download me-1"></i>Grab
            </button>
          </td>
        </tr>`;
    }).join('');

    el.innerHTML = infoHtml + `
      <div class="text-secondary small mb-2">${results.length} result${results.length !== 1 ? 's' : ''}</div>
      <div class="table-responsive">
        <table class="table table-dark table-hover file-table align-middle">
          <thead class="text-secondary">
            <tr><th>Title</th><th>Size</th><th title="Seeds / Peers">S/P</th><th>Type</th><th></th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  },

  async grab(torrentRef, title, suggestedType, source = 'ipt') {
    const label = window._iptTag || '';
    toast(`Grabbing ${title.slice(0, 50)}…`, 'info');
    try {
      const endpoint = source === 'btn' ? '/btn/grab' : '/iptorrents/grab';
      await API.post(endpoint, { torrent_url: torrentRef, label });
      toast(`✓ Added to rTorrent — sync when ready to download`, 'success');
    } catch (e) {
      toast(`Grab failed: ${e.message}`, 'danger');
    }
  },
};

// ── Smart movie filter ────────────────────────────────────────────────────────
// CAM/TS/Screener quality tags — excluded by default, shown on request.
const _LOWQ_RE = /\b(cam(?:rip)?|hdcam|telesync|telecine|screener|dvdscr|ts(?=\b)|tc(?=\b)|r[2-9](?=\b))\b/i;

// Priority: 2160p → 1080p → all.  Within a resolution tier, sort size
// ascending then deduplicate: within any 1 GB size cluster keep only the
// result with the most seeders.
// includeLowQ = false → strip CAM/TS/Screener first.
function _smartMovieFilter(results, includeLowQ = false) {
  // 1. Optionally exclude low-quality sources
  const lowQItems = results.filter(r => _LOWQ_RE.test(r.title));
  const clean     = includeLowQ ? results : results.filter(r => !_LOWQ_RE.test(r.title));
  const lowQCount = lowQItems.length;

  const tiers = [
    { label: '2160p', re: /\b(2160p|4k|uhd)\b/i },
    { label: '1080p', re: /\b1080p\b/i },
  ];

  let filtered = clean;
  let tierLabel = null;

  for (const tier of tiers) {
    const match = clean.filter(r => tier.re.test(r.title));
    if (match.length) { filtered = match; tierLabel = tier.label; break; }
  }

  // 2. Sort by size ascending
  filtered = [...filtered].sort((a, b) => a.size_bytes - b.size_bytes);

  // 3. Deduplicate: within each 1 GB cluster keep the highest-seeder result
  const GB = 1_000_000_000;
  const deduped = [];
  let i = 0;
  while (i < filtered.length) {
    const clusterMin = filtered[i].size_bytes;
    let j = i;
    while (j < filtered.length && (filtered[j].size_bytes - clusterMin) <= GB) j++;
    const best = filtered.slice(i, j).reduce((a, b) => b.seeders > a.seeders ? b : a);
    deduped.push(best);
    i = j;
  }

  return { results: deduped, tierLabel, lowQCount };
}

function _iptTypeIcon(type) {
  const { icon, color } = categoryIcon(type);
  return `<i class="bi ${icon} ${color}"></i>`;
}

function _shortDate(dateStr) {
  try {
    return new Date(dateStr).toLocaleDateString();
  } catch (_) { return dateStr; }
}

// ─────────────────────────────────────────────
// Hash router
// ─────────────────────────────────────────────
const Router = {
  async route() {
    const hash = (location.hash || '#/').slice(1);
    const parts = hash.split('/').filter(Boolean);

    // highlight active nav link
    document.getElementById('nav-search-link')?.classList.toggle(
      'text-info', parts[0] === 'search');

    if (!parts.length || (parts[0] !== 'category' && parts[0] !== 'search')) {
      await Views.home();
    } else if (parts[0] === 'search') {
      await IPTSearch.render();
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
window.addEventListener('load', async () => {
  Router.route();
  JobsPanel.refresh();
  JobPoller.init();
  // Pre-fetch IPT status so grab() knows the configured sync tag
  try {
    const st = await API.get('/iptorrents/status');
    window._iptTag = (st.rtorrent?.configured) ? '' : '';
    // tag is baked into rtorrent config; leave blank to use server default
  } catch (_) {}
});
