/**
 * Web Audio player for 16 kHz mono PCM frames streamed over the RPC socket
 * (conversation.audio notifications).
 *
 * Primary path: AudioWorklet (pcm-worker.js) playing from a ring buffer on
 * the audio thread — gap-free and immune to main-thread stalls.
 * Fallback path: lookahead BufferSource scheduler (used if the worklet
 * cannot be loaded, so audio always plays).
 */

const WORKLET_URL = "/static/js/pcm-worker.js";
const MAX_AHEAD_S = 0.35;
const START_BUFFER_S = 0.25;
const MAX_QUEUE_S = 4;

export function createPcmPlayer() {
  let ctx = null;
  let workletNode = null;
  let workletOk = false;
  let fallback = null;
  let initPromise = null;
  let disposed = false;
  const pending = [];

  // ---------- utilidades ----------

  function resample(chunk, fromRate, toRate) {
    if (fromRate === toRate) return chunk;
    const outLen = Math.max(1, Math.round((chunk.length * toRate) / fromRate));
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const pos = (i * fromRate) / toRate;
      const i0 = Math.floor(pos);
      const i1 = Math.min(i0 + 1, chunk.length - 1);
      const frac = pos - i0;
      out[i] = chunk[i0] * (1 - frac) + chunk[i1] * frac;
    }
    return out;
  }

  function decode(pcmBase64) {
    const bytes = Uint8Array.from(atob(pcmBase64), (c) => c.charCodeAt(0));
    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
    const float = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float[i] = int16[i] / 32768;
    return float;
  }

  /** Crear el contexto sincrónicamente (dentro de un gesto del usuario). */
  function ensureContext() {
    if (!ctx) ctx = new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
    return ctx;
  }

  // ---------- ruta alternativa: planificador BufferSource ----------

  function createFallback() {
    let nextTime = 0;
    let timer = null;
    const queue = [];

    function scheduleChunk(data) {
      const buffer = ctx.createBuffer(1, data.length, ctx.sampleRate);
      buffer.copyToChannel(data, 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      if (nextTime < ctx.currentTime + 0.03) {
        nextTime = ctx.currentTime + START_BUFFER_S;
      }
      source.start(nextTime);
      nextTime += data.length / ctx.sampleRate;
    }

    function flush() {
      if (queue.length === 0) {
        if (timer != null) {
          clearInterval(timer);
          timer = null;
        }
        return;
      }
      if (ctx.state === "suspended") void ctx.resume();
      const limit = ctx.currentTime + MAX_AHEAD_S;
      while (queue.length > 0 && nextTime < limit) {
        scheduleChunk(queue.shift());
      }
    }

    function push(data16, rate) {
      const out = resample(data16, rate, ctx.sampleRate);
      queue.push(out);
      let total = 0;
      for (const item of queue) total += item.length;
      while (total > ctx.sampleRate * MAX_QUEUE_S && queue.length > 0) {
        queue.shift();
        total = 0;
        for (const item of queue) total += item.length;
      }
      if (timer == null) timer = setInterval(flush, 40);
      flush();
    }

    function dispose() {
      if (timer != null) clearInterval(timer);
      queue.length = 0;
    }

    return { push, dispose };
  }

  // ---------- iniciar rutas ----------

  async function initWorklet() {
    try {
      const audioCtx = ensureContext();
      await audioCtx.audioWorklet.addModule(WORKLET_URL);
      const node = new AudioWorkletNode(audioCtx, "pcm-playback", {
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      node.connect(audioCtx.destination);
      workletNode = node;
      workletOk = true;
      console.info("pcm-player: AudioWorklet activo");
    } catch (err) {
      console.warn("pcm-player: worklet no disponible, usando planificador", err);
      workletOk = false;
      fallback = fallback || createFallback();
    }
  }

  function init() {
    if (initPromise) return initPromise;
    initPromise = initWorklet().then(() => {
      if (pending.length > 0) {
        const queued = pending.splice(0);
        for (const item of queued) post(item.data, item.rate);
      }
    });
    return initPromise;
  }

  function post(data16, rate) {
    if (disposed) return;
    if (workletOk && workletNode) {
      const out = resample(data16, rate, ctx.sampleRate);
      workletNode.port.postMessage(out, [out.buffer]);
    } else if (fallback) {
      fallback.push(data16, rate);
    }
  }

  // ---------- API publica ----------

  /** Llamar desde un gesto del usuario (política de autoplay). */
  function unlock() {
    if (disposed) return;
    ensureContext();
    void init();
  }

  function push(pcmBase64, rate = 16000) {
    if (disposed) return;
    ensureContext(); // reanudar si el navegador suspendió el contexto
    const data16 = decode(pcmBase64);
    if (!workletOk && !fallback) {
      pending.push({ data: data16, rate });
      void init();
      return;
    }
    post(data16, rate);
  }

  function dispose() {
    disposed = true;
    pending.length = 0;
    if (workletNode) {
      try {
        workletNode.disconnect();
      } catch {
        // ya desconectado
      }
      workletNode = null;
    }
    if (fallback) {
      fallback.dispose();
      fallback = null;
    }
    if (ctx) {
      void ctx.close().catch(() => {});
      ctx = null;
    }
    workletOk = false;
  }

  return { push, unlock, dispose };
}