// Receive-only WebRTC client for the installed GStreamer signalling protocol.
// In immersive VR, Quest throttles setInterval / rVFC. Freshness must be
// polled from the XR animation loop; a connected PC is not torn down just
// because a timer failed to fire.
export class CameraStream {
  constructor(video, onState) {
    this.video = video;
    this.onState = onState;
    this.generation = 0;
    this.lastFrame = 0;
    // XR poll keeps this honest; Quest Wi-Fi can still stall briefly.
    this.stallMs = 2500;
    this.recoveryMs = 10000;
    this.autoReconnect = true;
    this.reconnectDelay = 1000;
    this.reconnectTimer = null;
    this.watchTimer = null;
    this.disconnectedAt = 0;
    this.signallingLostAt = 0;
    this.closedByUser = false;
    this.pc = null;
    this.ws = null;
    this.sessionId = null;
    this.statsTimer = null;
    this.fallback = null;
    this.frameCallback = null;
    this.lastDecoded = 0;
    this.lastPackets = 0;
    this.fps = 0;
    this.polling = false;
  }

  fresh() {
    return !!this.pc && this.pc.connectionState === 'connected' &&
      performance.now() - this.lastFrame < this.stallMs;
  }

  mediaDead() {
    if (!this.pc || this.pc.connectionState !== 'connected') return true;
    if (!this.lastFrame) return true;
    return performance.now() - this.lastFrame > this.recoveryMs;
  }

  close(options = {}) {
    const permanent = options.permanent !== false;
    if (permanent) {
      this.closedByUser = true;
      this.autoReconnect = false;
    }
    // Tell the daemon to drop this consumer so webrtcsink can free its encoder.
    if (this.ws && this.sessionId && this.ws.readyState === 1) {
      try {
        this.ws.send(JSON.stringify({type: 'endSession', sessionId: this.sessionId}));
      } catch (_) { /* already closing */ }
    }
    this.generation++;
    clearTimeout(this.reconnectTimer);
    clearTimeout(this.startupTimer);
    clearInterval(this.watchTimer);
    clearInterval(this.statsTimer);
    clearInterval(this.fallback);
    if (this.frameCallback) this.video.cancelVideoFrameCallback?.(this.frameCallback);
    this.ws?.close();
    this.pc?.close();
    this.ws = this.pc = null;
    this.sessionId = null;
    this.frameCallback = null;
    this.statsTimer = this.fallback = null;
    this.video.srcObject = null;
    this.lastFrame = 0;
    this.disconnectedAt = 0;
    this.signallingLostAt = 0;
    this.lastDecoded = 0;
    this.lastPackets = 0;
    this.fps = 0;
    this.polling = false;
  }

  reconnectNow() {
    this.closedByUser = false;
    this.autoReconnect = true;
    this.reconnectDelay = 1000;
    this.connect();
  }

  scheduleReconnect(reason) {
    if (!this.autoReconnect || this.closedByUser) return;
    clearTimeout(this.reconnectTimer);
    const delay = this.reconnectDelay;
    this.reconnectDelay = Math.min(15000, Math.round(this.reconnectDelay * 1.7));
    this.onState(`Reconectando cámara · ${reason}`, false);
    this.reconnectTimer = setTimeout(() => {
      if (!this.autoReconnect || this.closedByUser) return;
      this.connect();
    }, delay);
  }

  fail(reason) {
    if (this.closedByUser) return;
    // Never tear down a healthy media path because a timer looked stale.
    if (this.pc?.connectionState === 'connected' && this.fresh()) return;
    this.onState(reason, false);
    this.close({permanent: false});
    this.scheduleReconnect(reason);
  }

  markFrame() {
    this.lastFrame = performance.now();
  }

