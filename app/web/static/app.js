/* Creator Pipeline front end. Plain JavaScript, no build step. Talks to /api/* on the same origin. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  const state = {
    boot: null, engine: "wan", tier: "Standard", seeds: 2, chunks: 1, lengths: [], info: null,
    image: null, video: null, imageName: "", videoName: "", preflightOk: false, preflightTimer: null,
    jobs: [], filter: "all", search: "", open: null, view: "create", estTimer: null,
  };

  // ---------- API ----------
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) throw new Error((data && data.error) || `${r.status} ${r.statusText}`);
    return data;
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  function toast(text, bad = false) {
    const t = $("#toast"); t.textContent = text; t.className = "toast show" + (bad ? " bad" : "");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), bad ? 5000 : 2800);
  }

  // ---------- views ----------
  function show(view) {
    state.view = view;
    $$(".view").forEach(v => { v.hidden = v.id !== "view-" + view; });
    $$(".rail a").forEach(a => a.classList.toggle("on", a.dataset.view === view));
    if (view === "costs") loadCosts();
    if (view === "library") renderGrid();
  }
  window.addEventListener("hashchange", () => show((location.hash || "#create").slice(1)));

  // ---------- segmented controls ----------
  function seg(container, items, value, onPick) {
    container.innerHTML = "";
    items.forEach(it => {
      const b = el("button", it.value === value ? "on" : "", it.label);
      b.type = "button"; b.title = it.title || "";
      if (it.disabled) b.disabled = true;
      b.addEventListener("click", () => { $$("button", container).forEach(x => x.classList.remove("on")); b.classList.add("on"); onPick(it.value); });
      container.appendChild(b);
    });
  }

  // ---------- bootstrap ----------
  async function boot() {
    const b = await api("/api/bootstrap");
    state.boot = b;
    $("#user-chip").hidden = !b.auth;
    seg($("#seg-engine"), b.engines.map(e => ({ value: e.key, label: e.label.split(" (")[0], title: e.label })), state.engine, v => { state.engine = v; engineHint(); preflight(); estimate(); });
    engineHint();
    const chars = Object.keys(b.characters), scenes = Object.keys(b.scenes);
    fill($("#sel-character"), chars); fill($("#sel-scene"), scenes);
    $("#appearance").value = b.characters[chars[0]] || "";
    applyScene(scenes[0]);
    $("#sel-character").addEventListener("change", e => { $("#appearance").value = b.characters[e.target.value] || ""; });
    $("#sel-scene").addEventListener("change", e => applyScene(e.target.value));
    seg($("#seg-tier"), Object.keys(b.tiers).map(t => ({ value: t, label: t, title: b.tiers[t].note })), state.tier, v => { state.tier = v; estimate(); });
    seg($("#seg-seeds"), [1, 2, 3, 4].map(n => ({ value: n, label: String(n), title: n + " seed" + (n > 1 ? "s" : "") })), state.seeds, v => { state.seeds = v; estimate(); });
    fill($("#batch-engine"), b.engines.map(e => e.key), b.engines.map(e => e.label));
    fill($("#batch-tier"), Object.keys(b.tiers));
    $("#btn-describe").disabled = !b.describe_available;
    $("#btn-describe").title = b.describe_available ? "Fill the background text from the reference image or the driving video" : "Needs ANTHROPIC_API_KEY on the machine running the app";
    setStatus(b.status);
    estimate();
    await loadJobs();
    show((location.hash || "#create").slice(1));
    setInterval(poll, 4000);
  }
  function fill(select, values, labels) {
    select.innerHTML = "";
    values.forEach((v, i) => { const o = el("option", "", labels ? labels[i] : v); o.value = v; select.appendChild(o); });
  }
  function applyScene(name) {
    const s = state.boot.scenes[name]; if (!s) return;
    $("#background").value = s.background; $("#motion").value = s.motion;
    $("#identity_hold").value = s.identity_hold; $("#motion_looseness").value = s.motion_looseness;
  }
  function engineHint() {
    const h = { wan: "Background and camera come from the scene text.", scail: "The character is swapped into the original video; background, lighting and camera are kept. Identity hold does not apply.",
                seedance: "ByteDance's model with sound, billed in Comfy credits. No GPU needed. Swap mode keeps the driving video's background and length; Fast uses the cheaper Draft model. Finish and the sliders do not apply." }[state.engine];
    const needKey = state.engine === "seedance" && !state.boot.seedance_available;
    $("#seg-seedance-mode").hidden = state.engine !== "seedance";
    $("#views").hidden = state.engine !== "scail";
    if (state.engine === "seedance" && !$("#seg-seedance-mode").children.length) {
      state.seedanceMode = state.seedanceMode || "edit";
      seg($("#seg-seedance-mode"), Object.entries(state.boot.seedance_modes).map(([k, v]) => ({ value: k, label: v, title: v })), state.seedanceMode, v => { state.seedanceMode = v; estimate(); });
    }
    $("#engine-hint").textContent = h + (needKey ? " Connect your Comfy account below to use it." : "");
    $("#comfy-keyform").hidden = !needKey;
    $("#anthropic-keyform").hidden = state.boot.describe_available;
    $("#btn-describe").disabled = !state.boot.describe_available;
  }
  async function connectKey(e, field, which) {
    e.preventDefault();
    const value = $(field).value.trim(); if (!value) return;
    try {
      const r = await post("/api/keys", { [which]: value });
      state.boot.seedance_available = r.seedance_available; state.boot.describe_available = r.describe_available;
      $(field).value = ""; engineHint(); renderButton();
      toast(which === "comfy_api_key" ? (r.seedance_available ? "Comfy account connected." : "Key was empty.") : (r.describe_available ? "Describe is ready." : "Key was empty."));
    } catch (err) { toast(err.message, true); }
  }
  function setStatus(s) {
    const c = $("#status-chip");
    c.textContent = s.text + (s.gpu_name ? ` · ${s.gpu_name.replace("NVIDIA ", "")} ${Math.round(s.gpu_vram_gib)} GiB` : "");
    c.className = "chip" + (s.busy ? " busy" : "");
  }

  // ---------- uploads ----------
  function wireDrop(dropId, inputId, kind) {
    const drop = $(dropId), input = $(inputId);
    input.addEventListener("change", () => input.files[0] && upload(input.files[0], kind));
    ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) upload(f, kind); });
  }
  async function upload(file, kind) {
    const fd = new FormData(); fd.append("file", file); fd.append("kind", kind);
    const nameEl = kind === "ref" ? $("#name-image") : (kind === "drive" ? $("#name-video") : $("#name-" + kind));
    nameEl.hidden = false; nameEl.textContent = "Uploading " + file.name;
    try {
      const r = await api("/api/uploads", { method: "POST", body: fd });
      if (kind === "ref") {
        state.image = r.id; state.imageName = file.name;
        const img = $("#img-preview"); img.src = URL.createObjectURL(file); img.hidden = false; $("#drop-image").classList.add("filled");
      } else if (kind === "view1" || kind === "view2") {
        state.views = state.views || {}; state.views[kind] = r.id;
        const img = $("#" + kind + "-preview"); img.src = URL.createObjectURL(file); img.hidden = false; img.closest(".drop").classList.add("filled");
        nameEl.textContent = file.name; return;
      } else {
        state.video = r.id; state.videoName = file.name;
        const v = $("#vid-preview"); v.src = URL.createObjectURL(file); v.hidden = false; $("#drop-video").classList.add("filled");
        v.addEventListener("loadeddata", () => { v.currentTime = 0.1; }, { once: true });
      }
      nameEl.textContent = file.name;
      preflight();
    } catch (e) { nameEl.textContent = "Upload failed"; toast(e.message, true); }
  }
  async function setInputsFromJob(job) {
    // Reuse of an earlier job's inputs: they already live in ComfyUI/input, referenced by name.
    state.image = job.inputs.image; state.video = job.inputs.video; state.imageName = job.inputs.image; state.videoName = job.inputs.video;
    const img = $("#img-preview"); img.src = job.inputs_urls.image || ""; img.hidden = !job.inputs_urls.image; $("#drop-image").classList.toggle("filled", !!job.inputs_urls.image);
    const v = $("#vid-preview"); v.src = job.inputs_urls.video || ""; v.hidden = !job.inputs_urls.video; $("#drop-video").classList.toggle("filled", !!job.inputs_urls.video);
    $("#name-image").hidden = false; $("#name-image").textContent = job.inputs.image;
    $("#name-video").hidden = false; $("#name-video").textContent = job.inputs.video;
    preflight();
  }

  // ---------- preflight and estimate ----------
  async function preflight() {
    const box = $("#check");
    if (!state.image || !state.video) { box.textContent = "Add an image and a video. The check runs by itself."; state.preflightOk = false; renderButton(); return; }
    box.textContent = "Checking";
    try {
      const r = await post("/api/preflight", { image: state.image, video: state.video, engine: state.engine });
      box.innerHTML = "";
      r.messages.forEach(m => { const s = el("span", m.level, m.text); box.appendChild(s); });
      state.preflightOk = r.ok; state.info = r.info; state.lengths = r.lengths;
      const current = state.lengths.find(l => l.passes === state.chunks) || state.lengths[state.lengths.length - 1];
      state.chunks = current ? current.passes : 1;
      if (state.lengths.length) {
        seg($("#seg-length"), state.lengths.map(l => ({ value: l.passes, label: l.seconds + " s", title: l.label })), state.chunks, v => { state.chunks = v; estimate(); });
      } else {
        $("#seg-length").innerHTML = '<button disabled>Video too short</button>';
      }
    } catch (e) { box.innerHTML = ""; box.appendChild(el("span", "bad", e.message)); state.preflightOk = false; }
    renderButton(); estimate();
  }
  function seconds() {
    if (!state.info || !state.info.frames) return 5;
    return state.info.frames / (state.info.fps || 24);
  }
  function estimate() {
    clearTimeout(state.estTimer);
    state.estTimer = setTimeout(async () => {
      try {
        const q = new URLSearchParams({ engine: state.engine, tier: state.tier, chunks: state.chunks, finishing: $("#finishing").checked, seeds: state.seeds, seconds: seconds(), seedance_mode: state.seedanceMode || "edit" });
        const r = await api("/api/estimate?" + q);
        const passes = state.engine === "seedance" ? "whole clip" : `${state.chunks} pass${state.chunks > 1 ? "es" : ""}`;
        $("#est").innerHTML = `<span>${passes} × ${state.seeds} seed${state.seeds > 1 ? "s" : ""}</span><b>about ${Math.round(r.minutes)} min</b>`;
        $("#est-note").textContent = r.note || "";
        $("#est-note").classList.toggle("bad", r.vram_ok === false);
        $("#offload-row").hidden = r.vram_ok !== false;
        state.price = r; renderButton();
      } catch (e) { $("#est-note").textContent = e.message; }
    }, 120);
  }
  function renderButton() {
    const b = $("#btn-render");
    const pr = state.price;
    const price = pr ? (pr.currency === "credits" ? `<span class="price">${pr.credits.toLocaleString()} credits (~$${pr.usd.toFixed(2)})</span>` : `<span class="price">$${pr.usd.toFixed(2)}</span>`) : "";
    const tooSmall = !!(pr && pr.vram_ok === false && !$("#allow_offload").checked);
    b.innerHTML = (pr && pr.over_cap ? "Over the credit cap" : (tooSmall ? "GPU too small for this tier" : "Render")) + price;
    b.disabled = !(state.preflightOk && state.image && state.video) || !!(pr && pr.over_cap) || tooSmall;
  }
  async function render() {
    const b = $("#btn-render"); b.disabled = true; $("#run-error").hidden = true;
    try {
      const job = await post("/api/jobs", {
        image: state.image, video: state.video, engine: state.engine, character: $("#sel-character").value, appearance: $("#appearance").value,
        scene: $("#sel-scene").value, background: $("#background").value, motion: $("#motion").value,
        identity_hold: parseFloat($("#identity_hold").value) || 1, motion_looseness: parseFloat($("#motion_looseness").value) || 1,
        tier: state.tier, seeds: state.seeds, chunks: state.chunks, finishing: $("#finishing").checked, note: $("#note").value,
        seedance_mode: state.seedanceMode || "edit",
        extra_views: state.engine === "scail" && state.views ? ["view1", "view2"].map(k => state.views[k]).filter(Boolean) : [],
        allow_offload: $("#allow_offload").checked,
      });
      toast(`Queued. ${job.seeds} seed${job.seeds > 1 ? "s" : ""}, about ${Math.round(job.estimate_minutes)} minutes.`);
      state.jobs.unshift(job); job._new = true; renderGrid();
    } catch (e) { $("#run-error").hidden = false; $("#run-error").textContent = e.message; }
    b.disabled = false; renderButton();
  }
  async function describe() {
    const useVideo = !!state.video && (!state.image || state.engine === "scail");
    const ref = useVideo ? state.video : state.image;
    const hint = $("#describe-hint"); hint.hidden = false;
    if (!ref) { hint.textContent = "Add the reference image or the driving video first."; return; }
    hint.textContent = "Looking at the " + (useVideo ? "driving video" : "reference image");
    $("#btn-describe").disabled = true;
    try {
      const r = await post("/api/describe", { ref, source: useVideo ? "video" : "image" });
      $("#background").value = r.text; hint.textContent = "Background filled from the " + (useVideo ? "driving video" : "reference image") + ". Edit it if you like.";
    } catch (e) { hint.textContent = e.message; }
    $("#btn-describe").disabled = false;
  }

  // ---------- jobs feed ----------
  async function loadJobs() { state.jobs = await api("/api/jobs"); renderGrid(); }
  async function poll() {
    try {
      const [jobs, status] = await Promise.all([api("/api/jobs"), api("/api/status")]);
      const before = Object.fromEntries(state.jobs.map(j => [j.id, j.status]));
      state.jobs = jobs; setStatus(status); renderGrid();
      jobs.forEach(j => { if (before[j.id] && before[j.id] !== j.status && (j.status === "done" || j.status === "partial" || j.status === "error")) toast(`${j.character} · ${j.scene}: ${j.status}`, j.status === "error"); });
      if (state.open && $("#drawer").classList.contains("show")) refreshDrawer();
    } catch (e) { const c = $("#status-chip"); c.textContent = "App unreachable"; c.className = "chip off"; }
  }
  function matches(j) {
    const f = state.filter;
    if (f === "running" && !(j.status === "queued" || j.status === "running")) return false;
    if (f === "done" && j.status !== "done") return false;
    if (f === "failed" && !(j.status === "error" || j.status === "partial")) return false;
    if (state.search) {
      const s = state.search.toLowerCase();
      return [j.character, j.scene, j.note, j.id, j.engine_label, j.tier].some(x => (x || "").toLowerCase().includes(s));
    }
    return true;
  }
  function card(j) {
    const c = el("button", "card" + (j._new ? " enter" : "")); c.type = "button"; c.dataset.id = j.id;
    const working = j.status === "queued" || j.status === "running";
    const thumb = el("div", "thumb" + (working ? " working" : ""));
    const badge = el("span", "badge " + ({ done: "ok", error: "bad", partial: "warn" }[j.status] || ""), { queued: "Queued", running: "Rendering", done: "Done", partial: "Partial", error: "Failed" }[j.status] || j.status);
    thumb.appendChild(badge);
    if (j.poster) { const img = el("img"); img.alt = ""; img.loading = "lazy"; img.src = j.poster; img.onload = () => img.classList.add("ready"); thumb.appendChild(img); }
    if (working) {
      const st = el("div", "stage");
      st.innerHTML = `<small><span>${j.stage}</span><span>${Math.round(j.fraction * 100)}%</span></small><div class="bar"><i style="width:${Math.round(j.fraction * 100)}%"></i></div>`;
      thumb.appendChild(st);
    } else if (j.poster) { const p = el("div", "play"); p.innerHTML = "<i></i>"; thumb.appendChild(p); }
    c.appendChild(thumb);
    const meta = el("div", "meta");
    const price = j.status === "error" ? null : (j.actual_usd != null ? j.actual_usd : j.estimate_usd);
    meta.innerHTML = `<b>${esc(j.character)} · ${esc(j.scene)}</b><span>${esc(j.engine_label.split(" (")[0])} · ${esc(j.tier)} · ${j.engine === "seedance" ? "whole clip" : j.chunks + " pass" + (j.chunks > 1 ? "es" : "")}${j.finishing ? " · Finish" : ""} · ${j.seeds_done}/${j.seeds}${price != null ? " · $" + price.toFixed(2) : ""}</span><span class="id">${j.id}</span>`;
    c.appendChild(meta);
    c.addEventListener("click", () => openJob(j.id));
    return c;
  }
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  function renderGrid() {
    const recent = $("#grid-recent"), all = $("#grid-all");
    const list = state.jobs.filter(matches);
    patch(recent, state.jobs.slice(0, 12)); $("#empty-recent").hidden = state.jobs.length > 0;
    patch(all, list); $("#empty-all").hidden = list.length > 0;
    state.jobs.forEach(j => { delete j._new; });
  }
  function patch(grid, jobs) {
    // Rebuild only cards whose state changed, so finished thumbnails do not flicker on every poll.
    const have = Object.fromEntries($$(".card", grid).map(c => [c.dataset.id, c]));
    const want = new Set(jobs.map(j => j.id));
    Object.keys(have).forEach(id => { if (!want.has(id)) have[id].remove(); });
    let prev = null;
    jobs.forEach(j => {
      const key = `${j.status}|${j.stage}|${Math.round(j.fraction * 100)}|${j.poster}|${j.seeds_done}|${j.actual_usd}`;
      let c = have[j.id];
      if (!c || c.dataset.key !== key) { const n = card(j); n.dataset.key = key; if (c) c.replaceWith(n); else grid.insertBefore(n, prev ? prev.nextSibling : grid.firstChild); c = n; }
      else if (c.previousSibling !== prev) grid.insertBefore(c, prev ? prev.nextSibling : grid.firstChild);
      prev = c;
    });
  }

  // ---------- drawer ----------
  async function openJob(id) {
    state.open = id;
    $("#scrim").hidden = false; $("#drawer").hidden = false;
    requestAnimationFrame(() => { $("#scrim").classList.add("show"); $("#drawer").classList.add("show"); });
    await refreshDrawer(true);
    $(".drawer-body").scrollTop = 0;
    $("#d-close").focus({ preventScroll: true });
  }
  function closeDrawer() {
    $("#scrim").classList.remove("show"); $("#drawer").classList.remove("show");
    const v = $("#d-video"); v.pause();
    setTimeout(() => { $("#scrim").hidden = true; $("#drawer").hidden = true; }, 250);
    state.open = null;
  }
  let drawerJob = null;
  async function refreshDrawer(first = false) {
    let j; try { j = await api("/api/jobs/" + state.open); } catch (e) { toast(e.message, true); return; }
    const changed = !drawerJob || drawerJob.status !== j.status || drawerJob.seeds_done !== j.seeds_done || first;
    drawerJob = j;
    $("#d-title").textContent = `${j.character} · ${j.scene}`;
    $("#d-sub").textContent = `${j.engine_label} · ${j.tier} · ${j.engine === "seedance" ? "whole clip" : j.chunks + " pass" + (j.chunks > 1 ? "es" : "")}${j.finishing ? " · Finish" : ""} · ${j.id}`;
    const working = j.status === "queued" || j.status === "running";
    $("#d-progress").hidden = !working;
    if (working) { $("#d-stage").textContent = j.stage; $("#d-pct").textContent = Math.round(j.fraction * 100) + "%"; $("#d-bar").style.width = Math.round(j.fraction * 100) + "%"; }
    if (!changed) return;
    // videos: compare first, then result and final per seed
    const vids = [];
    j.runs.forEach(r => r.outputs.forEach(o => { if (o.kind === "compare") vids.push({ label: `Compare · seed ${r.seed}`, url: o.url }); }));
    j.runs.forEach(r => r.outputs.forEach(o => { if (o.kind !== "compare") vids.push({ label: `${o.kind === "final" ? "Final" : "Result"} · seed ${r.seed}`, url: o.url }); }));
    const v = $("#d-video");
    seg($("#d-videos"), vids.map(x => ({ value: x.url, label: x.label })), vids.length ? vids[0].url : null, url => { v.src = url; v.play().catch(() => {}); });
    $("#d-videos").hidden = vids.length === 0;
    if (vids.length) { v.src = vids[0].url; } else { v.removeAttribute("src"); v.load(); }
    const sheets = $("#d-sheets"); sheets.innerHTML = "";
    j.runs.forEach(r => {
      const s = el("div", "seed");
      const head = el("div", "seed-head"); head.innerHTML = `<b>seed ${r.seed}</b><span>${r.status}${r.seconds ? " · " + Math.round(r.seconds / 60) + " min" : ""}</span>`;
      s.appendChild(head);
      r.sheets.forEach(sh => { const img = el("img"); img.src = sh.url; img.alt = sh.kind; img.loading = "lazy"; s.appendChild(img); });
      if (r.error) s.appendChild(el("div", "err", r.error));
      sheets.appendChild(s);
    });
    if (!j.runs.length) sheets.appendChild(el("div", "hint", "No seeds."));
    const files = $("#d-files"); files.innerHTML = "";
    j.runs.forEach(r => r.outputs.forEach(o => {
      const a = el("a", "file"); a.href = o.url; a.download = o.name; a.innerHTML = `${esc(o.name)}<span>${(o.bytes / 1e6).toFixed(1)} MB</span>`; files.appendChild(a);
    }));
    if (!files.children.length) files.appendChild(el("div", "hint", working ? "Files appear here when the render finishes." : "No files."));
    const kv = $("#d-kv"); kv.innerHTML = "";
    const rows = [["Status", j.status + (j.error ? ": " + j.error : "")], ["Character prompt", j.prompts.character], ["Motion prompt", j.prompts.motion], ["Render", j.settings_line],
                  ["Cost", `estimated $${(j.estimate_usd || 0).toFixed(2)}` + (j.actual_usd != null ? `, actual $${j.actual_usd.toFixed(2)}` : "")],
                  ["Inputs", `${j.inputs.image}, ${j.inputs.video}`], ["Workflow", j.workflow_version], ["Created", (j.created || "").replace("T", " ")]];
    if (j.note) rows.splice(1, 0, ["Note", j.note]);
    rows.forEach(([k, val]) => { kv.appendChild(el("dt", "", k)); kv.appendChild(el("dd", k === "Workflow" ? "mono" : "", val)); });
  }
  async function reuse() {
    const j = drawerJob; if (!j) return;
    closeDrawer(); location.hash = "#create";
    state.engine = j.engine; seg($("#seg-engine"), state.boot.engines.map(e => ({ value: e.key, label: e.label.split(" (")[0], title: e.label })), state.engine, v => { state.engine = v; engineHint(); preflight(); estimate(); }); engineHint();
    if (state.boot.characters[j.character] !== undefined) $("#sel-character").value = j.character;
    if (state.boot.scenes[j.scene]) $("#sel-scene").value = j.scene;
    $("#appearance").value = (j.prompts.character.match(/Character appearance description: (.*?)\. Background description:/) || [, ""])[1] || $("#appearance").value;
    $("#background").value = (j.prompts.character.match(/Background description: (.*)\.$/) || [, ""])[1] || $("#background").value;
    $("#motion").value = j.prompts.motion || "";
    $("#identity_hold").value = j.settings.identity_hold; $("#motion_looseness").value = j.settings.motion_looseness;
    state.tier = j.tier; seg($("#seg-tier"), Object.keys(state.boot.tiers).map(t => ({ value: t, label: t, title: state.boot.tiers[t].note })), state.tier, v => { state.tier = v; estimate(); });
    state.seeds = Math.min(4, Math.max(1, j.seeds)); seg($("#seg-seeds"), [1, 2, 3, 4].map(n => ({ value: n, label: String(n) })), state.seeds, v => { state.seeds = v; estimate(); });
    state.chunks = j.chunks; $("#finishing").checked = !!j.finishing; $("#note").value = j.note || "";
    await setInputsFromJob(j);
    toast("Settings and inputs loaded. Change what you want, then Render.");
  }

  // ---------- batch and costs ----------
  async function submitBatch(e) {
    e.preventDefault();
    const f = $("#batch-file").files[0]; if (!f) { $("#batch-result").textContent = "Choose a CSV first."; return; }
    const fd = new FormData(); fd.append("file", f); fd.append("tier", $("#batch-tier").value); fd.append("finishing", $("#batch-finishing").checked); fd.append("engine", $("#batch-engine").value);
    $("#batch-result").textContent = "Queueing";
    try {
      const r = await api("/api/batch", { method: "POST", body: fd });
      $("#batch-result").textContent = `${r.made.length} job(s) queued.` + (r.problems.length ? " Problems: " + r.problems.join("; ") : "");
      loadJobs();
    } catch (err) { $("#batch-result").textContent = err.message; }
  }
  async function loadCosts() {
    try {
      const c = await api("/api/costs");
      $("#costs-intro").textContent = `Per month, from the job history. Render $ is the GPU time the clips took at $${c.gpu_hourly_usd.toFixed(2)} per hour. Pod $ (est.) adds each day's span between jobs plus the watchdog's ${Math.round(c.idle_minutes)} minute idle allowance and a few minutes of start-up. Seedance $ is Comfy credits.`;
      const t = $("#costs-table");
      t.tHead.innerHTML = "<tr>" + c.headers.map(h => `<th>${esc(h)}</th>`).join("") + "</tr>";
      t.tBodies[0].innerHTML = c.rows.length ? c.rows.map(r => "<tr>" + r.map(x => `<td>${esc(x)}</td>`).join("") + "</tr>").join("") : `<tr><td colspan="${c.headers.length}">No finished jobs yet.</td></tr>`;
    } catch (e) { toast(e.message, true); }
  }

  // ---------- wiring ----------
  wireDrop("#drop-image", "#file-image", "ref");
  wireDrop("#drop-video", "#file-video", "drive");
  $("#file-view1").addEventListener("change", e => e.target.files[0] && upload(e.target.files[0], "view1"));
  $("#file-view2").addEventListener("change", e => e.target.files[0] && upload(e.target.files[0], "view2"));
  $("#btn-render").addEventListener("click", render);
  $("#btn-describe").addEventListener("click", describe);
  $("#comfy-keyform").addEventListener("submit", e => connectKey(e, "#comfy-key", "comfy_api_key"));
  $("#anthropic-keyform").addEventListener("submit", e => connectKey(e, "#anthropic-key", "anthropic_api_key"));
  $("#finishing").addEventListener("change", estimate);
  $("#allow_offload").addEventListener("change", renderButton);
  $("#d-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  $("#d-reuse").addEventListener("click", reuse);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && state.open) closeDrawer(); });
  $$("#filters button").forEach(b => b.addEventListener("click", () => { $$("#filters button").forEach(x => x.classList.remove("on")); b.classList.add("on"); state.filter = b.dataset.f; renderGrid(); }));
  $("#search").addEventListener("input", e => { state.search = e.target.value.trim(); renderGrid(); });
  $("#batch-form").addEventListener("submit", submitBatch);
  $("#batch-file").addEventListener("change", e => { const n = $("#batch-name"); n.hidden = !e.target.files[0]; n.textContent = e.target.files[0] ? e.target.files[0].name : ""; });
  $$("[data-view]").forEach(a => a.addEventListener("click", () => { /* hashchange handles it */ }));

  boot().catch(e => { toast("Could not load the app: " + e.message, true); $("#status-chip").textContent = "Error"; $("#status-chip").className = "chip off"; });
})();
