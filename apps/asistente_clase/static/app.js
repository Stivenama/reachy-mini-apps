const $ = (id) => document.getElementById(id);
const STATE = { profile: "", classes: [], filter: "all" };
const RUNNING = ["queued", "running", "uploading", "researching", "analyzing", "generating", "publishing"];
const LABELS = {
  pending: "Pendiente", queued: "En cola", running: "Procesando", uploading: "Subiendo fuente",
  researching: "Investigando fuentes", analyzing: "Analizando pendientes", generating: "Generando",
  publishing: "Publicando", done: "Listo", partial: "Parcial", failed: "Error", ready: "Listo",
  skipped: "Omitido", cancelled: "Cancelado", not_found: "Sin fuentes académicas adecuadas",
};
const PIPELINE = [
  { key: "source", label: "Fuente en Gemini Notebook" },
  { key: "research", label: "Fuentes académicas según el contenido" },
  { key: "pending", label: "Pendientes para la próxima clase" },
  { key: "report", label: "Informe" },
  { key: "infographic", label: "Infografía" },
  { key: "video", label: "Video" },
  { key: "drive", label: "Subida a Google Drive" },
  { key: "classroom", label: "Publicación en Classroom" },
];

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ detail: "respuesta no válida" }));
  if (!response.ok) throw new Error(data.detail || data.error || "Error de la aplicación");
  return data;
}

function save(key, value) { try { localStorage.setItem("asistente." + key, value); } catch (e) {} }
function load(key) { try { return localStorage.getItem("asistente." + key) || ""; } catch (e) { return ""; } }

function message(text, kind) {
  const el = $("message");
  el.textContent = text || "";
  el.className = "message" + (kind ? " " + kind : "");
}

function toast(text, kind) {
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = text;
  $("toasts").append(el);
  setTimeout(() => el.remove(), 4500);
}

function fill(select, items, valueKey, labelKey, placeholder) {
  const previous = select.value;
  select.replaceChildren();
  if (placeholder) {
    const option = document.createElement("option");
    option.value = ""; option.textContent = placeholder;
    select.append(option);
  }
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = item[labelKey];
    select.append(option);
  }
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
}

function askText(title, value = "") {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal";
    overlay.innerHTML = `
      <div class="modal-card" role="dialog" aria-modal="true">
        <div class="modal-head"><h2></h2><button class="btn btn-ghost btn-sm" data-cancel>Cerrar</button></div>
        <div class="field"><label>Nombre</label><input class="input" type="text"></div>
        <div class="actions"><button class="btn btn-primary" data-ok>Aceptar</button></div>
      </div>`;
    overlay.querySelector("h2").textContent = title;
    const input = overlay.querySelector("input");
    input.value = value;
    const close = (result) => { overlay.remove(); resolve(result); };
    overlay.querySelector("[data-cancel]").onclick = () => close(null);
    overlay.querySelector("[data-ok]").onclick = () => close(input.value.trim() || null);
    overlay.addEventListener("keydown", (event) => { if (event.key === "Enter") close(input.value.trim() || null); });
    document.body.append(overlay);
    input.focus();
  });
}

async function loadConfig() {
  const data = await api("api/config");
  STATE.profile = data.active_profile;
  fill($("account"), data.profiles, "id", "label");
  $("account").value = data.active_profile;
}

let TRANSCRIPTS = [];
function renderTranscripts() {
  const term = $("txt-search").value.trim().toLowerCase();
  const items = TRANSCRIPTS.filter((name) => name.toLowerCase().includes(term));
  const previous = load("txt");
  fill($("txt"), items.map((name) => ({ name })), "name", "name");
  if (items.includes(previous)) $("txt").value = previous;
  $("txt-meta").textContent = items.length
    ? items.length + " archivo(s) en la carpeta de transcripciones."
    : "No hay archivos que coincidan.";
}

async function loadTranscripts() {
  const data = await api("api/transcripts");
  TRANSCRIPTS = data.files || [];
  renderTranscripts();
}

async function loadNotebooks() {
  const auto = { id: "__auto__", title: "Crear automáticamente con el nombre del archivo" };
  const data = await api(`api/notebooks?profile_id=${encodeURIComponent(STATE.profile)}`);
  if (!data.ok) { fill($("notebook"), [auto], "id", "title"); toast(data.error, "error"); return; }
  const items = [auto, ...data.notebooks.map((n) => ({ id: n.id, title: n.title || "(sin título)" }))];
  const previous = load("notebook");
  fill($("notebook"), items, "id", "title");
  if ([...$("notebook").options].some((o) => o.value === previous)) $("notebook").value = previous;
}

