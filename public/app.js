/* Atoms Native — frontend logic (vanilla JS, no build step). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { token: localStorage.getItem("an_token") || "", user: null, current: null, currentTitle: "", models: [], code: "", versionId: null, securityScore: null, securityFindings: null, securitySummary: null, feedbackSent: false };

  // ---------- helpers ----------
  function authHeader() { return state.token ? { Authorization: "Bearer " + state.token } : {}; }
  async function api(method, path, body) {
    const res = await fetch("./api" + path, {
      method,
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) { let m = ""; try { m = await res.text(); } catch (e) { } throw new Error(m || res.status); }
    return res.json();
  }
  let toastTimer;
  function toast(msg, kind) {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }
  function setBusy(b) {
    ["genBtn", "raceBtn", "refineBtn"].forEach((id) => { const el = $(id); if (el) el.disabled = b; });
    const s = $("streamStatus");
    if (s) { s.textContent = b ? "工作中…" : "空闲"; s.classList.toggle("busy", b); }
  }
  function resetStream() {
    $("stream").innerHTML = ""; agentCards = {}; specCard = null; resetPhases();
    const b = $("secBadge"); if (b) b.classList.add("hidden");
    const pop = $("secPopover"); if (pop) pop.classList.add("hidden");
    state.versionId = null; state.securityScore = null; state.securityFindings = null; state.securitySummary = null;
    resetFeedback();
  }
  function updateSecurityBadge(score, findings, summary) {
    const b = $("secBadge"); if (!b) return;
    if (findings !== undefined) state.securityFindings = findings || [];
    if (summary !== undefined) state.securitySummary = summary || "";
    b.classList.remove("hidden", "good", "warn", "bad");
    b.textContent = "🔐 安全 " + score;
    b.classList.add(score >= 80 ? "good" : score >= 60 ? "warn" : "bad");
  }
  function toggleSecPopover() {
    const p = $("secPopover"); if (!p) return;
    if (!p.classList.contains("hidden")) { p.classList.add("hidden"); return; }
    const score = state.securityScore, f = state.securityFindings || [], s = state.securitySummary || "";
    let html = '<div class="sec-pop-head">🔐 安全扫描 <b>' + score + "/100</b></div>";
    if (f.length) {
      html += '<div class="sec-pop-list">' + f.map((x) => {
        const sev = x.severity || "info";
        return '<div class="sec-find sev-' + sev + '"><span class="sev-dot"></span><b>[' + esc(sev) + "]</b> " +
          esc(x.category || "") + (x.owasp ? ' <span class="sev-owasp">' + esc(x.owasp) + "</span>" : "") + "</div>";
      }).join("") + "</div>";
    } else {
      html += '<div class="sec-pop-list"><div class="sec-find sev-ok">✓ 未发现高危问题</div></div>';
    }
    if (s) html += '<div class="sec-pop-sum">' + esc(s) + "</div>";
    html += '<div class="sec-pop-foot">基于 OWASP LLM Top 10 2025</div>';
    p.innerHTML = html;
    p.classList.remove("hidden");
  }

  // ---------- phase / team / spec helpers ----------
  const PHASE_ORDER = ["PM", "Architect", "Engineer", "Reviewer", "done"];
  const PHASE_LABELS = ["需求分析", "架构设计", "工程实现", "代码评审", "完成"];
  function resetPhases() {
    document.querySelectorAll(".phase").forEach((el) => el.classList.remove("active", "done"));
    const st = $("phaseStatus"); if (st) st.textContent = "待开始";
    const fill = $("pbarFill"); if (fill) fill.style.width = "0%";
  }
  function markPhase(agent) {
    const idx = PHASE_ORDER.indexOf(agent);
    if (idx < 0) return;
    document.querySelectorAll(".phase").forEach((el) => {
      const i = PHASE_ORDER.indexOf(el.dataset.phase);
      el.classList.toggle("active", i === idx);
      el.classList.toggle("done", i < idx);
    });
    const st = $("phaseStatus");
    if (st) st.textContent = agent === "done" ? "已完成" : PHASE_LABELS[idx];
    const fill = $("pbarFill");
    if (fill) fill.style.width = Math.round((idx / (PHASE_ORDER.length - 1)) * 100) + "%";
  }
  function highlightTeam(agent) {
    document.querySelectorAll(".team .member").forEach((el) =>
      el.classList.toggle("active", !!agent && el.dataset.agent === agent));
  }
  function ensureSpecCard() {
    if (specCard) return specCard;
    specCard = document.createElement("div");
    specCard.className = "agent spec running";
    specCard.innerHTML = `<div class="head"><span class="ic">📋</span><span>产品规格 · Emma</span><span class="dot"></span></div><div class="body"></div>`;
    $("stream").appendChild(specCard);
    return specCard;
  }

  // ---------- SSE ----------
  async function streamPost(url, body, onEvent) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify(body),
    });
    if (!res.ok) { let m = ""; try { m = await res.text(); } catch (e) { } throw new Error(m || res.status); }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (line) { try { onEvent(JSON.parse(line.slice(6))); } catch (e) { } }
      }
    }
  }

  // ---------- agent stream rendering ----------
  let agentCards = {};
  let specCard = null;
  function pushAgent(ev) {
    const key = ev.label || ev.agent;
    const stream = $("stream");
    let card = agentCards[key];
    if (!card) {
      card = document.createElement("div");
      card.className = "agent running";
      card.innerHTML = `<div class="head"><span class="ic">${ev.icon || "🤖"}</span><span>${ev.label || ev.agent}</span><span class="dot"></span></div><div class="body"></div>`;
      stream.appendChild(card);
      agentCards[key] = card;
    }
    return card;
  }
  function appendAgent(ev) {
    const key = ev.label || ev.agent;
    const card = agentCards[key]; if (!card) return;
    card.classList.remove("running");
    const body = card.querySelector(".body");
    if (ev.output) body.textContent += (body.textContent ? "\n\n" : "") + ev.output;
    body.scrollTop = body.scrollHeight;
  }
  function addNote(text, icon) {
    const stream = $("stream");
    const d = document.createElement("div");
    d.className = "agent";
    d.innerHTML = `<div class="head"><span class="ic">${icon || "ℹ️"}</span><span>${text}</span></div>`;
    stream.appendChild(d);
  }

  // ---------- preview ----------
  // 沙箱垫片：iframe 无 allow-same-origin 时访问 localStorage 会抛 SecurityError。
  // 注意：window.localStorage 是 getter-only 访问器，直接赋值会被静默忽略（存量 bug），
  // 必须用 Object.defineProperty 以自身属性遮蔽原型访问器。变更经 postMessage
  // 回传父页面落库（P0），重开项目时通过 __AN_BOOT__ 恢复上次数据。
  function injectShim(html, boot) {
    let bootJson = "{}";
    try { bootJson = JSON.stringify(boot || {}).replace(/</g, "\\u003c"); } catch (e) { }
    const shim = '<script>window.__AN_BOOT__=' + bootJson + ';(function(){var m={};for(var k in window.__AN_BOOT__){m[k]=String(window.__AN_BOOT__[k]);}var t=null;function report(){if(t)clearTimeout(t);t=setTimeout(function(){try{window.parent.postMessage({type:"an_sandbox_state",data:m},"*");}catch(e){}},600);}var native=false;try{if(window.localStorage)native=true;}catch(e){}if(native)return;var shimObj={getItem:function(k){return k in m?m[k]:null;},setItem:function(k,v){m[k]=String(v);report();},removeItem:function(k){delete m[k];report();},clear:function(){m={};report();}};try{Object.defineProperty(window,"localStorage",{value:shimObj,writable:false,configurable:false});}catch(e){}})();<\/script>';
    if (html.indexOf("<head") >= 0) return html.replace("<head>", "<head>" + shim);
    return shim + html;
  }
  function setPreview(code, boot) {
    const f = $("preview");
    if (!code) return;
    f.srcdoc = injectShim(code, boot);
    state.code = code;
    $("frameEmpty").style.display = "none";
  }
  // 接收预览 iframe 回传的数据快照，节流落库
  window.addEventListener("message", (e) => {
    if (!state.current) return;
    if (e.source !== $("preview").contentWindow) return;
    const d = e.data;
    if (!d || d.type !== "an_sandbox_state" || typeof d.data !== "object") return;
    state._pendingState = d.data;
    clearTimeout(state._stateTimer);
    state._stateTimer = setTimeout(saveSandboxState, 800);
  });
  async function saveSandboxState() {
    if (!state.current || !state._pendingState) return;
    try {
      await api("POST", "/projects/" + state.current + "/state", {
        state: JSON.stringify(state._pendingState),
      });
    } catch (e) { }
  }

  function markAllStopped() {
    document.querySelectorAll(".agent.running").forEach((el) => el.classList.remove("running"));
    highlightTeam(null);
  }

  // ---------- event router ----------
  function onEvent(ev) {
    switch (ev.type) {
      case "system": addNote(ev.message, "⚠️"); break;
      case "spec":
        addNote("已确定规格，开始并行生成候选", "📐");
        markPhase("PM"); markPhase("Architect");
        break;
      case "agent_start":
        if (ev.agent === "PM") ensureSpecCard();
        else pushAgent(ev);
        markPhase(ev.agent); highlightTeam(ev.agent);
        break;
      case "agent_output":
        if (ev.agent === "PM") {
          const c = ensureSpecCard(); c.classList.remove("running");
          c.querySelector(".body").textContent += ev.output;
        } else {
          if (ev.agent === "Reviewer") markPhase("Reviewer");
          appendAgent(ev);
        }
        break;
      case "app_code":
        if (ev.code) setPreview(ev.code);
        break;
      case "security":
        if (typeof ev.score === "number") {
          state.securityScore = ev.score;
          updateSecurityBadge(ev.score, ev.findings, ev.summary);
          const findings = (ev.findings || []).map((f) => "• [" + f.severity + "] " + f.category).join("<br>");
          addNote("🔐 安全扫描得分 <b>" + ev.score + "/100</b>" + (findings ? "<br>" + findings : " · 未发现高危问题"), "🛡️");
        }
        break;
      case "race_done":
        addNote("Race 完成，最优模型：" + (ev.winner || "?"), "🏁");
        markPhase("done"); highlightTeam(null);
        break;
      case "done":
        markPhase("done"); highlightTeam(null);
        if (typeof ev.security === "number") { state.securityScore = ev.security; updateSecurityBadge(ev.security); }
        if (ev.version_id) state.versionId = ev.version_id;
        onDone(ev);
        break;
      case "error":
        addNote("出错：" + ev.message, "❌");
        markAllStopped();
        setBusy(false);
        break;
    }
  }
  async function onDone(ev) {
    setBusy(false);
    if (ev.project_id) { state.current = ev.project_id; await afterUpdate(ev.project_id); }
  }
  async function applyProjectData(d) {
    state.current = d.project.id;
    state.currentTitle = d.project.title;
    if (d.project.current_version) state.versionId = d.project.current_version;
    const curVer = (d.versions || []).find((v) => v.id === d.project.current_version);
    if (curVer && curVer.security_score != null) {
      state.securityScore = curVer.security_score;
      updateSecurityBadge(curVer.security_score);
    } else {
      const b = $("secBadge"); if (b) b.classList.add("hidden");
      state.securityScore = null; state.securityFindings = null; state.securitySummary = null;
    }
    let boot = {};
    if (d.app_state) { try { boot = JSON.parse(d.app_state); } catch (e) { } }
    setPreview(d.current_code || "", boot);
    renderMessages(d.messages || []);
    highlightGallery(d.project.id);
    resetFeedback();
  }
  async function afterUpdate(id) {
    try {
      const d = await api("GET", "/projects/" + id);
      await applyProjectData(d);
    } catch (e) { }
    await loadProjects();
  }

  // ---------- gallery ----------
  async function loadProjects() {
    try {
      const d = await api("GET", "/projects");
      const g = $("gallery"); g.innerHTML = "";
      const list = d.projects || [];
      if (!list.length) {
        const e = document.createElement("div");
        e.className = "empty";
        e.innerHTML = "还没有项目 ✨<br>在上方描述一个想法，点击「生成应用」开始。";
        g.appendChild(e);
      }
      list.forEach((p) => {
        const el = document.createElement("div");
        el.className = "proj" + (p.id === state.current ? " active" : "");
        el.dataset.id = p.id;
        el.innerHTML = `<span class="del" data-del="${p.id}">✕</span><div class="t">${esc(p.title)}</div><div class="m">${esc(p.idea)}</div>`;
        el.onclick = (e) => { if (e.target.dataset.del) return; openProject(p.id); };
        el.querySelector(".del").onclick = (e) => { e.stopPropagation(); delProject(p.id); };
        g.appendChild(el);
      });
    } catch (e) { toast("加载项目失败"); }
  }
  function highlightGallery(id) {
    document.querySelectorAll(".proj").forEach((el) =>
      el.classList.toggle("active", String(el.dataset.id) === String(id)));
  }
  async function openProject(id) {
    try {
      const d = await api("GET", "/projects/" + id);
      await applyProjectData(d);
      loadProjects();
      toast("已打开：" + d.project.title);
    } catch (e) { toast("打开失败"); }
  }
  async function delProject(id) {
    if (!confirm("删除该项目及其所有版本？")) return;
    try { await api("DELETE", "/projects/" + id); if (state.current === id) { state.current = null; setPreview(""); $("chat").innerHTML = ""; } loadProjects(); toast("已删除"); }
    catch (e) { toast("删除失败"); }
  }

  // ---------- messages ----------
  function renderMessages(msgs) {
    const c = $("chat"); c.innerHTML = "";
    (msgs || []).forEach((m) => {
      const d = document.createElement("div");
      d.className = "msg " + (m.role === "user" ? "user" : "agent");
      d.textContent = m.content;
      c.appendChild(d);
    });
    c.scrollTop = c.scrollHeight;
  }

  // ---------- actions ----------
  async function doGenerate() {
    const idea = $("idea").value.trim();
    if (!idea) { toast("先描述你的想法"); return; }
    setBusy(true); resetStream();
    let proj;
    try { proj = await api("POST", "/projects", { idea, title: idea.slice(0, 30) }); }
    catch (e) { toast("创建项目失败：" + e.message); setBusy(false); return; }
    state.current = proj.project.id; state.currentTitle = proj.project.title;
    try { await streamPost("./api/generate", { project_id: proj.project.id, model: $("modelSel").value || null }, onEvent); }
    catch (e) { toast("生成失败：" + e.message); markAllStopped(); }
    finally { setBusy(false); }
  }
  async function doRefine() {
    const msg = $("refineInput").value.trim();
    if (!msg) { toast("输入精修要求"); return; }
    if (!state.current) { toast("请先生成或打开一个项目"); return; }
    setBusy(true); resetStream();
    try {
      await streamPost("./api/refine", { project_id: state.current, message: msg, model: $("modelSel").value || null }, onEvent);
      $("refineInput").value = "";
    } catch (e) { toast("精修失败：" + e.message); markAllStopped(); }
    finally { setBusy(false); }
  }
  async function doRace() {
    if (!state.current) { toast("请先生成或打开一个项目"); return; }
    let models = state.models.filter((m) => m.on).map((m) => m.id);
    if (!models.length) models = ["deepseek"];
    setBusy(true); resetStream();
    try { await streamPost("./api/race", { project_id: state.current, models }, onEvent); }
    catch (e) { toast("Race 失败：" + e.message); markAllStopped(); }
    finally { setBusy(false); }
  }
  async function doExport() {
    if (!state.current) { toast("没有可导出的项目"); return; }
    try {
      const res = await fetch("./api/projects/" + state.current + "/export", { headers: authHeader() });
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (state.currentTitle || "atoms-app").replace(/\s+/g, "_") + ".html";
      a.click();
    } catch (e) { toast("导出失败"); }
  }
  async function doCopy() {
    if (!state.code) { toast("还没有可复制的源码"); return; }
    try { await navigator.clipboard.writeText(state.code); toast("已复制 HTML 到剪贴板"); }
    catch (e) {
      const ta = document.createElement("textarea"); ta.value = state.code; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); toast("已复制 HTML 到剪贴板"); }
      catch (_) { toast("复制失败，请用导出下载"); }
      document.body.removeChild(ta);
    }
  }
  function toggleSrc() {
    const p = $("srcPanel");
    if (!state.code) { toast("还没有可查看的源码"); return; }
    if (p.classList.contains("hidden")) { $("srcCode").textContent = state.code; p.classList.remove("hidden"); $("srcBtn").textContent = "隐藏源码"; }
    else { p.classList.add("hidden"); $("srcBtn").textContent = "源码"; }
  }
  const TEMPLATES = [
    { t: "落地页", i: "一个现代感的 SaaS 产品落地页，含 Hero、功能特性、价格方案与行动号召" },
    { t: "待办清单", i: "一个支持增删改、完成状态切换与本地保存的待办事项应用" },
    { t: "小游戏", i: "一个用方向键控制、计分与最高分记录的网页小游戏" },
    { t: "作品集", i: "一个个人作品集主页，含头像、简介、项目卡片与联系方式" },
    { t: "打卡工具", i: "一个团队每日饮水/目标打卡小工具，带统计图表与本地记录" },
    { t: "问卷表单", i: "一个多题型问卷表单，提交后显示结果并可导出本地数据" },
    { t: "博客", i: "一个极简博客，支持发布/编辑文章并本地持久化" },
    { t: "数据仪表盘", i: "一个可视化数据仪表盘，含卡片指标与图表（用 chart.js）" },
  ];
  function renderTemplates() {
    const c = $("templates"); if (!c) return;
    TEMPLATES.forEach((tp) => {
      const el = document.createElement("span");
      el.className = "chip"; el.textContent = tp.t;
      el.onclick = () => { $("idea").value = tp.i; $("idea").focus(); };
      c.appendChild(el);
    });
  }

  function doReload() { if (state.current) afterUpdate(state.current); }

  async function toggleVersionPanel() {
    const panel = $("versionPanel");
    if (!panel.classList.contains("hidden")) { panel.classList.add("hidden"); return; }
    if (!state.current) { toast("请先生成或打开一个项目"); return; }
    panel.classList.remove("hidden");
    try {
      const d = await api("GET", "/projects/" + state.current);
      renderVersions(d);
    } catch (e) { toast("加载版本失败"); }
  }
  function renderVersions(d) {
    const panel = $("versionPanel");
    const cur = d.project.current_version;
    const vs = (d.versions || []).slice().reverse();
    if (!vs.length) { panel.innerHTML = '<div class="ver-empty">暂无版本</div>'; return; }
    panel.innerHTML =
      '<div class="ver-head"><span>🕘 版本历史</span><button id="verClose" class="ghost sm">收起</button></div>' +
      '<div class="ver-list">' + vs.map((v) => {
        const s = v.security_score;
        const sec = s != null ? '<span class="ver-sec ' + (s >= 80 ? "good" : s >= 60 ? "warn" : "bad") + '">🔐' + s + "</span>" : "";
        const isCur = v.id === cur;
        return '<div class="ver-item' + (isCur ? " cur" : "") + '">' +
          '<div class="ver-meta"><b>#' + v.version_no + "</b> " + esc(v.model_used || "") + " " + sec +
          " <small>" + esc(v.created_at || "") + "</small>" + (isCur ? ' <span class="ver-cur-tag">当前</span>' : "") + "</div>" +
          '<div class="ver-note">' + (esc(v.note || "") || "—") + "</div>" +
          (isCur ? "" : '<button class="ghost sm ver-restore" data-vid="' + v.id + '">恢复此版本</button>') +
          "</div>";
      }).join("") + "</div>" +
      '<div class="ver-actions"><button id="verPrev" class="secondary sm">↩ 回滚到上一版本</button></div>';
    panel.querySelector("#verClose").onclick = () => panel.classList.add("hidden");
    panel.querySelectorAll(".ver-restore").forEach((b) => b.onclick = () => restoreVersion(parseInt(b.dataset.vid, 10)));
    const vp = $("verPrev"); if (vp) vp.onclick = () => rollbackPrev();
  }
  async function restoreVersion(vid) {
    if (!state.current) return;
    try {
      const r = await api("POST", "/projects/" + state.current + "/rollback", { version_id: vid });
      toast("已恢复到版本 " + r.version_id);
      state.versionId = r.version_id;
      $("versionPanel").classList.add("hidden");
      await afterUpdate(state.current);
    } catch (e) { toast("恢复失败：" + e.message); }
  }
  async function rollbackPrev() {
    if (!state.current) return;
    try {
      const r = await api("POST", "/projects/" + state.current + "/rollback", {});
      toast("已回滚到版本 " + r.version_id);
      state.versionId = r.version_id;
      $("versionPanel").classList.add("hidden");
      await afterUpdate(state.current);
    } catch (e) { toast("回滚失败：" + e.message); }
  }
  async function doFeedback(rating) {
    if (!state.current) { toast("请先生成或打开一个项目"); return; }
    if (!state.versionId) { toast("暂无可评价版本"); return; }
    if (state.feedbackSent) { toast("本版本已反馈过，感谢！"); return; }
    const comment = $("fbComment").value.trim();
    try {
      await api("POST", "/feedback", { project_id: state.current, version_id: state.versionId, rating, comment });
      state.feedbackSent = true;
      if ($("fbUp")) $("fbUp").disabled = true;
      if ($("fbDown")) $("fbDown").disabled = true;
      if ($("fbState")) $("fbState").classList.remove("hidden");
      if ($("fbComment")) $("fbComment").disabled = true;
      toast(rating > 0 ? "感谢反馈 👍" : "感谢反馈，我们会改进 👎");
    } catch (e) { toast("反馈失败：" + e.message); }
  }
  function resetFeedback() {
    state.feedbackSent = false;
    const up = $("fbUp"), down = $("fbDown"), st = $("fbState"), c = $("fbComment");
    if (up) up.disabled = false;
    if (down) down.disabled = false;
    if (st) st.classList.add("hidden");
    if (c) { c.value = ""; c.disabled = false; }
  }

  // ---------- auth ----------
  let authTab = "login";
  async function submitAuth(e) {
    e.preventDefault();
    const username = $("username").value.trim();
    const password = $("password").value;
    if (!username || !password) { $("authErr").textContent = "请输入用户名和密码"; return; }
    try {
      const path = authTab === "login" ? "/auth/login" : "/auth/register";
      const r = await api("POST", path, { username, password });
      state.token = r.token; state.user = r.user;
      localStorage.setItem("an_token", r.token);
      await enterApp();
      // 登录前从发现页带着「使用此模板」跳转来的：直达刚创建的项目
      let pending = null;
      try { pending = sessionStorage.getItem("an_open_project"); } catch (e) { }
      if (pending) {
        try { sessionStorage.removeItem("an_open_project"); } catch (e) { }
        openProject(parseInt(pending, 10)).catch(() => { });
      }
    } catch (err) { $("authErr").textContent = err.message || "认证失败"; }
  }
  function logout() { state.token = ""; localStorage.removeItem("an_token"); location.reload(); }
  async function enterApp() {
    $("auth").classList.add("hidden");
    $("main").classList.remove("hidden");
    $("userName").textContent = state.user ? state.user.username : "";
    await loadModels();
    await loadProjects();
  }

  // ---------- models / race UI ----------
  async function loadModels() {
    try {
      const d = await api("GET", "/models");
      const badge = $("modeBadge");
      badge.textContent = d.mock ? "离线模板模式" : "真实大模型";
      badge.style.color = d.mock ? "var(--amber)" : "var(--cyan)";
      const choices = (d.choices && d.choices.length) ? d.choices : [{ id: "deepseek", label: "DeepSeek" }];
      const sel = $("modelSel");
      sel.innerHTML = "";
      choices.forEach((c) => {
        const o = document.createElement("option");
        o.value = c.id;
        o.textContent = c.label + (c.free ? " · 免费" : "");
        sel.appendChild(o);
      });
      // Prefer the working direct-DeepSeek option as the default selection so a
      // plain "生成应用" click produces a real app; OpenRouter models (which may
      // be account-gated) stay selectable but aren't the default.
      const hasDirect = choices.some((c) => c.id === "deepseek");
      if (hasDirect) sel.value = "deepseek";
      // race model list (toggle chips) — also from choices
      state.models = choices.map((c, i) => ({
        id: c.id, label: c.label,
        on: hasDirect ? (c.id === "deepseek") : (i < Math.min(2, choices.length)),
      }));
      const ml = $("modelList"); ml.innerHTML = "";
      state.models.forEach((m) => {
        const c = document.createElement("span");
        c.className = "model-chip" + (m.on ? " on" : "");
        c.textContent = m.label;
        c.onclick = () => { m.on = !m.on; c.classList.toggle("on", m.on); };
        ml.appendChild(c);
      });
    } catch (e) { toast("加载模型失败"); }
  }

  function esc(s) { return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  // ---------- wire up ----------
  function init() {
    document.querySelectorAll(".tab").forEach((t) => t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active"); authTab = t.dataset.tab;
      $("authBtn").textContent = authTab === "login" ? "进入工作台" : "创建账号并进入";
    });
    $("authForm").onsubmit = submitAuth;
    $("logoutBtn").onclick = logout;
    $("genBtn").onclick = doGenerate;
    $("raceBtn").onclick = doRace;
    $("refineBtn").onclick = doRefine;
    $("refineInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doRefine(); });
    $("rollbackBtn").onclick = toggleVersionPanel;
    $("secBadge").onclick = toggleSecPopover;
    $("fbUp").onclick = () => doFeedback(1);
    $("fbDown").onclick = () => doFeedback(-1);
    $("exportBtn").onclick = doExport;
    $("reloadBtn").onclick = doReload;
    $("copyBtn").onclick = doCopy;
    $("srcBtn").onclick = toggleSrc;
    document.querySelectorAll("#devSeg .seg-btn").forEach((b) => b.onclick = () => {
      document.querySelectorAll("#devSeg .seg-btn").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      $("deviceFrame").className = "device " + b.dataset.dev;
    });
    renderTemplates();
    $("newBtn").onclick = () => { state.current = null; state.code = ""; $("idea").value = ""; $("idea").focus(); resetStream(); setPreview(""); $("frameEmpty").style.display = ""; $("chat").innerHTML = ""; $("srcPanel").classList.add("hidden"); $("srcBtn").textContent = "源码"; document.querySelectorAll(".proj").forEach((e) => e.classList.remove("active")); };

    if (state.token) {
      api("GET", "/me").then((r) => { state.user = r.user; enterApp(); })
        .catch(() => { localStorage.removeItem("an_token"); state.token = ""; });
    }
    // 发现页「使用此模板」登录跳转：?project=<id> 直达新项目
    const pid = new URLSearchParams(location.search).get("project");
    if (pid) {
      history.replaceState(null, "", location.pathname);
      if (state.token) {
        api("GET", "/me").then((r) => { state.user = r.user; return enterApp(); })
          .then(() => openProject(parseInt(pid, 10)))
          .catch(() => { });
      } else {
        try { sessionStorage.setItem("an_open_project", pid); } catch (e) { }
      }
    }
    const pending = (() => { try { return sessionStorage.getItem("an_open_project"); } catch (e) { return null; } })();
    if (pending && state.token) {
      sessionStorage.removeItem("an_open_project");
      api("GET", "/me").then((r) => { state.user = r.user; return enterApp(); })
        .then(() => openProject(parseInt(pending, 10)))
        .catch(() => { });
    }
  }
  init();
})();
