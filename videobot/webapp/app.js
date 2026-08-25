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
  const TIPS = {
    create: "Тема — пара слов. Своё фото необязательно.",
    cut: "Вайб сниму сам. Своё видео режется в чате бота.",
    improve: "Видео: 4K или слоу-мо. Фото: реставрация.",
    tryon: "Два фото и галочка согласия — без неё не беру.",
    voice: "Чистая речь 10+ сек. Согласие отдельно от фото.",
    mine: "Пришлю последний готовый файл в этот чат.",
  };

  function showStatus(text, kind) {
    statusEl.hidden = false;
    statusEl.className = "status" + (kind ? " " + kind : "");
    statusEl.textContent = text;
  }

  function showTip(key) {
    const text = TIPS[key];
    if (!tipEl || !text) return;
    tipEl.hidden = false;
    tipEl.textContent = text;
  }

  function hideTip() {
    if (tipEl) tipEl.hidden = true;
  }

  function showHome() {
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("is-on"));
    if (homeEl) homeEl.classList.remove("is-off");
    hideTip();
  }

  function showPanel(name) {
    if (homeEl) homeEl.classList.add("is-off");
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.toggle("is-on", p.id === "panel-" + name);
    });
    showTip(name);
  }

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
      return;
    }
    showStatus(data.message || "Готово. Смотри чат с ботом.", "ok");
    if (tg && tg.close && data.close) {
      setTimeout(() => tg.close(), 1200);
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
