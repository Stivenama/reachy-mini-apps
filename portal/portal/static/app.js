/* Mi Reachy Portal — launcher + red WiFi */
const CLIENT_VERSION = "22";

const toastEl = document.getElementById("toast") || null;
const restartBtn = document.getElementById("btn-daemon-restart") || null;
const viewApps = document.getElementById("view-apps");
const viewMovimientos = document.getElementById("view-movimientos");
const viewWifi = document.getElementById("view-wifi");
const viewLogs = document.getElementById("view-logs");
const tabs = Array.from(document.querySelectorAll(".tab"));

let toastTimer = null;

function on(id, evt, fn) {
  const el = document.getElementById(id);
  if (el) el.addEventListener(evt, fn);
}

function toast(text, isError = false, ms = 4500, url = null) {
  clearTimeout(toastTimer);
  if (!toastEl) return;
  toastEl.classList.toggle("is-error", isError);
  toastEl.textContent = "";
  toastEl.append(text);
  if (url) {
    toastEl.append(" ");
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "Toca para abrir";
    a.className = "toast__link";
    toastEl.append(a);
  }
  toastEl.hidden = false;
  requestAnimationFrame(() => toastEl.classList.add("show"));
  toastTimer = setTimeout(() => {
    toastEl.classList.remove("show");
    setTimeout(() => (toastEl.hidden = true), 300);
  }, ms);
}

/* ---------- Router ---------- */
const TABS = ["apps", "movimientos", "wifi", "logs"];
let logsAutoTimer = null;
let logsAuto = true;

function route() {
  const hash = (location.hash || "#/apps").replace("#/", "");
  const tab = TABS.includes(hash) ? hash : "apps";
  if (viewApps) viewApps.hidden = tab !== "apps";
  if (viewMovimientos) viewMovimientos.hidden = tab !== "movimientos";
  if (viewWifi) viewWifi.hidden = tab !== "wifi";
  if (viewLogs) viewLogs.hidden = tab !== "logs";
  tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.tab === tab));
  if (tab === "movimientos") {
    loadMovimientos();
    startMvPoll();
  } else {
    stopMvPoll();
  }
  if (tab === "wifi") loadWifiStatus();
  if (tab === "logs") {
    loadLogs();
    startLogsAuto();
  } else {
    stopLogsAuto();
  }
}
window.addEventListener("hashchange", route);

/* ---------- Logs ---------- */
async function loadLogs() {
  const out = document.getElementById("logs-out");
  const sel = document.getElementById("log-service");
  if (!out || !sel) return;
  try {
    const res = await fetch(`/api/logs?service=${encodeURIComponent(sel.value)}&lines=250`);
    const data = await res.json();
    const lines = data.lines || [];
    out.textContent = lines.join("\n") || "(sin logs)";
    out.scrollTop = out.scrollHeight;
  } catch (e) {
    out.textContent = "Error al leer logs: " + e.message;
  }
}

function startLogsAuto() {
  if (logsAuto && !logsAutoTimer) logsAutoTimer = setInterval(loadLogs, 5000);
}
function stopLogsAuto() {
  if (logsAutoTimer) {
    clearInterval(logsAutoTimer);
    logsAutoTimer = null;
  }
}

const logRefreshBtn = document.getElementById("btn-log-refresh");
if (logRefreshBtn) logRefreshBtn.addEventListener("click", loadLogs);
const logAutoBtn = document.getElementById("btn-log-auto");
if (logAutoBtn)
  logAutoBtn.addEventListener("click", () => {
    logsAuto = !logsAuto;
    logAutoBtn.textContent = logsAuto ? "Auto: sí" : "Auto: no";
    if (logsAuto) startLogsAuto();
    else stopLogsAuto();
  });
const logServiceSel = document.getElementById("log-service");
if (logServiceSel) logServiceSel.addEventListener("change", loadLogs);

/* ---------- Aplicaciones ---------- */
function initial(name) {
  const letter = (name || "").trim().charAt(0).toUpperCase();
  return letter && letter !== "Ñ" ? letter : "R";
}

