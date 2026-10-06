'use strict';
// PDF viewer: pages exactly as printed + text of the page for the AI companion, search and copying.
hydrateIcons();

const BOOK_ID = document.body.dataset.bookId;
const ENC_ID = encodeURIComponent(BOOK_ID);
const API = `/api/pdf/${ENC_ID}`;

const P = {
  n: 0,
  page: 0,                 // 0-based; in spread mode the left page of the spread
  info: null,
  config: { ai_enabled: false },
  settings: loadSettings(),
  fit: storage.get('reader3:pdf:fit', innerWidth <= 720 ? 'width' : 'page'),   // 'page' | 'width' (phones: width, text stays readable)
  spread: storage.get('reader3:pdf:spread', null),     // null = automatic (wide landscape screens)
  zoom: 1,
  win: +storage.get('reader3:pdf:win', 2),
  vision: storage.get('reader3:pdf:vision', true),   // send pictures of the pages on screen to the model
  chat: storage.get(`reader3:pdfchat:${BOOK_ID}`, []),
  stream: null,
};

const ZOOMS = [1, 1.25, 1.5, 2, 3];
const stage = $('#stage');
const box = $('#pages');
const docked = window.matchMedia('(min-width: 1101px)');

const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const isTyping = () => { const el = document.activeElement; return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable); };
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

async function copyText(text, okMessage = 'Скопировано') {
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
  }
  toast(okMessage);
}

// ---------- Pages ----------

const spreadOn = () => P.spread === null ? (innerWidth > innerHeight && innerWidth >= 900) : P.spread;
const spreadStart = p => p === 0 ? 0 : (p % 2 === 1 ? p : p - 1);   // the cover stands alone, then pairs 1-2, 3-4 ...

function visiblePages() {
  if (!spreadOn()) return [P.page];
  const s = spreadStart(P.page);
  return s === 0 ? [0] : [s, s + 1].filter(i => i < P.n);
}

function pageWidth(count) {
  const [pw, ph] = P.info.size;
  const cs = getComputedStyle(stage);
  const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  const availW = stage.clientWidth - padX - 6 * (count - 1);
  const availH = stage.clientHeight - padY;
  const perW = availW / count;
  const w = P.fit === 'width' ? perW : Math.min(perW, availH * pw / ph);
  return Math.max(160, Math.floor(w * P.zoom));
}

function makeImg(i, width) {
  const img = new Image();
  img.alt = `Страница ${i + 1}`;
  img.className = 'loading';
  img.style.width = width + 'px';
  img.draggable = false;
  img.onload = () => img.classList.remove('loading');
  img.src = `${API}/page/${i}.jpg?w=${Math.ceil(width * (window.devicePixelRatio || 1))}`;
  return img;
}

function render() {
  const vis = visiblePages();
  const width = pageWidth(vis.length);
  box.replaceChildren(...vis.map(i => makeImg(i, width)));
  stage.scrollTo(0, 0);
  updateUi(vis);
  prefetch(vis, width);
}

function prefetch(vis, width) {
  const last = vis[vis.length - 1], first = vis[0];
  const ahead = [last + 1, last + 2, first - 1].filter(i => i >= 0 && i < P.n);
  const w = Math.ceil(width * (window.devicePixelRatio || 1));
  ahead.forEach(i => { const im = new Image(); im.src = `${API}/page/${i}.jpg?w=${w}`; });
}

function updateUi(vis = visiblePages()) {
  const label = vis.length > 1 ? `${vis[0] + 1}–${vis[1] + 1}` : `${vis[0] + 1}`;
  $('#tb-page').textContent = `стр. ${label} из ${P.n}`;
  $('#page-input').value = vis[0] + 1;
  $('#scrub').value = vis[0] + 1;
  $('#page-total').textContent = `/ ${P.n}`;
  $('#book-progress > div').style.width = (P.n > 1 ? vis[0] / (P.n - 1) * 100 : 100) + '%';
  $('#fit-btn').classList.toggle('active', P.fit === 'width');
  $('#spread-btn').classList.toggle('active', spreadOn());
  $$('.thumb').forEach(t => t.classList.toggle('current', vis.includes(+t.dataset.p)));
  $$('.toc-link').forEach(l => l.classList.toggle('current', +l.dataset.p === currentTocPage(vis[0])));
  updateAiContext();
  if ($('#sidebar').classList.contains('open') && currentTab() === 'text') loadText();
  history.replaceState(null, '', `#p=${vis[0] + 1}`);
  saveState(vis[0], vis[vis.length - 1]);
}

