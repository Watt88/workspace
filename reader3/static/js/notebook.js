'use strict';
// Notebook mode: sources | chat with verified citations | Studio + notes
hydrateIcons();

const S = {
  books: [], nbs: [], nb: null, sel: [], chat: [], stream: null, agentReady: true,
  mode: storage.get('reader3:nb:mode', 'chat'), length: storage.get('reader3:nb:length', 'default'),
  poll: null, studioBusy: false, pane: 'sources', openGuides: new Set(),
};
const CITE_RE = /\[\[\s*([A-Za-z]):\s*([cC]?\d+(?:\s*[-–]\s*\d+)?)\s*(?:\|\s*([\s\S]+?))?\s*\]\]/g;
const attr = s => escapeHtml(String(s ?? '')).replace(/"/g, '&quot;');
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const byId = id => S.books.find(b => b.id === id);
const nbKey = () => `reader3:nbchat:${S.nb?.id}`;

// ---------- SSE helper ----------

async function streamSSE(url, body, on, signal) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (res.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = /^event: (.*)$/m.exec(chunk)?.[1];
      let data = {};
      try { data = JSON.parse(/^data: (.*)$/m.exec(chunk)?.[1] || '{}'); } catch (_) {}
      on(ev, data);
    }
  }
}

// ---------- init ----------

async function init() {
  applySettings(loadSettings());
  await loadSources();            // before the notebooks: the saved selection is matched against the books
  await loadNotebooks();
  $('#mode').value = S.mode; $('#length').value = S.length;
  renderAll();
}

async function loadSources() {
  const r = await api('/api/notebook/sources');
  S.books = r.books; S.agentReady = r.agent_ready;
  $('#engine').textContent = `${r.embedder.split('/').pop()} + ${r.reranker.split('/').pop()}`;
  $('#engine').title = `Эмбеддер: ${r.embedder}\nРеранкер: ${r.reranker}\nПорог «нет ответа»: ${r.threshold}`;
}

async function loadNotebooks() {
  S.nbs = await api('/api/notebooks');
  const want = storage.get('reader3:nb:current', null);
  const id = S.nbs.find(n => n.id === want)?.id || S.nbs[0].id;
  await openNotebook(id);
}

async function openNotebook(id) {
  S.nb = await api(`/api/notebooks/${id}`);
  storage.set('reader3:nb:current', id);
  S.sel = (S.nb.books || []).filter(b => byId(b));
  const pending = storage.get(pendKey(), null);                    // a pick the server has not confirmed yet is newer than its copy
  if (Array.isArray(pending)) { S.sel = pending.filter(b => byId(b)); selDirty = true; sendSel(); }
  S.chat = storage.get(nbKey(), []);
  renderAll();
}

function renderAll() {
  $('#nb-select').innerHTML = S.nbs.map(n => `<option value="${attr(n.id)}"${n.id === S.nb.id ? ' selected' : ''}>${escapeHtml(n.title)}</option>`).join('');
  renderSources(); renderChat(); renderStudio(); renderNotes(); renderHint();
  maybePoll();
}

// ---------- notebooks ----------

$('#nb-select').addEventListener('change', e => openNotebook(e.target.value));

function askText(title, value = '') {
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'modal';
    d.innerHTML = `<div class="modal-head"><h2>${escapeHtml(title)}</h2></div><div class="modal-body"><input class="input" style="width:100%" value="${attr(value)}"></div>
      <div class="modal-foot"><button class="btn" data-x>Отмена</button><button class="btn primary" data-ok>OK</button></div>`;
    document.body.appendChild(d);
    const inp = $('input', d);
    const done = v => { d.close(); d.remove(); resolve(v); };
    $('[data-x]', d).onclick = () => done(null);
    $('[data-ok]', d).onclick = () => done(inp.value.trim() || null);
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') done(inp.value.trim() || null); });
    d.showModal(); inp.select();
  });
}

