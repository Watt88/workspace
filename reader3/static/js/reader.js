hydrateIcons();

const BOOK_ID = document.body.dataset.bookId;
const ENC_ID = encodeURIComponent(BOOK_ID);

const R = {
  book: null,
  state: null,
  config: { ai_enabled: false },
  settings: loadSettings(),
  chapter: -1,
  cache: new Map(),
  cumWords: [],
  totalWords: 1,
  loadToken: 0,
  selection: null,     // {start, end, text} of the current text selection
  activeHl: null,      // id of the highlight whose toolbar is open
  quote: null,         // text attached to the next AI question
  chat: storage.get(`reader3:chat:${BOOK_ID}`, []),
  stream: null,        // AbortController of the running AI request
};

const scroller = $('#scroller');
const content = $('#content');
const topbar = $('#topbar');
const toolbar = $('#sel-toolbar');
const docked = window.matchMedia('(min-width: 1101px)');
const touchUi = window.matchMedia('(hover: none) and (pointer: coarse)');
const phone = window.matchMedia('(max-width: 720px)');

// ---------- Small utilities ----------

const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
const isTyping = () => { const el = document.activeElement; return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable); };
const chapterInfo = i => R.book.spine[i];

async function copyText(text, okMessage = 'Скопировано') {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // Clipboard API needs a secure context; fall back for plain-http LAN access
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  toast(okMessage);
}

function coverBox(el) {
  el.innerHTML = R.book.cover
    ? `<img src="${escapeHtml(R.book.cover)}" alt="">`
    : `<div style="width:100%;height:100%;background:${coverGradient(R.book.title)}"></div>`;
}

// ---------- Text offsets & wrapping (highlights, search hits) ----------

function textNodes(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const out = [];
  let n;
  while ((n = walker.nextNode())) out.push(n);
  return out;
}

// Character offset of a DOM point inside the chapter, counted over its text content.
function offsetOf(container, offset) {
  const r = document.createRange();
  r.selectNodeContents(content);
  r.setEnd(container, offset);
  return r.toString().length;
}

// Wrap the text between two character offsets with elements produced by `make`.
function wrapOffsets(start, end, make) {
  const marks = [];
  let pos = 0;
  for (const node of textNodes(content)) {
    const len = node.data.length;
    const s = Math.max(start, pos), e = Math.min(end, pos + len);
    if (s < e && node.data.slice(s - pos, e - pos).trim()) {
      let target = node;
      if (s - pos > 0) target = target.splitText(s - pos);
      if (e - s < target.data.length) target.splitText(e - s);
      const m = make();
      target.parentNode.insertBefore(m, target);
      m.appendChild(target);
      marks.push(m);
    }
    pos += len;
    if (pos >= end) break;
  }
  return marks;
}

function unwrap(selector) {
  $$(selector, content).forEach(m => {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    m.remove();
    parent.normalize();
  });
}

// ---------- State persistence ----------

function patchState(patch, { keepalive = false } = {}) {
  return fetch(`/api/books/${ENC_ID}/state`, {
    method: 'PATCH', keepalive,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }).catch(() => { /* offline: will be retried on the next save */ });
}

// ---------- Reading geometry: vertical scroll or horizontal pages ----------

const columns = $('#columns');
const isPaged = () => document.body.classList.contains('paged');

function wantPaged() {
  const mode = R.settings.mode;
  return mode === 'paged' || (mode === 'auto' && touchUi.matches);
}

// Size of one "screen": a page width in paged mode, the viewport height otherwise
function viewSize() { return isPaged() ? scroller.clientWidth : scroller.clientHeight; }
function scrollPos() { return isPaged() ? scroller.scrollLeft : scroller.scrollTop; }

function pageCount() {
  if (!isPaged()) return 1;
  return Math.max(1, Math.ceil(scroller.scrollWidth / scroller.clientWidth - 0.02));
}

function currentPage() { return isPaged() ? Math.round(scroller.scrollLeft / scroller.clientWidth) : 0; }

function scrollMax() {
  return isPaged() ? (pageCount() - 1) * scroller.clientWidth : scroller.scrollHeight - scroller.clientHeight;
}

function setScrollPos(v, smooth = false) {
  if (isPaged()) {
    const page = Math.max(0, Math.min(pageCount() - 1, Math.round(v / scroller.clientWidth)));
    scroller.scrollTo({ left: page * scroller.clientWidth, top: 0, behavior: smooth ? 'smooth' : 'instant' });
  } else {
    scroller.scrollTo({ top: v, behavior: smooth ? 'smooth' : 'instant' });
  }
}

// Bring an element inside the chapter into view (page containing it, or centered)
function reveal(el, block = 'center') {
  if (!el) return;
  if (isPaged()) {
    const x = el.getClientRects()[0]?.left ?? el.getBoundingClientRect().left;
    const offset = x - scroller.getBoundingClientRect().left + scroller.scrollLeft;
    setScrollPos(Math.floor(offset / scroller.clientWidth) * scroller.clientWidth);
  } else {
    el.scrollIntoView({ block });
  }
}

// Lay the chapter out in columns, one column per screen-wide page
function layoutPages() {
  const paged = wantPaged();
  document.body.classList.toggle('paged', paged);
  if (!paged) {
    columns.style.cssText = '';
    return;
  }
  const W = scroller.clientWidth;
  const pad = W < 600 ? 22 : 48;
  const readWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--read-width')) || 700;
  const colW = Math.min(readWidth, W - 2 * pad);
  const gap = W - colW;
  Object.assign(columns.style, {
    width: colW + 'px', columnWidth: colW + 'px', columnGap: gap + 'px', marginLeft: gap / 2 + 'px',
  });
  columns.style.setProperty('--col-h', columns.clientHeight + 'px');
}

function relayout() {
  const ratio = chapterRatio();
  layoutPages();
  setScrollPos(ratio * scrollMax());
  onScrollUpdate();
}

window.addEventListener('resize', debounce(relayout, 150));
document.fonts?.ready.then(() => { if (R.chapter >= 0) relayout(); });
// Late-loading images change the number of pages: stay on the same page
content.addEventListener('load', debounce(() => { if (isPaged()) setScrollPos(scrollPos()); onScrollUpdate(); }, 100), true);

function chapterRatio() {
  const max = scrollMax();
  return max > 0 ? Math.min(1, Math.max(0, scrollPos() / max)) : 1;
}

function bookProgress() {
  if (R.chapter < 0) return 0;
  return Math.min(1, (R.cumWords[R.chapter] + chapterRatio() * chapterInfo(R.chapter).words) / R.totalWords);
}

function progressPayload() {
  return { chapter: R.chapter, position: +chapterRatio().toFixed(4), progress: +bookProgress().toFixed(4) };
}

const saveProgress = debounce(() => {
  const p = progressPayload();
  Object.assign(R.state, p);
  patchState(p);
}, 800);

const saveAnnotations = () => patchState({ bookmarks: R.state.bookmarks, highlights: R.state.highlights });

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden' && R.chapter >= 0) patchState(progressPayload(), { keepalive: true });
});

// ---------- Chapter loading & navigation ----------

async function loadChapter(i) {
  if (!R.cache.has(i)) {
    R.cache.set(i, api(`/api/books/${ENC_ID}/chapters/${i}`).catch(e => { R.cache.delete(i); throw e; }));
  }
  return R.cache.get(i);
}