let pendingSave = null;
function flushSave() {
  if (!pendingSave) return;
  const body = JSON.stringify(pendingSave);
  pendingSave = null;
  fetch(`/api/books/${ENC_ID}/state`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
}
const scheduleSave = debounce(flushSave, 600);
function saveState(page, last) {
  // the last spread counts as finished even when it starts before the last page
  pendingSave = { chapter: page, position: 0, progress: last >= P.n - 1 ? 1 : (P.n > 1 ? page / (P.n - 1) : 1) };
  scheduleSave();
}
addEventListener('pagehide', flushSave);
document.addEventListener('visibilitychange', () => { if (document.hidden) flushSave(); });

function go(page) {
  page = clamp(page, 0, P.n - 1);
  P.page = spreadOn() ? spreadStart(page) : page;
  render();
}

function step(dir) {
  if (spreadOn()) {
    const s = spreadStart(P.page);
    go(dir > 0 ? (s === 0 ? 1 : s + 2) : (s <= 1 ? 0 : s - 2));
  } else go(P.page + dir);
}

$('#prev').onclick = () => step(-1);
$('#next').onclick = () => step(1);
const scrubGo = debounce(p => go(p), 140);
$('#scrub').addEventListener('input', e => { $('#page-input').value = e.target.value; scrubGo(+e.target.value - 1); });
$('#page-input').addEventListener('change', e => go((+e.target.value || 1) - 1));
$('#page-input').addEventListener('focus', e => e.target.select());

function setZoom(z) { P.zoom = z; render(); }
$('#zoom-in').onclick = () => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(P.zoom) + 1)] || 1);
$('#zoom-out').onclick = () => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(P.zoom) - 1)] || 1);
$('#fit-btn').onclick = () => { P.fit = P.fit === 'page' ? 'width' : 'page'; P.zoom = 1; storage.set('reader3:pdf:fit', P.fit); render(); };
$('#spread-btn').onclick = () => { P.spread = !spreadOn(); P.zoom = 1; storage.set('reader3:pdf:spread', P.spread); go(P.page); };

// taps on the page edges turn pages, a tap in the middle hides the bars; swipes turn pages
let touch = null;
stage.addEventListener('touchstart', e => {
  touch = e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() } : null;
}, { passive: true });
stage.addEventListener('touchend', e => {
  if (!touch || P.zoom !== 1 || !e.changedTouches.length) { touch = null; return; }
  const dx = e.changedTouches[0].clientX - touch.x, dy = e.changedTouches[0].clientY - touch.y;
  if (Math.abs(dx) > 60 && Math.abs(dy) < 45 && Date.now() - touch.t < 700) { step(dx < 0 ? 1 : -1); e.preventDefault(); }
  touch = null;
});
stage.addEventListener('click', e => {
  const sel = window.getSelection();
  if (sel && !sel.isCollapsed) return;
  const r = stage.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width;
  if (P.zoom === 1 && x < 0.2) step(-1);
  else if (P.zoom === 1 && x > 0.8) step(1);
  else document.body.classList.toggle('chrome-hidden');
});

addEventListener('resize', debounce(() => { if (P.info) { P.page = spreadOn() ? spreadStart(P.page) : P.page; render(); } }, 250));

// ---------- Panels ----------