function typeLabel(tipo) {
  return tipo === "polla" ? "Aplicación de Reachy" : "Aplicación propia";
}

let lastApps = [];
let lastOpenedTs = 0;

async function loadApps() {
  const grid = document.getElementById("apps");
  try {
    const res = await fetch("/api/apps");
    const data = await res.json();
    lastApps = data.apps || [];
    renderApps(grid, lastApps);
  } catch (error) {
    grid.innerHTML = "";
    const empty = document.createElement("p");
    empty.className = "intro__text";
    empty.textContent = `No pude cargar las aplicaciones: ${error.message}`;
    grid.appendChild(empty);
  }
}

/* Abre la última app iniciada (por voz o botón) en una pestaña nueva. */
async function checkPendingOpen() {
  try {
    const res = await fetch("/api/pending-open");
    const d = await res.json();
    if (!d.slug || !d.ts || d.ts === lastOpenedTs) return;
    const app = lastApps.find((a) => a.slug === d.slug);
    if (!app || !app.url) return;
    lastOpenedTs = d.ts;
    let w = null;
    try {
      w = window.open(app.url, "_blank");
    } catch (e) {
      w = null;
    }
    if (w) {
      toast(`Abriendo ${app.nombre}…`);
      try {
        w.focus();
      } catch (e) {
        /* noop */
      }
    } else {
      // El navegador bloqueó el popup: NUNCA secuestramos la pestaña del portal.
      toast(`Abriendo ${app.nombre}.`, false, 20000, app.url);
    }
  } catch (e) {
    /* noop */
  }
}
setInterval(checkPendingOpen, 2000);

/* Auto-actualización: si el servidor publica otra versión, recarga la página. */
async function checkVersion() {
  try {
    const res = await fetch("/api/version");
    const d = await res.json();
    if (d.v && String(d.v) !== CLIENT_VERSION) {
      location.reload();
    }
  } catch (e) {
    /* noop */
  }
}
setInterval(checkVersion, 30000);

function renderApps(grid, apps) {
  grid.innerHTML = "";
  if (!apps.length) {
    const empty = document.createElement("p");
    empty.className = "intro__text";
    empty.textContent = "No hay aplicaciones registradas.";
    grid.appendChild(empty);
    return;
  }

  apps.forEach((app, index) => {
    const card = document.createElement("article");
    card.className = "app-card" + (app.online ? "" : " is-offline");
    card.style.animationDelay = `${index * 60}ms`;

    const head = document.createElement("div");
    head.className = "app-card__head";

    const mono = document.createElement("div");
    mono.className = "monogram";
    mono.textContent = initial(app.nombre);

    const meta = document.createElement("div");
    meta.className = "app-card__meta";

    const name = document.createElement("span");
    name.className = "app-card__name";
    name.textContent = app.nombre;

    const ttype = document.createElement("span");
    ttype.className = "app-card__type";
    ttype.textContent = typeLabel(app.tipo);

    meta.appendChild(name);
    meta.appendChild(ttype);
    head.appendChild(mono);
    head.appendChild(meta);

    const status = document.createElement("span");
    status.className = "status " + (app.online ? "status--open" : "status--closed");
    const dot = document.createElement("span");
    dot.className = "status__dot";
    status.appendChild(dot);
    status.appendChild(document.createTextNode(app.online ? "Abierta" : "Cerrada"));

    const actions = document.createElement("div");
    actions.className = "app-card__actions";

    if (app.online) {
      const openBtn = document.createElement("a");
      openBtn.className = "btn btn--primary";
      openBtn.href = app.url || "#";
      openBtn.target = "_blank";
      openBtn.rel = "noopener";
      openBtn.textContent = "Abrir";

      const stopBtn = document.createElement("button");
      stopBtn.type = "button";
      stopBtn.className = "btn btn--danger";
      stopBtn.textContent = "Detener";
      stopBtn.addEventListener("click", () => act(app, "stop", stopBtn, false));

      actions.appendChild(openBtn);
      actions.appendChild(stopBtn);
    } else {
      const startBtn = document.createElement("button");
      startBtn.type = "button";
      startBtn.className = "btn btn--primary";
      startBtn.textContent = "Iniciar";
      startBtn.addEventListener("click", () => act(app, "start", startBtn, true));
      actions.appendChild(startBtn);
    }

    card.appendChild(head);
    card.appendChild(status);
    card.appendChild(actions);
    grid.appendChild(card);
  });
}

