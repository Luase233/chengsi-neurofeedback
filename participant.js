/* Participant-only display: authoritative snapshots in, cue acknowledgements out.
   This window cannot create sessions, scan devices or control acquisition. */
(() => {
  'use strict';
  const $=selector=>document.querySelector(selector);
  const canvas=$('.participant-canvas'),ctx=canvas.getContext('2d',{alpha:false});
  const ringCanvas=document.createElement('canvas'),ringCtx=ringCanvas.getContext('2d',{alpha:false});
  const previewCanvas=document.createElement('canvas'),previewCtx=previewCanvas.getContext('2d',{alpha:false});
  const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const smooth=value=>{const x=Math.max(0,Math.min(1,value));return x*x*(3-2*x);};
  const view={phaseStarted:performance.now(),wearStep:'waiting',wearStarted:0,wearPlaying:false,
    eye:1,awaitingEnd:false,endCount:0,levels:[0,0,0],energy:[0,0,0],exitLevels:[0,0,0],settling:false,guidanceOverride:false,lastTelemetry:0,lastPreview:0};
  const guidanceChannel=typeof BroadcastChannel==='function'?new BroadcastChannel('chengsi-ui-guidance'):null;
  const clientId=crypto.randomUUID(),policy=new ResonanceFeedbackPolicy();
  const audio=new ResonanceAudio({programId:'clear-current-v04'});
  audio.setMuted(true);audio.setScore(0,false);
  const state={snapshot:null,receivedAt:0,entered:false,lease:false,online:false,busy:false,
    problem:'',cueProblem:false,leaseConflict:false,closed:false,presentation:policy.result('inactive'),visualTime:3,
    currentCue:null,cueEpoch:0,recoveryEpoch:0,recoveryPending:false,seenCues:new Set(),waitingAnnounced:new Set(),waitingPending:false,wearBusy:false,wearError:'',heartbeatBusy:false,musicTask:null,musicDirty:false,
    stateRequest:null,socket:null,lastHeartbeat:0,lastReadySent:null,heartbeatQueued:false};
  const cues=window.CalibrationCues;
  const currentSettings=()=>state.snapshot?.presentation || {};
  const phase=()=>state.snapshot?.phase || 'idle';
  const displayOwnsAudio=()=>state.entered && state.lease && state.online && currentSettings().dual_screen===true;
  const trainingScene=()=>TrainingScenes.normalize(currentSettings().training_scene);
  const showingTrainingScene=()=>displayOwnsAudio() && !view.guidanceOverride
    && (['ready','preparing_training','training','resting','completed'].includes(phase())
      || phase()==='paused' && ['training','preparing_training','resting'].includes(state.snapshot?.resume_phase));
  const audioReady=()=>{
    const cue=cues.diagnostics(),music=audio.getState();
    return state.entered && !state.cueProblem && !state.recoveryPending && !state.waitingPending && !cue.completionFailure
      && !(phase()==='ready' && (cue.cueBusy ?? cue.instructionBusy)) && cue.unlocked && cue.state==='running' && !cue.disposed
      && music.ready && music.contextState!=='closed';
  };
  const phaseGuidance={
    preparing_closed:['闭眼前测','先听说明，保持睁眼',''],
    calibrating_closed:['闭眼前测','轻闭双眼，自然放松','结束时会有声音提醒'],
    closed_complete:['闭眼前测','这一段已完成','可以睁眼休息'],
    preparing_training:['训练准备','请先听开始引导',''],
    resting:['轮间休息','这一轮已完成，放松一下','下一轮开始前会再次提示'],
    preparing_open:['睁眼前测','先听说明，保持放松',''],
    calibrating_open:['睁眼前测','自然注视中心','正常眨眼即可'],
    ready:['前测已完成','稍作休息，等待训练','准备好后由工作人员开始本轮训练'],
    training:['专注训练','让声音陪伴你的专注','目光自然停留，正常眨眼即可'],
    paused:['','训练已暂停','自然放松，稍候继续'],
    disconnected:['','稍候继续','请自然放松'],
    completed:['','本次训练已完成','休息一下，工作人员会与你回顾结果'],
    error:['','请稍候','工作人员正在准备']};
  function wearGuidance(){
    const t=view.wearPlaying?(performance.now()-view.wearStarted)/1000:Infinity;
    if(view.wearStep==='headphones')return ['',t<3.5?'轻轻戴上耳机':'检查耳后触点是否仍贴合',''];
    if(view.wearStep==='ready')return ['佩戴已确认','准备就绪','请等待工作人员开始前测'];
    if(view.wearStep==='headband')return ['',t<3.2?'戴上头环':t<4.8?'轻轻落到额头':t<8.3?'额头触点贴合':t<13?'调节两侧，舒适贴合':'额头与耳后保持贴合',''];
    return ['','',''];
  }
  const wearPhaseAllowed=()=>['idle','connecting','connected'].includes(phase()) || view.guidanceOverride && ['ready','closed_complete','completed','paused'].includes(phase());
  const idleScreen=()=>view.wearStep==='waiting' && (!displayOwnsAudio() || ['idle','connecting','connected','error'].includes(phase()));
  function showWearStep(step){
    view.wearStep=step;view.wearStarted=performance.now();view.wearPlaying=['headband','headphones'].includes(step) && !reducedMotion;
    view.settling=false;view.guidanceOverride=true;state.wearError='';renderText();
  }
  function renderText(){
    const completionFailure=cues.diagnostics().completionFailure;
    const cueFailed=state.cueProblem || completionFailure?.sessionId===state.snapshot?.session_id && !!completionFailure;
    document.body.dataset.phase=phase();
    document.body.dataset.idle=String(idleScreen());
    $('.participant-brand').hidden=idleScreen();
    const wearActive=wearPhaseAllowed() && ['headband','headphones'].includes(view.wearStep);
    document.body.dataset.wearActive=String(wearActive);
    document.body.dataset.trainingPhoto=String(showingTrainingScene() && TrainingScenes.isPhoto(trainingScene()));
    $('.participant-idle').hidden=!idleScreen();
    canvas.setAttribute('aria-label',idleScreen()?'等待佩戴引导':phase()==='ready'&&!view.guidanceOverride?'前测结束，等待训练':phase().includes('closed')?'闭眼前测引导':['headband','headphones','ready'].includes(view.wearStep)&&wearPhaseAllowed()?'头环与耳机佩戴引导':'随音乐平滑变化的三层声场');
    if(showingTrainingScene())canvas.setAttribute('aria-label',TrainingScenes.modes.find(mode=>mode.id===trainingScene()).label);
    $('.participant-setup').hidden=state.entered;
    $('.participant-tools').hidden=!state.entered;
    $('.participant-enable').disabled=state.busy;
    $('.participant-enable').textContent=state.busy?'正在准备声音…':'启用声音并进入展示';
    $('.setup-status').textContent=state.problem;
    const guide=$('.participant-guide');
    let guidance=displayOwnsAudio() && !view.guidanceOverride?(phaseGuidance[phase()] || wearGuidance()):wearGuidance();
    if(currentSettings().cue_mode==='tone' && phase().startsWith('preparing_'))guidance=[phase()==='preparing_training'?'训练准备':'前测准备','请按工作人员说明准备',''];
    if(phase()==='closed_complete' && view.awaitingEnd)guidance=['闭眼前测','这一段已完成','请等待结束提示'];
    if(state.entered && !state.lease && state.leaseConflict)guidance=['','请稍候','工作人员正在准备展示窗口'];
    else if(cueFailed)guidance=['','请稍候','工作人员正在准备声音提示'];
    if(phase()==='ready' && state.snapshot?.calibration?.reused && !view.guidanceOverride)guidance=['个人基线已就绪','稍作休息，等待训练','准备好后由工作人员开始本轮训练'];
    guide.hidden=!state.entered || idleScreen();
    guide.classList.toggle('is-quiet',displayOwnsAudio() && phase()==='training' && performance.now()-view.phaseStarted>4000 && !cueFailed);
    guide.querySelector('.participant-stage').textContent=guidance[0];
    guide.querySelector('h1 span').textContent=guidance[1];
    guide.querySelector('.participant-hint').textContent=guidance[2];
    const confirm=$('.participant-confirm');
    confirm.hidden=!wearActive || !state.entered || !state.lease;
    confirm.disabled=state.wearBusy || view.wearPlaying || !state.online;
    confirm.textContent=state.wearBusy?'正在确认…':view.wearStep==='headphones'?'耳机已戴好，耳后触点仍贴合':'头环已戴好';
    $('.participant-confirm-status').textContent=wearActive?state.wearError:'';
  }
  guidanceChannel?.addEventListener('message',event=>{
    const message=event.data;
    if(!message || message.action!=='play' || !['headband','headphones','ready'].includes(message.step))return;
    if(message.sessionId!==undefined && message.sessionId!==(state.snapshot?.session_id || null))return;
    if(['preparing_closed','calibrating_closed','preparing_open','calibrating_open','preparing_training','training','resting'].includes(phase()))return;
    showWearStep(message.step);
  });
  async function request(url,body){
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),3000);
    try{
      const response=await fetch(url,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:undefined,
        body:body?JSON.stringify(body):undefined,cache:'no-store',signal:controller.signal});
      const data=await response.json();
      if(!response.ok){const error=new Error(data.detail || data.error?.message || '本地服务暂不可用');error.status=response.status;error.snapshot=data.snapshot;throw error;}
      return data.snapshot || data;
    }finally{clearTimeout(timeout);}
  }
  function cancelCue(){
    state.cueEpoch++;
    if(state.currentCue && !state.currentCue.finished) cancelPlayback();
    state.currentCue=null;
  }
  function cancelPlayback(){
    state.recoveryEpoch++;state.recoveryPending=false;state.waitingPending=false;cues.cancel();
  }
  function playClosedRecovery(){
    const cue=cues.diagnostics();
    if(!displayOwnsAudio() || !cue.unlocked || cue.state!=='running' || currentSettings().cue_mode==='tone' || typeof cues.playRecovery!=='function')return;
    cancelPlayback();
    const epoch=state.recoveryEpoch;state.recoveryPending=true;
    reportReadiness();
    Promise.resolve(cues.playRecovery(currentSettings().cue_mode || 'voice')).catch(()=>false)
      .finally(()=>{if(state.recoveryEpoch===epoch){state.recoveryPending=false;reportReadiness();}});
  }
  function silence(){
    audio.setMuted(true);
    if(audio.getState().playing) audio.pause().catch(()=>{});
  }
  function invalidateLease(message='',conflict=false){
    // Once an instruction is cancelled, do not renew this same preparation as
    // ready: it has already been consumed and must be restarted by the operator.
    if(state.currentCue && currentRequestKey() && phase().startsWith('preparing_'))state.cueProblem=true;
    state.lease=false;state.problem=message;state.leaseConflict=conflict;
    cancelCue();cancelPlayback();silence();renderText();
  }
  function context(){
    const s=state.snapshot,age=performance.now()-state.receivedAt;
    const windowEnd=s?.feedback?.window_end;
    const sentAge=s?.sent_at?Date.now()-Date.parse(s.sent_at):age;
    const feedbackAge=Number.isFinite(s?.feedback?.age_ms)?s.feedback.age_ms+age:sentAge;
    const sampleAge=s?.mode==='live' && Number.isFinite(windowEnd)?Date.now()-windowEnd*1000:0;
    const stale=!s || !state.online || age>2500 || !Number.isFinite(sentAge) || sentAge>2500 || feedbackAge>2500 || sampleAge>2500;
    const valid=s?.phase==='training' && !stale && s?.quality?.valid===true && s?.feedback?.valid===true && Number.isFinite(s.feedback.score);
    return {mode:s?.mode || 'live',phase:s?.phase || 'idle',snapshot:s,online:state.online,stale,valid,score:valid?s.feedback.score:null};
  }
  function syncMusic(){
    const owned=displayOwnsAudio(),settings=currentSettings(),feedback=state.presentation;
    const cue=cues.diagnostics();
    const play=owned && phase()==='training' && settings.sound_enabled===true && !(cue.cueBusy ?? cue.instructionBusy);
    audio.setVolume(Number.isFinite(settings.volume)?settings.volume:.35);
    audio.setMuted(!play);
    audio.setScore(feedback.audioScore,owned && feedback.hasFeedback,{allowReward:owned && feedback.status==='live'});
    state.musicDirty=true;
    if(state.musicTask)return;
    state.musicTask=(async()=>{
      while(state.musicDirty && !state.closed){
        state.musicDirty=false;
        const id=state.snapshot?.program_id;
        if(id && ResonanceAudio.programs.some(program=>program.id===id) && audio.getState().programId!==id) await audio.selectProgram(id);
        const currentCue=cues.diagnostics();
        const shouldPlay=displayOwnsAudio() && phase()==='training' && currentSettings().sound_enabled===true && !(currentCue.cueBusy ?? currentCue.instructionBusy);
        audio.setMuted(!shouldPlay);
        if(!audio.getState().ready)continue;
        if(shouldPlay && !audio.getState().playing)await audio.resume();
        else if(!shouldPlay && audio.getState().playing)await audio.pause();
      }
    })().catch(()=>{state.problem='声音尚未就绪，请工作人员退出展示后重新启用。';state.cueProblem=true;silence();renderText();})
      .finally(()=>{state.musicTask=null;});
  }
  function currentRequestKey(){
    const request=currentSettings().cue_request;
    return request?.id && state.snapshot?.session_id ? state.snapshot.session_id+':'+request.id : null;
  }
  function cueStillCurrent(task){
    return !state.closed && displayOwnsAudio() && state.cueEpoch===task.epoch && currentRequestKey()===task.key
      && phase()==='preparing_'+task.stage;
  }
  async function acknowledge(task){
    if(!task.finished || task.ackBusy || task.acked || !cueStillCurrent(task))return;
    task.ackBusy=true;
    try{
      const snapshot=await request('/api/sessions/'+encodeURIComponent(task.sessionId)+'/commands',
        {command:'cue_finished',command_id:task.commandId,cue_request_id:task.id,client_id:clientId});
      task.acked=true;
      if(state.snapshot?.session_id===task.sessionId && state.cueEpoch===task.epoch)accept(snapshot);
    }catch(error){
      if(cueStillCurrent(task) && error.snapshot)accept(error.snapshot);
      // Keep a completed request for an idempotent acknowledgement retry, never
      // replay the already finished spoken instruction after a network error.
    }finally{task.ackBusy=false;}
  }
  function syncCue(){
    const cue=currentSettings().cue_request,key=currentRequestKey();
    const owns=displayOwnsAudio();
    if(state.currentCue && (state.currentCue.key!==key || !owns || phase()!=='preparing_'+state.currentCue.stage))cancelCue();
    if(!owns)return;
    const events=cues.observe(state.snapshot,{suppressStart:true,cueMode:currentSettings().cue_mode || 'voice'});
    const waitingKey=state.snapshot.session_id+':'+(state.snapshot.calibration?.baseline_id || state.snapshot.calibration?.created_at || 'baseline');
    if(events?.some(event=>event.kind==='complete' && event.stage==='open'))state.waitingAnnounced.add(waitingKey);
    if(phase()==='ready' && state.snapshot.calibration?.valid && state.snapshot.mode!=='replay'
      && !view.guidanceOverride && !state.waitingAnnounced.has(waitingKey) && typeof cues.playWaiting==='function'){
      state.waitingAnnounced.add(waitingKey);
      const epoch=state.recoveryEpoch;state.waitingPending=true;
      Promise.resolve(cues.playWaiting(currentSettings().cue_mode || 'voice',{sessionId:state.snapshot.session_id})).then(complete=>{
        if(epoch!==state.recoveryEpoch)return;
        if(complete!==true && phase()==='ready' && displayOwnsAudio()){state.cueProblem=true;state.problem='等待说明未完整播放，请工作人员重新启用声音。';}
      }).catch(()=>{if(epoch===state.recoveryEpoch && phase()==='ready')state.cueProblem=true;})
        .finally(()=>{if(epoch===state.recoveryEpoch){state.waitingPending=false;renderText();reportReadiness();}});
    }
    if(!cue || !['closed','open','training'].includes(cue.stage) || phase()!=='preparing_'+cue.stage)return;
    const requestOwner=cue.client_id;
    if(requestOwner && requestOwner!==clientId)return;
    if(state.currentCue?.key===key){if(state.currentCue.finished)acknowledge(state.currentCue);return;}
    if(state.seenCues.has(key) || !audioReady())return;
    state.seenCues.add(key);
    const task=state.currentCue={key,id:cue.id,sessionId:state.snapshot.session_id,stage:cue.stage,
      epoch:state.cueEpoch,commandId:crypto.randomUUID(),finished:false,ackBusy:false,acked:false};
    Promise.resolve(cues.playInstruction(cue.stage,cue.mode || currentSettings().cue_mode || 'voice')).then(complete=>{
      if(!cueStillCurrent(task))return;
      if(complete===true){task.finished=true;acknowledge(task);}
      else{state.cueProblem=true;state.problem='提示音未完整播放，请工作人员重新准备这一段。';renderText();heartbeat();}
    }).catch(()=>{if(cueStillCurrent(task)){state.cueProblem=true;renderText();heartbeat();}});
  }
  function refreshFeedback(){
    const failure=cues.diagnostics().completionFailure;
    if(failure && failure.sessionId===state.snapshot?.session_id){
      state.problem='结束提示未完整播放，请工作人员确认被试状态并重新启用声音。';
    }
    state.presentation=policy.update(context(),performance.now());
    syncMusic();renderText();reportReadiness();
  }
  function accept(snapshot){
    if(!snapshot || typeof snapshot!=='object')return;
    const previous=state.snapshot;
    if(previous?.session_id===snapshot.session_id && Number(snapshot.seq)<Number(previous.seq))return;
    const changedSession=previous?.session_id!==snapshot.session_id;
    const oldRequest=currentRequestKey();
    const interruptedClosed=!changedSession && state.entered && state.lease
      && ['preparing_closed','calibrating_closed'].includes(previous?.phase)
      && snapshot.phase!==previous.phase
      && !(previous.phase==='preparing_closed' && snapshot.phase==='calibrating_closed')
      && !(previous.phase==='calibrating_closed' && snapshot.phase==='closed_complete');
    if(changedSession){
      cancelCue();cancelPlayback();policy.clear();state.waitingAnnounced.clear();
      if(previous?.session_id){view.wearStep='waiting';view.wearPlaying=false;state.wearError='';}
    }
    if(changedSession || previous?.phase!==snapshot.phase){
      view.phaseStarted=performance.now();
      view.guidanceOverride=false;
      view.settling=!changedSession && ['calibrating_open','training'].includes(previous?.phase)
        && ['paused','completed','disconnected','ready'].includes(snapshot.phase);
      if(view.settling)view.exitLevels=[...view.levels];
      view.awaitingEnd=!changedSession && previous?.phase==='calibrating_closed' && snapshot.phase==='closed_complete';
      if(view.awaitingEnd)view.endCount=cues.diagnostics().ended?.complete || 0;
      if(changedSession){view.eye=snapshot.phase==='calibrating_closed'?0:1;view.levels=[0,0,0];}
    }
    state.snapshot=snapshot;state.receivedAt=performance.now();state.online=true;
    if(changedSession || oldRequest!==currentRequestKey()){
      state.cueProblem=false;
      if(currentRequestKey() && state.recoveryPending)cancelPlayback();
    }
    const owner=currentSettings().participant_client_id || currentSettings().client_id;
    if(state.lease && owner && owner!==clientId)invalidateLease('另一展示窗口正在使用声音。',true);
    if(currentSettings().dual_screen!==true){cancelCue();cancelPlayback();}
    syncCue();refreshFeedback();
    // Only a real phase interruption opens this recovery path. Contact warnings
    // within the same calibration phase never produce participant speech.
    if(interruptedClosed && !currentSettings().cue_request)playClosedRecovery();
    reportReadiness();
  }
  async function refreshState(){
    if(state.stateRequest || state.closed)return state.stateRequest;
    state.stateRequest=request('/api/state').then(accept).catch(()=>{state.online=false;invalidateLease('请工作人员检查本地服务是否开启。');})
      .finally(()=>{state.stateRequest=null;});
    return state.stateRequest;
  }
  async function heartbeat(){
    if(state.heartbeatBusy || !state.entered || state.closed)return;
    state.heartbeatBusy=true;
    try{
      const ready=audioReady();state.lastReadySent=ready;
      const snapshot=await request('/api/presentation/heartbeat',{client_id:clientId,ready,visible:!document.hidden});
      if(!state.entered || state.closed)return;
      const presentation=snapshot.presentation || {};
      const owner=presentation.participant_client_id || presentation.client_id;
      state.lease=owner===clientId;state.lastHeartbeat=performance.now();state.problem='';state.leaseConflict=false;
      accept(snapshot);
      if(!state.lease)invalidateLease('展示尚未获得声音控制，请工作人员检查另一窗口。',Boolean(owner && owner!==clientId));
    }catch(error){
      if(error.snapshot)accept(error.snapshot);
      invalidateLease(error.status===409?'另一展示窗口正在使用声音，请工作人员切换。':'本地服务暂不可用，请工作人员检查连接。',error.status===409);
    }finally{
      state.heartbeatBusy=false;
      if(state.heartbeatQueued){state.heartbeatQueued=false;reportReadiness();}
    }
  }
  function reportReadiness(){
    if(!state.entered || state.closed || audioReady()===state.lastReadySent)return;
    if(state.heartbeatBusy)state.heartbeatQueued=true;else heartbeat();
  }
  function connectSocket(){
    if(state.closed)return;
    const url=new URL('/ws/live',location.href);url.protocol=url.protocol==='https:'?'wss:':'ws:';
    const socket=state.socket=new WebSocket(url);
    socket.onopen=()=>refreshState();
    socket.onmessage=event=>{
      try{const data=JSON.parse(event.data),snapshot=data.snapshot || data;
        if(state.snapshot && snapshot.session_id!==state.snapshot.session_id)refreshState();else accept(snapshot);
      }catch(_){}
    };
    socket.onclose=()=>{if(!state.closed)setTimeout(connectSocket,1000);};
    socket.onerror=()=>{};
  }
  $('.participant-enable').addEventListener('click',async()=>{
    if(state.busy)return;
    state.busy=true;state.problem='';state.cueProblem=false;
    if(phase()==='ready')state.waitingAnnounced.clear();
    renderText();
    try{
      // Both resume attempts begin inside the gesture before any HTTP awaits.
      audio.setMuted(true);
      const cueUnlock=cues.unlock(),musicUnlock=audio.start();
      const [ready]=await Promise.all([cueUnlock,musicUnlock]);
      if(!ready)throw new Error('声音未启用，请再次点击。');
      state.entered=true;await heartbeat();syncMusic();
    }catch(error){state.problem=error.message || '声音未启用，请工作人员重试。';state.entered=false;silence();}
    finally{state.busy=false;renderText();}
  });
  $('.participant-confirm').addEventListener('click',async()=>{
    if(state.wearBusy || view.wearPlaying || !state.entered || !state.lease || !wearPhaseAllowed() || !['headband','headphones'].includes(view.wearStep))return;
    const step=view.wearStep,sessionId=state.snapshot?.session_id || null;
    state.wearBusy=true;state.wearError='';renderText();
    try{
      const snapshot=await request('/api/presentation/wear-confirmation',{client_id:clientId,session_id:sessionId,step,confirmed:true});
      if((snapshot.session_id || null)!==sessionId)return;
      accept(snapshot);
      if(view.wearStep===step && wearPhaseAllowed())showWearStep(step==='headband'?'headphones':'ready');
    }catch(error){
      if(error.snapshot)accept(error.snapshot);
      state.wearError='确认未送达，请再试一次或告知工作人员。';
    }finally{state.wearBusy=false;renderText();}
  });
  function leave(){
    const wasEntered=state.entered;
    state.entered=false;state.lease=false;state.cueProblem=false;cancelCue();cancelPlayback();silence();renderText();
    if(wasEntered)request('/api/presentation/heartbeat',{client_id:clientId,ready:false,visible:!document.hidden}).catch(()=>{});
  }
  $('.participant-exit').addEventListener('click',leave);
  $('.participant-fullscreen').addEventListener('click',()=>{
    const action=document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen();action.catch(()=>{});
  });
  document.addEventListener('fullscreenchange',()=>{$('.participant-fullscreen').textContent=document.fullscreenElement?'退出全屏':'全屏';});
  let last=performance.now(),lastDraw=0;
  let stillRingKey='';
  function drawRings(width,height,levels,energy,{scale=1,opacity=1,particles=true,linear=false,still=false}={}){
    // A dedicated layer lets entry/exit opacity apply to the whole composition.
    // The shared renderer owns its local transform and alpha state.
    if(ringCanvas.width!==canvas.width || ringCanvas.height!==canvas.height){ringCanvas.width=canvas.width;ringCanvas.height=canvas.height;stillRingKey='';}
    const key=still?canvas.width+':'+canvas.height:'';
    if(still && key===stillRingKey){ctx.drawImage(ringCanvas,0,0,width,height);return;}
    stillRingKey=key;
    const dpr=Math.min(devicePixelRatio || 1,1.5);
    ringCtx.setTransform(dpr,0,0,dpr,0,0);ringCtx.fillStyle='#11151b';ringCtx.fillRect(0,0,width,height);
    const radius=Math.min(height*.30,width*.29)*scale,unit=radius/340;
    ringCtx.save();ringCtx.translate(width*.5-450*unit,height*.46-450*unit);ringCtx.scale(unit,unit);
    drawResonance(ringCtx,1000,900,still?3:state.visualTime,still?[1,1,1]:levels,still?[0,0,0]:energy,
      {intensity:.92,particles,valid:true,linearPresence:linear,previewBackground:'#11151b'});
    ringCtx.restore();
    ctx.save();ctx.globalAlpha=opacity;ctx.drawImage(ringCanvas,0,0,width,height);ctx.restore();
  }
  function drawRestMark(width,height,done,opacity){
    ctx.save();ctx.globalAlpha=opacity;ctx.translate(width*.5,height*.46);
    ctx.strokeStyle=done?'#77aaf1':'#53657a';ctx.lineCap='round';ctx.lineJoin='round';ctx.lineWidth=3;
    ctx.beginPath();ctx.arc(0,0,38,0,Math.PI*2);ctx.stroke();
    ctx.beginPath();
    if(done){ctx.moveTo(-14,0);ctx.lineTo(-4,10);ctx.lineTo(16,-11);}
    else{ctx.moveTo(-7,-12);ctx.lineTo(-7,12);ctx.moveTo(7,-12);ctx.lineTo(7,12);}
    ctx.stroke();ctx.restore();
  }
  function drawWaiting(width,height,opacity,now){
    const pulse=reducedMotion?0:(Math.sin(now/1900)+1)/2;
    const scale=Math.max(.8,Math.min(1.25,height/900));
    ctx.save();ctx.globalAlpha=opacity;ctx.translate(width*.5,height*.43);ctx.scale(scale,scale);
    ctx.fillStyle='#182b42';ctx.beginPath();ctx.arc(0,0,76+pulse*4,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='#294971';ctx.lineWidth=2;ctx.beginPath();ctx.arc(0,0,94+pulse*5,0,Math.PI*2);ctx.stroke();
    ctx.fillStyle='#2b80e8';ctx.beginPath();ctx.arc(0,0,43,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='#f2f7ff';ctx.lineWidth=5;ctx.lineCap='round';ctx.lineJoin='round';
    ctx.beginPath();ctx.moveTo(-17,0);ctx.lineTo(-5,12);ctx.lineTo(20,-14);ctx.stroke();ctx.restore();
  }
  function publishPreview(now){
    if(!guidanceChannel || !state.online || now-view.lastPreview<750 || !canvas.width || !canvas.height)return;
    const owner=currentSettings().participant_client_id || currentSettings().client_id;
    if(owner && owner!==clientId)return;
    const rect=canvas.getBoundingClientRect();if(!rect.width || !rect.height)return;
    view.lastPreview=now;
    const scale=480/rect.width;
    previewCanvas.width=480;previewCanvas.height=Math.round(rect.height*scale);
    previewCtx.drawImage(canvas,0,0,previewCanvas.width,previewCanvas.height);
    // Compose the actual visible instruction and buttons onto the same canvas
    // image. This stays local to the two same-origin windows, at thumbnail size.
    const paintText=(selector,{button=false}={})=>{
      const el=$(selector);if(!el || !el.getClientRects().length)return;
      if(el.closest('.participant-guide') && Number(getComputedStyle($('.participant-guide')).opacity)<.1)return;
      const style=getComputedStyle(el),box=el.getBoundingClientRect();
      if(!box.width || !box.height || Number(style.opacity)<.1)return;
      const x=(box.left-rect.left)*scale,y=(box.top-rect.top)*scale,w=box.width*scale,h=box.height*scale;
      if(button){previewCtx.fillStyle=style.backgroundColor;previewCtx.beginPath();previewCtx.roundRect(x,y,w,h,Math.min(5,h/3));previewCtx.fill();}
      const size=parseFloat(style.fontSize)*scale;
      previewCtx.font=style.fontWeight+' '+size+'px "Segoe UI","Microsoft YaHei",sans-serif';
      previewCtx.fillStyle=style.color;previewCtx.textAlign=button || style.textAlign==='center'?'center':'left';previewCtx.textBaseline='middle';
      previewCtx.fillText(el.innerText.replace(/\s+/g,' ').trim(),previewCtx.textAlign==='center'?x+w/2:x,y+h/2,Math.max(1,w));
    };
    paintText('.participant-brand');paintText('.participant-idle h1');paintText('.participant-idle p');
    paintText('.participant-stage');paintText('.participant-guide h1');paintText('.participant-hint');
    paintText('.participant-confirm',{button:true});paintText('.participant-confirm-status');
    paintText('.participant-setup h1');paintText('.setup-description');paintText('.participant-enable',{button:true});paintText('.setup-status');
    guidanceChannel.postMessage({type:'preview-frame',sessionId:state.snapshot?.session_id || null,clientId,
      phase:phase(),wearStep:view.wearStep,capturedAt:Date.now(),frame:previewCanvas.toDataURL('image/jpeg',.7)});
  }
  function frame(now,background=false){
    if(state.closed)return;
    const dt=Math.min(background?1:.1,(now-last)/1000);last=now;
    const active=displayOwnsAudio(),open=active && phase()==='calibrating_open',training=active && phase()==='training';
    if(open || training && trainingScene()==='rings-feedback')state.visualTime+=dt*(open?.45:1);
    const cueState=cues.diagnostics();
    if(view.awaitingEnd && (cueState.ended?.complete || 0)>view.endCount){view.awaitingEnd=false;renderText();}
    const targetEye=active && (phase()==='calibrating_closed' || phase()==='closed_complete' && view.awaitingEnd)?0:1;
    view.eye=reducedMotion?targetEye:view.eye+(targetEye-view.eye)*(1-Math.exp(-dt*4.5));
    // A throttled background frame keeps the staff thumbnail current even when
    // both pages are tabs in one window. Foreground animation remains 30 fps.
    if(now-lastDraw>=(document.hidden?750:32)){
      const rect=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio || 1,1.5);
      const width=Math.round(rect.width*dpr),height=Math.round(rect.height*dpr);
      if(canvas.width!==width || canvas.height!==height){canvas.width=width;canvas.height=height;}
      ctx.setTransform(dpr,0,0,dpr,0,0);
      ctx.fillStyle='#11151b';ctx.fillRect(0,0,rect.width,rect.height);
      const music=audio.getState(),age=(now-view.phaseStarted)/1000;
      const eyes=active && ['preparing_closed','calibrating_closed','closed_complete','preparing_open'].includes(phase());
      const waiting=active && !view.guidanceOverride && phase()==='ready';
      const rest=active && !view.guidanceOverride && ['paused','completed','disconnected'].includes(phase());
      view.levels=[0,0,0];view.energy=[0,0,0];
      if(idleScreen()){
        // The idle page is a centered wordmark; no device is shown as worn.
      }else if(showingTrainingScene() && !(training && trainingScene()==='rings-feedback')){
        const scene=trainingScene();
        view.settling=false;
        // Scene movement is independent of signal quality, attention score and
        // music gain. A pause simply holds the current cloud/water position.
        const photo=TrainingScenes.isPhoto(scene) && TrainingScenes.draw(ctx,rect.width,rect.height,
          {mode:scene,now,motion:!reducedMotion && !['paused','completed'].includes(phase())});
        if(!photo){view.levels=[1,1,1];drawRings(rect.width,rect.height,view.levels,[0,0,0],{particles:false,linear:true,still:true});}
      }else if(open || training){
        const entry=reducedMotion?1:smooth(age/2);
        view.levels=open?[entry,reducedMotion?1:smooth((age-.6)/2.1),reducedMotion?1:smooth((age-1.4)/2.1)]
          :(music.levels || [0,0,0]).map(level=>level*entry);
        view.energy=training?(music.energy || [0,0,0]):[.025,.02,.025];
        drawRings(rect.width,rect.height,view.levels,view.energy,{scale:.94+.06*entry,opacity:entry,linear:open || ResonanceAudio.isLayeredProgram(music.programId)});
      }else if(eyes){
        drawParticipantEye(ctx,rect.width,rect.height,view.eye,{color:phase()==='calibrating_closed'?'#7193be':'#448ff2'});
      }else if(waiting){
        const exit=reducedMotion?1:smooth(age/1.7);
        if(view.settling && exit<1)drawRings(rect.width,rect.height,view.exitLevels.map(level=>level*(1-exit)),[.02,.01,.01],{scale:1-exit*.55,opacity:1-exit,particles:false,linear:true});
        if(exit>=1)view.settling=false;
        drawWaiting(rect.width,rect.height,reducedMotion?1:smooth((age-.5)/1.4),now);
      }else if(view.settling || rest){
        const exit=reducedMotion?1:smooth(age/1.7);
        if(view.settling && exit<1){
          view.levels=view.exitLevels.map(level=>level*(1-exit));
          drawRings(rect.width,rect.height,view.levels,[.02,.01,.01],{scale:1-exit*.55,opacity:1-exit,particles:false,linear:true});
        }
        if(rest)drawRestMark(rect.width,rect.height,phase()==='completed',exit);
        else if(exit>=1)view.settling=false;
      }else{
        const headphones=['headphones','ready'].includes(view.wearStep);
        const duration=headphones?6:16,elapsed=view.wearPlaying?Math.min(duration,(now-view.wearStarted)/1000):duration;
        if(elapsed>=duration)view.wearPlaying=false;
        const w=Math.min(rect.width*.65,760),h=Math.min(rect.height*.67,585);
        ctx.save();ctx.translate((rect.width-w)/2,rect.height*.44-h/2);
        drawWearGuide(ctx,w,h,state.visualTime,{accent:'#448ff2',foreground:'#edf1f6',muted:'#7d8a9b',surface:'#11151b',
          assembly:true,headphones,sequenceTime:elapsed+(headphones?17:0),motion:reducedMotion?0:1});
        ctx.restore();
      }
      lastDraw=now;
      publishPreview(now);
    }
    if(active && now-view.lastTelemetry>=500){
      view.lastTelemetry=now;
      guidanceChannel?.postMessage({type:'telemetry',sessionId:state.snapshot?.session_id || null,phase:phase(),
        levels:[...(phase()==='training' ? audio.getState().levels || view.levels : view.levels)],energy:[...(phase()==='training' ? audio.getState().energy || view.energy : view.energy)],visualLevels:[...view.levels],trainingScene:trainingScene(),visualTime:state.visualTime});
    }
    if(!background)requestAnimationFrame(frame);
  }
  const heartbeatTimer=setInterval(heartbeat,1000),feedbackTimer=setInterval(refreshFeedback,200),pollTimer=setInterval(refreshState,3000);
  const previewTimer=setInterval(()=>{if(document.hidden && !state.closed)frame(performance.now(),true);},1000);
  window.participantDiagnostics=()=>({clientId,entered:state.entered,lease:state.lease,ready:audioReady(),phase:phase(),
    trainingScene:trainingScene(),scene:TrainingScenes.diagnostics(),
    sessionId:state.snapshot?.session_id || null,visible:!document.hidden,visualGuide:displayOwnsAudio() && phase()==='calibrating_open',
    visualLevels:[...view.levels],visualStep:view.wearStep,eyeOpenness:view.eye,waitingPending:state.waitingPending,wearConfirmation:currentSettings().wear_confirmation,
    cueRequestId:state.currentCue?.id || null,seenCueCount:state.seenCues.size,presentation:{...state.presentation},audio:audio.getState(),cues:cues.diagnostics()});
  window.addEventListener('pagehide',()=>{
    if(state.entered)navigator.sendBeacon('/api/presentation/heartbeat',new Blob([JSON.stringify({client_id:clientId,ready:false,visible:false})],{type:'application/json'}));
    state.closed=true;clearInterval(heartbeatTimer);clearInterval(feedbackTimer);clearInterval(pollTimer);clearInterval(previewTimer);
    state.socket?.close();guidanceChannel?.close();cancelCue();cues.dispose();audio.dispose();
  });
  renderText();refreshState();connectSocket();requestAnimationFrame(frame);
})();
