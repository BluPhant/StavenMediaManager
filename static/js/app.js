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
      const raw = await r.text();
      let detail;
      try { detail = JSON.parse(raw).detail; } catch (_) {}
      // If detail is an object (e.g. 409 conflict payload), attach it directly
      const err = new Error(typeof detail === 'string' ? detail : (raw || 'Request failed'));
      if (detail && typeof detail === 'object') err.detail = detail;
      err.status = r.status;
      throw err;
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
        API.get('/jobs?status=done&limit=15'),
        API.get('/sources/status').catch(() => null),
      ]);
    } catch (e) {
      this._setApp(`<div class="alert alert-danger mt-2">Failed to load: ${esc(e.message)}</div>`);
      return;
    }

    const processed = history.filter(j => ['move', 'music_import'].includes(j.type));
    const hasSeedbox = !!(syncStatus?.rtorrent?.configured);

    let active = [];
    if (hasSeedbox) {
      try { active = await API.get('/sources/active'); } catch (_) {}
    }

    // ── Hero: recent movie chips ─────────────────────────────────────────────
    const recentMovieTitles = processed
      .filter(j => j.type === 'move' && /movie/i.test(j.category || ''))
      .slice(0, 3)
      .map(j => {
        if (j.dest_path) {
          const parts = j.dest_path.replace(/\\/g, '/').split('/').filter(Boolean);
          return parts[parts.length - 1] || j.item_name;
        }
        return j.item_name;
      })
      .filter(Boolean);

    const chipHtml = recentMovieTitles.length
      ? `<div class="d-flex align-items-center gap-2 flex-wrap mt-2">
           ${recentMovieTitles.map(t => `
             <button class="btn btn-sm btn-outline-secondary py-0 px-2"
                     style="font-size:.75rem"
                     onclick="MovieDiscover.searchFor(${jsStr(t)})">
               ${esc(t)}
             </button>`).join('')}
           <span class="text-secondary" style="font-size:.7rem">recent</span>
         </div>`
      : '';

    const heroHtml = `
      <div class="card border-secondary mb-3">
        <div class="card-body p-3">
          <div class="text-secondary small mb-2 d-flex align-items-center gap-2">
            <i class="bi bi-film text-info"></i>Find a movie
          </div>
          <div class="d-flex gap-2">
            <input id="home-movie-search" type="text" class="form-control"
                   placeholder="Title, IMDB ID, or year…"
                   onkeydown="if(event.key==='Enter') MovieDiscover.searchFor(this.value.trim())">
            <button class="btn btn-info text-nowrap"
                    onclick="MovieDiscover.searchFor(document.getElementById('home-movie-search').value.trim())">
              <i class="bi bi-search me-1"></i>Search
            </button>
          </div>
          ${chipHtml}
        </div>
      </div>`;

    // ── Seedbox card ─────────────────────────────────────────────────────────
    let seedboxHtml = '';
    if (hasSeedbox) {
      const rt = syncStatus.rtorrent;
      let progressHtml = '';
      if (active.length) {
        const bars = active.map(t => {
          const done  = _humanSize(t.bytes_done);
          const total = _humanSize(t.size_bytes);
          const speed = t.down_rate > 0
            ? `<span class="text-success ms-2" style="font-size:.75rem">${_humanSize(t.down_rate)}/s</span>` : '';
          const barCls = t.is_active
            ? 'progress-bar bg-info progress-bar-animated progress-bar-striped' : 'progress-bar bg-secondary';
          const label = t.name.length > 54 ? t.name.slice(0, 54) + '…' : t.name;
          return `
            <div class="mb-2">
              <div class="d-flex justify-content-between align-items-baseline gap-2 mb-1">
                <span class="small text-truncate" style="min-width:0">${esc(label)}</span>
                <span class="text-secondary text-nowrap flex-shrink-0" style="font-size:.75rem">
                  ${done} / ${total}${speed}
                  <button class="btn btn-link text-danger p-0 ms-1" style="font-size:.7rem;line-height:1"
                          onclick="Actions.stopTorrent('${esc(t.hash)}', this)" title="Stop">
                    <i class="bi bi-stop-fill"></i>
                  </button>
                </span>
              </div>
              <div class="progress" style="height:5px">
                <div class="${barCls}" style="width:${t.pct}%" role="progressbar"></div>
              </div>
            </div>`;
        }).join('');
        progressHtml = `<hr class="border-secondary my-2">${bars}`;
      }
      seedboxHtml = `
        <div class="card border-secondary mb-3">
          <div class="card-body p-3">
            <div class="d-flex align-items-center justify-content-between flex-wrap gap-2">
              <div class="small d-flex align-items-center gap-2">
                <span class="text-success" style="font-size:.6rem">&#9679;</span>
                <span class="text-secondary">tag: <code class="text-info">${esc(rt.tag)}</code></span>
                ${rt.ssh_host ? `<span class="text-secondary" style="font-size:.8rem">${esc(rt.ssh_host)}</span>` : ''}
              </div>
              <div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-info" onclick="Actions.sync()">
                  <i class="bi bi-arrow-repeat me-1"></i>Sync now
                </button>
                <button class="btn btn-sm btn-outline-secondary" onclick="Actions.previewSync()">
                  <i class="bi bi-eye me-1"></i>Preview
                </button>
              </div>
            </div>
            ${progressHtml}
          </div>
        </div>`;
    }

    // ── Incoming categories as pills ─────────────────────────────────────────
    let catsHtml = '';
    if (cats.length) {
      const pills = cats.map(c => {
        const { icon, color } = categoryIcon(c.name);
        const badge = c.item_count > 0
          ? `<span class="badge bg-info text-dark ms-auto" style="font-size:.65rem">${c.item_count}</span>` : '';
        return `
          <div class="d-flex align-items-center gap-2 px-3 py-2 rounded border border-secondary"
               style="cursor:pointer;background:rgba(255,255,255,.025)"
               onclick="Router.go('/category/${enc(c.name)}')">
            <i class="bi ${icon} ${color}" style="font-size:.95rem"></i>
            <span class="small">${esc(c.name)}</span>
            ${badge}
          </div>`;
      }).join('');
      catsHtml = `
        <div class="text-secondary small fw-semibold text-uppercase mb-2" style="letter-spacing:.06em">Incoming</div>
        <div class="d-flex flex-wrap gap-2">${pills}</div>`;
    } else {
      catsHtml = `
        <div class="text-center py-4 text-secondary small">
          <i class="bi bi-folder-x fs-3 d-block mb-2"></i>
          No incoming items
        </div>`;
    }

    // ── Recently processed (right column) ────────────────────────────────────
    let recentHtml = '';
    if (processed.length) {
      const rows = processed.slice(0, 8).map(j => {
        let title = j.item_name;
        if (j.dest_path) {
          const parts = j.dest_path.replace(/\\/g, '/').split('/').filter(Boolean);
          title = parts[parts.length - 1] || title;
        }
        const isUpgrade = (j.message || '').toLowerCase().includes('upgrade');
        const catLabel  = j.type === 'music_import' ? 'music' : (j.category || j.type);
        const sub = isUpgrade
          ? `<span class="text-warning" style="font-size:.7rem">upgrade</span>`
          : `<span class="text-secondary" style="font-size:.7rem">${esc(catLabel)}</span>`;
        return `
          <div class="d-flex justify-content-between align-items-start gap-2 py-2 border-bottom border-secondary" style="border-bottom-width:1px!important">
            <div style="min-width:0">
              <div class="small text-truncate">${esc(title)}</div>
              ${sub}
            </div>
            <span class="text-secondary flex-shrink-0" style="font-size:.7rem;white-space:nowrap">${_relTime(j.created_at)}</span>
          </div>`;
      }).join('');
      recentHtml = `
        <div class="card border-secondary h-100">
          <div class="card-body p-3">
            <div class="text-secondary small fw-semibold text-uppercase mb-2" style="letter-spacing:.06em">Recently processed</div>
            ${rows}
          </div>
        </div>`;
    }

    // ── Assemble ─────────────────────────────────────────────────────────────
    const leftHtml = seedboxHtml + `<div class="card border-secondary"><div class="card-body p-3">${catsHtml}</div></div>`;

    this._setApp(`
      ${heroHtml}
      <div class="row g-3">
        <div class="col-12 col-lg-7">${leftHtml}</div>
        ${recentHtml ? `<div class="col-12 col-lg-5">${recentHtml}</div>` : ''}
      </div>`);

    document.getElementById('home-movie-search')?.focus();
  },

  async category(name) {
    this._loading();

    // Fetch items; for movie/music categories also fetch existing matches in parallel
    const isMovies = /movie/i.test(name);
    const isMusic  = /music/i.test(name);
    let items, matchMap = {};
    try {
      const fetches = [API.get(`/categories/${enc(name)}/items`)];
      if (isMovies) fetches.push(API.get(`/movies/matches?category=${enc(name)}`));
      if (isMusic)  fetches.push(API.get(`/music/matches?category=${enc(name)}`));
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
      let matchLabel = '';
      if (match) {
        matchLabel = isMusic
          ? `${match.artist} — ${match.album}${match.year ? ' (' + match.year + ')' : ''}`
          : (match.formatted_name || '');
      }
      return `
        <tr class="item-row" onclick="Router.go('/category/${enc(name)}/${enc(it.name)}')">
          <td>
            <i class="bi bi-folder me-2 text-warning"></i>${esc(it.name)}
            ${match ? `<span class="ms-2 text-success small" title="${esc(matchLabel)}"><i class="bi bi-check-circle-fill"></i></span>` : ''}
          </td>
          <td class="text-secondary text-nowrap">${it.size_human}</td>
          <td class="text-nowrap">
            ${it.has_rar ? '<span class="badge bg-info badge-rar me-1"><i class="bi bi-archive me-1"></i>RAR</span>' : ''}
            ${match ? `<span class="badge bg-success badge-rar">${esc(matchLabel)}</span>` : ''}
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
    const isMusic  = /music/i.test(category);
    const isSwitch = /switch/i.test(category);
    let detail, matchData, switchData, switchDetect;
    try {
      const fetches = [API.get(`/categories/${enc(category)}/items/${enc(itemName)}`)];
      if (isMovies) fetches.push(API.get(`/movies/match?category=${enc(category)}&item=${enc(itemName)}`));
      [detail, matchData] = await Promise.all(fetches);
      if (isSwitch) {
        [switchData, switchDetect] = await Promise.all([
          API.get(`/switch/content?item_name=${enc(itemName)}`).catch(() => null),
          API.get(`/switch/detect?item_name=${enc(itemName)}&category=${enc(category)}`).catch(() => null),
        ]);
      }
    } catch (e) {
      this._setApp(`<div class="alert alert-danger mt-2">Failed to load item: ${esc(e.message)}</div>`);
      return;
    }

    const crumb = breadcrumb([
      ['Home', '#/'],
      [esc(category), `#/category/${enc(category)}`],
      [esc(itemName)],
    ]);

    const hasMatch       = !!(matchData && matchData.match);
    const hasSwitchMatch = !!(switchData && switchData.title);
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
    if (isSwitch) {
      actionBtns.push(`
        <button class="btn btn-success btn-sm" id="btn-switch-move"
                ${!hasSwitchMatch ? 'disabled title="Save a match first"' : ''}
                onclick="SwitchMatch.move(${jsStr(category)}, ${jsStr(itemName)})">
          <i class="bi bi-joystick me-1"></i>Move to Library
        </button>`);
    }
    if (isMusic) {
      actionBtns.push(`
        <button class="btn btn-success btn-sm" id="btn-music-import"
                title="Convert FLAC → MP3 V0, tag, embed cover art, and file into the library"
                onclick="Actions.musicImport(${jsStr(category)}, ${jsStr(itemName)})">
          <i class="bi bi-music-note-beamed me-1"></i>Convert &amp; Import
        </button>`);
    }
    const actionsHtml = actionBtns.length ? `
      <div class="mb-3 d-flex gap-2 flex-wrap">${actionBtns.join('')}</div>` : '';

    const fileRows = detail.files.map(f => `
      <tr>
        <td><i class="bi ${fileIcon(f.name, f.is_dir)} me-2"></i>${esc(f.name)}</td>
        <td class="text-secondary text-nowrap">${f.is_dir ? '—' : f.size_human}</td>
      </tr>`).join('');

    this._setApp(crumb + (isMovies ? _matchPanelHtml() : '') + (isMusic ? _musicMatchPanelHtml() : '') + (isSwitch ? _switchMatchPanelHtml() : '') + actionsHtml + `
      <div class="table-responsive">
        <table class="table table-hover table-dark file-table">
          <thead><tr class="text-secondary"><th>File</th><th>Size</th></tr></thead>
          <tbody>${fileRows}</tbody>
        </table>
      </div>`);

    if (isMovies) {
      MovieMatch.init(category, itemName, matchData);
    }
    if (isMusic) {
      MusicMatch.init(category, itemName);
    }
    if (isSwitch) {
      SwitchMatch.init(category, itemName, switchData, switchDetect);
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
  _results: [],   // cache so onclick can reference by index, avoiding inline string escaping

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

    // Store results so onclick can reference by index — avoids embedding raw
    // overview/title strings inside HTML attributes (breaks on newlines / quotes).
    this._results = results;
    resultsEl.innerHTML = results.map((r, i) => `
      <div class="col-6 col-md-4 col-lg-3 col-xl-2">
        <div class="card h-100 match-result-card" onclick="MovieMatch._selectIdx(${i})">
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

  _selectIdx(i) {
    const r = this._results[i];
    if (!r) return;
    this.select(r.tmdb_id, r.title, r.year ?? null, r.poster_url || null, r.overview || null, r.formatted_name);
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
// Music match panel HTML template
// ─────────────────────────────────────────────
function _musicMatchPanelHtml() {
  return `
    <div class="card border-secondary mt-4" id="music-match-panel">
      <div class="card-header d-flex justify-content-between align-items-center py-2">
        <span class="small fw-semibold"><i class="bi bi-vinyl me-2 text-danger"></i>Discogs Match</span>
        <div id="music-match-status"></div>
      </div>
      <div class="card-body pb-2">
        <div id="music-current-match"></div>
        <div class="d-flex gap-2 mt-2 flex-wrap">
          <input type="text" id="music-match-artist" class="form-control form-control-sm bg-dark text-white border-secondary"
                 placeholder="Artist…" style="min-width:140px;flex:2"
                 onkeydown="if(event.key==='Enter') MusicMatch.search()">
          <input type="text" id="music-match-album" class="form-control form-control-sm bg-dark text-white border-secondary"
                 placeholder="Album…" style="min-width:140px;flex:3"
                 onkeydown="if(event.key==='Enter') MusicMatch.search()">
          <button class="btn btn-sm btn-outline-secondary" onclick="MusicMatch.search()">
            <i class="bi bi-search me-1"></i>Search
          </button>
        </div>
        <div id="music-match-results" class="row g-2 mt-2"></div>
        <div id="music-release-detail" class="mt-3" style="display:none"></div>
      </div>
    </div>`;
}

// ─────────────────────────────────────────────
// MusicMatch — Discogs lookup & persistence
// ─────────────────────────────────────────────
const MusicMatch = {
  _category: null,
  _item: null,
  _results: [],

  async init(category, item) {
    this._category = category;
    this._item = item;

    // Load existing match + pre-fill tags in parallel
    let matchResp, tagsResp;
    try {
      [matchResp, tagsResp] = await Promise.all([
        API.get(`/music/match?category=${enc(category)}&item=${enc(item)}`),
        API.get(`/music/tags?category=${enc(category)}&item=${enc(item)}`),
      ]);
    } catch (e) {
      _mEl('music-match-results').innerHTML =
        `<div class="col-12 text-danger small">${esc(e.message)}</div>`;
      return;
    }

    if (tagsResp.artist) _mEl('music-match-artist').value = tagsResp.artist;
    if (tagsResp.album)  _mEl('music-match-album').value  = tagsResp.album;

    if (matchResp.match) {
      this._renderMatch(matchResp.match);
    }
  },

  async search() {
    const artist = (_mEl('music-match-artist').value || '').trim();
    const album  = (_mEl('music-match-album').value  || '').trim();
    if (!artist && !album) return;

    const resultsEl = _mEl('music-match-results');
    const detailEl  = _mEl('music-release-detail');
    detailEl.style.display = 'none';
    resultsEl.innerHTML = `
      <div class="col-12 text-secondary small py-2">
        <span class="spinner-border spinner-border-sm me-2"></span>Searching Discogs…
      </div>`;

    let results;
    try {
      results = await API.get(
        `/music/search?artist=${enc(artist)}&album=${enc(album)}&limit=12`
      );
    } catch (e) {
      resultsEl.innerHTML =
        `<div class="col-12 text-danger small">${esc(e.message)}</div>`;
      return;
    }

    if (!results.length) {
      resultsEl.innerHTML =
        '<div class="col-12 text-secondary small">No results. Try adjusting artist or album.</div>';
      return;
    }

    this._results = results;
    resultsEl.innerHTML = results.map((r, i) => `
      <div class="col-6 col-md-4 col-lg-3 col-xl-2">
        <div class="card h-100 match-result-card" onclick="MusicMatch._selectIdx(${i})">
          ${r.thumb
            ? `<img src="${esc(r.thumb)}" class="card-img-top" alt=""
                    style="aspect-ratio:1/1;object-fit:cover">`
            : `<div class="card-img-top d-flex align-items-center justify-content-center bg-dark"
                    style="aspect-ratio:1/1"><i class="bi bi-vinyl text-secondary" style="font-size:2rem"></i></div>`
          }
          <div class="card-body p-2">
            <div class="small fw-semibold lh-sm text-truncate">${esc(r.title)}</div>
            <div class="text-secondary lh-sm" style="font-size:.75rem">${esc(r.artist)}</div>
            <div class="text-secondary" style="font-size:.7rem">
              ${r.year ?? ''}${r.label ? ` · ${esc(r.label)}` : ''}
            </div>
            ${r.format ? `<div class="text-secondary" style="font-size:.65rem">${esc(r.format)}</div>` : ''}
          </div>
        </div>
      </div>`).join('');
  },

  async _selectIdx(i) {
    const r = this._results[i];
    if (!r) return;

    const detailEl  = _mEl('music-release-detail');
    const resultsEl = _mEl('music-match-results');
    detailEl.style.display = 'none';
    detailEl.innerHTML = `
      <div class="text-secondary small py-2">
        <span class="spinner-border spinner-border-sm me-2"></span>Loading release…
      </div>`;
    detailEl.style.display = '';

    let rel;
    try {
      rel = await API.get(`/music/release/${r.id}`);
    } catch (e) {
      detailEl.innerHTML = `<div class="text-danger small">${esc(e.message)}</div>`;
      return;
    }

    // Merge search result cover with release detail cover
    const cover = rel.cover_url || r.cover_image || r.thumb;

    const trackRows = rel.tracks.length
      ? rel.tracks.map(t => `
          <tr>
            <td class="text-secondary text-nowrap pe-3" style="font-size:.8rem">${esc(t.position)}</td>
            <td style="font-size:.85rem">${esc(t.title)}</td>
            <td class="text-secondary text-nowrap" style="font-size:.75rem">${esc(t.duration || '')}</td>
          </tr>`).join('')
      : '<tr><td colspan="3" class="text-secondary small">No tracklist available.</td></tr>';

    detailEl.innerHTML = `
      <div class="border border-secondary rounded p-3 bg-dark bg-opacity-50">
        <div class="d-flex gap-3 align-items-start mb-3">
          ${cover
            ? `<img src="${esc(cover)}" alt="" class="flex-shrink-0 rounded" style="width:80px;height:80px;object-fit:cover">`
            : `<div class="flex-shrink-0 rounded bg-dark d-flex align-items-center justify-content-center"
                    style="width:80px;height:80px"><i class="bi bi-vinyl text-secondary" style="font-size:2rem"></i></div>`
          }
          <div class="flex-grow-1 min-w-0">
            <div class="fw-semibold">${esc(rel.artist)} — ${esc(rel.title)}</div>
            <div class="text-secondary small mt-1">
              ${rel.year ? `<span class="me-2">${rel.year}</span>` : ''}
              ${rel.label ? `<span class="me-2">${esc(rel.label)}${rel.catno ? ' · ' + esc(rel.catno) : ''}</span>` : ''}
              ${rel.country ? `<span class="me-2">${esc(rel.country)}</span>` : ''}
            </div>
            ${rel.genres?.length ? `<div class="text-secondary" style="font-size:.75rem">${rel.genres.map(esc).join(' · ')}</div>` : ''}
          </div>
        </div>
        <div class="table-responsive mb-3" style="max-height:200px;overflow-y:auto">
          <table class="table table-dark table-sm mb-0 file-table">
            <tbody>${trackRows}</tbody>
          </table>
        </div>
        <div class="d-flex gap-2">
          <button class="btn btn-success btn-sm" onclick="MusicMatch._confirm(${rel.id})">
            <i class="bi bi-check-lg me-1"></i>Confirm this match
          </button>
          <button class="btn btn-outline-secondary btn-sm" onclick="MusicMatch._hideDetail()">
            Cancel
          </button>
        </div>
      </div>`;

    // Store release data for confirm
    this._pendingRelease = rel;
    this._pendingCover   = cover;
  },

  _hideDetail() {
    const d = _mEl('music-release-detail');
    if (d) { d.style.display = 'none'; d.innerHTML = ''; }
  },

  async _confirm(releaseId) {
    const rel = this._pendingRelease;
    if (!rel || rel.id !== releaseId) return;

    const genres = [...(rel.genres || []), ...(rel.styles || [])].join(', ') || null;

    try {
      const saved = await API.post('/music/match', {
        category:    this._category,
        item_name:   this._item,
        discogs_id:  rel.id,
        artist:      rel.artist,
        album:       rel.title,
        year:        rel.year ?? null,
        label:       rel.label ?? null,
        cover_url:   this._pendingCover ?? null,
        genres:      genres,
        country:     rel.country ?? null,
        tracks_json: JSON.stringify(rel.tracks),
      });
      this._renderMatch(saved);
      _mEl('music-match-results').innerHTML = '';
      this._hideDetail();
      toast(`Matched: ${rel.artist} — ${rel.title}`, 'success');
    } catch (e) {
      toast(`Failed to save match: ${e.message}`, 'danger');
    }
  },

  async clear() {
    try {
      await API.del(`/music/match?category=${enc(this._category)}&item=${enc(this._item)}`);
      _mEl('music-current-match').innerHTML = '';
      _mEl('music-match-status').innerHTML  = '';
      toast('Discogs match cleared', 'secondary');
    } catch (e) {
      toast(e.message, 'danger');
    }
  },

  _renderMatch(match) {
    _mEl('music-match-status').innerHTML =
      '<span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>Matched</span>';

    const tracks = match.tracks || [];
    const tracksHtml = tracks.length
      ? `<div class="text-secondary mt-1" style="font-size:.75rem">
           ${tracks.slice(0, 4).map(t => esc(t.title)).join(' · ')}
           ${tracks.length > 4 ? `<em> +${tracks.length - 4} more</em>` : ''}
         </div>`
      : '';

    _mEl('music-current-match').innerHTML = `
      <div class="d-flex gap-3 align-items-start p-2 rounded mb-2
                  bg-success bg-opacity-10 border border-success border-opacity-25">
        ${match.cover_url
          ? `<img src="${esc(match.cover_url)}" alt="" class="flex-shrink-0 rounded"
                  style="width:54px;height:54px;object-fit:cover">`
          : `<div class="flex-shrink-0 rounded bg-dark d-flex align-items-center justify-content-center"
                  style="width:54px;height:54px"><i class="bi bi-vinyl text-secondary"></i></div>`
        }
        <div class="flex-grow-1 min-w-0">
          <div class="fw-semibold">${esc(match.artist)} — ${esc(match.album)}</div>
          <div class="text-secondary small">
            ${match.year ? `<span class="me-2">${match.year}</span>` : ''}
            ${match.label ? `<span class="me-2">${esc(match.label)}</span>` : ''}
            ${match.country ? `<span>${esc(match.country)}</span>` : ''}
          </div>
          ${match.genres ? `<div class="text-secondary" style="font-size:.75rem">${esc(match.genres)}</div>` : ''}
          ${tracksHtml}
        </div>
        <button class="btn btn-sm btn-outline-danger flex-shrink-0"
                onclick="MusicMatch.clear()" title="Clear match">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>`;
  },
};

function _mEl(id) { return document.getElementById(id); }

// ─────────────────────────────────────────────
// Switch match panel HTML template
// ─────────────────────────────────────────────
function _switchFormHtml() {
  return `
    <div class="d-flex gap-2 align-items-end mb-2">
      <div class="flex-grow-1">
        <label class="form-label small mb-1 text-secondary">Search by title</label>
        <input class="form-control form-control-sm bg-dark text-light border-secondary"
               id="switch-search-input" placeholder="Game title…"
               onkeydown="if(event.key==='Enter') SwitchMatch.search()">
      </div>
      <button class="btn btn-sm btn-outline-secondary" onclick="SwitchMatch.search()">
        <i class="bi bi-search me-1"></i>Search
      </button>
    </div>
    <div id="switch-search-results" class="mb-1"></div>
    <div class="d-flex gap-2 align-items-end mb-2">
      <div class="flex-grow-1">
        <label class="form-label small mb-1 text-secondary">GameTDB ID (e.g. BFLTA)</label>
        <input class="form-control form-control-sm bg-dark text-light border-secondary"
               id="switch-id-input" placeholder="From gametdb.com/Switch/XXXXX URL"
               onkeydown="if(event.key==='Enter') SwitchMatch.lookup()">
      </div>
      <button class="btn btn-sm btn-outline-secondary" onclick="SwitchMatch.lookup()">
        <i class="bi bi-joystick me-1"></i>Look Up
      </button>
    </div>
    <div class="d-flex gap-2 mb-2">
      <select class="form-select form-select-sm bg-dark text-light border-secondary" id="switch-type-select" style="max-width:140px">
        <option value="base">Base Game</option>
        <option value="update">Update</option>
        <option value="dlc">DLC</option>
      </select>
      <input class="form-control form-control-sm bg-dark text-light border-secondary"
             id="switch-version-input" placeholder="Version (e.g. 1.0.3)" style="max-width:160px">
    </div>
    <div id="switch-lookup-result"></div>`;
}

function _switchMatchPanelHtml() {
  return `
    <div class="card border-secondary mt-4" id="switch-match-panel">
      <div class="card-header d-flex justify-content-between align-items-center py-2">
        <span class="small fw-semibold"><i class="bi bi-joystick me-2 text-success"></i>Switch Match</span>
      </div>
      <div class="card-body p-3">
        <div id="switch-match-body">${_switchFormHtml()}</div>
      </div>
    </div>`;
}

// ─────────────────────────────────────────────
// SwitchMatch — title search, lookup & persistence
// ─────────────────────────────────────────────
const SwitchMatch = {
  _category: null,
  _itemName: null,
  _searchResults: [],

  init(category, itemName, existingContent, detect) {
    this._category = category;
    this._itemName = itemName;
    this._searchResults = [];

    if (existingContent && existingContent.title) {
      this._renderMatch(existingContent);
      return;
    }

    if (detect) {
      if (detect.content_type) {
        const sel = _mEl('switch-type-select');
        if (sel) sel.value = detect.content_type;
      }
      if (detect.version) {
        const vi = _mEl('switch-version-input');
        if (vi) vi.value = detect.version;
      }
      // nswdb auto-match: show a ready-to-confirm card
      if (detect.nswdb_title && !detect.game_id) {
        this._searchResults = [{
          source: 'nswdb',
          igdb_id: null,
          game_id: null,
          title: detect.nswdb_title,
          cover_url: null,
          subtitle: detect.nswdb_publisher || 'scene database match',
          publisher: detect.nswdb_publisher || null,
          developer: null,
          nintendo_id: detect.nintendo_id || null,
        }];
        const lu = _mEl('switch-lookup-result');
        if (lu) lu.innerHTML = this._resultCardHtml(this._searchResults[0], 0);
      }
      // GameTDB ID detected — trigger live lookup
      if (detect.game_id) {
        if (_mEl('switch-id-input')) _mEl('switch-id-input').value = detect.game_id;
        this.lookup();
      }
    }
  },

  // ── Inline title search ───────────────────────────────────────────────────

  async search() {
    const q = (_mEl('switch-search-input')?.value || '').trim();
    if (!q) { toast('Enter a title to search', 'warning'); return; }

    const resEl = _mEl('switch-search-results');
    resEl.innerHTML = `<div class="text-secondary small py-1"><i class="bi bi-hourglass-split me-1"></i>Searching…</div>`;
    this._searchResults = [];

    try {
      const data = await API.get(`/switch/search?q=${enc(q)}`);

      const igdbItems = (data.igdb || []).map(r => ({
        source: 'igdb', igdb_id: r.igdb_id, game_id: null,
        title: r.title, cover_url: r.cover_url,
        subtitle: [r.publisher, r.year ? `(${r.year})` : null].filter(Boolean).join(' '),
        publisher: r.publisher, developer: r.developer, nintendo_id: null,
      }));

      const igdbLow = new Set(igdbItems.map(i => i.title.toLowerCase()));
      const nswdbItems = (data.nswdb || [])
        .filter(r => !igdbLow.has(r.name.toLowerCase()))
        .map(r => ({
          source: 'nswdb', igdb_id: null, game_id: null,
          title: r.name, cover_url: null,
          subtitle: r.publisher || '',
          publisher: r.publisher, developer: null, nintendo_id: r.titleid,
        }));

      this._searchResults = [...igdbItems, ...nswdbItems];

      if (!this._searchResults.length) {
        resEl.innerHTML = `<div class="text-secondary small py-1">No results</div>`;
        return;
      }

      resEl.innerHTML =
        `<div class="border border-secondary rounded overflow-hidden mb-2">` +
        this._searchResults.map((item, idx) => `
          <div class="d-flex align-items-center gap-2 p-2${idx ? ' border-top border-secondary' : ''}">
            ${item.cover_url
              ? `<img src="${esc(item.cover_url)}" style="height:44px;border-radius:3px;flex-shrink:0" onerror="this.style.display='none'">`
              : `<div class="d-flex align-items-center justify-content-center bg-secondary rounded flex-shrink-0" style="width:30px;height:44px"><i class="bi bi-joystick text-dark" style="font-size:.7rem"></i></div>`}
            <div class="flex-grow-1 overflow-hidden">
              <div class="small fw-semibold text-truncate">${esc(item.title)}</div>
              ${item.subtitle ? `<div class="text-secondary" style="font-size:.72rem">${esc(item.subtitle)}</div>` : ''}
            </div>
            <button class="btn btn-sm btn-outline-success flex-shrink-0" onclick="SwitchMatch._useResult(${idx})">Use</button>
          </div>`).join('') +
        `</div>`;
    } catch (e) {
      resEl.innerHTML = `<div class="alert alert-warning mt-1 py-2 small">${esc(e.message)}</div>`;
    }
  },

  _useResult(idx) {
    const item = this._searchResults[idx];
    if (!item) return;
    const resEl = _mEl('switch-search-results');
    if (resEl) resEl.innerHTML = '';
    const lu = _mEl('switch-lookup-result');
    if (lu) lu.innerHTML = this._resultCardHtml(item, idx);
  },

  _resultCardHtml(item, idx) {
    return `
      <div class="d-flex align-items-center gap-3 p-2 bg-dark rounded border border-secondary mt-1">
        ${item.cover_url
          ? `<img src="${esc(item.cover_url)}" style="height:72px;border-radius:4px" onerror="this.style.display='none'">`
          : `<div class="d-flex align-items-center justify-content-center bg-secondary rounded" style="width:48px;height:72px"><i class="bi bi-joystick text-dark"></i></div>`}
        <div class="flex-grow-1 min-w-0">
          <div class="fw-semibold">${esc(item.title)}</div>
          <div class="text-secondary small">${esc(item.subtitle || item.source)}</div>
        </div>
        <button class="btn btn-success btn-sm flex-shrink-0" onclick="SwitchMatch._confirmResult(${idx})">
          <i class="bi bi-check-lg me-1"></i>Confirm
        </button>
      </div>`;
  },

  async _confirmResult(idx) {
    const item = this._searchResults[idx];
    if (!item) return;
    const contentType = _mEl('switch-type-select')?.value || 'base';
    const version = _mEl('switch-version-input')?.value?.trim() || null;
    const payload = {
      category: this._category, item_name: this._itemName,
      content_type: contentType, version: version || null,
      title: item.title, publisher: item.publisher || null, developer: item.developer || null,
    };
    if (item.igdb_id)     payload.igdb_id = item.igdb_id;
    if (item.cover_url)   payload.cover_url = item.cover_url;
    if (item.nintendo_id) payload.nintendo_id = item.nintendo_id;
    await this._doMatch(payload, item.title);
  },

  // ── GameTDB direct lookup ─────────────────────────────────────────────────

  async lookup() {
    const gameId = (_mEl('switch-id-input')?.value || '').trim().toUpperCase();
    if (!gameId) { toast('Enter a GameTDB ID first', 'warning'); return; }

    const res = _mEl('switch-lookup-result');
    res.innerHTML = `<div class="text-secondary small py-1"><i class="bi bi-hourglass-split me-1"></i>Looking up ${gameId}…</div>`;

    try {
      const game = await API.get(`/switch/lookup/${enc(gameId)}`);
      this._searchResults = [{
        source: 'gametdb', igdb_id: null, game_id: gameId,
        title: game.title, cover_url: game.cover_url,
        subtitle: [game.game_id, game.developer].filter(Boolean).join(' · '),
        publisher: game.publisher, developer: game.developer, nintendo_id: null,
      }];
      res.innerHTML = this._resultCardHtml(this._searchResults[0], 0);
    } catch (e) {
      res.innerHTML = `<div class="alert alert-warning mt-1 py-2 small">${esc(e.message)}</div>`;
    }
  },

  // ── Shared confirm helper ─────────────────────────────────────────────────

  async _doMatch(payload, title) {
    try {
      await API.post('/switch/match', payload);
      const content = await API.get(`/switch/content?item_name=${enc(this._itemName)}`).catch(() => null);
      this._renderMatch(content);
      const btn = _mEl('btn-switch-move');
      if (btn) { btn.disabled = false; btn.removeAttribute('title'); }
      toast(`Matched: ${title}`, 'success');
    } catch (e) {
      toast(`Match failed: ${e.message}`, 'danger');
    }
  },

  async move(category, itemName) {
    try {
      const r = await API.post('/switch/move', { category, item_name: itemName });
      JobPoller.track(r.job_id, { type: 'move', category, itemName });
      JobsPanel.open();
      toast(`Moving to library — Job #${r.job_id}`, 'success');
      Router.go('/');
    } catch (e) {
      toast(`Move failed: ${e.message}`, 'danger');
    }
  },

  async clear() {
    try {
      await API.delete(`/switch/content?item_name=${enc(this._itemName)}`);
      _mEl('switch-match-body').innerHTML = _switchFormHtml();
      this._searchResults = [];
      const btn = _mEl('btn-switch-move');
      if (btn) { btn.disabled = true; btn.title = 'Save a match first'; }
      toast('Match cleared', 'secondary');
    } catch (e) {
      toast(`Clear failed: ${e.message}`, 'danger');
    }
  },

  _renderMatch(content) {
    if (!content || !content.title) return;
    const t = content.title;
    const typeLabel = { base: 'Base Game', update: 'Update', dlc: 'DLC' }[content.content_type] || content.content_type;
    const idLine = [t.game_id, t.igdb_id ? `IGDB:${t.igdb_id}` : null].filter(Boolean).join(' · ');
    _mEl('switch-match-body').innerHTML = `
      <div class="d-flex align-items-center gap-3">
        ${t.cover_url
          ? `<img src="${esc(t.cover_url)}" style="height:80px;border-radius:4px" onerror="this.style.display='none'">`
          : `<div class="d-flex align-items-center justify-content-center bg-secondary rounded" style="width:52px;height:80px"><i class="bi bi-joystick text-dark"></i></div>`}
        <div class="flex-grow-1 min-w-0">
          <div class="fw-semibold">${esc(t.title)}</div>
          <div class="text-secondary small">
            ${idLine ? esc(idLine) + ' ' : ''}<span class="badge bg-secondary">${esc(typeLabel)}</span>
            ${content.version ? `<span class="text-secondary ms-1">v${esc(content.version)}</span>` : ''}
          </div>
          ${t.developer ? `<div class="text-secondary" style="font-size:.75rem">${esc(t.developer)}</div>` : ''}
          <div class="mt-1">
            ${(t.contents || []).map(c => {
              const lbl = { base: '🎮 Base', update: '🔄 Update', dlc: '📦 DLC' }[c.content_type] || c.content_type;
              return `<span class="badge bg-dark border border-secondary me-1 small">${lbl}${c.version ? ' v'+esc(c.version) : ''}</span>`;
            }).join('')}
          </div>
        </div>
        <button class="btn btn-sm btn-outline-danger flex-shrink-0"
                onclick="SwitchMatch.clear()" title="Clear match">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>`;
  },
};

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

  async musicImport(category, itemName) {
    try {
      const job = await API.post('/jobs/music-import', { category, item_name: itemName });
      JobPoller.track(job.id, { type: 'music_import', category, itemName });
      JobsPanel.open();
      toast(`Music import started — Job #${job.id}`, 'success');
    } catch (e) {
      toast(`Could not start music import: ${e.message}`, 'danger');
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

  close() {
    document.getElementById('jobs-panel-body').style.display = 'none';
    document.getElementById('jobs-chevron').className = 'bi bi-chevron-up';
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
    badge.classList.toggle('jobs-badge-pulse', active.length > 0);

    const panelCount = document.getElementById('jobs-panel-count');
    if (panelCount) {
      panelCount.textContent = active.length > 0 ? `· ${active.length} running` : '';
      panelCount.classList.toggle('d-none', active.length === 0);
    }

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
      } else if (ctx.type === 'move' || ctx.type === 'music_import') {
        Router.go('/');
      }
    } catch (_) {}
  },
};

