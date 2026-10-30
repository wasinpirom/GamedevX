/* GamedevX studio (project page) */
(async function () {
  "use strict";
  var api = GDX.api, esc = GDX.esc, toast = GDX.toast;
  var pid = +location.pathname.split("/").pop();
  var P = null;           // project data from server
  var story = null;       // working copy of the story
  var dirty = false;
  var tab = "script";
  var cfg, pollTimer = null, playerInst = null;
  var selected = {};      // asset selection: "char:cid:expr" / "bg:id"

  await GDX.loadMe();
  if (!GDX.me) { location.href = "/login?next=" + location.pathname; return; }
  GDX.renderNav();
  cfg = await GDX.loadCfg();

  // ------------------------------------------------------------ helpers
  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] != null) e.setAttribute(k, attrs[k]);
    }
    (children || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function select(options, value, onchange, cls) {
    var s = el("select", { class: cls || "" });
    options.forEach(function (o) {
      var op = el("option", { value: o[0], text: o[1] });
      if (String(o[0]) === String(value)) op.selected = true;
      s.appendChild(op);
    });
    s.onchange = function () { onchange(s.value); };
    return s;
  }
  function setDirty(v) {
    dirty = v;
    document.getElementById("dirty").style.display = v ? "inline" : "none";
    document.getElementById("save").style.display = v ? "inline-flex" : "none";
  }
  window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  function uid(prefix, list) {
    var i = list.length + 1;
    while (list.some(function (x) { return x.id === prefix + i; })) i++;
    return prefix + i;
  }
  function charName(id) { var c = story.characters.filter(function (x) { return x.id === id; })[0]; return c ? c.name : id; }

  // ------------------------------------------------------------ load + poll
  async function load(keepStory) {
    P = await api("GET", "/api/projects/" + pid);
    GDX.setCredits(P.credits);
    if (!keepStory || !story) story = P.story ? JSON.parse(JSON.stringify(P.story)) : null;
    document.getElementById("ptitle").textContent = story ? story.title : "โปรเจกต์ใหม่";
    document.title = (story ? story.title : "โปรเจกต์ใหม่") + " — GamedevX";
    renderJobs();
    renderTabs();
    render();
    schedulePoll();
  }

  function running() { return P.jobs.some(function (j) { return j.status === "queued" || j.status === "running"; }); }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!running()) return;
    pollTimer = setTimeout(async function () {
      var before = P.jobs.filter(function (j) { return j.status === "queued" || j.status === "running"; }).map(function (j) { return j.id; });
      var r = await api("GET", "/api/projects/" + pid + "/jobs").catch(function () { return null; });
      if (!r) return schedulePoll();
      var finished = r.jobs.filter(function (j) { return before.indexOf(j.id) >= 0 && j.status !== "queued" && j.status !== "running"; });
      P.jobs = r.jobs;
      renderJobs();
      if (finished.length) {
        finished.forEach(function (j) {
          if (j.status === "done") toast("งาน #" + j.id + " เสร็จแล้ว");
          else toast("งาน #" + j.id + " ล้มเหลว: " + j.error + (j.refunded ? " (คืน " + j.refunded + " เครดิตแล้ว)" : ""), true);
        });
        var storyChanged = finished.some(function (j) { return j.kind === "story" || j.kind === "rewrite_scene"; });
        if (storyChanged && dirty) toast("AI แก้เรื่องแล้ว แต่คุณมีการแก้ไขที่ยังไม่บันทึก — ใช้ฉบับของ AI", true);
        await load(!storyChanged);
        if (storyChanged) setDirty(false);
      } else schedulePoll();
    }, 2500);
  }

  var KIND = { story: "เขียนเรื่อง", rewrite_scene: "เขียนฉากใหม่", images: "สร้างภาพ", music: "สร้างเพลง", video: "เรนเดอร์วิดีโอ" };
  function renderJobs() {
    var box = document.getElementById("jobs");
    var show = P.jobs.filter(function (j) { return j.status === "queued" || j.status === "running" || (Date.now() / 1000 - j.created_at < 600); }).slice(0, 4);
    box.innerHTML = show.length ? '<div class="card" style="margin-top:12px;padding:8px 14px">' + show.map(function (j) {
      var st = j.status === "done" ? '<span class="tag ok">เสร็จ</span>' : j.status === "failed" ? '<span class="tag err">ล้มเหลว</span>' : '<span class="tag run"><span class="spinner"></span> กำลังทำ</span>';
      return '<div class="job">' + st + "<b>" + esc(KIND[j.kind] || j.kind) + "</b><span class='muted'>" + esc(j.progress || "") + (j.error ? " — " + esc(j.error) : "") + "</span><span class='sp' style='flex:1'></span><span class='small muted'>" + j.cost + " เครดิต" + (j.refunded ? " (คืน " + j.refunded + ")" : "") + "</span></div>";
    }).join("") + "</div>" : "";
  }

  // ------------------------------------------------------------ tabs
  var TABS = [["script", "บทเกม"], ["chars", "ตัวละคร"], ["bgs", "ฉากหลัง"], ["music", "เพลง"], ["play", "ทดลองเล่น"], ["export", "ส่งออก"]];
  function renderTabs() {
    var t = document.getElementById("tabs");
    t.innerHTML = "";
    if (!story) { t.style.display = "none"; return; }
    t.style.display = "flex";
    TABS.forEach(function (x) {
      t.appendChild(el("button", { class: tab === x[0] ? "on" : "", text: x[1], onclick: function () { tab = x[0]; renderTabs(); render(); } }));
    });
  }

  function render() {
    if (playerInst) { playerInst.destroy(); playerInst = null; }
    var v = document.getElementById("view");
    v.innerHTML = "";
    if (!story) return renderNoStory(v);
    ({ script: renderScript, chars: renderChars, bgs: renderBgs, music: renderMusic, play: renderPlay, export: renderExport })[tab](v);
  }

  // ------------------------------------------------------------ no story yet
  function renderNoStory(v) {
    var b = P.brief || {};
    var card = el("div", { class: "card", style: "margin-top:16px" });
    card.innerHTML = "<h2>ไอเดียของเกม</h2><p>" + esc(b.idea) + "</p><p class='small muted'>แนว " + esc(b.genre) + " · " + b.scenes + " ฉาก · " + b.characters + " ตัวละคร</p>";
    if (running()) card.appendChild(el("p", { html: '<span class="spinner"></span> AI กำลังเขียนเรื่อง รอสักครู่ (ประมาณ 1-2 นาที)...' }));
    else card.appendChild(el("button", { class: "btn", text: "ให้ AI เขียนเรื่อง", onclick: async function () {
      var j = await GDX.confirmAndRun(pid, "story", {}, "ให้ AI เขียนเรื่องและวางโครงเกม");
      if (j) load();
    } }));
    v.appendChild(card);
  }

  // ------------------------------------------------------------ script tab
  function renderScript(v) {
    var head = el("div", { class: "card" });
    var title = el("input", { value: story.title });
    title.oninput = function () { story.title = title.value; setDirty(true); };
    var syn = el("textarea", { rows: 2 }); syn.value = story.synopsis;
    syn.oninput = function () { story.synopsis = syn.value; setDirty(true); };
    head.appendChild(el("label", { text: "ชื่อเกม" })); head.appendChild(title);
    head.appendChild(el("label", { text: "เรื่องย่อ" })); head.appendChild(syn);
    head.appendChild(el("div", { class: "row", style: "margin-top:12px" }, [
      el("button", { class: "btn alt sm", text: "+ เพิ่มฉาก", onclick: addScene }),
      el("button", { class: "btn alt sm", text: "+ เพิ่มตัวละคร", onclick: function () { tab = "chars"; renderTabs(); render(); } }),
      el("button", { class: "btn alt sm", text: "แก้แบบ JSON", onclick: jsonEdit }),
      el("button", { class: "btn alt sm", text: "▶ เล่นตั้งแต่ต้น", onclick: function () { openPlay(); } })
    ]));
    v.appendChild(head);

    var sceneOpts = function () { return story.scenes.map(function (s) { return [s.id, s.title + " (" + s.id + ")"]; }); };
    var bgOpts = story.backgrounds.map(function (b) { return [b.id, b.name]; });
    var moodOpts = Object.keys(cfg.moods).map(function (m) { return [m, cfg.moods[m]]; });
    var charOpts = story.characters.map(function (c) { return [c.id, c.name]; });
    var exprOpts = Object.keys(cfg.expressions).map(function (e) { return [e, cfg.expressions[e]]; });

    story.scenes.forEach(function (s, si) {
      var card = el("div", { class: "card scene", style: "margin-top:14px" });
      var t = el("input", { value: s.title, style: "flex:1;min-width:160px;font-weight:600" });
      t.oninput = function () { s.title = t.value; setDirty(true); };
      card.appendChild(el("div", { class: "scene-head" }, [
        el("span", { class: "tag", text: "ฉาก " + (si + 1) }), t,
        select(bgOpts, s.bg, function (x) { s.bg = x; setDirty(true); }),
        select(moodOpts, s.mood, function (x) { s.mood = x; setDirty(true); }),
      ]));
      s.lines.forEach(function (ln, li) { card.appendChild(renderLine(s, ln, li, charOpts, exprOpts, sceneOpts)); });
      var add = function (obj) { return function () { s.lines.push(obj()); setDirty(true); render(); }; };
      card.appendChild(el("div", { class: "row", style: "margin-top:10px" }, [
        el("button", { class: "btn alt sm", text: "+ บรรยาย", onclick: add(function () { return { t: "narr", text: "" }; }) }),
        charOpts.length ? el("button", { class: "btn alt sm", text: "+ บทพูด", onclick: add(function () { return { t: "say", who: charOpts[0][0], expr: "neutral", text: "" }; }) }) : null,
        el("button", { class: "btn alt sm", text: "+ ทางเลือก", onclick: add(function () { return { t: "choice", options: [{ text: "ทางเลือก 1", goto: story.scenes[0].id }, { text: "ทางเลือก 2", goto: story.scenes[0].id }] }; }) }),
        el("button", { class: "btn alt sm", text: "+ ไปฉาก", onclick: add(function () { return { t: "goto", scene: story.scenes[0].id }; }) }),
        el("button", { class: "btn alt sm", text: "+ จบเกม", onclick: add(function () { return { t: "end" }; }) }),
        el("span", { style: "flex:1" }),
        el("button", { class: "btn cyan sm", text: "✨ ให้ AI เขียนฉากนี้ใหม่", onclick: function () { rewriteScene(s); } }),
        el("button", { class: "btn alt sm", text: "▶ เล่นจากฉากนี้", onclick: function () { openPlay(s.id); } }),
        el("button", { class: "btn danger sm", text: "ลบฉาก", onclick: function () {
          if (story.scenes.length < 2) return toast("ต้องมีอย่างน้อย 1 ฉาก", true);
          if (!confirm("ลบฉาก \"" + s.title + "\" ?")) return;
          story.scenes.splice(si, 1); setDirty(true); render();
        } })
      ]));
      v.appendChild(card);
    });
  }

  function renderLine(s, ln, li, charOpts, exprOpts, sceneOpts) {
    var row = el("div", { class: "line" });
    var del = el("button", { class: "btn alt sm", text: "✕", title: "ลบบรรทัด", onclick: function () { s.lines.splice(li, 1); setDirty(true); render(); } });
    var up = el("button", { class: "btn alt sm", text: "↑", title: "เลื่อนขึ้น", onclick: function () {
      if (li === 0) return; var x = s.lines[li - 1]; s.lines[li - 1] = ln; s.lines[li] = x; setDirty(true); render(); } });
    function ta() {
      var t = el("textarea", { rows: 1 }); t.value = ln.text || "";
      t.oninput = function () { ln.text = t.value; setDirty(true); };
      return t;
    }
    if (ln.t === "narr") {
      row.appendChild(el("div", { class: "who muted", text: "บรรยาย" })); row.appendChild(ta());
    } else if (ln.t === "say") {
      var who = el("div", { class: "who", style: "display:flex;flex-direction:column;gap:4px;min-width:130px" }, [
        select(charOpts, ln.who, function (x) { ln.who = x; setDirty(true); }),
        select(exprOpts, ln.expr, function (x) { ln.expr = x; setDirty(true); })
      ]);
      row.appendChild(who); row.appendChild(ta());
    } else if (ln.t === "choice") {
      var box = el("div", { style: "flex:1" });
      ln.options.forEach(function (o, oi) {
        var inp = el("input", { value: o.text });
        inp.oninput = function () { o.text = inp.value; setDirty(true); };
        box.appendChild(el("div", { class: "choice-opt" }, [inp, el("span", { class: "muted small", text: "→" }),
          select(sceneOpts(), o.goto, function (x) { o.goto = x; setDirty(true); }),
          el("button", { class: "btn alt sm", text: "✕", onclick: function () { if (ln.options.length > 1) { ln.options.splice(oi, 1); setDirty(true); render(); } } })]));
      });
      box.appendChild(el("button", { class: "btn alt sm", text: "+ ตัวเลือก", onclick: function () { ln.options.push({ text: "ทางเลือกใหม่", goto: s.id }); setDirty(true); render(); } }));
      row.appendChild(el("div", { class: "who", text: "ทางเลือก" })); row.appendChild(box);
    } else if (ln.t === "goto") {
      row.appendChild(el("div", { class: "who", text: "ไปที่ฉาก" }));
      row.appendChild(select(sceneOpts(), ln.scene, function (x) { ln.scene = x; setDirty(true); }));
    } else if (ln.t === "end") {
      row.appendChild(el("div", { class: "who", text: "— จบเกม —", style: "flex:1;color:var(--warn)" }));
    }
    row.appendChild(el("div", { style: "display:flex;gap:4px" }, [up, del]));
    return row;
  }

  function addScene() {
    var id = uid("s", story.scenes);
    story.scenes.push({ id: id, title: "ฉากใหม่", bg: story.backgrounds[0] ? story.backgrounds[0].id : null, mood: "calm", lines: [{ t: "narr", text: "" }] });
    setDirty(true); render();
  }

  async function rewriteScene(s) {
    if (dirty) { toast("กรุณาบันทึกก่อน แล้วค่อยให้ AI เขียนใหม่", true); return; }
    var m = GDX.modal('<h3>ให้ AI เขียนฉาก "' + esc(s.title) + '" ใหม่</h3><label>อยากให้ปรับอะไร (ไม่ใส่ก็ได้)</label><textarea id="ins" placeholder="เช่น ให้ตลกขึ้น เพิ่มฉากหักมุม ให้มะลิพูดมากขึ้น"></textarea><div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn alt" id="x">ยกเลิก</button><button class="btn" id="ok">ประเมินเครดิต →</button></div>');
    m.querySelector("#x").onclick = function () { m.remove(); };
    m.querySelector("#ok").onclick = async function () {
      var ins = m.querySelector("#ins").value; m.remove();
      var j = await GDX.confirmAndRun(pid, "rewrite_scene", { scene_id: s.id, instruction: ins }, "เขียนฉากใหม่ด้วย AI");
      if (j) load(true);
    };
  }

  function jsonEdit() {
    var m = GDX.modal('<h3>แก้ไขเรื่องแบบ JSON</h3><p class="small muted">สำหรับผู้ใช้ขั้นสูง รูปแบบเดียวกับ story.json ที่ส่งออก</p><textarea id="js" style="min-height:50vh;font-family:monospace;font-size:13px"></textarea><div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn alt" id="x">ยกเลิก</button><button class="btn" id="ok">ใช้ข้อมูลนี้</button></div>');
    m.querySelector("#js").value = JSON.stringify(story, null, 2);
    m.querySelector("#x").onclick = function () { m.remove(); };
    m.querySelector("#ok").onclick = function () {
      try { story = JSON.parse(m.querySelector("#js").value); } catch (e) { return toast("JSON ไม่ถูกต้อง: " + e.message, true); }
      m.remove(); setDirty(true); render();
    };
  }

  async function save() {
    try {
      var r = await api("PUT", "/api/projects/" + pid + "/story", { story: story });
      story = r.story; setDirty(false);
      toast("บันทึกแล้ว");
      await load(true);
    } catch (e) { toast(e.message, true); }
  }
  document.getElementById("save").onclick = save;

  async function ensureSaved() {
    if (!dirty) return true;
    if (!confirm("มีการแก้ไขที่ยังไม่บันทึก บันทึกก่อนไหม?")) return false;
    await save();
    return !dirty;
  }

  // ------------------------------------------------------------ assets
  function assetTile(kind, id, expr, label, url, wide) {
    var key = kind === "char" ? "char:" + id + ":" + expr : "bg:" + id;
    var cb = el("input", { type: "checkbox", class: "sel" });
    cb.checked = !!selected[key];
    cb.onchange = function () { if (cb.checked) selected[key] = 1; else delete selected[key]; updateGenBtn(); };
    var file = el("input", { type: "file", accept: "image/png,image/jpeg,image/webp", style: "display:none" });
    file.onchange = async function () {
      if (!file.files[0]) return;
      var fd = new FormData(); fd.append("kind", kind); fd.append("id", id); fd.append("expr", expr || "neutral"); fd.append("file", file.files[0]);
      try { await api("POST", "/api/projects/" + pid + "/upload-image", fd, true); toast("อัปโหลดแล้ว"); load(true); }
      catch (e) { toast(e.message, true); }
    };
    var acts = el("div", { class: "acts" }, [
      el("button", { text: "อัปโหลด", title: "ใช้ภาพของคุณเอง (ฟรี)", onclick: function () { file.click(); } }),
      url ? el("button", { text: "ลบ", onclick: async function () {
        if (!confirm("ลบภาพนี้?")) return;
        await api("DELETE", "/api/projects/" + pid + "/image?kind=" + kind + "&id=" + encodeURIComponent(id) + "&expr=" + (expr || "neutral"));
        load(true);
      } }) : null
    ]);
    var im = el("div", { class: "im" }, [url ? el("img", { src: url, loading: "lazy", alt: label }) : el("div", { class: "empty-im", text: "ยังไม่มีภาพ" })]);
    if (url) im.onclick = function () { window.open(url, "_blank"); };
    return el("div", { class: "asset" + (wide ? " wide" : "") }, [im, el("div", { class: "cap" }, [el("span", { text: label }), url ? el("span", { class: "tag ok", text: "✓" }) : null]), cb, acts, file]);
  }

  function genBar(kindLabel, onSelectMissing) {
    var bar = el("div", { class: "card row", style: "position:sticky;top:64px;z-index:5;margin-bottom:14px" });
    bar.appendChild(el("button", { class: "btn alt sm", text: "เลือกที่ยังไม่มีภาพทั้งหมด", onclick: function () { onSelectMissing(); render(); } }));
    bar.appendChild(el("button", { class: "btn alt sm", text: "ล้างที่เลือก", onclick: function () { selected = {}; render(); } }));
    bar.appendChild(el("span", { style: "flex:1" }));
    var b = el("button", { class: "btn", id: "gen-btn", onclick: generateSelected });
    bar.appendChild(b);
    bar.appendChild(el("div", { class: "small muted", style: "width:100%", text: "ติ๊กภาพที่ต้องการให้ AI สร้าง (ติ๊กภาพที่มีอยู่แล้ว = สร้างใหม่) · วางเมาส์บนภาพเพื่ออัปโหลดภาพของคุณเองฟรี" }));
    setTimeout(updateGenBtn);
    return bar;
  }
  function updateGenBtn() {
    var b = document.getElementById("gen-btn"); if (!b) return;
    var n = Object.keys(selected).length;
    b.textContent = n ? "✨ สร้างภาพที่เลือก (" + n + ")" : "เลือกภาพก่อน";
    b.disabled = !n;
  }
  async function generateSelected() {
    if (!(await ensureSaved())) return;
    var items = Object.keys(selected).map(function (k) {
      var p = k.split(":");
      return p[0] === "char" ? { kind: "char", id: p[1], expr: p[2] } : { kind: "bg", id: p[1] };
    });
    var j = await GDX.confirmAndRun(pid, "images", { items: items }, "สร้างภาพด้วย AI");
    if (j) { selected = {}; load(true); }
  }

  /* Reference image (photo or sketch) — AI draws the character/place to look like it */
  function refBox(kind, id, onUploaded) {
    var mt = (P.assets.refs || {})[kind + "_" + id];
    var url = mt ? "/files/" + pid + "/refs/" + kind + "_" + id + ".png?v=" + mt : null;
    var file = el("input", { type: "file", accept: "image/png,image/jpeg,image/webp", style: "display:none" });
    file.onchange = async function () {
      if (!file.files[0]) return;
      if (dirty && !(await ensureSaved())) return;
      var fd = new FormData(); fd.append("kind", kind); fd.append("id", id); fd.append("file", file.files[0]);
      try {
        await api("POST", "/api/projects/" + pid + "/upload-ref", fd, true);
        if (onUploaded) onUploaded();
        toast("เพิ่มภาพอ้างอิงแล้ว — เลือกภาพไว้ให้แล้ว กด \"สร้างภาพที่เลือก\" เพื่อวาดใหม่ตามภาพอ้างอิง");
        load(true);
      } catch (e) { toast(e.message, true); }
    };
    var thumb = url ? el("img", { src: url, alt: "ref", style: "cursor:zoom-in", onclick: function () { window.open(url, "_blank"); } }) : el("div", { class: "ph", text: "+" });
    return el("div", { class: "ref-box" }, [thumb, el("div", { style: "flex:1" }, [
      el("b", { text: "ภาพอ้างอิง " + (kind === "char" ? "(ภาพถ่ายหน้าตัวเอง หรือภาพสเก็ตช์ตัวละคร)" : "(ภาพถ่ายสถานที่ หรือภาพสเก็ตช์)") }),
      el("div", { class: "small muted", text: kind === "char"
        ? "AI จะวาดตัวละครให้หน้าตา ทรงผม และลักษณะคล้ายภาพนี้ในสไตล์ของเกม · ใช้ภาพของตัวเอง หรือภาพที่เจ้าของอนุญาตเท่านั้น"
        : "AI จะวาดฉากโดยคงองค์ประกอบหลักของภาพนี้ในสไตล์ของเกม" }),
      el("div", { class: "row", style: "margin-top:6px;gap:6px" }, [
        el("button", { class: "btn alt sm", text: url ? "เปลี่ยนภาพอ้างอิง" : "อัปโหลดภาพอ้างอิง", onclick: function () { file.click(); } }),
        url ? el("button", { class: "btn danger sm", text: "ลบ", onclick: async function () {
          await api("DELETE", "/api/projects/" + pid + "/ref?kind=" + kind + "&id=" + encodeURIComponent(id)); load(true);
        } }) : null])]), file]);
  }

  function renderChars(v) {
    v.appendChild(genBar("ตัวละคร", function () {
      story.characters.forEach(function (c) {
        (P.expressions_needed[c.id] || ["neutral"]).forEach(function (e) {
          if (!P.assets.chars[c.id + "_" + e]) selected["char:" + c.id + ":" + e] = 1;
        });
      });
    }));
    story.characters.forEach(function (c, ci) {
      var card = el("div", { class: "card", style: "margin-bottom:14px" });
      var name = el("input", { value: c.name, style: "max-width:220px;font-weight:600" });
      name.oninput = function () { c.name = name.value; setDirty(true); };
      var color = el("input", { type: "color", value: c.color, style: "width:52px;padding:2px;height:40px" });
      color.oninput = function () { c.color = color.value; setDirty(true); };
      var look = el("textarea", { rows: 2, placeholder: "ลักษณะภายนอก (ภาษาอังกฤษจะได้ผลดีที่สุด) เช่น teen girl, long black hair, red school uniform" });
      look.value = c.look;
      look.oninput = function () { c.look = look.value; setDirty(true); };
      card.appendChild(el("div", { class: "row" }, [name, color, el("span", { class: "muted small", text: "id: " + c.id }), el("span", { style: "flex:1" }),
        el("button", { class: "btn danger sm", text: "ลบตัวละคร", onclick: function () {
          var used = story.scenes.some(function (s) { return s.lines.some(function (l) { return l.t === "say" && l.who === c.id; }); });
          if (!confirm(used ? "ตัวละครนี้มีบทพูดอยู่ บทพูดจะกลายเป็นบรรยาย ยืนยันลบ?" : "ลบตัวละครนี้?")) return;
          story.characters.splice(ci, 1); setDirty(true); render();
        } })]));
      card.appendChild(el("label", { text: "ลักษณะภายนอก (ใช้เป็นคำสั่งวาดภาพ)" }));
      card.appendChild(look);
      var grid = el("div", { class: "asset-grid", style: "margin-top:12px" });
      var needed = P.expressions_needed[c.id] || ["neutral"];
      needed.forEach(function (e) {
        var mt = P.assets.chars[c.id + "_" + e];
        grid.appendChild(assetTile("char", c.id, e, cfg.expressions[e] || e, mt ? "/files/" + pid + "/chars/" + c.id + "_" + e + ".png?v=" + mt : null));
      });
      card.appendChild(grid);
      card.appendChild(refBox("char", c.id, function () {
        needed.forEach(function (e) { selected["char:" + c.id + ":" + e] = 1; });
      }));
      if (!P.expressions_needed[c.id]) card.appendChild(el("p", { class: "small muted", text: "บันทึกก่อนเพื่อสร้างภาพตัวละครใหม่นี้" }));
      v.appendChild(card);
    });
    v.appendChild(el("button", { class: "btn alt", text: "+ เพิ่มตัวละคร", onclick: function () {
      var id = uid("char", story.characters);
      story.characters.push({ id: id, name: "ตัวละครใหม่", color: "#9b59b6", look: "", personality: "" });
      setDirty(true); render();
    } }));
    v.appendChild(el("p", { class: "small muted", text: "สีหน้าที่แสดงคือสีหน้าที่ใช้ในบทเกม เปลี่ยนสีหน้าในแท็บบทเกมแล้วบันทึก ระบบจะเพิ่มช่องให้อัตโนมัติ" }));
  }

  function renderBgs(v) {
    v.appendChild(genBar("ฉากหลัง", function () {
      story.backgrounds.forEach(function (b) { if (!P.assets.bgs[b.id]) selected["bg:" + b.id] = 1; });
    }));
    story.backgrounds.forEach(function (b, bi) {
      var card = el("div", { class: "card", style: "margin-bottom:14px" });
      var name = el("input", { value: b.name, style: "max-width:260px;font-weight:600" });
      name.oninput = function () { b.name = name.value; setDirty(true); };
      var look = el("textarea", { rows: 2, placeholder: "รายละเอียดสถานที่ (ภาษาอังกฤษจะได้ผลดีที่สุด) เช่น old Thai temple at sunset, golden light" });
      look.value = b.look;
      look.oninput = function () { b.look = look.value; setDirty(true); };
      var mt = P.assets.bgs[b.id];
      var tile = assetTile("bg", b.id, null, b.name, mt ? "/files/" + pid + "/bgs/" + b.id + ".jpg?v=" + mt : null, true);
      tile.style.maxWidth = "420px";
      card.appendChild(el("div", { class: "grid g2" }, [tile, el("div", {}, [
        el("div", { class: "row" }, [name, el("span", { class: "muted small", text: "id: " + b.id }), el("span", { style: "flex:1" }),
          el("button", { class: "btn danger sm", text: "ลบ", onclick: function () {
            if (story.scenes.some(function (s) { return s.bg === b.id; })) return toast("ฉากหลังนี้ถูกใช้อยู่ เปลี่ยนในแท็บบทเกมก่อน", true);
            story.backgrounds.splice(bi, 1); setDirty(true); render();
          } })]),
        el("label", { text: "รายละเอียด (ใช้เป็นคำสั่งวาดภาพ)" }), look,
        refBox("bg", b.id, function () { selected["bg:" + b.id] = 1; })])]));
      v.appendChild(card);
    });
    v.appendChild(el("button", { class: "btn alt", text: "+ เพิ่มฉากหลัง", onclick: function () {
      story.backgrounds.push({ id: uid("place", story.backgrounds), name: "สถานที่ใหม่", look: "" });
      setDirty(true); render();
    } }));
  }

  // ------------------------------------------------------------ music
  async function renderMusic(v) {
    var gen = await api("GET", "/api/projects/" + pid + "/generated-music");
    var lib = P.music_library;
    var intro = el("div", { class: "card", style: "margin-bottom:14px" });
    intro.innerHTML = "<b>เพลงประกอบตามอารมณ์ฉาก</b><p class='muted small' style='margin:4px 0 0'>ระบบเลือกเพลงจากคลังให้อัตโนมัติ (ฟรี) เปลี่ยนเพลงได้ หรือให้ AI แต่งเพลงใหม่เฉพาะเกมนี้ (" + cfg.costs.music + " เครดิต/เพลง)</p>";
    v.appendChild(intro);
    if (!P.used_moods.length) v.appendChild(el("p", { class: "muted", text: "ยังไม่มีฉาก" }));
    P.used_moods.forEach(function (mood) {
      var entry = P.music_map[mood] || { type: "none" };
      var opts = [["none", "— ไม่มีเพลง —"]];
      lib.forEach(function (t) { opts.push(["lib:" + t.id, "[คลัง] " + (cfg.moods[t.mood] || t.mood) + " · " + t.title]); });
      gen.files.forEach(function (f) { opts.push(["gen:" + f, "[AI แต่งให้] " + f]); });
      var cur = entry.type === "lib" ? "lib:" + entry.id : entry.type === "gen" ? "gen:" + entry.file : "none";
      var url = entry.type === "lib" ? "/music/" + entry.id : entry.type === "gen" ? "/files/" + pid + "/music/" + entry.file : null;
      var audio = url ? el("audio", { controls: "", preload: "none", src: url, style: "width:100%;max-width:340px" }) : null;
      var sel = select(opts, cur, async function (x) {
        var body = { mood: mood, entry: x === "none" ? null : x.indexOf("lib:") === 0 ? { type: "lib", id: +x.slice(4) } : { type: "gen", file: x.slice(4) } };
        try { await api("PUT", "/api/projects/" + pid + "/music", body); toast("เปลี่ยนเพลงแล้ว"); load(true); } catch (e) { toast(e.message, true); }
      });
      sel.style.maxWidth = "380px";
      var scenes = story.scenes.filter(function (s) { return s.mood === mood; }).map(function (s) { return s.title; }).join(", ");
      v.appendChild(el("div", { class: "card", style: "margin-bottom:12px" }, [
        el("div", { class: "row" }, [el("b", { text: "อารมณ์: " + (cfg.moods[mood] || mood) }), el("span", { class: "small muted", text: "ใช้ในฉาก: " + scenes })]),
        el("div", { class: "row", style: "margin-top:10px" }, [sel, audio,
          el("button", { class: "btn cyan sm", text: "✨ ให้ AI แต่งเพลงใหม่", onclick: async function () {
            var j = await GDX.confirmAndRun(pid, "music", { mood: mood }, "แต่งเพลงใหม่ด้วย AI");
            if (j) load(true);
          } })])
      ]));
    });
    if (!lib.length) v.appendChild(el("p", { class: "small muted", text: "คลังเพลงยังว่าง (แอดมินอัปโหลดเพลงได้ที่หน้า Admin)" }));
  }

  // ------------------------------------------------------------ play
  function openPlay(sceneId) {
    if (dirty) { toast("บันทึกก่อนแล้วค่อยเล่น เพื่อให้เห็นการแก้ไขล่าสุด", true); return; }
    window.open("/play/" + pid + (sceneId ? "?scene=" + encodeURIComponent(sceneId) : ""), "_blank");
  }
  async function renderPlay(v) {
    if (dirty) v.appendChild(el("div", { class: "notice", text: "มีการแก้ไขที่ยังไม่บันทึก เกมด้านล่างเป็นฉบับที่บันทึกล่าสุด" }));
    var box = el("div", { class: "player-box" });
    v.appendChild(box);
    v.appendChild(el("div", { class: "row", style: "margin-top:12px" }, [
      el("button", { class: "btn", text: "เปิดเต็มจอ", onclick: function () { openPlay(); } }),
      el("span", { class: "small muted", text: "คลิกหรือกด Space เพื่อเล่นต่อ" })]));
    try {
      var data = await api("GET", "/api/projects/" + pid + "/gamedata");
      playerInst = GDXPlayer.mount(box, data, {});
    } catch (e) { box.innerHTML = '<p style="padding:20px">' + esc(e.message) + "</p>"; }
  }

  // ------------------------------------------------------------ export
  function renderExport(v) {
    var missingC = Object.keys(P.assets.chars).filter(function (k) { return !P.assets.chars[k]; }).length;
    var missingB = Object.keys(P.assets.bgs).filter(function (k) { return !P.assets.bgs[k]; }).length;
    var missingM = P.used_moods.filter(function (m) { return !P.music_map[m] || P.music_map[m].type === "none"; }).length;
    if (dirty) v.appendChild(el("div", { class: "notice", text: "บันทึกก่อนส่งออก เพื่อให้ได้ฉบับล่าสุด" }));
    if (missingC || missingB || missingM) v.appendChild(el("div", { class: "notice", html:
      "ยังขาด: " + [missingC ? "ภาพตัวละคร " + missingC + " ภาพ" : "", missingB ? "ภาพฉากหลัง " + missingB + " ภาพ" : "", missingM ? "เพลง " + missingM + " อารมณ์" : ""].filter(Boolean).join(", ") +
      " — ส่งออกได้เลย ส่วนที่ขาดจะแสดงเป็นภาพแทนชั่วคราว" }));
    var items = [
      ["renpy", "โปรเจกต์ Ren'Py", "ไฟล์บท .rpy ภาพ เพลง และฟอนต์ไทย เปิดใน Ren'Py แล้วส่งออกเป็นเกม PC / Android / Web ได้"],
      ["web", "เกมเว็บ (HTML)", "อัปโหลดขึ้นเว็บโฮสติ้งหรือ itch.io ได้ทันที หรือดับเบิลคลิก index.html เพื่อเล่น"],
      ["assets", "ชุด asset ล้วน", "ภาพตัวละคร PNG พื้นหลังใส ฉาก JPG เพลง และ story.json ใช้กับ Unity / Godot / pygame"]
    ];
    var grid = el("div", { class: "grid g3" });
    items.forEach(function (it) {
      grid.appendChild(el("div", { class: "card" }, [el("h3", { text: it[1] }), el("p", { class: "muted small", text: it[2] }),
        el("a", { class: "btn", href: "/api/projects/" + pid + "/export/" + it[0], text: "ดาวน์โหลด .zip" })]));
    });
    v.appendChild(grid);

    // ---- share to the community board
    v.appendChild(el("div", { class: "card", style: "margin-top:16px" }, [
      el("h3", { text: "แชร์ลงกระดาน GamedevX" }),
      el("p", { class: "muted small", text: "ให้เพื่อน ๆ เล่นเกมนี้บนหน้าแรกได้ทันทีโดยไม่ต้องสมัคร และแสดงความคิดเห็นแลกเปลี่ยนกัน (แก้เกมภายหลัง กระทู้จะอัปเดตตามอัตโนมัติ)" }),
      el("button", { class: "btn cyan", text: "แชร์เกมนี้", onclick: function () {
        var m = GDX.modal('<h3>แชร์ลงกระดาน</h3><label>หัวข้อ</label><input id="st" value="' + esc(story.title) + '"><label>รายละเอียด</label><textarea id="sb" rows="4" placeholder="เล่าที่มา อยากให้เพื่อนช่วยติชมเรื่องอะไร"></textarea><div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn alt" id="x">ยกเลิก</button><button class="btn" id="ok">แชร์</button></div>');
        m.querySelector("#x").onclick = function () { m.remove(); };
        m.querySelector("#ok").onclick = async function () {
          try {
            var fd = new FormData();
            fd.append("kind", "game"); fd.append("project_id", pid);
            fd.append("title", m.querySelector("#st").value); fd.append("body", m.querySelector("#sb").value);
            fd.append("category", "ผลงานเกม / แชร์เกม");
            var r = await api("POST", "/api/board", fd, true);
            location.href = "/board/" + r.id;
          } catch (e) { toast(e.message, true); }
        };
      } })]));

    // ---- MP4 video for YouTube
    var vcard = el("div", { class: "card", style: "margin-top:16px" });
    vcard.appendChild(el("h3", { text: "วิดีโอ MP4 สำหรับ YouTube" }));
    vcard.appendChild(el("p", { class: "muted small", text: "เรนเดอร์เรื่องราวในเกมเป็นวิดีโอ 1280×720 พร้อมเพลงประกอบ มีหน้าชื่อเรื่องและตอนจบ อัปโหลดขึ้น YouTube / TikTok / Facebook ได้เลย (" + cfg.costs.video + " เครดิต)" }));
    var route = {};
    var choiceScenes = story.scenes.filter(function (s) { return s.lines.some(function (l) { return l.t === "choice"; }); });
    if (choiceScenes.length) {
      vcard.appendChild(el("div", { class: "small", style: "font-weight:bold;margin-bottom:4px", text: "เลือกเส้นทางของเรื่องในวิดีโอ (เกมมีหลายทางเลือก)" }));
      choiceScenes.forEach(function (s) {
        var ch = s.lines.filter(function (l) { return l.t === "choice"; })[0];
        route[s.id] = 0;
        vcard.appendChild(el("div", { class: "row", style: "margin:4px 0" }, [
          el("span", { class: "small", style: "min-width:160px", text: "ฉาก \"" + s.title + "\" เลือก:" }),
          (function () { var sl = select(ch.options.map(function (o, i) { return [i, o.text]; }), 0, function (x) { route[s.id] = +x; }); sl.style.width = "auto"; sl.style.maxWidth = "100%"; return sl; })()]));
      });
    }
    var vlist = el("div", { style: "margin-top:10px" });
    vcard.appendChild(el("div", { class: "row", style: "margin-top:10px" }, [
      el("button", { class: "btn", text: "🎬 เรนเดอร์วิดีโอ", onclick: async function () {
        if (!(await ensureSaved())) return;
        var j = await GDX.confirmAndRun(pid, "video", { route: route }, "เรนเดอร์วิดีโอ MP4");
        if (j) load(true);
      } })]));
    vcard.appendChild(vlist);
    v.appendChild(vcard);
    api("GET", "/api/projects/" + pid + "/videos").then(function (r) {
      if (!r.videos.length) { vlist.innerHTML = '<p class="small muted">ยังไม่มีวิดีโอ</p>'; return; }
      vlist.innerHTML = '<video controls preload="metadata" src="' + esc(r.videos[0].url) + '" style="width:100%;max-width:640px;border-radius:6px;background:#000"></video>' +
        r.videos.map(function (x) {
          return '<div class="small" style="margin-top:4px">📹 <a href="' + esc(x.url) + '" download>' + esc(x.name) + "</a> · " + (x.size / 1048576).toFixed(1) + " MB · " + GDX.fmtDate(x.created_at) + "</div>";
        }).join("") + '<p class="small muted">เก็บไว้ 3 ไฟล์ล่าสุด · ไอเดียชื่อคลิป: "' + esc(story.title) + ' | เกม Visual Novel สร้างด้วย AI — GamedevX"</p>';
    });

    v.appendChild(el("div", { class: "card", style: "margin-top:16px" }, [
      el("h3", { text: "จัดการโปรเจกต์" }),
      el("button", { class: "btn danger", text: "ลบโปรเจกต์นี้", onclick: async function () {
        if (!confirm("ลบโปรเจกต์และไฟล์ทั้งหมดถาวร?")) return;
        try { await api("DELETE", "/api/projects/" + pid); location.href = "/app"; } catch (e) { toast(e.message, true); }
      } })]));
  }

  try { await load(); } catch (e) { document.getElementById("view").innerHTML = '<div class="card">' + esc(e.message) + "</div>"; }
})();
