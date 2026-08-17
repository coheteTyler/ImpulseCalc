/** Standalone chrome: this-app section header + Tesla sibling rail + Share. */
(function (global) {
  var SHARE_BASE = "https://coheteTyler.github.io/app-share";
  var APPS = [
    { id: "impulsecalc", name: "ImpulseCalc", short: "IC", local: "http://127.0.0.1:8765/calc.html", health: "http://127.0.0.1:8765/api/health", share: "impulsecalc", protocol: "impulsecalc" },
    { id: "cycle", name: "Cycle", short: "Cy", local: "http://127.0.0.1:8766/", health: "http://127.0.0.1:8766/api/health", share: "cycle", protocol: "lpre-cycle" },
    { id: "pump", name: "Pump", short: "Pu", local: "http://127.0.0.1:8767/", health: "http://127.0.0.1:8767/api/health", share: "pump", protocol: "lpre-pump" },
    { id: "powerhead", name: "Powerhead", short: "PH", local: "http://127.0.0.1:8768/", health: "http://127.0.0.1:8768/api/health", share: "powerhead", protocol: "lpre-powerhead" },
    { id: "flight", name: "Flight", short: "Fl", local: "http://127.0.0.1:8769/", health: "http://127.0.0.1:8769/api/health", share: "flight", protocol: "lpre-flight" },
    { id: "library", name: "Library", short: "LP", local: "http://127.0.0.1:8770/", health: "http://127.0.0.1:8770/api/health", share: "library", protocol: "lpre-library" },
    { id: "structures", name: "Structures", short: "St", local: "http://127.0.0.1:8771/", health: "http://127.0.0.1:8771/api/health", share: "structures", protocol: "lpre-structures" },
    { id: "tapin", name: "Tap-In", short: "TI", local: "http://127.0.0.1:8501/", health: "http://127.0.0.1:8501/", share: "tapin-workbench", protocol: "tapin" },
  ];

  var state = { currentId: "", up: {}, sections: [] };

  function shareUrl(app) {
    return SHARE_BASE + "/" + app.share + "/";
  }

  function toast(msg) {
    var el = document.getElementById("ac_toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "ac_toast";
      el.className = "app-chrome-toast hidden";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () {
      el.classList.add("hidden");
    }, 2800);
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        resolve();
      } catch (e) {
        reject(e);
      }
      document.body.removeChild(ta);
    });
  }

  function shareApp(app) {
    var url = shareUrl(app);
    copyText(url)
      .then(function () {
        toast("Link copied — paste in Discord or WhatsApp");
      })
      .catch(function () {
        toast(url);
      });
  }

  function openApp(app) {
    if (!app) return;
    if (app.id === state.currentId) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    var up = !!state.up[app.id];
    if (up) {
      window.open(app.local, "_blank", "noopener");
      return;
    }
    try {
      window.location.href = app.protocol + "://open";
    } catch (e) {
      /* ignore */
    }
    setTimeout(function () {
      window.open(app.local, "_blank", "noopener");
    }, 400);
  }

  function hideCtx() {
    var m = document.getElementById("ac_ctx");
    if (m) m.classList.add("hidden");
  }

  function showCtx(x, y, app) {
    var m = document.getElementById("ac_ctx");
    if (!m) return;
    m.dataset.appId = app.id;
    m.style.left = Math.min(x, window.innerWidth - 180) + "px";
    m.style.top = Math.min(y, window.innerHeight - 120) + "px";
    m.classList.remove("hidden");
  }

  function installCtx() {
    if (document.getElementById("ac_ctx")) return;
    var m = document.createElement("div");
    m.id = "ac_ctx";
    m.className = "app-chrome-ctx hidden";
    m.innerHTML =
      '<button type="button" data-act="open">Open</button>' +
      '<button type="button" data-act="share">Share</button>';
    document.body.appendChild(m);
    m.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      var app = APPS.filter(function (a) {
        return a.id === m.dataset.appId;
      })[0];
      hideCtx();
      if (!app) return;
      if (btn.dataset.act === "share") shareApp(app);
      else openApp(app);
    });
    document.addEventListener("click", hideCtx);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") hideCtx();
    });
  }

  function installHeader(title, sections) {
    var old = document.getElementById("ac_header");
    if (old) old.remove();
    var bar = document.createElement("header");
    bar.id = "ac_header";
    bar.className = "app-chrome-header";
    var mark = document.createElement("button");
    mark.type = "button";
    mark.className = "ac-mark";
    mark.textContent = title || "App";
    mark.onclick = function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
    var nav = document.createElement("nav");
    nav.setAttribute("aria-label", "Sections");
    (sections || []).forEach(function (s) {
      var a = document.createElement("a");
      a.href = "#" + s.id;
      a.dataset.sec = s.id;
      a.textContent = s.label;
      a.addEventListener("click", function (e) {
        var el = document.getElementById(s.id);
        if (!el) return;
        e.preventDefault();
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      nav.appendChild(a);
    });
    bar.appendChild(mark);
    bar.appendChild(nav);
    document.body.insertBefore(bar, document.body.firstChild);
  }

  function installRail(currentId) {
    var old = document.getElementById("ac_rail");
    if (old) old.remove();
    var rail = document.createElement("aside");
    rail.id = "ac_rail";
    rail.className = "app-chrome-rail";
    rail.setAttribute("aria-label", "Other apps");
    APPS.forEach(function (app) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ac-tile" + (app.id === currentId ? " current" : "");
      btn.dataset.appId = app.id;
      btn.title = app.name + " — click to open, right-click to share";
      btn.textContent = app.short;
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        openApp(app);
      });
      btn.addEventListener("contextmenu", function (e) {
        e.preventDefault();
        showCtx(e.clientX, e.clientY, app);
      });
      rail.appendChild(btn);
    });
    document.body.appendChild(rail);
  }

  function paintUp() {
    var tiles = document.querySelectorAll(".app-chrome-rail .ac-tile");
    tiles.forEach(function (btn) {
      var id = btn.dataset.appId;
      btn.classList.toggle("up", !!state.up[id]);
      btn.classList.toggle("dim", id !== state.currentId && !state.up[id]);
    });
  }

  function probe() {
    var url = "/api/siblings";
    fetch(url)
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (data && data.apps) {
          data.apps.forEach(function (row) {
            state.up[row.id] = !!row.up;
          });
          paintUp();
          return;
        }
        probeDirect();
      })
      .catch(probeDirect);
  }

  function probeDirect() {
    APPS.forEach(function (app) {
      fetch(app.health, { method: "GET" })
        .then(function (r) {
          state.up[app.id] = r.ok;
          paintUp();
        })
        .catch(function () {
          state.up[app.id] = false;
          paintUp();
        });
    });
  }

  function watchSections(sections) {
    if (!global.IntersectionObserver) return;
    var links = document.querySelectorAll(".app-chrome-header nav a");
    var obs = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          links.forEach(function (a) {
            a.classList.toggle("active", a.dataset.sec === en.target.id);
          });
        });
      },
      { rootMargin: "-20% 0px -70% 0px", threshold: 0.01 }
    );
    sections.forEach(function (s) {
      var el = document.getElementById(s.id);
      if (el) obs.observe(el);
    });
  }

  function fromLibrary() {
    try {
      var p = new URLSearchParams(location.search || "");
      return p.get("from_library") === "1" || !!p.get("library_model_id");
    } catch (e) {
      return false;
    }
  }

  function install(opts) {
    opts = opts || {};
    if (fromLibrary() && !opts.force) return;
    state.currentId = opts.currentId || "";
    state.sections = opts.sections || [];
    if (!document.body) return;
    document.body.classList.add("has-app-chrome");
    installCtx();
    installHeader(opts.title || "App", state.sections);
    installRail(state.currentId);
    if (state.sections.length) watchSections(state.sections);
    probe();
  }

  global.AppChrome = {
    install: install,
    shareUrl: function (id) {
      var app = APPS.filter(function (a) {
        return a.id === id;
      })[0];
      return app ? shareUrl(app) : SHARE_BASE + "/";
    },
    apps: APPS,
    shareApp: shareApp,
    SHARE_BASE: SHARE_BASE,
  };
})(window);
