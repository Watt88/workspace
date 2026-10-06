hydrateIcons();

const state = { books: [], query: '', sort: storage.get('reader3:sort', 'recent'), uploads: [] };

const grid = $('#grid');
const fileInput = $('#file-input');

function coverHtml(book) {
  if (book.cover) {
    return `<div class="cover"><img src="${escapeHtml(book.cover)}" alt="" loading="lazy"></div>`;
  }
  return `<div class="cover generated" style="background:${coverGradient(book.title)}">
      <div class="g-title">${escapeHtml(book.title)}</div>
      <div class="g-author">${escapeHtml(book.authors.join(', '))}</div>
    </div>`;
}

function progressHtml(book) {
  const pct = Math.round((book.progress || 0) * 100);
  if (pct >= 99) return '<span class="done">Прочитано</span>';
  if (!book.last_read) return book.kind === 'pdf' ? `<span>PDF · ${book.chapters} стр.</span>` : `<span>${readingTime(book.words)}</span>`;
  return `<div class="progress-track"><div style="width:${pct}%"></div></div><span>${pct}%</span>`;
}

function sortBooks(books) {
  const by = {
    recent: (a, b) => (b.last_read || '').localeCompare(a.last_read || '') || (b.added || '').localeCompare(a.added || ''),
    added: (a, b) => (b.added || '').localeCompare(a.added || ''),
    title: (a, b) => a.title.localeCompare(b.title, 'ru'),
    author: (a, b) => (a.authors[0] || '').localeCompare(b.authors[0] || '', 'ru'),
    progress: (a, b) => (b.progress || 0) - (a.progress || 0),
  }[state.sort];
  return [...books].sort(by);
}

function render() {
  $('#loading').hidden = true;
  const hasBooks = state.books.length > 0 || state.uploads.length > 0;
  $('#empty').hidden = hasBooks;
  $('#shelf-section').hidden = !hasBooks;
  $('#count').textContent = state.books.length || '';

  // Continue reading: the most recently opened, unfinished book
  const recent = state.books.filter(b => b.last_read && b.progress < 0.99)
    .sort((a, b) => b.last_read.localeCompare(a.last_read))[0];
  const cont = $('#continue');
  cont.hidden = !recent || !!state.query;
  if (recent) {
    const pct = Math.round(recent.progress * 100);
    const left = Math.round(recent.words * (1 - recent.progress));
    const leftText = recent.kind === 'pdf' ? `${Math.round(recent.chapters * (1 - recent.progress))} стр.` : readingTime(left);
    cont.innerHTML = `
      <a class="continue" href="/read/${encodeURIComponent(recent.id)}">
        ${coverHtml(recent)}
        <div class="continue-body">
          <div class="eyebrow">Продолжить чтение</div>
          <h3>${escapeHtml(recent.title)}</h3>
          <div class="author">${escapeHtml(recent.authors.join(', ') || 'Автор неизвестен')}</div>
          <div class="progress-row"><div class="progress-track"><div style="width:${pct}%"></div></div>
            <span>${pct}% · осталось ~${leftText}</span></div>
          <div class="progress-row" style="margin-top:6px">Открывали ${relativeTime(recent.last_read)}</div>
          <span class="btn primary">${icon('book', 'sm')}Продолжить</span>
        </div>
      </a>`;
  }

  const q = state.query.trim().toLowerCase();
  const books = sortBooks(state.books).filter(b =>
    !q || b.title.toLowerCase().includes(q) || b.authors.join(' ').toLowerCase().includes(q));
  $('#no-results').hidden = !q || books.length > 0;

  grid.innerHTML = state.uploads.map(u => `
      <div class="card uploading">
        <div class="cover"><div class="spinner"></div></div>
        <div class="card-title">${escapeHtml(u)}</div>
        <div class="card-author">Обработка…</div>
      </div>`).join('') +
    books.map(b => `
      <a class="card" href="/read/${encodeURIComponent(b.id)}" data-id="${escapeHtml(b.id)}">
        ${coverHtml(b)}
        <button class="icon-btn card-menu-btn" aria-haspopup="menu" aria-label="Действия" data-menu="${escapeHtml(b.id)}">${icon('more', 'sm')}</button>
        <div class="card-title">${escapeHtml(b.title)}</div>
        <div class="card-author">${escapeHtml(b.authors.join(', ') || 'Автор неизвестен')}</div>
        <div class="card-meta">${progressHtml(b)}</div>
      </a>`).join('');
}