async function goTo(i, { position = 0, anchor = null, toEnd = false, history: hist = 'push', after = null } = {}) {
  const n = R.book.spine.length;
  if (i < 0 || i >= n) return;
  hideToolbar();
  const token = ++R.loadToken;

  if (i !== R.chapter) {
    if (!R.cache.has(i)) content.innerHTML = '<div class="content-loading"><div class="spinner"></div></div>';
    let data;
    try {
      data = await loadChapter(i);
    } catch (e) {
      content.innerHTML = `<p style="color:var(--muted)">Не удалось загрузить главу: ${escapeHtml(e.message)}</p>`;
      return;
    }
    if (token !== R.loadToken) return;

    R.chapter = i;
    content.innerHTML = data.html;
    content.classList.remove('entering');
    void content.offsetWidth;
    content.classList.add('entering');
    $('#chapter-eyebrow').textContent = n > 1 ? `${i + 1} / ${n}` : '';
    applyHighlights();
    renderChapterEnd();
    updateTocCurrent();
    updateAiContext();
    document.title = `${chapterInfo(i).title} · ${R.book.title}`;
    $('#tb-chapter').textContent = chapterInfo(i).title;
    layoutPages();
    if (i + 1 < n) loadChapter(i + 1).catch(() => {});
  }

  const url = `#ch=${i}`;
  if (hist === 'push') history.pushState({ ch: i }, '', url);
  else if (hist === 'replace') history.replaceState({ ch: i }, '', url);

  const target = anchor && (content.querySelector(`[id="${CSS.escape(anchor)}"]`) || content.querySelector(`[name="${CSS.escape(anchor)}"]`));
  quietScrollUntil = Date.now() + 400;
  if (target) {
    reveal(target, 'start');
    target.classList.add('target-flash');
    setTimeout(() => target.classList.remove('target-flash'), 1700);
  } else if (toEnd) {
    setScrollPos(scrollMax());
  } else {
    setScrollPos(position * scrollMax());
  }
  if (!isTyping()) scroller.focus({ preventScroll: true });
  if (!isPaged()) showChrome();
  onScrollUpdate();
  saveProgress();
  if (after) after();
}

function nextChapter() { if (R.chapter < R.book.spine.length - 1) goTo(R.chapter + 1); }
function prevChapter() { if (R.chapter > 0) goTo(R.chapter - 1); }

const atChapterEnd = () => scrollPos() >= scrollMax() - 4;

function pageDown() {
  if (atChapterEnd()) { nextChapter(); return; }
  if (isPaged()) setScrollPos((currentPage() + 1) * scroller.clientWidth, true);
  else scroller.scrollBy({ top: scroller.clientHeight * 0.88, behavior: 'smooth' });
}

function pageUp() {
  if (scrollPos() <= 2) { if (R.chapter > 0) goTo(R.chapter - 1, { toEnd: true }); return; }
  if (isPaged()) setScrollPos((currentPage() - 1) * scroller.clientWidth, true);
  else scroller.scrollBy({ top: -scroller.clientHeight * 0.88, behavior: 'smooth' });
}

window.addEventListener('popstate', e => {
  const ch = e.state?.ch ?? parseHash();
  if (ch !== null && ch !== undefined) goTo(ch, { position: e.state?.pos ?? 0, history: 'none' });
});

function parseHash() {
  const m = location.hash.match(/ch=(\d+)/);
  return m ? +m[1] : null;
}

// Internal links (footnotes, cross references) and highlight clicks inside the chapter
content.addEventListener('click', e => {
  const link = e.target.closest('a[data-ch]');
  if (link) {
    e.preventDefault();
    // Remember where we were so the browser "back" returns to the same spot
    history.replaceState({ ch: R.chapter, pos: chapterRatio() }, '', `#ch=${R.chapter}`);
    const ch = +link.dataset.ch;
    if (ch === R.chapter) history.pushState({ ch }, '', `#ch=${ch}`);
    goTo(ch, { anchor: link.dataset.anchor || null, history: ch === R.chapter ? 'none' : 'push' });
    return;
  }
  const mark = e.target.closest('mark.hl');
  if (mark && window.getSelection().isCollapsed) {
    R.activeHl = mark.dataset.id;
    R.selection = null;
    const rects = $$(`mark.hl[data-id="${mark.dataset.id}"]`, content).map(m => m.getBoundingClientRect());
    showToolbar(unionRect(rects), R.state.highlights.find(h => h.id === R.activeHl));
  }
});

function renderChapterEnd() {
  const i = R.chapter, n = R.book.spine.length;
  const nav = $('#chapter-end');
  let html = '';
  if (i > 0) {
    html += `<button class="ch-nav prev" data-go="${i - 1}"><span class="lbl">${icon('left', 'sm')}Предыдущая</span>
      <span class="ttl">${escapeHtml(chapterInfo(i - 1).title)}</span></button>`;
  }
  if (i < n - 1) {
    html += `<button class="ch-nav next primary" data-go="${i + 1}"><span class="lbl">Следующая глава${icon('right', 'sm')}</span>
      <span class="ttl">${escapeHtml(chapterInfo(i + 1).title)}</span></button>`;
  } else {
    html += `<div class="book-finished"><h3>Конец книги</h3><p>«${escapeHtml(R.book.title)}» прочитана.</p>
      <a class="btn" href="/">${icon('grid', 'sm')}В библиотеку</a></div>`;
  }
  nav.innerHTML = html;
}

$('#chapter-end').addEventListener('click', e => {
  const btn = e.target.closest('[data-go]');
  if (btn) goTo(+btn.dataset.go);
});

// ---------- Scroll: progress, status bar, auto-hiding top bar ----------

let lastScrollPos = 0;
let quietScrollUntil = 0; // programmatic scrolls (restore, navigation) must not hide the top bar

function onScrollUpdate() {
  if (R.chapter < 0) return;
  const p = bookProgress();
  const ratio = chapterRatio();
  const wordsLeft = Math.round(chapterInfo(R.chapter).words * (1 - ratio));
  const pct = Math.round(p * 100);
  const endOfChapter = ratio > 0.98 || wordsLeft < 20;
  $('#book-progress > div').style.width = (p * 100).toFixed(2) + '%';
  if (isPaged()) {
    const pages = pageCount(), page = Math.min(currentPage() + 1, pages);
    $('#st-chapter').textContent = phone.matches ? `${page} / ${pages}` : `Страница ${page} из ${pages} · раздел ${R.chapter + 1} из ${R.book.spine.length}`;
  } else {
    $('#st-chapter').textContent = phone.matches ? `${R.chapter + 1} / ${R.book.spine.length}` : `Раздел ${R.chapter + 1} из ${R.book.spine.length}`;
  }
  $('#st-progress').textContent = phone.matches
    ? `${pct}% · ${endOfChapter ? 'конец главы' : readingTime(wordsLeft)}`
    : `${pct}% книги · ` + (endOfChapter ? 'конец главы' : `${readingTime(wordsLeft)} до конца главы`);
  if (!scrubbing) {
    $('#scrub').value = Math.round(p * 1000);
    $('#bb-info').textContent = pct + '%';
  }
  const left = Math.round((1 - p) * R.totalWords);
  $('#mini-progress').innerHTML = `<div class="progress-track"><div style="width:${(p * 100).toFixed(1)}%"></div></div>
    ${Math.round(p * 100)}% · осталось ~${readingTime(left)}`;
  updateBookmarkButton();
}