async function loadCourses() {
  const data = await api(`api/courses?profile_id=${encodeURIComponent(STATE.profile)}`);
  if (!data.ok) { fill($("course"), [], "id", "name", "Error al listar"); toast(data.error, "error"); return; }
  const items = data.courses.map((c) => ({ id: c.id, name: c.name }));
  const previous = load("course");
  fill($("course"), items, "id", "name");
  if ([...$("course").options].some((o) => o.value === previous)) $("course").value = previous;
  await loadTopics();
}

async function loadTopics() {
  const course = $("course").value;
  if (!course) { fill($("topic"), [], "topicId", "name", "Elige un curso primero"); return; }
  const data = await api(`api/topics?profile_id=${encodeURIComponent(STATE.profile)}&course_id=${encodeURIComponent(course)}`);
  if (!data.ok) { fill($("topic"), [], "topicId", "name", "Error al listar"); return; }
  const previous = load("topic");
  fill($("topic"), data.topics.map((t) => ({ topicId: t.topicId, name: t.name })), "topicId", "name", "Sin tema");
  if ([...$("topic").options].some((o) => o.value === previous)) $("topic").value = previous;
}

async function createNotebook() {
  const title = await askText("Nombre del nuevo notebook", ($("txt").value || "").replace(/\.txt$/i, ""));
  if (!title) return;
  try {
    const data = await api("api/notebooks", { profile_id: STATE.profile, title });
    if (!data.ok) { toast(data.error, "error"); return; }
    await loadNotebooks();
    $("notebook").value = data.notebook_id;
    save("notebook", data.notebook_id);
    toast("Notebook creado.", "ok");
  } catch (error) { toast(error.message, "error"); }
}

async function createTopic() {
  const course = $("course").value;
  if (!course) { toast("Elige un curso primero.", "error"); return; }
  const name = await askText("Nombre del nuevo tema", "Material de clases");
  if (!name) return;
  try {
    const data = await api("api/topics", { profile_id: STATE.profile, course_id: course, name });
    if (!data.ok) { toast(data.error, "error"); return; }
    await loadTopics();
    $("topic").value = data.topic_id;
    save("topic", data.topic_id);
    toast("Tema creado.", "ok");
  } catch (error) { toast(error.message, "error"); }
}

function stateClass(value) {
  if (value === "ready" || value === "done") return "done";
  if (RUNNING.includes(value)) return "active";
  if (value === "failed" || value === "error") return "error";
  return "off";
}

function stepValue(row, key) {
  if (key === "source") {
    if (row.notebook_source_id) return "ready";
    return row.status === "uploading" ? "generating" : "pending";
  }
  if (key === "drive") return row.drive_folder_id ? "ready" : "pending";
  return row[key + "_status"] || "pending";
}

function renderLive() {
  const list = $("live-steps");
  const active = STATE.classes.find((row) => RUNNING.includes(row.status));
  if (!active) {
    $("live-empty").hidden = false;
    list.replaceChildren();
    $("live-actions").replaceChildren();
    return;
  }
  $("live-empty").hidden = true;
  const options = JSON.parse(active.options || "{}");
  const items = PIPELINE.map((step) => {
    let value = stepValue(active, step.key);
    if (step.key === "research" && options.research === false) value = "skipped";
    const li = document.createElement("li");
    li.className = stateClass(value);
    const dot = document.createElement("span"); dot.className = "dot";
    const label = document.createElement("span"); label.className = "label"; label.textContent = step.label;
    const state = document.createElement("span"); state.className = "state"; state.textContent = LABELS[value] || value;
    li.append(dot, label, state);
    return li;
  });
  list.replaceChildren(...items);
  const actions = $("live-actions");
  actions.replaceChildren();
  if (RUNNING.includes(active.status)) {
    const cancel = document.createElement("button");
    cancel.className = "btn btn-ghost btn-sm"; cancel.textContent = "Cancelar proceso";
    cancel.onclick = () => doCancel(active.class_id);
    actions.append(cancel);
  }
}

function statusPill(row) {
  const pill = document.createElement("span");
  pill.className = "status-pill " + row.status;
  pill.textContent = LABELS[row.status] || row.status;
  return pill;
}

function meta(label, value) {
  const box = document.createElement("div");
  box.className = "meta";
  const k = document.createElement("div"); k.className = "k"; k.textContent = label;
  const v = document.createElement("div"); v.className = "v"; v.textContent = value || "—";
  box.append(k, v);
  return box;
}