async function act(app, action, btn, isStart) {
  btn.disabled = true;
  toast(isStart ? "Iniciando aplicación, espere un momento…" : "Deteniendo aplicación…", false, 8000);
  try {
    const res = await fetch(`/api/apps/${app.slug}/${action}`, { method: "POST" });
    const result = await res.json().catch(() => ({}));
    if (result.ok) {
      toast(isStart ? "Aplicación iniciada." : "Aplicación detenida.");
      if (restartBtn) restartBtn.hidden = true;
    } else {
      const detail = result.error || result.detail || "error desconocido";
      toast(`No se pudo completar la acción: ${detail}`, true, 8000);
      if (restartBtn) restartBtn.hidden = false;
    }
  } catch (error) {
    toast(`No se pudo conectar con el portal: ${error.message}`, true, 8000);
    if (restartBtn) restartBtn.hidden = false;
  } finally {
    setTimeout(loadApps, isStart ? 30000 : 6000);
  }
}

if (restartBtn) restartBtn.addEventListener("click", async () => {
  restartBtn.disabled = true;
  toast("Reiniciando el daemon de Reachy, tardará alrededor de un minuto…", false, 10000);
  try {
    const res = await fetch("/api/daemon/restart", { method: "POST" });
    const result = await res.json().catch(() => ({}));
    if (result.ok) {
      toast("Daemon reiniciado.");
      restartBtn.hidden = true;
    } else {
      toast(`No se pudo reiniciar: ${result.error || "sin respuesta"}`, true, 8000);
    }
  } catch (error) {
    toast(`Error: ${error.message}`, true, 8000);
  } finally {
    restartBtn.disabled = false;
    setTimeout(loadApps, 15000);
  }
});

/* ---------- Red WiFi ---------- */
function signalBar(signal) {
  const level = Math.max(0, Math.min(4, Math.round(signal / 25)));
  let html = "";
  for (let i = 1; i <= 4; i++) {
    html += `<span class="bar ${i <= level ? "on" : ""}"></span>`;
  }
  return `<span class="signal" title="Señal ${signal}%">${html}</span>`;
}

async function loadWifiStatus() {
  const box = document.querySelector("#view-wifi .wifi__status");
  try {
    const res = await fetch("/api/wifi/status");
    const data = await res.json();
    const st = data.data || {};
    if (!data.ok && !st.connected_network) {
      box.innerHTML = `<p class="waiting">No se pudo consultar el estado: ${data.detail || data.error || ""}</p>`;
      return;
    }
    const known = (st.known_networks || []).map((n) => `<span class="chip">${n}</span>`).join("");
    box.innerHTML = `
      <p class="kv"><span class="k">Modo</span><span class="v">${st.mode || "—"}</span></p>
      <p class="kv"><span class="k">Red conectada</span><span class="v">${st.connected_network || "—"}</span></p>
      <p class="kv"><span class="k">Redes guardadas</span><span class="v chips">${known || "—"}</span></p>`;
    renderRecovery();
  } catch (error) {
    box.innerHTML = `<p class="waiting">No se pudo consultar: ${error.message}</p>`;
  }
}

function renderRecovery() {
  const box = document.getElementById("wifi-recovery");
  box.innerHTML = `
    <h3 class="panel__title">Recuperación</h3>
    <p class="kv"><span class="k">Hotspot del robot</span><span class="v">reachy-mini-ap</span></p>
    <p class="kv"><span class="k">Contraseña</span><span class="v">reachy-mini</span></p>
    <p class="hint">
      Si pierde el acceso, únase a la red <strong>reachy-mini-ap</strong> del robot
      y abra <strong>http://reachy-mini.local:8090</strong>.
    </p>`;
}