$('#nb-new').onclick = async () => {
  const t = await askText('Название блокнота', 'Новый блокнот');
  if (!t) return;
  const nb = await api('/api/notebooks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: t, books: S.sel }) });
  S.nbs = await api('/api/notebooks');
  await openNotebook(nb.id);
};
$('#nb-rename').onclick = async () => {
  const t = await askText('Название блокнота', S.nb.title);
  if (!t) return;
  await api(`/api/notebooks/${S.nb.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: t }) });
  S.nbs = await api('/api/notebooks'); S.nb.title = t; renderAll();
};
$('#nb-del').onclick = async () => {
  if (S.nbs.length < 2) { toast('Нужен хотя бы один блокнот', { type: 'error' }); return; }
  if (!(await confirmDialog({ title: 'Удалить блокнот?', text: `«${S.nb.title}» и все его заметки будут удалены. Книги останутся.`, ok: 'Удалить', danger: true }))) return;
  await api(`/api/notebooks/${S.nb.id}`, { method: 'DELETE' });
  storage.set(nbKey(), []);
  S.nbs = await api('/api/notebooks');
  await openNotebook(S.nbs[0].id);
};

let selDirty = false;
const pendKey = () => `reader3:nb:selpending:${S.nb?.id}`;
const sendSel = (keepalive = false) => {
  if (!selDirty || !S.nb) return;
  selDirty = false;
  const key = pendKey(), sent = JSON.stringify(S.sel);
  fetch(`/api/notebooks/${S.nb.id}`, { method: 'PATCH', keepalive, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ books: S.sel }) })
    .then(r => { if (r.ok && JSON.stringify(S.sel) === sent) storage.remove(key); }).catch(() => {});
};
const saveSelSoon = debounce(() => sendSel(), 150);
const saveSel = () => { selDirty = true; storage.set(pendKey(), S.sel); saveSelSoon(); };       // written locally at once, confirmed by the server later
addEventListener('pagehide', () => sendSel(true));          // a pick made a moment before leaving the page is not lost
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') sendSel(true); });

// ---------- sources ----------

function kindLabel(b) { return b.kind === 'pdf' ? 'PDF/DjVu' : 'EPUB'; }

function renderSources() {
  const box = $('#sources');
  $('#src-count').textContent = `${S.sel.length} из ${S.books.length}`;
  box.innerHTML = S.books.map(b => {
    const on = S.sel.includes(b.id);
    const idx = b.index.ready ? `<span class="chip ok">индекс: ${b.index.chunks} кусков${b.index.dense ? '' : ' (только слова)'}</span>${b.index.stale ? '<span class="chip warn" title="Индекс построен другой моделью: поиск по смыслу выключен, пока книгу не обновить">индекс устарел</span>' : ''}` : '<span class="chip warn">не подготовлена</span>';
    const summ = b.summaries ? `<span class="chip ok">конспекты глав: ${b.summaries}</span>` : '';
    const ocr = b.ocr_pending ? `<span class="chip warn">нет текста: ${b.ocr_pending} стр.</span>` : '';
    const job = b.job || {};
    const running = job.state === 'running';
    const prog = running ? `<div class="bar"><i style="width:${Math.round((job.progress || 0) * 100)}%"></i></div><div class="step">${escapeHtml(job.step || '')}</div>`
      : (job.state === 'error' ? `<div class="step" style="color:#b3261e">Ошибка: ${escapeHtml(job.step || '')}</div>` : '');
    let guide = '';
    if (b.guide?.overview) {
      guide = `<details class="guide" data-g="${attr(b.id)}"${S.openGuides.has(b.id) ? ' open' : ''}><summary>Гид по книге</summary><p>${escapeHtml(b.guide.overview)}</p>
        ${b.guide.audience ? `<p><b>Для кого:</b> ${escapeHtml(b.guide.audience)}</p>` : ''}
        <div class="topics">${(b.guide.topics || []).map(t => `<span class="chip">${escapeHtml(t)}</span>`).join('')}</div>
        ${(b.guide.questions || []).map(q => `<button class="q" data-q="${attr(q)}" data-book="${attr(b.id)}">${escapeHtml(q)}</button>`).join('')}</details>`;
    }
    const btn = running ? '' : `<button class="btn small ${b.index.ready ? 'ghost' : 'primary'}" data-prep="${attr(b.id)}">${b.index.ready ? (b.index.stale ? 'Переиндексировать' : (b.guide ? 'Обновить' : 'Достроить гид')) : 'Подготовить'}</button>`;
    return `<div class="src${on ? ' picked' : ''}" data-id="${attr(b.id)}">
      <div class="src-top"><input type="checkbox" ${on ? 'checked' : ''} ${b.index.ready ? '' : 'disabled'} aria-label="Выбрать">
        ${b.cover ? `<img class="src-cover" src="${attr(b.cover)}" alt="" loading="lazy">` : '<div class="src-cover"></div>'}
        <div><div class="src-name">${escapeHtml(b.title)}</div>
          <div class="src-meta"><span>${kindLabel(b)}</span><span>${b.locs} ${b.kind === 'pdf' ? 'стр.' : 'гл.'}</span><span>${escapeHtml(b.lang || '')}</span></div>
          <div class="src-meta">${idx}${summ}${ocr}</div></div></div>
      ${prog}<div class="src-actions">${btn}<a class="btn small ghost" href="/read/${encodeURIComponent(b.id)}" target="_blank" rel="noopener">Читать</a></div>${guide}</div>`;
  }).join('') || '<p class="muted">В библиотеке нет книг. Добавьте EPUB, PDF или DjVu на главной странице.</p>';
}

$('#sources').addEventListener('toggle', e => {
  const d = e.target.closest?.('details.guide');
  if (d) d.open ? S.openGuides.add(d.dataset.g) : S.openGuides.delete(d.dataset.g);
}, true);

$('#sources').addEventListener('click', async e => {
  const q = e.target.closest('.q');
  if (q) {
    const id = q.dataset.book;
    if (!S.sel.includes(id)) { S.sel.push(id); saveSel(); renderSources(); }
    showPane('chat'); send(q.dataset.q); return;
  }
  const prep = e.target.closest('[data-prep]');
  if (prep) {
    const b = byId(prep.dataset.prep);
    await api('/api/notebook/prepare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ book: b.id, force: b.index.ready && !!b.guide && !b.index.stale }) });
    toast('Подготовка запущена: индекс, конспекты глав, гид');
    await loadSources(); renderSources(); maybePoll(); return;
  }
  const card = e.target.closest('.src');
  if (card && (e.target.tagName === 'INPUT' || (!e.target.closest('a,button,details,summary') && true))) {
    const b = byId(card.dataset.id);
    if (!b.index.ready) return;
    const i = S.sel.indexOf(b.id);
    if (i >= 0) S.sel.splice(i, 1); else S.sel.push(b.id);
    saveSel(); renderSources(); renderHint(); renderSuggest(); renderStudio();
  }
});

function maybePoll() {
  const busy = S.books.some(b => b.job?.state === 'running');
  if (busy && !S.poll) {
    S.poll = setInterval(async () => {
      await loadSources(); renderSources(); renderHint();
      if (!S.books.some(b => b.job?.state === 'running')) { clearInterval(S.poll); S.poll = null; renderStudio(); renderSuggest(); toast('Подготовка книги завершена'); }
    }, 2500);
  }
}

// ---------- chat ----------

function citeLabel(c, src) { return `${c.a.toUpperCase()}·${src?.kind === 'epub' || /^c/i.test(c.loc) ? c.loc.replace(/^c/i, 'гл.') : c.loc}`; }

function citeHtml(c, sources, v) {
  const a = c.a.toUpperCase();
  const src = sources?.[a];
  const st = v?.status || 'pending';
  let mark = '';
  if (st === 'ok' || st === 'fuzzy') mark = ' ✓';
  else if (st === 'moved') mark = ` ↪${v.found || ''}`;
  else if (st === 'missing') mark = ' ?';
  const title = src ? `${src.title}` : 'источник не найден';
  return `<button class="cite ${st}" data-book="${attr(src?.id || '')}" data-loc="${attr(c.loc)}" data-q="${attr(c.q)}" title="${attr(title)}">${escapeHtml(citeLabel(c, src))}${mark}</button>`;
}

function renderWithCites(text, sources, verify) {
  text = (text || '').replace(/\[\[[^\]]*$/, '');                     // a tag still being written
  const cites = [];
  const t = text.replace(CITE_RE, (m, a, loc, q) => { cites.push({ a, loc: loc.replace(/\s/g, ''), q: (q || '').trim() }); return `@@C${cites.length - 1}@@`; });
  return renderMarkdown(t).replace(/@@C(\d+)@@/g, (_, i) => citeHtml(cites[+i], sources, verify?.[+i]));
}

function describeTool(s) {
  const i = s.input || {}, src = i.source ? ` (${i.source})` : '';
  switch (s.tool) {
    case 'search': return `Поиск: «${i.query || ''}»${src}`;
    case 'read': return `Чтение ${i.source || ''}: ${i.start}${i.end && i.end !== i.start ? '–' + i.end : ''}`;
    case 'outline': return `Оглавление ${i.source || ''}`;
    case 'summary': return `Конспекты ${i.source || ''}${i.section ? ': ' + i.section : ''}`;
    case 'find_exact': return `Точная фраза: «${i.phrase || ''}»`;
    default: return 'Список источников';
  }
}

function verifySummary(v) {
  if (!v?.length) return '';
  const n = k => v.filter(x => x.status === k).length;
  const good = n('ok') + n('fuzzy'), moved = n('moved'), bad = n('missing');
  const parts = [`${good} подтверждены`];
  if (moved) parts.push(`${moved} найдены на другой странице`);
  if (bad) parts.push(`<b class="bad">${bad} не найдены в тексте</b>`);
  return `<div class="verify-sum">Проверка цитат: ${parts.join(', ')}</div>`;
}

function msgHtml(m, i) {
  if (m.error) return `<div class="msg error">${escapeHtml(m.content)}</div>`;
  if (m.role === 'user') return `<div class="msg user">${escapeHtml(m.content)}</div>`;
  const trace = m.trace?.length ? `<details class="trace"${m.pending ? ' open' : ''}><summary>Ход поиска: ${m.trace.length} шаг.</summary>${m.trace.map(t => `<div>${escapeHtml(t)}</div>`).join('')}</details>` : '';
  const body = m.content ? renderWithCites(m.content, m.meta?.sources, m.verify) : (m.pending ? '<div class="typing"><span></span><span></span><span></span></div>' : '');
  const acts = !m.pending && m.content ? `<div class="msg-actions"><button class="btn small ghost" data-save="${i}">Сохранить в заметки</button><button class="btn small ghost" data-copy="${i}">Копировать</button></div>` : '';
  return `<div class="msg assistant">${trace}${body}${m.pending ? '' : verifySummary(m.verify)}${acts}</div>`;
}

function renderChat() {
  const box = $('#messages');
  if (!S.chat.length) {
    box.innerHTML = `<div class="muted" style="margin:auto;max-width:460px;text-align:center;line-height:1.6">
      <div style="font-size:28px;color:var(--accent)">${icon('sparkles')}</div><h3 style="margin:.4em 0">Разговор по книгам</h3>
      Выберите книги на вкладке «Источники» (сначала нажмите «Подготовить»), затем спрашивайте: ИИ ищет по всем выбранным книгам, читает нужные места и
      отвечает с цитатами. Каждая цитата проверяется по тексту книги. Можно читать на английском и спрашивать по-русски.</div>`;
  } else {
    box.innerHTML = S.chat.map((m, i) => `<div data-i="${i}">${msgHtml(m, i)}</div>`).join('');
    box.scrollTop = box.scrollHeight;
  }
  renderSuggest();
}

function renderSuggest() {
  const box = $('#suggest');
  if (S.stream || $('#input').value) { box.innerHTML = ''; return; }
  const qs = [];
  for (const id of S.sel) for (const q of (byId(id)?.guide?.questions || []).slice(0, 2)) qs.push(q);
  const generic = S.sel.length > 1 ? ['Сравни подходы авторов этих книг', 'Какие идеи повторяются в разных книгах?'] : ['О чём эта книга и кому она полезна?', 'Какие главные идеи?'];
  box.innerHTML = [...qs.slice(0, 3), ...generic].map(q => `<button data-q="${attr(q)}">${escapeHtml(q)}</button>`).join('');
}
$('#suggest').addEventListener('click', e => { const b = e.target.closest('button'); if (b) send(b.dataset.q); });

function renderHint() {
  let h = '';
  if (!S.agentReady) h = 'Блокнот работает через Claude Code: запустите сервер с READER3_BACKEND=claude-code.';
  else if (!S.sel.length) h = 'Выберите хотя бы одну подготовленную книгу.';
  else h = `Книг в разговоре: ${S.sel.length}. Ответ с поиском занимает 20–60 секунд.`;
  $('#chat-hint').textContent = h;
}

$('#mode').onchange = e => { S.mode = e.target.value; storage.set('reader3:nb:mode', S.mode); };
$('#length').onchange = e => { S.length = e.target.value; storage.set('reader3:nb:length', S.length); };
$('#chat-clear').onclick = async () => {
  if (S.stream) S.stream.abort();
  if (S.chat.length && !(await confirmDialog({ title: 'Начать новый разговор?', text: 'Переписка будет удалена (заметки останутся).', ok: 'Очистить' }))) return;
  S.chat = []; storage.set(nbKey(), []); renderChat();
};

const autosize = () => { const t = $('#input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 180) + 'px'; };
$('#input').addEventListener('input', () => { autosize(); renderSuggest(); });
$('#input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($('#input').value); } });
$('#send').onclick = () => { if (S.stream) S.stream.abort(); else send($('#input').value); };

function setSending(on) { $('#send').innerHTML = icon(on ? 'stop' : 'send'); $('#send').setAttribute('aria-label', on ? 'Остановить' : 'Отправить'); }

async function send(text) {
  text = (text || '').trim();
  if (!text || S.stream) return;
  if (!S.sel.length) { toast('Выберите хотя бы одну книгу', { type: 'error' }); showPane('sources'); return; }
  S.chat.push({ role: 'user', content: text });
  const m = { role: 'assistant', content: '', pending: true, trace: [], meta: null, verify: null };
  S.chat.push(m);
  $('#input').value = ''; autosize();
  renderChat();
  const msgs = S.chat.filter(x => !x.error && !x.pending && x.content).map(x => ({ role: x.role, content: x.content }));
  msgs.push({ role: 'user', content: text });
  const history = msgs.filter((x, i) => !(i === msgs.length - 1 && x.role === 'user' && msgs[i - 1]?.content === x.content));
  S.stream = new AbortController(); setSending(true);
  const node = () => $(`#messages [data-i="${S.chat.indexOf(m)}"]`);
  let frame = null;
  const paint = () => { frame = null; const el = node(); if (!el) return; const box = $('#messages'); const near = box.scrollHeight - box.scrollTop - box.clientHeight < 90; el.innerHTML = msgHtml(m, S.chat.indexOf(m)); if (near) box.scrollTop = box.scrollHeight; };
  try {
    await streamSSE('/api/notebook/chat', { books: [...S.sel], messages: history, mode: S.mode, length: S.length }, (ev, d) => {
      if (ev === 'meta') m.meta = d;
      else if (ev === 'status') { m.trace.push(describeTool(d)); m.content = ''; }      // text before a tool call is a preface
      else if (ev === 'delta') m.content += d.text;
      else if (ev === 'verify') m.verify = d.citations;
      else if (ev === 'error') { S.chat.push({ role: 'assistant', error: true, content: d.message }); }
      if (!frame) frame = requestAnimationFrame(paint);
    }, S.stream.signal);
  } catch (e) {
    if (e.name !== 'AbortError') S.chat.push({ role: 'assistant', error: true, content: 'Ошибка: ' + e.message });
  } finally {
    m.pending = false;
    if (!m.content && !m.trace.length) S.chat.splice(S.chat.indexOf(m), 1);
    S.stream = null; setSending(false);
    storage.set(nbKey(), S.chat.slice(-60));
    renderChat();
  }
}

$('#messages').addEventListener('click', async e => {
  const cite = e.target.closest('.cite');
  if (cite) { openPassage(cite.dataset.book, cite.dataset.loc, cite.dataset.q); return; }
  const sv = e.target.closest('[data-save]');
  if (sv) {
    const i = +sv.dataset.save, m = S.chat[i];
    const q = [...S.chat.slice(0, i)].reverse().find(x => x.role === 'user')?.content || 'Ответ';
    const note = await api(`/api/notebooks/${S.nb.id}/notes`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'answer', title: q.slice(0, 70), content: m.content, sources: m.meta?.sources || {}, verify: m.verify || [] }) });
    S.nb.notes.unshift(note); renderNotes(); toast('Сохранено в заметки'); return;
  }
  const cp = e.target.closest('[data-copy]');
  if (cp) { navigator.clipboard?.writeText(S.chat[+cp.dataset.copy].content.replace(CITE_RE, (m, a, loc) => `[${a}:${loc}]`)); toast('Скопировано'); }
});

