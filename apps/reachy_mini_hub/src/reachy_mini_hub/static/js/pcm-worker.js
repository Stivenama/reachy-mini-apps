/**
 * AudioWorklet that plays PCM (context-rate) from a ring buffer.
 * Runs on the audio thread, isolated from the main thread: it absorbs
 * network jitter and is never starved by browser UI work. Holds the first
 * ~150 ms of audio before producing sound (agile start, gap-free).
 */

class PcmPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ring = new Float32Array(sampleRate * 4);
    this.head = 0;
    this.tail = 0;
    this.buffering = true;
    this.holdSamples = Math.round(sampleRate * 0.25);
    this.port.onmessage = (event) => this.onMessage(event.data);
  }

  available() {
    return (this.head - this.tail + this.ring.length) % this.ring.length;
  }

  onMessage(data) {
    if (!(data instanceof Float32Array) || data.length === 0) return;
    let n = data.length;
    const max = this.ring.length - 1; // never fill 100% (empty vs full)
    if (n > max) n = max;
    const free = max - this.available();
    if (n > free) {
      // Ring full: drop the oldest samples to keep the freshest audio.
      this.tail = (this.tail + (n - free)) % this.ring.length;
    }
    for (let i = 0; i < n; i++) {
      this.ring[this.head] = data[i];
      this.head = (this.head + 1) % this.ring.length;
    }
  }

  process(_inputs, outputs) {
    const ch = outputs[0][0];
    const avail = this.available();

    if (this.buffering) {
      if (avail >= this.holdSamples) {
        this.buffering = false;
      } else {
        ch.fill(0);
        return true;
      }
    }

    const n = Math.min(avail, ch.length);
    for (let i = 0; i < n; i++) {
      ch[i] = this.ring[this.tail];
      this.tail = (this.tail + 1) % this.ring.length;
    }
    for (let i = n; i < ch.length; i++) ch[i] = 0;
    if (this.available() === 0) this.buffering = true;
    return true;
  }
}

registerProcessor("pcm-playback", PcmPlaybackProcessor);