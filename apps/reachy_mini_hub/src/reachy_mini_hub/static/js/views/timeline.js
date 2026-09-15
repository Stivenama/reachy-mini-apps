/**
 * Timeline view (Reachy Hub): línea de tiempo de emociones.
 * Clic en la línea → se muestra el segundo → elegís la emoción → marcador.
 * Play/Pausa/Reanudar/Detener + guardar/cargar líneas.
 * Cargas independientes: si una llamada falla, el resto de la vista sigue
 * viva y se ofrece reintentar (nunca queda en un estado "gris" muerto).
 */

import {
  timelineAddEvent,
  timelineConfigure,
  timelineDelete,
  timelineEmotions,
  timelineLoad,
  timelinePause,
  timelinePlay,
  timelineRemoveEvent,
  timelineSave,
  timelineSaved,
  timelineState,
  timelineStop,
} from "../api.js";
import { h } from "../ui.js";

const MIN_MINUTES = 21;
const MAX_MINUTES = 120;

function formatTime(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.floor(totalSeconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export async function mountTimelineView({ outlet, signal }) {
  let duration = MIN_MINUTES * 60;
  let events = [];
  let playing = false;
  let position = 0;
  let displayPosition = 0;
  let lastSyncAt = performance.now();
  let emotions = [];
  let pollTimer = null;
  let tickTimer = null;
  let connectionOk = true;

  const errorBanner = h(
    "p",
    { class: "timeline__error", role: "alert", hidden: true },
    "Sin conexión con el robot.",
    h("button", { type: "button", class: "hub__btn hub__btn--small", hidden: true }, "Reintentar")
  );

  const caption = h("p", { class: "timeline__caption", role: "status" }, "Cargando...");

  const durationSelect = h("select", { class: "timeline__duration-select", "aria-label": "Duración" });
  for (let minutes = MIN_MINUTES; minutes <= MAX_MINUTES; minutes += 5) {
    durationSelect.append(h("option", { value: String(minutes) }, `${minutes} min`));
  }
  durationSelect.value = String(MIN_MINUTES);

  const applyDurationButton = h("button", { type: "button", class: "hub__btn" }, "Aplicar duración");
  applyDurationButton.addEventListener("click", onApplyDuration);

  const timelineBar = h(
    "div",
    { class: "timeline__bar", role: "slider", "aria-label": "Línea de tiempo" },
    h("div", { class: "timeline__progress", style: { width: "0%" } }),
    h("div", { class: "timeline__markers" })
  );
  timelineBar.addEventListener("click", onBarClick);
  timelineBar.addEventListener("mousemove", onBarHover);
  timelineBar.addEventListener("mouseleave", onBarHoverEnd);

  const tooltip = h("div", { class: "timeline__tooltip", hidden: true }, "00:00");

  const timeLabel = h("span", { class: "timeline__time" }, "00:00 / 21:00");

  const playButton = h("button", { type: "button", class: "hub__btn timeline__play", disabled: true, title: "Añade emociones para reproducir" }, "▶ Reproducir");
  const pauseButton = h("button", { type: "button", class: "hub__btn", disabled: true }, "⏸ Pausar");
  const resumeButton = h("button", { type: "button", class: "hub__btn", disabled: true }, "▶ Reanudar");
  const stopButton = h("button", { type: "button", class: "hub__btn", disabled: true }, "⏹ Detener");
  playButton.addEventListener("click", () => runTimelineAction(timelinePlay));
  pauseButton.addEventListener("click", () => runTimelineAction(timelinePause));
  resumeButton.addEventListener("click", () => runTimelineAction(timelinePlay));
  stopButton.addEventListener("click", () => runTimelineAction(timelineStop));

  const saveNameInput = h("input", { type: "text", class: "timeline__name-input", placeholder: "Nombre de la línea" });
  const saveButton = h("button", { type: "button", class: "hub__btn" }, "Guardar");
  saveButton.addEventListener("click", onSave);
  const savedList = h("div", { class: "timeline__saved" });

  const timeInput = h("input", {
    type: "text",
    class: "timeline__time-input",
    placeholder: "min:seg (ej: 12:12)",
    inputmode: "text",
  });
  let selectedEmotion = "";
  const emotionInput = h("input", {
    type: "text",
    class: "timeline__emotion-search",
    placeholder: "Buscar o elegir emoción...",
    autocomplete: "off",
    "aria-label": "Buscar o elegir emoción",
  });
  const comboList = h("div", { class: "timeline__combo-list", hidden: true });
  const comboWrap = h("div", { class: "timeline__combo" }, emotionInput, comboList);
  emotionInput.addEventListener("focus", () => {
    renderComboList(emotionInput.value.trim());
    comboList.hidden = false;
  });
  emotionInput.addEventListener("input", () => {
    selectedEmotion = "";
    renderComboList(emotionInput.value.trim());
    comboList.hidden = false;
  });
  emotionInput.addEventListener("blur", () => {
    setTimeout(() => {
      comboList.hidden = true;
    }, 150);
  });
  const addManualButton = h("button", { type: "button", class: "hub__btn" }, "Añadir emoción");
  addManualButton.addEventListener("click", onAddManual);
  timeInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") onAddManual();
  });

  function renderComboList(filter = "") {
    comboList.replaceChildren();
    const filtered = emotions.filter(
      (emotion) => !filter || emotion.toLowerCase().includes(filter.toLowerCase())
    );
    if (!filtered.length) {
      comboList.append(h("p", { class: "timeline__empty" }, "Sin coincidencias"));
      return;
    }
    for (const emotion of filtered) {
      const option = h("button", { type: "button", class: "timeline__combo-option" }, emotion);
      option.addEventListener("click", () => {
        selectedEmotion = emotion;
        emotionInput.value = emotion;
        comboList.hidden = true;
      });
      comboList.append(option);
    }
  }

  const modal = h(
    "div",
    { class: "timeline__modal", hidden: true },
    h("div", { class: "timeline__modal-card" }, h("div", { class: "timeline__modal-head" }))
  );

  const view = h(
    "section",
    { class: "view view--timeline" },
    h("h2", { class: "view__title" }, "Línea de tiempo de emociones"),
    errorBanner,
    caption,
    h("div", { class: "timeline__row" }, durationSelect, applyDurationButton),
    h("div", { class: "timeline__bar-wrap" }, timelineBar, tooltip),
    h("div", { class: "timeline__row" }, timeLabel),
    h("div", { class: "timeline__row" }, timeInput, comboWrap, addManualButton),
    h("div", { class: "timeline__row" }, playButton, pauseButton, resumeButton, stopButton),
    h("div", { class: "timeline__row" }, saveNameInput, saveButton),
    savedList,
    modal
  );
  outlet.replaceChildren(view);

  const retryButton = errorBanner.querySelector("button");
  retryButton.addEventListener("click", () => {
    retryButton.disabled = true;
    caption.textContent = "Reintentando...";
    void refresh().finally(() => {
      retryButton.disabled = false;
    });
  });

  signal.addEventListener("abort", cleanup, { once: true });

  await refresh();

  async function loadWithFallback(requestFn) {
    try {
      return { data: await requestFn(), error: null };
    } catch (error) {
      return { data: null, error };
    }
  }

  async function refresh() {
    const [stateResult, emotionsResult, savedResult] = await Promise.all([
      loadWithFallback(timelineState),
      loadWithFallback(timelineEmotions),
      loadWithFallback(timelineSaved),
    ]);

    let errorCount = 0;
    if (stateResult.error) errorCount += 1;
    else {
      duration = Number(stateResult.data.duration) || duration;
      events = stateResult.data.events || [];
      playing = Boolean(stateResult.data.playing);
      position = Number(stateResult.data.position) || 0;
      displayPosition = position;
      lastSyncAt = performance.now();
    }
    if (emotionsResult.error) errorCount += 1;
    else emotions = emotionsResult.data.emotions || [];
    const savedNames = savedResult.error ? [] : (savedResult.data.saved || []);
    if (savedResult.error) errorCount += 1;

    connectionOk = errorCount === 0;
    renderAll(savedNames, errorCount);
  }

  function parseTimeInput(value) {
    const text = String(value || "").trim();
    if (!text) return NaN;
    if (text.includes(":")) {
      const [minutes, seconds] = text.split(":").map((part) => Number(part));
      if (Number.isFinite(minutes) && Number.isFinite(seconds) && seconds >= 0 && seconds < 60) {
        return minutes * 60 + seconds;
      }
      return NaN;
    }
    const seconds = Number(text);
    return Number.isFinite(seconds) ? seconds : NaN;
  }

  async function onAddManual() {
    const time = parseTimeInput(timeInput.value);
    const emotion = selectedEmotion || emotionInput.value.trim();
    if (Number.isNaN(time) || time < 0 || time > duration) {
      caption.textContent = `Tiempo inválido. Usa min:seg (0:00 a ${formatTime(duration)}) o segundos.`;
      return;
    }
    if (!emotion || !emotions.includes(emotion)) {
      caption.textContent = "Elige una emoción del menú (o escribe su nombre exacto).";
      return;
    }
    try {
      await timelineAddEvent(time, emotion);
      timeInput.value = "";
      caption.textContent = `Emoción "${emotion}" añadida a los ${formatTime(time)}.`;
      await refresh();
    } catch (error) {
      caption.textContent = `Error: ${error?.message || error}`;
    }
  }

  function renderAll(savedNames, errorCount = 0) {
    errorBanner.hidden = errorCount === 0;
    retryButton.hidden = errorCount === 0;
    errorBanner.textContent = errorCount > 0 ? `Sin conexión con el robot (${errorCount} servicio(s) fallaron). ` : "";
    errorBanner.append(retryButton);
    caption.textContent = errorCount > 0 ? "" : caption.textContent === "Cargando..." ? "Haz clic en la línea o usa min:seg para agregar emociones." : caption.textContent;
    renderMarkers();
    renderTime();
    renderButtons();
    renderSaved(savedNames);
  }

  function renderMarkers() {
    const container = timelineBar.querySelector(".timeline__markers");
    container.replaceChildren();
    const sorted = [...events].sort((a, b) => a.time - b.time);
    for (const event of sorted) {
      const percent = duration > 0 ? Math.min(100, (event.time / duration) * 100) : 0;
      const marker = h(
        "button",
        {
          type: "button",
          class: "timeline__marker",
          style: { left: `${percent}%` },
          title: `${event.emotion} — ${formatTime(event.time)}`,
        },
        "🎭"
      );
      marker.addEventListener("click", (clickEvent) => {
        clickEvent.stopPropagation();
        onMarkerClick(event);
      });
      container.append(marker);
    }
  }

  function renderTime() {
    const shown = playing
      ? position + (performance.now() - lastSyncAt) / 1000
      : displayPosition;
    timeLabel.textContent = `${formatTime(shown)} / ${formatTime(duration)}`;
    timelineBar.querySelector(".timeline__progress").style.width = `${duration > 0 ? Math.min(100, (shown / duration) * 100) : 0}%`;
  }

  function startTick() {
    if (tickTimer != null) return;
    tickTimer = setInterval(() => {
      if (!playing) return;
      renderTime();
    }, 250);
  }

  function stopTick() {
    if (tickTimer != null) {
      clearInterval(tickTimer);
      tickTimer = null;
    }
  }

  function renderButtons() {
    const hasEvents = events.length > 0;
    playButton.disabled = playing || !hasEvents || !connectionOk;
    pauseButton.disabled = !playing || !connectionOk;
    resumeButton.disabled = playing || position <= 0 || !hasEvents || !connectionOk;
    stopButton.disabled = (!playing && position <= 0) || !connectionOk;
    playButton.title = hasEvents ? "Reproducir línea de tiempo" : "Añade emociones para reproducir";
  }

  function renderSaved(savedNames) {
    savedList.replaceChildren();
    if (!savedNames.length) return;
    savedList.append(h("p", { class: "timeline__saved-title" }, "Líneas guardadas"));
    for (const name of savedNames) {
      const row = h("div", { class: "timeline__saved-row" }, h("span", null, name));
      const loadBtn = h("button", { type: "button", class: "hub__btn hub__btn--small" }, "Cargar");
      const deleteBtn = h("button", { type: "button", class: "hub__btn hub__btn--small hub__btn--danger" }, "Borrar");
      loadBtn.addEventListener("click", () => runTimelineAction(() => timelineLoad(name)));
      deleteBtn.addEventListener("click", () => runTimelineAction(() => timelineDelete(name)));
      row.append(loadBtn, deleteBtn);
      savedList.append(row);
    }
  }

  async function runTimelineAction(action) {
    try {
      await action();
      await refresh();
    } catch (error) {
      caption.textContent = `Error: ${error?.message || error}`;
    }
  }

  async function onApplyDuration() {
    const minutes = Number(durationSelect.value) || MIN_MINUTES;
    try {
      await timelineConfigure(minutes * 60);
      await refresh();
      caption.textContent = `Línea configurada a ${minutes} minutos. Haz clic en la línea para agregar emociones.`;
    } catch (error) {
      caption.textContent = `Error: ${error?.message || error}`;
    }
  }

  function onBarClick(event) {
    if (playing) return;
    const rect = timelineBar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const time = Math.round(ratio * duration);
    openEmotionPicker(time);
  }

  function onBarHover(event) {
    const rect = timelineBar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const time = Math.round(ratio * duration);
    tooltip.textContent = formatTime(time);
    const wrapRect = timelineBar.parentElement.getBoundingClientRect();
    const left = event.clientX - wrapRect.left;
    tooltip.style.left = `${Math.min(Math.max(left - 22, 4), wrapRect.width - 52)}px`;
    tooltip.style.top = `${event.clientY - wrapRect.top - 34}px`;
    tooltip.hidden = false;
  }

  function onBarHoverEnd() {
    tooltip.hidden = true;
  }

  function onMarkerClick(event) {
    const remove = window.confirm(`Quitar "${event.emotion}" a los ${formatTime(event.time)}?`);
    if (!remove) return;
    runTimelineAction(() => timelineRemoveEvent(event.id));
  }

  async function openEmotionPicker(time) {
    const head = modal.querySelector(".timeline__modal-head");
    const searchInput = h("input", {
      type: "text",
      class: "timeline__search",
      placeholder: "Buscar emoción...",
      value: "",
    });
    const grid = h("div", { class: "timeline__emotion-grid" });
    const footer = h("div", { class: "timeline__modal-footer" });
    const cancelButton = h("button", { type: "button", class: "hub__btn" }, "Cancelar");

    let emotionList = emotions;
    const retryPick = h("button", { type: "button", class: "hub__btn", hidden: true }, "Reintentar cargar emociones");
    retryPick.addEventListener("click", async () => {
      retryPick.disabled = true;
      footer.textContent = "Cargando emociones...";
      try {
        const data = await timelineEmotions();
        emotionList = data.emotions || [];
        footer.textContent = "";
        renderGrid(searchInput.value.trim());
      } catch (error) {
        footer.textContent = `No se pudieron cargar: ${error?.message || error}`;
      } finally {
        retryPick.disabled = false;
      }
    });

    function renderGrid(filter = "") {
      grid.replaceChildren();
      retryPick.hidden = emotionList.length > 0;
      if (!emotionList.length) {
        grid.append(h("p", { class: "timeline__empty" }, "Sin conexión con la lista de emociones."));
        grid.append(retryPick);
        return;
      }
      const filtered = emotionList.filter((emotion) => !filter || emotion.toLowerCase().includes(filter.toLowerCase()));
      if (!filtered.length) {
        grid.append(h("p", { class: "timeline__empty" }, "Sin coincidencias"));
        return;
      }
      for (const emotion of filtered) {
        const button = h("button", { type: "button", class: "timeline__emotion-btn" }, emotion);
        button.addEventListener("click", async () => {
          try {
            await timelineAddEvent(time, emotion);
            closeModal();
            await refresh();
          } catch (error) {
            footer.textContent = `Error: ${error?.message || error}`;
          }
        });
        grid.append(button);
      }
    }

    searchInput.addEventListener("input", () => renderGrid(searchInput.value.trim()));
    cancelButton.addEventListener("click", closeModal);
    renderGrid();

    head.replaceChildren(
      h("h3", null, `Emoción a los ${formatTime(time)}`),
      searchInput,
      grid,
      footer,
      cancelButton
    );
    modal.hidden = false;
  }

  function closeModal() {
    modal.hidden = true;
  }

  async function onSave() {
    const name = saveNameInput.value.trim();
    if (!name) {
      caption.textContent = "Escribe un nombre para guardar la línea.";
      return;
    }
    try {
      await timelineSave(name);
      saveNameInput.value = "";
      caption.textContent = `Línea "${name}" guardada.`;
      const data = await timelineSaved();
      renderSaved(data.saved || []);
    } catch (error) {
      caption.textContent = `Error: ${error?.message || error}`;
    }
  }

  function cleanup() {
    if (pollTimer != null) clearInterval(pollTimer);
    modal.remove();
  }

  async function pollLoop() {
    if (!connectionOk) {
      await refresh();
      return;
    }
    try {
      const state = await timelineState();
      playing = Boolean(state.playing);
      position = Number(state.position) || 0;
      displayPosition = position;
      lastSyncAt = performance.now();
      events = state.events || events;
      duration = Number(state.duration) || duration;
      renderTime();
      renderButtons();
      if (playing) startTick();
      else stopTick();
    } catch {
      connectionOk = false;
      renderAll([], 1);
    }
  }

  function cleanup() {
    if (pollTimer != null) clearInterval(pollTimer);
    stopTick();
    modal.remove();
  }

  pollTimer = setInterval(() => void pollLoop(), playing || !connectionOk ? 1000 : 3000);
  if (playing) startTick();
}