  // Called from the XR animation loop (reliable on Quest) and from 2D timers.
  poll() {
    const pc = this.pc;
    if (!pc || this.polling || pc.connectionState !== 'connected') return;
    this.polling = true;
    const generation = this.generation;
    const done = () => { this.polling = false; };
    pc.getStats().then(stats => {
      if (this.generation !== generation || this.pc !== pc) return done();
      let decoded = this.lastDecoded;
      let packets = this.lastPackets;
      let changed = false;
      for (const report of stats.values()) {
        if (report.type !== 'inbound-rtp') continue;
        if (report.kind !== 'video' && report.mediaType !== 'video') continue;
        if (typeof report.framesDecoded === 'number' && report.framesDecoded > decoded) {
          decoded = report.framesDecoded;
          changed = true;
        }
        if (typeof report.packetsReceived === 'number' && report.packetsReceived > packets) {
          packets = report.packetsReceived;
        }
        if (typeof report.framesPerSecond === 'number') this.fps = report.framesPerSecond;
      }
      this.lastDecoded = decoded;
      this.lastPackets = packets;
      if (changed) {
        this.markFrame();
        if (pc.connectionState === 'connected') this.onState('Cámara en directo', true);
      }
      done();
    }).catch(done);
  }

  watchAfterSignallingLoss() {
    clearInterval(this.watchTimer);
    const generation = this.generation;
    this.watchTimer = setInterval(() => {
      if (generation !== this.generation) return clearInterval(this.watchTimer);
      this.poll();
      if (this.fresh()) {
        this.onState('Cámara en directo · señalización en segundo plano', true);
        return;
      }
      if (this.mediaDead()) {
        clearInterval(this.watchTimer);
        this.fail('Se perdió el vídeo tras cortar la señalización');
      }
    }, 400);
  }

  watchDisconnectedGrace() {
    clearInterval(this.watchTimer);
    const generation = this.generation;
    this.watchTimer = setInterval(() => {
      if (generation !== this.generation) return clearInterval(this.watchTimer);
      const state = this.pc?.connectionState;
      if (state === 'connected') {
        this.disconnectedAt = 0;
        clearInterval(this.watchTimer);
        this.poll();
        if (this.fresh()) this.onState('Cámara en directo', true);
        return;
      }
      if (state === 'failed' || state === 'closed') {
        clearInterval(this.watchTimer);
        this.fail('Conexión de vídeo fallida');
        return;
      }
      const waited = this.disconnectedAt ? performance.now() - this.disconnectedAt : 0;
      if (waited > 8000) {
        clearInterval(this.watchTimer);
        this.fail('Conexión de vídeo inestable');
      }
    }, 400);
  }