// ---------- citation popover ----------

function closePop() { $('#pop').hidden = true; $('#pop-back').hidden = true; }
$('#pop-close').onclick = closePop; $('#pop-back').onclick = closePop;
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePop(); });

async function openPassage(book, loc, q) {
  if (!book) { toast('Источник не найден', { type: 'error' }); return; }
  $('#pop').hidden = false; $('#pop-back').hidden = false;
  $('#pop-title').textContent = 'Загружаю…'; $('#pop-sub').textContent = ''; $('#pop-status').textContent = ''; $('#pop-text').textContent = '';
  try {
    const r = await api(`/api/notebook/passage?book=${encodeURIComponent(book)}&loc=${encodeURIComponent(loc)}&q=${encodeURIComponent(q || '')}`);
    $('#pop-title').textContent = r.title; $('#pop-sub').textContent = r.label;
    const st = { ok: ['ok', '✓ Цитата найдена в тексте на этой странице'], moved: ['moved', `↪ Цитата найдена на другой странице: ${r.label} (модель указала ${r.kind === 'pdf' ? 'стр.' : 'гл.'} ${r.cited_loc})`],
      missing: ['missing', 'Цитата не найдена дословно: выдержка может быть неточной. Показан указанный фрагмент.'], noquote: ['', 'Цитата без выдержки: проверить дословность нельзя.'] }[r.status] || ['', ''];
    $('#pop-status').innerHTML = `<span class="${st[0]}">${st[1]}</span>`;
    const t = r.text || '';
    if (r.hl) $('#pop-text').innerHTML = escapeHtml(t.slice(0, r.hl[0])) + '<mark>' + escapeHtml(t.slice(r.hl[0], r.hl[1])) + '</mark>' + escapeHtml(t.slice(r.hl[1]));
    else $('#pop-text').textContent = t || '(на этой странице нет текста)';
    $('#pop-open').href = r.open_url;
    setTimeout(() => $('#pop-text mark')?.scrollIntoView({ block: 'center' }), 50);
  } catch (e) { $('#pop-title').textContent = 'Ошибка'; $('#pop-status').textContent = e.message; }
}