async function doScan() {
  const list = document.getElementById("wifi-list");
  list.innerHTML = `<p class="waiting">Buscando redes…</p>`;
  try {
    const res = await fetch("/api/wifi/scan", { method: "POST" });
    const data = await res.json();
    const nets = (data.networks || []).filter((n) => !n.error);
    if (!nets.length) {
      list.innerHTML = `<p class="waiting">No se encontraron redes o el escaneo falló.</p>`;
      return;
    }
    list.innerHTML = "";
    nets.forEach((net) => {
      const row = document.createElement("div");
      row.className = "net";

      const info = document.createElement("div");
      info.className = "net__info";
      const name = document.createElement("span");
      name.className = "net__name";
      name.textContent = net.ssid;
      const sec = document.createElement("span");
      sec.className = "net__sec";
      sec.textContent = net.security || "Abierta";
      info.appendChild(name);
      info.appendChild(sec);

      const connectBtn = document.createElement("button");
      connectBtn.type = "button";
      connectBtn.className = "btn btn--primary btn--small";

      const isEnterprise = (net.security || "").toUpperCase().includes("802.1X");
      if (isEnterprise) {
        connectBtn.className += " btn--disabled";
        connectBtn.textContent = "Empresarial";
        connectBtn.disabled = true;
        connectBtn.title = "Las redes 802.1X requieren usuario/dominio y no se pueden configurar aquí.";
      } else {
        connectBtn.textContent = "Conectar";
        connectBtn.addEventListener("click", () => {
          let password = "";
          if (net.security) {
            password = window.prompt(`Contraseña de "${net.ssid}":`, "") || "";
            if (!password) {
              toast("Se necesita la contraseña de la red.", true);
              return;
            }
          }
          connectTo(net.ssid, password);
        });
      }

      const signalWrap = document.createElement("span");
      signalWrap.className = "net__signal";
      signalWrap.innerHTML = signalBar(net.signal);
      row.appendChild(signalWrap);
      row.appendChild(info);
      row.appendChild(connectBtn);
      list.appendChild(row);
    });
  } catch (error) {
    list.innerHTML = `<p class="waiting">No se pudo escanear: ${error.message}</p>`;
  }
}

async function connectTo(ssid, password) {
  toast(`Conectando a "${ssid}"… espere unos segundos`, false, 8000);
  try {
    const res = await fetch("/api/wifi/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid, password }),
    });
    const data = await res.json();
    if (data.connected) {
      toast(`Conectado a "${ssid}". Si cambió la dirección, abra http://reachy-mini.local:8090`, false, 10000);
    } else if (data.fallback_hotspot) {
      toast(`No se pudo conectar a "${ssid}". El robot quedó en el hotspot reachy-mini-ap (contraseña: reachy-mini).`, true, 10000);
    } else {
      toast(`No se pudo conectar a "${ssid}": ${data.error || "sin respuesta"}.`, true, 10000);
    }
  } catch (error) {
    toast(`Error: ${error.message}`, true);
  }
  setTimeout(loadWifiStatus, 5000);
}

document.getElementById("btn-scan").addEventListener("click", doScan);
on("btn-nacho", "click", () => connectTo("NachoNacho", ""));

on("wifi-manual", "submit", (e) => {
  e.preventDefault();
  const ssid = document.getElementById("manual-ssid")?.value.trim();
  const password = document.getElementById("manual-pass")?.value || "";
  if (!ssid) {
    toast("Escribe el nombre de la red (SSID).", true);
    return;
  }
  connectTo(ssid, password);
});

on("btn-hotspot", "click", async () => {
  toast("Creando hotspot del robot…");
  try {
    await fetch("/api/wifi/hotspot", { method: "POST" });
    toast("Hotspot solicitado. Únase a reachy-mini-ap.");
  } catch (error) {
    toast(`Error: ${error.message}`, true);
  }
  setTimeout(loadWifiStatus, 6000);
});

/* ---------- Asistente (widget) ---------- */
const assistantEl = document.getElementById("assistant");
const assistantStateEl = document.getElementById("assistant-state");
const settingsBtn = document.getElementById("btn-assistant-settings");
const panel = document.getElementById("assistant-panel");

