/* Bookcase 前端：书架 / 阅读 / 清理 / 设置 */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const PALETTES = [
    ['#4a3a28', '#241a10'], ['#3d2d40', '#1c1420'], ['#2f4038', '#141c18'],
    ['#4a2f2a', '#20130f'], ['#33414f', '#141a21'], ['#4d4330', '#1f1a12'],
    ['#3a3346', '#181420'], ['#443528', '#1b1510'],
  ];

  const state = {
    books: [],
    stats: null,
    tags: [],
    roots: [],
    filters: { q: '', status: 'all', favorite: false, format: 'all', tag: '', sort: 'added', listId: null },
    view: 'shelf',
    scanPoll: null,
    detailBook: null,
    lists: [],
    listId: null,
    editingList: null,
    editorForBook: null,
    pickerListId: null,
    pickerSel: new Set(),
    pickerBooks: [],
    checkMode: false,
    newBooks: [],
    deleteBook: null,
    turnHintShown: false,
  };

  const reader = {
    active: false,
    book: null,
    rendition: null,
    fontSize: 100,
    dark: true,
    lastCfi: '',
    lastPct: 0,
    saveTimer: null,
    bookId: null,
    locationsReady: false,
    allowSave: false,
  };

  const pdfState = { doc: null, book: null, page: 1, total: 0, saveTimer: null, renderToken: 0, suppressClick: false, swipe: null };

  function bindTap(el, fn) {
    // 单击即可（原来是双击）：用 click，触摸端在 viewport 设置下没有 300ms 延迟
    el.addEventListener('click', (e) => {
      e.preventDefault();
      fn();
    });
  }

  function makeReadBar(barSel, wakeSel, rangeSel, labelSel) {
    const bar = $(barSel), wake = $(wakeSel), range = $(rangeSel), label = $(labelSel);
    let timer = null;
    const hide = () => {
      bar.classList.add('hidden');
      wake.hidden = false;
      clearTimeout(timer);
    };
    const reset = () => {
      clearTimeout(timer);
      timer = setTimeout(hide, 5000);
    };
    const show = () => {
      bar.classList.remove('hidden');
      wake.hidden = true;
      reset();
    };
    bindTap(wake, show);
    bar.addEventListener('pointerdown', reset);
    range.addEventListener('input', reset);
    return { bar, wake, range, label, show, hide, reset };
  }

  const epubBar = makeReadBar('#epubProgress', '#epubWake', '#epubRange', '#epubPageLabel');
  const pdfBar = makeReadBar('#pdfProgress', '#pdfWake', '#pdfRange', '#pdfPageLabel');

  // ------------------------------------------------------------ 工具

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function pal(id) {
    const p = PALETTES[Math.abs(Number(id) || 0) % PALETTES.length];
    return `linear-gradient(150deg, ${p[0]}, ${p[1]})`;
  }

  function fmtBytes(n) {
    if (!n) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
  }

  function backAction() {
    // 给 APK 的返回手势用：关掉最上面一层，返回 true 表示已处理
    if (!$('#transferSheet').hidden) {
      closeTransfer();
      return true;
    }
    if (!$('#confirmSheet').hidden) {
      closeConfirm(false);
      return true;
    }
    if (!$('#deleteSheet').hidden) {
      $('#deleteSheet').hidden = true;
      state.deleteBook = null;
      lockBody();
      return true;
    }
    if (!$('#newSheet').hidden) {
      $('#newSheet').hidden = true;
      lockBody();
      return true;
    }
    if (!$('#picker').hidden) {
      $('#picker').hidden = true;
      lockBody();
      return true;
    }
    if (!$('#listSheet').hidden) {
      closeListEditor();
      return true;
    }
    if (!$('#pdfReader').hidden) {
      closePdfReader(true);
      return true;
    }
    if (reader.active) {
      closeReader(true);
      return true;
    }
    if (!$('#detail').hidden) {
      closeDetail();
      return true;
    }
    if (state.view !== 'shelf') {
      switchView('shelf');
      return true;
    }
    if (state.listId) {
      clearList();
      return true;
    }
    return false;
  }

  window.__bcBack = backAction;

  let confirmResolver = null;

  function uiConfirm(text, okLabel = '确定') {
    return new Promise(resolve => {
      confirmResolver = resolve;
      $('#confirmText').textContent = text;
      $('#confirmOk').textContent = okLabel;
      $('#confirmSheet').hidden = false;
      lockBody();
    });
  }

  function closeConfirm(result) {
    if ($('#confirmSheet').hidden) return;
    $('#confirmSheet').hidden = true;
    lockBody();
    const r = confirmResolver;
    confirmResolver = null;
    if (r) r(result);
  }

  function lockBody() {
    const open = !$('#detail').hidden || !$('#listSheet').hidden ||
      !$('#picker').hidden || !$('#newSheet').hidden || !$('#deleteSheet').hidden ||
      !$('#pdfReader').hidden || !$('#confirmSheet').hidden || !$('#transferSheet').hidden || reader.active;
    document.body.style.overflow = open ? 'hidden' : '';
  }

  const THEME_KEY = 'bookcase-theme';

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'light' ? '#f7f2e7' : '#121009');
    $$('[data-theme-btn]').forEach(b => b.classList.toggle('active', b.dataset.themeBtn === theme));
    try { localStorage.setItem(THEME_KEY, theme); } catch (_) { /* 忽略 */ }
  }

  function toast(msg, ms = 2200) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), ms);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* 空响应 */ }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  const patchBook = (id, body) =>
    api(`/api/book/${id}`, { method: 'PATCH', body: JSON.stringify(body) });

  // ------------------------------------------------------------ 数据加载

  function queryString() {
    const f = state.filters;
    const p = new URLSearchParams();
    if (f.q) p.set('q', f.q);
    if (f.status !== 'all') p.set('status', f.status);
    if (f.favorite) p.set('favorite', '1');
    if (f.format !== 'all') p.set('format', f.format);
    if (f.tag) p.set('tag', f.tag);
    if (f.listId) p.set('list', f.listId);
    p.set('sort', f.sort);
    return p.toString();
  }

  async function loadBooks() {
    const data = await api(`/api/books?${queryString()}`);
    state.books = data.books;
    renderGrid();
    if (data.scan && data.scan.running) startScanPoll();
  }

  async function loadStats() {
    state.stats = await api('/api/stats');
    renderSubtitle();
    renderSettingsStats();
    updateScanUI(state.stats.scan);
  }

  async function loadTags() {
    const data = await api('/api/tags');
    state.tags = data.tags;
    const sel = $('#tagSel');
    const cur = state.filters.tag;
    sel.innerHTML = '<option value="">全部标签</option>' +
      state.tags.map(t => `<option value="${esc(t.name)}">${esc(t.name)}（${t.count}）</option>`).join('');
    sel.value = cur;
  }

  async function refreshAll() {
    await Promise.all([loadBooks(), loadStats(), loadTags(), loadLists()]);
    if (state.view === 'clean') loadCleanup();
  }

  // ------------------------------------------------------------ 书架

  function renderSubtitle() {
    const s = state.stats;
    if (!s) return;
    const bits = [`共 ${s.total} 本`];
    if (s.reading) bits.push(`在读 ${s.reading}`);
    bits.push(s.sizeHuman);
    if (s.broken) bits.push(`${s.broken} 个损坏`);
    $('#subtitle').textContent = bits.join(' · ');
  }

  function renderGrid() {
    const grid = $('#grid');
    const empty = $('#shelfEmpty');
    if (!state.books.length) {
      grid.innerHTML = '';
      empty.hidden = false;
      const hasFilter = state.filters.q || state.filters.status !== 'all' ||
        state.filters.favorite || state.filters.format !== 'all' || state.filters.tag;
      if (state.filters.listId && !hasFilter) {
        empty.innerHTML = '<strong>这个书单还是空的</strong>点上面的「＋ 添加书籍」把书加进来';
      } else {
        empty.innerHTML = hasFilter
          ? '<strong>没有匹配的书</strong>换个关键词，或清掉筛选条件试试'
          : '<strong>书架还是空的</strong>点右上角刷新按钮开始扫描<br>会读取 Download 等目录里的 epub / pdf / mobi';
      }
      return;
    }
    empty.hidden = true;
    const countEl = $('#shelfCount');
    if (countEl) countEl.textContent = `${state.books.length} 本`;
    grid.innerHTML = state.books.map(b => {
      const pct = Math.max(0, Math.min(100, b.progress || 0));
      const stLabel = b.status === 'reading' ? '在读' : b.status === 'done' ? '读完' : '未读';
      const date = b.addedAt ? new Date(b.addedAt * 1000) : null;
      const dateStr = date
        ? `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} 入库`
        : '';
      const meta = [(b.format || '').toUpperCase(), b.sizeHuman, dateStr].filter(Boolean).join('   ·   ');
      const cover = b.cover
        ? `<img src="/api/cover/${b.id}?v=${b.updatedAt || 0}" alt="" loading="lazy" onerror="this.remove()">`
        : `<span class="ph" style="--ph-bg:${pal(b.id)}"><i>${esc((b.title || "?").slice(0, 1))}</i></span>`;
      return `
      <article class="f1-row" data-id="${b.id}">
        <div class="f1-thumb">${cover}</div>
        <div class="f1-body">
          <div class="f1-title">${esc(b.title)}</div>
          <div class="f1-author">${esc(b.author || '未知作者')}${b.publisher ? ' · ' + esc(b.publisher) : ''}</div>
          <div class="f1-meta">${meta}${b.favorite ? '   ·   ♥ 收藏' : ''}</div>
        </div>
        <div class="f1-side ${b.status || 'unread'}">
          <span class="st">${stLabel}</span>
          ${pct > 0 && pct < 100 ? `<span class="pc">${pct}%</span>` : ''}
        </div>
      </article>`;
    }).join('');
  }

  // ------------------------------------------------------------ 详情

  async function openDetail(id) {
    try {
      const { book } = await api(`/api/book/${id}`);
      state.detailBook = book;
      renderDetail(book);
      $('#detail').hidden = false;
      lockBody();
    } catch (e) {
      toast(`打开失败：${e.message}`);
    }
  }

  function closeDetail() {
    $('#detail').hidden = true;
    state.detailBook = null;
    lockBody();
  }

  function renderDetail(b) {
    $('#detailBody').dataset.bookId = String(b.id);
    const facts = [
      `<span>${esc((b.format || '').toUpperCase())}</span>`,
      `<span>${b.sizeHuman}</span>`,
      b.pages ? `<span>${b.pages} 页</span>` : '',
      b.year ? `<span>${esc(b.year)}</span>` : '',
      b.publisher ? `<span>${esc(b.publisher)}</span>` : '',
    ].filter(Boolean).join('');

    const statusChips = [['unread', '未读'], ['reading', '在读'], ['done', '读完']]
      .map(([k, label]) => `<button class="chip ${b.status === k ? 'active' : ''}" data-set-status="${k}">${label}</button>`)
      .join('');

    const tagHtml = b.tags.length
      ? b.tags.map(t => `<span class="tag">${esc(t)}<button data-del-tag="${esc(t)}" aria-label="删除标签">×</button></span>`).join('')
      : '<span class="c-note" style="margin:0">还没有标签，比如「科幻」「待读」</span>';

    const readLabel = b.status === 'reading' ? '继续阅读' : (b.progress > 0 ? '继续阅读' : '开始阅读');

    const bookLists = b.listIds || [];
    const listChips = state.lists.map(l =>
      `<button class="chip ${bookLists.includes(l.id) ? 'active' : ''}" data-toggle-list="${l.id}">${esc(l.name)}</button>`
    ).join('');

    const coverInner = b.cover
      ? `<img src="/api/cover/${b.id}?v=${b.updatedAt || 0}" alt="" onerror="this.remove()">`
      : esc(b.title);

    $('#detailBody').innerHTML = `
      <div class="d-head">
        <div class="d-cover" style="--ph-bg:${pal(b.id)}">${coverInner}
          <button class="cover-edit" data-act="cover" aria-label="更换封面">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h3l1.4-2h7.2L17 8h3v11H4V8Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><circle cx="12" cy="13" r="3.1" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>
          </button>
          <button class="cover-restore" data-act="cover-restore" aria-label="恢复初始封面">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10a8 8 0 1 1 2.3 6.4M4 5v5h5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>
        <div class="d-info">
          <div class="d-title">${esc(b.title)}</div>
          <div class="d-author">${esc(b.author || '未知作者')}</div>
          <div class="d-facts">${facts}</div>
          ${b.broken ? '<div class="c-note" style="color:var(--bad);margin-top:8px">文件可能不完整，打不开就移入回收站</div>' : ''}
        </div>
      </div>

      <div class="d-section">
        <div class="d-label">阅读状态</div>
        <div class="statusrow">${statusChips}</div>
        ${b.progress > 0 ? `<div class="c-note" style="margin-top:8px">已读 ${b.progress}%</div>` : ''}
      </div>

      <div class="d-section">
        <div class="d-label">标签</div>
        <div class="taglist">${tagHtml}</div>
        <div class="tag-add">
          <input id="tagInput" type="text" placeholder="输入标签后回车" maxlength="24">
          <button id="tagAddBtn">添加</button>
        </div>
      </div>

      <div class="d-section">
        <div class="d-label">书单</div>
        <div class="taglist">
          ${listChips || '<span class="c-note" style="margin:0">还没有书单，建一个按阶段读</span>'}
          <button class="chip" data-new-list-for="${b.id}">＋ 新建书单</button>
        </div>
      </div>

      ${b.description ? `
      <div class="d-section">
        <div class="d-label">简介</div>
        <div class="d-desc">${esc(b.description)}</div>
      </div>` : ''}

      <div class="d-actions">
        <button class="btn primary" data-act="read">${readLabel}</button>
        <button class="btn" data-act="fav">${b.favorite ? '♥ 已收藏' : '♡ 收藏'}</button>
        <button class="btn" data-act="share">分享这本书</button>
        <button class="btn ghost" data-act="browser">在浏览器中打开原文件</button>
        <button class="btn danger" data-act="delete">移入回收站</button>
      </div>
      <div class="d-path">${esc(b.path)}<br>加入书库 ${new Date((b.addedAt || 0) * 1000).toLocaleDateString('zh-CN')}</div>
    `;
    bindDetail(b);
  }

  function bindDetail(b) {
    const body = $('#detailBody');

    $$('[data-set-status]', body).forEach(btn => {
      btn.onclick = async () => {
        const status = btn.dataset.setStatus;
        try {
          const { book } = await patchBook(b.id, { status });
          Object.assign(b, book);
          renderDetail(b);
          loadBooks();
          loadStats();
        } catch (e) { toast(`保存失败：${e.message}`); }
      };
    });

    const addTag = async () => {
      const input = $('#tagInput');
      const t = (input.value || '').trim();
      if (!t) return;
      if (b.tags.includes(t)) { input.value = ''; return; }
      try {
        const { book } = await patchBook(b.id, { tags: [...b.tags, t] });
        Object.assign(b, book);
        renderDetail(b);
        loadTags();
      } catch (e) { toast(`保存失败：${e.message}`); }
    };
    $('#tagAddBtn').onclick = addTag;
    $('#tagInput').addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); addTag(); }
    });

    const act = body.querySelector('[data-act="read"]');
    if (act) act.onclick = () => {
      closeDetail();
      openBook(b);
    };
    const fav = body.querySelector('[data-act="fav"]');
    if (fav) fav.onclick = async () => {
      try {
        const { book } = await patchBook(b.id, { favorite: !b.favorite });
        Object.assign(b, book);
        renderDetail(b);
        loadBooks();
        loadStats();
      } catch (e) { toast(`保存失败：${e.message}`); }
    };
    const browser = body.querySelector('[data-act="browser"]');
    if (browser) browser.onclick = () => {
      window.open(`/api/file/${b.id}`, '_blank');
    };
    const del = body.querySelector('[data-act="delete"]');
    if (del) del.onclick = () => openDeleteSheet(b.id, b);
    const share = body.querySelector('[data-act="share"]');
    if (share) share.onclick = () => shareBook(b);
    const coverBtn = body.querySelector('[data-act="cover"]');
    if (coverBtn) coverBtn.onclick = () => {
      $('#coverInput').dataset.kind = 'book';
      $('#coverInput').dataset.bookId = String(b.id);
      $('#coverInput').value = '';
      $('#coverInput').click();
    };
    const coverRestore = body.querySelector('[data-act="cover-restore"]');
    if (coverRestore) coverRestore.onclick = async () => {
      if (!(await uiConfirm('恢复这本书的初始封面？当前自定义封面会被替换。', '恢复'))) return;
      try {
        const res = await api(`/api/book/${b.id}/cover/restore`, { method: 'POST' });
        Object.assign(b, res.book);
        renderDetail(b);
        loadBooks();
        toast('已恢复初始封面');
      } catch (e) { toast(`恢复失败：${e.message}`); }
    };
  }

  async function uploadCover(file, bid) {
    const dataUrl = await compressImage(file, 700);
    const res = await api(`/api/book/${bid}/cover`, {
      method: 'POST',
      body: JSON.stringify({ data: dataUrl }),
    });
    toast('封面已更新');
    if (state.detailBook && state.detailBook.id === bid) {
      Object.assign(state.detailBook, res.book);
      renderDetail(state.detailBook);
    }
    loadBooks();
    loadLists();
  }

  async function uploadListCover(file, listId) {
    const dataUrl = await compressImage(file, 700);
    await api(`/api/lists/${listId}/cover`, {
      method: 'POST',
      body: JSON.stringify({ data: dataUrl }),
    });
    toast('书单封面已更新');
    await loadLists();
  }

  function compressImage(file, maxWidth) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        try {
          const scale = Math.min(1, maxWidth / img.width);
          const canvas = document.createElement('canvas');
          canvas.width = Math.round(img.width * scale);
          canvas.height = Math.round(img.height * scale);
          canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
          const data = canvas.toDataURL('image/jpeg', 0.86);
          URL.revokeObjectURL(url);
          resolve(data);
        } catch (e) {
          URL.revokeObjectURL(url);
          reject(e);
        }
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('图片读不出来')); };
      img.src = url;
    });
  }

  // ------------------------------------------------------------ 阅读

  function openBook(b) {
    if (b.format === 'pdf' && window.pdfjsLib) {
      openPdfReader(b);
    } else if (b.format === 'epub' && window.ePub) {
      openEpub(b);
    } else {
      window.open(`/api/file/${b.id}`, '_blank');
      toast('已交给浏览器打开（这种格式暂时无法内嵌阅读）', 2800);
    }
  }

  async function openEpub(b) {
    if (reader.active) closeReader(false);
    $('#reader').hidden = false;
    document.body.style.overflow = 'hidden';
    epubBar.show();
    $('#readerTitle').textContent = b.title;
    $('#readerPct').textContent = (b.progress || 0) + '%';
    reader.active = true;
    reader.bookId = b.id;
    reader.lastPct = b.progress || 0;
    reader.lastCfi = b.cfi || '';
    reader.locationsReady = false;
    reader.allowSave = false;
    const viewer = $('#viewer');
    viewer.innerHTML = '';

    try {
      const book = window.ePub(`/api/file/${b.id}`, { openAs: 'epub' });
      reader.book = book;
      await book.ready;
      const rendition = book.renderTo(viewer, {
        width: '100%',
        height: '100%',
        flow: 'paginated',
        spread: 'none',
        allowScriptedContent: false,
      });
      reader.rendition = rendition;

      rendition.themes.fontSize(reader.fontSize + '%');
      applyReaderTheme();
      rendition.themes.override('line-height', '1.75', false);

      await rendition.display(b.cfi || undefined);

      book.locations.generate(1200).then(() => {
        reader.locationsReady = true;
        reader.allowSave = true;
        if (reader.rendition && reader.lastCfi) {
          try { setReaderPct(book.locations.percentageFromCfi(reader.lastCfi) * 100); } catch (_) { /* 忽略 */ }
          scheduleSave();
          updateEpubBar(reader.lastCfi);
        }
      }).catch(err => {
        reader.locationsReady = false;
        reader.locationsError = String(err);
        console.warn('locations generate failed', err);
      });

      rendition.on('relocated', loc => {
        if (!loc || !loc.start) return;
        reader.lastCfi = loc.start.cfi || reader.lastCfi;
        if (reader.locationsReady) reader.allowSave = true;
        if (reader.allowSave) {
          setReaderPct(currentPct(book, loc));
          scheduleSave();
        }
        updateEpubBar(reader.lastCfi);
      });

      addTapZones();
      if (!state.turnHintShown) {
        state.turnHintShown = true;
        toast('点左右两侧屏幕翻页', 1800);
      }
    } catch (e) {
      toast(`这本打不开：${e.message}`, 3200);
      closeReader(false);
    }
  }

  function addTapZones() {
    const viewer = $('#viewer');
    $$('.tapzone', viewer).forEach(z => z.remove());
    const left = document.createElement('div');
    left.className = 'tapzone left';
    const right = document.createElement('div');
    right.className = 'tapzone right';
    left.onclick = () => turn(-1);
    right.onclick = () => turn(1);
    viewer.appendChild(left);
    viewer.appendChild(right);
  }

  function currentPct(book, loc) {
    if (reader.locationsReady) {
      try { return book.locations.percentageFromCfi(loc.start.cfi) * 100; } catch (_) { /* 忽略 */ }
    }
    const total = (book.spine && book.spine.length) || 0;
    const idx = (loc.start && loc.start.index) || 0;
    return total ? (idx / total) * 100 : 0;
  }

  function turn(direction) {
    if (!reader.rendition) return;
    reader.allowSave = true;
    if (direction > 0) reader.rendition.next();
    else reader.rendition.prev();
  }

  function updateEpubBar(cfi) {
    const book = reader.book;
    if (!book || !book.locations || !book.locations.length || !book.locations.length()) {
      epubBar.label.textContent = '…';
      return;
    }
    const total = book.locations.length();
    let idx = 0;
    try { idx = book.locations.locationFromCfi(cfi); } catch (_) { idx = 0; }
    const page = Math.min(total, Math.max(1, (idx || 0) + 1));
    epubBar.range.max = String(total);
    epubBar.range.value = String(page);
    epubBar.label.textContent = `${page} / ${total}`;
    if (!epubBar.bar.classList.contains('hidden')) epubBar.reset();
  }

  function setReaderPct(pct) {
    reader.lastPct = pct;
    const shown = pct > 0 && pct < 1 ? pct.toFixed(1) : String(Math.round(pct));
    $('#readerPct').textContent = shown + '%';
  }

  function applyReaderTheme() {
    if (!reader.rendition) return;
    const dark = reader.dark;
    $('#reader').style.background = dark ? '#121009' : '#f6f0e4';
    $('#viewer').style.background = dark ? '#121009' : '#f6f0e4';
    $('#readerTitle').style.color = dark ? '' : '#5c5344';
    reader.rendition.themes.override('background', dark ? '#121009' : '#f6f0e4', true);
    reader.rendition.themes.override('color', dark ? '#ded5c5' : '#2b2419', true);
    $('#themeBtn').textContent = dark ? '夜' : '日';
  }

  function scheduleSave() {
    clearTimeout(reader.saveTimer);
    reader.saveTimer = setTimeout(saveProgress, 1200);
  }

  async function saveProgress() {
    if (!reader.bookId) return;
    const body = {};
    if (reader.lastCfi) body.cfi = reader.lastCfi;
    if (reader.allowSave) {
      const pct = Math.min(100, Math.round(reader.lastPct || 0));
      body.progress = pct;
      body.status = pct >= 99 ? 'done' : 'reading';
    }
    if (!Object.keys(body).length) return;
    try {
      await patchBook(reader.bookId, body);
      const b = state.books.find(x => x.id === reader.bookId);
      if (b && reader.allowSave) { b.progress = body.progress; b.status = body.status; }
      renderSubtitle();
      loadStats();
    } catch (_) { /* 下次再存 */ }
  }

  async function closeReader(save = true) {
    if (save) { clearTimeout(reader.saveTimer); await saveProgress(); }
    reader.active = false;
    if (reader.rendition) {
      try { reader.rendition.destroy(); } catch (_) {}
      reader.rendition = null;
    }
    if (reader.book) {
      try { reader.book.destroy(); } catch (_) {}
      reader.book = null;
    }
    $('#viewer').innerHTML = '';
    $('#reader').hidden = true;
    epubBar.hide();
    document.body.style.overflow = '';
    if (reader.bookId) {
      loadBooks();
      const b = state.books.find(x => x.id === reader.bookId);
      if (b) { /* 保持列表数据 */ }
    }
  }

  // ------------------------------------------------------------ 书单

  async function loadLists() {
    try {
      const data = await api('/api/lists');
      state.lists = data.lists;
      renderLists();
      renderListBanner();
    } catch (e) {
      if (state.view === 'lists') toast(`读取书单失败：${e.message}`);
    }
  }

  function renderLists() {
    const grid = $('#listGrid');
    const empty = $('#listsEmpty');
    if (!state.lists.length) {
      grid.innerHTML = '';
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    grid.innerHTML = state.lists.map(l => {
      const coverSrc = l.cover
        ? `/api/list-cover/${l.id}?v=${l.updated_at || 0}`
        : (l.covers.length ? `/api/cover/${l.covers[0]}?v=${l.updated_at || 0}` : '');
      const cover = coverSrc
        ? `<img src="${coverSrc}" alt="" loading="lazy" onerror="this.remove()">`
        : esc(l.count ? l.count + ' 本' : '空书单');
      return `
      <article class="lcard" data-list="${l.id}">
        <div class="lcard-covers ${coverSrc ? '' : 'img0'}" style="--ph-bg:${pal(l.id)}">
          ${cover}
          <button class="lcover-btn cam" data-list-cover="${l.id}" aria-label="更换书单封面">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h3l1.4-2h7.2L17 8h3v11H4V8Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="13" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>
          </button>
          <button class="lcover-btn restore" data-list-cover-restore="${l.id}" aria-label="恢复自动封面">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10a8 8 0 1 1 2.3 6.4M4 5v5h5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>
        <div class="lcard-body">
          <div class="lcard-name">${esc(l.name)}</div>
          ${l.note ? `<div class="lcard-note">${esc(l.note)}</div>` : ''}
          <div class="lcard-meta">${l.count} 本 · 平均 ${l.avgProgress}%${l.done ? ` · 读完 ${l.done}` : ''}</div>
          <div class="pbar"><span style="width:${Math.min(100, l.avgProgress || 0)}%"></span></div>
        </div>
        <button class="lcard-edit" data-edit-list="${l.id}" aria-label="编辑书单">✎</button>
      </article>`;
    }).join('');
  }

  function openList(id) {
    const l = state.lists.find(x => x.id === id);
    if (!l) return;
    // 进书单时清掉上次的筛选，避免看到空列表
    state.filters.q = '';
    state.filters.status = 'all';
    state.filters.favorite = false;
    state.filters.format = 'all';
    state.filters.tag = '';
    $('#search').value = '';
    $$('#statusChips .chip').forEach((c, i) => c.classList.toggle('active', i === 0));
    $('#formatSel').value = 'all';
    $('#tagSel').value = '';
    state.listId = id;
    state.filters.listId = id;
    switchView('shelf');
    renderListBanner();
    loadBooks();
    window.scrollTo({ top: 0 });
  }

  function clearList() {
    state.listId = null;
    state.filters.listId = null;
    renderListBanner();
    loadBooks();
  }

  function renderListBanner() {
    const banner = $('#listBanner');
    const l = state.lists.find(x => x.id === state.listId);
    if (!l) { banner.hidden = true; return; }
    banner.hidden = false;
    $('#lbName').textContent = l.name;
    $('#lbNote').textContent = l.note || '';
    $('#lbNote').hidden = !l.note;
    $('#lbMeta').textContent = `${l.count} 本 · 平均 ${l.avgProgress}%` + (l.done ? ` · 读完 ${l.done}` : '');
    $('#lbBar').style.width = `${Math.min(100, l.avgProgress || 0)}%`;
  }

  function openTransfer() {
    $('#transferText').value = '';
    $('#transferSheet').hidden = false;
    lockBody();
  }

  function closeTransfer() {
    $('#transferSheet').hidden = true;
    lockBody();
  }

  async function exportLists() {
    try {
      const data = await api('/api/lists/export');
      $('#transferText').value = JSON.stringify(data, null, 2);
      toast('已生成，点「复制」带走');
    } catch (e) { toast(`导出失败：${e.message}`); }
  }

  async function copyTransfer() {
    const el = $('#transferText');
    if (!el.value.trim()) return toast('先导出，或先粘贴内容');
    el.select();
    try {
      await navigator.clipboard.writeText(el.value);
      toast('已复制');
    } catch (_) {
      try { document.execCommand('copy'); toast('已复制'); }
      catch (e) { toast('复制失败，请手动长按选择'); }
    }
  }

  async function importLists() {
    const raw = $('#transferText').value.trim();
    if (!raw) return toast('先粘贴书单 JSON');
    let data;
    try { data = JSON.parse(raw); } catch (e) { return toast('JSON 格式不对'); }
    try {
      const r = await api('/api/lists/import', { method: 'POST', body: JSON.stringify({ data }) });
      toast(`导入完成：新建 ${r.created}，合并 ${r.merged}，匹配 ${r.matched} 本${r.missing ? `，缺失 ${r.missing} 本` : ''}`, 4500);
      await loadLists();
      loadBooks();
    } catch (e) { toast(`导入失败：${e.message}`); }
  }

  function openListEditor(list, forBookId) {
    state.editingList = list || null;
    state.editorForBook = forBookId || null;
    $('#listSheetTitle').textContent = list ? '编辑书单' : '新建书单';
    $('#listName').value = list ? list.name : '';
    $('#listNote').value = list ? (list.note || '') : '';
    $('#listDeleteBtn').hidden = !list;
    $('#listSheet').hidden = false;
    lockBody();
    setTimeout(() => $('#listName').focus(), 120);
  }

  function closeListEditor() {
    $('#listSheet').hidden = true;
    state.editingList = null;
    state.editorForBook = null;
    lockBody();
  }

  async function saveListEditor() {
    const name = $('#listName').value.trim();
    if (!name) return toast('给书单起个名字');
    const note = $('#listNote').value.trim();
    try {
      let list;
      if (state.editingList) {
        list = (await api(`/api/lists/${state.editingList.id}`, {
          method: 'PATCH', body: JSON.stringify({ name, note }),
        })).list;
      } else {
        list = (await api('/api/lists', {
          method: 'POST', body: JSON.stringify({ name, note }),
        })).list;
      }
      if (state.editorForBook) {
        await api(`/api/lists/${list.id}/books`, {
          method: 'POST', body: JSON.stringify({ bookIds: [state.editorForBook] }),
        });
      }
      closeListEditor();
      await loadLists();
      if (state.listId === list.id) { renderListBanner(); loadBooks(); }
      if (state.detailBook) {
        const { book } = await api(`/api/book/${state.detailBook.id}`);
        Object.assign(state.detailBook, book);
        renderDetail(state.detailBook);
      }
      toast('已保存');
    } catch (e) { toast(`保存失败：${e.message}`); }
  }

  async function deleteCurrentList() {
    const l = state.editingList;
    if (!l) return;
    if (!(await uiConfirm(`删除书单「${l.name}」？\n只删书单，书本身不会动。`, '删除书单'))) return;
    try {
      await api(`/api/lists/${l.id}`, { method: 'DELETE' });
      closeListEditor();
      if (state.listId === l.id) clearList();
      await loadLists();
      toast('书单已删除');
    } catch (e) { toast(`删除失败：${e.message}`); }
  }

  async function openPicker(listId) {
    try {
      const [{ list }, { books }] = await Promise.all([
        api(`/api/lists/${listId}`),
        api('/api/books?sort=title'),
      ]);
      state.pickerListId = listId;
      state.pickerSel = new Set(list.bookIds || []);
      state.pickerBooks = books;
      $('#pickerTitle').textContent = `添加书籍到「${list.name}」`;
      $('#pickerSearch').value = '';
      renderPicker();
      $('#picker').hidden = false;
      lockBody();
    } catch (e) { toast(`打开失败：${e.message}`); }
  }

  function renderPicker() {
    const q = ($('#pickerSearch').value || '').trim().toLowerCase();
    const rows = state.pickerBooks.filter(b => !q ||
      (b.title || '').toLowerCase().includes(q) || (b.author || '').toLowerCase().includes(q));
    $('#pickerList').innerHTML = rows.length ? rows.map(b => {
      const on = state.pickerSel.has(b.id);
      return `<div class="pick-row ${on ? 'on' : ''}" data-pick="${b.id}">
        <div class="pick-thumb">${b.cover ? `<img src="/api/cover/${b.id}?v=${b.updatedAt || 0}" alt="" loading="lazy" onerror="this.remove()">` : esc((b.format || '').toUpperCase())}</div>
        <div class="pick-body">
          <div class="pick-title">${esc(b.title)}</div>
          <div class="pick-sub">${esc(b.author || '未知作者')} · ${esc((b.format || '').toUpperCase())}</div>
        </div>
        <div class="pick-check">${on ? '✓' : ''}</div>
      </div>`;
    }).join('') : '<p class="c-note">没有匹配的书</p>';
    $('#pickerCount').textContent = `已选 ${state.pickerSel.size} 本`;
  }

  async function savePicker() {
    const id = state.pickerListId;
    if (!id) return;
    try {
      await api(`/api/lists/${id}/books`, {
        method: 'POST',
        body: JSON.stringify({ bookIds: [...state.pickerSel], replace: true }),
      });
      $('#picker').hidden = true;
      lockBody();
      await loadLists();
      if (state.listId === id) { renderListBanner(); loadBooks(); }
      toast('书单已更新');
    } catch (e) { toast(`保存失败：${e.message}`); }
  }

  function openDeleteSheet(id, book) {
    const b = book || state.books.find(x => x.id === id) || { id };
    state.deleteBook = b;
    const title = b ? b.title : '这本书';
    $('#deleteTitle').textContent = `删除「${title}」？`;
    $('#deletePreview').innerHTML = `
      <div class="d-cover" style="--ph-bg:${pal(id)}">${b && b.cover
        ? `<img src="/api/cover/${id}" alt="" onerror="this.remove()">`
        : esc((b && b.format || '').toUpperCase())}</div>
      <div>
        <div class="d-title" style="font-size:.9rem">${esc(title)}</div>
        <div class="c-note">${esc((b && (b.author || b.format)) || '')}</div>
      </div>`;
    $('#deleteSheet').hidden = false;
    lockBody();
  }

  async function confirmDelete() {
    const b = state.deleteBook;
    if (!b) return;
    const btn = $('#deleteTrashBtn');
    btn.disabled = true;
    try {
      const r = await api('/api/cleanup', {
        method: 'POST', body: JSON.stringify({ action: 'trash', id: b.id }),
      });
      if (!r.ok) throw new Error(r.message || '删除失败');
      $('#deleteSheet').hidden = true;
      if (state.detailBook && state.detailBook.id === b.id) {
        $('#detail').hidden = true;
        state.detailBook = null;
      }
      state.deleteBook = null;
      lockBody();
      toast('已移入回收站');
      await refreshAll();
    } catch (e) {
      toast(`删除失败：${e.message}`);
    } finally {
      btn.disabled = false;
    }
  }

  // ------------------------------------------------------------ PDF 阅读

  function parsePdfPage(cfi) {
    const m = /^pdf:(\d+)$/.exec(cfi || '');
    return m ? parseInt(m[1], 10) : 1;
  }

  async function openPdfReader(b) {
    pdfState.book = b;
    pdfState.page = Math.min(Math.max(1, parsePdfPage(b.cfi)), 9999);
    $('#pdfReader').hidden = false;
    $('#pdfTitle').textContent = b.title;
    $('#pdfPct').textContent = '…';
    $('#pdfInfo').textContent = '…';
    pdfBar.label.textContent = '…';
    pdfBar.show();
    lockBody();
    try {
      pdfState.doc = await window.pdfjsLib.getDocument({ url: `/api/file/${b.id}` }).promise;
      pdfState.total = pdfState.doc.numPages;
      pdfState.page = Math.min(pdfState.page, pdfState.total);
      await renderPdfPage(pdfState.page);
      if (!state.turnHintShown) {
        state.turnHintShown = true;
        toast('点左右两侧屏幕翻页', 1800);
      }
    } catch (e) {
      toast(`PDF 打不开：${e.message}`, 3200);
      closePdfReader(false);
    }
  }

  async function renderPdfPage(n) {
    if (!pdfState.doc) return;
    const token = ++pdfState.renderToken;
    const page = await pdfState.doc.getPage(n);
    const view = $('#pdfView');
    const canvas = $('#pdfCanvas');
    const base = page.getViewport({ scale: 1 });
    const avail = Math.max(200, view.clientWidth - 24);
    const scale = Math.min(3, avail / base.width);
    const vp = page.getViewport({ scale });
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(vp.width * dpr);
    canvas.height = Math.floor(vp.height * dpr);
    canvas.style.width = `${Math.floor(vp.width)}px`;
    canvas.style.height = `${Math.floor(vp.height)}px`;
    const ctx = canvas.getContext('2d');
    try {
      await page.render({
        canvasContext: ctx,
        viewport: vp,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      }).promise;
    } catch (e) {
      // 快速翻页时上一页的渲染会被取消，属正常情况
      if (e && e.name === 'RenderingCancelledException') return;
      throw e;
    }
    if (token !== pdfState.renderToken) return;
    pdfState.page = n;
    $('#pdfPct').textContent = `${n} / ${pdfState.total}`;
    $('#pdfInfo').textContent = `${n} / ${pdfState.total}`;
    pdfBar.range.max = String(pdfState.total);
    pdfBar.range.value = String(n);
    pdfBar.label.textContent = `${n} / ${pdfState.total}`;
    view.scrollTop = 0;
    if (pdfState.book) {
      pdfState.book.progress = Math.round((n / pdfState.total) * 100);
      pdfState.book.cfi = `pdf:${n}`;
    }
    clearTimeout(pdfState.saveTimer);
    pdfState.saveTimer = setTimeout(savePdfProgress, 700);
  }

  async function savePdfProgress() {
    const b = pdfState.book;
    if (!b || !pdfState.total) return;
    const pct = Math.min(100, Math.round((pdfState.page / pdfState.total) * 100));
    try {
      await patchBook(b.id, {
        progress: pct,
        cfi: `pdf:${pdfState.page}`,
        status: pct >= 99 ? 'done' : 'reading',
      });
    } catch (_) { /* 忽略 */ }
  }

  function closePdfReader(save = true) {
    if (save) savePdfProgress();
    if (pdfState.doc) {
      try { pdfState.doc.destroy(); } catch (_) { /* 忽略 */ }
    }
    pdfState.doc = null;
    pdfState.book = null;
    pdfState.total = 0;
    const canvas = $('#pdfCanvas');
    canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    $('#pdfReader').hidden = true;
    pdfBar.hide();
    lockBody();
    loadBooks();
  }

  // ------------------------------------------------------------ 清理

  async function loadCleanup() {
    try {
      const [report, junkData, brokenData] = await Promise.all([
        api('/api/cleanup'),
        api('/api/books?junk=1&sort=title'),
        api('/api/books?junk=0&sort=title'),
      ]);
      renderCleanup(report, junkData.books, brokenData.books);
    } catch (e) {
      $('#cleanBody').innerHTML = `<p class="c-note">读取失败：${esc(e.message)}</p>`;
    }
  }

  function thumb(b) {
    return `<div class="c-thumb" style="--ph-bg:${pal(b.id)}">${b.cover ? `<img src="/api/cover/${b.id}?v=${b.updatedAt || 0}" alt="" loading="lazy" onerror="this.remove()">` : esc((b.format || '').toUpperCase())}</div>`;
  }

  function bookLine(b, opts = {}) {
    return `
      <div class="c-item">
        ${thumb(b)}
        <div class="c-body">
          <div class="c-title">${esc(b.title)}</div>
          <div class="c-sub">${esc(b.author || '未知作者')} · ${esc((b.format || '').toUpperCase())} · ${fmtBytes(b.size)}${opts.extra || ''}</div>
          ${opts.actions || ''}
        </div>
      </div>`;
  }

  function renderCleanup(report, junkBooks, brokenBooks) {
    const dupCount = report.duplicates.length;
    const junkCount = junkBooks.length;
    const brokenCount = brokenBooks.filter(b => b.broken).length;
    const dirtyCount = report.dirtyNames.length;

    $('#cleanSummary').innerHTML = `
      <div class="metric ${dupCount ? 'hot' : 'clean'}"><div class="n">${dupCount}</div><div class="l">重复组</div></div>
      <div class="metric ${junkCount ? 'hot' : 'clean'}"><div class="n">${junkCount}</div><div class="l">杂项文件</div></div>
      <div class="metric ${brokenCount ? 'hot' : 'clean'}"><div class="n">${brokenCount}</div><div class="l">打不开</div></div>
      <div class="metric"><div class="n">${dirtyCount}</div><div class="l">文件名可整理</div></div>
    `;

    const parts = [];

    // 损坏
    if (brokenCount) {
      parts.push(`<div class="c-block">
        <div class="c-head"><div><h2>打不开的文件</h2><p>多半是下载没下完或者加密了，留着也读不了</p></div><span class="cnt">${brokenCount}</span></div>
        ${brokenBooks.filter(b => b.broken).map(b => bookLine(b, {
          extra: ' · 文件不完整',
          actions: `<div class="c-actions">
            <button class="btn small" data-repair="${b.id}">重新扫描试试</button>
            <button class="btn small danger" data-trash="${b.id}" data-title="${esc(b.title)}">移入回收站</button>
          </div>`,
        })).join('')}
      </div>`);
    }

    // 重复
    if (dupCount) {
      parts.push(`<div class="c-block">
        <div class="c-head"><div><h2>完全重复的书</h2><p>内容一模一样，留一份就够</p></div><span class="cnt">${dupCount} 组</span></div>
        ${report.duplicates.map(g => `
          <div class="c-group">
            ${g.books.map((b, i) => bookLine(b, {
              extra: i === 0 ? ' · <span class="keep-first">建议保留</span>' : '',
              actions: i === 0 ? '' : `<div class="c-actions"><button class="btn small danger" data-trash="${b.id}" data-title="${esc(b.title)}">移入回收站</button></div>`,
            })).join('')}
          </div>`).join('')}
      </div>`);
    }

    // 同名不同文件
    if (report.sameTitle.length) {
      parts.push(`<div class="c-block">
        <div class="c-head"><div><h2>同名但文件不同</h2><p>可能是同一本书的不同版本或格式，需要你自己判断</p></div><span class="cnt">${report.sameTitle.length} 组</span></div>
        ${report.sameTitle.map(g => `
          <div class="c-group">
            ${g.books.map(b => bookLine(b, {
              actions: `<div class="c-actions"><button class="btn small danger" data-trash="${b.id}" data-title="${esc(b.title)}">移入回收站</button></div>`,
            })).join('')}
          </div>`).join('')}
      </div>`);
    }

    // 杂项
    if (junkCount) {
      parts.push(`<div class="c-block">
        <div class="c-head"><div><h2>杂项文件</h2><p>报告、表格之类的 PDF，被自动挑出来了</p></div><span class="cnt">${junkCount}</span></div>
        ${junkBooks.map(b => bookLine(b, {
          actions: `<div class="c-actions">
            <button class="btn small" data-not-junk="${b.id}">其实是一本书</button>
            <button class="btn small danger" data-trash="${b.id}" data-title="${esc(b.title)}">移入回收站</button>
          </div>`,
        })).join('')}
      </div>`);
    }

    // 文件名
    if (dirtyCount) {
      parts.push(`<div class="c-block">
        <div class="c-head"><div><h2>文件名可整理</h2><p>去掉「(Z-Library)」这类后缀，只改文件名，内容不动</p></div><span class="cnt">${dirtyCount}</span></div>
        <button class="btn wide primary" data-rename-all="${dirtyCount}" style="margin-bottom:10px">全部按建议重命名（${dirtyCount} 个）</button>
        ${report.dirtyNames.slice(0, 25).map(d => {
          const cur = (d.book.path || '').split('/').pop();
          return `<div class="c-item">
            <div class="c-thumb">${esc((d.book.format || '').toUpperCase())}</div>
            <div class="c-body">
              <div class="c-title" style="font-size:.74rem;color:var(--muted)">${esc(cur)}</div>
              <div class="c-sub" style="color:var(--good)">→ ${esc(d.newName)}</div>
              <div class="c-actions"><button class="btn small" data-rename="${d.book.id}">重命名</button></div>
            </div>
          </div>`;
        }).join('')}
        ${dirtyCount > 25 ? `<p class="c-note">只列出前 25 个，批量操作会处理全部。</p>` : ''}
      </div>`);
    }

    if (!parts.length) {
      parts.push(`<div class="empty"><strong>很干净</strong>没有重复、杂项和打不开的文件</div>`);
    }
    $('#cleanBody').innerHTML = parts.join('');
    bindCleanup();
  }

  function bindCleanup() {
    const body = $('#cleanBody');
    $$('[data-trash]', body).forEach(btn => {
      btn.onclick = async () => {
        const id = Number(btn.dataset.trash);
        const title = btn.dataset.title || '这本书';
        if (!(await uiConfirm(`把《${title}》移入回收站？\n文件会移到 /sdcard/Download/.bookcase-trash，之后可以手动找回。`, '移入回收站'))) return;
        try {
          const r = await api('/api/cleanup', { method: 'POST', body: JSON.stringify({ action: 'trash', id }) });
          toast(r.ok ? '已移入回收站' : `失败：${r.message}`);
          if (r.ok) { await refreshAll(); }
        } catch (e) { toast(`失败：${e.message}`); }
      };
    });
    const renameAll = body.querySelector('[data-rename-all]');
    if (renameAll) renameAll.onclick = async () => {
      const n = renameAll.dataset.renameAll;
      if (!(await uiConfirm(`将重命名 ${n} 个文件？\n只改文件名，书的内容不变；遇到重名会自动跳过。`, '开始重命名'))) return;
      try {
        const r = await api('/api/cleanup', { method: 'POST', body: JSON.stringify({ action: 'renameAll' }) });
        toast(r.message || '完成', 3000);
        await refreshAll();
      } catch (e) { toast(`失败：${e.message}`); }
    };
    $$('[data-rename]', body).forEach(btn => {
      btn.onclick = async () => {
        const id = Number(btn.dataset.rename);
        if (!(await uiConfirm('按建议重命名这个文件？内容不会变。', '重命名'))) return;
        try {
          const r = await api('/api/cleanup', { method: 'POST', body: JSON.stringify({ action: 'rename', id }) });
          toast(r.ok ? `已重命名：${r.message.split('/').pop()}` : `失败：${r.message}`, 3000);
          if (r.ok) { await refreshAll(); }
        } catch (e) { toast(`失败：${e.message}`); }
      };
    });
    $$('[data-not-junk]', body).forEach(btn => {
      btn.onclick = async () => {
        const id = Number(btn.dataset.notJunk);
        try {
          await patchBook(id, { junk: false });
          toast('已放回书架');
          await refreshAll();
          loadCleanup();
        } catch (e) { toast(`失败：${e.message}`); }
      };
    });
    $$('[data-repair]', body).forEach(btn => {
      btn.onclick = async () => {
        toast('重新扫描中…');
        try {
          await api('/api/scan', { method: 'POST' });
          startScanPoll();
        } catch (e) { toast(`失败：${e.message}`); }
      };
    });
  }

  // ------------------------------------------------------------ 设置

  function renderSettingsStats() {
    const s = state.stats;
    if (!s) return;
    $('#settingsStats').innerHTML = `
      <div><b>${s.total}</b>本书</div>
      <div><b>${s.reading}</b>在读</div>
      <div><b>${s.done}</b>读完</div>
      <div><b>${s.sizeHuman}</b>占用空间</div>
    `;
    const sc = s.scan;
    if (sc && sc.lastResult) {
      const r = sc.lastResult;
      $('#scanDetail').textContent = `上次扫描：新增 ${r.added}，更新 ${r.updated}，移除 ${r.removed}，共 ${r.total} 个文件，用时 ${r.seconds}s`;
    }
    if (sc && sc.running) {
      $('#scanDetail').textContent = `正在扫描 ${sc.done}/${sc.total}：${sc.current}`;
    }
    const errBox = $('#scanError');
    if (errBox) {
      if (sc && sc.error) {
        errBox.hidden = false;
        errBox.textContent = `上次扫描出错：${sc.error}`;
      } else {
        errBox.hidden = true;
      }
    }
  }

  async function loadConfig() {
    try {
      const cfg = await api('/api/config');
      state.roots = cfg.roots;
      renderRoots();
      const ver = $('#appVersion');
      if (ver && cfg.version) ver.textContent = `Bookcase v${cfg.version} · 本地服务 127.0.0.1`;
    } catch (_) {}
  }

  function renderRoots() {
    $('#rootList').innerHTML = state.roots.map(r => `
      <div class="rootrow"><span>${esc(r)}</span><button data-del-root="${esc(r)}" aria-label="移除目录">×</button></div>
    `).join('') || '<div class="c-note">还没有扫描目录</div>';
    $$('[data-del-root]').forEach(btn => {
      btn.onclick = async () => {
        const roots = state.roots.filter(r => r !== btn.dataset.delRoot);
        if (!roots.length) return toast('至少保留一个目录');
        try {
          const r = await api('/api/config', { method: 'POST', body: JSON.stringify({ roots }) });
          state.roots = r.roots;
          renderRoots();
          toast('已更新，点重新扫描生效');
        } catch (e) { toast(`失败：${e.message}`); }
      };
    });
  }

  // 扫描不到书时的自检：每个目录能读到多少本，哪些有书目录还没加进扫描范围
  async function checkRoots() {
    const box = $('#rootCheck');
    box.innerHTML = '<div class="c-note">检查中，最多几秒…</div>';
    try {
      const r = await api('/api/roots_info');
      const rows = r.roots.map(x => `
        <div class="rootrow"><span>${esc(x.path)}<br><small>${x.exists
          ? `${x.files} 个文件`
          : '目录不存在或读不到'}${x.error ? ' · ' + esc(x.error) : ''}</small></span></div>
      `).join('');
      const sug = r.suggested.length ? `
        <div class="c-note" style="margin-top:12px">这些目录里有书，还没加进扫描范围：</div>
        ${r.suggested.map(s => `<div class="rootrow"><span>${esc(s.path)}<br><small>${s.files} 个文件</small></span><button data-add-suggest="${esc(s.path)}">添加</button></div>`).join('')}
      ` : '<div class="c-note" style="margin-top:12px">没发现漏掉的有书目录。</div>';
      box.innerHTML = rows + sug;
      $$('[data-add-suggest]').forEach(b => {
        b.onclick = () => addRootPath(b.dataset.addSuggest);
      });
    } catch (e) {
      box.innerHTML = `<div class="c-note">检查失败：${esc(e.message)}</div>`;
    }
  }

  async function addRootPath(p) {
    const roots = [...state.roots];
    if (!roots.includes(p)) roots.push(p);
    try {
      const r = await api('/api/config', { method: 'POST', body: JSON.stringify({ roots }) });
      state.roots = r.roots;
      renderRoots();
      toast('已添加，点「重新扫描」开始索引');
      checkRoots();
    } catch (e) { toast(`添加失败：${e.message}`); }
  }

  // ------------------------------------------------------------ 分享 / 导入

  function humanSize(n) {
    if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' KB';
    return `${n} B`;
  }

  function nativeShare(kind, id) {
    const bridge = window.BookcaseAndroid;
    if (bridge && typeof bridge.share === 'function') {
      bridge.share(kind, String(id));
      return true;
    }
    return false;
  }

  function shareBook(b) {
    if (nativeShare('book', b.id)) return;
    window.open(`/api/file/${b.id}`, '_blank');
    toast('当前环境不支持系统分享，已打开原文件');
  }

  function shareList() {
    if (!state.listId) return;
    const l = state.lists.find(x => x.id === state.listId);
    if (!l || !l.count) return toast('书单是空的');
    if (!nativeShare('list', state.listId)) toast('系统分享只在 App 里可用');
  }

  async function findImportPacks() {
    const box = $('#importList');
    box.innerHTML = '<div class="c-note">查找中…</div>';
    try {
      const r = await api('/api/import/candidates');
      if (!r.files.length) {
        box.innerHTML = '<div class="c-note">没找到 zip。收到的文件先保存到 Download，再点一次。</div>';
        return;
      }
      box.innerHTML = r.files.map(f => `
        <div class="rootrow"><span>${esc(f.name)}<br><small>${humanSize(f.size)} · ${new Date((f.mtime || 0) * 1000).toLocaleDateString('zh-CN')}</small></span><button data-import="${esc(f.path)}">导入</button></div>
      `).join('');
      $$('[data-import]').forEach(btn => {
        btn.onclick = () => importPack(btn.dataset.import, btn);
      });
    } catch (e) {
      box.innerHTML = `<div class="c-note">查找失败：${esc(e.message)}</div>`;
    }
  }

  async function importPack(path, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '导入中…'; }
    try {
      const r = await api('/api/import', { method: 'POST', body: JSON.stringify({ path }) });
      toast(`已导入 ${r.count} 本，正在扫描…`, 2800);
      $('#importList').innerHTML = '';
      startScanPoll();
    } catch (e) {
      toast(`导入失败：${e.message}`, 3200);
      if (btn) { btn.disabled = false; btn.textContent = '导入'; }
    }
  }

  // ------------------------------------------------------------ 扫描状态

  function updateScanUI(scan) {
    const line = $('#scanline');
    if (!scan || !scan.running) {
      line.hidden = true;
      $('#scanBtn').classList.remove('spinning');
      return;
    }
    line.hidden = false;
    const pct = scan.total ? Math.round((scan.done / scan.total) * 100) : 0;
    line.style.setProperty('--p', pct + '%');
    $('#scanBtn').classList.add('spinning');
    $('#subtitle').textContent = `扫描中 ${scan.done}/${scan.total} · ${scan.current || ''}`;
  }

  function startScanPoll() {
    if (state.scanPoll) return;
    let wasRunning = true;
    state.scanPoll = setInterval(async () => {
      try {
        const scan = await api('/api/scan');
        updateScanUI(scan);
        if (scan.running) {
          wasRunning = true;
          if (scan.total) {
            $('#subtitle').textContent = `扫描中 ${scan.done}/${scan.total} · ${scan.current || ''}`;
          }
        } else {
          clearInterval(state.scanPoll);
          state.scanPoll = null;
          if (wasRunning) {
            const added = (scan.lastResult && scan.lastResult.addedBooks) || [];
            const wasCheck = state.checkMode;
            state.checkMode = false;
            await refreshAll();
            if (wasCheck) {
              if (added.length) showNewBooks(added);
              else toast('没有发现新书');
            } else {
              toast(added.length ? `扫描完成，新增 ${added.length} 本` : '扫描完成');
            }
          }
        }
      } catch (_) {
        clearInterval(state.scanPoll);
        state.scanPoll = null;
      }
    }, 1200);
  }

  async function startScan(mode = 'check') {
    if (state.scanPoll) { toast('正在检查…'); return; }
    state.checkMode = mode === 'check';
    try {
      const r = await api('/api/scan', { method: 'POST' });
      if (r.started) {
        toast(mode === 'check' ? '正在检查新书…' : '开始扫描…');
      } else {
        state.checkMode = false;
      }
      startScanPoll();
      updateScanUI(r.scan);
    } catch (e) {
      state.checkMode = false;
      toast(`检查失败：${e.message}`);
    }
  }

  function showNewBooks(books) {
    state.newBooks = books;
    $('#newTitle').textContent = `发现 ${books.length} 本新书`;
    $('#newList').innerHTML = books.map(b => `
      <div class="pick-row" data-new-book="${b.id}">
        <div class="pick-thumb">${b.cover ? `<img src="/api/cover/${b.id}?v=${b.updatedAt || 0}" alt="" loading="lazy" onerror="this.remove()">` : esc((b.format || '').toUpperCase())}</div>
        <div class="pick-body">
          <div class="pick-title">${esc(b.title)}</div>
          <div class="pick-sub">${esc(b.author || '未知作者')} · ${esc((b.format || '').toUpperCase())} · ${fmtBytes(b.size)}</div>
        </div>
      </div>`).join('');
    $('#newSheet').hidden = false;
    lockBody();
  }

  // ------------------------------------------------------------ 导航与事件

  function switchView(view) {
    state.view = view;
    ['shelf', 'lists', 'clean', 'settings'].forEach(v => {
      const el = $(`#view-${v}`);
      if (el) el.hidden = v !== view;
    });
    $$('.navbtn').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    window.scrollTo({ top: 0 });
    if (view === 'lists') loadLists();
    if (view === 'clean') loadCleanup();
    if (view === 'settings') { loadConfig(); loadStats(); }
  }

  function bindEvents() {
    // 搜索
    let searchTimer = null;
    $('#search').addEventListener('input', e => {
      clearTimeout(searchTimer);
      const v = e.target.value.trim();
      searchTimer = setTimeout(() => {
        state.filters.q = v;
        loadBooks();
      }, 240);
    });

    // 状态 chips
    $('#statusChips').addEventListener('click', e => {
      const chip = e.target.closest('.chip');
      if (!chip) return;
      const chips = $$('.chip', $('#statusChips'));
      chips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.filters.status = 'all';
      state.filters.favorite = false;
      if (chip.dataset.status) state.filters.status = chip.dataset.status;
      if (chip.dataset.favorite) state.filters.favorite = true;
      loadBooks();
    });

    $('#formatSel').onchange = e => { state.filters.format = e.target.value; loadBooks(); };
    $('#tagSel').onchange = e => { state.filters.tag = e.target.value; loadBooks(); };
    $('#sortSel').onchange = e => { state.filters.sort = e.target.value; loadBooks(); };

    // 封面点击看详情
    $('#grid').addEventListener('click', e => {
      const card = e.target.closest('.card, .f1-row');
      if (card) openDetail(Number(card.dataset.id));
    });

    // 详情关闭
    $('#detail').addEventListener('click', e => {
      if (e.target.dataset.close) closeDetail();
    });

    // 详情里的标签 / 书单操作（委托）
    $('#detailBody').addEventListener('click', async ev => {
      if (!state.detailBook) return;
      const b = state.detailBook;

      const del = ev.target.closest('[data-del-tag]');
      if (del) {
        const tags = b.tags.filter(t => t !== del.dataset.delTag);
        try {
          const { book } = await patchBook(b.id, { tags });
          Object.assign(b, book);
          renderDetail(b);
          loadTags();
          loadBooks();
        } catch (e) { toast(`保存失败：${e.message}`); }
        return;
      }

      const chip = ev.target.closest('[data-toggle-list]');
      if (chip) {
        const listId = Number(chip.dataset.toggleList);
        try {
          if ((b.listIds || []).includes(listId)) {
            await api(`/api/lists/${listId}/books/${b.id}`, { method: 'DELETE' });
          } else {
            await api(`/api/lists/${listId}/books`, { method: 'POST', body: JSON.stringify({ bookIds: [b.id] }) });
          }
          const { book } = await api(`/api/book/${b.id}`);
          Object.assign(b, book);
          await loadLists();
          renderDetail(b);
          loadBooks();
        } catch (e) { toast(`保存失败：${e.message}`); }
        return;
      }

      const newList = ev.target.closest('[data-new-list-for]');
      if (newList) openListEditor(null, b.id);
    });

    // 导航
    $$('.navbtn').forEach(btn => {
      btn.onclick = () => switchView(btn.dataset.view);
    });

    // 扫描
    $('#scanBtn').onclick = () => startScan('check');
    $('#rescanBtn').onclick = () => startScan('full');
    $('#newSheet').addEventListener('click', e => {
      if (e.target.dataset.closeNew) {
        $('#newSheet').hidden = true;
        lockBody();
      }
    });
    $('#newList').addEventListener('click', e => {
      const row = e.target.closest('[data-new-book]');
      if (!row) return;
      const id = Number(row.dataset.newBook);
      $('#newSheet').hidden = true;
      lockBody();
      openDetail(id);
    });
    $('#newSettingsBtn').onclick = () => {
      $('#newSheet').hidden = true;
      lockBody();
      switchView('settings');
    };

    // 添加目录
    $('#addRootBtn').onclick = async () => {
      const input = $('#newRoot');
      const p = (input.value || '').trim();
      if (!p) return;
      const roots = [...state.roots, p];
      try {
        const r = await api('/api/config', { method: 'POST', body: JSON.stringify({ roots }) });
        state.roots = r.roots;
        input.value = '';
        renderRoots();
        toast('已添加，点重新扫描开始索引');
      } catch (e) { toast(`添加失败：${e.message}`); }
    };

    // 目录检查
    const checkRootsBtn = $('#checkRootsBtn');
    if (checkRootsBtn) checkRootsBtn.onclick = () => checkRoots();
    const findImportBtn = $('#findImportBtn');
    if (findImportBtn) findImportBtn.onclick = () => findImportPacks();


    // 书单
    $('#newListBtn').onclick = () => openListEditor(null);
    $('#listGrid').addEventListener('click', async e => {
      const cam = e.target.closest('[data-list-cover]');
      if (cam) {
        e.stopPropagation();
        $('#coverInput').dataset.kind = 'list';
        $('#coverInput').dataset.listId = cam.dataset.listCover;
        $('#coverInput').value = '';
        $('#coverInput').click();
        return;
      }
      const rst = e.target.closest('[data-list-cover-restore]');
      if (rst) {
        e.stopPropagation();
        const lid = Number(rst.dataset.listCoverRestore);
        if (!(await uiConfirm('恢复书单的自动封面？', '恢复'))) return;
        try {
          await api(`/api/lists/${lid}/cover/restore`, { method: 'POST' });
          await loadLists();
          toast('已恢复自动封面');
        } catch (err) { toast(`恢复失败：${err.message}`); }
        return;
      }
      const edit = e.target.closest('[data-edit-list]');
      if (edit) {
        const l = state.lists.find(x => x.id === Number(edit.dataset.editList));
        if (l) openListEditor(l);
        return;
      }
      const card = e.target.closest('[data-list]');
      if (card) openList(Number(card.dataset.list));
    });
    $('#lbEdit').onclick = () => {
      const l = state.lists.find(x => x.id === state.listId);
      if (l) openListEditor(l);
    };
    $('#lbClose').onclick = clearList;
    $('#listTransferBtn').onclick = openTransfer;
    $('#transferExport').onclick = exportLists;
    $('#transferCopy').onclick = copyTransfer;
    $('#transferImport').onclick = importLists;
    $('#transferSheet').addEventListener('click', e => {
      if (e.target.dataset.closeTransfer) closeTransfer();
    });
    $('#lbAdd').onclick = () => { if (state.listId) openPicker(state.listId); };
    const lbShare = $('#lbShare');
    if (lbShare) lbShare.onclick = () => shareList();
    $('#listSheet').addEventListener('click', e => { if (e.target.dataset.closeList) closeListEditor(); });
    $('#listSaveBtn').onclick = saveListEditor;
    $('#listDeleteBtn').onclick = deleteCurrentList;
    $('#picker').addEventListener('click', e => {
      if (e.target.dataset.closePicker) { $('#picker').hidden = true; lockBody(); }
    });
    $('#pickerSave').onclick = savePicker;
    $('#pickerSearch').addEventListener('input', renderPicker);
    $('#pickerList').addEventListener('click', e => {
      const row = e.target.closest('[data-pick]');
      if (!row) return;
      const id = Number(row.dataset.pick);
      if (state.pickerSel.has(id)) state.pickerSel.delete(id);
      else state.pickerSel.add(id);
      renderPicker();
    });

    $('#deleteSheet').addEventListener('click', e => {
      if (e.target.dataset.closeDelete) {
        $('#deleteSheet').hidden = true;
        state.deleteBook = null;
        lockBody();
      }
    });
    $('#deleteTrashBtn').onclick = confirmDelete;

    // 自定义确认框
    $('#confirmOk').onclick = () => closeConfirm(true);
    $('#confirmSheet').addEventListener('click', e => {
      if (e.target.dataset.confirmCancel) closeConfirm(false);
    });

    // 换封面文件选择
    $('#coverInput').addEventListener('change', async e => {
      const file = e.target.files && e.target.files[0];
      const ds = e.target.dataset;
      const kind = ds.kind;
      const bookId = Number(ds.bookId || 0);
      const listId = Number(ds.listId || 0);
      e.target.value = '';
      if (!file) return;
      try {
        if (kind === 'list' && listId) await uploadListCover(file, listId);
        else if (bookId) await uploadCover(file, bookId);
      } catch (err) {
        toast(`封面更新失败：${err.message}`);
      }
    });

    // 阅读进度条拖动
    epubBar.range.addEventListener('change', () => {
      const book = reader.book;
      if (!book || !reader.locationsReady) return;
      const idx = Math.max(0, Math.min(book.locations.length() - 1, parseInt(epubBar.range.value, 10) - 1));
      try {
        const cfi = book.locations.cfiFromLocation(idx);
        if (cfi && reader.rendition) reader.rendition.display(cfi);
      } catch (_) { /* 忽略 */ }
      epubBar.reset();
    });
    epubBar.range.addEventListener('input', () => {
      if (reader.book && reader.locationsReady) {
        epubBar.label.textContent = `${epubBar.range.value} / ${reader.book.locations.length()}`;
      }
    });
    pdfBar.range.addEventListener('change', () => {
      const n = parseInt(pdfBar.range.value, 10);
      if (n >= 1 && n <= pdfState.total) renderPdfPage(n);
      pdfBar.reset();
    });
    pdfBar.range.addEventListener('input', () => {
      if (pdfState.total) pdfBar.label.textContent = `${pdfBar.range.value} / ${pdfState.total}`;
    });

    // PDF 阅读器：点左右翻页 + 横滑翻页
    $('#pdfView').addEventListener('click', e => {
      if (pdfState.suppressClick) { pdfState.suppressClick = false; return; }
      if ($('#pdfReader').hidden || !pdfState.total) return;
      const rect = $('#pdfView').getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width;
      if (x < 0.32 && pdfState.page > 1) renderPdfPage(pdfState.page - 1);
      else if (x > 0.68 && pdfState.page < pdfState.total) renderPdfPage(pdfState.page + 1);
    });
    $('#pdfView').addEventListener('pointerdown', e => {
      pdfState.swipe = { x: e.clientX, y: e.clientY };
    });
    const endSwipe = e => {
      const s = pdfState.swipe;
      pdfState.swipe = null;
      if (!s || $('#pdfReader').hidden) return;
      const dx = e.clientX - s.x;
      const dy = e.clientY - s.y;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
      pdfState.suppressClick = true;
      setTimeout(() => { pdfState.suppressClick = false; }, 350);
      if (dx < 0 && pdfState.page < pdfState.total) renderPdfPage(pdfState.page + 1);
      if (dx > 0 && pdfState.page > 1) renderPdfPage(pdfState.page - 1);
    };
    $('#pdfView').addEventListener('pointerup', endSwipe);
    $('#pdfView').addEventListener('pointercancel', () => { pdfState.swipe = null; });

    $('#pdfBack').onclick = () => closePdfReader(true);
    $('#pdfClose').onclick = () => closePdfReader(true);
    $('#pdfPrev').onclick = () => { if (pdfState.page > 1) renderPdfPage(pdfState.page - 1); };
    $('#pdfNext').onclick = () => { if (pdfState.page < pdfState.total) renderPdfPage(pdfState.page + 1); };
    let pdfResizeTimer = null;
    window.addEventListener('resize', () => {
      if ($('#pdfReader').hidden) return;
      clearTimeout(pdfResizeTimer);
      pdfResizeTimer = setTimeout(() => renderPdfPage(pdfState.page), 250);
    });

    // 阅读器
    $('#readerBack').onclick = () => closeReader(true);
    $('#prevPage').onclick = () => turn(-1);
    $('#nextPage').onclick = () => turn(1);
    $('#fontMinus').onclick = () => {
      reader.fontSize = Math.max(70, reader.fontSize - 10);
      if (reader.rendition) reader.rendition.themes.fontSize(reader.fontSize + '%');
    };
    $('#fontPlus').onclick = () => {
      reader.fontSize = Math.min(200, reader.fontSize + 10);
      if (reader.rendition) reader.rendition.themes.fontSize(reader.fontSize + '%');
    };
    $('#themeBtn').onclick = () => {
      reader.dark = !reader.dark;
      applyReaderTheme();
    };

    document.addEventListener('keydown', e => {
      if (!$('#pdfReader').hidden) {
        if (e.key === 'ArrowLeft' && pdfState.page > 1) renderPdfPage(pdfState.page - 1);
        if (e.key === 'ArrowRight' && pdfState.page < pdfState.total) renderPdfPage(pdfState.page + 1);
        if (e.key === 'Escape') closePdfReader(true);
        return;
      }
      if (!reader.active) return;
      if (e.key === 'ArrowLeft') turn(-1);
      if (e.key === 'ArrowRight') turn(1);
      if (e.key === 'Escape') closeReader(true);
    });

    window.addEventListener('beforeunload', () => {
      if (reader.active) saveProgress();
    });
  }

  // ------------------------------------------------------------ 启动

  async function init() {
    applyTheme(document.documentElement.dataset.theme || 'light');
    $$('[data-theme-btn]').forEach(btn => {
      btn.onclick = () => applyTheme(btn.dataset.themeBtn);
    });
    const themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
      themeToggle.onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    }
    if (window.pdfjsLib) {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.min.js';
    }
    bindEvents();
    try {
      await loadStats();
      await Promise.all([loadBooks(), loadTags(), loadConfig(), loadLists()]);
      const scan = state.stats && state.stats.scan;
      if (scan && scan.running) {
        startScanPoll();
      } else if (!state.stats || state.stats.total === 0) {
        // 空库自动扫一次
        startScan('full');
      }
    } catch (e) {
      toast(`连接服务失败：${e.message}`, 4000);
    }
    setInterval(() => { if (!state.scanPoll && state.view !== 'clean') loadStats().catch(() => {}); }, 30000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();