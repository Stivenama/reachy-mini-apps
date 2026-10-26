/* Camera controls run independently of class capture, summaries and playback. */
(() => {
  'use strict';
  const el = id => document.getElementById(id), panel = el('camera-panel'), stage = el('camera-stage');
  const img = el('camera-image'), wrap = el('camera-image-wrap');
  const video = el('camera-video');
  let stream = null, videoLive = false;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const owner = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
  let active = false, visible = true, pending = false, claiming = false, sequence = 0, intent = 0;
  let angles = [0, 0, 0], zoom = 1, offset = {x: 0, y: 0}, anchor = {x: 0, y: 0};
  let imageURL = null, lastFrame = 0, binding = null, keys = new Set(), holds = new Map();
  let lastTap = 0, lastTapPoint = null, lastTick = performance.now(), pointers = new Map(), gesture = null;
  let mapping = {in: '+', out: '-'};
  try { mapping = {...mapping, ...JSON.parse(localStorage.getItem('classroom-camera-keys') || '{}')}; } catch (_) {}
  const message = text => { el('control-state').textContent = text; };
  async function api(path, body) {
    const r = await fetch(`api/view/${path}`, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({owner, ...body}), signal:AbortSignal.timeout(1800)});
    const data = await r.json();
    if (!r.ok) throw Error(data.detail || 'No se pudo controlar el robot.');
    return data;
  }
  function syncControls() {
    el('control-toggle').textContent = active ? 'Soltar control' : 'Controlar Reachy';
    el('control-toggle').setAttribute('aria-pressed', String(active));
    el('view-center').disabled = !active;
    panel.querySelectorAll('[data-axis]').forEach(b => { b.disabled = !active; });
    panel.classList.toggle('controlling', active);
  }
  function stop(reason = 'Control liberado. La clase continúa.') {
    intent++;
    const wasActive = active;
    active = false; keys.clear(); holds.clear(); pointers.clear(); gesture = null; syncControls();
    if (wasActive) fetch('api/view/release', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({owner}), keepalive:true}).catch(() => {});
    message(reason);
  }
  el('control-toggle').onclick = async () => {
    if (active) return stop();
    if (claiming || pending) return;
    if (Date.now() - lastFrame > 2200) return message('Espera a que llegue imagen de la cámara antes de mover a Reachy.');
    claiming = true;
    const attempt = ++intent;
    try {
      const s = await api('claim', {});
      if (attempt !== intent || document.hidden || !visible) { await api('release', {}); return; }
      angles = s.angles.slice(); sequence = 0; active = true;
      el('natural-motion').checked = s.natural;
      syncControls(); stage.focus({preventScroll:true}); message('Tienes el control. Arrastra la imagen o usa las flechas.');
    } catch (e) { if (attempt === intent) message(e.message); }
    finally { claiming = false; }
  };
  el('view-center').onclick = () => { angles = [0, 0, 0]; };
  function fit() {
    const width = stage.clientWidth, height = stage.clientHeight;
    const ratio = videoLive && video.videoWidth ? video.videoWidth / video.videoHeight : (img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 16 / 9);
    const w = Math.min(width, height * ratio), h = w / ratio;
    wrap.style.width = `${w}px`; wrap.style.height = `${h}px`;
    draw();
  }
  function draw() {
    const w = wrap.clientWidth, h = wrap.clientHeight;
    offset.x = clamp(offset.x, -w * (zoom - 1) / 2, w * (zoom - 1) / 2);
    offset.y = clamp(offset.y, -h * (zoom - 1) / 2, h * (zoom - 1) / 2);
    img.style.transform = `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`;
    video.style.transform = img.style.transform;
    el('camera-zoom').value = zoom;
    el('zoom-value').textContent = el('zoom-badge').textContent = `${zoom.toFixed(1)}×`;
  }
  function setZoom(value, point = anchor) {
    const next = clamp(value, 1, 4), scale = next / zoom;
    offset = {x:point.x - (point.x - offset.x) * scale, y:point.y - (point.y - offset.y) * scale};
    zoom = next; draw();
  }
  function local(e) { const r = stage.getBoundingClientRect(); return {x:e.clientX-r.left-r.width/2, y:e.clientY-r.top-r.height/2}; }
  el('camera-zoom').oninput = e => setZoom(Number(e.target.value));
  el('zoom-reset').onclick = () => { zoom = 1; offset = {x:0, y:0}; anchor = {x:0, y:0}; draw(); };
  stage.addEventListener('wheel', e => { e.preventDefault(); anchor = local(e); setZoom(zoom * Math.exp(-e.deltaY * .002), anchor); }, {passive:false});
  stage.addEventListener('pointerdown', e => {
    if (e.target.closest('button') || (e.pointerType === 'mouse' && e.button !== 0)) return;
    e.preventDefault(); stage.focus({preventScroll:true}); stage.setPointerCapture(e.pointerId);
    const p = local(e); pointers.set(e.pointerId, p); anchor = p;
    gesture = {previous:p, start:p, moved:false, pinch:pointers.size > 1};
    if (pointers.size === 2) startPinch();
  });
  function pair() {
    const [a, b] = [...pointers.values()];
    return {distance:Math.hypot(a.x-b.x, a.y-b.y), middle:{x:(a.x+b.x)/2, y:(a.y+b.y)/2}};
  }
  function startPinch() { const p = pair(); gesture = {pinch:true, distance:p.distance, middle:p.middle, moved:true}; }
  stage.addEventListener('pointermove', e => {
    if (!pointers.has(e.pointerId)) { if (e.pointerType === 'mouse') anchor = local(e); return; }
    const p = local(e); pointers.set(e.pointerId, p);
    if (pointers.size >= 2) {
      const next = pair();
      if (!gesture.pinch) startPinch();
      setZoom(zoom * next.distance / Math.max(1, gesture.distance), gesture.middle);
      offset.x += next.middle.x - gesture.middle.x; offset.y += next.middle.y - gesture.middle.y;
      gesture.distance = next.distance; gesture.middle = next.middle; anchor = next.middle; draw(); return;
    }
    if (!gesture || gesture.pinch) return;
    const dx = p.x - gesture.previous.x, dy = p.y - gesture.previous.y;
    gesture.moved ||= Math.hypot(p.x-gesture.start.x, p.y-gesture.start.y) > 5;
    if (el('camera-gesture').value === 'crop') { offset.x += dx; offset.y += dy; draw(); }
    else if (active) {
      angles[0] = clamp(angles[0] - dx * .12 / zoom, -25, 25);
      angles[1] = clamp(angles[1] + dy * .12 / zoom, -20, 20);
    }
    gesture.previous = p;
  });
  function endPointer(e) {
    if (!pointers.has(e.pointerId)) return;
    const tap = e.type === 'pointerup' && gesture && !gesture.moved && !gesture.pinch;
    const p = pointers.get(e.pointerId); pointers.delete(e.pointerId);
    if (tap) {
      const now = performance.now(); anchor = p;
      if (now-lastTap < 330 && lastTapPoint && Math.hypot(p.x-lastTapPoint.x,p.y-lastTapPoint.y)<30) {
        setZoom(zoom < 2 ? 2.5 : 1, p); lastTap = 0;
      } else { lastTap = now; lastTapPoint = p; }
    }
    if (!pointers.size) gesture = null;
    // Lifting one finger after a pinch must never turn into a robot drag.
  }
  ['pointerup','pointercancel','lostpointercapture'].forEach(name => stage.addEventListener(name, endPointer));
  panel.querySelectorAll('[data-axis]').forEach(button => {
    button.onpointerdown = e => {
      if (!active) return; e.preventDefault(); button.setPointerCapture(e.pointerId);
      holds.set(e.pointerId, {axis:['yaw','pitch','body'].indexOf(button.dataset.axis), direction:Number(button.dataset.direction)});
    };
    ['pointerup','pointercancel','lostpointercapture'].forEach(name => button.addEventListener(name, e => holds.delete(e.pointerId)));
    button.onclick = e => { if (e.detail === 0 && active) { const i = ['yaw','pitch','body'].indexOf(button.dataset.axis); angles[i] = clamp(angles[i]+Number(button.dataset.direction)*3, -[25,20,45][i], [25,20,45][i]); } };
  });
  function keyLabels() { el('bind-in').textContent = mapping.in; el('bind-out').textContent = mapping.out; }
  ['in','out'].forEach(name => { el(`bind-${name}`).onclick = () => { binding = name; keys.clear(); el(`bind-${name}`).textContent = 'Pulsa una tecla…'; }; });
  keyLabels();
  const editable = target => target?.matches('input,textarea,select,[contenteditable="true"]');
  document.addEventListener('keydown', e => {
    if (binding) {
      e.preventDefault();
      if (e.key === 'Escape') { binding = null; keyLabels(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey || ['Shift','Control','Alt','Meta','Tab','Enter',' ','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)) return;
      if (e.key === mapping[binding === 'in' ? 'out' : 'in']) return message('Elige una tecla distinta para cada sentido del zoom.');
      mapping[binding] = e.key; binding = null; keyLabels();
      try { localStorage.setItem('classroom-camera-keys', JSON.stringify(mapping)); } catch (_) {}
      stage.focus({preventScroll:true}); return;
    }
    if (e.key === 'Escape') { stop(); exitExpanded(); return; }
    if (editable(e.target) || e.ctrlKey || e.metaKey || e.altKey || !visible || document.hidden) return;
    if (!panel.contains(document.activeElement)) return;
    if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight',mapping.in,mapping.out,'Shift'].includes(e.key)) {
      e.preventDefault(); keys.add(e.key);
    }
  });
  document.addEventListener('keyup', e => keys.delete(e.key));
  document.addEventListener('focusin', e => { if (editable(e.target) || !panel.contains(e.target)) keys.clear(); });
  function exitExpanded() {
    panel.classList.remove('expanded'); document.body.classList.remove('camera-expanded');
    if (document.fullscreenElement === panel) document.exitFullscreen().catch(() => {});
    el('camera-fullscreen').textContent = '⛶ Pantalla completa'; fit();
  }
  el('camera-fullscreen').onclick = async () => {
    if (panel.classList.contains('expanded')) return exitExpanded();
    panel.classList.add('expanded'); document.body.classList.add('camera-expanded');
    el('camera-fullscreen').textContent = '↙ Volver a la clase';
    // CSS expansion also works on mobile browsers without Fullscreen API support.
    try { if (panel.requestFullscreen) await panel.requestFullscreen(); } catch (_) {}
    fit(); stage.focus({preventScroll:true});
  };
  document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement) exitExpanded();
    else if (document.fullscreenElement === panel && !panel.classList.contains('expanded')) {
      // Escape can arrive before the asynchronous fullscreen request completes.
      document.exitFullscreen().catch(() => {});
    }
  });
  el('camera-toggle').onclick = () => {
    visible = !visible; el('camera-content').hidden = !visible;
    el('camera-toggle').textContent = visible ? 'Ocultar cámara' : 'Mostrar cámara';
    el('camera-toggle').setAttribute('aria-pressed', String(visible));
    if (!visible) { stop(); exitExpanded(); stream?.close(); videoLive = false; } else { stream?.reconnectNow(); fit(); }
  };
  window.addEventListener('blur', () => stop('Control liberado al salir de la ventana.'));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { stop(); stream?.close(); videoLive = false; }
    else if (visible) stream?.reconnectNow();
  });
  window.addEventListener('pagehide', () => { stop(); stream?.close(); if (imageURL) URL.revokeObjectURL(imageURL); });
  window.addEventListener('gamepaddisconnected', () => { if (el('gamepad-enabled').checked) stop('Mando desconectado. Activa el control para continuar.'); });
  new ResizeObserver(fit).observe(stage);
  video.onloadedmetadata = fit;
  import('./video.js').then(({CameraStream}) => {
    stream = new CameraStream(video, (_text, healthy) => {
      if (!visible || document.hidden) return;
      if (healthy && stream.fresh()) {
        const first = !videoLive;
        videoLive = true; img.hidden = true; video.hidden = false;
        lastFrame = Date.now(); stage.classList.remove('camera-offline');
        el('camera-state').textContent = `● En vivo · ${Math.round(stream.fps || 0) || '…'} fps`;
        if (first) fit();
      } else if (videoLive && !stream.fresh()) {
        videoLive = false; video.hidden = true; img.hidden = false;
        if (active) stop('Se interrumpió el vídeo. Recuperando la cámara…');
      }
    });
    if (visible && !document.hidden) stream.reconnectNow();
  }).catch(() => { /* Camera snapshots remain available without WebRTC. */ });
  async function frames() {
    // Keep the compatibility path inexpensive while WebRTC recovers on Wi-Fi.
    let delay = stream ? 250 : 80;
    if (videoLive && !stream?.fresh()) { videoLive = false; video.hidden = true; img.hidden = false; }
    if (visible && !document.hidden && !videoLive) {
      try {
        const response = await fetch(`api/camera/frame?t=${Date.now()}`, {cache:'no-store', signal:AbortSignal.timeout(2500)});
        if (!response.ok) throw Error('Cámara no disponible. Reconectando…');
        const url = URL.createObjectURL(await response.blob());
        try { img.src = url; await img.decode(); } catch(e) { URL.revokeObjectURL(url); throw e; }
        if (imageURL) URL.revokeObjectURL(imageURL); imageURL = url;
        if (!videoLive) {
          video.hidden = true; img.hidden = false;
          lastFrame = Date.now(); el('camera-state').textContent = '● En vivo · modo compatible'; stage.classList.remove('camera-offline'); fit();
        }
      } catch (_) {
        if (!videoLive) {
          el('camera-state').textContent = 'Sin imagen · reconectando'; stage.classList.add('camera-offline');
          if (active) stop('Control detenido porque se perdió la imagen.'); delay = 1500;
        }
      }
    }
    setTimeout(frames, delay);
  }
  function tick(now) {
    const dt = Math.min(.06, (now-lastTick)/1000); lastTick = now;
    if (active && Date.now()-lastFrame > 2500) stop('Control detenido porque se perdió la imagen.');
    const movement = [0,0,0];
    const horizontal = Number(keys.has('ArrowLeft')) - Number(keys.has('ArrowRight'));
    movement[keys.has('Shift') ? 2 : 0] += horizontal;
    movement[1] += Number(keys.has('ArrowDown')) - Number(keys.has('ArrowUp'));
    holds.forEach(h => { movement[h.axis] += h.direction; });
    let zoomRate = Number(keys.has(mapping.in)) - Number(keys.has(mapping.out));
    if (active && el('gamepad-enabled').checked && navigator.getGamepads) {
      try {
        const pad = [...navigator.getGamepads()].find(p => p?.connected && p.mapping === 'standard');
        if (pad) {
          const dead = n => Math.abs(n || 0) < .18 ? 0 : (n-Math.sign(n)*.18)/.82;
          movement[0] -= dead(pad.axes[0]); movement[1] += dead(pad.axes[1]); movement[2] -= dead(pad.axes[2]);
          zoomRate += (pad.buttons[7]?.value || 0)-(pad.buttons[6]?.value || 0);
          el('gamepad-state').textContent = 'Mando conectado · stick izquierdo: cabeza · derecho: cuerpo · gatillos: zoom.';
        } else el('gamepad-state').textContent = 'No se detecta un mando estándar. Pulsa un botón del mando para conectarlo.';
      } catch (_) { el('gamepad-state').textContent = 'Este navegador no permite mandos aquí. Usa los controles táctiles o el teclado.'; }
    }
    if (active) angles = angles.map((v,i) => clamp(v + clamp(movement[i],-1,1)*18*dt, -[25,20,45][i], [25,20,45][i]));
    if (visible && !document.hidden && zoomRate) setZoom(zoom + zoomRate*dt*1.2);
    requestAnimationFrame(tick);
  }
  async function send() {
    if (active && !pending) {
      pending = true;
      try { await api('position', {sequence:++sequence, yaw:angles[0], pitch:angles[1], body:angles[2], natural:el('natural-motion').checked}); }
      catch (e) { stop(e.message); }
      finally { pending = false; }
    }
    setTimeout(send, 100);
  }
  fit(); frames(); send(); requestAnimationFrame(tick);
})();