// ---------- Studio ----------

const STUDIO = [
  ['Отчёты', [['briefing', 'Брифинг', 'Главные идеи, тезисы и выводы на 1–2 страницы'], ['study_guide', 'Учебный конспект', 'Понятия, конспект, вопросы для самопроверки'],
    ['faq', 'FAQ', 'Вопросы новичка и ответы с цитатами'], ['timeline', 'Хронология', 'События или шаги по порядку'],
    ['blog', 'Статья', 'Пост по материалу книги'], ['custom', 'Свой отчёт', 'Любое задание по книгам']]],
  ['Учёба', [['quiz', 'Тест', 'Вопросы с вариантами и пояснениями'], ['flashcards', 'Карточки', 'Для запоминания, с повторением']]],
  ['Структура', [['mindmap', 'Карта идей', 'Дерево тем с цитатами'], ['datatable', 'Таблица', 'Сравнение или перечень по вашему запросу']]],
];
const DLG = {
  custom: { focus: 'Задание', req: true, ph: 'Например: сравни, как авторы объясняют ритм' },
  datatable: { focus: 'Что свести в таблицу', req: true, ph: 'Например: техники сведения — название, зачем, где в книге' },
  quiz: { n: 10 }, flashcards: { n: 20 },
};

function renderStudio() {
  const ok = S.sel.length > 0 && S.agentReady;
  $('#studio').innerHTML = (S.studioMsg || '') + STUDIO.map(([g, items]) => `<div class="st-group">${g}</div><div class="studio-grid">${items.map(([k, t, d]) =>
    `<button class="st-card${ok && !S.studioBusy ? '' : ' busy'}" data-type="${k}"><b>${t}</b><span>${d}</span></button>`).join('')}</div>`).join('') +
    (ok ? '' : '<p class="muted" style="margin-top:14px">Выберите подготовленные книги, чтобы создавать материалы.</p>');
}

