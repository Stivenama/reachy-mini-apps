/* Mi Reachy Portal — launcher + red WiFi */

const toastEl = document.getElementById("toast");
const restartBtn = document.getElementById("btn-daemon-restart");
const viewApps = document.getElementById("view-apps");
const viewWifi = document.getElementById("view-wifi");
const tabs = Array.from(document.querySelectorAll(".tab"));

let toastTimer = null;

function toast(text, isError = false, ms = 4500) {
  clearTimeout(toastTimer);
  toastEl.textContent = text;
  toastEl.classList.toggle("is-error", isError);
  toastEl.hidden = false;
  requestAnimationFrame(() => toastEl.classList.add("show"));
  toastTimer = setTimeout(() => {
    toastEl.classList.remove("show");
    setTimeout(() => (toastEl.hidden = true), 300);
  }, ms);
}

/* ---------- Router ---------- */
function route() {
  const hash = (location.hash || "#/apps").replace("#/", "");
  const showWifi = hash === "wifi";
  viewApps.hidden = showWifi;
  viewWifi.hidden = !showWifi;
  tabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.tab === (showWifi ? "wifi" : "apps"));
  });
  if (showWifi) loadWifiStatus();
}
window.addEventListener("hashchange", route);

/* ---------- Aplicaciones ---------- */
function initial(name) {
  const letter = (name || "").trim().charAt(0).toUpperCase();
  return letter && letter !== "Ñ" ? letter : "R";
}

function typeLabel(tipo) {
  return tipo === "polla" ? "Aplicación de Reachy" : "Aplicación propia";
}

async function loadApps() {
  const grid = document.getElementById("apps");
  try {
    const res = await fetch("/api/apps");
    const data = await res.json();
    renderApps(grid, data.apps || []);
  } catch (error) {
    grid.innerHTML = "";
    const empty = document.createElement("p");
    empty.className = "intro__text";
    empty.textContent = `No pude cargar las aplicaciones: ${error.message}`;
    grid.appendChild(empty);
  }
}

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
      restartBtn.hidden = true;
    } else {
      const detail = result.error || result.detail || "error desconocido";
      toast(`No se pudo completar la acción: ${detail}`, true, 8000);
      restartBtn.hidden = false;
    }
  } catch (error) {
    toast(`No se pudo conectar con el portal: ${error.message}`, true, 8000);
    restartBtn.hidden = false;
  } finally {
    setTimeout(loadApps, isStart ? 30000 : 6000);
  }
}

restartBtn.addEventListener("click", async () => {
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
document.getElementById("btn-nacho").addEventListener("click", () => connectTo("NachoNacho", ""));

document.getElementById("wifi-manual").addEventListener("submit", (e) => {
  e.preventDefault();
  const ssid = document.getElementById("manual-ssid").value.trim();
  const password = document.getElementById("manual-pass").value;
  if (!ssid) {
    toast("Escribe el nombre de la red (SSID).", true);
    return;
  }
  connectTo(ssid, password);
});

document.getElementById("btn-hotspot").addEventListener("click", async () => {
  toast("Creando hotspot del robot…");
  try {
    await fetch("/api/wifi/hotspot", { method: "POST" });
    toast("Hotspot solicitado. Únase a reachy-mini-ap.");
  } catch (error) {
    toast(`Error: ${error.message}`, true);
  }
  setTimeout(loadWifiStatus, 6000);
});

/* ---------- Arranque ---------- */
route();
loadApps();
setInterval(loadApps, 15000);