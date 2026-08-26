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
    autorolik: "До 6 фото + галочка. Сценарий подтвердишь в чате.",
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
    await postJob("/api/autorolik", { topic, consent: "1" }, photos);
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
