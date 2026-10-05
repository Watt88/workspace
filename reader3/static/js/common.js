// Shared helpers for the library and reader pages.

const ICONS = {
  back: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  left: '<path d="m15 18-6-6 6-6"/>',
  right: '<path d="m9 18 6-6-6-6"/>',
  toc: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  search: '<circle cx="11" cy="11" r="7.5"/><path d="m20.5 20.5-4.2-4.2"/>',
  bookmark: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
  type: '<path d="M4 7V5h11v2M9.5 5v14M7 19h5"/><path d="M14 13v-1.5h7V13M17.5 11.5V19M16 19h3"/>',
  sparkles: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0l1.58 6.14a2 2 0 0 0 1.44 1.44l6.14 1.58a.5.5 0 0 1 0 .96l-6.14 1.58a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  highlight: '<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
  note: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
  expand: '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
  send: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  book: '<path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/>',
  more: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
  keyboard: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M8 13h.01M12 13h.01M16 13h.01M7 16h10"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  reset: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  plus: '<path d="M5 12h14M12 5v14"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  quote: '<path d="M3 21c3 0 7-1 7-8V5c0-1.25-.76-2-2-2H4c-1.25 0-2 .75-2 2v6c0 1.25.75 2 2 2h3c0 4-2 6-4 6zM15 21c3 0 7-1 7-8V5c0-1.25-.76-2-2-2h-4c-1.25 0-2 .75-2 2v6c0 1.25.75 2 2 2h3c0 4-2 6-4 6z"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
};

function icon(name, cls = '') {
  return `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}

// Replace <i data-icon="name"></i> placeholders in static markup.
function hydrateIcons(root = document) {
  root.querySelectorAll('i[data-icon]').forEach(el => {
    el.outerHTML = icon(el.dataset.icon, el.className || '');
  });
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.json !== undefined) {
    opts.body = JSON.stringify(opts.json);
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    delete opts.json;
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    // The session expired: go to the login page and come back afterwards
    location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash);
    throw new Error('Требуется вход');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const storage = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
    catch (_) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode */ }
  },
  remove(key) { try { localStorage.removeItem(key); } catch (_) { /* ignore */ } },
};

// ---------- Settings & theme ----------

const DEFAULT_SETTINGS = {
  theme: 'auto', font: 'serif', fontSize: 19, lineHeight: 1.7, width: 'medium', justify: false,
  mode: 'auto', keepAwake: false,
};
const WIDTHS = { narrow: '600px', medium: '700px', wide: '860px' };
const FONTS = { serif: 'var(--font-serif)', classic: 'var(--font-classic)', sans: 'var(--font-sans)' };

function loadSettings() {
  return { ...DEFAULT_SETTINGS, ...storage.get('reader3:settings', {}) };
}

function saveSettings(s) { storage.set('reader3:settings', s); }

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function resolvedTheme(theme) {
  return theme === 'auto' ? (darkQuery.matches ? 'dark' : 'light') : theme;
}

function applySettings(s) {
  const root = document.documentElement;
  root.dataset.theme = resolvedTheme(s.theme);
  root.style.setProperty('--read-font', FONTS[s.font] || FONTS.serif);
  root.style.setProperty('--read-size', s.fontSize + 'px');
  root.style.setProperty('--read-leading', s.lineHeight);
  root.style.setProperty('--read-width', WIDTHS[s.width] || WIDTHS.medium);
  root.style.setProperty('--read-align', s.justify ? 'justify' : 'left');
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = getComputedStyle(root).getPropertyValue('--bg').trim() || '#fbfaf7';
}

darkQuery.addEventListener('change', () => applySettings(loadSettings()));
applySettings(loadSettings());

// ---------- Toasts ----------

function toast(message, { type = '', timeout = 2600 } = {}) {
  let box = document.getElementById('toasts');
  if (!box) {
    box = document.createElement('div');
    box.id = 'toasts';
    box.setAttribute('role', 'status');
    document.body.appendChild(box);
  }
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => { el.classList.add('leaving'); setTimeout(() => el.remove(), 220); }, timeout);
}

// ---------- Dialog helpers ----------

function confirmDialog({ title, text, ok = 'OK', danger = false }) {
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'modal';
    d.innerHTML = `
      <div class="modal-head"><h2>${escapeHtml(title)}</h2></div>
      <div class="modal-body"><p style="margin:0;color:var(--text-2)">${escapeHtml(text)}</p></div>
      <div class="modal-foot">
        <button class="btn ghost" value="cancel">Отмена</button>
        <button class="btn ${danger ? 'danger' : 'primary'}" value="ok">${escapeHtml(ok)}</button>
      </div>`;
    document.body.appendChild(d);
    d.addEventListener('click', e => {
      if (e.target === d) d.close('cancel');
      if (e.target.closest('button[value]')) d.close(e.target.closest('button').value);
    });
    d.addEventListener('close', () => { resolve(d.returnValue === 'ok'); d.remove(); });
    d.showModal();
    d.querySelector('button[value="ok"]').focus();
  });
}

// Simple menu positioned near an anchor element.
function openMenu(menu, anchor, { align = 'right' } = {}) {
  closeMenus();
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = align === 'right' ? r.right - mw : r.left;
  left = Math.max(8, Math.min(left, window.innerWidth - mw - 8));
  let top = r.bottom + 6;
  if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
  menu._anchor = anchor;
  anchor.setAttribute('aria-expanded', 'true');
}

function closeMenus() {
  $$('.menu').forEach(m => {
    if (!m.hidden && !m.dataset.persistent) {
      m.hidden = true;
      m._anchor?.setAttribute('aria-expanded', 'false');
    }
  });
}

document.addEventListener('pointerdown', e => {
  if (!e.target.closest('.menu') && !e.target.closest('[aria-haspopup]')) closeMenus();
});

// ---------- Formatting ----------

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

const WPM = 220;

function readingTime(words) {
  const min = Math.max(1, Math.round(words / WPM));
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h} ч ${m} мин` : `${h} ч`;
}

