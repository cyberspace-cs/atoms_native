/* 一次性构建脚本：把 guizang deck (index.html) 转成 frontend-slides-editable 可编辑版
 * 产物: docs/decks/atoms-native-intro/index-editable.html
 */
const fs = require('fs');
const path = require('path');

const DECK = 'd:/download/project/TX-budddy/Atoms_Native/docs/decks/atoms-native-intro/index.html';
const OUT = 'd:/download/project/TX-budddy/Atoms_Native/docs/decks/atoms-native-intro/index-editable.html';
const BROAD = 'd:/download/project/TX-budddy/Atoms_Native/.trae/skills/frontend-slides-editable/examples/generated/presets/broadside.html';

function must(cond, msg) {
  if (!cond) throw new Error('BUILD FAILED: ' + msg);
}
function replaceOnce(hay, needle, repl, label) {
  const i = hay.indexOf(needle);
  must(i >= 0, 'anchor not found: ' + label);
  must(hay.indexOf(needle, i + 1) < 0, 'anchor not unique: ' + label);
  return hay.slice(0, i) + repl + hay.slice(i + needle.length);
}

let deck = fs.readFileSync(DECK, 'utf8').replace(/\r\n/g, '\n');
let broad = fs.readFileSync(BROAD, 'utf8').replace(/\r\n/g, '\n');

/* ---------- 1. 从 broadside 抽取三块资产 ---------- */
const CHROME_START = '<div class="deck-left-hover-anchor"';
const CHROME_END = '<input type="file" id="slotImageInput" accept="image/*" hidden data-deck-chrome-surface="">';
const ci = broad.indexOf(CHROME_START);
const ce = broad.indexOf(CHROME_END);
must(ci >= 0 && ce > ci, 'extract chrome block');
const chromeBlock = broad.slice(ci, ce + CHROME_END.length);

const CSS_OPEN = '<style id="swiss-edit-runtime-css">';
const cs = broad.indexOf(CSS_OPEN);
must(cs >= 0, 'extract css block start');
const csEnd = broad.indexOf('</style>', cs);
must(csEnd > cs, 'extract css block end');
let cssBlock = broad.slice(cs + CSS_OPEN.length, csEnd);

const JS_OPEN = '<script id="swiss-slot-edit-runtime-js">';
const jsI = broad.indexOf(JS_OPEN);
must(jsI >= 0, 'extract js block start');
const jsEnd = broad.indexOf('</script>', jsI);
must(jsEnd > jsI, 'extract js block end');
let jsBlock = broad.slice(jsI + JS_OPEN.length, jsEnd);

/* ---------- 2. 改造运行时 JS ---------- */
// J1. SlideDeck：滚动导航 → 原生 transform 翻页适配器
const ADAPTER = `
  /* ---------- Slide deck（editable: 对接原生 guizang transform 横向翻页） ---------- */
  class SlideDeck {
    constructor() {
      this.refreshSlides();
      this.current = 0;
      this.onSlideChange = null;
      /* 原生 go() 每次翻页都会派发 deck:navigate，这里据此同步编辑器状态 */
      window.addEventListener('deck:navigate', (e) => {
        const i = Math.max(0, Number(e.detail && e.detail.index) || 0);
        this.refreshSlides();
        this.current = Math.min(i, Math.max(0, this.slides.length - 1));
        this.slides.forEach((s, k) => s.classList.toggle('is-active', k === this.current));
        this.onSlideChange && this.onSlideChange(this.current);
        this._updateChrome();
      });
    }
    refreshSlides() {
      const root = deckRoot();
      this.slides = root ? Array.from(root.querySelectorAll(':scope > section.slide')) : [];
    }
    goTo(i) {
      this.refreshSlides();
      i = Math.max(0, Math.min(this.slides.length - 1, i));
      this.current = i;
      if (typeof go === 'function') go(i); /* 原生翻页：transform + 动效 + 演讲者同步 */
      this._updateChrome();
      this.onSlideChange && this.onSlideChange(i);
    }
    _syncCurrentFromScroll() {
      /* 兼容 applyDeckState 的调用点：transform 翻页下直接读原生索引 */
      this.refreshSlides();
      const i = Math.max(0, Math.min(this.slides.length - 1, Number(window.__currentSlideIndex) || 0));
      this.current = i;
    }
    _updateChrome() { /* 原生 deck 自带圆点导航，编辑器无需额外进度条 */ }
  }
`;
const SD_START = '  /* ---------- Slide deck (scroll / nav) ---------- */';
const SD_END = '  function ensureResizeHandles(obj) {';
const sa = jsBlock.indexOf(SD_START);
const sb = jsBlock.indexOf(SD_END);
must(sa >= 0 && sb > sa, 'replace SlideDeck class');
jsBlock = jsBlock.slice(0, sa) + ADAPTER + jsBlock.slice(sb);