function renderClasses() {
  const container = $("classes");
  let rows = STATE.classes;
  if (STATE.filter === "running") rows = rows.filter((r) => RUNNING.includes(r.status));
  else if (STATE.filter === "done") rows = rows.filter((r) => r.status === "done");
  else if (STATE.filter === "error") rows = rows.filter((r) => r.status === "failed" || r.status === "partial");

  if (!rows.length) {
    container.replaceChildren(Object.assign(document.createElement("p"), { className: "empty", textContent: "No hay clases en esta vista." }));
    return;
  }

  const cards = rows.map((row) => {
    const card = document.createElement("article");
    card.className = "class";

    const head = document.createElement("div");
    head.className = "class-head";
    const chev = document.createElement("span"); chev.className = "chev"; chev.textContent = "\u203A";
    const title = document.createElement("div"); title.className = "class-title"; title.textContent = row.title || row.txt_name || row.class_id;
    const sub = document.createElement("div"); sub.className = "class-sub";
    sub.textContent = new Date((row.updated_at || row.created_at) * 1000).toLocaleString("es");
    head.append(chev, title, sub, statusPill(row));
    head.onclick = () => card.classList.toggle("open");

    const body = document.createElement("div"); body.className = "class-body";
    const grid = document.createElement("div"); grid.className = "meta-grid";
    grid.append(
      meta("Informe", LABELS[row.report_status] || row.report_status),
      meta("Infografía", LABELS[row.infographic_status] || row.infographic_status),
      meta("Video", LABELS[row.video_status] || row.video_status),
      meta("Investigación", LABELS[row.research_status] || row.research_status),
      meta("Pendientes", LABELS[row.pending_status] || row.pending_status),
      meta("Classroom", LABELS[row.classroom_status] || row.classroom_status)
    );
    body.append(grid);

    if (row.pending_text) {
      const pending = document.createElement("p");
      pending.className = "hint";
      pending.style.whiteSpace = "pre-wrap";
      pending.textContent = "Pendientes detectados: " + row.pending_text;
      body.append(pending);
    }

    if (row.research_text_title || row.research_video_title) {
      const sources = document.createElement("p");
      sources.className = "hint";
      const parts = [];
      if (row.research_text_title) parts.push("Texto: " + row.research_text_title);
      if (row.research_video_title) parts.push("Video: " + row.research_video_title);
      sources.textContent = "Fuentes complementarias — " + parts.join(" · ");
      body.append(sources);
    }

    if (row.error) {
      const error = document.createElement("p"); error.className = "err-text"; error.textContent = row.error;
      body.append(error);
    }

    const actions = document.createElement("div"); actions.className = "class-actions";
    if (row.drive_folder_id) {
      const link = document.createElement("a");
      link.href = `https://drive.google.com/drive/folders/${row.drive_folder_id}`;
      link.target = "_blank"; link.rel = "noopener"; link.textContent = "Abrir carpeta en Drive";
      actions.append(link);
    }
    if (RUNNING.includes(row.status)) {
      const cancel = document.createElement("button");
      cancel.className = "btn btn-ghost btn-sm"; cancel.textContent = "Cancelar";
      cancel.onclick = (event) => { event.stopPropagation(); doCancel(row.class_id); };
      actions.append(cancel);
    }
    if (row.status === "failed" || row.status === "partial" || row.status === "cancelled") {
      const retry = document.createElement("button");
      retry.className = "btn btn-ghost btn-sm"; retry.textContent = "Reintentar";
      retry.onclick = (event) => { event.stopPropagation(); doRetry(row.class_id); };
      actions.append(retry);
    }
    body.append(actions);
    card.append(head, body);
    return card;
  });
  container.replaceChildren(...cards);
}

async function refreshClasses() {
  const data = await api("api/classes");
  STATE.classes = data.classes || [];
  renderClasses();
  renderLive();
}

async function doCancel(classId) {
  try {
    await api(`api/classes/${classId}/cancel`, {});
    toast("Proceso cancelado.");
    await refreshClasses();
  } catch (error) { toast(error.message, "error"); }
}

async function doRetry(classId) {
  try {
    await api(`api/classes/${classId}/retry`, {});
    toast("Reintentando…");
    await refreshClasses();
  } catch (error) { toast(error.message, "error"); }
}