function relativeTime(iso) {
  if (!iso) return '';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'только что';
  if (diff < 3600) { const n = Math.floor(diff / 60); return `${n} ${plural(n, 'минуту', 'минуты', 'минут')} назад`; }
  if (diff < 86400) { const n = Math.floor(diff / 3600); return `${n} ${plural(n, 'час', 'часа', 'часов')} назад`; }
  const n = Math.floor(diff / 86400);
  if (n === 1) return 'вчера';
  if (n < 30) return `${n} ${plural(n, 'день', 'дня', 'дней')} назад`;
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
}

// Deterministic pleasant gradient for books without a cover.
function coverGradient(seed) {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const a = h % 360, b = (a + 40 + (h >> 8) % 60) % 360;
  return `linear-gradient(150deg, hsl(${a} 45% 42%), hsl(${b} 50% 28%))`;
}

// ---------- Minimal, safe Markdown renderer (for AI answers) ----------

function renderMarkdown(src) {
  const blocks = [];
  // Fenced code blocks first, so their content is not touched by inline rules
  src = src.replace(/```[^\n]*\n([\s\S]*?)(```|$)/g, (_, code) => {
    blocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ''))}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });

  const inline = s => escapeHtml(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  const out = [];
  let list = null, para = [], quote = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inline).join('<br>')}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.type}>${list.items.map(i => `<li>${inline(i)}</li>`).join('')}</${list.type}>`); list = null; } };
  const flushQuote = () => { if (quote.length) { out.push(`<blockquote>${quote.map(inline).join('<br>')}</blockquote>`); quote = []; } };
  const flushAll = () => { flushPara(); flushList(); flushQuote(); };

  for (const raw of src.split('\n')) {
    const line = raw.trimEnd();
    let m;
    if (/^\u0000\d+\u0000$/.test(line.trim())) { flushAll(); out.push(blocks[+line.trim().slice(1, -1)]); }
    else if (!line.trim()) flushAll();
    else if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flushAll(); const lvl = Math.min(m[1].length + 2, 6); out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`); }
    else if ((m = line.match(/^\s*>\s?(.*)/))) { flushPara(); flushList(); quote.push(m[1]); }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flushPara(); flushQuote(); if (!list || list.type !== 'ul') { flushList(); list = { type: 'ul', items: [] }; } list.items.push(m[1]); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); flushQuote(); if (!list || list.type !== 'ol') { flushList(); list = { type: 'ol', items: [] }; } list.items.push(m[1]); }
    else if (/^\s*(---|\*\*\*)\s*$/.test(line)) { flushAll(); out.push('<hr>'); }
    else if (list && /^\s{2,}\S/.test(raw)) list.items[list.items.length - 1] += ' ' + line.trim();
    else { flushList(); flushQuote(); para.push(line); }
  }
  flushAll();
  return out.join('');
}
