/**
 * Chat view (Reachy Hub): lector de texto.
 * Escribís un texto y Reachy lo lee en voz alta con la voz del push-to-talk,
 * manteniendo los movimientos de habla (wobbler). La voz se cambia con el
 * selector de personalidad.
 */

import { applyPersonality, listPersonalities, readText, subscribe } from "../api.js";
import { h, prettifyProfileName } from "../ui.js";

export async function mountChatView({ outlet, signal }) {
  const caption = h("p", { class: "chat__caption", role: "status" }, "Cargando...");
  const personalitySelect = h("select", { class: "chat__personality-select", "aria-label": "Voz / personalidad" });
  personalitySelect.addEventListener("change", onPersonalityChange);

  const textInput = h("input", {
    type: "text",
    class: "chat__input",
    placeholder: "Escribe el texto que Reachy va a leer...",
  });
  const readButton = h("button", { type: "button", class: "hub__btn chat__send" }, "📖 Leer");
  readButton.addEventListener("click", onRead);
  textInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") onRead();
  });

  const view = h(
    "section",
    { class: "view view--chat" },
    h("h2", { class: "view__title" }, "Lector de texto"),
    caption,
    h("div", { class: "chat__row" }, personalitySelect),
    h("div", { class: "chat__row" }, textInput, readButton)
  );
  outlet.replaceChildren(view);

  const unsubNotice = subscribe("conversation.notice", (params) => {
    const message = params?.message;
    if (typeof message === "string" && message.trim()) caption.textContent = message;
  });
  const unsubActivity = subscribe("conversation.activity", (params) => {
    const reason = params?.reason;
    if (reason === "assistant_tts") caption.textContent = "Leído.";
    if (reason === "response_created" || reason === "user_text") caption.textContent = "Pensando...";
  });

  signal.addEventListener("abort", cleanup, { once: true });

  await populatePersonalitySelect();

  async function populatePersonalitySelect() {
    try {
      const data = await listPersonalities();
      const choices = data?.choices || [];
      personalitySelect.replaceChildren(
        ...choices.map((choice) => h("option", { value: choice }, prettifyProfileName(choice)))
      );
      personalitySelect.value = data?.current || "";
      caption.textContent = "";
    } catch (error) {
      caption.textContent = `No se pudo cargar la voz: ${error?.message || error}`;
    }
  }

  async function onPersonalityChange() {
    const name = personalitySelect.value;
    if (!name) return;
    caption.textContent = `Aplicando "${prettifyProfileName(name)}"...`;
    try {
      await applyPersonality(name);
      caption.textContent = `Voz de "${prettifyProfileName(name)}" activa.`;
    } catch (error) {
      caption.textContent = `No se pudo aplicar: ${error?.message || error}`;
    }
  }

  async function onRead() {
    const text = textInput.value.trim();
    if (!text) return;
    readButton.disabled = true;
    caption.textContent = "Leyendo...";
    try {
      const result = await readText(text);
      if (result?.mode === "say_fallback") {
        caption.textContent = "Leyendo (modo alternativo)...";
      }
    } catch (error) {
      caption.textContent = `No se pudo leer: ${error?.message || error}`;
    } finally {
      readButton.disabled = false;
    }
  }

  function cleanup() {
    unsubNotice();
    unsubActivity();
  }
}