// J2. 序列化时重置入场动画内联样式
jsBlock = replaceOnce(jsBlock,
`  function serializeSlidesRoot(root) {
    const clone = root.cloneNode(true);
    sanitizeEditableState(clone);
    return clone.innerHTML;
  }`,
`  function serializeSlidesRoot(root) {
    const clone = root.cloneNode(true);
    sanitizeEditableState(clone);
    /* editable: 重置入场动画内联样式，避免把 opacity:0 存进快照 */
    clone.querySelectorAll('[data-anim]').forEach((el) => {
      el.style.opacity = '';
      el.style.transform = '';
    });
    return clone.innerHTML;
  }`, 'serializeSlidesRoot patch');

// J3. 自动槽位标记 + 恢复存档
jsBlock = replaceOnce(jsBlock, '  loadState();',
`  /* ---------- editable: 自动把模板文本标记为可编辑槽位（锁定布局） ---------- */
  function autoMarkSlots() {
    const root = deckRoot();
    if (!root) return;
    const SEL = 'h1,h2,h3,h4,h5,h6,p,li,blockquote,figcaption,td,th,dt,dd,pre,' +
      '.kicker,.lead,.stat-label,.stat-note,.stat-nb,.chrome>div,.foot>div,.meta-row,.eyebrow,.sub,.note,.label';
    Array.from(root.querySelectorAll(':scope > section.slide')).forEach((slide, si) => {
      if (slide.dataset.slotsMarked) return;
      let n = 0;
      Array.from(slide.querySelectorAll(SEL)).forEach((el) => {
        if (el.closest('[data-edit-slot]')) return;      /* 外层已覆盖 */
        if (el.closest('[data-slide-object]')) return;   /* 自由对象内部不重复标 */
        if (!el.textContent || !el.textContent.trim()) return;
        el.setAttribute('data-edit-slot', 's' + si + '-slot-' + (n++));
        el.setAttribute('data-slot-type', 'text');
        el.setAttribute('data-slot-locked-layout', 'true');
        el.setAttribute('data-slot-label', el.textContent.trim().slice(0, 40));
      });
      slide.dataset.slotsMarked = '1';
    });
  }
  autoMarkSlots();

  loadState();`, 'autoMarkSlots insert');

// J4. 键盘路由：编辑器先注册，拦截原生 deck 的翻页按键
jsBlock = replaceOnce(jsBlock,
`  document.addEventListener('keydown', (e) => {
    const ce = e.target.closest && e.target.closest('.slide-object-text[contenteditable="true"], [data-edit-slot][contenteditable="true"]');`,
`  document.addEventListener('keydown', (e) => {
    const ce = e.target.closest && e.target.closest('.slide-object-text[contenteditable="true"], [data-edit-slot][contenteditable="true"]');
    /* editable: 编辑器脚本先注册，先收到事件 —— 在这里拦截原生 deck 的按键 */
    const _tag = ((e.target && e.target.tagName) || '').toLowerCase();
    const _inField = _tag === 'input' || _tag === 'textarea' || _tag === 'select' || !!(e.target && e.target.isContentEditable);
    const _editing = document.body.classList.contains('deck-edit-mode');
    if (!_editing && _inField && !ce) return; /* 普通表单/备注输入不打扰 */
    if (ce) {
      /* 输入中：按键不再传给原生 deck（原生复制/粘贴等不受影响） */
      e.stopImmediatePropagation();
    } else if (_editing && ['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', ' ', 'Home', 'End', 'Escape', 'b', 'B'].indexOf(e.key) >= 0) {
      e.stopImmediatePropagation();
    }`, 'keydown routing patch');