// "Chrome" = the top bar and, on phones, the bottom bar. Hidden while reading, like in e-reader apps.
function showChrome(instant = false) {
  const body = document.body;
  if (instant && body.classList.contains('chrome-hidden')) {
    // Menus are positioned from the bar buttons, so the bars must be in place right away
    topbar.style.transition = 'none';
    body.classList.remove('chrome-hidden');
    void topbar.offsetWidth;
    topbar.style.transition = '';
  }
  body.classList.remove('chrome-hidden');
}
const showTopbar = showChrome;

function hideChrome() {
  const busy = $$('.panel.open').length > 0 || !$('#settings').hidden || !$('#more-menu').hidden || scrubbing;
  if (!busy) document.body.classList.add('chrome-hidden');
}

function toggleChrome() {
  if (document.body.classList.contains('chrome-hidden')) showChrome();
  else hideChrome();
}

scroller.addEventListener('scroll', () => {
  const pos = scrollPos();
  const delta = pos - lastScrollPos;
  lastScrollPos = pos;
  if (Date.now() < quietScrollUntil) { /* programmatic: keep the bars as they are */ }
  else if (isPaged()) { if (Math.abs(delta) > 4) hideChrome(); }
  else if (delta > 6 && pos > 140) hideChrome();
  else if (delta < -6 && !touchUi.matches) showChrome();
  if (!toolbar.hidden) hideToolbar();
  onScrollUpdate();
  saveProgress();
}, { passive: true });

document.addEventListener('mousemove', e => { if (e.clientY < 70 && !touchUi.matches) showChrome(); }, { passive: true });

// ---------- Touch: tap zones, swipes, mouse wheel in paged mode ----------

let lastSwipeAt = 0;
let scrubbing = false;

// Tap on the left/right edge turns the page, tap in the middle shows or hides the controls
scroller.addEventListener('click', e => {
  if (e.target.closest('a, button, mark.hl, input, textarea, .chapter-end') || !window.getSelection().isCollapsed) return;
  if (Date.now() - lastSwipeAt < 400 || (!isPaged() && !touchUi.matches)) return;
  const rect = scroller.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  if (x < 0.25) { pageUp(); hideChrome(); }
  else if (x > 0.75) { pageDown(); hideChrome(); }
  else toggleChrome();
});

let touch = null;
scroller.addEventListener('touchstart', e => {
  if (!isPaged() || e.touches.length !== 1) { touch = null; return; }
  const t = e.touches[0];
  touch = { x: t.clientX, y: t.clientY, time: Date.now(), left: scroller.scrollLeft, dragging: false };
}, { passive: true });

scroller.addEventListener('touchmove', e => {
  if (!touch || e.touches.length !== 1 || !window.getSelection().isCollapsed) return;
  const t = e.touches[0];
  const dx = t.clientX - touch.x, dy = t.clientY - touch.y;
  if (!touch.dragging && Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) * 1.2) touch.dragging = true;
  if (touch.dragging) {
    // The page follows the finger; resistance past the chapter edges
    let left = touch.left - dx;
    const max = scrollMax();
    if (left < 0) left = left / 3;
    if (left > max) left = max + (left - max) / 3;
    scroller.scrollLeft = Math.max(0, Math.min(left, max));
    if (!toolbar.hidden) hideToolbar();
  }
}, { passive: true });

scroller.addEventListener('touchend', e => {
  if (!touch) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - touch.x;
  const fast = Math.abs(dx) / Math.max(1, Date.now() - touch.time) > 0.35;
  const swiped = touch.dragging && (Math.abs(dx) > scroller.clientWidth * 0.18 || (fast && Math.abs(dx) > 30));
  const startPage = Math.round(touch.left / scroller.clientWidth);
  if (touch.dragging) lastSwipeAt = Date.now();
  touch = null;
  if (!swiped) { if (Math.abs(dx) > 2) setScrollPos(startPage * scroller.clientWidth, true); return; }
  hideChrome();
  if (dx < 0) {
    if (startPage >= pageCount() - 1) nextChapter();
    else setScrollPos((startPage + 1) * scroller.clientWidth, true);
  } else {
    if (startPage <= 0) { if (R.chapter > 0) goTo(R.chapter - 1, { toEnd: true }); else setScrollPos(0, true); }
    else setScrollPos((startPage - 1) * scroller.clientWidth, true);
  }
});

let wheelAcc = 0, wheelLock = 0;
scroller.addEventListener('wheel', e => {
  if (!isPaged() || e.ctrlKey) return;
  e.preventDefault();
  if (Date.now() < wheelLock) return;
  wheelAcc += Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
  if (Math.abs(wheelAcc) > 40) {
    wheelAcc > 0 ? pageDown() : pageUp();
    wheelAcc = 0;
    wheelLock = Date.now() + 350;
  }
}, { passive: false });

// ---------- Panels ----------

function syncPanels() {
  const sidebarOpen = $('#sidebar').classList.contains('open');
  const aiOpen = $('#ai-panel').classList.contains('open');
  $('#backdrop').hidden = docked.matches || !(sidebarOpen || aiOpen);
  document.body.classList.toggle('sidebar-docked', docked.matches && sidebarOpen);
  document.body.classList.toggle('ai-docked', docked.matches && aiOpen);
  $('#toc-btn').classList.toggle('active', sidebarOpen);
  $('#ai-btn').classList.toggle('active', aiOpen);
  if (docked.matches) storage.set('reader3:panels', { sidebar: sidebarOpen, ai: aiOpen });
}

function setPanel(id, open) {
  const ratio = chapterRatio();
  if (open && !docked.matches) $$('.panel.open').forEach(p => p.id !== id && p.classList.remove('open'));
  $('#' + id).classList.toggle('open', open);
  syncPanels();
  showChrome();
  $$('.bb-btn[data-bb="toc"], .bb-btn[data-bb="search"]').forEach(b => b.classList.toggle('active', $('#sidebar').classList.contains('open') && $('.tab[aria-selected="true"]')?.dataset.tab === b.dataset.bb));
  $('.bb-btn[data-bb="ai"]').classList.toggle('active', $('#ai-panel').classList.contains('open'));
  // Docked panels change the text width: keep the reading position
  if (docked.matches) requestAnimationFrame(() => setTimeout(() => {
    layoutPages();
    setScrollPos(ratio * scrollMax());
  }, 320));
}

function togglePanel(id) { setPanel(id, !$('#' + id).classList.contains('open')); }

function openSidebar(tab) {
  const open = $('#sidebar').classList.contains('open');
  const current = $('.tab[aria-selected="true"]')?.dataset.tab;
  if (open && current === tab) { setPanel('sidebar', false); return; }
  selectTab(tab);
  setPanel('sidebar', true);
  if (tab === 'search') setTimeout(() => $('#search-input').focus(), 50);
  if (tab === 'toc') setTimeout(() => $('.toc-link.current')?.scrollIntoView({ block: 'center' }), 50);
}

function selectTab(tab) {
  $$('.tab').forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === tab)));
  $$('.tab-panel').forEach(p => { p.hidden = p.dataset.panel !== tab; });
  storage.set('reader3:tab', tab);
}

$$('.tab').forEach(t => t.addEventListener('click', () => {
  selectTab(t.dataset.tab);
  if (t.dataset.tab === 'search') $('#search-input').focus();
}));
$$('[data-close]').forEach(b => b.addEventListener('click', () => setPanel(b.dataset.close, false)));
$('#backdrop').addEventListener('click', () => { $$('.panel.open').forEach(p => p.classList.remove('open')); syncPanels(); });
docked.addEventListener('change', syncPanels);

