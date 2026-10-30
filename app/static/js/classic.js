/* GamedevX classic frame: header, member box, left ads, right history, footer.
 * Requires common.js (GDX). Page markup provides #cx-head, #cx-left, #cx-right, #cx-foot.
 */
var CX = (function () {
  "use strict";
  var esc = GDX.esc;

  function adHtml(b, cls) {
    var inner = (b.image_url ? '<img src="' + esc(b.image_url) + '" alt="' + esc(b.title || "") + '" loading="lazy">' : "") +
      (b.title || b.caption ? "<span>" + (b.title ? "<b>" + esc(b.title) + "</b> " : "") + esc(b.caption || "") + "</span>" : "");
    return b.go_url ? '<a class="' + cls + '" href="' + esc(b.go_url) + '" target="_blank" rel="noopener">' + inner + "</a>"
                    : '<div class="' + cls + '">' + inner + "</div>";
  }

  async function init() {
    await GDX.loadMe();
    var me = GDX.me;
    var cfg = await GDX.loadCfg();
    var B = await GDX.loadBanners();
    if (cfg.site_title && !document.body.getAttribute("data-keep-title")) document.title = cfg.site_title;

    // ---- header: logo (uploaded image or wordmark) + header art
    var head = document.getElementById("cx-head");
    var logo = (B.logo || [])[0];
    var art = (B.header || [])[0];
    head.innerHTML =
      '<a id="cx-logo" href="/">' + (logo && logo.image_url ? '<img src="' + esc(logo.image_url) + '" alt="GamedevX">'
        : '<span class="cx-wordmark"><span class="g">G</span>amedev<span class="x">X</span></span>') + "</a>" +
      (art && art.image_url ? '<div id="cx-head-art">' + (art.go_url ? '<a href="' + esc(art.go_url) + '" target="_blank" rel="noopener">' : "") +
        '<img src="' + esc(art.image_url) + '" alt="">' + (art.go_url ? "</a>" : "") + "</div>"
        : '<div id="cx-tag">' + esc(cfg.site_tagline || "") + "</div>");

    // ---- left: member box
    var left = document.getElementById("cx-left");
    var member;
    if (me) {
      member = "สวัสดีคุณ <b>" + esc(me.name) + "</b><br>รหัสสมาชิก #" + me.id + '<br>เครดิต <b style="color:#c00">' + me.credits + "</b>" +
        '<ul class="cx-menu"><li><a href="/app#new">สร้างเกมด้วย AI</a></li><li><a href="/app">โปรเจกต์ของฉัน</a></li>' +
        '<li><a href="/topup">เติมเครดิต</a></li>' + (me.is_admin ? '<li><a href="/admin">หลังบ้าน (Admin)</a></li>' : "") +
        '<li><a href="#" id="cx-logout">ออกจากระบบ</a></li></ul>';
    } else {
      member = '<form id="cx-login"><label>username</label><input type="text" name="u" autocomplete="username">' +
        '<label>password</label><input type="password" name="p" autocomplete="current-password">' +
        '<div class="rm"><input type="checkbox" checked> Remember</div><button class="cx-btn">Sign In</button></form>' +
        '<div style="margin-top:10px"><a href="/login?mode=register">สมาชิกใหม่โปรดใช้บริการ GamedevX</a>' +
        (cfg.welcome_credits ? '<br><span style="color:#c00">รับฟรี ' + cfg.welcome_credits + " เครดิต สร้างเกมด้วย AI</span>" : "") + "</div>";
    }
    var lads = (B.left || []).map(function (b) { return adHtml(b, "cx-ad"); }).join("");
    left.innerHTML = '<div class="cx-box"><div class="cx-box-h">GamedevX Member</div><div class="cx-box-b">' + member + "</div></div>" +
      (lads ? '<div class="cx-section-t">' + esc(cfg.left_title || "") + '</div><div class="cx-left-ads">' + lads + "</div>" : "");
    var lf = document.getElementById("cx-login");
    if (lf) lf.onsubmit = async function (e) {
      e.preventDefault();
      try { await GDX.api("POST", "/api/login", { email: lf.u.value, password: lf.p.value }); location.reload(); }
      catch (err) { GDX.toast(err.message, true); }
    };
    var lo = document.getElementById("cx-logout");
    if (lo) lo.onclick = async function (e) { e.preventDefault(); await GDX.api("POST", "/api/logout"); location.href = "/"; };

    // ---- right: history / news
    var right = document.getElementById("cx-right");
    right.innerHTML = '<div class="cx-box"><div class="cx-box-h">' + esc(cfg.history_title || "") + "</div><div>" +
      ((B.right || []).map(function (b) {
        var inner = (b.image_url ? '<img src="' + esc(b.image_url) + '" alt="" loading="lazy">' : "") +
          (b.title ? "<b>" + esc(b.title) + "</b> - " : "") + esc(b.caption || "");
        return b.go_url ? '<a class="cx-hist" style="display:block" href="' + esc(b.go_url) + '" target="_blank" rel="noopener">' + inner + "</a>"
                        : '<div class="cx-hist">' + inner + "</div>";
      }).join("") || '<div class="cx-hist">—</div>') + "</div></div>";

    var foot = document.getElementById("cx-foot");
    if (foot) foot.innerHTML = "GamedevX New Generation © โดย อ.ดร.วศิน ภิรมย์ · ติดต่อ LINE OA <a href=\"" + esc(cfg.line_url) + "\" target=\"_blank\" rel=\"noopener\">" + esc(cfg.line_oa) + "</a>";
    return { me: me, cfg: cfg, banners: B };
  }

  function fmtDate(ts) {
    return new Date(ts * 1000).toLocaleDateString("th-TH", { day: "numeric", month: "short", year: "2-digit" });
  }
  function fmtFull(ts) {
    return new Date(ts * 1000).toLocaleString("th-TH", { day: "numeric", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  /* text with URLs turned into links (all text escaped first) */
  function richText(s) {
    return esc(s).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener nofollow">$1</a>');
  }

  return { init: init, fmtDate: fmtDate, fmtFull: fmtFull, richText: richText };
})();