const STATE_LABELS = { idle: "En espera", listening: "Escuchando…", thinking: "Pensando…", speaking: "Hablando…" };

async function loadAssistantState() {
  try {
    const res = await fetch("/api/assistant/state");
    const data = await res.json();
    const d = data.data || {};
    const state = d.state || "idle";
    if (assistantEl) {
      assistantEl.className = "assistant";
      assistantEl.classList.add("is-" + state);
    }
    if (assistantStateEl) assistantStateEl.textContent = !data.ok ? "No conectado" : d.ready ? "✓ Listo para escuchar" : STATE_LABELS[state] || state;
    const youEl = document.getElementById("assistant-you");
    const aiEl = document.getElementById("assistant-ai");
    if (youEl) {
      const t = (d.user_text || "").slice(0, 140);
      youEl.textContent = t ? "Tú: " + t : "";
      youEl.hidden = !t;
    }
    if (aiEl) {
      const t = (d.assistant_text || "").slice(0, 140);
      aiEl.textContent = t ? "Asistente: " + t : "";
      aiEl.hidden = !t;
    }
  } catch (e) {
    if (assistantStateEl) assistantStateEl.textContent = "No conectado";
  }
}

on("btn-assistant-stop", "click", async () => {
  try { const r=await fetch("/api/assistant/stop-speech", {method:"POST"}); const d=await r.json(); if(!d.ok) throw new Error("El asistente no está disponible"); await loadAssistantState(); }
  catch(e) { toast(e.message,true); }
});

let currentVoiceId = "";
let voiceSearchTimer = null;

async function populateVoiceSelect(current, search = "") {
  const sel = document.getElementById("set-voice");
  if (!sel) return;
  try {
    const q = (search || "").trim();
    const url = q
      ? `/api/elevenlabs/voices?search=${encodeURIComponent(q)}`
      : "/api/elevenlabs/voices";
    const res = await fetch(url);
    const d = await res.json();
    sel.replaceChildren();
    const voices = d.voices || [];
    if (!voices.length) {
      sel.appendChild(new Option(
        q ? `Sin resultados para "${q}"` : "No hay voces disponibles",
        ""
      ));
      if (current) {
        sel.appendChild(new Option(current, current));
        sel.value = current;
      }
      return;
    }
    if (!current) sel.appendChild(new Option("— Seleccionar voz —", ""));
    for (const v of voices) {
      const labels = v.labels || {};
      const desc = [labels.accent, labels.gender].filter(Boolean).join(", ");
      let label = desc ? `${v.name} (${desc})` : v.name;
      if (v.free === false) label += " ⚠ no en tu plan";
      sel.appendChild(new Option(label, v.voice_id));
    }
    if (current) {
      sel.value = current;
      if (sel.value !== current) {
        sel.insertBefore(new Option(current, current), sel.firstChild);
        sel.value = current;
      }
    }
  } catch (e) {
    sel.replaceChildren(new Option("Error cargando voces", ""));
  }
}

async function populateSettings() {
  try {
    const res = await fetch("/api/assistant/state");
    const data = await res.json();
    const c = (data.data || {}).config || {};
    document.getElementById("set-personality").value = c.personality || "";
    currentVoiceId = c.elevenlabs_voice_id || "";
    const searchEl = document.getElementById("voice-search");
    if (searchEl) searchEl.value = "";
    await populateVoiceSelect(currentVoiceId, "");
  } catch (e) {}
}