$('#toc-btn').addEventListener('click', () => openSidebar(storage.get('reader3:tab', 'toc') === 'toc' ? 'toc' : storage.get('reader3:tab', 'toc')));
$('#search-btn').addEventListener('click', () => openSidebar('search'));
$('#ai-btn').addEventListener('click', () => { togglePanel('ai-panel'); if ($('#ai-panel').classList.contains('open')) focusComposer(); });

// Close an overlay panel after navigating from it on small screens
function afterPanelNavigation() { if (!docked.matches) setPanel('sidebar', false); }

// ---------- Table of contents ----------

function renderToc() {
  const firstEntry = new Set();
  const walk = items => `<ul>${items.map(it => {
    const first = it.chapter !== null && !firstEntry.has(it.chapter) && !it.anchor;
    if (first) firstEntry.add(it.chapter);
    const time = first ? `<span class="toc-time">${readingTime(chapterInfo(it.chapter).words)}</span>` : '';
    return `<li><button class="toc-link" data-ch="${it.chapter ?? ''}" data-anchor="${escapeHtml(it.anchor || '')}"
        ${it.chapter === null ? 'disabled' : ''}>${escapeHtml(it.title)}${time}</button>${it.children.length ? walk(it.children) : ''}</li>`;
  }).join('')}</ul>`;
  $('#toc').innerHTML = walk(R.book.toc);
}

function updateTocCurrent() {
  let marked = false;
  $$('.toc-link').forEach(l => {
    const ch = l.dataset.ch === '' ? null : +l.dataset.ch;
    const current = ch === R.chapter && !marked;
    if (current) marked = true;
    l.classList.toggle('current', current);
    l.classList.toggle('read', ch !== null && ch < R.chapter);
  });
  $('.toc-link.current')?.scrollIntoView({ block: 'nearest' });
}

$('#toc').addEventListener('click', e => {
  const l = e.target.closest('.toc-link');
  if (!l || l.disabled) return;
  goTo(+l.dataset.ch, { anchor: l.dataset.anchor || null });
  afterPanelNavigation();
});

// ---------- Search ----------

let searchQuery = '';

const runSearch = debounce(async q => {
  searchQuery = q;
  const box = $('#search-results'), summary = $('#search-summary');
  if (q.trim().length < 2) { box.innerHTML = ''; summary.textContent = ''; unwrap('mark.search-hit'); return; }
  summary.textContent = 'Ищу…';
  try {
    const res = await api(`/api/books/${ENC_ID}/search?q=${encodeURIComponent(q)}`);
    if (q !== searchQuery) return;
    if (!res.total) { summary.textContent = 'Ничего не найдено'; box.innerHTML = ''; return; }
    const chapters = new Set(res.results.map(r => r.chapter)).size;
    summary.textContent = `${res.total} ${plural(res.total, 'совпадение', 'совпадения', 'совпадений')} в ${chapters} ${plural(chapters, 'главе', 'главах', 'главах')}` +
      (res.results.length < res.total ? ` (показаны первые ${res.results.length})` : '');
    let html = '', last = null;
    for (const r of res.results) {
      if (r.chapter !== last) {
        if (last !== null) html += '</div>';
        html += `<div class="result-group"><div class="result-chapter">${escapeHtml(r.chapter_title)}</div>`;
        last = r.chapter;
      }
      html += `<button class="result" data-ch="${r.chapter}" data-occ="${r.occurrence}">${escapeHtml(r.before)}<mark>${escapeHtml(r.match)}</mark>${escapeHtml(r.after)}</button>`;
    }
    box.innerHTML = html + '</div>';
  } catch (e) {
    summary.textContent = 'Ошибка поиска: ' + e.message;
  }
}, 250);

$('#search-input').addEventListener('input', e => runSearch(e.target.value));

$('#search-results').addEventListener('click', e => {
  const r = e.target.closest('.result');
  if (!r) return;
  $$('.result').forEach(x => x.style.background = '');
  r.style.background = 'var(--surface-2)';
  const occ = +r.dataset.occ;
  goTo(+r.dataset.ch, { after: () => highlightSearch(searchQuery, occ) });
  afterPanelNavigation();
});