function syncPanels() {
  const sidebarOpen = $('#sidebar').classList.contains('open');
  const aiOpen = $('#ai-panel').classList.contains('open');
  $('#backdrop').hidden = docked.matches || !(sidebarOpen || aiOpen);
  document.body.classList.toggle('sidebar-docked', docked.matches && sidebarOpen);
  document.body.classList.toggle('ai-docked', docked.matches && aiOpen);
  $('#toc-btn').classList.toggle('active', sidebarOpen);
  $('#ai-btn').classList.toggle('active', aiOpen);
  if (docked.matches) storage.set('reader3:pdf:panels', { sidebar: sidebarOpen, ai: aiOpen });
}

function setPanel(id, open) {
  if (open && !docked.matches) $$('.panel.open').forEach(p => p.id !== id && p.classList.remove('open'));
  $('#' + id).classList.toggle('open', open);
  document.body.classList.remove('chrome-hidden');
  syncPanels();
  if (docked.matches) setTimeout(() => P.info && render(), 330);
}
const togglePanel = id => setPanel(id, !$('#' + id).classList.contains('open'));
const currentTab = () => $('.tab[aria-selected="true"]')?.dataset.tab;

function selectTab(tab) {
  $$('.tab').forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === tab)));
  $$('.tab-panel').forEach(p => { p.hidden = p.dataset.panel !== tab; });
  storage.set('reader3:pdf:tab', tab);
  if (tab === 'text') loadText();
  if (tab === 'pages') $('.thumb.current')?.scrollIntoView({ block: 'center' });
}

function openSidebar(tab) {
  const open = $('#sidebar').classList.contains('open');
  if (open && currentTab() === tab) { setPanel('sidebar', false); return; }
  selectTab(tab);
  setPanel('sidebar', true);
  if (tab === 'search') setTimeout(() => $('#search-input').focus(), 60);
}

$$('.tab').forEach(t => t.addEventListener('click', () => { selectTab(t.dataset.tab); if (t.dataset.tab === 'search') $('#search-input').focus(); }));
$$('[data-close]').forEach(b => b.addEventListener('click', () => setPanel(b.dataset.close, false)));
$('#backdrop').addEventListener('click', () => { $$('.panel.open').forEach(p => p.classList.remove('open')); syncPanels(); });
docked.addEventListener('change', syncPanels);
$('#toc-btn').onclick = () => openSidebar(currentTab() === 'pages' ? 'pages' : 'toc');
$('#search-btn').onclick = () => openSidebar('search');
$('#text-btn').onclick = () => openSidebar('text');
$('#ai-btn').onclick = () => { togglePanel('ai-panel'); if ($('#ai-panel').classList.contains('open')) focusComposer(); };

function afterPanelNavigation() { if (!docked.matches) { $$('.panel.open').forEach(p => p.classList.remove('open')); syncPanels(); } }

// ---------- Outline, thumbnails, search, text ----------

function currentTocPage(page) {
  let cur = -1;
  for (const [, , p] of P.info.outline) if (p <= page) cur = Math.max(cur, p);
  return cur;
}

function renderToc() {
  const out = P.info.outline;
  if (!out.length) {
    $('#toc').innerHTML = '<div class="toc-empty">В этом PDF нет оглавления. Откройте вкладку «Страницы»: там миниатюры всех страниц.</div>';
    return;
  }
  $('#toc').innerHTML = '<ul>' + out.map(([lvl, title, p]) =>
    `<li style="padding-left:${(lvl - 1) * 14}px"><button class="toc-link" data-p="${p}">${escapeHtml(title)}<span class="toc-time">${p + 1}</span></button></li>`).join('') + '</ul>';
}
$('#toc').addEventListener('click', e => {
  const l = e.target.closest('.toc-link');
  if (!l) return;
  go(+l.dataset.p);
  afterPanelNavigation();
});

function renderThumbs() {
  let html = '';
  for (let i = 0; i < P.n; i++) {
    html += `<button class="thumb" data-p="${i}"><img loading="lazy" decoding="async" alt="" src="${API}/page/${i}.jpg?w=200"><span>${i + 1}</span></button>`;
  }
  $('#thumbs').innerHTML = html;
}
$('#thumbs').addEventListener('click', e => {
  const t = e.target.closest('.thumb');
  if (!t) return;
  go(+t.dataset.p);
  afterPanelNavigation();
});

