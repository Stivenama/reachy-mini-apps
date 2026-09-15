/**
 * Talk view (Reachy Hub): push-to-talk conversation.
 * El audio del asistente se reproduce siempre en el parlante del robot.
 * Tocar el orb (o el botón) empieza a escuchar; tocar de nuevo responde
 * con un resumen de toda la conversación + una pregunta.
 */

import {
  applyPersonality,
  getListenState,
  listPersonalities,
  newSession,
  setListening,
  subscribe,
} from "../api.js";
import { ORB_STATES } from "../constants.js";
import { createOrb, mapActivityToState } from "../orb.js";
import { consumePendingApply } from "../pending-apply.js";
import { setPersonality } from "../personality-badge.js";
import { h, prettifyProfileName } from "../ui.js";

const CAPTION_BY_STATE = Object.freeze({
  [ORB_STATES.IDLE]: "Toca el botón para hablar",
  [ORB_STATES.CONNECTING]: "Conectando con el backend...",
  [ORB_STATES.LISTENING]: "Escuchando... toca para responder",
  [ORB_STATES.THINKING]: "Pensando...",
  [ORB_STATES.SPEAKING]: "Hablando...",
  [ORB_STATES.ERROR]: "Error de conexión",
});

export async function mountTalkView({ outlet, signal }) {
  const pending = consumePendingApply();
  const listenStatePromise = getListenState().catch((error) => {
    console.warn("Failed to load listening state", error);
    return null;
  });
  let listening = false;
  let togglePending = false;
  let activePersonality = null;
  let subscription = null;
  let readyToRespond = false;
  let lastTranscript = "";

  const caption = h(
    "p",
    { class: "talk__caption", role: "status", "aria-live": "polite" },
    CAPTION_BY_STATE[ORB_STATES.CONNECTING]
  );
  const transcriptLine = h("p", { class: "talk__transcript", hidden: true });
  const defaultAction = document.querySelector('[data-component="default-personality-action"]');
  if (defaultAction) {
    defaultAction.hidden = true;
    defaultAction.addEventListener("click", onSetDefault);
  }
  const orb = createOrb({
    initialState: ORB_STATES.CONNECTING,
    onStateChange: (state) => {
      caption.textContent = CAPTION_BY_STATE[state] || "";
    },
    holdStates: [ORB_STATES.LISTENING],
  });
  orb.root.disabled = true;
  orb.root.addEventListener("click", onTalkTap);

  const talkButton = h(
    "button",
    {
      type: "button",
      class: "talk__ptt-button",
      "aria-pressed": "false",
    },
    h("span", { class: "talk__ptt-icon", "aria-hidden": "true" }, "🎤"),
    h("span", { class: "talk__ptt-label" }, "Hablar")
  );
  talkButton.addEventListener("click", onTalkTap);

  const newSessionButton = h(
    "button",
    { type: "button", class: "talk__new-session" },
    "Nueva conversación"
  );
  newSessionButton.addEventListener("click", onNewSession);

  const personalitySelect = h("select", { class: "talk__personality-select", "aria-label": "Personalidad" });
  personalitySelect.addEventListener("change", onPersonalityChange);

  signal.addEventListener("abort", cleanup, { once: true });

  const view = h(
    "section",
    { class: "view view--talk" },
    h("div", { class: "talk__orb-wrap" }, orb.root),
    caption,
    transcriptLine,
    h("div", { class: "talk__controls" }, talkButton),
    h("div", { class: "talk__row" }, personalitySelect, newSessionButton)
  );
  outlet.replaceChildren(view);

  await populatePersonalitySelect();

  if (pending) {
    caption.textContent = `Aplicando "${prettifyProfileName(pending.name)}"...`;
    try {
      await pending.promise;
    } catch (error) {
      if (signal.aborted) return;
      orb.setState(ORB_STATES.ERROR);
      caption.textContent = `No se pudo aplicar la personalidad: ${error?.message || error}`;
      return;
    }
    if (signal.aborted) return;
    caption.textContent = CAPTION_BY_STATE[ORB_STATES.CONNECTING];
    void refreshPersonalityState();
  } else {
    void refreshPersonalityState();
  }

  const listenState = await listenStatePromise;
  if (listenState) listening = Boolean(listenState.listening);
  if (signal.aborted) return;
  orb.root.disabled = false;
  syncTalkAria();

  subscription = subscribeConversationEvents({
    onReady: async () => {
      if (!togglePending) {
        try {
          listening = Boolean((await getListenState())?.listening);
        } catch {
          // keep the last known listening state
        }
      }
      if (signal.aborted) return;
      orb.setState(restingState());
      caption.textContent = CAPTION_BY_STATE[restingState()];
      syncTalkAria();
    },
    onNotice: (params) => {
      const message = params?.message;
      if (typeof message !== "string" || !message) return;
      caption.textContent = message;
    },
    onTranscript: ({ role, text, final }) => {
      if (role !== "user" || !listening) return;
      lastTranscript = text || "";
      transcriptLine.hidden = false;
      if (final) {
        if (lastTranscript.trim()) {
          transcriptLine.textContent = `Te escuché: "${lastTranscript.trim()}"`;
        }
      } else {
        transcriptLine.textContent = `Escuchando: "${lastTranscript.trim() || "..."}"`;
      }
    },
    onActivity: (reason) => {
      const next = mapActivityToState(reason);
      if (next == null) return;
      // The server echoes speech_started while processing the audio we flushed
      // on release; the button is already off, so don't flash back to listening.
      if (next === ORB_STATES.LISTENING && !listening) return;
      orb.setState(next);
    },
    onTurn: (state) => {
      if (state === "listening_ready") {
        readyToRespond = true;
        orb.setState(ORB_STATES.LISTENING);
        caption.textContent = "✅ Listo — toca Responder";
        syncTalkAria();
        return;
      }
      if (state === "listening" && readyToRespond) {
        readyToRespond = false;
        caption.textContent = CAPTION_BY_STATE[ORB_STATES.LISTENING];
        syncTalkAria();
        return;
      }
      if (state === "ready") state = ORB_STATES.IDLE;
      if (!Object.values(ORB_STATES).includes(state)) return;
      if (state === ORB_STATES.LISTENING && !listening) return;
      orb.setState(state);
    },
  });

  function cleanup() {
    subscription?.close();
    orb.dispose();
    talkButton.removeEventListener("click", onTalkTap);
    newSessionButton.removeEventListener("click", onNewSession);
    personalitySelect.removeEventListener("change", onPersonalityChange);
    if (defaultAction) {
      defaultAction.hidden = true;
      defaultAction.removeEventListener("click", onSetDefault);
    }
  }

  function restingState() {
    return listening ? ORB_STATES.LISTENING : ORB_STATES.IDLE;
  }

  async function onTalkTap() {
    if (togglePending) return;
    togglePending = true;
    try {
      const data = await setListening(!listening);
      listening = Boolean(data?.listening);
    } catch (error) {
      if (!signal.aborted) {
        caption.textContent = `No se pudo cambiar el estado: ${error?.message || error}`;
      }
      return;
    } finally {
      togglePending = false;
    }
    if (signal.aborted) return;
    if (listening) {
      lastTranscript = "";
      readyToRespond = false;
      transcriptLine.hidden = true;
    }
    orb.setState(restingState());
    caption.textContent = CAPTION_BY_STATE[restingState()];
    syncTalkAria();
  }

  async function onNewSession() {
    try {
      await newSession();
      caption.textContent = "Conversación reiniciada.";
    } catch (error) {
      caption.textContent = `No se pudo reiniciar: ${error?.message || error}`;
    }
  }

  async function populatePersonalitySelect() {
    try {
      const data = await listPersonalities();
      const choices = data?.choices || [];
      personalitySelect.replaceChildren(
        ...choices.map((choice) => h("option", { value: choice }, prettifyProfileName(choice)))
      );
      personalitySelect.value = data?.current || "";
    } catch (error) {
      console.warn("Failed to load personalities", error);
    }
  }

  async function onPersonalityChange() {
    const name = personalitySelect.value;
    if (!name) return;
    caption.textContent = `Aplicando "${prettifyProfileName(name)}"...`;
    try {
      await applyPersonality(name);
      caption.textContent = CAPTION_BY_STATE[restingState()];
      void refreshPersonalityState();
    } catch (error) {
      caption.textContent = `No se pudo aplicar: ${error?.message || error}`;
    }
  }

  async function refreshPersonalityState() {
    const personalityState = await fetchPersonalityState();
    if (signal.aborted || personalityState == null) return;
    activePersonality = personalityState.current;
    setPersonality(personalityState.current);
    if (personalitySelect.value !== personalityState.current) {
      personalitySelect.value = personalityState.current;
    }
    const shouldHide = personalityState.locked || personalityState.current === personalityState.startup;
    if (defaultAction) {
      defaultAction.hidden = shouldHide;
    }
  }

  async function onSetDefault() {
    if (!defaultAction || !activePersonality) return;
    defaultAction.disabled = true;
    caption.textContent = `Guardando "${prettifyProfileName(activePersonality)}" como predeterminada...`;
    try {
      await applyPersonality(activePersonality, { persist: true });
      if (signal.aborted) return;
      defaultAction.hidden = true;
      caption.textContent = `"${prettifyProfileName(activePersonality)}" se usará al inicio.`;
    } catch (error) {
      if (!signal.aborted) {
        caption.textContent = `No se pudo guardar: ${error?.message || error}`;
      }
    } finally {
      defaultAction.disabled = false;
    }
  }

  function syncTalkAria() {
    orb.root.setAttribute("aria-pressed", String(listening));
    orb.root.setAttribute("aria-label", listening ? "Dejar de escuchar y responder" : "Empezar a escuchar");
    talkButton.setAttribute("aria-pressed", String(listening));
    talkButton.setAttribute("aria-label", listening ? "Dejar de escuchar y responder" : "Empezar a escuchar");
    talkButton.classList.toggle("is-ready", listening && readyToRespond);
    const label = talkButton.querySelector(".talk__ptt-label");
    if (label) label.textContent = listening ? (readyToRespond ? "✅ Responder" : "Responder") : "Hablar";
  }
}