function highlightSearch(q, occurrence) {
  unwrap('mark.search-hit');
  const pattern = new RegExp(q.trim().split(/\s+/).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+'), 'giu');
  const text = content.textContent;
  const matches = [...text.matchAll(pattern)];
  // Wrap from the end so earlier offsets stay valid
  let current = null;
  matches.reverse().forEach((m, idx) => {
    const marks = wrapOffsets(m.index, m.index + m[0].length, () => {
      const el = document.createElement('mark');
      el.className = 'search-hit';
      return el;
    });
    if (matches.length - 1 - idx === occurrence) { marks.forEach(x => x.classList.add('current')); current = marks[0]; }
  });
  reveal(current || $('mark.search-hit', content));
}

// ---------- Highlights & notes ----------

function applyHighlights() {
  const text = content.textContent;
  const list = R.state.highlights.filter(h => h.chapter === R.chapter);
  for (const h of list) {
    // Relocate by text if the book was re-imported and offsets moved
    if (text.slice(h.start, h.end) !== h.text) {
      const idx = text.indexOf(h.text);
      if (idx < 0) continue;
      h.start = idx;
      h.end = idx + h.text.length;
    }
    wrapOffsets(h.start, h.end, () => {
      const m = document.createElement('mark');
      m.className = 'hl' + (h.note ? ' has-note' : '');
      m.dataset.id = h.id;
      m.dataset.color = h.color;
      if (h.note) m.title = h.note;
      return m;
    });
  }
}

function refreshHighlights() {
  unwrap('mark.hl');
  applyHighlights();
  renderNotes();
}

function createHighlight(color, note = '') {
  if (!R.selection) return null;
  const h = { id: uid(), chapter: R.chapter, start: R.selection.start, end: R.selection.end,
              text: R.selection.text, color, note, created: new Date().toISOString() };
  R.state.highlights.push(h);
  window.getSelection().removeAllRanges();
  refreshHighlights();
  saveAnnotations();
  return h;
}

function editNote(h, isNew) {
  const d = $('#note-dialog');
  $('#note-quote').textContent = h.text;
  $('#note-text').value = h.note || '';
  d.returnValue = '';
  d.showModal();
  $('#note-text').focus();
  d.onclose = () => {
    if (d.returnValue === 'save') {
      h.note = $('#note-text').value.trim();
      refreshHighlights();
      saveAnnotations();
      toast(h.note ? 'Заметка сохранена' : 'Выделение сохранено');
    } else if (isNew) {
      R.state.highlights = R.state.highlights.filter(x => x.id !== h.id);
      refreshHighlights();
      saveAnnotations();
    }
  };
}

function deleteHighlight(id) {
  R.state.highlights = R.state.highlights.filter(h => h.id !== id);
  refreshHighlights();
  saveAnnotations();
}

// ---------- Selection toolbar ----------

function unionRect(rects) {
  const r = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
  rects.forEach(x => { r.left = Math.min(r.left, x.left); r.top = Math.min(r.top, x.top); r.right = Math.max(r.right, x.right); r.bottom = Math.max(r.bottom, x.bottom); });
  return r;
}

function showToolbar(rect, highlight) {
  $('[data-act="delete"]', toolbar).hidden = !highlight;
  $$('.swatch', toolbar).forEach(s => s.classList.toggle('active', !!highlight && s.dataset.color === highlight.color));
  $('[data-act="note"] span', toolbar).textContent = highlight?.note ? 'Изменить' : 'Заметка';
  toolbar.hidden = false;
  const w = toolbar.offsetWidth, h = toolbar.offsetHeight;
  const minTop = Math.max(8, topbar.getBoundingClientRect().bottom + 6);
  let top = touchUi.matches ? rect.bottom + 14 : rect.top - h - 10;
  if (top < minTop) top = rect.bottom + 10;
  if (top + h > window.innerHeight - 8) top = rect.top - h - 14;
  top = Math.max(minTop, top);
  const left = Math.max(8, Math.min((rect.left + rect.right) / 2 - w / 2, window.innerWidth - w - 8));
  toolbar.style.left = left + 'px';
  toolbar.style.top = Math.min(top, window.innerHeight - h - 8) + 'px';
}

function hideToolbar() {
  toolbar.hidden = true;
  R.activeHl = null;
}

function readSelection() {
  const sel = window.getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  if (!content.contains(range.commonAncestorContainer)) return null;
  const raw = range.toString();
  const text = raw.trim();
  if (!text) return null;
  const start = offsetOf(range.startContainer, range.startOffset) + (raw.length - raw.trimStart().length);
  return { start, end: start + text.length, text, rect: range.getBoundingClientRect() };
}

function onSelectionDone() {
  const s = readSelection();
  if (!s) { if (!R.activeHl) hideToolbar(); return; }
  R.activeHl = null;
  R.selection = s;
  showToolbar(s.rect, null);
}

content.addEventListener('pointerup', () => setTimeout(onSelectionDone, 10));
document.addEventListener('selectionchange', debounce(() => {
  // Touch devices and keyboard selection do not fire pointerup on the content
  const s = readSelection();
  if (s) { R.selection = s; R.activeHl = null; showToolbar(s.rect, null); }
  else if (!R.activeHl && !toolbar.matches(':hover')) hideToolbar();
}, 300));

toolbar.addEventListener('pointerdown', e => e.preventDefault()); // keep the text selection
toolbar.addEventListener('click', e => {
  const sw = e.target.closest('.swatch');
  const act = e.target.closest('[data-act]')?.dataset.act;
  const hl = R.activeHl && R.state.highlights.find(h => h.id === R.activeHl);

  if (sw) {
    if (hl) { hl.color = sw.dataset.color; refreshHighlights(); saveAnnotations(); }
    else createHighlight(sw.dataset.color);
  } else if (act === 'note') {
    if (hl) editNote(hl, false);
    else { const h = createHighlight('yellow'); if (h) editNote(h, true); }
  } else if (act === 'copy') {
    copyText(hl ? hl.text : R.selection?.text || '');
    window.getSelection().removeAllRanges();
  } else if (act === 'ask') {
    setQuote(hl ? hl.text : R.selection?.text);
    window.getSelection().removeAllRanges();
    setPanel('ai-panel', true);
    focusComposer();
  } else if (act === 'delete' && hl) {
    deleteHighlight(hl.id);
  } else return;
  hideToolbar();
});

// ---------- Bookmarks ----------

function visibleSnippet() {
  const view = scroller.getBoundingClientRect();
  const top = isPaged() ? view.top : topbar.getBoundingClientRect().bottom + 8;
  const onScreen = r => r.bottom > top && r.top < view.bottom && r.right > view.left && r.left < view.right;
  for (const el of content.querySelectorAll('p, h1, h2, h3, h4, li, blockquote, div')) {
    if (el.children.length && el.tagName === 'DIV') continue;
    if ([...el.getClientRects()].some(onScreen) && el.textContent.trim()) return el.textContent.trim().replace(/\s+/g, ' ').slice(0, 160);
  }
  return chapterInfo(R.chapter).title;
}

function bookmarkHere() {
  const max = scrollMax();
  const tolerance = max > 0 ? (viewSize() * 0.5) / max : 1;
  const ratio = chapterRatio();
  return R.state.bookmarks.find(b => b.chapter === R.chapter && Math.abs(b.position - ratio) <= tolerance);
}

function updateBookmarkButton() {
  const on = !!bookmarkHere();
  const btn = $('#bookmark-btn');
  btn.setAttribute('aria-pressed', String(on));
  btn.innerHTML = icon('bookmark', on ? 'filled' : '');
  const bb = $('#bb-bookmark');
  bb.classList.toggle('active', on);
  $('svg', bb).classList.toggle('filled', on);
}

function toggleBookmark() {
  const existing = bookmarkHere();
  if (existing) {
    R.state.bookmarks = R.state.bookmarks.filter(b => b !== existing);
    toast('Закладка удалена');
  } else {
    R.state.bookmarks.push({ id: uid(), chapter: R.chapter, position: +chapterRatio().toFixed(4),
                             snippet: visibleSnippet(), created: new Date().toISOString() });
    toast('Закладка добавлена');
  }
  saveAnnotations();
  updateBookmarkButton();
  renderNotes();
}

$('#bookmark-btn').addEventListener('click', toggleBookmark);

// ---------- Notes panel ----------

function renderNotes() {
  const bms = [...R.state.bookmarks].sort((a, b) => a.chapter - b.chapter || a.position - b.position);
  const hls = [...R.state.highlights].sort((a, b) => a.chapter - b.chapter || a.start - b.start);
  const count = bms.length + hls.length;
  $('#notes-count').hidden = !count;
  $('#notes-count').textContent = count;

  if (!count) {
    $('#notes-list').innerHTML = `<div class="notes-empty">${icon('highlight')}<br>
      Выделите текст, чтобы сохранить цитату или заметку.<br>Нажмите <kbd>B</kbd>, чтобы поставить закладку.</div>`;
    return;
  }
  const title = i => escapeHtml(chapterInfo(i)?.title || '');
  let html = `<div class="notes-actions"><button class="btn small" id="export-notes">${icon('copy', 'sm')}Экспорт в Markdown</button></div>`;
  if (bms.length) {
    html += '<div class="notes-section">Закладки</div>' + bms.map(b => `
      <div class="note-item" role="button" tabindex="0" data-bm="${b.id}" style="--hl:var(--accent)">
        <div class="n-quote">${escapeHtml(b.snippet)}</div>
        <div class="n-meta">${title(b.chapter)} · ${relativeTime(b.created)}</div>
        <button class="icon-btn n-del" data-del-bm="${b.id}" aria-label="Удалить закладку">${icon('trash', 'sm')}</button>
      </div>`).join('');
  }
  if (hls.length) {
    html += '<div class="notes-section">Выделения и заметки</div>' + hls.map(h => `
      <div class="note-item" role="button" tabindex="0" data-hl="${h.id}" style="--hl:var(--hl-${h.color})">
        <div class="n-quote">${escapeHtml(h.text)}</div>
        ${h.note ? `<div class="n-note">${escapeHtml(h.note)}</div>` : ''}
        <div class="n-meta">${title(h.chapter)} · ${relativeTime(h.created)}</div>
        <button class="icon-btn n-del" data-del-hl="${h.id}" aria-label="Удалить">${icon('trash', 'sm')}</button>
      </div>`).join('');
  }
  $('#notes-list').innerHTML = html;
}

$('#notes-list').addEventListener('click', e => {
  if (e.target.closest('#export-notes')) { exportNotes(); return; }
  const delBm = e.target.closest('[data-del-bm]')?.dataset.delBm;
  const delHl = e.target.closest('[data-del-hl]')?.dataset.delHl;
  if (delBm) { R.state.bookmarks = R.state.bookmarks.filter(b => b.id !== delBm); saveAnnotations(); renderNotes(); updateBookmarkButton(); return; }
  if (delHl) { deleteHighlight(delHl); return; }

  const bm = R.state.bookmarks.find(b => b.id === e.target.closest('[data-bm]')?.dataset.bm);
  if (bm) { goTo(bm.chapter, { position: bm.position }); afterPanelNavigation(); return; }
  const hl = R.state.highlights.find(h => h.id === e.target.closest('[data-hl]')?.dataset.hl);
  if (hl) {
    goTo(hl.chapter, { after: () => {
      const m = $(`mark.hl[data-id="${hl.id}"]`, content);
      if (m) { reveal(m); m.classList.add('target-flash'); setTimeout(() => m.classList.remove('target-flash'), 1700); }
    } });
    afterPanelNavigation();
  }
});

function exportNotes() {
  const b = R.book;
  let md = `# ${b.title}\n\n${b.authors.length ? `*${b.authors.join(', ')}*\n\n` : ''}`;
  const hls = [...R.state.highlights].sort((x, y) => x.chapter - y.chapter || x.start - y.start);
  let last = null;
  for (const h of hls) {
    if (h.chapter !== last) { md += `## ${chapterInfo(h.chapter)?.title || ''}\n\n`; last = h.chapter; }
    md += `> ${h.text.replace(/\n+/g, '\n> ')}\n\n${h.note ? h.note + '\n\n' : ''}`;
  }
  const blob = new Blob([md], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${b.title} — заметки.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- Settings ----------

function syncSettingsUI() {
  const s = R.settings;
  $$('[data-theme-opt]').forEach(b => b.classList.toggle('active', b.dataset.themeOpt === s.theme));
  $$('[data-font]').forEach(b => b.classList.toggle('active', b.dataset.font === s.font));
  $$('[data-width]').forEach(b => b.classList.toggle('active', b.dataset.width === s.width));
  $$('[data-justify]').forEach(b => b.classList.toggle('active', b.dataset.justify === String(s.justify)));
  $$('[data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === s.mode));
  $('#font-size-val').textContent = s.fontSize + ' px';
  $('#leading').value = s.lineHeight;
  $('#keep-awake').checked = !!s.keepAwake;
}

function updateSettings(patch) {
  const ratio = chapterRatio();
  R.settings = { ...R.settings, ...patch };
  saveSettings(R.settings);
  applySettings(R.settings);
  syncSettingsUI();
  // Keep the same place in the text after reflow
  layoutPages();
  setScrollPos(ratio * scrollMax());
  onScrollUpdate();
  if ('keepAwake' in patch) updateWakeLock();
}

$('#settings').addEventListener('click', e => {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.dataset.themeOpt) updateSettings({ theme: t.dataset.themeOpt });
  if (t.dataset.font) updateSettings({ font: t.dataset.font });
  if (t.dataset.width) updateSettings({ width: t.dataset.width });
  if (t.dataset.justify) updateSettings({ justify: t.dataset.justify === 'true' });
  if (t.dataset.mode) updateSettings({ mode: t.dataset.mode });
  if (t.dataset.step) changeFontSize(+t.dataset.delta);
});
$('#leading').addEventListener('input', e => updateSettings({ lineHeight: +e.target.value }));
$('#keep-awake').addEventListener('change', e => updateSettings({ keepAwake: e.target.checked }));
if (!('wakeLock' in navigator)) $('#awake-row').hidden = true;

// Keep the screen on while reading (Screen Wake Lock API)
let wakeLock = null;
async function updateWakeLock() {
  const want = R.settings.keepAwake && document.visibilityState === 'visible' && 'wakeLock' in navigator;
  if (want && !wakeLock) {
    try { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); }
    catch (_) { wakeLock = null; }
  } else if (!want && wakeLock) {
    wakeLock.release().catch(() => {});
    wakeLock = null;
  }
}
document.addEventListener('visibilitychange', updateWakeLock);

function changeFontSize(delta) {
  updateSettings({ fontSize: Math.max(14, Math.min(30, R.settings.fontSize + delta)) });
}

function openSettings(anchor) {
  const menu = $('#settings');
  if (!menu.hidden) { closeMenus(); return; }
  syncSettingsUI();
  showChrome(true);
  openMenu(menu, anchor);
}
$('#settings-btn').addEventListener('click', e => openSettings(e.currentTarget));

// ---------- More menu, dialogs ----------

$('#more-btn').addEventListener('click', e => {
  const menu = $('#more-menu');
  if (!menu.hidden) { closeMenus(); return; }
  showTopbar(true);
  openMenu(menu, e.currentTarget);
});

$('#more-menu').addEventListener('click', e => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (!act) return;
  closeMenus();
  if (act === 'info') showInfo();
  if (act === 'copy-chapter') copyText(chapterForLLM(), 'Глава скопирована — вставьте её в чат с LLM');
  if (act === 'fullscreen') toggleFullscreen();
  if (act === 'shortcuts') $('#keys-dialog').showModal();
  if (act === 'search') openSidebar('search');
  if (act === 'bookmark') toggleBookmark();
});

$$('[data-dialog-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
$$('dialog.modal').forEach(d => d.addEventListener('click', e => { if (e.target === d) d.close(); }));

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => toast('Полноэкранный режим недоступен'));
}