let dlgType = null;
$('#studio').addEventListener('click', e => {
  const c = e.target.closest('.st-card');
  if (!c || c.classList.contains('busy')) return;
  dlgType = c.dataset.type;
  const t = STUDIO.flatMap(x => x[1]).find(x => x[0] === dlgType);
  const cfg = DLG[dlgType] || {};
  $('#sd-title').textContent = t[1];
  $('#sd-scope').innerHTML = `<option value="">Все выбранные книги</option>` + S.sel.map((id, i) => `<option value="${'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[i]}">${'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[i]} — ${escapeHtml(byId(id)?.title || id)}</option>`).join('');
  $('#sd-n-row').hidden = !cfg.n; if (cfg.n) $('#sd-n').value = cfg.n;
  $('#sd-focus-row').hidden = !cfg.focus && dlgType !== 'briefing' && dlgType !== 'study_guide' && dlgType !== 'faq' && dlgType !== 'timeline' && dlgType !== 'blog' && dlgType !== 'quiz' && dlgType !== 'flashcards' && dlgType !== 'mindmap';
  $('#sd-focus-label').textContent = cfg.focus || 'Фокус (необязательно)';
  $('#sd-focus').placeholder = cfg.ph || 'Например: только про ритм и грув';
  $('#sd-focus').value = '';
  syncRange(); $('#studio-dlg').showModal();
});
function syncRange() { const one = !!$('#sd-scope').value; $('#sd-range-row').hidden = !one; }
$('#sd-scope').onchange = syncRange;
$('#sd-close').onclick = $('#sd-cancel').onclick = () => $('#studio-dlg').close();