async function saveSettings() {
  const payload = {
    personality: document.getElementById("set-personality").value,
  };
  const voiceEl = document.getElementById("set-voice");
  if (voiceEl && voiceEl.value) payload.elevenlabs_voice_id = voiceEl.value;
  try {
    const res = await fetch("/api/assistant/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    toast(data.ok ? "Ajustes guardados." : `Error: ${data.detail || data.error || "desconocido"}`, !data.ok);
    if (panel) panel.hidden = true;
  } catch (e) {
    toast(`Error: ${e.message}`, true);
  }
}

if (settingsBtn) settingsBtn.addEventListener("click", () => {
  if (!panel) return;
  panel.hidden = !panel.hidden;
  if (!panel.hidden) populateSettings();
});
on("voice-search", "input", (e) => {
  clearTimeout(voiceSearchTimer);
  const q = e.target.value;
  voiceSearchTimer = setTimeout(() => populateVoiceSelect(currentVoiceId, q), 300);
});
on("btn-close-assistant", "click", () => { if (panel) panel.hidden = true; });
on("btn-save-assistant", "click", saveSettings);
on("btn-assistant-greet", "click", async () => {
  toast("Saludando…");
  try { await fetch("/api/assistant/greet", { method: "POST" }); }
  catch (e) { toast(`Error: ${e.message}`, true); }
});

async function greetOnOpen() {
  try {
    const res = await fetch("/api/assistant/state");
    const data = await res.json();
    if (data.data && data.data.state === "idle") {
      await fetch("/api/assistant/greet", { method: "POST" });
    }
  } catch (e) { /* noop */ }
}

/* ---------- Movimientos (bailes y gestos) ---------- */
const mvState = { cat: "bailes", loaded: {}, moves: [], last: null };
let mvPoll = null;

function prettyMove(m) {
  return String(m).replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

async function loadMovimientos(force = false) {
  const grid = document.getElementById("mv-grid");
  if (!grid) return;
  const cat = mvState.cat;
  if (!force && mvState.loaded[cat] && mvState.moves.length) {
    renderMvGrid();
    return;
  }
  grid.innerHTML = "";
  const wait = document.createElement("p");
  wait.className = "waiting";
  wait.textContent = "Cargando movimientos (desde Hugging Face)…";
  grid.append(wait);
  try {
    const res = await fetch(`/api/movimientos?categoria=${encodeURIComponent(cat)}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "error del robot");
    mvState.moves = data.moves || [];
    mvState.loaded[cat] = true;
    renderMvGrid();
  } catch (e) {
    grid.innerHTML = "";
    const err = document.createElement("p");
    err.className = "waiting";
    err.textContent = "Error al cargar: " + e.message;
    grid.append(err);
  }
}

function renderMvGrid() {
  const grid = document.getElementById("mv-grid");
  if (!grid) return;
  const q = (document.getElementById("mv-search")?.value || "").toLowerCase();
  const list = mvState.moves.filter((m) => String(m).toLowerCase().includes(q));
  grid.innerHTML = "";
  if (!list.length) {
    const p = document.createElement("p");
    p.className = "waiting";
    p.textContent = "Sin resultados.";
    grid.append(p);
    return;
  }
  for (const m of list) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mv-item";
    b.textContent = prettyMove(m);
    b.addEventListener("click", () => playMove(m));
    grid.append(b);
  }
}

async function playMove(m) {
  mvState.last = m;
  const duracion = document.getElementById("mv-duracion");
  if (duracion && duracion.checked) {
    const variados = document.getElementById("mv-variados");
    startPrograma(variados && variados.checked ? null : m);
    return;
  }
  try {
    const res = await fetch("/api/movimientos/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ categoria: mvState.cat, move: m }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "no se pudo reproducir");
    toast(`Reproduciendo: ${prettyMove(m)}`);
    refreshMvRunning();
  } catch (e) {
    toast(`Error: ${e.message}`, true);
  }
}

async function mvAction(path, body, quiet = false) {
  try {
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json();
    if (!data.ok && !data.data && data.activo === undefined) {
      throw new Error(data.error || data.detail || "falló");
    }
    return data;
  } catch (e) {
    if (!quiet) toast(`Error: ${e.message}`, true);
    return null;
  }
}

function fmtRestante(seg) {
  const m = Math.floor(seg / 60);
  const s = Math.round(seg % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

async function refreshMvRunning() {
  const el = document.getElementById("mv-status");
  const progEl = document.getElementById("mv-prog-status");
  const [run, prog] = await Promise.all([
    mvAction("/api/movimientos/running", null, true),
    mvAction("/api/movimientos/programa", null, true),
  ]);
  if (progEl) {
    if (prog && prog.activo) {
      const que = prog.modo === "fijo" && prog.move ? prettyMove(prog.move) : "variados";
      progEl.textContent = `⏳ ${que}: quedan ${fmtRestante(prog.quedan)}`;
      progEl.dataset.active = "1";
    } else if (progEl.dataset.active) {
      delete progEl.dataset.active;
      progEl.textContent = "Programa finalizado.";
      setTimeout(() => {
        if (!progEl.dataset.active) progEl.textContent = "";
      }, 5000);
    }
  }
  if (!el) return;
  if (prog && prog.activo) {
    el.textContent = "En programa";
    return;
  }
  const d = run && run.data !== undefined ? run.data : null;
  const names = Array.isArray(d) ? d : (d && (d.running || d.moves)) || [];
  const list = (Array.isArray(names) ? names : [])
    .map((x) => (typeof x === "string" ? x : x && (x.name || x.move_name || x.type)))
    .filter(Boolean);
  el.textContent = list.length ? `En movimiento: ${list.map(prettyMove).join(", ")}` : "En reposo";
}

function startMvPoll() {
  refreshMvRunning();
  if (!mvPoll) mvPoll = setInterval(refreshMvRunning, 2500);
}

function stopMvPoll() {
  if (mvPoll) {
    clearInterval(mvPoll);
    mvPoll = null;
  }
}

on("mv-search", "input", () => renderMvGrid());
on("mv-duracion", "change", (e) => {
  const act = !!e.target.checked;
  const min = document.getElementById("mv-min");
  const varChk = document.getElementById("mv-variados");
  const minLabel = document.getElementById("mv-min-label");
  const varLabel = document.getElementById("mv-variados-label");
  if (min) min.disabled = !act;
  if (varChk) varChk.disabled = !act;
  if (minLabel) minLabel.classList.toggle("is-off", !act);
  if (varLabel) varLabel.classList.toggle("is-off", !act);
  const hint = document.getElementById("mv-hint");
  if (hint) {
    hint.innerHTML = act
      ? 'Duración activa: toque un baile y <strong>se repetirá</strong> durante el tiempo elegido (con <strong>Variados</strong> encadena bailes distintos). <strong>Detener</strong> lo para cuando usted quiera.'
      : 'Toque un baile y se ejecuta <strong>una vez</strong>. Marque <strong>Duración</strong> si quiere que dure más tiempo: al tocar el baile se repite durante los minutos elegidos (o encadena varios al azar con <strong>Variados</strong>). <strong>Detener</strong> lo para cuando usted quiera.';
  }
});
on("mv-stop", "click", async () => {
  const r = await mvAction("/api/movimientos/programa/stop", {});
  if (r) toast("Detenido.");
  refreshMvRunning();
});

async function startPrograma(move) {
  const inp = document.getElementById("mv-min");
  let min = parseFloat(inp ? inp.value : "1");
  if (!isFinite(min)) min = 1;
  min = Math.min(Math.max(min, 0.25), 60);
  if (inp) inp.value = String(min);
  const data = await mvAction("/api/movimientos/programa", {
    minutos: min,
    categoria: mvState.cat,
    move: move || null,
  });
  if (data && data.ok) {
    toast(`Programa de ${min} min iniciado (${move ? prettyMove(move) : "variados"}).`);
    refreshMvRunning();
  }
}
on("mv-random", "click", () => {
  const list = mvState.moves;
  if (!list.length) return toast("Primero elija una categoría.", true);
  playMove(list[Math.floor(Math.random() * list.length)]);
});
on("mv-wake", "click", async () => {
  const r = await mvAction("/api/movimientos/basico", { accion: "wake_up" });
  if (r) toast("Despertando…");
});
on("mv-sleep", "click", async () => {
  const r = await mvAction("/api/movimientos/basico", { accion: "goto_sleep" });
  if (r) toast("Durmiendo…");
});
document.querySelectorAll(".mv-cat").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mv-cat").forEach((b) => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    mvState.cat = btn.dataset.cat;
    loadMovimientos();
  });
});

/* ---------- Arranque ---------- */
route();
loadApps();
loadAssistantState();
setInterval(loadApps, 15000);
setInterval(loadAssistantState, 500);
