/* Shared helpers for GamedevX pages */
var GDX = (function () {
  "use strict";
  var me = null, cfg = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function api(method, url, body, isForm) {
    var opt = { method: method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      if (isForm) opt.body = body;
      else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    }
    var r = await fetch(url, opt);
    var data = null;
    try { data = await r.json(); } catch (e) { data = {}; }
    if (!r.ok) {
      var err = new Error((data && data.detail) || ("HTTP " + r.status));
      err.status = r.status;
      throw err;
    }
    return data;
  }

  function toast(msg, isErr) {
    var box = document.getElementById("toast");
    if (!box) { box = document.createElement("div"); box.id = "toast"; document.body.appendChild(box); }
    var t = document.createElement("div");
    t.className = "toast" + (isErr ? " err" : "");
    t.textContent = msg;
    box.appendChild(t);
    setTimeout(function () { t.remove(); }, isErr ? 6000 : 3200);
  }

  function modal(html) {
    var bg = document.createElement("div");
    bg.className = "modal-bg";
    bg.innerHTML = '<div class="modal">' + html + "</div>";
    document.body.appendChild(bg);
    bg.addEventListener("click", function (e) { if (e.target === bg) bg.remove(); });
    return bg;
  }

  function fmtDate(ts) {
    var d = new Date(ts * 1000);
    return d.toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" });
  }

  async function loadMe() {
    var r = await api("GET", "/api/me");
    me = r.user;
    return me;
  }

  async function loadCfg() {
    if (!cfg) cfg = await api("GET", "/api/public-config");
    return cfg;
  }

  var bannerPromise = null;
  function loadBanners() {
    if (!bannerPromise) bannerPromise = api("GET", "/api/banners").catch(function () { return { header: [], center: [], left: [], right: [] }; });
    return bannerPromise;
  }

  function bannerHtml(b, cls) {
    var inner = (b.image_url ? '<img src="' + esc(b.image_url) + '" alt="' + esc(b.title || "banner") + '" loading="lazy">' : "") +
      (b.title ? "<b>" + esc(b.title) + "</b>" : "") + (b.caption ? "<span>" + esc(b.caption) + "</span>" : "");
    var link = b.go_url || b.link_url ? esc(b.go_url || b.link_url) : "";
    var ext = b.go_url || /^https?:/.test(b.link_url || "") ? ' target="_blank" rel="noopener"' : "";
    return link ? '<a class="' + cls + '" href="' + link + '"' + ext + ">" + inner + "</a>" : '<div class="' + cls + '">' + inner + "</div>";
  }

  function renderNav(active) {
    var nav = document.getElementById("nav");
    if (!nav) return;
    var right = me
      ? '<a class="credit-pill" href="/topup" title="เติมเครดิต">เครดิต <b id="nav-credits">' + me.credits + "</b></a>" +
        '<a class="link" href="#" id="logout">ออกจากระบบ</a>'
      : '<a class="link" href="/login">เข้าสู่ระบบ</a><a class="btn sm" href="/login?mode=register">สมัครสมาชิก</a>';
    nav.innerHTML =
      '<div class="gdx-header"><div class="container">' +
        '<a class="logo" href="/"><span class="g">G</span>amedev<span class="x">X</span></a>' +
        '<div class="header-tag" id="hdr-tag"></div>' +
        '<div class="header-banner" id="hdr-banner"></div>' +
      "</div></div>" +
      '<div class="menubar"><div class="container">' +
        '<a class="link" href="/">หน้าแรก</a>' +
        '<a class="link" href="/#board">กระดานเกม</a>' +
        (me ? '<a class="link" href="/app">โปรเจกต์ของฉัน</a>' : "") +
        '<a class="link" href="/app#new">สร้างเกมด้วย AI</a>' +
        '<a class="link" href="/topup">เติมเครดิต</a>' +
        (me && me.is_admin ? '<a class="link" href="/admin">หลังบ้าน</a>' : "") +
        '<span class="sp"></span>' + right +
      "</div></div>";
    var lo = document.getElementById("logout");
    if (lo) lo.onclick = async function (e) { e.preventDefault(); await api("POST", "/api/logout"); location.href = "/"; };
    loadCfg().then(function (c) { var t = document.getElementById("hdr-tag"); if (t) t.textContent = c.site_tagline || ""; });
    loadBanners().then(function (b) {
      var h = document.getElementById("hdr-banner");
      var lg = document.querySelector(".gdx-header .logo");
      if (lg && b.logo && b.logo.length && b.logo[0].image_url) lg.innerHTML = '<img src="' + esc(b.logo[0].image_url) + '" alt="GamedevX" style="max-height:110px;display:block">';
      if (h && b.header && b.header.length) h.innerHTML = bannerHtml({ image_url: b.header[0].image_url, go_url: b.header[0].go_url, title: b.header[0].image_url ? "" : b.header[0].title }, "hb");
    });
  }

  function fmtShort(ts) {
    return new Date(ts * 1000).toLocaleDateString("th-TH", { day: "numeric", month: "short", year: "2-digit" });
  }

  function setCredits(n) {
    if (me) me.credits = n;
    var el = document.getElementById("nav-credits");
    if (el) el.textContent = n;
  }

  /* Quote → confirm → start job. The heart of the credit UX. */
  async function confirmAndRun(pid, kind, payload, title) {
    var q;
    try { q = await api("POST", "/api/projects/" + pid + "/quote", { kind: kind, payload: payload }); }
    catch (e) { toast(e.message, true); return null; }
    return new Promise(function (resolve) {
      var lines = q.lines.map(function (l) { return "<div>• " + esc(l) + "</div>"; }).join("");
      var after = q.balance - q.cost;
      var m = modal(
        "<h3>" + esc(title || "ยืนยันการใช้เครดิต") + "</h3>" +
        '<div class="quote-lines">' + lines +
        '<div class="quote-total"><span>รวม</span><span>' + q.cost + " เครดิต</span></div></div>" +
        '<div class="small muted">เครดิตคงเหลือ ' + q.balance + " → หลังใช้ <b>" + after + "</b></div>" +
        '<div class="small muted" style="margin-top:6px">ถ้างานล้มเหลว ระบบคืนเครดิตให้อัตโนมัติ</div>' +
        (q.enough ? "" : '<div class="notice">เครดิตไม่พอ <a href="/topup">เติมเครดิต</a></div>') +
        '<div class="row" style="justify-content:flex-end;margin-top:16px">' +
        '<button class="btn alt" data-x>ยกเลิก</button>' +
        '<button class="btn" data-ok ' + (q.enough ? "" : "disabled") + ">ตกลง ใช้ " + q.cost + " เครดิต</button></div>"
      );
      m.querySelector("[data-x]").onclick = function () { m.remove(); resolve(null); };
      m.querySelector("[data-ok]").onclick = async function () {
        this.disabled = true;
        try {
          var r = await api("POST", "/api/projects/" + pid + "/jobs", { kind: kind, payload: q.payload, expected_cost: q.cost });
          setCredits(after);
          m.remove();
          toast("เริ่มงานแล้ว กำลังทำงาน...");
          resolve(r.job_id);
        } catch (e) {
          toast(e.message, true);
          this.disabled = false;
          if (e.status === 409 || e.status === 402) { m.remove(); resolve(null); }
        }
      };
    });
  }

  return {
    api: api, esc: esc, toast: toast, modal: modal, fmtDate: fmtDate,
    loadMe: loadMe, loadCfg: loadCfg, renderNav: renderNav, setCredits: setCredits,
    loadBanners: loadBanners, bannerHtml: bannerHtml, fmtShort: fmtShort,
    confirmAndRun: confirmAndRun,
    get me() { return me; }
  };
})();