async function processClass() {
  const payload = {
    profile_id: STATE.profile,
    txt_name: $("txt").value,
    notebook_id: $("notebook").value,
    course_id: $("course").value,
    topic_id: $("topic").value,
    report: $("chk-report").checked,
    infographic: $("chk-infographic").checked,
    video: $("chk-video").checked,
    transcript: $("chk-transcript").checked,
    publish: document.querySelector('input[name="publish"]:checked').value === "published",
  };
  if (!payload.txt_name) { message("Selecciona un archivo de transcripción.", "error"); return; }
  if (!payload.notebook_id) { message("Selecciona o crea un notebook.", "error"); return; }
  if (!payload.report && !payload.infographic && !payload.video && !payload.transcript) {
    message("Marca al menos un material.", "error"); return;
  }
  $("process").disabled = true;
  message("Iniciando…");
  try {
    const data = await api("api/process", payload);
    if (!data.ok) { message(data.error, "error"); return; }
    message("Procesando en segundo plano. Puedes cerrar la ventana.", "ok");
    toast("Clase en proceso.", "ok");
    await refreshClasses();
  } catch (error) {
    message(error.message, "error");
  } finally {
    $("process").disabled = false;
  }
}

// --- Cuenta ---
let addTimer = null;

function openModal() { $("modal").hidden = false; $("add-message").textContent = ""; }
function closeModal() { clearInterval(addTimer); $("modal").hidden = true; }

async function pollAdd() {
  clearInterval(addTimer);
  addTimer = setInterval(async () => {
    try {
      const s = await api("api/profiles/status");
      $("add-message").textContent = s.message || "";
      $("add-log").textContent = (s.log || []).join("\n");
      if (s.step === "done" || s.step === "error") {
        clearInterval(addTimer);
        $("start-add").disabled = false;
        if (s.step === "done") {
          await loadConfig();
          await Promise.all([loadNotebooks(), loadCourses()]);
          toast("Cuenta " + s.email + " agregada.", "ok");
        } else {
          toast(s.message, "error");
        }
      }
    } catch (error) { /* se reintenta */ }
  }, 2000);
}

async function startAddAccount() {
  const email = $("new-email").value.trim();
  if (!email) { $("add-message").textContent = "Escribe el correo."; return; }
  $("start-add").disabled = true;
  $("add-message").textContent = "Iniciando…";
  try {
    const data = await api("api/profiles", { email });
    if (!data.ok) { $("add-message").textContent = data.error; $("start-add").disabled = false; return; }
    pollAdd();
  } catch (error) {
    $("add-message").textContent = error.message;
    $("start-add").disabled = false;
  }
}

// --- Eventos ---
$("account").onchange = async () => {
  STATE.profile = $("account").value;
  save("account", STATE.profile);
  await api("api/config/active", { profile_id: STATE.profile });
  await Promise.all([loadNotebooks(), loadCourses()]);
};
$("txt-search").oninput = renderTranscripts;
$("txt").onchange = () => { save("txt", $("txt").value); };
$("notebook").onchange = () => save("notebook", $("notebook").value);
$("course").onchange = () => { save("course", $("course").value); loadTopics(); };
$("topic").onchange = () => save("topic", $("topic").value);
$("new-notebook").onclick = createNotebook;
$("new-topic").onclick = createTopic;
$("process").onclick = processClass;
$("add-account").onclick = openModal;
$("close-add").onclick = closeModal;
$("start-add").onclick = startAddAccount;
$("relogin").onclick = async () => {
  const profile = $("account").value;
  if (!profile) return;
  openModal();
  $("add-message").textContent = "Reconectando…";
  try {
    const data = await api("api/profiles/relogin", { profile_id: profile });
    if (!data.ok) { $("add-message").textContent = data.error; return; }
    pollAdd();
  } catch (error) { $("add-message").textContent = error.message; }
};
$("filters").onclick = (event) => {
  const button = event.target.closest(".chip");
  if (!button) return;
  STATE.filter = button.dataset.filter;
  [...$("filters").children].forEach((chip) => chip.classList.toggle("active", chip === button));
  renderClasses();
};

(async function init() {
  try {
    await loadConfig();
    if (load("account")) $("account").value = load("account");
    STATE.profile = $("account").value || STATE.profile;
    await Promise.all([loadTranscripts(), loadNotebooks(), loadCourses()]);
    if (load("txt")) $("txt").value = load("txt");
    await refreshClasses();
  } catch (error) {
    message("Sin conexión con la aplicación: " + error.message, "error");
  }
  setInterval(refreshClasses, 2500);
})();