async function fetchPersonalityState() {
  try {
    const data = await listPersonalities();
    const current = data?.current;
    if (!current) return null;
    return {
      current,
      startup: data?.startup || "default",
      locked: Boolean(data?.locked),
    };
  } catch {
    return null;
  }
}

function subscribeConversationEvents({ onActivity, onTurn, onReady, onNotice, onTranscript } = {}) {
  if (typeof onActivity !== "function") {
    throw new TypeError("subscribeConversationEvents: onActivity is required");
  }

  const unsubscribeActivity = subscribe("conversation.activity", (params) => {
    const reason = (params?.reason || "").trim();
    if (reason) onActivity(reason);
  });
  const unsubscribeTurn =
    typeof onTurn === "function"
      ? subscribe("conversation.turn", (params) => {
          const state = (params?.state || "").trim();
          if (state) onTurn(state);
        })
      : () => {};
  const unsubscribeNotice =
    typeof onNotice === "function"
      ? subscribe("conversation.notice", (params) => {
          onNotice(params || {});
        })
      : () => {};
  const unsubscribeTranscript =
    typeof onTranscript === "function"
      ? subscribe("conversation.transcript", (params) => {
          onTranscript(params || {});
        })
      : () => {};

  if (typeof onReady === "function") Promise.resolve().then(onReady);

  return {
    close() {
      unsubscribeActivity();
      unsubscribeTurn();
      unsubscribeNotice();
      unsubscribeTranscript();
    },
  };
}