$('#sd-go').onclick = async () => {
  const cfg = DLG[dlgType] || {};
  const focus = $('#sd-focus').value.trim();
  if (cfg.req && !focus) { toast('Опишите задание', { type: 'error' }); return; }
  $('#studio-dlg').close();
  const body = { nb: S.nb.id, books: [...S.sel], type: dlgType, n: +$('#sd-n').value || 10, book: $('#sd-scope').value || null,
    start: +$('#sd-from').value || null, end: +$('#sd-to').value || null, focus };
  S.studioBusy = true; S.studioMsg = '<div class="gen" id="gen">Создаю… <div id="gen-lines"></div></div>'; renderStudio();
  try {
    await streamSSE('/api/notebook/studio', body, (ev, d) => {
      if (ev === 'status') { const l = $('#gen-lines'); if (l) l.insertAdjacentHTML('beforeend', `<div>${escapeHtml(describeTool(d))}</div>`); }
      else if (ev === 'error') toast(d.message, { type: 'error', timeout: 6000 });
      else if (ev === 'result') { S.nb.notes.unshift(d.note); renderNotes(); showSideTab('notes'); openNote(d.note); }
    });
  } catch (e) { toast('Ошибка: ' + e.message, { type: 'error', timeout: 6000 }); }
  S.studioBusy = false; S.studioMsg = ''; renderStudio();
};

// ---------- notes ----------

const NOTE_TYPES = { briefing: 'Брифинг', study_guide: 'Конспект', faq: 'FAQ', timeline: 'Хронология', blog: 'Статья', custom: 'Отчёт', quiz: 'Тест',
  flashcards: 'Карточки', mindmap: 'Карта идей', datatable: 'Таблица', answer: 'Ответ', note: 'Заметка' };

