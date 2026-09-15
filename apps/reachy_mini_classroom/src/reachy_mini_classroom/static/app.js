const $ = id => document.getElementById(id);
const labels = {idle:'Lista para escuchar',starting:'Preparando reconocimiento en español',listening:'Escuchando en español',finishing:'Confirmando la última frase',responding:'Preparando y leyendo el resumen',paused:'Clase guardada · en pausa',complete:'Resumen terminado'};
let selected = '', state = {}, busy = false, userError = '';
$('portal').href = `${location.protocol}//${location.hostname}:8090`;
const GUARDAR_URL='http://127.0.0.1:8765/guardar';
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'No se pudo completar la acción');
  return data;
}
async function act(path, body) {
  if (busy) return;
  busy = true; userError = ''; controls();
  try { await request(path, body); if(path==='api/start' && !body.session) selected=''; }
  catch(error) { userError=error.message; }
  finally { busy=false; await refresh(); }
}
function controls() {
  const listening=['listening','starting'].includes(state.phase), waiting=['finishing','responding'].includes(state.phase);
  $('start').disabled=busy||listening||waiting;
  $('pause').disabled=busy||!listening;
  $('respond').disabled=busy||waiting||!selected||(listening&&selected!==state.session);
  $('resume').disabled=busy||listening||waiting||!selected;
  $('pause-voice').disabled=!waiting;
}
async function refresh() {
  try {
    state=await request('api/status');
    if(!selected) selected=state.session || state.sessions[0]?.id || '';
    const options=state.sessions.map(s=>{const option=document.createElement('option');option.value=s.id;option.textContent=`${s.title} · ${new Date(s.created*1000).toLocaleString('es')}`;return option;});
    $('sessions').replaceChildren(...options);$('sessions').value=selected;
    $('phase').textContent=labels[state.phase]||state.phase;
    $('dot').classList.toggle('live',state.phase==='listening');
    if(state.live_text) $('live-text').textContent=state.live_text;
    else $('live-text').textContent='Aquí verás lo que Reachy va entendiendo. Las frases se confirman al detectar una pausa.';
    $('live-state').textContent=state.live_text?(state.live_final?'Español · frase confirmada':'Español · texto provisional'):'Español · esperando';
    if(state.phase==='listening' && state.asr_lag_seconds>5) $('live-state').textContent=`Español · procesando ${Math.round(state.asr_lag_seconds)} s pendientes`;
    $('mic-level').value=state.level||0;
    const seconds=Math.floor(state.elapsed||0);
    $('clock').textContent=`${Math.floor(seconds/60).toString().padStart(2,'0')}:${(seconds%60).toString().padStart(2,'0')}`;
    $('progress').value=Math.min(seconds,1200);
    $('hint').textContent=seconds>=1200&&state.phase==='listening'?'20 minutos cumplidos. Esperando la siguiente pausa para cerrar el bloque…':'Cierre de bloque: 20 minutos + la siguiente pausa. Voz desactivada durante la escucha.';
    const note=['finishing','responding'].includes(state.phase)?state.progress_note||'':'';
    $('progress-note').textContent=note;$('progress-note').hidden=!note;
    let error=userError||state.error;
    if(selected) {
      const d=await request(`api/sessions/${selected}`);
      const blocks=d.blocks.map(b=>{
        const card=document.createElement('article');card.className='block';
        const title=document.createElement('h3');title.textContent=`Bloque ${b.number} · ${(b.seconds/60).toFixed(1)} min`;
        const badge=document.createElement('p');badge.className='badge';
        const pending=b.pieces.filter(p=>p.status!=='done').length;
        badge.textContent=b.ready?'Guardado y transcrito':(!b.closed?'En curso':'Guardado')+` · ${pending} fragmentos pendientes`;
        const text=document.createElement('p');text.textContent=b.text||'Todavía no hay texto transcrito en este bloque.';
        card.append(title,badge,text);
        const failures=b.pieces.filter(p=>p.error);
        if(failures.length){const warning=document.createElement('p');warning.textContent=failures[0].error;card.append(warning);}
        return card;
      });
      $('blocks').replaceChildren(...blocks);
      $('summary').hidden=!d.summary;$('summary-text').textContent=d.summary;
      // Token changes the moment a summary appears, so an already-cached copy cannot win.
      $('download').href=`api/sessions/${selected}/transcript.txt?v=${d.summary.length}.${d.blocks.length}`;
      $('download').hidden=false;
      if(!error && selected!==state.session) error=d.error;
    }
    $('error').textContent=error||'';$('error').hidden=!error;
    controls();
  } catch(error) { $('error').textContent=`Sin conexión con la app: ${error.message}. La página se reconectará automáticamente.`;$('error').hidden=false; }
}
$('start').onclick=()=>act('api/start',{title:$('title').value.trim()||'Clase'});
$('pause').onclick=()=>act('api/pause',{});
$('respond').onclick=()=>act('api/respond',{session:selected});
$('resume').onclick=()=>act('api/start',{session:selected});
$('pause-voice').onclick=async()=>{try{await request('api/pause-voice',{});}catch(error){userError=error.message;}await refresh();};
$('sessions').onchange=()=>{selected=$('sessions').value;userError='';refresh();};
$('download').onclick=async event=>{
  event.preventDefault();
  const link=$('download'), original='Descargar texto completo';
  if(!selected) return;
  link.textContent='Guardando…';
  try {
    const detail=await request(`api/sessions/${selected}`);
    const content=await (await fetch(`api/sessions/${selected}/transcript.txt?v=${Date.now()}`,{cache:'no-store'})).text();
    const response=await fetch(GUARDAR_URL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:selected,title:detail.title,content})});
    const data=await response.json();
    if(!data.ok) throw new Error(data.error||'No se pudo guardar');
    userError='';link.textContent=`Guardado en ${data.archivo}`;
  } catch(error) {
    link.textContent=original;
    userError=`No se pudo guardar en "transcripciones": ${error.message}. Comprueba que el servicio Guardar transcripciones esté encendido.`;
  }
  await refresh();
};
(async function poll(){await refresh();setTimeout(poll,500);})();