// J4b. Esc 退出输入时同步提交撤销历史（不依赖 focusout 异步时序）
jsBlock = replaceOnce(jsBlock,
`      if (ce) {
        e.preventDefault();
        ce.contentEditable = 'false';
        ce.blur();
        editor._closeRteDrawers();
        editor.toolbar.classList.remove('visible');
        return;
      }`,
`      if (ce) {
        e.preventDefault();
        commitEditableNow(ce); /* editable: 同步提交撤销历史，不依赖 focusout 异步时序 */
        if (document.activeElement === ce) ce.blur();
        editor._closeRteDrawers();
        editor.toolbar.classList.remove('visible');
        return;
      }`, 'Escape commit history patch');

// J4c. 退出编辑模式时同步提交所有未入栈的编辑（含焦点在工具栏时的遗留快照）
jsBlock = replaceOnce(jsBlock,
`      } else {
        this._closeRteDrawers();
        this.clearSelection();
        this._clearRteFormatStash();
        this.toolbar.classList.remove('visible');
        document.querySelectorAll('.slide-object-text[contenteditable="true"], [data-edit-slot][contenteditable="true"]').forEach((el) => {
          el.contentEditable = 'false';
        });
      }`,
`      } else {
        this._closeRteDrawers();
        this.clearSelection();
        this._clearRteFormatStash();
        this.toolbar.classList.remove('visible');
        /* editable: 同步提交所有未入栈的编辑；不依赖 focusout 异步时序，
           焦点在工具栏/按钮上时 focusout 回调会提前 return，快照会遗留在这里 */
        commitAllPendingEditables();
      }`, 'setActive(false) commit history patch');

// J4d. 同步提交函数：与 focusout 异步路径共用同一套历史结构，双路径互不重复入栈
jsBlock = replaceOnce(jsBlock,
`  const slotEditor = new SlotEditor(history, editor, updateUndoRedoChrome);`,
`  const slotEditor = new SlotEditor(history, editor, updateUndoRedoChrome);

  /* ---------- editable: 同步提交撤销历史（Escape / 退出编辑模式等时点） ----------
     focusout 的 setTimeout(0) 回调在两种场景下会丢历史：
     1. setActive(false) 先置 active=false 再 blur → object-text 回调里 !editor.active 直接 return
     2. 焦点在 RTE 工具栏/按钮上时回调早退，_deckHtmlBefore 快照遗留
     这里提供同步入口，在确定性时点上栈；异步路径若先入栈，此处因快照已删而幂等跳过 */
  function commitEditableNow(el) {
    if (!el || !el.getAttribute) return;
    const pending = el.getAttribute('contenteditable') === 'true' || el.dataset._deckHtmlBefore !== undefined;
    if (!pending) return;
    el.contentEditable = 'false';
    const before = el.dataset._deckHtmlBefore;
    delete el.dataset._deckHtmlBefore;
    if (before === undefined || before === el.innerHTML) return;
    const after = el.innerHTML;
    if (el.hasAttribute('data-edit-slot') && !el.closest('[data-slide-object]')) {
      const id = el.getAttribute('data-edit-slot');
      history.push({
        undo: () => { const f = slotEditor._slotById(id); if (f) f.innerHTML = before; },
        redo: () => { const f = slotEditor._slotById(id); if (f) f.innerHTML = after; }
      });
      slotEditor.onChange();
    } else if (el.classList.contains('slide-object-text')) {
      history.push({
        undo: () => { el.innerHTML = before; },
        redo: () => { el.innerHTML = after; }
      });
      updateUndoRedoChrome();
    }
  }
  function commitAllPendingEditables() {
    document.querySelectorAll('.slide-object-text[contenteditable="true"], [data-edit-slot][contenteditable="true"]').forEach(commitEditableNow);
    document.querySelectorAll('[data-edit-slot][data-_deck-html-before], .slide-object-text[data-_deck-html-before]').forEach(commitEditableNow);
  }`, 'commitEditableNow functions');