// ─────────────────────────────────────────────
// IPTorrents search view
// ─────────────────────────────────────────────
const IPTSearch = {
  _lastQuery: '',
  _lastCat: 'ipt:tv',
  _showLowQ: false,           // toggle: include CAM/TS/Screener in movie results
  _cachedMovieResults: null,  // raw results before movie filter (for toggling)

  // Dropdown options: value is "source:category"
  _catOptions() {
    const sel = this._lastCat;
    const o = (v, label) => `<option value="${v}"${sel===v?' selected':''}>${label}</option>`;
    return [
      o('ipt:tv',         'TV · IPT'),
      o('btn:tv',         'TV · BTN'),
      o('ipt:music',      'Music · IPT'),
      o('ipt:audiobooks', 'Audiobooks · IPT'),
      o('ipt:games',      'Games · IPT'),
      o('ipt:ebooks',     'Ebooks · IPT'),
      o('ipt:software',   'Software · IPT'),
      o('ipt:all',        'All · IPT'),
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
        <tr data-result-title="${esc(r.title)}">
          <td>
            <div class="fw-semibold lh-sm">${titleHtml}<span class="sbx-badge"></span></div>
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

    // Async checks — library presence + seedbox dupes (both non-blocking)
    this._checkLibrary(this._lastQuery);
    this._checkSbxDupes();
  },

  async grab(torrentRef, title, suggestedType, source = 'ipt', force = false) {
    const label    = window._iptTag || '';
    const endpoint = source === 'btn' ? '/btn/grab' : '/iptorrents/grab';
    const body     = source === 'btn'
      ? { torrent_url: torrentRef, label, title, suggested_type: suggestedType }
      : { torrent_url: torrentRef, label, force, title, suggested_type: suggestedType };

    toast(`Grabbing ${title.slice(0, 50)}…`, 'info');
    try {
      await API.post(endpoint, body);
      toast(`✓ Added to rTorrent — sync when ready to download`, 'success');
    } catch (e) {
      if (e.status === 409 && e.detail?.conflict) {
        _showSbxConflict(e.detail);
        return;
      }
      toast(`Grab failed: ${e.message}`, 'danger');
    }
  },

  // ── Library check — is this title already on the local server? ──────────────
  async _checkLibrary(q) {
    if (!q) return;
    let hits;
    try { hits = await API.get(`/library/search?q=${enc(q)}`); } catch (_) { return; }
    if (!hits || !hits.length) return;
    const el = document.getElementById('ipt-results');
    if (!el) return;
    const fileList = hits.slice(0, 3).map(h => `<code>${esc(h.filename)}</code>`).join(', ');
    const more = hits.length > 3 ? ` <span class="text-secondary">+${hits.length - 3} more</span>` : '';
    el.insertAdjacentHTML('afterbegin', `
      <div class="alert alert-success py-2 small mb-2"
           style="border-color:#28a745;background:rgba(40,167,69,.1)">
        <i class="bi bi-check-circle-fill me-1 text-success"></i>
        <strong>${esc(q)}</strong> is already in your library — ${fileList}${more}
      </div>`);
  },

  // ── Seedbox dupe check — badges results; in movie mode applies tier logic ────
  async _checkSbxDupes() {
    let brief;
    try { brief = await API.get('/sources/brief'); } catch (_) { return; }

    // Build normalised sbx entries (now includes size_bytes from API)
    const sbxEntries = Object.entries(brief).map(([hash, t]) => ({
      hash,
      name:       t.name,
      label:      t.label,
      pct:        t.pct,
      size_bytes: t.size_bytes,
      norm:       _normTitle(t.name),
    }));

    // Badge all DOM result rows that match a seedbox entry by title
    document.querySelectorAll('[data-result-title]').forEach(row => {
      const norm = _normTitle(row.dataset.resultTitle);
      const hit  = sbxEntries.find(s => s.norm && norm && (s.norm.includes(norm) || norm.includes(s.norm)));
      if (hit) {
        const badge = row.querySelector('.sbx-badge');
        if (badge) { badge.textContent = 'On SBX'; badge.className = 'sbx-badge badge bg-warning text-dark ms-1'; }
      }
    });

    // ── Movie-specific tier logic ────────────────────────────────────────────
    const [, cat] = (this._lastCat || 'ipt:all').split(':');
    if (cat !== 'movies') return;

    // Match sbx items against the search query (catches releases not in results)
    const queryWords = _normTitle(this._lastQuery || '').split(' ').filter(w => w.length >= 2);
    if (!queryWords.length) return;

    const sbxMovieMatches = sbxEntries.filter(s =>
      s.norm && queryWords.every(w => s.norm.includes(w))
    );
    if (!sbxMovieMatches.length) return;

    // Pick the highest-resolution sbx match
    const _TIER_2160 = /\b(2160p|4k|uhd)\b/i;
    const _TIER_1080 = /\b1080p\b/i;
    const tierRank   = name => _TIER_2160.test(name) ? 2 : _TIER_1080.test(name) ? 1 : 0;
    sbxMovieMatches.sort((a, b) => tierRank(b.name) - tierRank(a.name));
    const best      = sbxMovieMatches[0];
    const rank      = tierRank(best.name);
    const tierLabel = rank === 2 ? '2160p' : rank === 1 ? '1080p' : null;

    const el = document.getElementById('ipt-results');
    if (!el) return;

    // Helper: build an Import button (or % text) for synthetic sbx rows
    const _sbxActionCell = (b) => b.pct >= 100
      ? `<button class="btn btn-sm btn-outline-success"
                 onclick="IPTSearch._importSbxHash('${esc(b.hash)}', ${jsStr(b.name)})">
           <i class="bi bi-box-arrow-in-down me-1"></i>Import
         </button>`
      : `<span class="text-secondary small">${b.pct != null ? b.pct + '%' : ''}</span>`;

    if (tierLabel) {
      // ── Good quality (2160p or 1080p) on sbx → show only that release ──────
      const tierColor = tierLabel === '2160p' ? 'text-warning' : 'text-info';
      const allRows   = [...document.querySelectorAll('[data-result-title]')];
      const sbxNorm   = best.norm;
      const matchRow  = allRows.find(row => {
        const rn = _normTitle(row.dataset.resultTitle);
        return rn && sbxNorm && (sbxNorm.includes(rn) || rn.includes(sbxNorm));
      });

      // Hide all rows except the matching one (if found in search results)
      allRows.forEach(row => { if (row !== matchRow) row.style.display = 'none'; });

      // If the sbx release wasn't in the search results at all, inject a synthetic row
      if (!matchRow) {
        const tbody = el.querySelector('tbody');
        if (tbody) {
          const sz     = best.size_bytes ? _humanSize(best.size_bytes) : '?';
          const pctStr = best.pct != null ? ` · ${best.pct}%` : '';
          tbody.insertAdjacentHTML('afterbegin', `
            <tr data-result-title="${esc(best.name)}" class="table-warning bg-opacity-25">
              <td>
                <div class="fw-semibold lh-sm">${esc(best.name)}
                  <span class="badge bg-warning text-dark ms-1">On SBX</span>
                </div>
                <div class="text-secondary" style="font-size:.75rem">
                  Seedbox · label: ${esc(best.label || '(none)')}${pctStr}
                </div>
              </td>
              <td class="text-nowrap text-secondary small">${sz}</td>
              <td class="text-nowrap small"><span class="text-secondary">—</span></td>
              <td><span class="badge bg-secondary">movie</span></td>
              <td>${_sbxActionCell(best)}</td>
            </tr>`);
        }
      }

      el.insertAdjacentHTML('afterbegin', `
        <div class="alert alert-warning py-2 small mb-2" id="sbx-tier-note"
             style="border-color:#f0ad4e;background:rgba(240,173,78,.1)">
          <i class="bi bi-hdd-network me-1 ${tierColor}"></i>
          Already on seedbox in <strong class="${tierColor}">${tierLabel}</strong> — showing only that version.
          <button class="btn btn-link btn-sm text-secondary p-0 ms-2" style="font-size:.75rem"
                  onclick="IPTSearch._showAllSbxResults()">Show all options</button>
        </div>`);

    } else {
      // ── Below threshold (720p / unknown) → keep all results + add sbx item ──
      const sbxNorm      = best.norm;
      const alreadyShown = [...document.querySelectorAll('[data-result-title]')].some(row => {
        const rn = _normTitle(row.dataset.resultTitle);
        return rn && sbxNorm && (sbxNorm.includes(rn) || rn.includes(sbxNorm));
      });

      if (!alreadyShown) {
        const tbody = el.querySelector('tbody');
        if (tbody) {
          const sz     = best.size_bytes ? _humanSize(best.size_bytes) : '?';
          const pctStr = best.pct != null ? ` · ${best.pct}%` : '';
          tbody.insertAdjacentHTML('afterbegin', `
            <tr data-result-title="${esc(best.name)}">
              <td>
                <div class="fw-semibold lh-sm text-secondary">${esc(best.name)}
                  <span class="badge bg-warning text-dark ms-1">On SBX</span>
                </div>
                <div class="text-secondary" style="font-size:.75rem">
                  Seedbox · label: ${esc(best.label || '(none)')}${pctStr}
                </div>
              </td>
              <td class="text-nowrap text-secondary small">${sz}</td>
              <td class="text-nowrap small"><span class="text-secondary">—</span></td>
              <td><span class="badge bg-secondary">movie</span></td>
              <td>${_sbxActionCell(best)}</td>
            </tr>`);
        }
      }

      el.insertAdjacentHTML('afterbegin', `
        <div class="alert alert-secondary py-2 small mb-2" style="border-color:#666">
          <i class="bi bi-exclamation-triangle me-1 text-warning"></i>
          A lower quality version is already on the seedbox — better options shown below.
        </div>`);
    }
  },

  // Restore all hidden rows and remove the sbx-tier banner
  _showAllSbxResults() {
    document.getElementById('sbx-tier-note')?.remove();
    document.querySelectorAll('[data-result-title]').forEach(row => { row.style.display = ''; });
  },

  // Import a seedbox torrent by hash (used by synthetic sbx rows)
  async _importSbxHash(hash, name) {
    try {
      const job = await API.post(`/sources/import/${encodeURIComponent(hash)}`, {});
      JobPoller.track(job.id, { type: 'sync' });
      JobsPanel.open();
      toast(`Importing ${name.slice(0, 50)}…`, 'info');
    } catch (e) {
      toast(`Import failed: ${e.message}`, 'danger');
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

function _normTitle(s) {
  // Strip common release tags and punctuation for loose name comparison
  return (s || '').toLowerCase()
    .replace(/\b(2160p|1080p|720p|4k|uhd|hdr\w*|web[-.]?dl|webrip|bluray|bdrip|hevc|h\.?26[45]|avc|x26[45]|dts|aac|dd[p+]?\d*|atmos|remux|repack|proper|\d{4})\b/g, '')
    .replace(/[._\-[\]()]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _showSbxConflict(detail) {
  const pct      = detail.pct != null ? detail.pct : null;
  const pctStr   = pct != null ? `${pct}%` : '?%';
  const label    = detail.label || '(no label)';
  const complete = pct != null && pct >= 100;
  const id       = 'sbx-conflict-modal';
  document.getElementById(id)?.remove();

  const importBtn = complete ? `
    <button class="btn btn-success btn-sm" id="sbx-import-btn">
      <i class="bi bi-box-arrow-in-down me-1"></i>Import Now
    </button>` : '';

  const html = `
    <div class="modal fade" id="${id}" tabindex="-1">
      <div class="modal-dialog">
        <div class="modal-content bg-dark text-white border-info">
          <div class="modal-header border-info py-2">
            <h6 class="modal-title mb-0">
              <i class="bi bi-arrow-down-circle-fill text-info me-2"></i>Already on Seedbox
            </h6>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body small">
            <p class="mb-2"><strong>${esc(detail.name)}</strong></p>
            <p class="text-secondary mb-0">
              <i class="bi bi-pie-chart me-1"></i>${pctStr} complete
              &nbsp;·&nbsp;
              <i class="bi bi-tag me-1"></i>label: <code class="text-info">${esc(label)}</code>
            </p>
            <p class="text-secondary mt-2 mb-0" style="font-size:.8rem">
              ${complete
                ? 'Complete — click Import Now to download to your library.'
                : 'Still downloading — it will be available once complete.'}
            </p>
          </div>
          <div class="modal-footer border-secondary py-2">
            ${importBtn}
            <button class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Dismiss</button>
          </div>
        </div>
      </div>
    </div>`;

  document.getElementById('app').insertAdjacentHTML('beforeend', html);
  const modalEl = document.getElementById(id);
  const modal   = new bootstrap.Modal(modalEl);
  modalEl.addEventListener('hidden.bs.modal', e => e.target.remove());

  if (complete) {
    modalEl.querySelector('#sbx-import-btn').addEventListener('click', async () => {
      modal.hide();
      try {
        const job = await API.post(`/sources/import/${encodeURIComponent(detail.hash)}`, {});
        JobPoller.track(job.id, { type: 'sync' });
        JobsPanel.open();
        toast(`Importing ${detail.name.slice(0, 50)}…`, 'info');
      } catch (e) {
        toast(`Import failed: ${e.message}`, 'danger');
      }
    });
  }

  modal.show();
}

function _shortDate(dateStr) {
  try {
    return new Date(dateStr).toLocaleDateString();
  } catch (_) { return dateStr; }
}

// ─────────────────────────────────────────────
// Hash router
// ─────────────────────────────────────────────
// ─────────────────────────────────────────────
// Movie Discovery (IMDB-keyed movie search)
// ─────────────────────────────────────────────
const MovieDiscover = {
  _tab: 'search',          // 'search' | 'history' | 'queue' | 'reviews'
  _searchResults: [],      // TMDB candidates from /movies/search
  _confirmed: null,        // full confirm response (plex/sbx/ipt/status)
  _grabbing: false,
  _plexCheckToken: null,   // cancel token for lazy Plex badge loading
  _showAllQualities: false,
  _showIptOverride: false, // show IPT even when movie is already 4K in Plex
  _preQuery: null,         // query pre-seeded from home search hero

  async render(tab) {
    this._tab = tab || this._tab || 'search';
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="d-flex align-items-center gap-3 mb-3">
        <h5 class="mb-0"><i class="bi bi-film text-info me-2"></i>Movies</h5>
      </div>
      <ul class="nav nav-tabs mb-3" id="movie-tabs">
        ${['search','history','queue','reviews'].map(t => `
          <li class="nav-item">
            <a class="nav-link ${this._tab===t?'active':''}" href="#/movies/${t}"
               id="movie-tab-${t}">${_movieTabLabel(t)}</a>
          </li>`).join('')}
      </ul>
      <div id="movie-tab-body"></div>`;
    await this._renderTab();
    this._pollReviewBadge();
  },

  // Navigate to movie search pre-seeded with a query (called from home hero)
  searchFor(query) {
    if (!query) return;
    this._preQuery = query;
    Router.go('/movies/search');
  },

  // Navigate to movie discover and auto-confirm by TMDB ID (called from browse page)
  goById(tmdbId) {
    if (!tmdbId) return;
    this._preConfirmId = tmdbId;
    Router.go('/movies/search');
  },

  async _renderTab() {
    const body = document.getElementById('movie-tab-body');
    if (!body) return;
    if      (this._tab === 'search')  await this._renderSearch(body);
    else if (this._tab === 'history') await this._renderHistory(body);
    else if (this._tab === 'queue')   await this._renderQueue(body);
    else if (this._tab === 'reviews') await this._renderReviews(body);
  },

  // ── Search tab ──────────────────────────────────────────────────────────────
  async _renderSearch(body) {
    body.innerHTML = `
      <div class="row g-3 mb-3">
        <div class="col-md-7">
          <div class="input-group">
            <input id="movie-search-input" type="text" class="form-control"
                   placeholder="Movie title…"
                   onkeydown="if(event.key==='Enter') MovieDiscover.search()">
            <button class="btn btn-info" onclick="MovieDiscover.search()">
              <i class="bi bi-search me-1"></i>Search
            </button>
          </div>
        </div>
      </div>
      <div id="movie-search-results"></div>
      <div id="movie-confirm-panel"></div>`;

    // Pre-seeded TMDB ID from browse page — auto-confirm
    if (this._preConfirmId) {
      const tmdbId = this._preConfirmId;
      this._preConfirmId = null;
      await this.confirm(tmdbId);
      return;
    }

    // Pre-seeded query from home search hero — fill in and auto-search
    if (this._preQuery) {
      const inp = document.getElementById('movie-search-input');
      if (inp) inp.value = this._preQuery;
      this._preQuery = null;
      await this.search();
      return;
    }

    // Re-render confirmed state if we have it (back-navigation)
    if (this._confirmed) {
      this._renderConfirmPanel(this._confirmed);
      return;
    }

    document.getElementById('movie-search-input')?.focus();
  },

  async search() {
    const q = document.getElementById('movie-search-input')?.value?.trim();
    if (!q) return;
    // Cancel any running Plex badge checks from previous search
    this._plexCheckToken = null;
    this._confirmed = null;
    this._showAllQualities = false;
    document.getElementById('movie-confirm-panel').innerHTML = '';
    const res = document.getElementById('movie-search-results');
    res.innerHTML = '<div class="text-secondary small py-2"><div class="spinner-border spinner-border-sm me-2"></div>Searching…</div>';
    try {
      this._searchResults = await API.get(`/movies/search?q=${enc(q)}`);
    } catch (e) {
      res.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
      return;
    }
    if (!this._searchResults.length) {
      res.innerHTML = '<div class="text-secondary py-2">No results found.</div>';
      return;
    }
    res.innerHTML = `
      <div class="row g-2">
        ${this._searchResults.map((r, i) => `
          <div class="col-6 col-md-4 col-lg-3">
            <div class="card h-100 border-secondary movie-candidate-card"
                 data-tmdb-id="${r.tmdb_id}"
                 onclick="MovieDiscover.confirm(${i})" style="cursor:pointer">
              ${r.poster_url
                ? `<img src="${esc(r.poster_url)}" class="card-img-top"
                        style="height:180px;object-fit:cover" alt="">`
                : `<div class="d-flex align-items-center justify-content-center bg-secondary"
                        style="height:180px"><i class="bi bi-film fs-1 text-muted"></i></div>`}
              <div class="card-body p-2">
                <div class="fw-semibold small">${esc(r.title)}</div>
                <div class="text-secondary small">${r.year || ''}</div>
                <div class="plex-badge mt-1">
                  <span class="spinner-grow spinner-grow-sm text-secondary"
                        style="width:.5rem;height:.5rem" role="status"></span>
                </div>
              </div>
            </div>
          </div>`).join('')}
      </div>`;

    // Start lazy Plex status checks (batches of 3, cancellable)
    this._lazyPlexCheck(this._searchResults);
  },

  async confirm(idx) {
    const candidate = this._searchResults[idx];
    if (!candidate) return;
    this._plexCheckToken = null;   // cancel any running badge checks
    this._showAllQualities = false;
    this._showIptOverride  = false;
    const panel = document.getElementById('movie-confirm-panel');
    panel.innerHTML = '<div class="text-secondary small py-3"><div class="spinner-border spinner-border-sm me-2"></div>Checking all systems…</div>';
    document.getElementById('movie-search-results').innerHTML = '';
    try {
      const data = await API.post('/movies/confirm', { tmdb_id: candidate.tmdb_id });
      this._confirmed = data;
      this._renderConfirmPanel(data);
    } catch (e) {
      panel.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
    }
  },

  _renderConfirmPanel(d) {
    const panel = document.getElementById('movie-confirm-panel');
    if (!panel) return;
    const statusBadge = _movieStatusBadge(d.status, d.plex?.resolution);
    const plexHtml = d.plex?.found
      ? `<span class="badge bg-success me-2">🟢 In Plex — ${esc(d.plex.resolution || '?')}</span>`
      : '<span class="badge bg-secondary me-2">Not in Plex</span>';
    const sbxHtml = d.sbx?.found
      ? `<span class="badge bg-warning text-dark me-2">🟡 On Seedbox ${d.sbx.pct != null ? d.sbx.pct+'%' : ''}</span>`
      : '';
    const upgradeAlert = d.upgrade_available
      ? `<div class="alert alert-warning py-2 mb-3">
           <i class="bi bi-arrow-up-circle me-1"></i>
           Better quality available — you can upgrade from ${esc(d.plex?.resolution || '?')}
           to ${esc(d.ipt?.best_resolution || '?')}.
         </div>`
      : '';

    const iptHtml = MovieDiscover._buildIptSection(d);

    panel.innerHTML = `
      <div class="card border-secondary mt-3">
        <div class="card-body">
          <div class="row g-3">
            <div class="col-auto">
              ${d.poster_url
                ? `<img src="${esc(d.poster_url)}" style="width:100px;border-radius:6px" alt="">`
                : `<div class="d-flex align-items-center justify-content-center bg-secondary rounded"
                        style="width:100px;height:150px"><i class="bi bi-film fs-2"></i></div>`}
            </div>
            <div class="col">
              <h5 class="mb-1">${esc(d.title)} <span class="text-secondary fs-6">(${d.year || '?'})</span></h5>
              <div class="mb-2">${statusBadge}</div>
              <div class="mb-2">${plexHtml}${sbxHtml}</div>
              ${d.vote_average ? `<div class="text-secondary small mb-1">★ ${d.vote_average.toFixed(1)}</div>` : ''}
              ${d.genres?.length ? `<div class="text-secondary small mb-2">${esc(d.genres.join(' · '))}</div>` : ''}
              <p class="small text-secondary mb-0">${esc((d.overview||'').slice(0,200))}${(d.overview||'').length>200?'…':''}</p>
            </div>
          </div>
          ${upgradeAlert}
          <div id="movie-ipt-section">${iptHtml}</div>
          <div class="mt-3 d-flex gap-2">
            <button class="btn btn-sm btn-outline-secondary"
                    onclick="MovieDiscover._clearConfirm()">
              <i class="bi bi-arrow-left me-1"></i>Back to search
            </button>
          </div>
        </div>
      </div>`;
  },

  async grab(torrentUrl, imdbId, title) {
    if (this._grabbing) return;
    this._grabbing = true;
    try {
      await API.post('/iptorrents/grab', {
        torrent_url:    torrentUrl,
        label:          '',
        suggested_type: 'movies',
        title:          title,
        imdb_id:        imdbId,
      });
      // Navigate home and show Jobs so user can watch download progress
      this._grabbing = false;
      Router.go('/');
      await Router.route();
      JobsPanel.open();
      JobsPanel.refresh();
    } catch (e) {
      if (e.status === 409 && e.detail?.conflict) {
        toast(`Already on seedbox: ${e.detail.name} (${e.detail.pct}%)`, 'warning');
      } else {
        toast(`Grab failed: ${e.message}`, 'danger');
      }
      this._grabbing = false;
    }
  },

  async _queueThis() {
    if (!this._confirmed?.imdb_id) return;
    try {
      await API.post(`/movies/queue/${enc(this._confirmed.imdb_id)}`, { min_resolution: '2160p' });
      // Navigate home and show Jobs so user can watch the immediate check run
      Router.go('/');
      await Router.route();
      JobsPanel.open();
      JobsPanel.refresh();
    } catch (e) {
      toast(`Queue failed: ${e.message}`, 'danger');
    }
  },

  // ── Lazy Plex badge loading ───────────────────────────────────────────────────
  async _lazyPlexCheck(results) {
    const token = Symbol();
    this._plexCheckToken = token;
    const BATCH = 3;
    for (let i = 0; i < results.length; i += BATCH) {
      if (this._plexCheckToken !== token) return;  // cancelled by new search/confirm
      const batch = results.slice(i, i + BATCH);
      await Promise.all(batch.map(r => this._plexBadgeOne(r.tmdb_id, token)));
    }
  },

  async _plexBadgeOne(tmdbId, token) {
    try {
      const s = await API.get(`/movies/plex-check?tmdb_id=${tmdbId}`);
      if (this._plexCheckToken !== token) return;   // navigated away while waiting
      const card  = document.querySelector(`[data-tmdb-id="${tmdbId}"]`);
      const badge = card?.querySelector('.plex-badge');
      if (!badge) return;
      if (s.found) {
        const rank     = s.resolution_rank ?? -1;
        const cls      = rank >= 4 ? 'bg-success'
                       : rank >= 2 ? 'bg-warning text-dark'
                       :             'bg-danger';
        const icon     = rank >= 4 ? '🟢' : rank >= 2 ? '🟡' : '🔴';
        const resLabel = s.resolution ? ` · ${esc(s.resolution)}` : '';
        badge.innerHTML = `<span class="badge ${cls}" style="font-size:.65rem">
          ${icon} In Plex${resLabel}
        </span>`;
      } else {
        badge.innerHTML = '';   // not found — clean empty
      }
    } catch (_) {
      // silently clear spinner on error
      const badge = document.querySelector(`[data-tmdb-id="${tmdbId}"] .plex-badge`);
      if (badge) badge.innerHTML = '';
    }
  },

  _renderIptTable(d, showAll) {
    const ipt     = d.ipt;
    const results = showAll ? (ipt.all_results || []) : (ipt.results || []);
    const isUpgrade = d.status === 'upgrading' || d.status === 'in_library';
    const btnLabel  = isUpgrade ? 'Upgrade' : 'Get';

    const _resBadge = res => {
      const cls = res === '2160p' ? 'bg-warning text-dark'
                : res === '1440p' ? 'bg-info text-dark'
                : res === '1080p' ? 'bg-primary'
                : res === '720p'  ? 'bg-secondary'
                : 'bg-secondary';
      return `<span class="badge ${cls}">${esc(res)}</span>`;
    };

    const _fitBadge = fit => {
      const map = {
        ideal:   ['bg-success',          'Ideal'],
        ok:      ['bg-warning text-dark', 'OK'],
        large:   ['bg-danger',            'Large'],
        small:   ['bg-secondary',         'Small'],
        unknown: ['bg-secondary',         '—'],
      };
      const [cls, label] = map[fit] || map.unknown;
      return `<span class="badge ${cls}" style="font-size:.65rem">${label}</span>`;
    };

    const filterBanner = ipt.filtered_by_quality && !showAll
      ? `<div class="alert alert-info py-2 small mb-2" style="border-color:#0dcaf0">
           <i class="bi bi-funnel-fill me-1"></i>
           Showing only <strong>${esc(ipt.best_resolution || 'better')}</strong> copies
           (you have <strong>${esc(ipt.current_plex_resolution || '?')}</strong> in Plex).
           <button class="btn btn-link btn-sm p-0 ms-2" style="font-size:.8rem"
                   onclick="MovieDiscover._toggleAllQualities()">Show all qualities</button>
         </div>`
      : showAll && ipt.filtered_by_quality
      ? `<div class="alert alert-secondary py-2 small mb-2" style="border-color:#444">
           <i class="bi bi-eye me-1"></i>Showing all qualities.
           <button class="btn btn-link btn-sm p-0 ms-2" style="font-size:.8rem"
                   onclick="MovieDiscover._toggleAllQualities()">Back to upgrade view</button>
         </div>`
      : '';

    const titleMatchBanner = ipt.search_method === 'title'
      ? `<div class="alert alert-warning py-2 small mb-2 d-flex align-items-center gap-2">
           <i class="bi bi-exclamation-triangle-fill flex-shrink-0"></i>
           <span>
             <strong>Title match only</strong> — IPT has no IMDB ID indexed for this torrent.
             Results are matched by title and may not be the correct film.
             ${d.imdb_id
               ? `<a href="https://www.imdb.com/title/${esc(d.imdb_id)}/" target="_blank"
                     rel="noopener" class="ms-1 text-warning">
                    Verify on IMDB <i class="bi bi-box-arrow-up-right" style="font-size:.7rem"></i>
                  </a>`
               : ''}
           </span>
         </div>`
      : '';

    if (!results.length) {
      const noMsg = ipt.filtered_by_quality && !showAll
        ? `No copies above ${esc(ipt.current_plex_resolution||'?')} found on IPT.
           <button class="btn btn-link btn-sm p-0 ms-1" onclick="MovieDiscover._toggleAllQualities()">
             Show all qualities
           </button>`
        : `Not found on IPT.
           <button class="btn btn-sm btn-outline-secondary ms-2" onclick="MovieDiscover._queueThis()">
             <i class="bi bi-clock me-1"></i>Watch for it
           </button>`;
      return `<div class="mt-3 text-secondary small">${titleMatchBanner}${filterBanner}${noMsg}</div>`;
    }

    const rows = results.map((r, idx) => {
      const isBest  = idx === 0 && !showAll || r.torrent_id === ipt.best?.torrent_id;
      const bestTag = isBest && idx === 0
        ? `<span class="badge bg-success me-1" style="font-size:.65rem">Best Pick</span>`
        : '';
      const title = r.title.length > 55 ? r.title.slice(0, 55) + '…' : r.title;
      return `
        <tr class="${isBest && idx === 0 ? 'table-success' : ''}">
          <td class="text-nowrap">${bestTag}${_resBadge(r.resolution)}</td>
          <td class="small" style="max-width:260px">
            <div class="text-truncate" title="${esc(r.title)}">${esc(title)}</div>
            <div class="text-secondary" style="font-size:.7rem">${esc(r.ipt_category||'')}</div>
          </td>
          <td class="text-nowrap small">
            ${_humanSize(r.size_bytes)}
            <div class="mt-1">${_fitBadge(r.size_fitness)}</div>
          </td>
          <td class="text-nowrap small text-success">${r.seeders}</td>
          <td>
            <button class="btn btn-sm btn-outline-info py-0"
                    onclick="MovieDiscover.grab(${jsStr(r.torrent_url)},${jsStr(d.imdb_id)},${jsStr(r.title)})">
              ${btnLabel}
            </button>
          </td>
        </tr>`;
    }).join('');

    const runtime = ipt.runtime_minutes
      ? `<span class="text-secondary ms-2" style="font-size:.75rem">
           (runtime ${ipt.runtime_minutes} min · ideal ~${Math.round(15 * ipt.runtime_minutes / 120)} GB)
         </span>`
      : '';

    return `
      <div class="mt-3">
        <div class="text-secondary small fw-semibold mb-2 text-uppercase d-flex align-items-center gap-2"
             style="letter-spacing:.05em">
          <span>Available on IPT</span>${runtime}
        </div>
        ${titleMatchBanner}${filterBanner}
        <div class="table-responsive">
          <table class="table table-dark table-hover table-sm align-middle mb-0">
            <thead class="text-secondary">
              <tr><th>Quality</th><th>Title</th><th>Size</th><th title="Seeds">S</th><th></th></tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  },

  _buildIptSection(d) {
    // If the movie is already at 4K in Plex and the user hasn't asked to see
    // IPT anyway, replace the table with a single "already good" note.
    const has4k = d.plex?.found && /^(2160p|4k|uhd)$/i.test(d.plex.resolution || '');
    if (has4k && !this._showIptOverride) {
      return `
        <div class="mt-3 small text-secondary">
          <i class="bi bi-check-circle-fill text-success me-1"></i>
          Already in Plex at 4K.
          <button class="btn btn-link btn-sm p-0 ms-1" style="font-size:.8rem"
                  onclick="MovieDiscover._toggleIptOverride()">
            Search IPT anyway
          </button>
        </div>`;
    }
    if (d.ipt?.results?.length || d.ipt?.all_results?.length) {
      return MovieDiscover._renderIptTable(d, this._showAllQualities);
    }
    if (d.ipt?.configured) {
      return `
        <div class="mt-3 text-secondary small">
          Not found on IPT.
          <button class="btn btn-sm btn-outline-secondary ms-2"
                  onclick="MovieDiscover._queueThis()">
            <i class="bi bi-clock me-1"></i>Watch for it
          </button>
        </div>`;
    }
    return '';
  },

  _toggleIptOverride() {
    this._showIptOverride = true;
    const iptSection = document.getElementById('movie-ipt-section');
    if (iptSection && this._confirmed) {
      iptSection.innerHTML = this._buildIptSection(this._confirmed);
    }
  },

  _toggleAllQualities() {
    this._showAllQualities = !this._showAllQualities;
    if (!this._confirmed) return;
    const iptSection = document.getElementById('movie-ipt-section');
    if (iptSection) {
      iptSection.innerHTML = this._buildIptSection(this._confirmed);
    }
  },

  _clearConfirm() {
    this._confirmed = null;
    this._showAllQualities = false;
    this._showIptOverride  = false;
    this._plexCheckToken = null;
    this._renderSearch(document.getElementById('movie-tab-body'));
  },

  // ── History tab ─────────────────────────────────────────────────────────────
  async _renderHistory(body) {
    body.innerHTML = '<div class="text-secondary small py-2"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</div>';
    let rows;
    try { rows = await API.get('/movies/history'); }
    catch (e) { body.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; return; }
    if (!rows.length) {
      body.innerHTML = '<div class="text-secondary py-3">No movies searched yet.</div>';
      return;
    }
    body.innerHTML = `
      <div class="table-responsive">
        <table class="table table-sm table-hover">
          <thead><tr><th>Movie</th><th>Status</th><th>Plex</th><th>Last searched</th><th></th></tr></thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td>
                  <span class="fw-semibold">${esc(r.title)}</span>
                  <span class="text-secondary small ms-1">${r.year||''}</span>
                </td>
                <td>${_movieStatusBadge(r.status, r.plex_resolution)}</td>
                <td>${r.plex_found
                      ? `<span class="badge bg-success">${esc(r.plex_resolution||'?')}</span>`
                      : '<span class="text-secondary small">—</span>'}</td>
                <td class="text-secondary small">${_relTime(r.last_searched)}</td>
                <td>
                  <button class="btn btn-sm btn-outline-secondary py-0"
                          onclick="MovieDiscover._reconfirm(${jsStr(r.imdb_id)},${r.tmdb_id})">
                    <i class="bi bi-arrow-repeat"></i>
                  </button>
                </td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  },

  async _reconfirm(imdbId, tmdbId) {
    this._tab = 'search';
    document.querySelectorAll('.nav-link[id^="movie-tab-"]').forEach(el => {
      el.classList.toggle('active', el.id === 'movie-tab-search');
    });
    const body = document.getElementById('movie-tab-body');
    await this._renderSearch(body);
    const panel = document.getElementById('movie-confirm-panel');
    panel.innerHTML = '<div class="text-secondary small py-3"><div class="spinner-border spinner-border-sm me-2"></div>Refreshing…</div>';
    try {
      const data = await API.post('/movies/confirm', { tmdb_id: tmdbId });
      this._confirmed = data;
      this._renderConfirmPanel(data);
    } catch (e) {
      panel.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
    }
  },

  // ── Queue tab ────────────────────────────────────────────────────────────────
  async _renderQueue(body) {
    body.innerHTML = '<div class="text-secondary small py-2"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</div>';
    let rows;
    try { rows = await API.get('/movies/queue'); }
    catch (e) { body.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; return; }
    if (!rows.length) {
      body.innerHTML = `
        <div class="text-center py-5 text-secondary">
          <i class="bi bi-clock fs-1"></i>
          <p class="mt-3">No movies in queue.<br>
          <small>When a movie isn't found on IPT, use "Watch for it" to queue it.</small></p>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="text-secondary small mb-3">
        Checked every 4 hours. Auto-grabs when found at the minimum quality.
      </div>
      <div class="row g-3">
        ${rows.map(r => `
          <div class="col-md-6 col-lg-4">
            <div class="card border-secondary">
              <div class="card-body d-flex gap-3 align-items-start">
                ${r.poster_url
                  ? `<img src="${esc(r.poster_url)}" style="width:60px;border-radius:4px" alt="">`
                  : `<div class="bg-secondary rounded d-flex align-items-center justify-content-center"
                          style="width:60px;height:90px"><i class="bi bi-film"></i></div>`}
                <div class="flex-grow-1">
                  <div class="fw-semibold">${esc(r.title)}</div>
                  <div class="text-secondary small">${r.year||''}</div>
                  <div class="small mt-1">Min: <span class="badge bg-secondary">${esc(r.queue_min_res||'2160p')}</span></div>
                  <div class="text-secondary small">Checked ${r.queue_check_count||0} time(s)</div>
                  ${r.queue_checked_at ? `<div class="text-secondary small">Last: ${_relTime(r.queue_checked_at)}</div>` : ''}
                </div>
                <button class="btn btn-sm btn-outline-danger py-0"
                        onclick="MovieDiscover._dequeue(${jsStr(r.imdb_id)})">
                  <i class="bi bi-x"></i>
                </button>
              </div>
            </div>
          </div>`).join('')}
      </div>`;
  },

  async _dequeue(imdbId) {
    try {
      await API.del(`/movies/queue/${enc(imdbId)}`);
      await this._renderQueue(document.getElementById('movie-tab-body'));
    } catch (e) {
      toast(`Error: ${e.message}`, 'danger');
    }
  },

  // ── Reviews tab ──────────────────────────────────────────────────────────────
  async _renderReviews(body) {
    body.innerHTML = '<div class="text-secondary small py-2"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</div>';
    let rows;
    try { rows = await API.get('/movies/reviews'); }
    catch (e) { body.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; return; }
    if (!rows.length) {
      body.innerHTML = `
        <div class="text-center py-5 text-secondary">
          <i class="bi bi-check-circle fs-1 text-success"></i>
          <p class="mt-3">No pending upgrade reviews.</p>
        </div>`;
      return;
    }
    body.innerHTML = rows.map(r => `
      <div class="card border-warning mb-3">
        <div class="card-header d-flex justify-content-between align-items-center py-2">
          <span class="fw-semibold">${esc(r.title)}</span>
          <span class="badge bg-warning text-dark">Pending Review</span>
        </div>
        <div class="card-body">
          <div class="row g-3">
            <div class="col-md-6">
              <div class="p-3 rounded border border-secondary">
                <div class="text-danger small fw-semibold mb-2">
                  <i class="bi bi-trash me-1"></i>Old Copy (in trash)
                </div>
                <div class="small">${esc(r.old_filename || 'Unknown')}</div>
                <div class="text-secondary small">${_humanSize(r.old_size_bytes)} · ${esc(r.old_resolution || '?')}</div>
                <div class="text-secondary small text-truncate" title="${esc(r.old_path)}">${esc(r.old_path)}</div>
              </div>
            </div>
            <div class="col-md-6">
              <div class="p-3 rounded border border-success">
                <div class="text-success small fw-semibold mb-2">
                  <i class="bi bi-stars me-1"></i>New Copy (in library)
                </div>
                <div class="small">${esc(r.new_filename || 'Unknown')}</div>
                <div class="text-secondary small">${_humanSize(r.new_size_bytes)} · ${esc(r.new_resolution || '?')}</div>
                <div class="text-secondary small text-truncate" title="${esc(r.new_path)}">${esc(r.new_path)}</div>
              </div>
            </div>
          </div>
          <div class="d-flex gap-2 mt-3">
            <button class="btn btn-success" onclick="MovieDiscover._confirmReview(${r.id})">
              <i class="bi bi-check-lg me-1"></i>Confirm — Delete Old Copy
            </button>
            <button class="btn btn-outline-danger" onclick="MovieDiscover._revertReview(${r.id})">
              <i class="bi bi-arrow-counterclockwise me-1"></i>Revert — Restore Old
            </button>
          </div>
        </div>
      </div>`).join('');
  },

  async _confirmReview(id) {
    try {
      await API.post(`/movies/reviews/${id}/confirm`, {});
      toast('Old copy deleted. Upgrade confirmed!', 'success');
      await this._renderReviews(document.getElementById('movie-tab-body'));
      this._pollReviewBadge();
    } catch (e) { toast(e.message, 'danger'); }
  },

  async _revertReview(id) {
    try {
      await API.post(`/movies/reviews/${id}/revert`, {});
      toast('Reverted. Old copy restored.', 'info');
      await this._renderReviews(document.getElementById('movie-tab-body'));
      this._pollReviewBadge();
    } catch (e) { toast(e.message, 'danger'); }
  },

  // ── Badge polling ─────────────────────────────────────────────────────────────
  async _pollReviewBadge() {
    try {
      const { count } = await API.get('/movies/reviews/count');
      const badge = document.getElementById('nav-reviews-badge');
      if (badge) {
        badge.textContent = count;
        badge.classList.toggle('d-none', count === 0);
      }
    } catch (_) {}
  },
};

function _movieTabLabel(t) {
  return { search: 'Search', history: 'History', queue: 'Watching', reviews: 'Pending Review' }[t] || t;
}

function _movieStatusBadge(status, resolution) {
  const map = {
    in_library: '<span class="badge bg-success">🟢 In Library</span>',
    upgrading:  `<span class="badge bg-info text-dark">🔵 In Library (${esc(resolution||'?')}) — Upgrade available</span>`,
    grabbed:    '<span class="badge bg-warning text-dark">🟡 On Seedbox</span>',
    available:  '<span class="badge bg-primary">🔵 Available</span>',
    wanted:     '<span class="badge bg-secondary">⏳ Watching</span>',
    not_found:  '<span class="badge bg-secondary">⚪ Not Found</span>',
  };
  return map[status] || `<span class="badge bg-secondary">${esc(status)}</span>`;
}

function _relTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const diff = (Date.now() - d) / 1000;
  if (diff < 60)  return 'just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

// ─────────────────────────────────────────────
// About page
// ─────────────────────────────────────────────
const AboutPage = {
  async render() {
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="mb-0"><i class="bi bi-info-circle me-2 text-secondary"></i>About</h5>
        <button class="btn btn-sm btn-outline-secondary" onclick="AboutPage.render()">
          <i class="bi bi-arrow-clockwise me-1"></i>Re-check
        </button>
      </div>
      <div id="about-body">
        <div class="text-secondary small"><span class="spinner-border spinner-border-sm me-2"></span>Checking connections…</div>
      </div>`;
    try {
      const data = await API.get('/about');
      document.getElementById('about-body').innerHTML = AboutPage._html(data);
    } catch (e) {
      document.getElementById('about-body').innerHTML =
        `<div class="alert alert-danger">${esc(e.message)}</div>`;
    }
  },

  _html(d) {
    // ── Version card ──────────────────────────────────────────────────────────
    const verLabel  = d.version && d.version !== 'dev' ? d.version : 'dev';
    const revLabel  = d.revision ? `<span class="text-secondary ms-2" style="font-size:.8rem">${esc(d.revision)}</span>` : '';
    const dateLabel = d.build_date
      ? `<div class="text-secondary small mt-1">Built ${esc(d.build_date.slice(0, 10))}</div>`
      : '';

    const versionCard = `
      <div class="card border-secondary mb-4" style="max-width:420px">
        <div class="card-body py-2 px-3">
          <div class="d-flex align-items-center gap-2">
            <img src="/static/img/icon.svg" width="28" height="28" style="border-radius:5px" alt="">
            <div>
              <span class="fw-semibold">Staven Media Manager</span>
              <span class="badge bg-secondary ms-2">${esc(verLabel)}</span>${revLabel}
              ${dateLabel}
            </div>
          </div>
        </div>
      </div>`;

    // ── Connection check cards ────────────────────────────────────────────────
    const SERVICE_META = {
      plex:       { label: 'Plex',        icon: 'bi-display',        color: '#e5a00d' },
      rtorrent:   { label: 'rTorrent',    icon: 'bi-cloud-download', color: '#6ea8fe' },
      iptorrents: { label: 'IPTorrents',  icon: 'bi-database',       color: '#20c997' },
      tmdb:       { label: 'TMDB',        icon: 'bi-film',           color: '#01b4e4' },
      btn:        { label: 'BTN',         icon: 'bi-broadcast',      color: '#a78bfa' },
    };

    const cards = Object.entries(SERVICE_META).map(([key, meta]) => {
      const chk = d.checks?.[key] || {};
      let dot, statusText, msText = '';
      if (!chk.configured) {
        dot        = `<span style="color:#6c757d;font-size:1.1rem">&#9679;</span>`;
        statusText = `<span class="text-secondary">Not configured</span>`;
      } else if (chk.ok) {
        dot        = `<span style="color:#198754;font-size:1.1rem">&#9679;</span>`;
        statusText = `<span class="text-success-emphasis">${esc(chk.detail || 'OK')}</span>`;
        if (chk.ms != null) msText = `<span class="text-secondary ms-2" style="font-size:.72rem">${chk.ms}ms</span>`;
      } else {
        dot        = `<span style="color:#dc3545;font-size:1.1rem">&#9679;</span>`;
        statusText = `<span class="text-danger">${esc(chk.detail || 'Error')}</span>`;
      }

      return `
        <div class="card border-secondary">
          <div class="card-body py-2 px-3">
            <div class="d-flex align-items-start gap-2">
              <i class="bi ${meta.icon} mt-1" style="color:${meta.color};font-size:1.1rem;flex-shrink:0"></i>
              <div class="min-w-0">
                <div class="fw-semibold small">${meta.label}</div>
                <div class="small d-flex align-items-center gap-1 flex-wrap">
                  ${dot} ${statusText}${msText}
                </div>
              </div>
            </div>
          </div>
        </div>`;
    }).join('');

    return `
      ${versionCard}
      <h6 class="text-secondary text-uppercase mb-2" style="font-size:.75rem;letter-spacing:.06em">Connections</h6>
      <div class="d-grid gap-2" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr));display:grid">
        ${cards}
      </div>`;
  },
};

// ─────────────────────────────────────────────
// Browse — recently uploaded movies on IPT
// ─────────────────────────────────────────────
const BrowsePage = {
  _pageSize: 24,
  _offset: 0,
  _total: 0,

  async render() {
    this._offset = 0;
    this._total = 0;
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="d-flex align-items-baseline gap-2 mb-3">
        <h5 class="mb-0"><i class="bi bi-fire text-danger me-2"></i>New on IPT</h5>
        <span class="text-secondary small" id="browse-count"></span>
      </div>
      <div id="browse-grid" class="row g-3">
        <div class="col-12 text-center py-4">
          <div class="spinner-border text-secondary"></div>
          <div class="text-secondary small mt-2">Loading recent uploads…</div>
        </div>
      </div>
      <div id="browse-more" class="text-center mt-3"></div>`;
    await this._loadPage();
  },

  async _loadPage() {
    const grid = document.getElementById('browse-grid');
    const moreEl = document.getElementById('browse-more');
    if (this._offset === 0) {
      grid.innerHTML = `
        <div class="col-12 text-center py-4">
          <div class="spinner-border text-secondary"></div>
        </div>`;
    } else if (moreEl) {
      moreEl.innerHTML = `<div class="spinner-border spinner-border-sm text-secondary"></div>`;
    }

    let data;
    try {
      data = await API.get(`/iptorrents/browse?limit=${this._pageSize}&offset=${this._offset}`);
    } catch (e) {
      grid.innerHTML =
        `<div class="col-12"><div class="alert alert-danger">${esc(e.message)}</div></div>`;
      return;
    }

    const movies = data.items || [];
    this._total = data.total || 0;

    const countEl = document.getElementById('browse-count');
    if (countEl) countEl.textContent = `${this._total} movies`;

    if (!movies.length && this._offset === 0) {
      grid.innerHTML =
        '<div class="col-12 text-secondary text-center py-4">No recent movies found.</div>';
      return;
    }

    const cards = movies.map(m => {
      const resLabel = m.best_res
        ? `<span class="badge ${m.best_res === '2160p' || m.best_res === '4k' ? 'bg-success' : 'bg-secondary'} me-1">${esc(m.best_res)}</span>`
        : '';
      return `
        <div class="col-6 col-md-4 col-lg-3 col-xl-2">
          <div class="card h-100 match-result-card"
               onclick="${m.tmdb_id ? `MovieDiscover.goById(${m.tmdb_id})` : ''}"
               style="${m.tmdb_id ? 'cursor:pointer' : 'cursor:default'}">
            ${m.poster_url
              ? `<img src="${esc(m.poster_url)}" class="card-img-top" alt=""
                      style="aspect-ratio:2/3;object-fit:cover" loading="lazy">`
              : `<div class="card-img-top d-flex align-items-center justify-content-center bg-dark"
                      style="aspect-ratio:2/3"><i class="bi bi-film text-secondary" style="font-size:2.5rem"></i></div>`
            }
            <div class="card-body p-2">
              <div class="small fw-semibold lh-sm">${esc(m.title)}</div>
              <div class="text-secondary" style="font-size:.75rem">${m.year ?? ''}</div>
              <div class="mt-1">
                ${resLabel}
                <span class="text-secondary" style="font-size:.7rem">
                  ${m.release_count} release${m.release_count !== 1 ? 's' : ''}
                  · ${m.total_seeds} seed${m.total_seeds !== 1 ? 's' : ''}
                </span>
              </div>
            </div>
          </div>
        </div>`;
    }).join('');

    if (this._offset === 0) {
      grid.innerHTML = cards;
    } else {
      grid.insertAdjacentHTML('beforeend', cards);
    }

    this._offset += movies.length;

    if (moreEl) {
      if (this._offset < this._total) {
        moreEl.innerHTML = `
          <button class="btn btn-outline-secondary btn-sm" onclick="BrowsePage.loadMore()">
            <i class="bi bi-arrow-down-circle me-1"></i>Load more
            <span class="text-secondary ms-1">(${this._offset}/${this._total})</span>
          </button>`;
      } else {
        moreEl.innerHTML = '';
      }
    }
  },

  async loadMore() {
    await this._loadPage();
  },
};

// ─────────────────────────────────────────────
// Switch Library — browse games/switch/ROMS
// ─────────────────────────────────────────────
const SwitchLibrary = {
  _importing: false,
  _targets: [],
  _scanData: null,

  async render() {
    Views._setApp(`
      <div class="d-flex align-items-center justify-content-between mb-3 flex-wrap gap-2">
        <div class="d-flex align-items-center gap-2">
          <i class="bi bi-joystick text-success fs-5"></i>
          <h5 class="mb-0">Switch Library</h5>
          <span class="text-secondary small" id="switch-lib-count"></span>
        </div>
        <button class="btn btn-sm btn-outline-success" id="switch-import-btn"
                onclick="SwitchLibrary.runImport()">
          <i class="bi bi-cloud-download me-1"></i>Scan &amp; Import
        </button>
      </div>
      <div id="switch-lib-body">
        <div class="text-center py-5"><div class="spinner-border text-secondary"></div></div>
      </div>`);
    await this._load();
  },

  async _load() {
    let data, targets;
    try {
      [data, targets] = await Promise.all([
        API.get('/switch/scan'),
        API.get('/switch/targets').catch(() => []),
      ]);
    } catch (e) {
      document.getElementById('switch-lib-body').innerHTML =
        `<div class="alert alert-danger">${esc(e.message)}</div>`;
      return;
    }
    this._scanData = data;
    this._targets  = targets;

    const countEl = document.getElementById('switch-lib-count');
    if (countEl) countEl.textContent =
      `${data.total} game${data.total !== 1 ? 's' : ''}`
      + (data.unmatched ? ` · ${data.unmatched} unmatched` : '');

    const matched   = data.found.filter(g => g.matched);
    const unmatched = data.found.filter(g => !g.matched);

    let html = '';
    if (matched.length) {
      html += `<div class="row g-3 mb-4">${matched.map(g => this._card(g)).join('')}</div>`;
    }
    if (unmatched.length) {
      html += `
        <div class="d-flex align-items-center gap-2 mb-2 mt-2">
          <span class="text-warning small fw-semibold text-uppercase" style="letter-spacing:.06em">
            <i class="bi bi-exclamation-triangle me-1"></i>Unmatched (${unmatched.length})
          </span>
          <span class="text-secondary small">— click Scan &amp; Import to create DB records</span>
        </div>
        <div class="row g-3">${unmatched.map(g => this._card(g)).join('')}</div>`;
    }
    if (!data.total) {
      html = `<div class="text-center py-5 text-secondary">
        <i class="bi bi-joystick fs-1 d-block mb-2"></i>
        No folders found in games/switch/ROMS
      </div>`;
    }
    document.getElementById('switch-lib-body').innerHTML = html;
  },

  _coverSrc(g) {
    if (g.cover_url) return esc(g.cover_url);
    if (g.has_cover || g.cover_local) {
      const id = g.title_id ? `&id=${g.title_id}` : '';
      return `/api/switch/cover-image?title=${enc(g.title)}${id}`;
    }
    return null;
  },

  _card(g) {
    const coverSrc = this._coverSrc(g);
    const imgHtml = coverSrc
      ? `<img src="${coverSrc}" class="card-img-top switch-cover" alt=""
              style="aspect-ratio:3/4;object-fit:cover" loading="lazy"
              onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
         <div class="card-img-top switch-cover-fallback" style="aspect-ratio:3/4;display:none">
           <i class="bi bi-joystick text-secondary" style="font-size:2.5rem"></i>
         </div>`
      : `<div class="card-img-top switch-cover-fallback" style="aspect-ratio:3/4">
           <i class="bi bi-joystick text-secondary" style="font-size:2.5rem"></i>
         </div>`;

    const genreTags = g.genres
      ? g.genres.split(',').slice(0, 2).map(x =>
          `<span class="badge bg-dark border border-secondary" style="font-size:.6rem">${esc(x.trim())}</span>`
        ).join('')
      : '';

    const playersBadge = g.num_players
      ? `<span class="badge bg-dark border border-secondary" style="font-size:.6rem">
           <i class="bi bi-people-fill me-1"></i>${esc(g.num_players)}
         </span>`
      : '';

    const year = g.release_date ? g.release_date.slice(0, 4) : '';

    const installBtn = g.matched && this._targets.length
      ? `<button class="btn btn-success btn-sm w-100 mt-2" style="font-size:.75rem"
                 onclick="event.stopPropagation();SwitchLibrary.install(${g.title_id})">
           <i class="bi bi-send-fill me-1"></i>Install
         </button>`
      : '';

    const unmatchedBadge = !g.matched
      ? `<span class="badge bg-warning text-dark" style="font-size:.6rem">unmatched</span>`
      : '';

    return `
      <div class="col-6 col-md-4 col-lg-3 col-xl-2">
        <div class="card h-100 switch-game-card${g.matched ? ' clickable' : ''}"
             ${g.matched ? `onclick="SwitchLibrary.showDetail(${g.title_id})"` : ''}>
          ${imgHtml}
          <div class="card-body p-2 d-flex flex-column">
            <div class="small fw-semibold lh-sm mb-1">${esc(g.title)}</div>
            ${g.publisher ? `<div class="text-secondary lh-sm mb-1" style="font-size:.7rem">${esc(g.publisher)}</div>` : ''}
            <div class="d-flex flex-wrap gap-1 mb-1">
              ${genreTags}${playersBadge}${unmatchedBadge}
              ${year ? `<span class="text-secondary" style="font-size:.65rem">${esc(year)}</span>` : ''}
            </div>
            ${g.description ? `<div class="text-secondary switch-desc mt-auto" style="font-size:.68rem;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(g.description)}</div>` : ''}
            ${installBtn}
          </div>
        </div>
      </div>`;
  },

  async runImport() {
    if (this._importing) return;
    this._importing = true;
    const btn = document.getElementById('switch-import-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Importing…'; }
    try {
      const result = await API.post('/switch/scan-import', {});
      toast(`Imported ${result.imported} game${result.imported !== 1 ? 's' : ''}, ${result.skipped} already matched.`, 'success');
      if (result.errors?.length) result.errors.forEach(e => toast(`${e.folder}: ${e.error}`, 'warning'));
      await this._load();
    } catch (e) {
      toast(`Import failed: ${e.message}`, 'danger');
    } finally {
      this._importing = false;
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-cloud-download me-1"></i>Scan &amp; Import'; }
    }
  },

  async install(titleId) {
    if (!this._targets.length) { toast('No Switch targets configured', 'warning'); return; }

    // Single target: install immediately. Multiple targets: show picker.
    if (this._targets.length === 1) {
      await this._doInstall(titleId, this._targets[0]);
      return;
    }

    // Picker modal for multiple targets
    const opts = this._targets.map(t =>
      `<button class="btn btn-outline-success w-100 mb-2 text-start"
               onclick="SwitchLibrary._pickTarget(${titleId},${t.id})">
         <i class="bi bi-joystick me-2"></i>${esc(t.name)}
         <span class="text-secondary small ms-2">${esc(t.ip_address)}</span>
       </button>`
    ).join('');
    const html = `
      <div class="modal fade" id="switch-target-modal" tabindex="-1">
        <div class="modal-dialog modal-sm">
          <div class="modal-content bg-dark border-secondary">
            <div class="modal-header border-secondary py-2">
              <h6 class="modal-title mb-0"><i class="bi bi-joystick me-2 text-success"></i>Send to…</h6>
              <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">${opts}</div>
          </div>
        </div>
      </div>`;
    document.getElementById('app').insertAdjacentHTML('beforeend', html);
    const modal = new bootstrap.Modal(document.getElementById('switch-target-modal'));
    document.getElementById('switch-target-modal').addEventListener('hidden.bs.modal', e => e.target.remove());
    modal.show();
  },

  async _pickTarget(titleId, targetId) {
    const modal = bootstrap.Modal.getInstance(document.getElementById('switch-target-modal'));
    if (modal) modal.hide();
    const target = this._targets.find(t => t.id === targetId);
    if (target) await this._doInstall(titleId, target);
  },

  async _doInstall(titleId, target) {
    toast(`Sending to ${target.name}… make sure Awoo is open in Network Install mode.`, 'info');
    try {
      const r = await API.post('/switch/install', { title_id: titleId, target_id: target.id });
      toast(`Install started on ${target.name} — ${r.files_sent} file(s) queued.`, 'success');
    } catch (e) {
      toast(`Install failed: ${e.message}`, 'danger');
    }
  },

  async showDetail(titleId) {
    let t;
    try {
      const all = await API.get('/switch/titles');
      t = all.find(x => x.id === titleId);
    } catch (e) { return; }
    if (!t) return;

    const coverSrc = t.cover_url
      ? esc(t.cover_url)
      : t.cover_local ? `/api/switch/cover-image?id=${t.id}` : null;

    const genreTags = t.genres
      ? t.genres.split(',').map(x =>
          `<span class="badge bg-dark border border-secondary me-1" style="font-size:.7rem">${esc(x.trim())}</span>`
        ).join('')
      : '';

    const contents = t.contents || [];
    const contentRows = contents.map(c => {
      const lbl   = { base: 'Base Game', update: 'Update', dlc: 'DLC' }[c.content_type] || c.content_type;
      const fname = c.filename || (c.library_path || '').split('/').pop();
      return `
        <tr>
          <td><span class="badge bg-secondary">${esc(lbl)}</span></td>
          <td class="small text-truncate" style="max-width:220px" title="${esc(c.library_path||'')}">${esc(fname)}</td>
          <td class="text-secondary small text-nowrap">${c.version ? 'v'+esc(c.version) : '—'}</td>
          <td class="text-secondary small text-nowrap">${_humanSize(c.file_size)}</td>
        </tr>`;
    }).join('');

    const installBtn = this._targets.length
      ? `<button class="btn btn-success"
                 onclick="SwitchLibrary.install(${t.id});bootstrap.Modal.getInstance(document.getElementById('switch-detail-modal')).hide()">
           <i class="bi bi-send-fill me-1"></i>Install to Switch
         </button>`
      : '';

    const html = `
      <div class="modal fade" id="switch-detail-modal" tabindex="-1">
        <div class="modal-dialog modal-lg">
          <div class="modal-content bg-dark text-white border-secondary">
            <div class="modal-header border-secondary py-2">
              <h6 class="modal-title mb-0"><i class="bi bi-joystick me-2 text-success"></i>${esc(t.title)}</h6>
              <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
              <div class="d-flex gap-3 align-items-start mb-3">
                ${coverSrc
                  ? `<img src="${coverSrc}" alt="" class="flex-shrink-0 rounded"
                          style="width:110px;aspect-ratio:3/4;object-fit:cover">`
                  : `<div class="flex-shrink-0 rounded bg-secondary d-flex align-items-center justify-content-center"
                          style="width:110px;height:147px"><i class="bi bi-joystick text-dark fs-3"></i></div>`}
                <div class="flex-grow-1">
                  <div class="fw-semibold fs-6 mb-1">${esc(t.title)}</div>
                  ${t.publisher ? `<div class="text-secondary small">${esc(t.publisher)}</div>` : ''}
                  ${t.developer && t.developer !== t.publisher ? `<div class="text-secondary small">${esc(t.developer)}</div>` : ''}
                  <div class="d-flex flex-wrap gap-1 mt-2">
                    ${genreTags}
                    ${t.num_players ? `<span class="badge bg-dark border border-secondary" style="font-size:.7rem"><i class="bi bi-people-fill me-1"></i>${esc(t.num_players)}</span>` : ''}
                    ${t.release_date ? `<span class="text-secondary small">${esc(t.release_date.slice(0,4))}</span>` : ''}
                  </div>
                  ${t.description ? `<p class="text-secondary mt-2 mb-0" style="font-size:.82rem;line-height:1.5">${esc(t.description)}</p>` : ''}
                </div>
              </div>
              ${contents.length ? `
                <h6 class="text-secondary text-uppercase small mb-2" style="letter-spacing:.06em">Content in library</h6>
                <div class="table-responsive mb-3">
                  <table class="table table-dark table-sm align-middle mb-0">
                    <thead class="text-secondary"><tr><th>Type</th><th>File</th><th>Version</th><th>Size</th></tr></thead>
                    <tbody>${contentRows}</tbody>
                  </table>
                </div>` : ''}
              <div class="d-flex gap-2">${installBtn}</div>
            </div>
          </div>
        </div>
      </div>`;

    document.getElementById('app').insertAdjacentHTML('beforeend', html);
    const modal = new bootstrap.Modal(document.getElementById('switch-detail-modal'));
    document.getElementById('switch-detail-modal').addEventListener('hidden.bs.modal', e => e.target.remove());
    modal.show();
  },
};

const Router = {
  async route() {
    const hash = (location.hash || '#/').slice(1);
    const parts = hash.split('/').filter(Boolean);

    JobsPanel.close();

    // highlight active nav links
    document.getElementById('nav-search-link')?.classList.toggle('text-info', parts[0] === 'search');
    document.getElementById('nav-movies-link')?.classList.toggle('text-info', parts[0] === 'movies');
    document.getElementById('nav-browse-link')?.classList.toggle('text-info', parts[0] === 'browse');
    document.getElementById('nav-about-link')?.classList.toggle('text-info', parts[0] === 'about');
    document.getElementById('nav-switch-link')?.classList.toggle('text-success', parts[0] === 'switch');

    if (parts[0] === 'about') {
      await AboutPage.render();
    } else if (parts[0] === 'browse') {
      await BrowsePage.render();
    } else if (parts[0] === 'switch') {
      await SwitchLibrary.render();
    } else if (!parts.length || (parts[0] !== 'category' && parts[0] !== 'search' && parts[0] !== 'movies')) {
      await Views.home();
    } else if (parts[0] === 'movies') {
      await MovieDiscover.render(parts[1] || 'search');
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
  // Poll upgrade review badge every 60s
  MovieDiscover._pollReviewBadge();
  setInterval(() => MovieDiscover._pollReviewBadge(), 60_000);
  // Pre-fetch IPT status so grab() knows the configured sync tag
  try {
    const st = await API.get('/iptorrents/status');
    window._iptTag = (st.rtorrent?.configured) ? '' : '';
  } catch (_) {}
});