// highlight the query inside a plain-text snippet: escape the pieces, never the markup
function markHtml(text, q) {
  const re = new RegExp(q.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'ig');
  let out = '', last = 0, m;
  while ((m = re.exec(text))) {
    if (!m[0]) break;
    out += escapeHtml(text.slice(last, m.index)) + '<mark>' + escapeHtml(m[0]) + '</mark>';
    last = m.index + m[0].length;
  }
  return out + escapeHtml(text.slice(last));
}

let searchQuery = '';
const runSearch = debounce(async q => {
  searchQuery = q;
  const out = $('#search-results'), summary = $('#search-summary');
  if (q.trim().length < 2) { out.innerHTML = ''; summary.textContent = ''; return; }
  summary.textContent = 'Ищу…';
  try {
    const res = await api(`${API}/search?q=${encodeURIComponent(q)}`);
    if (q !== searchQuery) return;
    summary.textContent = res.length ? `${res.length} ${plural(res.length, 'страница', 'страницы', 'страниц')} с совпадениями` : 'Ничего не найдено';
    out.innerHTML = res.map(r => `<button class="result" data-p="${r.page}"><b>Стр. ${r.page + 1}.</b> ${markHtml(r.snippet, q)}</button>`).join('');
  } catch (e) { summary.textContent = 'Ошибка поиска: ' + e.message; }
}, 250);
$('#search-input').addEventListener('input', e => runSearch(e.target.value));
$('#search-results').addEventListener('click', e => {
  const r = e.target.closest('.result');
  if (!r) return;
  go(+r.dataset.p);
  afterPanelNavigation();
});

const textCache = new Map();
async function pageText(i) {
  if (!textCache.has(i)) textCache.set(i, api(`${API}/text/${i}`).then(r => r.text).catch(e => { textCache.delete(i); throw e; }));
  return textCache.get(i);
}

async function loadText() {
  const vis = visiblePages();
  const target = $('#page-text'), status = $('#text-status');
  target.textContent = 'Читаю текст…';
  try {
    const texts = await Promise.all(vis.map(pageText));
    if (vis.join() !== visiblePages().join()) return;
    target.innerHTML = vis.map((p, k) => `<span class="pt-num">Страница ${p + 1}</span>${escapeHtml(texts[k] || '(на этой странице нет текста)')}`).join('');
    target.dataset.copy = vis.map((p, k) => texts[k]).join('\n\n');
    const src = P.info.text_source === 'ocr' ? 'распознано OCR' : 'текст из PDF';
    status.textContent = P.info.ocr_pending ? `${src} · ещё читается страниц: ${P.info.ocr_pending}` : src;
  } catch (e) { target.textContent = 'Не удалось получить текст: ' + e.message; }
}
$('#text-copy').onclick = () => copyText($('#page-text').dataset.copy || $('#page-text').textContent, 'Текст страницы скопирован');

// While pages are being read by OCR in the background, keep the counter fresh
async function pollInfo() {
  try {
    P.info = { ...P.info, ...(await api(`${API}/info`)) };
    textCache.clear();
    if (P.info.ocr_pending > 0 && P.info.ocr_available) setTimeout(pollInfo, 10000);
    updateAiContext();
  } catch (_) {}
}

// ---------- AI companion ----------

const SUGGESTIONS = [
  'Кратко: о чём эти страницы?',
  'Объясни простыми словами, что здесь важно',
  'Составь короткий конспект',
  'Задай мне три вопроса, чтобы проверить понимание',
];

const saveChat = () => storage.set(`reader3:pdfchat:${BOOK_ID}`, P.chat.slice(-100));
const focusComposer = () => setTimeout(() => $('#ai-input')?.focus(), 60);

