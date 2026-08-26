(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#0c0b0a");
      tg.setBackgroundColor("#0c0b0a");
    } catch (_e) {
      /* older clients */
    }
  }

  const statusEl = document.getElementById("status");
  const homeEl = document.getElementById("home");
  const tipEl = document.getElementById("tip");
  const initData = (tg && tg.initData) || "";
  const TIP_STORE = "vb_onboard_v1";
  // Один короткий намёк. Первый запуск + первый заход в режим. Не абзацы.
  const TIPS = {
    home: "Нажми карточку. Под ней написано, что она делает.",
    create: "Напиши тему двумя словами и жми «Снять».",
    autorolik: "До 6 фото + галочка. Сценарий и съёмка здесь, видео — в чат.",
    cut: "Напиши вайб. Своё видео режется в чате бота.",
    improve: "Кинь файл: видео — 4K или слоу-мо, фото — починить.",
    tryon: "Фото тебя + фото одежды + галочка.",
    voice: "Голосовое 10+ секунд и галочка.",
    mine: "Пришлю последний ролик сюда, в чат.",
  };
  let tipTimer = 0;

  function showStatus(text, kind) {
    statusEl.hidden = false;
    statusEl.className = "status" + (kind ? " " + kind : "");
    statusEl.textContent = text;
  }

  function seenTips() {
    try {
      return JSON.parse(localStorage.getItem(TIP_STORE) || "{}") || {};
    } catch (_e) {
      return {};
    }
  }

  function markTip(key) {
    const seen = seenTips();
    seen[key] = 1;
    try {
      localStorage.setItem(TIP_STORE, JSON.stringify(seen));
    } catch (_e) {
      /* private mode */
    }
  }

  function hideTip() {
    if (tipTimer) {
      clearTimeout(tipTimer);
      tipTimer = 0;
    }
    if (tipEl) tipEl.hidden = true;
  }

  function showTipOnce(key) {
    const text = TIPS[key];
    if (!tipEl || !text) return;
    if (seenTips()[key]) {
      hideTip();
      return;
    }
    tipEl.hidden = false;
    tipEl.textContent = text;
    markTip(key);
    if (tipTimer) clearTimeout(tipTimer);
    tipTimer = setTimeout(hideTip, 4500);
  }

  function showHome() {
    stopAutoPoll();
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("is-on"));
    if (homeEl) homeEl.classList.remove("is-off");
    hideTip();
  }

  function showPanel(name) {
    if (homeEl) homeEl.classList.add("is-off");
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.toggle("is-on", p.id === "panel-" + name);
    });
    showTipOnce(name);
    if (name === "autorolik") {
      restoreAutorolik();
    }
  }

  if (tipEl) {
    tipEl.addEventListener("click", hideTip);
  }
  showTipOnce("home");

  document.querySelectorAll("[data-go]").forEach((btn) => {
    btn.addEventListener("click", () => showPanel(btn.dataset.go));
  });
  document.querySelectorAll("[data-back]").forEach((btn) => {
    btn.addEventListener("click", showHome);
  });

  async function postJob(path, extra, files) {
    if (!initData) {
      showStatus("Открой меню из Telegram — иначе нет подписи Mini App.", "err");
      return;
    }
    const body = new FormData();
    body.append("initData", initData);
    Object.entries(extra || {}).forEach(([k, v]) => body.append(k, v));
    Object.entries(files || {}).forEach(([k, f]) => {
      if (f) body.append(k, f);
    });
    showStatus("Отправил задачу. Результат придёт в чат с ботом…");
    const resp = await fetch(path, { method: "POST", body });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      showStatus(data.error || "Не вышло. Попробуй ещё раз.", "err");
      return null;
    }
    showStatus(data.message || "Готово. Смотри чат с ботом.", "ok");
    if (tg && tg.close && data.close) {
      setTimeout(() => tg.close(), 1200);
    }
    return data;
  }

  function autoForm(extra) {
    const body = new FormData();
    body.append("initData", initData);
    Object.entries(extra || {}).forEach(([k, v]) => body.append(k, v));
    return body;
  }

  let autoTimer = 0;

  function stopAutoPoll() {
    if (autoTimer) {
      clearInterval(autoTimer);
      autoTimer = 0;
    }
  }

  function startAutoPoll() {
    stopAutoPoll();
    autoTimer = setInterval(() => {
      refreshAutorolik().catch(() => {});
    }, 1600);
  }

  function setHidden(el, hide) {
    if (!el) return;
    el.hidden = !!hide;
  }

  function renderScript(script) {
    const titleEl = document.getElementById("auto-title");
    const hookEl = document.getElementById("auto-hook");
    const list = document.getElementById("auto-scenes");
    if (!titleEl || !list) return;
    titleEl.textContent = (script && script.title) || "Авторолик";
    if (hookEl) hookEl.textContent = script && script.hook ? "Хук: " + script.hook : "";
    list.innerHTML = "";
    (script && script.scenes ? script.scenes : []).forEach((s) => {
      const li = document.createElement("li");
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = (s.n || "") + ". " + (s.tag || "");
      const narr = document.createElement("p");
      narr.className = "narr";
      narr.textContent = s.narration || "";
      const vis = document.createElement("p");
      vis.className = "vis";
      vis.textContent = s.visual || "";
      li.appendChild(tag);
      li.appendChild(narr);
      li.appendChild(vis);
      list.appendChild(li);
    });
  }

  function renderShoot(shoot) {
    const box = document.getElementById("auto-progress");
    const label = document.getElementById("auto-progress-label");
    const list = document.getElementById("auto-progress-scenes");
    if (!box || !list) return;
    const scenes = (shoot && shoot.scenes) || [];
    const n = (shoot && shoot.scene_n) || 0;
    const total = (shoot && shoot.scene_total) || scenes.length;
    if (label) {
      const head = shoot && shoot.label ? shoot.label : "Снимаю…";
      label.textContent = total ? head + " · сцена " + n + " из " + total : head;
    }
    list.innerHTML = "";
    scenes.forEach((s) => {
      const li = document.createElement("li");
      if (s.current) li.classList.add("is-now");
      if (s.done) li.classList.add("is-done");
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "Сцена " + s.n + (s.current ? " · сейчас" : s.done ? " · готово" : "");
      const narr = document.createElement("p");
      narr.className = "narr";
      narr.textContent = s.label || "ждёт";
      li.appendChild(tag);
      li.appendChild(narr);
      list.appendChild(li);
    });
  }

  function showAutoPhase(phase, pending, shoot) {
    const setup = document.getElementById("auto-setup");
    const wait = document.getElementById("auto-wait");
    const review = document.getElementById("auto-review");
    const progress = document.getElementById("auto-progress");
    const refreshWrap = document.getElementById("auto-refresh-wrap");
    const script = pending && pending.script;
    const hasScenes = script && script.scenes && script.scenes.length;
    if (phase === "shooting" || (shoot && shoot.active && !shoot.done && !shoot.failed)) {
      setHidden(setup, true);
      setHidden(wait, true);
      setHidden(review, true);
      setHidden(progress, false);
      setHidden(refreshWrap, false);
      renderShoot(shoot);
      startAutoPoll();
      return;
    }
    if (phase === "done" && shoot && shoot.done && !shoot.failed) {
      setHidden(setup, true);
      setHidden(wait, true);
      setHidden(review, true);
      setHidden(progress, false);
      setHidden(refreshWrap, false);
      renderShoot(shoot);
      stopAutoPoll();
      showStatus("Готово. Видео в чате с ботом.", "ok");
      return;
    }
    if ((phase === "review" || phase === "error") && hasScenes) {
      setHidden(setup, true);
      setHidden(wait, true);
      setHidden(review, false);
      setHidden(progress, true);
      setHidden(refreshWrap, false);
      renderScript(script);
      stopAutoPoll();
      if (phase === "error" && pending && pending.error) {
        showStatus(pending.error, "err");
      }
      return;
    }
    if (phase === "scripting") {
      setHidden(setup, true);
      setHidden(wait, false);
      setHidden(review, true);
      setHidden(progress, true);
      setHidden(refreshWrap, false);
      startAutoPoll();
      return;
    }
    setHidden(setup, false);
    setHidden(wait, true);
    setHidden(review, true);
    setHidden(progress, true);
    setHidden(refreshWrap, true);
    stopAutoPoll();
  }

  function clockStamp() {
    try {
      return new Date().toLocaleTimeString("ru-RU", { hour12: false });
    } catch (_e) {
      return "";
    }
  }

  function markRefresh(line) {
    const meta = document.getElementById("auto-refresh-meta");
    if (!meta) return;
    const t = clockStamp();
    meta.textContent = t ? ("Обновлено " + t + (line ? " · " + line : "")) : (line || "");
  }

  async function refreshAutorolik() {
    if (!initData) return;
    const resp = await fetch("/api/autorolik/status", { method: "POST", body: autoForm() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      if (data.error) showStatus(data.error, "err");
      return data;
    }
    const pending = data.pending || {};
    const shoot = data.shoot || {};
    const phase = data.phase || pending.phase || "";
    const waitLabel = document.getElementById("auto-wait-label");
    if (waitLabel && phase === "scripting") {
      waitLabel.textContent = data.message || pending.error || "Пишу сценарий…";
    }
    if (phase === "scripting") {
      showStatus(data.message || "Пишу сценарий…", "");
      markRefresh("пишу сценарий");
    } else if (phase === "shooting") {
      showStatus(shoot.label || data.message || "Снимаю…", "");
      markRefresh(shoot.label || "съёмка");
    } else if (phase === "review") {
      markRefresh("сценарий готов");
    } else if (phase === "done") {
      markRefresh("готово");
    } else if (phase === "error" && pending.error) {
      showStatus(pending.error, "err");
      markRefresh("ошибка");
    } else {
      markRefresh(phase || "");
    }
    showAutoPhase(phase, pending, shoot);
    return data;
  }

  async function restoreAutorolik() {
    try {
      await refreshAutorolik();
    } catch (_e) {
      /* offline */
    }
  }

  function bind(id, handler) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("click", async () => {
      el.disabled = true;
      try {
        await handler();
      } finally {
        el.disabled = false;
      }
    });
  }

  bind("go-quick", async () => {
    const idea = document.getElementById("idea").value.trim();
    if (idea.length < 3) {
      showStatus("Напиши тему: хватит 2–3 слов.", "err");
      return;
    }
    const photo = document.getElementById("quick-photo").files[0];
    const consent = document.getElementById("quick-consent").checked;
    if (photo && !consent) {
      showStatus("Для своего фото нужна галочка согласия.", "err");
      return;
    }
    const quality = (document.querySelector('input[name="quality"]:checked') || {}).value || "optimal";
    await postJob("/api/quick", { idea, quality, consent: consent ? "1" : "0" }, { photo });
  });

  bind("go-autorolik", async () => {
    const files = [...(document.getElementById("auto-photos").files || [])];
    if (!files.length) {
      showStatus("Нужно хотя бы одно фото.", "err");
      return;
    }
    if (files.length > 6) {
      showStatus("Максимум 6 фото.", "err");
      return;
    }
    const consent = document.getElementById("auto-consent").checked;
    if (!consent) {
      showStatus("Для фото людей нужна галочка согласия.", "err");
      return;
    }
    const topic = document.getElementById("auto-topic").value.trim();
    const photos = {};
    files.forEach((f, i) => {
      photos["photo" + (i + 1)] = f;
    });
    const started = await postJob("/api/autorolik", { topic, consent: "1" }, photos);
    if (!started) return;
    showAutoPhase("scripting", { phase: "scripting" }, {});
    startAutoPoll();
    await refreshAutorolik();
  });

  bind("go-auto-refresh", async () => {
    if (!initData) {
      showStatus("Открой меню из Telegram — иначе нет подписи Mini App.", "err");
      return;
    }
    showStatus("Проверяю статус…");
    try {
      const data = await refreshAutorolik();
      if (!data || data.ok === false) {
        showStatus((data && data.error) || "Не удалось обновить статус.", "err");
      }
    } catch (_e) {
      showStatus("Не достучался до сервера. Нажми ещё раз.", "err");
    }
  });

  bind("go-auto-shoot", async () => {
    if (!initData) {
      showStatus("Открой меню из Telegram — иначе нет подписи Mini App.", "err");
      return;
    }
    showStatus("Снимаю. Прогресс здесь, видео — в чат.");
    const resp = await fetch("/api/autorolik/shoot", { method: "POST", body: autoForm() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      showStatus(data.error || "Не вышло снять.", "err");
      return;
    }
    startAutoPoll();
    await refreshAutorolik();
  });

  bind("go-auto-edit", async () => {
    const notes = (document.getElementById("auto-notes").value || "").trim();
    if (notes.length < 3) {
      showStatus("Напиши правку парой слов.", "err");
      return;
    }
    showStatus("Переписываю сценарий…");
    const resp = await fetch("/api/autorolik/revise", {
      method: "POST",
      body: autoForm({ notes }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      showStatus(data.error || "Не вышло поправить.", "err");
      return;
    }
    startAutoPoll();
    await refreshAutorolik();
  });

  bind("go-auto-cancel", async () => {
    const resp = await fetch("/api/autorolik/cancel", { method: "POST", body: autoForm() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      showStatus(data.error || "Не вышло отменить.", "err");
      return;
    }
    stopAutoPoll();
    showAutoPhase("", {}, {});
    showStatus("Отменил. Можно собрать сценарий заново.", "ok");
  });

  bind("go-vibe", async () => {
    const vibe = document.getElementById("vibe").value.trim();
    if (vibe.length < 3) {
      showStatus("Напиши вайб: хватит 2–3 слов.", "err");
      return;
    }
    await postJob("/api/vibe", { vibe });
  });

  bind("go-upscale", async () => {
    const file = document.getElementById("improve-file").files[0];
    if (!file) {
      showStatus("Приложи фото или видео.", "err");
      return;
    }
    await postJob("/api/upscale", {}, { file });
  });

  bind("go-slowmo", async () => {
    const file = document.getElementById("improve-file").files[0];
    if (!file) {
      showStatus("Приложи видео для слоу-мо.", "err");
      return;
    }
    if ((file.type || "").startsWith("image/")) {
      showStatus("Слоу-мо только для видео. Для фото — реставрация.", "err");
      return;
    }
    await postJob("/api/interpolate", {}, { file });
  });

  bind("go-restore", async () => {
    const file = document.getElementById("improve-file").files[0];
    if (!file) {
      showStatus("Приложи фото для реставрации.", "err");
      return;
    }
    if ((file.type || "").startsWith("video/")) {
      showStatus("Реставрация только для фото. Для видео — апскейл или слоу-мо.", "err");
      return;
    }
    await postJob("/api/restore", {}, { file });
  });

  bind("go-tryon", async () => {
    const person = document.getElementById("tryon-person").files[0];
    const clothes = document.getElementById("tryon-clothes").files[0];
    const consent = document.getElementById("tryon-consent").checked;
    if (!person || !clothes) {
      showStatus("Нужны оба фото: человек и одежда.", "err");
      return;
    }
    if (!consent) {
      showStatus("Без согласия фото человека не беру.", "err");
      return;
    }
    await postJob("/api/tryon", { consent: "1" }, { person, clothes });
  });

  bind("go-clone", async () => {
    const audio = document.getElementById("clone-audio").files[0];
    const consent = document.getElementById("clone-consent").checked;
    if (!audio) {
      showStatus("Приложи голосовое или аудиофайл.", "err");
      return;
    }
    if (!consent) {
      showStatus("Нужна галочка «Разрешаю клонировать».", "err");
      return;
    }
    await postJob("/api/clone", { consent: "1" }, { audio });
  });

  bind("go-history", async () => {
    await postJob("/api/history");
  });
})();