  connect() {
    this.close({permanent: false});
    this.closedByUser = false;
    const generation = this.generation;
    const alive = () => generation === this.generation && !this.closedByUser;
    this.startupTimer = setTimeout(() => { if (alive() && !this.fresh()) this.fail('El vídeo tardó demasiado en llegar'); }, 20000);
    this.onState('Conectando cámara', false);
    let ws;
    try {
      ws = this.ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/video`);
    } catch (error) {
      this.fail('No se pudo abrir la señalización de vídeo');
      return;
    }
    let sessionId, peerId, requested = false, pending = [], chain = Promise.resolve();
    const send = message => { if (alive() && ws.readyState === 1) ws.send(JSON.stringify(message)); };
    const keepMedia = () => {
      this.signallingLostAt = performance.now();
      this.onState('Cámara en directo · señalización en segundo plano', true);
      this.ws = null;
      this.watchAfterSignallingLoss();
    };
    const failSignalling = () => {
      if (!alive()) return;
      if (this.fresh() || (this.pc?.connectionState === 'connected' && this.lastFrame &&
          performance.now() - this.lastFrame < this.stallMs)) {
        keepMedia();
        return;
      }
      this.fail('Cámara desconectada');
    };
    ws.onerror = failSignalling;
    ws.onclose = failSignalling;
    const ensure = () => {
      if (this.pc) return this.pc;
      const pc = this.pc = new RTCPeerConnection({iceServers: []});
      let decoded = 0, checking = false;
      // Secondary poll for 2D mode. In VR the XR loop owns freshness.
      this.statsTimer = setInterval(() => {
        if (!alive()) return;
        this.poll();
        if (this.lastFrame && this.mediaDead() && pc.connectionState === 'connected') this.fail('Vídeo detenido');
      }, 200);
      pc.onicecandidate = event => { if (event.candidate) send({type:'peer', sessionId, ice:event.candidate.toJSON()}); };
      pc.onconnectionstatechange = () => {
        if (!alive()) return;
        const state = pc.connectionState;
        if (state === 'connected') {
          this.disconnectedAt = 0;
          this.reconnectDelay = 1000;
          this.poll();
          if (this.lastFrame) this.onState('Cámara en directo', true);
        } else if (state === 'failed' || state === 'closed') {
          this.fail('Conexión de vídeo fallida');
        } else if (state === 'disconnected') {
          if (!this.disconnectedAt) this.disconnectedAt = performance.now();
          this.onState('Cámara inestable · recuperando', this.fresh());
          this.watchDisconnectedGrace();
        }
      };
      pc.ontrack = event => {
        if (!alive() || event.track.kind !== 'video') return;
        this.video.srcObject = new MediaStream([event.track]);
        this.video.play().catch(() => this.onState('Pulsa Reconectar para reproducir', false));
        const received = () => {
          if (!alive()) return;
          this.markFrame();
          this.onState('Cámara en directo', true);
          this.frameCallback = this.video.requestVideoFrameCallback(received);
        };
        if (this.video.requestVideoFrameCallback) this.frameCallback = this.video.requestVideoFrameCallback(received);
        else {
          let lastTime = -1;
          this.fallback = setInterval(() => {
            if (this.video.currentTime !== lastTime) {
              lastTime = this.video.currentTime;
              this.markFrame();
              this.onState('Cámara en directo', true);
            }
          }, 150);
        }
        event.track.onended = () => { if (alive()) this.fail('Pista de vídeo finalizada'); };
      };
      pc.ondatachannel = event => { event.channel.onmessage = () => {}; };
      return pc;
    };
    ws.onmessage = event => {
      chain = chain.then(async () => {
        if (!alive()) return;
        const msg = JSON.parse(event.data);
        if (msg.type === 'welcome') {
          peerId = msg.peerId;
          send({type:'setPeerStatus', roles:['listener'], meta:{name:'Clase continua'}});
          send({type:'list'});
        } else if (msg.type === 'list' && !requested) {
          const source = msg.producers?.find(p => p.meta?.name === 'reachymini');
          if (!source) throw Error('No camera producer');
          requested = true; send({type:'startSession', peerId:source.id});
        } else if (msg.type === 'sessionStarted') {
          sessionId = msg.sessionId; this.sessionId = sessionId; ensure();
        } else if (msg.type === 'peer' && msg.sessionId === sessionId) {
          const pc = ensure();
          if (msg.sdp) {
            await pc.setRemoteDescription(msg.sdp);
            // Keep the daemon's bundled audio transport negotiated. The displayed
            // MediaStream below contains only video, so no microphone audio plays.
            for (const ice of pending) await this._addIce(pc, ice);
            pending = [];
            if (msg.sdp.type === 'offer') {
              await pc.setLocalDescription(await pc.createAnswer());
              send({type:'peer', sessionId, sdp:pc.localDescription.toJSON()});
            }
          } else if (msg.ice) {
            if (pc.remoteDescription) await this._addIce(pc, msg.ice);
            else pending.push(msg.ice);
          }
        } else if (msg.type === 'error' || msg.type === 'endSession') {
          throw Error('Camera session ended');
        }
      }).catch(error => {
        if (!alive()) return;
        if (this.fresh() || (this.pc?.connectionState === 'connected' && this.lastFrame &&
            performance.now() - this.lastFrame < this.stallMs)) {
          keepMedia();
          return;
        }
        this.fail(String(error?.message || error));
      });
    };
  }

// The classroom daemon offers audio0 + video1; VR-only streams offer video0.
// Resolve the mid from the actual SDP instead of assuming a video-only producer.
  async _addIce(pc, ice) {
    if (!ice || !pc) return;
    const candidate = typeof ice === 'string' ? ice : ice.candidate;
    // Empty string is end-of-candidates, not a real route.
    if (!candidate) return;
    const index = (typeof ice === 'object' && ice.sdpMLineIndex != null) ? ice.sdpMLineIndex : 0;
    const mids = [...(pc.remoteDescription?.sdp || '').matchAll(/^a=mid:([^\r\n]+)/gm)].map(m => m[1]);
    const normalized = {
      candidate,
      sdpMid: (typeof ice === 'object' && ice.sdpMid) || mids[index] || null,
      sdpMLineIndex: index,
    };
    try {
      await pc.addIceCandidate(normalized);
    } catch (error) {
      // A single bad candidate must not tear down an otherwise healthy session.
      console.warn('ICE candidate ignored', error);
    }
  }
}