function updateAiContext() {
  const vis = visiblePages();
  const lo = Math.max(0, vis[0] - P.win) + 1, hi = Math.min(P.n, vis[vis.length - 1] + P.win + 1);
  let s = `стр. ${lo === hi ? lo : lo + '–' + hi}`;
  if (P.info?.ocr_pending > 0) s += ` · OCR: ${P.info.ocr_pending}`;
  $('#ai-context').textContent = s;
}

function messageHtml(m) {
  if (m.error) return `<div class="msg error">${escapeHtml(m.content)}</div>`;
  if (m.role === 'user') return `<div class="msg user">${escapeHtml(m.content)}</div>`;
  return `<div class="msg assistant">${m.content ? renderMarkdown(m.content) : '<div class="typing"><span></span><span></span><span></span></div>'}
    ${m.content && !m.pending ? `<div class="msg-actions"><button class="icon-btn" data-copy-msg title="Копировать">${icon('copy', 'sm')}</button></div>` : ''}</div>`;
}

function renderAi() {
  const body = $('#ai-body');
  if (!P.config.ai_enabled) {
    $('#ai-composer').hidden = true;
    $('#ai-clear').hidden = true;
    body.innerHTML = `<div class="ai-setup"><h3>Встроенный ИИ не настроен</h3>
      <p>Запустите сервер с <code>READER3_BACKEND=claude-code</code> (подписка Claude Code) или с ключом <code>ANTHROPIC_API_KEY</code>.</p>
      <p>А пока можно скопировать текст страницы на вкладке «Текст» и вставить его в любой чат.</p></div>`;
    return;
  }
  $('#ai-clear').hidden = !P.chat.length;
  if (!P.chat.length) {
    body.innerHTML = `<div class="ai-empty"><div class="big-icon">${icon('sparkles')}</div>
      <h3>Читайте вместе с ИИ</h3>
      <p>ИИ получает текст страницы и соседних страниц. Если включено «Показывать ИИ страницу как картинку», он видит ещё и саму страницу: рисунки, скриншоты, схемы.</p>
      <div class="suggestions">${SUGGESTIONS.map(s => `<button class="suggestion">${escapeHtml(s)}</button>`).join('')}</div></div>`;
    return;
  }
  let html = '', last = null;
  P.chat.forEach((m, i) => {
    if (m.role === 'user' && m.page !== last) { html += `<div class="ch-divider">Страница ${m.page + 1}</div>`; last = m.page; }
    html += `<div data-i="${i}">${messageHtml(m)}</div>`;
  });
  body.innerHTML = html;
  body.scrollTop = body.scrollHeight;
}

$('#ai-body').addEventListener('click', e => {
  const s = e.target.closest('.suggestion');
  if (s) { sendMessage(s.textContent); return; }
  const c = e.target.closest('[data-copy-msg]');
  if (c) copyText(P.chat[+c.closest('[data-i]').dataset.i].content);
});