function renderNotes() {
  const notes = S.nb?.notes || [];
  $('#notes-n').hidden = !notes.length; $('#notes-n').textContent = notes.length;
  $('#notes').innerHTML = notes.map(n => `<div class="note" data-id="${attr(n.id)}"><div class="note-top"><span class="note-type">${NOTE_TYPES[n.type] || n.type}</span><span class="grow"></span>
    <button class="icon-btn" data-del="${attr(n.id)}" aria-label="Удалить">${icon('trash', 'sm')}</button></div><div class="note-title">${escapeHtml(n.title || '(без названия)')}</div>
    <div class="note-date">${relativeTime(n.created)}</div></div>`).join('') || '<p class="muted" style="padding:10px 0">Здесь появятся сохранённые ответы и материалы Студии.</p>';
}
$('#notes').addEventListener('click', async e => {
  const del = e.target.closest('[data-del]');
  if (del) {
    e.stopPropagation();
    if (!(await confirmDialog({ title: 'Удалить заметку?', text: '', ok: 'Удалить', danger: true }))) return;
    await api(`/api/notebooks/${S.nb.id}/notes/${del.dataset.del}`, { method: 'DELETE' });
    S.nb.notes = S.nb.notes.filter(n => n.id !== del.dataset.del); renderNotes(); return;
  }
  const c = e.target.closest('.note');
  if (c) openNote(S.nb.notes.find(n => n.id === c.dataset.id));
});

function showSideTab(t) {
  $$('.seg button').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
  $('#studio').hidden = t !== 'studio'; $('#notes').hidden = t !== 'notes';
}
$$('.seg button').forEach(b => b.onclick = () => showSideTab(b.dataset.tab));

// ---------- note viewer ----------

function chipFromObj(c, sources) {
  if (!c || !c.q) return '';
  const a = String(c.s || '').toUpperCase();
  return citeHtml({ a, loc: String(c.loc || '1').replace(/\s/g, ''), q: c.q }, sources, { status: c.status, found: c.found });
}

function openNote(n) {
  if (!n) return;
  $('#viewer-title').textContent = n.title || NOTE_TYPES[n.type];
  const body = $('#viewer-body'); body.onclick = null;
  const ex = $('#viewer-export'); ex.hidden = true;
  const src = n.sources || {};
  body.onclick = e => { const c = e.target.closest('.cite'); if (c) openPassage(c.dataset.book, c.dataset.loc, c.dataset.q); };
  if (n.type === 'quiz') renderQuiz(n, body, src);
  else if (n.type === 'flashcards') renderCards(n, body, src);
  else if (n.type === 'mindmap') renderMindmap(n, body, src);
  else if (n.type === 'datatable') { renderTable(n, body, src); ex.hidden = false; ex.onclick = () => exportCsv(n); }
  else {
    body.innerHTML = renderWithCites(n.content || '', src, n.verify) + verifySummary(n.verify);
    ex.hidden = false; ex.onclick = () => download(`${(n.title || 'note').slice(0, 50)}.md`, mdExport(n), 'text/markdown');
  }
  $('#viewer').showModal();
}
$('#viewer-close').onclick = () => $('#viewer').close();

