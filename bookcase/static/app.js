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
    cloud: null,
    lastScanFinished: 0,
    upload: { items: [], running: false, xhr: null, abort: false },
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

  function lockBody() {
    const open = !$('#detail').hidden || !$('#listSheet').hidden || !$('#picker').hidden ||
      !$('#uploadSheet').hidden || reader.active;
    document.body.style.overflow = open ? 'hidden' : '';
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
    if (res.status === 401) {
      showLogin();
      const err = new Error((data && data.error) || '需要访问口令');
      err.status = 401;
      throw err;
    }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  // ------------------------------------------------------------ 登录

  function showLogin() {
    if (!$('#loginOverlay').hidden) return;
    $('#loginOverlay').hidden = false;
    $('#loginErr').textContent = '';
    setTimeout(() => $('#loginInput').focus(), 80);
  }

  async function submitLogin() {
    const token = $('#loginInput').value.trim();
    if (!token) return;
    $('#loginErr').textContent = '';
    $('#loginSubmit').disabled = true;
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || '口令不对');
      $('#loginOverlay').hidden = true;
      $('#loginInput').value = '';
      await refreshAll();
      toast('已登录');
    } catch (e) {
      $('#loginErr').textContent = e.message || '登录失败';
    } finally {
      $('#loginSubmit').disabled = false;
    }
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
    // 后台自动扫描完成后，书架跟着刷新
    const sc = state.stats.scan;
    const finished = sc && sc.lastResult && sc.lastResult.finishedAt;
    if (finished) {
      if (state.lastScanFinished && finished !== state.lastScanFinished) {
        loadBooks();
        loadTags();
        loadLists();
      }
      state.lastScanFinished = finished;
    }
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

  // ------------------------------------------------------------ 主题

  const THEME_KEY = 'bookcase-theme';

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#0d1420' : '#e7ebf0');
    try { localStorage.setItem(THEME_KEY, theme); } catch (_) { /* 忽略 */ }
  }

  function currentTheme() {
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  }

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
          : '<strong>书架还是空的</strong>点右上角 ＋ 从手机上传电子书<br>也可以点刷新，扫描服务器上已有的书库目录';
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
        ? `<img src="/api/cover/${b.id}" alt="" loading="lazy" onerror="this.remove()">`
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
      ? `<img src="/api/cover/${b.id}" alt="" onerror="this.remove()">`
      : esc(b.title);

    $('#detailBody').innerHTML = `
      <div class="d-head">
        <div class="d-cover" style="--ph-bg:${pal(b.id)}">${coverInner}</div>
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
        <a class="btn" href="/api/file/${b.id}?dl=1" download>下载到本机</a>
        <button class="btn" data-act="fav">${b.favorite ? '♥ 已收藏' : '♡ 收藏'}</button>
        <button class="btn ghost" data-act="browser">在浏览器中打开原文件</button>
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
  }

  // ------------------------------------------------------------ 阅读

  function openBook(b) {
    if (b.format === 'epub' && window.ePub) {
      openEpub(b);
    } else {
      window.open(`/api/file/${b.id}`, '_blank');
      toast('已交给浏览器打开（PDF 等格式无法内嵌阅读）', 2800);
    }
  }

  async function openEpub(b) {
    if (reader.active) closeReader(false);
    $('#reader').hidden = false;
    document.body.style.overflow = 'hidden';
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
      });

      addTapZones();
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
    reader.rendition.themes.override('background', dark ? '#0d1420' : '#fbfcfe', true);
    reader.rendition.themes.override('color', dark ? '#e7edf5' : '#0f1726', true);
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
      const cover = l.covers.length
        ? `<img src="/api/cover/${l.covers[0]}" alt="" loading="lazy" onerror="this.remove()">`
        : esc(l.count ? l.count + ' 本' : '空书单');
      return `
      <article class="lcard" data-list="${l.id}">
        <div class="lcard-covers ${l.covers.length ? '' : 'img0'}" style="--ph-bg:${pal(l.id)}">
          ${cover}
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
    const dl = $('#lbDownload');
    if (l.count) {
      dl.hidden = false;
      dl.href = `/api/lists/${l.id}/download`;
      dl.textContent = `下载书单（${l.count}）`;
    } else {
      dl.hidden = true;
      dl.removeAttribute('href');
    }
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
    if (!confirm(`删除书单「${l.name}」？\n只删书单，书本身不会动。`)) return;
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
        <div class="pick-thumb">${b.cover ? `<img src="/api/cover/${b.id}" alt="" loading="lazy" onerror="this.remove()">` : esc((b.format || '').toUpperCase())}</div>
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
    return `<div class="c-thumb" style="--ph-bg:${pal(b.id)}">${b.cover ? `<img src="/api/cover/${b.id}" alt="" loading="lazy" onerror="this.remove()">` : esc((b.format || '').toUpperCase())}</div>`;
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
        const trashDir = (state.cloud && state.cloud.trashDir) || '回收站';
        if (!confirm(`把《${title}》移入回收站？\n文件会移到 ${trashDir}，之后可以手动找回。`)) return;
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
      if (!confirm(`将重命名 ${n} 个文件？\n只改文件名，书的内容不变；遇到重名会自动跳过。`)) return;
      try {
        const r = await api('/api/cleanup', { method: 'POST', body: JSON.stringify({ action: 'renameAll' }) });
        toast(r.message || '完成', 3000);
        await refreshAll();
      } catch (e) { toast(`失败：${e.message}`); }
    };
    $$('[data-rename]', body).forEach(btn => {
      btn.onclick = async () => {
        const id = Number(btn.dataset.rename);
        if (!confirm('按建议重命名这个文件？内容不会变。')) return;
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
  }

  async function loadConfig() {
    try {
      const cfg = await api('/api/config');
      state.roots = cfg.roots;
      state.cloud = cfg;
      renderRoots();
      renderCloudInfo();
      const vt = $('#versionText');
      if (vt) vt.textContent = `v${cfg.version} · ${cfg.authRequired ? '已设访问口令' : '未设口令'}`;
    } catch (_) {}
  }

  function renderCloudInfo() {
    const c = state.cloud;
    const el = $('#cloudInfo');
    if (!c) {
      el.innerHTML = '<p class="c-note" style="margin:0">读取失败</p>';
      return;
    }
    const missing = (c.missingRoots || []).filter(Boolean);
    el.innerHTML = `
      <div class="cloudrow"><span>手机访问</span><b>${esc(location.origin)}</b></div>
      <div class="cloudrow"><span>访问口令</span><b class="${c.authRequired ? 'ok' : 'warn'}">${c.authRequired ? '已开启' : '未设置'}</b></div>
      <div class="cloudrow"><span>上传落点</span><b>${esc(c.uploadDir)}</b></div>
      <div class="cloudrow"><span>扫描目录</span><b>${c.roots.map(r => esc(r)).join('<br>')}</b></div>
      ${missing.length ? `<div class="cloudrow"><span>目录状态</span><b class="warn">${missing.map(r => esc(r)).join('<br>')}<br>暂时不可用，检查网络盘是否已连接</b></div>` : ''}
      <div class="cloudrow"><span>单文件上限</span><b>${c.maxUploadMB} MB</b></div>
      <div class="cloudrow"><span>自动扫描</span><b>${c.autoScanMinutes ? `每 ${Number(c.autoScanMinutes)} 分钟` : '关闭'}</b></div>
    `;
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

  // ------------------------------------------------------------ 上传备份

  const BOOK_EXTS = ['epub', 'pdf', 'mobi', 'azw3', 'azw', 'fb2', 'cbz', 'djvu'];

  function upStateText(item) {
    if (item.status === 'up') return (item.pct || 0) + '%';
    return { wait: '等待', done: '已备份', skip: '云端已有', error: '失败' }[item.status] || '';
  }

  function updateUpSummary(msg) {
    const el = $('#upSummary');
    if (msg) {
      el.textContent = msg;
      return;
    }
    const items = state.upload.items;
    if (!items.length) {
      el.textContent = '';
      return;
    }
    const count = s => items.filter(i => i.status === s).length;
    const bits = [`共 ${items.length} 个`];
    if (count('up')) bits.push(`上传中 ${count('up')}`);
    if (count('wait')) bits.push(`等待 ${count('wait')}`);
    if (count('done')) bits.push(`成功 ${count('done')}`);
    if (count('skip')) bits.push(`跳过 ${count('skip')}`);
    if (count('error')) bits.push(`失败 ${count('error')}`);
    el.textContent = bits.join(' · ');
  }

  function renderUploadList() {
    const list = $('#upList');
    if (!state.upload.items.length) {
      list.innerHTML = '<div class="up-empty">还没有选择文件</div>';
      return;
    }
    list.innerHTML = state.upload.items.map((it, i) => `
      <div class="up-item" data-up="${i}">
        <div class="up-line">
          <span class="up-name">${esc(it.file.name)}</span>
          <span class="up-size">${fmtBytes(it.file.size)}</span>
          <span class="up-state ${it.status}">${upStateText(it)}</span>
        </div>
        <div class="up-track"><i style="width:${it.pct || 0}%"></i></div>
      </div>`).join('');
  }

  function updateItemRow(item) {
    const idx = state.upload.items.indexOf(item);
    const row = $(`[data-up="${idx}"]`);
    if (!row) return;
    const st = row.querySelector('.up-state');
    st.className = 'up-state ' + item.status;
    st.textContent = upStateText(item);
    const bar = row.querySelector('.up-track i');
    if (bar) bar.style.width = (item.pct || 0) + '%';
  }

  function openUpload() {
    if (!state.upload.running) {
      state.upload.items = [];
      state.upload.abort = false;
      renderUploadList();
      updateUpSummary();
    }
    $('#uploadSheet').hidden = false;
    lockBody();
  }

  function closeUpload() {
    if (state.upload.running) {
      if (!confirm('还有文件在上传，关闭会中断，确定吗？')) return;
      cancelUpload(true);
    }
    $('#uploadSheet').hidden = true;
    lockBody();
    if (state.upload.items.some(i => i.status === 'done')) refreshAll();
  }

  function addFiles(fileList) {
    const files = Array.from(fileList || []);
    let ignored = 0;
    for (const f of files) {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      if (!BOOK_EXTS.includes(ext)) {
        ignored += 1;
        continue;
      }
      const same = state.upload.items.some(it =>
        it.file.name === f.name && it.file.size === f.size);
      if (same) continue;
      state.upload.items.push({ file: f, status: 'wait', pct: 0 });
    }
    renderUploadList();
    updateUpSummary();
    if (ignored) toast(`已忽略 ${ignored} 个非电子书文件`, 2600);
    if (!state.upload.running && state.upload.items.some(i => i.status === 'wait')) {
      startUpload();
    }
  }

  async function startUpload() {
    const pending = state.upload.items.filter(i => i.status === 'wait');
    if (!pending.length) return;
    state.upload.running = true;
    state.upload.abort = false;
    $('#upCancel').hidden = false;
    updateUpSummary('正在检查云端已有…');

    let missing = new Set();
    try {
      const r = await api('/api/upload/check', {
        method: 'POST',
        body: JSON.stringify({
          files: pending.map(i => ({ name: i.file.name, size: i.file.size })),
        }),
      });
      missing = new Set(r.missing || []);
    } catch (e) {
      if (e.status === 401) {
        state.upload.running = false;
        $('#upCancel').hidden = true;
        return;
      }
      // 预检失败不影响上传；服务端还会按内容哈希去重
    }

    for (const item of pending) {
      if (state.upload.abort) break;
      if (!missing.has(item.file.name)) {
        item.status = 'skip';
        item.pct = 100;
        renderUploadList();
        updateUpSummary();
        continue;
      }
      try {
        const result = await uploadOne(item);
        item.pct = 100;
        if (result.duplicate) item.status = 'skip';
        else item.status = 'done';
      } catch (e) {
        if (e.aborted) {
          item.status = 'wait';
          item.pct = 0;
        } else if (e.status === 401) {
          item.status = 'error';
          renderUploadList();
          updateUpSummary();
          break;
        } else {
          item.status = 'error';
          item.msg = e.message;
        }
      }
      renderUploadList();
      updateUpSummary();
    }

    state.upload.running = false;
    state.upload.xhr = null;
    $('#upCancel').hidden = true;
    updateUpSummary();
    await refreshAll();
    const done = state.upload.items.filter(i => i.status === 'done').length;
    const failed = state.upload.items.filter(i => i.status === 'error').length;
    if (done || failed) toast(`备份结束：成功 ${done} 本${failed ? `，失败 ${failed} 本` : ''}`, 3200);
  }

  function uploadOne(item) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      state.upload.xhr = xhr;
      item.status = 'up';
      item.pct = 0;
      renderUploadList();
      updateUpSummary();

      xhr.open('POST', '/api/upload?name=' + encodeURIComponent(item.file.name));
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = ev => {
        if (!ev.lengthComputable) return;
        item.pct = Math.min(100, Math.round((ev.loaded / ev.total) * 100));
        updateItemRow(item);
        updateUpSummary();
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* 忽略 */ }
        if (xhr.status === 401) {
          showLogin();
          reject(Object.assign(new Error('需要访问口令'), { status: 401 }));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
          resolve(data);
          return;
        }
        reject(new Error((data && data.error) || `HTTP ${xhr.status}`));
      };
      xhr.onerror = () => reject(new Error('网络中断'));
      xhr.onabort = () => reject(Object.assign(new Error('已取消'), { aborted: true }));
      xhr.send(item.file);
    });
  }

  function cancelUpload(silent) {
    state.upload.abort = true;
    if (state.upload.xhr) {
      try { state.upload.xhr.abort(); } catch (_) { /* 忽略 */ }
    }
    if (!silent) toast('已取消，已经传完的书保留');
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
            toast('扫描完成');
            await refreshAll();
            loadCleanup();
          }
        }
      } catch (_) {
        clearInterval(state.scanPoll);
        state.scanPoll = null;
      }
    }, 1200);
  }

  async function startScan() {
    try {
      const r = await api('/api/scan', { method: 'POST' });
      if (r.started) toast('开始扫描…');
      startScanPoll();
      updateScanUI(r.scan);
    } catch (e) { toast(`扫描失败：${e.message}`); }
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
    const themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
      themeToggle.onclick = () => applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    }
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

    // 封面点击
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
    $('#scanBtn').onclick = startScan;
    $('#rescanBtn').onclick = startScan;

    // 整库导出
    $('#backupBtn').onclick = () => {
      const s = state.stats;
      const n = s ? s.total : 0;
      if (!n) return toast('书架还是空的');
      if (!confirm(`导出全部 ${n} 本书（约 ${s.sizeHuman}）？\n会边打包边下载，书多时需要等一会儿。`)) return;
      window.location.href = '/api/backup';
    };

    // 上传 / 备份
    $('#uploadBtn').onclick = openUpload;
    $('#uploadSheet').addEventListener('click', e => {
      if (e.target.dataset.closeUpload) closeUpload();
    });
    $('#pickFiles').addEventListener('change', e => {
      addFiles(e.target.files);
      e.target.value = '';
    });
    $('#pickFolder').addEventListener('change', e => {
      addFiles(e.target.files);
      e.target.value = '';
    });
    const drop = $('#upDrop');
    ['dragenter', 'dragover'].forEach(type => drop.addEventListener(type, e => {
      e.preventDefault();
      drop.classList.add('dragover');
    }));
    ['dragleave', 'drop'].forEach(type => drop.addEventListener(type, e => {
      e.preventDefault();
      drop.classList.remove('dragover');
    }));
    drop.addEventListener('drop', e => {
      if (e.dataTransfer) addFiles(e.dataTransfer.files);
    });
    $('#upCancel').onclick = () => cancelUpload(false);
    $('#upDone').onclick = closeUpload;

    // 口令弹层
    $('#loginSubmit').onclick = submitLogin;
    $('#loginInput').addEventListener('keydown', e => {
      if (e.key === 'Enter') submitLogin();
    });

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


    // 书单
    $('#newListBtn').onclick = () => openListEditor(null);
    $('#listGrid').addEventListener('click', e => {
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
    $('#lbAdd').onclick = () => { if (state.listId) openPicker(state.listId); };
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
      if (!reader.active) return;
      if (e.key === 'ArrowLeft') turn(-1);
      if (e.key === 'ArrowRight') turn(1);
      if (e.key === 'Escape') closeReader(true);
    });

    window.addEventListener('beforeunload', e => {
      if (reader.active) saveProgress();
      if (state.upload.running) {
        e.preventDefault();
        e.returnValue = '';
      }
    });
  }

  // ------------------------------------------------------------ 启动

  async function init() {
    applyTheme(document.documentElement.dataset.theme || 'light');
    bindEvents();
    try {
      const session = await api('/api/session');
      if (session.authRequired && !session.authed) {
        showLogin();
        return;
      }
    } catch (_) { /* 老版本服务没有 session 接口，继续 */ }
    try {
      await loadStats();
      await Promise.all([loadBooks(), loadTags(), loadConfig(), loadLists()]);
      const scan = state.stats && state.stats.scan;
      if (scan && scan.running) {
        startScanPoll();
      } else if (!state.stats || state.stats.total === 0) {
        // 空库自动扫一次
        startScan();
      }
    } catch (e) {
      toast(`连接服务失败：${e.message}`, 4000);
    }
    setInterval(() => {
      if (!state.scanPoll && state.view !== 'clean' && $('#loginOverlay').hidden) {
        loadStats().catch(() => {});
      }
    }, 30000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