// J4e. Escape 分支整体阻断原生 deck（否则退出编辑模式时原生 ESC 总览视图会被同时触发）
jsBlock = replaceOnce(jsBlock,
`    if (editor.active && (e.key === 'Escape' || e.key === 'Esc')) {
      if (ce) {`,
`    if (editor.active && (e.key === 'Escape' || e.key === 'Esc')) {
      e.stopImmediatePropagation(); /* editable: Escape 属编辑器管辖，原生 deck（ESC 总览开关）不再响应 */
      if (ce) {`, 'Escape native block patch');

// J4f. 缩略图克隆剥离槽位属性（否则编辑模式下侧栏缩略图会显示可编辑虚线框，且污染全局槽位查询）
jsBlock = replaceOnce(jsBlock,
`      cl.querySelectorAll('[contenteditable]').forEach((n) => n.setAttribute('contenteditable', 'false'));`,
`      cl.querySelectorAll('[contenteditable]').forEach((n) => n.setAttribute('contenteditable', 'false'));
      cl.querySelectorAll('[data-edit-slot]').forEach((n) => {
        n.removeAttribute('data-edit-slot');
        n.removeAttribute('data-slot-type');
        n.removeAttribute('data-slot-locked-layout');
        n.removeAttribute('data-slot-label');
        n.removeAttribute('data-_deck-html-before');
      });`, 'filmstrip strip slot attrs');

// J5. 编辑模式下拦截滚轮/触屏翻页
jsBlock = replaceOnce(jsBlock,
`  deck._updateChrome();
})();`,
`  /* editable: 编辑模式下拦截原生 deck 的滚轮/触屏翻页 */
  addEventListener('wheel', (e) => {
    if (document.body.classList.contains('deck-edit-mode')) e.stopImmediatePropagation();
  }, { capture: true, passive: true });
  addEventListener('touchstart', (e) => {
    if (document.body.classList.contains('deck-edit-mode')) e.stopImmediatePropagation();
  }, { capture: true, passive: true });
  addEventListener('touchend', (e) => {
    if (document.body.classList.contains('deck-edit-mode')) e.stopImmediatePropagation();
  }, { capture: true, passive: true });

  deck._updateChrome();
})();`, 'wheel/touch interceptors');

/* ---------- 3. 改造 deck 本体 ---------- */
// R1. html 属性：deck id（决定 localStorage key）+ slots 编辑模式
deck = replaceOnce(deck, '<html lang="zh-CN">',
'<html lang="zh-CN" data-deck-id="atoms-native-intro-editable" data-template-edit-mode="slots" data-mobile-adaptation="desktop-default">',
'html attrs');

// R2. 标题
deck = replaceOnce(deck, '<title>把想法变成应用 · Atoms Native</title>',
'<title>把想法变成应用 · Atoms Native（可编辑版）</title>', 'title');

// R3. #deck 加 slides-offset 类（编辑器以此定位 deck 根）
deck = replaceOnce(deck, '  <div id="deck">', '  <div id="deck" class="slides-offset">', 'deck class');

// R4. slides 声明 let（编辑器增删页后 go() 能拿到新列表）
deck = replaceOnce(deck, "    const slides = deck.querySelectorAll('.slide');",
"    let slides = deck.querySelectorAll('.slide');", 'slides let');

// R5. go() 补丁：实时校正 slides/圆点 + 派发 deck:navigate
deck = replaceOnce(deck,
`    function go(n, opts = {}) {
      if (lock && !opts.force) return;
      idx = Math.max(0, Math.min(total - 1, n));
      window.__currentSlideIndex = idx;`,
`    function go(n, opts = {}) {
      if (lock && !opts.force) return;
      /* editable: 页可能被编辑器增删/重排，翻页前实时校正 slides 与圆点 */
      const live = deck.querySelectorAll('.slide');
      const want = Math.max(0, Math.min(total - 1, n));
      if (live.length !== slides.length || live[want] !== slides[want]) {
        slides = live;
        total = live.length;
        nav.innerHTML = '';
        slides.forEach((s, i) => {
          const b = document.createElement('button');
          b.className = 'dot'; b.dataset.i = i; b.setAttribute('aria-label', 'Page ' + (i + 1));
          b.onclick = () => go(i);
          nav.appendChild(b);
        });
      }
      idx = Math.max(0, Math.min(total - 1, n));
      window.__currentSlideIndex = idx;
      window.dispatchEvent(new CustomEvent('deck:navigate', { detail: { index: idx } }));`,
'go() patch');

