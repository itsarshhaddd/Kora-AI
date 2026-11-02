(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const ls = { get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} }, del: k => { try { localStorage.removeItem(k); } catch (_) {} } };
  const esc = v => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const fmt = n => Number.isFinite(Number(n)) ? Number(n).toLocaleString() : "—";
  const VIEWS = ["home", "chat", "documents", "index", "settings"];
  const TITLES = { home: "Home", chat: "Chat", documents: "Documents", index: "Index", settings: "Settings" };
  const state = { view: "home", sessionId: ls.get("kora_session_id") || null, sending: false, docs: [], sessions: [], progress: { active: false, message: "Ready." }, token: 0, abort: null, selected: new Set(), health: null };

  const MARK = `<span class="mark"><i></i><i></i><i></i><i></i></span>`;
  const IC = {
    user: "",
    copy: `<svg class="ic" viewBox="0 0 24 24" style="width:14px;height:14px"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>`,
  };

  // ------------------------------------------------------------------ helpers
  function toast(msg, kind = "info") {
    const box = $("#toasts");
    if ([...box.children].some(t => t.textContent === msg)) return;
    const el = document.createElement("div"); el.className = "toast " + kind; el.textContent = msg; box.appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 320); }, 2800);
  }
  async function api(url, opt = {}) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), opt.timeout || 20000);
    try {
      const r = await fetch(url, { cache: "no-store", ...opt, signal: ctl.signal });
      let d = {}; try { d = await r.json(); } catch (_) {}
      if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
      return d;
    } catch (e) {
      if (e?.name === "AbortError") throw new Error("KORA took too long to respond. Please try again.");
      if (e instanceof TypeError) throw new Error("Can't reach the KORA server. Make sure KORA is running, then reload.");
      throw e;
    } finally { clearTimeout(t); }
  }
  const docName = d => String(d?.source || d?.path || "Document").split(/[\\/]/).pop();
  const docExt = d => String(d?.file_type || docName(d).split(".").pop() || "file").replace(/^\./, "").toLowerCase();
  const profileName = () => (ls.get("kora_profile_name") || "Local user").trim() || "Local user";
  function applyName() {
    const n = profileName(), init = n.charAt(0).toUpperCase(); $("#profileBtn").textContent = init; $("#profileBtn").title = n; $("#profAvatar").textContent = init; $("#nameInput").value = n === "Local user" ? "" : n;
    const h = new Date().getHours(), g = h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    $("#homeH").textContent = n === "Local user" ? g : `${g}, ${n}`;
  }
  const locLabel = s => [s.page != null && `Page ${s.page}`, s.sheet && `Sheet ${s.sheet}`, s.row != null && `Row ${s.row}`, s.slide != null && `Slide ${s.slide}`, s.section, s.line_start != null && `Lines ${s.line_start}-${s.line_end ?? s.line_start}`, s.metadata?.symbol].filter(Boolean).join(" · ");
  const sTitle = s => s?.title || (s?.messages || []).find(m => m.role === "user")?.content?.slice(0, 80) || "Conversation";
  const qCount = s => (s.messages || []).filter(m => m.role === "user").length;

  function renderRich(text) {
    const inl = raw => String(raw).split(/(`[^`]+`)/).map(p => p.startsWith("`") && p.length > 2 ? `<code>${esc(p.slice(1, -1))}</code>` : esc(p)
      .replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>").replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')).join("");
    const L = String(text ?? "").replace(/\r\n?/g, "\n").split("\n"); let html = "", list = null;
    const close = () => { if (list) html += `</${list}>`; list = null; }, cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(x => x.trim());
    for (let i = 0; i < L.length; i++) {
      const raw = L[i];
      if (/^\s*```/.test(raw)) { close(); const code = []; for (i++; i < L.length && !/^\s*```/.test(L[i]); i++) code.push(L[i]); html += `<pre><code>${esc(code.join("\n"))}</code></pre>`; continue; }
      if (raw.includes("|") && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(L[i + 1] || "")) {
        close(); const head = cells(raw), rows = []; for (i += 2; i < L.length && L[i].includes("|"); i++) rows.push(cells(L[i])); i--;
        html += `<div class="tbl"><table><thead><tr>${head.map(c => `<th>${inl(c)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inl(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; continue;
      }
      const hd = raw.match(/^\s{0,3}(#{1,4})\s+(.*)$/), b = /^\s*[-•*]\s+/.test(raw), n = /^\s*\d+[.)]\s+/.test(raw), q = raw.match(/^\s*>\s?(.*)$/);
      if (hd) { close(); const lv = Math.min(5, hd[1].length + 2); html += `<h${lv}>${inl(hd[2])}</h${lv}>`; }
      else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(raw)) { close(); html += "<hr>"; }
      else if (q) { close(); html += `<blockquote>${inl(q[1])}</blockquote>`; }
      else if (b || n) { const t = b ? "ul" : "ol"; if (list !== t) { close(); html += `<${t}>`; list = t; } html += `<li>${inl(raw.replace(/^\s*(?:[-•*]|\d+[.)])\s+/, ""))}</li>`; }
      else { close(); if (raw.trim()) html += `<p>${inl(raw)}</p>`; }
    }
    close(); return html;
  }
  function clean(text) {
    let v = String(text ?? "").trim(); if (!v) return "I couldn't produce an answer.";
    v = v.replace(/<\|[^>]+\|>/g, "").replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, "").trim(); return v || "I couldn't produce an answer.";
  }

  // ------------------------------------------------------------------ modal / confirm
  function openModal(html, cls = "") {
    const ov = $("#overlay"); ov.innerHTML = `<div class="modal ${cls}" role="dialog" aria-modal="true">${html}</div>`; ov.hidden = false;
    return ov.firstElementChild;
  }
  function closeModal() { const ov = $("#overlay"); ov.hidden = true; ov.innerHTML = ""; ov.onkeydown = null; }
  $("#overlay").addEventListener("mousedown", e => { if (e.target.id === "overlay") closeModal(); });
  function confirmBox(message, { title = "Are you sure?", ok = "Delete" } = {}) {
    return new Promise(res => {
      const m = openModal(`<h3>${esc(title)}</h3><p>${esc(message)}</p><div class="modal-f"><button class="btn" data-c="0">Cancel</button><button class="btn danger" data-c="1">${esc(ok)}</button></div>`, "confirm");
      const done = v => { document.removeEventListener("keydown", key, true); closeModal(); res(v); };
      const key = e => { if (e.key === "Escape") { e.preventDefault(); done(false); } };
      document.addEventListener("keydown", key, true);
      m.addEventListener("click", e => { const b = e.target.closest("[data-c]"); if (b) done(b.dataset.c === "1"); });
      $("[data-c='0']", m).focus();
    });
  }

  // ------------------------------------------------------------------ router
  function moveInd() { const b = $("#nav button.on"), i = $("#navInd"); if (!b || !i) return; i.style.width = b.offsetWidth + "px"; i.style.transform = `translateX(${b.offsetLeft}px)`; }
  function go(view, { noHash = false } = {}) {
    if (!VIEWS.includes(view)) view = "home";
    state.view = view;
    $$(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + view));
    $$("#nav button").forEach(b => b.classList.toggle("on", b.dataset.view === view)); moveInd();
    document.title = `${TITLES[view]} — KORA`;
    if (!noHash && location.hash !== "#/" + view) history.replaceState(null, "", "#/" + view);
    if (view !== "documents") { $("#bulk").hidden = true; }
    $("#v-" + view).scrollTop = 0;
    ({ home: loadHomeFoot, chat: loadChatList, documents: loadDocs, index: loadIndex, settings: loadSettings })[view]();
    if (view === "documents") updateBulk();
    if (view === "chat" && state.sessionId && !$("#messages").children.length) openSession(state.sessionId, { silent: true });
    if (view === "chat") setTimeout(() => $("#composerInput")?.focus(), 50);
  }
  window.addEventListener("hashchange", () => { const v = location.hash.replace(/^#\/?/, ""); if (VIEWS.includes(v) && v !== state.view) go(v, { noHash: true }); });

  async function checkHealth() { try { state.health = await api("/api/health", { timeout: 5000 }); } catch (_) { state.health = null; } }

  // ------------------------------------------------------------------ chat
  let stick = true;
  function scrollBottom(force) { const el = $("#chatScroll"); if (force === true || stick) el.scrollTop = el.scrollHeight; }
  function updateExport() { $("#exportBtn").disabled = !(state.sessionId && $("#messages .msg:not(.error)")); }
  function srcChips(sources) {
    const list = (Array.isArray(sources) ? sources : []);
    if (!list.length) return "";
    return `<div class="srcs"><span class="mono">Sources</span>${list.map((s, i) => { const ext = String(s.source).split(".").pop().toLowerCase(); return `<button class="src" type="button" style="--i:${i}" data-rec="${esc(s.record_id)}"><span class="ft ${esc(ext)}">${esc(ext.slice(0, 4).toUpperCase())}</span><span class="sc"><b>${esc(s.source)}</b><span>${esc(locLabel(s) || "Workspace source")}</span></span><span class="sn">${i + 1}</span></button>`; }).join("")}</div>`;
  }
  function bindMsg(el) {
    $$(".src", el).forEach(b => b.addEventListener("click", () => openSource(b.dataset.rec)));
    $$(".copy", el).forEach(b => b.addEventListener("click", async () => { try { await navigator.clipboard.writeText($(".rich", el).innerText); toast("Copied to clipboard", "success"); } catch (_) { toast("Couldn't copy", "error"); } }));
    $$(".structured-copy", el).forEach(b => b.addEventListener("click", async () => { try { await navigator.clipboard.writeText(b.closest(".structured-result")?.dataset.csv || ""); toast("CSV copied", "success"); } catch (_) { toast("Couldn't copy CSV", "error"); } }));
    $$(".structured-download", el).forEach(b => b.addEventListener("click", () => {
      const csv = b.closest(".structured-result")?.dataset.csv || ""; if (!csv) return;
      const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob); const a = document.createElement("a");
      a.href = url; a.download = "kora-results.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }));
  }
  function addMsg(role, text, meta = null) {
    $("#chatHero").hidden = true;
    const el = document.createElement("div"); el.className = `msg ${role}`;
    if (role === "user") {
      el.innerHTML = `<div class="col"><div class="bub">${text ? esc(text) : ""}</div></div>`;
    } else if (role === "error") {
      el.innerHTML = `<div class="av">${MARK}</div><div class="col"><div class="bub">${esc(text)}</div></div>`;
    } else {
      const structuredHtml = window.KoraStructuredResult?.render(meta?.structured_data) || "";
      el.innerHTML = `<div class="av">${MARK}</div><div class="col"><div class="bub rich">${renderRich(clean(text))}</div>${structuredHtml}${srcChips(meta?.sources)}<div class="acts"><button class="copy" type="button">${IC.copy}Copy</button></div></div>`;
    }
    $("#messages").appendChild(el); bindMsg(el); requestAnimationFrame(() => scrollBottom(role === "user")); return el;
  }
  function addThinking() {
    $("#typing")?.remove();
    const el = document.createElement("div"); el.className = "msg assistant"; el.id = "typing";
    el.innerHTML = `<div class="av">${MARK}</div><div class="col"><div class="think"><span class="mark think-mark"><i></i><i></i><i></i><i></i></span><div><b id="thStage">Thinking</b><span class="s" id="thMsg">Preparing your answer…</span></div></div></div>`;
    $("#messages").appendChild(el); scrollBottom();
  }
  async function pollThinking() {
    if (!$("#thStage")) return;
    try { const a = await api("/api/activity", { timeout: 4000 }); $("#thStage").textContent = { tool: "Using a tool", indexing: "Indexing", thinking: "Thinking" }[a.stage] || "Working"; $("#thMsg").textContent = a.message || "Working…"; } catch (_) {}
  }
  function setSending(on) { const b = $("#sendBtn"); b.classList.toggle("stop-mode", on); b.title = on ? "Stop generating" : ""; if (!on) b.disabled = false; }
  async function readSSE(resp, onEvent) {
    const rd = resp.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { done, value } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); const lines = buf.split("\n"); buf = lines.pop() || "";
      for (const l of lines) { const t = l.trim(); if (!t.startsWith("data:")) continue; const raw = t.slice(5).trim(); if (!raw || raw === "[DONE]") continue; try { onEvent(JSON.parse(raw)); } catch (e) { if (e && e.message) throw e; } }
    }
  }
  async function send(question) {
    const value = String(question ?? $("#composerInput").value).trim();
    if (!value || state.sending) return;
    const token = ++state.token;
    state.sending = true; $("#composerInput").value = ""; $("#composerInput").style.height = "auto"; $("#askInput").value = "";
    if (state.view !== "chat") go("chat");
    addMsg("user", value); addThinking(); setSending(true);
    const timer = setInterval(pollThinking, 700), ctl = new AbortController(); state.abort = ctl;
    const stopHandler = e => { if (token === state.token && state.abort === ctl && state.sending) { e.preventDefault(); ctl.abort(); } };
    $("#sendBtn").onclick = stopHandler;
    let wrap = null, text = "", ok = false;
    const live = () => { if (!wrap) { $("#typing")?.remove(); wrap = document.createElement("div"); wrap.className = "msg assistant streaming"; wrap.innerHTML = `<div class="av">${MARK}</div><div class="col"><div class="bub rich"></div></div>`; $("#messages").appendChild(wrap); } return wrap; };
    const body = JSON.stringify({ question: value, session_id: state.sessionId });
    try {
      const resp = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body, signal: ctl.signal });
      if (!resp.ok || !resp.body) { let m = `Request failed (${resp.status})`; try { m = (await resp.json()).error || m; } catch (_) {} throw new Error(m); }
      await readSSE(resp, ev => {
        if (token !== state.token) return;
        if (ev.type === "delta") { text += ev.text; if (!state.raf) state.raf = requestAnimationFrame(() => { state.raf = 0; if (token !== state.token || ok) return; $(".rich", live()).innerHTML = renderRich(text); scrollBottom(); }); }
        else if (ev.type === "final") {
          ok = true; if (ev.session_id) { state.sessionId = ev.session_id; ls.set("kora_session_id", ev.session_id); }
          $("#typing")?.remove(); live().remove(); wrap = null; addMsg("assistant", ev.answer || text, ev);
        } else if (ev.type === "error") throw new Error(ev.error || "Streaming failed");
      });
      if (token !== state.token) return;
      if (!ok) throw new Error("The response ended unexpectedly.");
      await loadChatList();
    } catch (e) {
      if (token !== state.token) return;
      if (e?.name === "AbortError") { $("#typing")?.remove(); if (wrap && text) { const w = wrap; w.classList.remove("streaming"); } else wrap?.remove(); return; }
      wrap?.remove(); $("#typing")?.remove(); const er = addMsg("error", e.message || "Something went wrong."); toast(e.message || "Chat failed", "error");
      if (value) { $(".col", er).insertAdjacentHTML("beforeend", '<div class="acts"><button class="retry" type="button">Try again</button></div>'); $(".retry", er).onclick = () => { [...$$("#messages .msg.user")].pop()?.remove(); er.remove(); send(value); }; }
    } finally {
      clearInterval(timer);
      // Only the request that still owns the UI may clear its controls. An
      // aborted prior request can finish after a new chat has already started.
      if (token === state.token && state.abort === ctl) {
        state.sending = false;
        state.abort = null;
        if ($("#sendBtn").onclick === stopHandler) $("#sendBtn").onclick = null;
        setSending(false);
        updateExport();
      }
      if (wrap) wrap.classList.remove("streaming");
    }
  }
  function newChat(stay = false) {
    state.token++; state.abort?.abort(); state.abort = null; $("#sendBtn").onclick = null; state.sending = false; setSending(false); state.sessionId = null; ls.del("kora_session_id");
    $("#messages").innerHTML = ""; $("#chatHero").hidden = false; $("#chatTitle").textContent = "New conversation";
    $("#composerInput").value = ""; $("#composerInput").style.height = "auto"; $("#askInput").value = ""; updateExport();
    if (stay === true) return;
    if (state.view !== "chat") go("chat"); else { renderChatList(); $("#composerInput").focus(); }
  }
  async function openSession(id, { silent = false } = {}) {
    const token = ++state.token; state.abort?.abort(); state.abort = null; $("#sendBtn").onclick = null; state.sending = false; setSending(false);
    try {
      const d = await api(`/api/library/${encodeURIComponent(id)}`); if (token !== state.token) return;
      state.sessionId = d.id || id; ls.set("kora_session_id", state.sessionId);
      $("#messages").innerHTML = ""; $("#chatHero").hidden = true; $("#chatTitle").textContent = d.title || "Conversation";
      for (const m of d.messages || []) if (m?.role && m.role !== "system" && m.role !== "tool") addMsg(m.role === "user" ? "user" : "assistant", m.content || "", m);
      if (state.view !== "chat") go("chat"); else renderChatList();
      updateExport(); requestAnimationFrame(() => scrollBottom(true));
    } catch (e) {
      if (token !== state.token) return;
      // A saved conversation that no longer exists is normal (deleted, or history reset): quietly start fresh.
      const gone = /not found/i.test(e.message || "");
      if (state.sessionId === id || gone) { state.sessionId = null; ls.del("kora_session_id"); $("#chatTitle").textContent = "New conversation"; $("#messages").innerHTML = ""; $("#chatHero").hidden = false; updateExport(); }
      if (gone) loadChatList(); else if (!silent) toast(e.message || "Could not open conversation", "error");
    }
  }
  function renderChatList() {
    const q = $("#chatFilter").value.trim().toLowerCase();
    const cur = state.sessions.find(s => s.id === state.sessionId); if (cur && state.view === "chat") $("#chatTitle").textContent = sTitle(cur);
    const rows = state.sessions.filter(s => !q || sTitle(s).toLowerCase().includes(q));
    $("#chatList").innerHTML = rows.map(s => `<div class="ri ${s.id === state.sessionId ? "on" : ""}"><button data-sess="${esc(s.id)}"><b>${esc(sTitle(s))}</b><span>${qCount(s)} question${qCount(s) === 1 ? "" : "s"}</span></button><button class="x" data-del="${esc(s.id)}" aria-label="Delete conversation" title="Delete">×</button></div>`).join("") || `<div class="empty">No conversations yet.</div>`;
  }
  async function loadChatList() { try { state.sessions = (await api("/api/library")).sessions || []; renderChatList(); } catch (e) { $("#chatList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; } }
  async function removeSession(id) {
    const s = state.sessions.find(x => x.id === id);
    if (!(await confirmBox(`Delete “${sTitle(s)}”? This can't be undone.`, { title: "Delete conversation" }))) return;
    try {
      await api(`/api/library/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (state.sessionId === id) newChat(true);
      await loadChatList(); toast("Conversation deleted", "success");
    } catch (e) { toast(e.message, "error"); }
  }
  const bridge = () => (window.pywebview && window.pywebview.api) || null;
  async function exportChat(fmtName) {
    $("#exportMenu").hidden = true; $("#exportBtn").setAttribute("aria-expanded", "false");
    if (!state.sessionId) return;
    const label = fmtName === "md" ? "Markdown" : "PDF";
    try {
      // Desktop app: native "Save as" dialog (choose folder + file name).
      const b = bridge();
      if (b && typeof b.save_export === "function") {
        const res = await b.save_export(state.sessionId, fmtName);
        if (res && res.cancelled) return;
        if (!res || !res.ok) throw new Error((res && res.error) || "Export failed");
        toast(`${label} saved to ${res.path}`, "success"); loadExportFolder(); return;
      }
      const r = await fetch(`/api/library/${encodeURIComponent(state.sessionId)}/export.${fmtName}`);
      if (!r.ok) { let m = `Export failed (${r.status})`; try { m = (await r.json()).error || m; } catch (_) {} throw new Error(m); }
      const blob = await r.blob(), name = r.headers.get("X-Export-Filename") || `KORA_conversation.${fmtName}`;
      // Browsers that support it: let the user pick the location.
      if (window.showSaveFilePicker) {
        try {
          const h = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: label, accept: fmtName === "md" ? { "text/markdown": [".md"] } : { "application/pdf": [".pdf"] } }] });
          const w = await h.createWritable(); await w.write(blob); await w.close(); toast(`${label} saved`, "success"); return;
        } catch (e) { if (e && e.name === "AbortError") return; }
      }
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); toast(`${label} downloaded`, "success");
    } catch (e) { toast(e.message || "Export failed", "error"); }
  }
  async function loadExportFolder() {
    const b = bridge(), el = $("#exportFolder"), btn = $("#exportFolderBtn");
    if (!el || !b || typeof b.get_export_folder !== "function") return;
    try { el.textContent = await b.get_export_folder(); btn.hidden = false; } catch (_) {}
  }
  async function chooseExportFolder() {
    const b = bridge(); if (!b || typeof b.choose_export_folder !== "function") return;
    try { const r = await b.choose_export_folder(); if (r && r.ok) { $("#exportFolder").textContent = r.folder; toast("Export folder updated", "success"); } else if (r && r.error) throw new Error(r.error); }
    catch (e) { toast(e.message || "Could not change folder", "error"); }
  }
  async function openSource(id) {
    if (!id) return;
    const m = openModal(`<div class="modal-h"><div><h3>Source</h3><span>Loading…</span></div><button class="ibtn" data-close aria-label="Close">×</button></div><pre></pre>`);
    m.addEventListener("click", e => { if (e.target.closest("[data-close]")) closeModal(); });
    try { const d = await api(`/api/source-context/${encodeURIComponent(id)}`); $("h3", m).textContent = d.source || "Source"; $(".modal-h span", m).textContent = d.location || "Workspace source"; $("pre", m).textContent = d.content || "No readable content."; }
    catch (e) { $(".modal-h span", m).textContent = ""; $("pre", m).textContent = e.message || "Source unavailable."; }
  }

  // ------------------------------------------------------------------ documents
  const PAGE = 48; let docLimit = PAGE; let layout = ls.get("kora_layout") === "list" ? "list" : "grid"; state.facet = ""; state.mainTypes = [];
  async function loadDocs() { try { state.docs = (await api("/api/documents")).documents || []; renderFacets(); renderDocs(true); } catch (e) { $("#docList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; } }
  function renderFacets() {
    const counts = {}; state.docs.forEach(d => { const t = docExt(d); counts[t] = (counts[t] || 0) + 1; });
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])), main = top.slice(0, 6), rest = top.slice(6);
    state.mainTypes = main.map(x => x[0]);
    if (state.facet && state.facet !== "__other" && !counts[state.facet]) state.facet = "";
    if (state.facet === "__other" && !rest.length) state.facet = "";
    const f = (v, label, n) => `<button class="facet ${state.facet === v ? "on" : ""}" role="tab" aria-selected="${state.facet === v}" data-facet="${esc(v)}">${esc(label)}<em>${fmt(n)}</em></button>`;
    $("#facets").innerHTML = f("", "All", state.docs.length) + main.map(([t, n]) => f(t, t.toUpperCase(), n)).join("") + (rest.length ? f("__other", "Other", rest.reduce((a, [, n]) => a + n, 0)) : "");
    const recs = state.docs.reduce((a, d) => a + (d.record_count || 0), 0);
    $("#docSummary").textContent = state.docs.length ? `${fmt(state.docs.length)} file${state.docs.length === 1 ? "" : "s"} · ${fmt(recs)} records` : "Nothing here yet";
  }
  function filteredDocs() {
    const q = $("#docSearch").value.trim().toLowerCase(), f = state.facet, s = $("#docSort").value;
    return state.docs.filter(d => (!q || docName(d).toLowerCase().includes(q)) && (!f || (f === "__other" ? !state.mainTypes.includes(docExt(d)) : docExt(d) === f)))
      .sort((a, b) => s === "records" ? b.record_count - a.record_count : s === "type" ? docExt(a).localeCompare(docExt(b)) || docName(a).localeCompare(docName(b)) : docName(a).localeCompare(docName(b), undefined, { numeric: true }));
  }
  const TRASH = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>`;
  function renderDocs(reset) {
    if (reset) docLimit = PAGE;
    const docs = filteredDocs(), shown = docs.slice(0, docLimit), keep = new Set(state.docs.map(d => d.path));
    state.selected = new Set([...state.selected].filter(p => keep.has(p)));
    $("#docList").classList.toggle("list", layout === "list"); $$("#viewSeg button").forEach(b => b.classList.toggle("on", b.dataset.layout === layout));
    $("#docList").innerHTML = shown.map(d => `<div class="dcard ${state.selected.has(d.path) ? "sel" : ""}"><button class="dc-main" data-doc="${esc(d.path)}"><span class="ft ${esc(docExt(d))}">${esc(docExt(d).slice(0, 4).toUpperCase())}</span><span class="dc-name">${esc(docName(d))}</span><span class="dc-meta">${fmt(d.record_count)} record${d.record_count === 1 ? "" : "s"}</span></button><label class="chk" title="Select"><input type="checkbox" data-check="${esc(d.path)}" ${state.selected.has(d.path) ? "checked" : ""} aria-label="Select ${esc(docName(d))}"><span></span></label><button class="dx" data-rm="${esc(d.path)}" aria-label="Remove ${esc(docName(d))}" title="Remove">${TRASH}</button></div>`).join("")
      || `<div class="empty">${state.docs.length ? "Nothing matches your search." : "No documents yet. Upload one to get started."}</div>`;
    $("#docShown").textContent = docs.length ? `Showing ${fmt(shown.length)} of ${fmt(docs.length)}` : "";
    $("#docFoot").hidden = shown.length >= docs.length;
    updateBulk();
  }
  function updateBulk() {
    const n = state.selected.size; $("#bulk").hidden = !(n && state.view === "documents"); $("#bulkN").textContent = `${fmt(n)} selected`;
    $("#docList").classList.toggle("selecting", n > 0);
  }
  async function openDoc(loc) {
    const m = openModal(`<div class="modal-h"><div><h3>${esc(String(loc).split(/[\\/]/).pop())}</h3><span>Loading…</span></div><button class="ibtn" data-close aria-label="Close">×</button></div><pre></pre><div class="modal-f"><button class="btn danger small" data-rm2>Remove from workspace</button></div>`);
    m.addEventListener("click", e => { if (e.target.closest("[data-close]")) closeModal(); if (e.target.closest("[data-rm2]")) removeDocs([loc], true); });
    try {
      const [d, p] = await Promise.all([api(`/api/documents/${encodeURIComponent(loc)}`), api(`/api/preview/${encodeURIComponent(loc)}`).catch(() => null)]);
      $("h3", m).textContent = docName(d); $(".modal-h span", m).textContent = `${fmt(d.record_count)} records · ${docExt(d).toUpperCase()}`;
      $("pre", m).textContent = p?.text || `Document ID: ${d.document_id}\nType: ${docExt(d).toUpperCase()}\nHash: ${d.file_hash}`;
    } catch (e) { $(".modal-h span", m).textContent = ""; $("pre", m).textContent = e.message || "Document unavailable."; }
  }
  async function removeDocs(locs, fromModal = false) {
    if (!locs.length) return;
    const one = locs.length === 1;
    if (!(await confirmBox(one ? `Remove ${String(locs[0]).split(/[\\/]/).pop()} from the KORA workspace?` : `Remove ${fmt(locs.length)} documents from the workspace?`, { title: "Remove documents", ok: "Remove" }))) return;
    let n = 0; for (const l of locs) { try { await api(`/api/sources/${encodeURIComponent(l)}`, { method: "DELETE" }); n++; state.selected.delete(l); } catch (_) {} }
    if (fromModal) closeModal(); toast(`${fmt(n)} document${n === 1 ? "" : "s"} removed`, n ? "success" : "error");
    await loadDocs();
  }
  function openUpload(files) {
    const m = openModal(`<div class="modal-h"><div><h3>Upload documents</h3><span>Files stay inside the KORA workspace.</span></div><button class="ibtn" data-close aria-label="Close">×</button></div><div class="upgrid"><label>Company / workspace<input class="field" id="companyIn" maxlength="80" placeholder="e.g. NovaMart"></label><label>Subfolder (optional)<input class="field" id="folderIn" maxlength="160" placeholder="e.g. Reports/2026"></label></div><div class="drop" id="drop"><b>Drop files here</b><span>Documents · text · spreadsheets · presentations · code · configuration</span><button class="btn" id="browse">Browse files</button><input type="file" id="fileIn" multiple hidden></div><div class="queue" id="queue"></div>`);
    const drop = $("#drop", m), inp = $("#fileIn", m);
    m.addEventListener("click", e => { if (e.target.closest("[data-close]")) closeModal(); });
    $("#browse", m).addEventListener("click", () => inp.click()); inp.addEventListener("change", () => upload([...inp.files]));
    ["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", e => upload([...e.dataTransfer.files]));
    if (files?.length) upload(files);
  }
  async function upload(files) {
    const q = $("#queue"); if (!q) return;
    const company = $("#companyIn")?.value.trim() || "";
    const folder = $("#folderIn")?.value.trim() || "";
    for (const f of files) {
      const row = document.createElement("div"); row.className = "qi"; row.innerHTML = `<b style="font-weight:500">${esc(f.name)}</b><span>Uploading…</span>`; q.appendChild(row);
      try {
        const form = new FormData(); form.append("file", f);
        if (company) form.append("company", company);
        if (folder) form.append("folder", folder);
        const r = await api("/api/sources", { method: "POST", body: form, timeout: 180000 });
        $("span", row).textContent = r.status === "indexed" ? "Indexed" : r.status === "indexing_queued" ? "Indexing queued" : "Saved";
        toast(r.status === "indexing_queued" ? `${f.name} saved; indexing queued` : `${f.name} saved`, "success");
      } catch (e) { $("span", row).textContent = "Failed"; toast(`${f.name}: ${e.message}`, "error"); }
    }
    await loadDocs(); refreshProgress(); loadHomeFoot();
  }

  // ------------------------------------------------------------------ index
  const CIRC = 2 * Math.PI * 68;
  async function refreshProgress() { try { state.progress = await api("/api/progress", { timeout: 4000 }); if (state.view === "index") paintIndex(); } catch (_) {} }
  function paintIndex() {
    const p = state.progress, m = String(p.message || "").match(/(\d+)\s*\/\s*(\d+)/), pct = p.active ? (Number.isFinite(+p.percent) ? +p.percent : m ? Math.round(+m[1] / Math.max(1, +m[2]) * 100) : 40) : (p.stage === "complete" ? 100 : 0);
    const failed = Number(p.failed || 0) > 0, ready = p.stage === "complete" && !failed;
    $("#ring").classList.toggle("busy", !!p.active); $("#ringArc").style.strokeDasharray = CIRC; $("#ringArc").style.strokeDashoffset = CIRC * (1 - pct / 100);
    $("#ringTxt").textContent = p.active ? pct + "%" : ready ? "✓" : failed ? "!" : "—"; $("#ringSub").textContent = p.active ? "Indexing" : ready ? "Ready" : failed ? "Issues" : "Waiting";
    $("#idxTitle").textContent = p.active ? "Indexing your workspace" : ready ? "Up to date" : failed ? "Indexing finished with issues" : "Not indexed yet";
    $("#idxMsg").textContent = p.message || (ready ? "Your indexed documents are searchable." : "Import documents or start indexing your workspace.");
    $("#reindexBtn").disabled = !!p.active;
  }
  async function loadIndex() {
    try {
      const [ov, docs, p, report] = await Promise.all([api("/api/overview"), api("/api/documents"), api("/api/progress"), api("/api/index/report")]); state.docs = docs.documents || []; state.progress = p; paintIndex();
      const reportFiles = Array.isArray(report.files) ? report.files : [];
      const reportRows = reportFiles.filter(x => x.status === "failed").concat(reportFiles.filter(x => x.status !== "failed").slice(-5)).slice(0, 10);
      $("#idxReport").innerHTML = report.summary ? `<div class="index-report-summary">${["files", "indexed", "empty", "unchanged", "failed"].map(k => `<span><b>${fmt(report.summary[k] || 0)}</b>${k}</span>`).join("")}</div>` + (reportRows.length ? reportRows.map(x => `<div class="index-report-row"><span>${esc(x.name || x.path || "Unknown file")}</span><b class="${x.status === "failed" ? "bad" : ""}">${esc(x.status)}</b>${x.error ? `<small>${esc(x.error)}</small>` : ""}</div>`).join("") : `<p class="dim">No per-file issues reported.</p>`) : `<p class="dim">No completed indexing report yet.</p>`;
      const groups = {}; state.docs.forEach(d => { const t = docExt(d); (groups[t] ||= { n: 0, r: 0 }).n++; groups[t].r += d.record_count || 0; });
      let rows = Object.entries(groups).sort((a, b) => b[1].r - a[1].r); const extra = rows.slice(6);
      rows = rows.slice(0, 6); if (extra.length) rows.push(["other", extra.reduce((o, [, g]) => ({ n: o.n + g.n, r: o.r + g.r }), { n: 0, r: 0 })]);
      const cls = t => "c-" + (["pdf", "docx", "doc", "xlsx", "csv", "pptx", "txt", "md", "json"].includes(t) ? t : "other");
      $("#idxMini").innerHTML = [["Documents", fmt(ov.documents)], ["Records", fmt(ov.records)], ["File types", fmt(Object.keys(groups).length)]].map(([a, b]) => `<div><strong>${b}</strong><span>${a}</span></div>`).join("");
      $("#idxStack").innerHTML = rows.map(([t, g]) => `<i class="${cls(t)}" style="flex:${Math.max(1, g.r)}" title="${esc(t.toUpperCase())} · ${fmt(g.n)} files"></i>`).join("");
      $("#idxLegend").innerHTML = rows.map(([t, g]) => `<div class="lg-i ${cls(t)}"><i></i><span>${esc(t.toUpperCase())}</span><span class="mono">${fmt(g.n)} file${g.n === 1 ? "" : "s"} · ${fmt(g.r)} rec</span></div>`).join("") || `<div class="empty">Nothing indexed yet.</div>`;
      $("#idxModels").innerHTML = [["Chat model", ov.model], ["Embedding model", ov.embedding_model]].map(([a, b]) => `<div class="kvr"><span>${a}</span><b>${esc(b || "—")}</b></div>`).join("");
    } catch (e) { toast(e.message, "error"); }
  }
  async function reindex() {
    try { const r = await api("/api/reindex", { method: "POST", timeout: 60000 }); toast(r.status === "already_running" ? "Indexing is already running" : "Indexing started", "success"); await refreshProgress(); } catch (e) { toast(e.message, "error"); }
  }
  async function loadHomeFoot() { try { const ov = await api("/api/overview", { timeout: 4000 }); $("#homeFoot").textContent = `${fmt(ov.indexed_documents || 0)} indexed · ${fmt(ov.documents)} in workspace · runs on this computer`; } catch (_) {} }

  // ------------------------------------------------------------------ settings
  function loadSettings() { applyUI(); applyName(); loadSystem(); loadExportFolder(); }
  async function loadSystem() {
    try {
      const d = await api("/api/system"), hw = d.hardware || {}, pr = d.profile || {}, run = d.running || {}, g = n => `${Number(n || 0).toFixed(n >= 10 ? 0 : 1)} GB`;
      const gpu = (hw.gpus || []).map(x => x.name).join(", ") || "None detected";
      $("#sysList").innerHTML = [["Processor", `${hw.cpu_cores} cores / ${hw.cpu_logical} threads`], ["Memory", `${g(hw.ram_total_gb)} (${g(hw.ram_avail_gb)} free)`], ["Graphics", gpu], ["Running on", (run.backend === "CPU" ? "CPU" : "GPU") + " · " + run.backend], ["Context size", `${fmt(run.ctx)} tokens`]].map(([a, b]) => `<div class="kvr"><span>${a}</span><b>${esc(b)}</b></div>`).join("");
      $("#sysWarn").innerHTML = (pr.warnings || []).map(w => `<div class="warn">${esc(w)}</div>`).join("");
      paintProfile(d.prefs?.profile || "auto"); $("#sysGpu").checked = !!d.prefs?.force_gpu;
    } catch (e) { $("#sysList").innerHTML = `<div class="kvr"><span>Unavailable</span><b>${esc(e.message)}</b></div>`; }
  }
  const HELP = { auto: "Auto picks the best settings for this computer.", low: "Gentle uses less memory and CPU, with a smaller context window.", cpu: "CPU only never uses the graphics card." };
  function paintProfile(p) { $$("#profileSeg button").forEach(b => b.classList.toggle("on", b.dataset.profile === p)); $("#modeHelp").textContent = HELP[p]; state.profile = p; }
  async function savePerf() {
    try { await api("/api/system/performance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: state.profile || "auto", force_gpu: $("#sysGpu").checked }) }); toast("Saved — restart KORA to apply", "success"); loadSystem(); }
    catch (e) { toast(e.message, "error"); }
  }
  async function post(url, okMsg) { try { await api(url, { method: "POST" }); toast(okMsg, "success"); } catch (e) { toast(e.message, "error"); } }

  // appearance
  const UI_DEF = { theme: "dark", accent: "#8b6cff", calm: false };
  const getUI = () => { try { return { ...UI_DEF, ...JSON.parse(ls.get("kora_ui") || "{}") }; } catch (_) { return { ...UI_DEF }; } };
  function applyUI(patch) {
    const u = { ...getUI(), ...(patch || {}) }; if (patch) ls.set("kora_ui", JSON.stringify(u));
    const r = document.documentElement; r.dataset.theme = u.theme; r.style.setProperty("--ac", u.accent); r.style.setProperty("--ac2", `color-mix(in srgb,${u.accent} 55%,#fff)`);
    const lum = (() => { const n = parseInt(u.accent.slice(1), 16); return (.299 * (n >> 16) + .587 * ((n >> 8) & 255) + .114 * (n & 255)) / 255; })(); r.style.setProperty("--on-ac", lum > .62 ? "#111" : "#fff");
    if (u.calm) r.dataset.calm = "1"; else delete r.dataset.calm;
    $$("#themes button").forEach(b => b.classList.toggle("on", b.dataset.themePick === u.theme));
    const preset = $$("#swatches button").some(b => b.dataset.accent.toLowerCase() === u.accent.toLowerCase());
    $$("#swatches button").forEach(b => b.classList.toggle("on", b.dataset.accent.toLowerCase() === u.accent.toLowerCase()));
    $(".swatch-custom").classList.toggle("on", !preset); $("#customAccent").value = u.accent; $("#calmToggle").checked = !!u.calm;
  }

  // ------------------------------------------------------------------ custom dropdown
  const CHEV = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>`;
  const CHECK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>`;
  function enhanceSelect(sel) {
    const wrap = document.createElement("div");
    wrap.className = "dd" + (sel.hasAttribute("data-up") ? " up" : "") + (sel.hasAttribute("data-right") ? " right" : "");
    sel.parentNode.insertBefore(wrap, sel); wrap.appendChild(sel); sel.tabIndex = -1;
    const btn = document.createElement("button"), menu = document.createElement("div"), label = sel.dataset.label || "";
    btn.type = "button"; btn.className = "dd-btn"; btn.setAttribute("aria-haspopup", "listbox"); btn.setAttribute("aria-expanded", "false"); btn.setAttribute("aria-label", sel.getAttribute("aria-label") || label);
    menu.className = "dd-menu"; menu.setAttribute("role", "listbox"); wrap.append(btn, menu);
    menu.innerHTML = [...sel.options].map((o, i) => `<button type="button" class="dd-item" role="option" data-i="${i}"><div><b>${esc(o.textContent)}</b>${o.dataset.desc ? `<span>${esc(o.dataset.desc)}</span>` : ""}</div>${CHECK}</button>`).join("");
    const items = () => [...menu.children]; let hl = -1;
    const paint = () => {
      btn.innerHTML = (label ? `<span class="lab">${esc(label)}</span>` : "") + `<span>${esc(sel.selectedOptions[0]?.textContent || "")}</span>` + CHEV;
      items().forEach((it, i) => { it.classList.toggle("sel", i === sel.selectedIndex); it.setAttribute("aria-selected", String(i === sel.selectedIndex)); });
    };
    const mark = i => { hl = i; items().forEach((it, j) => it.classList.toggle("hl", j === i)); items()[i]?.scrollIntoView({ block: "nearest" }); };
    const open = () => { $$(".dd.open").forEach(d => d !== wrap && d.classList.remove("open")); wrap.classList.add("open"); btn.setAttribute("aria-expanded", "true"); mark(sel.selectedIndex); };
    const close = (focus) => { wrap.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); mark(-1); if (focus) btn.focus(); };
    const choose = i => { if (i < 0) return; if (sel.selectedIndex !== i) { sel.selectedIndex = i; sel.dispatchEvent(new Event("change", { bubbles: true })); } paint(); close(true); };
    btn.addEventListener("click", () => wrap.classList.contains("open") ? close() : open());
    menu.addEventListener("click", e => { const it = e.target.closest(".dd-item"); if (it) choose(+it.dataset.i); });
    menu.addEventListener("mousemove", e => { const it = e.target.closest(".dd-item"); if (it && +it.dataset.i !== hl) mark(+it.dataset.i); });
    wrap.addEventListener("keydown", e => {
      const n = sel.options.length, isOpen = wrap.classList.contains("open");
      if (e.key === "Escape" && isOpen) { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (!isOpen) open(); else mark((hl + (e.key === "ArrowDown" ? 1 : -1) + n) % n); }
      else if ((e.key === "Enter" || e.key === " ") && isOpen && hl >= 0) { e.preventDefault(); choose(hl); }
      else if (e.key === "Tab") close();
    });
    document.addEventListener("mousedown", e => { if (!wrap.contains(e.target)) close(); });
    sel.addEventListener("change", paint); paint();
  }

  // ------------------------------------------------------------------ window controls (desktop app only)
  function initWindowControls() {
    const wc = $("#winCtl"), MAX = '<rect x="2.5" y="2.5" width="9" height="9" rx="1"/>', RESTORE = '<rect x="2.5" y="4.5" width="7" height="7" rx="1"/><path d="M4.5 4.5v-1a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-1"/>';
    const paint = on => { $("#wcMaxIc").innerHTML = on ? RESTORE : MAX; $("#wcMax").title = on ? "Restore" : "Maximize"; $("#wcMax").setAttribute("aria-label", on ? "Restore" : "Maximize"); };
    const call = async (name) => { const b = bridge(); if (!b || typeof b[name] !== "function") return; try { return await b[name](); } catch (_) {} };
    const ready = () => { const b = bridge(); if (!b || typeof b.minimize !== "function") return; wc.hidden = false; call("is_maximized").then(v => paint(!!v)); };
    $("#wcMin").addEventListener("click", () => call("minimize"));
    $("#wcMax").addEventListener("click", async () => paint(!!(await call("toggle_maximize"))));
    $("#wcClose").addEventListener("click", () => call("close"));
    $("#topbar").addEventListener("dblclick", async e => { if (e.target.closest(".pywebview-drag-region") === e.target || e.target === $("#topbar")) { if (wc.hidden) return; paint(!!(await call("toggle_maximize"))); } });
    window.addEventListener("pywebviewready", ready); ready();
  }

  // ------------------------------------------------------------------ wiring
  function bind() {
    document.addEventListener("click", e => {
      const t = e.target;
      const del = t.closest("[data-del]"); if (del) { e.stopPropagation(); return removeSession(del.dataset.del); }
      const sess = t.closest("[data-sess]"); if (sess) return openSession(sess.dataset.sess);
      const rm = t.closest("[data-rm]"); if (rm) return removeDocs([rm.dataset.rm]);
      const doc = t.closest("[data-doc]"); if (doc) return openDoc(doc.dataset.doc);
      const nav = t.closest("#nav button"); if (nav) return go(nav.dataset.view);
      if (!t.closest(".export")) { $("#exportMenu").hidden = true; $("#exportBtn").setAttribute("aria-expanded", "false"); }
    });
    $("#profileBtn").addEventListener("click", () => go("settings"));
    $("#askForm").addEventListener("submit", e => { e.preventDefault(); send($("#askInput").value); });
    $("#composerForm").addEventListener("submit", e => { e.preventDefault(); send(); });
    $("#composerInput").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
    $("#composerInput").addEventListener("input", e => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px"; });
    $("#newChat").addEventListener("click", () => newChat());
    $("#starters").addEventListener("click", e => { const b = e.target.closest("[data-q]"); if (b) send(b.dataset.q); });
    $("#homeAttach").addEventListener("click", () => openUpload());
    $("#facets").addEventListener("click", e => { const b = e.target.closest("[data-facet]"); if (!b) return; state.facet = b.dataset.facet; renderFacets(); renderDocs(true); });
    $("#viewSeg").addEventListener("click", e => { const b = e.target.closest("[data-layout]"); if (!b) return; layout = b.dataset.layout; ls.set("kora_layout", layout); renderDocs(); }); $("#chatFilter").addEventListener("input", renderChatList);
    $("#exportBtn").addEventListener("click", e => { e.stopPropagation(); const m = $("#exportMenu"); m.hidden = !m.hidden; $("#exportBtn").setAttribute("aria-expanded", String(!m.hidden)); });
    $$("[data-export]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); exportChat(b.dataset.export); }));
    new MutationObserver(updateExport).observe($("#messages"), { childList: true });
    // documents
    let tm; $("#docSearch").addEventListener("input", () => { clearTimeout(tm); tm = setTimeout(() => renderDocs(true), 120); });
    $("#docSort").addEventListener("change", () => renderDocs(true));
    $("#docMore").addEventListener("click", () => { docLimit += PAGE; renderDocs(); });
    $("#docUpload").addEventListener("click", () => openUpload());
    $("#docList").addEventListener("change", e => { const c = e.target.closest("[data-check]"); if (!c) return; c.checked ? state.selected.add(c.dataset.check) : state.selected.delete(c.dataset.check); c.closest(".dcard").classList.toggle("sel", c.checked); updateBulk(); });
    $("#bulkAll").addEventListener("click", () => { filteredDocs().forEach(d => state.selected.add(d.path)); renderDocs(); });
    $("#bulkCancel").addEventListener("click", () => { state.selected.clear(); renderDocs(); });
    $("#bulkDel").addEventListener("click", () => removeDocs([...state.selected]));
    const dv = $("#v-documents"); ["dragover", "dragenter"].forEach(t => dv.addEventListener(t, e => { if ([...(e.dataTransfer?.types || [])].includes("Files")) e.preventDefault(); }));
    dv.addEventListener("drop", e => { if (e.dataTransfer?.files?.length) { e.preventDefault(); openUpload([...e.dataTransfer.files]); } });
    // index + settings
    $("#reindexBtn").addEventListener("click", reindex);
    const saveName = () => { const v = $("#nameInput").value.trim().slice(0, 80); if (v === (profileName() === "Local user" ? "" : profileName())) return; ls.set("kora_profile_name", v || "Local user"); applyName(); toast("Name saved", "success"); };
    $("#nameInput").addEventListener("change", saveName); $("#nameInput").addEventListener("keydown", e => { if (e.key === "Enter") e.target.blur(); });
    $$("#profileSeg button").forEach(b => b.addEventListener("click", () => { paintProfile(b.dataset.profile); savePerf(); }));
    $("#sysGpu").addEventListener("change", savePerf);
    $("#exportFolderBtn").addEventListener("click", chooseExportFolder);
    window.addEventListener("pywebviewready", loadExportFolder);
    $("#openWs").addEventListener("click", () => post("/api/open-workspace", "Opened workspace folder")); $("#sysLogs").addEventListener("click", () => post("/api/open-logs", "Opened logs folder"));
    $$("#themes button").forEach(b => b.addEventListener("click", () => applyUI({ theme: b.dataset.themePick })));
    $$("#swatches button").forEach(b => b.addEventListener("click", () => applyUI({ accent: b.dataset.accent })));
    $("#customAccent").addEventListener("input", e => applyUI({ accent: e.target.value }));
    $("#calmToggle").addEventListener("change", e => applyUI({ calm: e.target.checked }));
    $("#uiReset").addEventListener("click", () => { applyUI({ ...UI_DEF }); toast("Appearance reset", "success"); });
    document.addEventListener("keydown", e => {
      if (e.key === "/" && !e.ctrlKey && !e.metaKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) && $("#overlay").hidden) { e.preventDefault(); go("chat"); }
      else if (e.key === "Escape") { if (!$("#overlay").hidden) closeModal(); $("#exportMenu").hidden = true; }
    });
    $("#chatScroll").addEventListener("scroll", e => { const el = e.target; stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }, { passive: true });
    window.addEventListener("resize", moveInd); document.fonts?.ready.then(moveInd);
    $$("select[data-label]").forEach(enhanceSelect); initWindowControls();
    window.KoraApp = { sendQuestion: send, toast };
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    applyName(); bind(); applyUI();
    // The native splash (main.py) ends at 40%; carry on from there so it feels like one screen.
    const bar = $("#bootBar"), msg = $("#bootMsg"), skip = $("#bootSkip"), FROM = 40; let done = false;
    bar.style.transition = "none"; bar.style.width = FROM + "%"; void bar.offsetWidth; bar.style.transition = "";
    $("#netbar").hidden = true;
    const finish = () => {
      if (done) return; done = true; bar.style.width = "100%"; msg.textContent = "KORA is ready."; checkHealth();
      setTimeout(() => { document.body.classList.remove("booting"); $("#boot").classList.add("out"); setTimeout(() => $("#boot").remove(), 600); }, 350);
    };
    skip.addEventListener("click", finish);
    const poll = async () => {
      try {
        const h = await api("/api/health", { timeout: 4000 }), b = h.boot || {}, p = Math.max(0, Math.min(100, +b.percent || 0));
        bar.style.width = (FROM + p * (100 - FROM) / 100) + "%"; if (b.message) msg.textContent = b.message; skip.hidden = true;
        if (h.ready || p >= 100) return finish();
      } catch (_) { msg.textContent = "Waiting for the KORA server…"; skip.hidden = false; }
      if (!done) setTimeout(poll, 400);
    };
    poll();
    const first = location.hash.replace(/^#\/?/, "");
    go(VIEWS.includes(first) ? first : "home", { noHash: true });
    const tick = () => { if (!document.hidden && (state.view === "index" || state.progress.active)) refreshProgress(); setTimeout(tick, state.view === "index" || state.progress.active ? 1500 : 6000); }; setTimeout(tick, 1500);
    setInterval(async () => { if (document.hidden || !done) return; try { await api("/api/health", { timeout: 4000 }); $("#netbar").hidden = true; } catch (_) { $("#netbar").hidden = false; } }, 7000);
  }
  boot();
})();
