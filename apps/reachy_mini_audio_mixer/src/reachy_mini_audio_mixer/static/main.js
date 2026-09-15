/** Audio Mixer UI: selector de salida + volumen + sonido de prueba. */

const devices = Array.from(document.querySelectorAll(".device"));
const volumeInput = document.getElementById("volume");
const volumeValue = document.getElementById("volume-value");
const testButton = document.getElementById("test");
const statusEl = document.getElementById("status");

let current = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function setStatus(message, error = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("mixer__status--error", error);
}

function render() {
  for (const button of devices) {
    const active = button.dataset.device === current;
    button.classList.toggle("device--active", active);
  }
  if (current == null) {
    volumeValue.textContent = "--";
    volumeInput.disabled = true;
    return;
  }
  volumeInput.disabled = false;
}

async function refresh() {
  try {
    const state = await api("/api/output");
    current = state.output;
    volumeInput.value = state.volume >= 0 ? state.volume : 50;
    volumeValue.textContent = state.volume >= 0 ? `${state.volume}%` : "--";
    render();
  } catch (error) {
    setStatus(`No se pudo leer el estado: ${error.message}`, true);
  }
}

devices.forEach((button) => {
  button.addEventListener("click", async () => {
    const deviceId = button.dataset.device;
    if (deviceId === current) return;
    try {
      setStatus("Cambiando salida...");
      const state = await api("/api/output", {
        method: "POST",
        body: JSON.stringify({ device: deviceId }),
      });
      current = state.output;
      volumeInput.value = state.volume >= 0 ? state.volume : 50;
      volumeValue.textContent = state.volume >= 0 ? `${state.volume}%` : "--";
      render();
      setStatus("Salida cambiada. Se usará la próxima vez que abras Push to Talk.");
    } catch (error) {
      setStatus(`Error al cambiar: ${error.message}`, true);
    }
  });
});

volumeInput.addEventListener("change", async () => {
  try {
    const state = await api("/api/volume", {
      method: "POST",
      body: JSON.stringify({ volume: Number(volumeInput.value) }),
    });
    volumeInput.value = state.volume;
    volumeValue.textContent = `${state.volume}%`;
  } catch (error) {
    setStatus(`Error de volumen: ${error.message}`, true);
  }
});

testButton.addEventListener("click", async () => {
  try {
    setStatus("Reproduciendo tono de prueba...");
    await api("/api/test", { method: "POST" });
    setStatus("Tono reproducido.");
  } catch (error) {
    setStatus(`Error en la prueba: ${error.message}`, true);
  }
});

refresh();