async function loadBooks() {
  try {
    state.books = await api('/api/books');
  } catch (e) {
    toast('Не удалось загрузить библиотеку: ' + e.message, { type: 'error' });
  }
  render();
}

async function uploadFiles(files) {
  const accepted = [...files].filter(f => /[.](epub|pdf|djvu?)$/i.test(f.name));
  if (!accepted.length) { toast('Поддерживаются файлы EPUB, PDF и DjVu', { type: 'error' }); return; }
  for (const file of accepted) {
    state.uploads.push(file.name);
    render();
    const fd = new FormData();
    fd.append('file', file);
    try {
      const book = await api('/api/books', { method: 'POST', body: fd });
      state.books.push(book);
      toast(`«${book.title}» добавлена`);
    } catch (e) {
      toast(`${file.name}: ${e.message}`, { type: 'error', timeout: 5000 });
    }
    state.uploads.splice(state.uploads.indexOf(file.name), 1);
    render();
  }
}

// ---------- Events ----------

$('#add-btn').addEventListener('click', () => fileInput.click());
$('#empty-add').addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => { uploadFiles(fileInput.files); fileInput.value = ''; });

$('#search').addEventListener('input', e => { state.query = e.target.value; render(); });
$('#sort').value = state.sort;
$('#sort').addEventListener('change', e => { state.sort = e.target.value; storage.set('reader3:sort', state.sort); render(); });

// Card menu
let menuBook = null;
grid.addEventListener('click', e => {
  const btn = e.target.closest('[data-menu]');
  if (!btn) return;
  e.preventDefault();
  menuBook = state.books.find(b => b.id === btn.dataset.menu);
  openMenu($('#card-menu'), btn);
});

$('#card-menu').addEventListener('click', async e => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (!act || !menuBook) return;
  closeMenus();
  const book = menuBook;
  if (act === 'open') location.href = `/read/${encodeURIComponent(book.id)}`;
  if (act === 'reset') {
    await api(`/api/books/${encodeURIComponent(book.id)}/state`, { method: 'PATCH', json: { chapter: 0, position: 0, progress: 0 } });
    location.href = `/read/${encodeURIComponent(book.id)}`;
  }
  if (act === 'delete') {
    const ok = await confirmDialog({
      title: 'Удалить книгу?',
      text: `«${book.title}» будет удалена вместе с прогрессом, закладками и заметками.`,
      ok: 'Удалить', danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/books/${encodeURIComponent(book.id)}`, { method: 'DELETE' });
      state.books = state.books.filter(b => b.id !== book.id);
      render();
      toast('Книга удалена');
    } catch (err) { toast(err.message, { type: 'error' }); }
  }
});

// Theme menu
const themeBtn = $('#theme-btn');
function syncThemeMenu() {
  const s = loadSettings();
  $$('[data-theme-opt]').forEach(b => {
    b.innerHTML = (b.dataset.themeOpt === s.theme ? icon('check', 'sm') : '<span style="width:16px"></span>') + b.textContent;
  });
  themeBtn.innerHTML = icon(resolvedTheme(s.theme) === 'light' || resolvedTheme(s.theme) === 'sepia' ? 'sun' : 'moon');
}
themeBtn.addEventListener('click', () => { syncThemeMenu(); openMenu($('#theme-menu'), themeBtn); });
$('#theme-menu').addEventListener('click', e => {
  const opt = e.target.closest('[data-theme-opt]')?.dataset.themeOpt;
  if (!opt) return;
  const s = { ...loadSettings(), theme: opt };
  saveSettings(s);
  applySettings(s);
  syncThemeMenu();
  closeMenus();
});
syncThemeMenu();

// Drag & drop anywhere on the page
let dragDepth = 0;
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; $('#drop-overlay').hidden = false; });
window.addEventListener('dragleave', e => { if (!hasFiles(e)) return; if (--dragDepth <= 0) { dragDepth = 0; $('#drop-overlay').hidden = true; } });
window.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('drop', e => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  $('#drop-overlay').hidden = true;
  uploadFiles(e.dataTransfer.files);
});

document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); $('#search').focus(); }
  if (e.key === 'Escape') closeMenus();
});

// Progress changes while reading in another tab
window.addEventListener('pageshow', e => { if (e.persisted) loadBooks(); });

loadBooks();
