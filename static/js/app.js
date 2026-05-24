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
  [/audiobook/i,  'bi-book-half',          'text-warning'],
  [/music/i,      'bi-music-note-beamed',   'text-danger'],
  [/movie/i,      'bi-film',               'text-info'],
  [/switch/i,     'bi-joystick',           'text-success'],
  [/pc.?game/i,   'bi-pc-display',         'text-primary'],
  [/game/i,       'bi-controller',         'text-success'],
  [/tv|show/i,    'bi-tv',                 'text-info'],
  [/comic/i,      'bi-book',              'text-warning'],
  [/ebook/i,      'bi-journal-text',       'text-warning'],
  [/software/i,   'bi-floppy',             'text-secondary'],
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
  mkv: 'bi-file-play text-success',
  mp4: 'bi-file-play text-success',
  avi: 'bi-file-play text-success',
  mov: 'bi-file-play text-success',
  mp3: 'bi-file-music text-danger',
  flac: 'bi-file-music text-danger',
  m4a: 'bi-file-music text-danger',
  m4b: 'bi-file-music text-warning',
  aac: 'bi-file-music text-danger',
  rar: 'bi-file-zip text-info',
  zip: 'bi-file-zip text-info',
  '7z': 'bi-file-zip text-info',
  nfo: 'bi-file-text text-secondary',
  jpg: 'bi-file-image text-pink',
  jpeg: 'bi-file-image text-pink',
  png: 'bi-file-image text-pink',
  xci: 'bi-sd-card text-success',
  nsp: 'bi-sd-card text-success',
  iso: 'bi-disc text-primary',
  exe: 'bi-file-binary text-warning',
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
    let cats;
    try {
      cats = await API.get('/categories');
    } catch (e) {
      this._setApp(`<div class="alert alert-danger mt-2">Failed to load categories: ${esc(e.message)}</div>`);
      return;
    }

    if (!cats.length) {
      this._setApp(`
        <div class="text-center py-5 empty-state">
          <i class="bi bi-folder-x"></i>
          <p class="mt-3">No categories found in the incoming directory.<br>
          <small class="text-secondary">Create subdirectories inside the mounted incoming path.</small></p>
        </div>`);
      return;
    }

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

    this._setApp(`
      <h6 class="text-secondary mb-3 text-uppercase" style="letter-spacing:.08em">Incoming Categories</h6>
      <div class="row g-3">${cards}</div>`);
  },

  async category(name) {
    this._loading();
    let items;
    try {
      items = await API.get(`/categories/${enc(name)}/items`);
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

    const rows = items.map(it => `
      <tr class="item-row" onclick="Router.go('/category/${enc(name)}/${enc(it.name)}')">
        <td><i class="bi bi-folder me-2 text-warning"></i>${esc(it.name)}</td>
        <td class="text-secondary text-nowrap">${it.size_human}</td>
        <td>
          ${it.has_rar
            ? '<span class="badge bg-info badge-rar"><i class="bi bi-archive me-1"></i>RAR</span>'
            : ''}
        </td>
      </tr>`).join('');

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
    let detail;
    try {
      detail = await API.get(`/categories/${enc(category)}/items/${enc(itemName)}`);
    } catch (e) {
      this._setApp(`<div class="alert alert-danger mt-2">Failed to load item: ${esc(e.message)}</div>`);
      return;
    }

    const crumb = breadcrumb([
      ['Home', '#/'],
      [esc(category), `#/category/${enc(category)}`],
      [esc(itemName)],
    ]);

    const actions = detail.has_rar ? `
      <div class="mb-3 d-flex gap-2 flex-wrap">
        <button class="btn btn-primary btn-sm"
                onclick="Actions.extract(${jsStr(category)}, ${jsStr(itemName)})">
          <i class="bi bi-archive me-1"></i>Extract RAR
        </button>
      </div>` : '';

    const rows = detail.files.map(f => `
      <tr>
        <td><i class="bi ${fileIcon(f.name, f.is_dir)} me-2"></i>${esc(f.name)}</td>
        <td class="text-secondary text-nowrap">${f.is_dir ? '—' : f.size_human}</td>
      </tr>`).join('');

    this._setApp(crumb + actions + `
      <div class="table-responsive">
        <table class="table table-hover table-dark file-table">
          <thead><tr class="text-secondary"><th>File</th><th>Size</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  },
};

// ─────────────────────────────────────────────
// Actions
// ─────────────────────────────────────────────
const Actions = {
  async extract(category, itemName) {
    try {
      const job = await API.post('/jobs/extract', { category, item_name: itemName });
      JobPoller.track(job.id);
      JobsPanel.open();
      toast(`Extraction started — Job #${job.id}`, 'success');
    } catch (e) {
      toast(`Could not start extraction: ${e.message}`, 'danger');
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

    const statusColor = { pending: 'warning', running: 'info', done: 'success', error: 'danger', cancelled: 'secondary' };

    list.innerHTML = jobs.map(j => {
      const color = statusColor[j.status] || 'secondary';
      const isActive = j.status === 'pending' || j.status === 'running';
      const canDelete = !isActive;

      return `
        <div class="job-item" id="job-item-${j.id}">
          <div class="d-flex justify-content-between align-items-start gap-2">
            <div class="flex-grow-1 min-width-0">
              <span class="badge bg-${color} me-1">${j.status}</span>
              <span class="small fw-semibold">${esc(j.item_name)}</span>
              <span class="text-secondary small ms-1">[${esc(j.type)}]</span>
            </div>
            ${canDelete ? `
              <button class="btn btn-sm btn-link text-secondary p-0 flex-shrink-0"
                      onclick="JobsPanel.remove(${j.id})" title="Dismiss">
                <i class="bi bi-x-lg"></i>
              </button>` : ''}
          </div>
          ${isActive ? `
            <div class="progress mt-2">
              <div class="progress-bar progress-bar-striped progress-bar-animated bg-${color}"
                   style="width:${j.progress}%" role="progressbar"
                   aria-valuenow="${j.progress}" aria-valuemin="0" aria-valuemax="100"></div>
            </div>
            <small class="text-secondary d-block mt-1">${j.progress}%${j.message ? ' — ' + esc(j.message) : ''}</small>
          ` : (j.message ? `<small class="text-secondary d-block mt-1">${esc(j.message)}</small>` : '')}
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
// Job poller — fast when there are tracked active jobs
// ─────────────────────────────────────────────
const JobPoller = {
  _fastTimer: null,
  _slowTimer: null,
  _tracked: new Set(),

  track(jobId) {
    this._tracked.add(jobId);
    this._startFast();
  },

  init() {
    // Background ambient poll every 8s
    this._slowTimer = setInterval(() => JobsPanel.refresh(), 8000);
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
      for (const id of [...this._tracked]) {
        if (!activeIds.has(id)) this._tracked.delete(id);
      }
    } catch (_) {}

    if (!this._tracked.size) {
      clearInterval(this._fastTimer);
      this._fastTimer = null;
      await JobsPanel.refresh();
    }
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
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function enc(str) { return encodeURIComponent(str); }

// Produce a JS string literal safe for inline onclick attributes
function jsStr(str) { return `'${String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`; }

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
  const t = new bootstrap.Toast(el, { delay: 4500 });
  t.show();
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