function showInfo() {
  const b = R.book;
  const rows = [
    b.authors.length && `Автор: ${b.authors.join(', ')}`,
    b.publisher && `Издатель: ${b.publisher}`,
    b.date && `Дата: ${String(b.date).slice(0, 10)}`,
    b.language && `Язык: ${b.language}`,
    `${b.spine.length} ${plural(b.spine.length, 'глава', 'главы', 'глав')} · ~${readingTime(R.totalWords)} чтения`,
  ].filter(Boolean).map(escapeHtml).join('<br>');
  $('#info-body').innerHTML = `
    <div class="info-head"><div class="mini-cover" id="info-cover"></div>
      <div><h3>${escapeHtml(b.title)}</h3><div class="meta">${rows}</div></div></div>
    ${b.subjects?.length ? `<p class="meta" style="color:var(--muted);font-size:13px">${escapeHtml(b.subjects.join(' · '))}</p>` : ''}
    ${b.description ? `<p class="info-desc">${escapeHtml(b.description)}</p>` : ''}`;
  coverBox($('#info-cover'));
  $('#info-dialog').showModal();
}

function chapterForLLM() {
  const b = R.book, i = R.chapter;
  const authors = b.authors.length ? ` (${b.authors.join(', ')})` : '';
  return `Я читаю книгу «${b.title}»${authors}. Ниже — текст главы «${chapterInfo(i).title}» ` +
    `(${i + 1} из ${b.spine.length}). Помоги мне понять и обсудить её: объясняй сложные места, давай контекст ` +
    `и не раскрывай сюжет дальше этой главы.\n\n<chapter>\n${content.innerText.trim()}\n</chapter>`;
}

// ---------- AI companion ----------

