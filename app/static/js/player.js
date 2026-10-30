/* GamedevX visual novel web player.
 * Usage: GDXPlayer.mount(element, gameData, {startScene})
 * gameData = {story, assets: {chars: {"<cid>_<expr>": url}, bgs: {id: url}, music: {mood: url}}}
 * Works standalone (exported web game) and inside the GamedevX studio.
 */
(function () {
  "use strict";

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function positions(n) {
    if (n === 1) return ["center"];
    if (n === 2) return ["left", "right"];
    return ["left", "center", "right", "left", "center", "right"];
  }

  function mount(root, data, opts) {
    opts = opts || {};
    var story = data.story;
    var A = data.assets || { chars: {}, bgs: {}, music: {} };
    var chars = {};
    story.characters.forEach(function (c) { chars[c.id] = c; });
    var sceneIdx = {};
    story.scenes.forEach(function (s, i) { sceneIdx[s.id] = i; });
    var isThai = /[฀-๿]/.test(story.title + story.synopsis);
    var T = isThai
      ? { start: "เริ่มเกม", end: "— จบ —", again: "เล่นอีกครั้ง", menu: "เมนู", mute: "เสียง", auto: "อัตโนมัติ" }
      : { start: "Start", end: "— THE END —", again: "Play again", menu: "Menu", mute: "Sound", auto: "Auto" };

    root.innerHTML = "";
    var wrap = h("div", "gdx-wrap");
    var stage = h("div", "gdx-stage");
    var bg = h("div", "gdx-bg");
    var layer = h("div", "gdx-chars");
    var box = h("div", "gdx-box");
    var nameEl = h("div", "gdx-name");
    var textEl = h("div", "gdx-text");
    var nextEl = h("div", "gdx-next", "▼");
    var choicesEl = h("div", "gdx-choices");
    var title = h("div", "gdx-title");
    var bar = h("div", "gdx-bar");
    box.appendChild(nameEl); box.appendChild(textEl); box.appendChild(nextEl);
    stage.appendChild(bg); stage.appendChild(layer); stage.appendChild(box);
    stage.appendChild(choicesEl); stage.appendChild(bar); stage.appendChild(title);
    wrap.appendChild(stage); root.appendChild(wrap);

    var btnMenu = h("button", "", T.menu);
    var btnAuto = h("button", "", T.auto);
    var btnMute = h("button", "", "♪ " + T.mute);
    bar.appendChild(btnAuto); bar.appendChild(btnMute); bar.appendChild(btnMenu);

    // ---- sizing: keep 16:9 inside the container
    function fit() {
      var w = root.clientWidth || window.innerWidth;
      var hgt = root.clientHeight || window.innerHeight;
      if (!root.clientHeight) hgt = w * 9 / 16;
      var sw = Math.min(w, hgt * 16 / 9);
      stage.style.width = sw + "px";
      stage.style.height = (sw * 9 / 16) + "px";
      stage.style.fontSize = (sw / 1920 * 34) + "px";
    }
    fit();
    window.addEventListener("resize", fit);

    // ---- music with crossfade
    var audio = null, audioUrl = null, muted = false;
    function fade(a, to, ms, done) {
      var from = a.volume, t0 = performance.now();
      function tick(t) {
        var k = Math.min(1, (t - t0) / ms);
        a.volume = Math.max(0, Math.min(1, from + (to - from) * k));
        if (k < 1) requestAnimationFrame(tick); else if (done) done();
      }
      requestAnimationFrame(tick);
    }
    function playMusic(mood) {
      var url = A.music && A.music[mood];
      if (!url || url === audioUrl) return;
      var old = audio;
      if (old) fade(old, 0, 800, function () { old.pause(); });
      audioUrl = url;
      audio = new Audio(url);
      audio.loop = true;
      audio.volume = 0;
      audio.muted = muted;
      var p = audio.play();
      if (p && p.catch) p.catch(function () {});
      fade(audio, 0.6, 1200);
    }
    function stopMusic() {
      if (audio) { var a = audio; fade(a, 0, 600, function () { a.pause(); }); }
      audio = null; audioUrl = null;
    }
    btnMute.onclick = function (e) {
      e.stopPropagation();
      muted = !muted;
      if (audio) audio.muted = muted;
      btnMute.classList.toggle("off", muted);
    };

    // ---- state
    var scene = null, li = 0, waiting = null, typing = null, auto = false, autoTimer = null;
    var shown = {}, pos = {};

    function setBg(id) {
      var url = id && A.bgs[id];
      bg.style.backgroundImage = url ? "url('" + url + "')" : "";
      bg.classList.toggle("gdx-bg-missing", !url);
      bg.setAttribute("data-label", url ? "" : (story.backgrounds.filter(function (b) { return b.id === id; })[0] || {}).name || "");
    }

    function showChar(cid, expr) {
      var key = cid + "_" + expr;
      var el = layer.querySelector('[data-cid="' + cid + '"]');
      if (!el) {
        el = h("div", "gdx-char gdx-pos-" + (pos[cid] || "center"));
        el.setAttribute("data-cid", cid);
        layer.appendChild(el);
        requestAnimationFrame(function () { el.classList.add("in"); });
      }
      if (shown[cid] === expr) return;
      shown[cid] = expr;
      el.innerHTML = "";
      var url = A.chars[key] || A.chars[cid + "_neutral"];
      if (url) {
        var img = h("img");
        img.src = url;
        img.alt = chars[cid].name;
        el.appendChild(img);
      } else {
        var ph = h("div", "gdx-ph", chars[cid].name + " (" + expr + ")");
        ph.style.background = chars[cid].color + "99";
        el.appendChild(ph);
      }
      layer.querySelectorAll(".gdx-char").forEach(function (c) {
        c.classList.toggle("dim", c.getAttribute("data-cid") !== cid);
      });
    }

    function typeText(text, done) {
      clearInterval(typing);
      textEl.textContent = "";
      nextEl.style.visibility = "hidden";
      var chars_ = Array.from(text), i = 0;
      typing = setInterval(function () {
        i += 2;
        textEl.textContent = chars_.slice(0, i).join("");
        if (i >= chars_.length) { clearInterval(typing); typing = null; nextEl.style.visibility = "visible"; done(); }
      }, 22);
      waiting = function () {
        if (typing) { clearInterval(typing); typing = null; textEl.textContent = text; nextEl.style.visibility = "visible"; done(); return true; }
        return false;
      };
    }

    function say(name, color, text) {
      box.style.display = "block";
      nameEl.textContent = name || "";
      nameEl.style.display = name ? "inline-block" : "none";
      nameEl.style.background = color || "";
      typeText(text, function () {
        waiting = null;
        if (auto) { clearTimeout(autoTimer); autoTimer = setTimeout(advance, 1400 + text.length * 35); }
      });
    }

    function enterScene(id) {
      var i = sceneIdx[id];
      if (i == null) return finish();
      scene = story.scenes[i];
      li = 0;
      shown = {}; layer.innerHTML = "";
      var speakers = [];
      scene.lines.forEach(function (l) { if (l.t === "say" && speakers.indexOf(l.who) < 0) speakers.push(l.who); });
      pos = {};
      var p = positions(speakers.length);
      speakers.forEach(function (s, k) { pos[s] = p[k]; });
      stage.classList.add("gdx-fade");
      setTimeout(function () { stage.classList.remove("gdx-fade"); }, 400);
      setBg(scene.bg);
      playMusic(scene.mood);
      step();
    }

    function nextScene() {
      var i = sceneIdx[scene.id] + 1;
      if (i < story.scenes.length) enterScene(story.scenes[i].id); else finish();
    }

    function step() {
      choicesEl.innerHTML = "";
      if (li >= scene.lines.length) return nextScene();
      var l = scene.lines[li++];
      if (l.t === "narr") return say("", "", l.text);
      if (l.t === "say") {
        var c = chars[l.who];
        if (!c) return step();
        showChar(l.who, l.expr);
        return say(c.name, c.color, l.text);
      }
      if (l.t === "choice") {
        clearTimeout(autoTimer);
        waiting = function () { return true; };
        l.options.forEach(function (o) {
          var b = h("button", "gdx-choice", o.text);
          b.onclick = function (e) {
            e.stopPropagation();
            waiting = null;
            choicesEl.innerHTML = "";
            if (o.goto) enterScene(o.goto); else finish();
          };
          choicesEl.appendChild(b);
        });
        return;
      }
      if (l.t === "goto") return enterScene(l.scene);
      if (l.t === "end") return finish();
      step();
    }

    function advance() {
      clearTimeout(autoTimer);
      if (!scene) return;
      if (waiting && waiting()) return;
      if (choicesEl.children.length) return;
      step();
    }

    function finish() {
      clearTimeout(autoTimer);
      scene = null;
      box.style.display = "none";
      choicesEl.innerHTML = "";
      showTitle(true);
    }

    function showTitle(ended) {
      title.innerHTML = "";
      title.style.display = "flex";
      var t = h("h1", "", ended ? T.end : story.title);
      title.appendChild(t);
      if (!ended && story.synopsis) title.appendChild(h("p", "", story.synopsis));
      var b = h("button", "gdx-start", ended ? T.again : T.start);
      b.onclick = function (e) {
        e.stopPropagation();
        title.style.display = "none";
        if (ended) stopMusic();
        enterScene(opts.startScene && !ended ? opts.startScene : story.scenes[0].id);
      };
      title.appendChild(b);
      if (story.scenes[0] && story.scenes[0].bg && A.bgs[story.scenes[0].bg]) {
        title.style.backgroundImage = "linear-gradient(rgba(10,8,30,.55),rgba(10,8,30,.85)),url('" + A.bgs[story.scenes[0].bg] + "')";
      }
    }

    stage.addEventListener("click", function (e) {
      if (e.target.closest(".gdx-bar") || e.target.closest(".gdx-title")) return;
      advance();
    });
    root.tabIndex = 0;
    root.addEventListener("keydown", function (e) {
      if (e.key === " " || e.key === "Enter") { e.preventDefault(); advance(); }
    });
    btnAuto.onclick = function (e) {
      e.stopPropagation();
      auto = !auto;
      btnAuto.classList.toggle("on", auto);
      if (auto && !waiting) advance();
    };
    btnMenu.onclick = function (e) {
      e.stopPropagation();
      clearTimeout(autoTimer); clearInterval(typing);
      scene = null; box.style.display = "none"; choicesEl.innerHTML = ""; layer.innerHTML = "";
      stopMusic();
      showTitle(false);
    };

    box.style.display = "none";
    showTitle(false);
    return {
      destroy: function () { stopMusic(); clearTimeout(autoTimer); clearInterval(typing); root.innerHTML = ""; window.removeEventListener("resize", fit); }
    };
  }

  window.GDXPlayer = { mount: mount };
})();