function apiMessages() {
  const msgs = [];
  for (const m of P.chat.slice(-40)) {
    if (m.error || !m.content || m.pending) continue;
    const last = msgs[msgs.length - 1];
    if (last && last.role === m.role) last.content += '\n\n' + m.content;
    else msgs.push({ role: m.role, content: m.content });
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
  if (!text || P.stream) return;
  const page = visiblePages()[0];
  P.chat.push({ role: 'user', content: text, page });
  $('#ai-input').value = '';
  autosize();
  const reply = { role: 'assistant', content: '', page, pending: true };
  P.chat.push(reply);
  renderAi();

  const body = $('#ai-body');
  const node = () => body.querySelector(`[data-i="${P.chat.indexOf(reply)}"]`);
  let frame = null;
  const paint = () => {
    frame = null;
    const el = node();
    if (!el) return;
    const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    el.innerHTML = messageHtml(reply);
    if (nearBottom) body.scrollTop = body.scrollHeight;
  };

  P.stream = new AbortController();
  setStreaming(true);
  try {
    // the page being looked at; for a spread the left page, the window then covers both
    const res = await fetch(`${API}/chat`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page, window: P.win + (visiblePages().length > 1 ? 1 : 0), pages: visiblePages(), images: P.vision, messages: apiMessages() }),
      signal: P.stream.signal,
    });
    if (res.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash); return; }
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
        if (ev === 'delta') { reply.content += data.text; if (!frame) frame = requestAnimationFrame(paint); }
        else if (ev === 'error') P.chat.push({ role: 'assistant', error: true, content: data.message, page });
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') P.chat.push({ role: 'assistant', error: true, content: 'Ошибка: ' + e.message, page });
  } finally {
    P.stream = null;
    reply.pending = false;
    if (!reply.content) P.chat.splice(P.chat.indexOf(reply), 1);
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
$('#ai-send').addEventListener('click', () => { if (P.stream) P.stream.abort(); else sendMessage($('#ai-input').value); });
$('#ai-clear').addEventListener('click', async () => {
  if (P.stream) P.stream.abort();
  if (P.chat.length && !(await confirmDialog({ title: 'Начать новый разговор?', text: 'Текущая переписка о книге будет удалена.', ok: 'Очистить' }))) return;
  P.chat = [];
  saveChat();
  renderAi();
});
$('#ai-window').value = String(P.win);
$('#ai-vision').checked = P.vision;
$('#ai-vision').addEventListener('change', e => { P.vision = e.target.checked; storage.set('reader3:pdf:vision', P.vision); });
$('#ai-window').addEventListener('change', e => { P.win = +e.target.value; storage.set('reader3:pdf:win', P.win); updateAiContext(); });

// ---------- Keyboard ----------

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (isTyping()) { document.activeElement.blur(); return; }
    const open = $$('.panel.open');
    if (open.length) { open.forEach(p => p.classList.remove('open')); syncPanels(); }
    return;
  }
  if (isTyping() || e.ctrlKey || e.metaKey || e.altKey || $('dialog[open]')) return;
  const k = e.key;
  const acts = {
    ArrowRight: () => step(1), PageDown: () => step(1), ' ': () => step(e.shiftKey ? -1 : 1),
    ArrowLeft: () => step(-1), PageUp: () => step(-1),
    Home: () => go(0), End: () => go(P.n - 1),
    '+': () => $('#zoom-in').click(), '=': () => $('#zoom-in').click(), '-': () => $('#zoom-out').click(),
    f: () => $('#fit-btn').click(), d: () => $('#spread-btn').click(),
    t: () => $('#toc-btn').click(), x: () => $('#text-btn').click(), a: () => $('#ai-btn').click(),
    '/': () => $('#search-btn').click(),
  };
  const fn = acts[k] || acts[k.toLowerCase()];
  if (fn) { e.preventDefault(); fn(); }
});

// ---------- Start ----------

async function init() {
  applySettings(P.settings);
  try {
    const [info, state, config] = await Promise.all([api(API + '/info'), api(`/api/books/${ENC_ID}/state`), api('/api/config')]);
    P.info = info; P.n = info.pages; P.config = config;
    const hash = /#p=(\d+)/.exec(location.hash);
    const start = hash ? +hash[1] - 1 : (state.chapter || 0);
    $('#scrub').max = P.n;
    $('#page-input').max = P.n;
    renderToc();
    renderThumbs();
    renderAi();
    const panels = storage.get('reader3:pdf:panels', { sidebar: false, ai: false });
    selectTab(storage.get('reader3:pdf:tab', info.outline.length ? 'toc' : 'pages'));
    if (docked.matches) { $('#sidebar').classList.toggle('open', !!panels.sidebar); $('#ai-panel').classList.toggle('open', !!panels.ai); }
    syncPanels();
    go(clamp(start, 0, P.n - 1));
    if (info.ocr_pending > 0 && info.ocr_available) setTimeout(pollInfo, 4000);
  } catch (e) {
    if (e.status === 401) return;
    box.innerHTML = `<div class="toc-empty">Не удалось открыть PDF: ${escapeHtml(e.message)}</div>`;
  }
}
init();