function download(name, text, type) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: type + ';charset=utf-8' }));
  a.download = name.replace(/[\\/:*?"<>|]/g, '_'); a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function mdExport(n) {
  const names = Object.fromEntries(Object.entries(n.sources || {}).map(([k, v]) => [k, v.title]));
  const txt = (n.content || '').replace(CITE_RE, (m, a, loc, q) => `(${names[a.toUpperCase()] || a}, ${/^c/i.test(loc) ? 'гл. ' + loc.slice(1) : 'стр. ' + loc}${q ? `: «${q.trim()}»` : ''})`);
  return `# ${n.title}\n\n${txt}\n`;
}
function exportCsv(n) {
  const d = n.data || {}, esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const lines = [d.columns.map(esc).join(',')].concat((d.rows || []).map(r => r.map(c => esc(c?.v ?? c)).join(',')));
  download(`${(n.title || 'table').slice(0, 50)}.csv`, '﻿' + lines.join('\n'), 'text/csv');
}
const saveNoteData = (n) => api(`/api/notebooks/${S.nb.id}/notes/${n.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data: n.data }) }).catch(() => {});

function renderQuiz(n, body, src) {
  const qs = n.data?.questions || []; let i = 0, score = 0;
  const show = () => {
    if (i >= qs.length) {
      const p = n.data.progress = { last: score, total: qs.length, best: Math.max(score, n.data.progress?.best || 0) };
      saveNoteData(n);
      body.innerHTML = `<h3>Результат: ${score} из ${qs.length}</h3><p class="prog">Лучший результат: ${p.best} из ${qs.length}</p><div class="row-btns"><button class="btn primary" id="qz-again">Пройти ещё раз</button></div>`;
      $('#qz-again').onclick = () => { i = 0; score = 0; show(); }; return;
    }
    const q = qs[i];
    body.innerHTML = `<div class="prog">Вопрос ${i + 1} из ${qs.length}${n.data.progress?.best != null ? ` · лучший результат ${n.data.progress.best}/${qs.length}` : ''}</div>
      <div class="quiz-q">${escapeHtml(q.q)}</div>${q.options.map((o, k) => `<button class="opt" data-k="${k}">${escapeHtml(o)}</button>`).join('')}<div id="qz-x"></div>`;
    $$('.opt', body).forEach(b => b.onclick = () => {
      if ($('#qz-x').innerHTML) return;
      const k = +b.dataset.k; if (k === q.answer) score++;
      $$('.opt', body).forEach(x => { x.classList.toggle('right', +x.dataset.k === q.answer); if (+x.dataset.k === k && k !== q.answer) x.classList.add('wrong'); });
      $('#qz-x').innerHTML = `<div class="expl">${escapeHtml(q.explanation || '')} ${chipFromObj(q.cite, src)}</div><div class="row-btns"><button class="btn primary" id="qz-next">${i + 1 < qs.length ? 'Дальше' : 'Результат'}</button></div>`;
      $('#qz-next').onclick = () => { i++; show(); };
    });
  };
  show();
}

function renderCards(n, body, src) {
  const cards = n.data?.cards || [];
  n.data.progress = n.data.progress || { known: [] };
  let queue = cards.map((_, k) => k).filter(k => !n.data.progress.known.includes(k));
  if (!queue.length) { n.data.progress.known = []; queue = cards.map((_, k) => k); }
  let flipped = false;
  const show = () => {
    if (!queue.length) {
      n.data.progress.known = cards.map((_, k) => k); saveNoteData(n);
      body.innerHTML = `<h3>Все карточки выучены</h3><div class="row-btns"><button class="btn primary" id="fc-reset">Начать заново</button></div>`;
      $('#fc-reset').onclick = () => { n.data.progress.known = []; saveNoteData(n); renderCards(n, body, src); }; return;
    }
    const c = cards[queue[0]];
    body.innerHTML = `<div class="prog">Осталось: ${queue.length} из ${cards.length} · выучено ${n.data.progress.known.length}</div>
      <div class="card3d" id="fc">${escapeHtml(flipped ? c.back : c.front)}<small>${flipped ? chipFromObj(c.cite, src) : 'нажмите, чтобы перевернуть'}</small></div>
      <div class="row-btns" ${flipped ? '' : 'hidden'}><button class="btn" id="fc-no">Повторить позже</button><button class="btn primary" id="fc-yes">Знаю</button></div>`;
    $('#fc').onclick = e => { if (e.target.closest('.cite')) return; flipped = !flipped; show(); };
    if (flipped) {
      $('#fc-yes').onclick = () => { n.data.progress.known.push(queue.shift()); flipped = false; saveNoteData(n); show(); };
      $('#fc-no').onclick = () => { queue.push(queue.shift()); flipped = false; show(); };
    }
  };
  show();
}

function renderMindmap(n, body, src) {
  const node = c => {
    const kids = (c.children || []).filter(Boolean);
    return `<li class="${kids.length > 1 && kids[0]?.children?.length ? '' : ''}"><span class="n${kids.length ? ' has' : ''}">${escapeHtml(c.title || '')} ${chipFromObj(c.cite, src)}</span>${kids.length ? `<ul>${kids.map(node).join('')}</ul>` : ''}</li>`;
  };
  const d = n.data || {};
  body.innerHTML = `<div class="row-btns" style="margin:0 0 10px"><button class="btn small" id="mm-all">Развернуть всё</button><button class="btn small" id="mm-none">Свернуть</button></div>
    <div class="mm"><ul><li><span class="n has" style="font-weight:600">${escapeHtml(d.title || 'Карта идей')}</span><ul>${(d.children || []).map(node).join('')}</ul></li></ul></div>`;
  body.querySelectorAll('.n.has').forEach(el => el.addEventListener('click', e => { if (e.target.closest('.cite')) return; el.parentElement.classList.toggle('closed'); }));
  $('#mm-all').onclick = () => body.querySelectorAll('li.closed').forEach(l => l.classList.remove('closed'));
  $('#mm-none').onclick = () => body.querySelectorAll('.mm li:has(> ul)').forEach((l, k) => { if (k > 0) l.classList.add('closed'); });
}

function renderTable(n, body, src) {
  const d = n.data || {};
  body.innerHTML = `<table class="dt"><thead><tr>${(d.columns || []).map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead><tbody>${(d.rows || []).map(r =>
    `<tr>${r.map(c => `<td>${escapeHtml(typeof c === 'object' ? c?.v : c)} ${typeof c === 'object' ? chipFromObj(c?.cite, src) : ''}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// ---------- layout (mobile tabs) ----------

function showPane(p) {
  S.pane = p;
  $$('#nb-tabs button').forEach(b => b.classList.toggle('on', b.dataset.pane === p));
  ['sources', 'chat', 'studio'].forEach(k => $(`#pane-${k}`).classList.toggle('on', k === p));
}
$$('#nb-tabs button').forEach(b => b.onclick = () => showPane(b.dataset.pane));
if (matchMedia('(max-width: 1100px)').matches) showPane('chat'); else ['sources', 'chat', 'studio'].forEach(k => $(`#pane-${k}`).classList.add('on'));

init().catch(e => { toast('Не удалось загрузить блокнот: ' + e.message, { type: 'error', timeout: 8000 }); });