// R6. 动效模块：实时查询 slide（编辑器可能增删/重排页面）
deck = replaceOnce(deck, "      const slides = [...document.querySelectorAll('.slide')];",
"      const liveSlides = () => [...document.querySelectorAll('#deck > section.slide')];", 'motion live query');
deck = replaceOnce(deck, '        const slide = slides[i];',
'        const slide = liveSlides()[i];', 'motion playSlide');
deck = replaceOnce(deck, '        const slide = slides[lastIdx];',
'        const slide = liveSlides()[lastIdx];', 'motion pipeAdvance');

// R7. 注入编辑器 chrome（紧跟 <body>）
deck = replaceOnce(deck, '<body>\n',
'<body>\n<!-- ═══════════ Deck Editor Chrome（frontend-slides-editable 运行时） ═══════════ -->\n' + chromeBlock + '\n',
'chrome insert');

// R8. 注入编辑器样式（主题 token + 运行时 CSS + 覆盖规则）
const styleBlock = `
  <style id="swiss-edit-runtime-css">
    /* —— editable: chrome tokens，适配 Atoms Native 墨水主题 —— */
    :root {
      --deck-chrome-bg: rgba(10, 16, 28, .94);
      --deck-chrome-border: rgba(255, 255, 255, .14);
      --deck-chrome-text: #e8edf7;
      --deck-chrome-muted: #94a3b8;
      --deck-chrome-accent: #22d3ee;
      --deck-chrome-shadow: 0 12px 40px rgba(0, 0, 0, .5);
      --deck-chrome-surface: rgba(255, 255, 255, .06);
    }

    /* —— editable: 编辑模式/侧栏缩略图强制显示入场动画元素 —— */
    body.deck-edit-mode [data-anim],
    body.deck-edit-mode [data-animate="pipeline"] [data-anim],
    #slideSidebar [data-anim],
    #slideSidebar [data-animate="pipeline"] [data-anim] {
      opacity: 1 !important;
      transform: none !important;
    }

    /* —— editable: 锁定布局槽位的可编辑提示 —— */
    body.deck-edit-mode [data-edit-slot] {
      outline: 1px dashed rgba(34, 211, 238, .38);
      outline-offset: 3px;
      cursor: text;
    }
    body.deck-edit-mode [data-edit-slot]:hover {
      outline-color: rgba(34, 211, 238, .85);
    }
    body.deck-edit-mode [data-edit-slot][contenteditable="true"] {
      outline: 2px solid var(--deck-chrome-accent);
    }
` + cssBlock + `
  </style>
</head>`;
deck = replaceOnce(deck, '</head>', styleBlock, 'style insert');

// R9. 注入编辑器运行时脚本（必须先于原生 deck 脚本执行：恢复存档 + 抢先拦截按键）
deck = replaceOnce(deck, '  <script>\n    /* =============== WebGL 双背景 ===============',
'  <script id="swiss-slot-edit-runtime-js">' + jsBlock + '  </script>\n  <script>\n    /* =============== WebGL 双背景 ===============',
'runtime script insert');

fs.writeFileSync(OUT, deck, 'utf8');

/* ---------- 4. 摘要 ---------- */
const slidesCount = (deck.match(/<section class="slide/g) || []).length;
console.log('OK →', OUT);
console.log('slides:', slidesCount);
console.log('bytes:', deck.length);
console.log('checks:', {
  chrome: deck.indexOf('id="deckLeftHover"') > 0,
  runtimeJs: deck.indexOf('swiss-slot-edit-runtime-js') > 0,
  runtimeCss: deck.indexOf('swiss-edit-runtime-css') > 0,
  navigate: deck.indexOf('deck:navigate') > 0,
  autoMark: deck.indexOf('autoMarkSlots') > 0,
  commitNow: deck.indexOf('function commitEditableNow') > 0 && deck.indexOf('commitAllPendingEditables()') > 0,
  slidesOffsetClass: deck.indexOf('<div id="deck" class="slides-offset">') > 0,
  orderOk: deck.indexOf('swiss-slot-edit-runtime-js') < deck.indexOf('/* =============== WebGL')
});