const SUGGESTIONS = [
  'Кратко перескажи эту главу',
  'Объясни сложные места и непонятные слова',
  'Какой исторический и культурный контекст у этой главы?',
  'Какие ключевые идеи и темы здесь поднимаются?',
  'Задай мне три вопроса, чтобы проверить понимание',
];

function saveChat() { storage.set(`reader3:chat:${BOOK_ID}`, R.chat.slice(-100)); }

function focusComposer() { setTimeout(() => $('#ai-input')?.focus(), 60); }

function setQuote(text) {
  R.quote = text ? text.trim() : null;
  const box = $('#ai-quote');
  box.hidden = !R.quote;
  if (R.quote) {
    box.innerHTML = `<div class="q-text">${escapeHtml(R.quote)}</div>
      <button class="icon-btn" aria-label="Убрать цитату">${icon('x', 'sm')}</button>`;
    $('button', box).onclick = () => setQuote(null);
  }
}

function updateAiContext() {
  if (R.chapter < 0) return;
  $('#ai-context').innerHTML = `${icon('book')}<span>Контекст: «${escapeHtml(chapterInfo(R.chapter).title)}»</span>`;
}

function messageHtml(m) {
  if (m.error) return `<div class="msg error">${escapeHtml(m.content)}</div>`;
  if (m.role === 'user') {
    return `<div class="msg user">${m.quote ? `<blockquote>${escapeHtml(m.quote)}</blockquote>` : ''}${escapeHtml(m.content)}</div>`;
  }
  return `<div class="msg assistant">${m.content ? renderMarkdown(m.content) : '<div class="typing"><span></span><span></span><span></span></div>'}
    ${m.content && !m.pending ? `<div class="msg-actions"><button class="icon-btn" data-copy-msg title="Копировать">${icon('copy', 'sm')}</button></div>` : ''}</div>`;
}

function renderAi() {
  const body = $('#ai-body');
  if (!R.config.ai_enabled) {
    $('#ai-composer').hidden = true;
    $('#ai-clear').hidden = true;
    body.innerHTML = `
      <div class="ai-setup">
        <div class="ai-empty" style="margin:0 0 12px"><div class="big-icon">${icon('sparkles')}</div></div>
        <h3>Встроенный ИИ не настроен</h3>
        <p>Чтобы обсуждать книгу прямо здесь, запустите сервер с ключом Claude API:</p>
        <pre>export ANTHROPIC_API_KEY=sk-ant-...
uv run server.py</pre>
        <p>А пока можно скопировать текущую главу с готовым промптом и вставить её в любой чат:</p>
        <button class="btn primary" id="copy-for-llm">${icon('copy', 'sm')}Копировать главу для LLM</button>
        <div class="links">
          <a class="btn" href="https://claude.ai/new" target="_blank" rel="noopener">Claude ${icon('external', 'sm')}</a>
          <a class="btn" href="https://chatgpt.com/" target="_blank" rel="noopener">ChatGPT ${icon('external', 'sm')}</a>
        </div>
      </div>`;
    $('#copy-for-llm').onclick = () => copyText(chapterForLLM(), 'Глава скопирована — вставьте её в чат');
    return;
  }
  $('#ai-clear').hidden = !R.chat.length;
  if (!R.chat.length) {
    body.innerHTML = `
      <div class="ai-empty">
        <div class="big-icon">${icon('sparkles')}</div>
        <h3>Читайте вместе с ИИ</h3>
        <p>Задавайте вопросы о главе, просите объяснить сложные места или выделите фрагмент текста и нажмите «Спросить».</p>
        <div class="suggestions">${SUGGESTIONS.map(s => `<button class="suggestion">${escapeHtml(s)}</button>`).join('')}</div>
      </div>`;
    return;
  }
  let html = '', lastCh = null;
  R.chat.forEach((m, i) => {
    if (m.role === 'user' && m.chapter !== lastCh) {
      html += `<div class="ch-divider">${escapeHtml(chapterInfo(m.chapter)?.title || '')}</div>`;
      lastCh = m.chapter;
    }
    html += `<div data-i="${i}">${messageHtml(m)}</div>`;
  });
  body.innerHTML = html;
  body.scrollTop = body.scrollHeight;
}

$('#ai-body').addEventListener('click', e => {
  const s = e.target.closest('.suggestion');
  if (s) { sendMessage(s.textContent); return; }
  const c = e.target.closest('[data-copy-msg]');
  if (c) copyText(R.chat[+c.closest('[data-i]').dataset.i].content);
});

function apiMessages() {
  const msgs = [];
  for (const m of R.chat.slice(-40)) {
    if (m.error || !m.content || m.pending) continue;
    const text = m.quote ? `> ${m.quote.replace(/\n+/g, '\n> ')}\n\n${m.content}` : m.content;
    const last = msgs[msgs.length - 1];
    if (last && last.role === m.role) last.content += '\n\n' + text;
    else msgs.push({ role: m.role, content: text });
  }
  while (msgs.length && msgs[0].role !== 'user') msgs.shift();
  return msgs;
}

function setStreaming(on) {
  const btn = $('#ai-send');
  btn.innerHTML = icon(on ? 'stop' : 'send');
  btn.setAttribute('aria-label', on ? 'Остановить' : 'Отправить');
}

