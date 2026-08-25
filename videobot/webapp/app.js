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
  const initData = (tg && tg.initData) || "";

  function showStatus(text, kind) {
    statusEl.hidden = false;
    statusEl.className = "status" + (kind ? " " + kind : "");
    statusEl.textContent = text;
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("is-on", b === btn));
      document.querySelectorAll(".panel").forEach((p) => {
        p.classList.toggle("is-on", p.id === "panel-" + btn.dataset.tab);
      });
    });
  });

  async function postJob(path, extra, files) {
    if (!initData) {
      showStatus("Открой Студию из Telegram — иначе нет подписи Mini App.", "err");
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

  document.getElementById("go-quick").addEventListener("click", async () => {
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
    document.getElementById("go-quick").disabled = true;
    try {
      await postJob("/api/quick", { idea, quality, consent: consent ? "1" : "0" }, { photo });
    } finally {
      document.getElementById("go-quick").disabled = false;
    }
  });

  document.getElementById("go-upscale").addEventListener("click", async () => {
    const file = document.getElementById("upscale-file").files[0];
    if (!file) {
      showStatus("Приложи фото или видео.", "err");
      return;
    }
    document.getElementById("go-upscale").disabled = true;
    try {
      await postJob("/api/upscale", {}, { file });
    } finally {
      document.getElementById("go-upscale").disabled = false;
    }
  });

  document.getElementById("go-tryon").addEventListener("click", async () => {
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
    document.getElementById("go-tryon").disabled = true;
    try {
      await postJob("/api/tryon", { consent: "1" }, { person, clothes });
    } finally {
      document.getElementById("go-tryon").disabled = false;
    }
  });

  document.getElementById("go-clone").addEventListener("click", async () => {
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
    document.getElementById("go-clone").disabled = true;
    try {
      await postJob("/api/clone", { consent: "1" }, { audio });
    } finally {
      document.getElementById("go-clone").disabled = false;
    }
  });
})();
