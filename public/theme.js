// Atoms Native — 全站颜色模式绑定（an_theme: 'light'|'dark'，无值=浅色）
// 防闪烁脚本已在各页 <head> 内联设置 <html data-theme>；这里只负责切换与按钮状态。
(function () {
  var root = document.documentElement;
  var btn = document.getElementById('themeToggle');
  if (!btn) return;

  function sync() {
    var dark = root.dataset.theme === 'dark';
    btn.textContent = dark ? '☀️' : '🌙';
    var label = dark ? '切换浅色模式' : '切换深色模式';
    btn.setAttribute('aria-label', label);
    btn.title = label;
  }

  btn.addEventListener('click', function () {
    var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('an_theme', next); } catch (e) { /* 隐私模式等：仅本次会话生效 */ }
    sync();
  });

  sync();
})();