async function sendMessage(text) {
  text = text.trim();
  if (!text || R.stream || R.chapter < 0) return;
  R.chat.push({ role: 'user', content: text, quote: R.quote, chapter: R.chapter });
  setQuote(null);
  $('#ai-input').value = '';
  autosize();
  const reply = { role: 'assistant', content: '', chapter: R.chapter, pending: true };
  R.chat.push(reply);
  renderAi();

  const body = $('#ai-body');
  const node = () => body.querySelector(`[data-i="${R.chat.indexOf(reply)}"]`);
  let frame = null;
  const paint = () => {
    frame = null;
    const el = node();
    if (!el) return;
    const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    el.innerHTML = messageHtml(reply);
    if (nearBottom) body.scrollTop = body.scrollHeight;
  };

  R.stream = new AbortController();
  setStreaming(true);
  try {
    const res = await fetch(`/api/books/${ENC_ID}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chapter: R.chapter, messages: apiMessages() }),
      signal: R.stream.signal,
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const ev = /^event: (.*)$/m.exec(chunk)?.[1];
        const data = JSON.parse(/^data: (.*)$/m.exec(chunk)?.[1] || '{}');
        if (ev === 'delta') {
          reply.content += data.text;
          if (!frame) frame = requestAnimationFrame(paint);
        } else if (ev === 'error') {
          R.chat.push({ role: 'assistant', error: true, content: data.message, chapter: R.chapter });
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') R.chat.push({ role: 'assistant', error: true, content: 'Ошибка: ' + e.message, chapter: R.chapter });
  } finally {
    R.stream = null;
    reply.pending = false;
    if (!reply.content) R.chat.splice(R.chat.indexOf(reply), 1);
    setStreaming(false);
    saveChat();
    renderAi();
  }
}

function autosize() {
  const ta = $('#ai-input');
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
}

$('#ai-input').addEventListener('input', autosize);
$('#ai-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(e.target.value); }
});
$('#ai-send').addEventListener('click', () => {
  if (R.stream) R.stream.abort();
  else sendMessage($('#ai-input').value);
});
$('#ai-clear').addEventListener('click', async () => {
  if (R.stream) R.stream.abort();
  if (R.chat.length && !(await confirmDialog({ title: 'Начать новый разговор?', text: 'Текущая переписка о книге будет удалена.', ok: 'Очистить' }))) return;
  R.chat = [];
  saveChat();
  renderAi();
});

// ---------- Keyboard ----------

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (!toolbar.hidden) { hideToolbar(); window.getSelection().removeAllRanges(); return; }
    if ($$('.menu').some(m => !m.hidden)) { closeMenus(); return; }
    if (isTyping()) { document.activeElement.blur(); return; }
    const open = $$('.panel.open');
    if (open.length) { open.forEach(p => p.classList.remove('open')); syncPanels(); }
    return;
  }
  if (isTyping() || e.ctrlKey || e.metaKey || e.altKey || $('dialog[open]')) return;

  const actions = {
    ArrowRight: pageDown, PageDown: pageDown, ArrowLeft: pageUp, PageUp: pageUp,
    ']': nextChapter, '[': prevChapter,
    t: () => openSidebar('toc'), n: () => openSidebar('notes'), '/': () => openSidebar('search'),
    b: toggleBookmark, a: () => $('#ai-btn').click(), s: () => $('#settings-btn').click(),
    f: toggleFullscreen, '?': () => $('#keys-dialog').showModal(),
    m: () => updateSettings({ mode: isPaged() ? 'scroll' : 'paged' }),
    '+': () => changeFontSize(1), '=': () => changeFontSize(1), '-': () => changeFontSize(-1),
    d: () => updateSettings({ theme: ['dark', 'black'].includes(resolvedTheme(R.settings.theme)) ? 'light' : 'dark' }),
  };
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  // Space scrolls natively; at the very end of a chapter (or in paged mode) we handle it
  if (e.key === ' ' && (isPaged() || (!e.shiftKey && atChapterEnd()))) {
    e.preventDefault(); e.shiftKey ? pageUp() : pageDown(); return;
  }
  if (isPaged() && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { e.preventDefault(); e.key === 'ArrowDown' ? pageDown() : pageUp(); return; }
  const fn = actions[key] || actions[{ 'е': 't', 'т': 'n', 'и': 'b', 'ф': 'a', 'ы': 's', 'а': 'f', 'в': 'd', 'ь': 'm' }[key]];
  if (fn) { e.preventDefault(); fn(); }
});

// ---------- Bottom bar (phones) ----------

$('#bottombar').addEventListener('click', e => {
  const b = e.target.closest('[data-bb]');
  if (!b) return;
  const act = b.dataset.bb;
  if (act === 'toc') openSidebar('toc');
  if (act === 'search') openSidebar('search');
  if (act === 'settings') openSettings(b);
  if (act === 'bookmark') toggleBookmark();
  if (act === 'ai') { togglePanel('ai-panel'); if ($('#ai-panel').classList.contains('open')) focusComposer(); }
});

// Book scrubber: drag to preview a place in the book, release to jump there
function progressToPlace(p) {
  const target = p * R.totalWords;
  let ch = R.cumWords.findIndex((w, i) => target < w + R.book.spine[i].words);
  if (ch < 0) ch = R.book.spine.length - 1;
  const words = R.book.spine[ch].words || 1;
  return { chapter: ch, position: Math.min(1, Math.max(0, (target - R.cumWords[ch]) / words)) };
}

const scrub = $('#scrub');
scrub.addEventListener('input', () => {
  scrubbing = true;
  const p = scrub.value / 1000;
  const place = progressToPlace(p);
  const label = $('#scrub-label');
  label.hidden = false;
  label.textContent = `${chapterInfo(place.chapter).title} · ${Math.round(p * 100)}%`;
  $('#bb-info').textContent = Math.round(p * 100) + '%';
});
scrub.addEventListener('change', () => {
  const place = progressToPlace(scrub.value / 1000);
  $('#scrub-label').hidden = true;
  scrubbing = false;
  goTo(place.chapter, { position: place.position });
});

// ---------- Mobile viewport: on-screen keyboard, swipe-to-close panels ----------

// Panels follow the visual viewport so the AI composer stays above the keyboard
if (window.visualViewport) {
  const syncViewport = () => document.documentElement.style.setProperty('--vvh', window.visualViewport.height + 'px');
  window.visualViewport.addEventListener('resize', syncViewport);
  syncViewport();
}

$$('.panel').forEach(panel => {
  const dir = panel.classList.contains('panel-left') ? -1 : 1; // direction that closes the panel
  let start = null;
  panel.addEventListener('touchstart', e => {
    if (docked.matches || e.touches.length !== 1 || e.target.closest('textarea, input')) { start = null; return; }
    start = { x: e.touches[0].clientX, y: e.touches[0].clientY, dragging: false };
  }, { passive: true });
  panel.addEventListener('touchmove', e => {
    if (!start) return;
    const dx = e.touches[0].clientX - start.x, dy = e.touches[0].clientY - start.y;
    if (!start.dragging && Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy) * 1.5 && Math.sign(dx) === dir) start.dragging = true;
    if (start.dragging) {
      panel.classList.add('dragging');
      panel.style.transform = `translateX(${dir * Math.max(0, dx * dir)}px)`;
    }
  }, { passive: true });
  panel.addEventListener('touchend', e => {
    if (!start) return;
    const dx = e.changedTouches[0].clientX - start.x;
    const wasDragging = start.dragging;
    start = null;
    panel.classList.remove('dragging');
    panel.style.transform = '';
    if (wasDragging && dx * dir > Math.min(100, panel.offsetWidth * 0.25)) setPanel(panel.id, false);
  });
});

// ---------- Init ----------

async function init() {
  let book, state, config;
  try {
    [book, state, config] = await Promise.all([
      api(`/api/books/${ENC_ID}`),
      api(`/api/books/${ENC_ID}/state`),
      api('/api/config').catch(() => ({ ai_enabled: false })),
    ]);
  } catch (e) {
    content.innerHTML = `<p>Не удалось открыть книгу: ${escapeHtml(e.message)}</p>`;
    return;
  }
  R.book = book;
  R.state = state;
  R.config = config;
  let acc = 0;
  R.cumWords = book.spine.map(c => { const v = acc; acc += c.words; return v; });
  R.totalWords = Math.max(1, acc);

  coverBox($('#mini-cover'));
  renderToc();
  renderNotes();
  renderAi();
  syncSettingsUI();
  selectTab(storage.get('reader3:tab', 'toc'));

  if (docked.matches) {
    const panels = storage.get('reader3:panels', { sidebar: false, ai: false });
    $('#sidebar').classList.toggle('open', panels.sidebar);
    $('#ai-panel').classList.toggle('open', panels.ai);
  }
  syncPanels();

  const fromHash = parseHash();
  if (fromHash !== null && fromHash < book.spine.length) {
    await goTo(fromHash, { history: 'replace', position: fromHash === state.chapter ? state.position : 0 });
  } else {
    await goTo(Math.min(state.chapter || 0, book.spine.length - 1), { position: state.position || 0, history: 'replace' });
  }
  // Images load lazily and change the chapter height: re-apply the saved position once they are in
  const restore = R.chapter === state.chapter ? state.position : null;
  if (restore) setTimeout(() => { if (R.chapter === state.chapter && Math.abs(chapterRatio() - restore) > 0.01 && scrollPos() < 5) setScrollPos(restore * scrollMax()); }, 400);
  updateWakeLock();
}

init();
