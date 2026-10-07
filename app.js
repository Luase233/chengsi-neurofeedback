/* Presentation controller. Audio scheduling lives in audio-engine.js. */
(() => {
  'use strict';
  const root = document.getElementById('resonance-training');
  const isOperator = document.documentElement.dataset.view === 'operator';
  const $ = selector => root.querySelector(selector);
  const $$ = selector => Array.from(root.querySelectorAll(selector));
  const programs = ResonanceAudio.programs;
  const state = {
    score: 72, volume: .35, sound: isOperator, playing: true, valid: true,
    programId: 'clear-current-v04', pendingProgram: null, programBusy: false,
    visualTime: 4, levels: null, busy: false, error: null
  };
  try {
    const saved = JSON.parse(localStorage.getItem('chengsi-resonance-preferences'));
    if (Number.isFinite(saved?.score)) state.score = Math.max(0, Math.min(100, saved.score));
    if (Number.isFinite(saved?.volume)) state.volume = Math.max(0, Math.min(.7, saved.volume));
    if (programs.some(program => program.id === saved?.programId)) state.programId = saved.programId;
  } catch (_) {}
  state.levels = ResonanceAudio.isLayeredProgram(state.programId) ? window.EasyGoingProfile.levelsFor(state.score) : ResonanceAudio.levelsFor(state.score, state.valid);
  const options = { intensity: 1, particles: true, valid: true };
  let sessionContext = { mode:'preview', phase:'idle', snapshot:null, busy:false };
  let displayedMeditation = null;
  const feedbackPolicy = new ResonanceFeedbackPolicy();
  let presentation = feedbackPolicy.result('inactive');
  let previewScore = state.score, previewValid = true;
  let lastAudioPhase = 'preview', failedProgram = null;
  let uiDirty = true;
  const audio = new ResonanceAudio({ programId: state.programId, onStatus: () => { uiDirty = true; } });
  audio.setScore(state.score, true);
  audio.setVolume(state.volume);
  audio.setMuted(true);
  const programSelect = $('.program-select');
  programs.forEach(program => {
    const option = document.createElement('option');
    option.value = program.id; option.textContent = program.title;
    programSelect.appendChild(option);
  });
  programSelect.value = state.programId;
  const main = $('.res-canvas');
  const mainCtx = main.getContext('2d', { alpha: false });
  const presets = $$('.preset-card').map(button => ({
    button, score: Number(button.dataset.score), canvas: button.querySelector('canvas'),
    ctx: button.querySelector('canvas').getContext('2d', { alpha: false }),
    originalScore: Number(button.dataset.score), originalCopy: button.querySelector('.preset-copy').innerHTML,
    originalLabel: button.querySelector('canvas').getAttribute('aria-label')
  }));
  let displayedProfile = null;
  $$('.level-meter').forEach(meter => {
    for (let i = 0; i < 6; i++) {
      const dot = document.createElement('i');
      dot.setAttribute('aria-hidden', 'true');
      meter.appendChild(dot);
    }
  });
  const wave = $('.score-wave');
  for (let i = 0; i < 11; i++) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M0 '+(52+i*.65)+' C45 '+(88-i*.9)+' 63 '+(3+i*1.5)+' 110 '+(12+i*1.1)+' S179 '+(59+i)+' 220 '+(38+i*.9));
    path.setAttribute('stroke-width', i === 0 ? '1.8' : '.7');
    path.setAttribute('opacity', String(.85-i*.055));
    wave.appendChild(path);
  }
  function persist() {
    try { localStorage.setItem('chengsi-resonance-preferences', JSON.stringify({ score: previewScore, volume: state.volume, programId: state.programId })); } catch (_) {}
  }
  function announce(message) { $('.a11y-status').textContent = message; }
  function setScore(value) {
    if (sessionContext.mode !== 'preview') return;
    state.score = Math.max(0, Math.min(100, value));
    previewScore = state.score;
    audio.setScore(state.score, state.valid);
    persist(); uiDirty = true;
  }
  $('.score-slider').value = state.score;
  $('.volume-slider').value = Math.round(state.volume*100);
  $('.score-slider').addEventListener('input', event => setScore(Number(event.target.value)));
  presets.forEach(preset => preset.button.addEventListener('click', () => {
    setScore(preset.score);
    announce('声场正在平滑过渡到指数 ' + preset.score);
  }));
  $('.volume-slider').addEventListener('input', event => {
    state.volume = Number(event.target.value)/100;
    audio.setVolume(state.volume); persist(); uiDirty = true;
  });
  $('.volume-slider').addEventListener('change', () => {
    if (isOperator) session.setPresentation({volume:state.volume}).catch(error => {state.error=error.message;uiDirty=true;});
  });
  async function switchProgram(id) {
    if (state.busy || state.programBusy) return;
    state.pendingProgram = id; state.programBusy = true; state.error = null; uiDirty = true;
    try {
      if (!isOperator) await audio.selectProgram(id);
      state.programId = isOperator ? id : audio.getState().programId;
      // A newly selected layered cue begins from its actual audio envelope, not
      // from the previous song's score-to-brightness mapping.
      if (ResonanceAudio.isLayeredProgram(state.programId)) state.levels = (audio.getState().levels || [.55,0,0]).slice();
      thumbsDirty = true;
      persist();
      announce('已切换至 ' + programs.find(program => program.id === state.programId).title);
    } catch (error) {
      failedProgram = id;
      state.error = '音频切换失败，已保留原音频，可重试';
      announce(state.error);
      console.error('Resonance program:', error);
    } finally {
      state.pendingProgram = null; state.programBusy = false; uiDirty = true;
    }
  }
  programSelect.addEventListener('change', async event => {
    failedProgram = null;
    if (sessionContext.mode === 'preview' || !sessionContext.snapshot?.session_id || ['completed','idle'].includes(sessionContext.phase)) await switchProgram(event.target.value);
    else {
      try { await session.setProgram(event.target.value); }
      catch (error) { state.error = error.message; uiDirty = true; }
    }
  });
  function applyAudioMode() {
    if (isOperator || sessionContext.snapshot?.presentation?.dual_screen) {
      audio.setMuted(true);
      if (audio.getState().ready && audio.getState().playing) audio.pause().catch(() => {});
      return;
    }
    const preview = sessionContext.mode === 'preview';
    audio.setMuted(!state.sound || (!preview && sessionContext.phase !== 'training'));
    if (!audio.getState().ready) return;
    const shouldPlay = preview ? state.playing : sessionContext.phase === 'training';
    if (shouldPlay && !audio.getState().playing) audio.resume().catch(() => { state.error = '点击开启声音以继续播放'; uiDirty = true; });
    if (!shouldPlay && audio.getState().playing) audio.pause().catch(() => {});
  }
  const session = new ResonanceSession({ root, getProgram:() => state.programId,
    getPresentation:() => ({cue_mode:$('.operator-cue-mode')?.value || 'voice',volume:state.volume,sound_enabled:state.sound,training_scene:$('.operator-training-scene')?.value || 'lake-trees'}),
    unlockAudio:async () => {
      if (isOperator || sessionContext.snapshot?.presentation?.dual_screen) return;
      // start() resumes the AudioContext before awaiting asset loading.
      audio.setMuted(true);
      await audio.start();
      audio.setScore(0, false);
      audio.setVolume(state.volume);
    },
    onChange:context => {
      const wasPreview = sessionContext.mode === 'preview';
      sessionContext = context;
      if (isOperator && context.snapshot?.presentation) {
        const settings=context.snapshot.presentation;
        state.sound=settings.sound_enabled!==false;
        if (document.activeElement !== $('.volume-slider')) {
          state.volume=settings.volume ?? state.volume;
          $('.volume-slider').value=Math.round(state.volume*100);
        }
        if (document.activeElement !== $('.operator-cue-mode')) $('.operator-cue-mode').value=settings.cue_mode || 'voice';
        if ($('.operator-training-scene') && document.activeElement !== $('.operator-training-scene')) $('.operator-training-scene').value=settings.training_scene || 'lake-trees';
      }
      presentation = feedbackPolicy.update(context, performance.now());
      const meditation=context.snapshot?.feedback?.meditation;
      if (context.valid && presentation.status==='live' && typeof meditation==='number' && Number.isFinite(meditation)) displayedMeditation=meditation;
      else if (!presentation.held) displayedMeditation=null;
      if (context.mode === 'preview') {
        if (!wasPreview) { state.score = previewScore; state.valid = previewValid; state.playing = true; audio.setScore(state.score, state.valid); }
      } else {
        state.score = context.score;
        state.valid = context.valid;
        state.playing = context.phase === 'training';
        audio.setScore(presentation.audioScore, presentation.hasFeedback, {allowReward:presentation.status==='live'});
        if (presentation.announceReminder) announce('信号尚未稳定，可以检查额头与两侧耳后触点。声场继续播放。');
        const nextProgram = context.snapshot?.program_id;
        if (!['completed','idle'].includes(context.phase) && programs.some(p => p.id === nextProgram) && nextProgram !== state.programId && nextProgram !== failedProgram && !state.programBusy) switchProgram(nextProgram);
      }
      const audioPhase = context.mode === 'preview' ? 'preview' : context.phase;
      const shouldPlay = context.mode === 'preview' ? state.playing : context.phase === 'training';
      if (audioPhase !== lastAudioPhase || (audio.getState().ready && audio.getState().playing !== shouldPlay)) { lastAudioPhase = audioPhase; applyAudioMode(); }
      uiDirty = true;
    }
  });
  $('.audio-button').addEventListener('click', async () => {
    if (state.busy || state.programBusy) return;
    state.error = null; state.busy = true; uiDirty = true;
    try {
      if (isOperator) {
        const enabled=!state.sound;
        await session.setPresentation({sound_enabled:enabled});
        state.sound=enabled;
        announce(enabled ? '被试训练音乐已开启' : '被试训练音乐已静音，前测提示仍会播放');
        return;
      }
      if (sessionContext.snapshot?.presentation?.dual_screen) {announce('双屏会话请在工作人员控制台操作，声音由被试窗口播放');return;}
      if (!audio.getState().ready) {
        await audio.start();
        audio.setScore(sessionContext.mode === 'preview' ? state.score : presentation.audioScore, sessionContext.mode === 'preview' ? state.valid : presentation.hasFeedback, {allowReward:sessionContext.mode==='preview' || presentation.status==='live'});
        audio.setVolume(state.volume);
      }
      state.sound = !state.sound;
      applyAudioMode();
      announce(state.sound ? '完整音频已开启，持续播放' : '声音已关闭，播放位置保持');
    } catch (error) {
      state.sound = false;
      state.error = '音频加载失败，请重试';
      announce(state.error);
      console.error('Resonance audio:', error);
    } finally { state.busy = false; uiDirty = true; }
  });
  if (isOperator) {
    $('.operator-open-display').addEventListener('click', () => {
      const display=window.open('/participant.html','chengsi-participant','popup=yes,width=1280,height=900');
      if (!display) {state.error='请允许浏览器打开被试窗口，或直接打开 /participant.html';uiDirty=true;}
    });
    $('.operator-cue-mode').addEventListener('change', event => {
      session.setPresentation({cue_mode:event.target.value}).catch(error=>{state.error=error.message;uiDirty=true;});
    });
    $('.operator-training-scene')?.addEventListener('change', event => {
      session.setPresentation({training_scene:event.target.value}).catch(error=>{state.error=error.message;uiDirty=true;});
    });
  }
  $('.pause-button').addEventListener('click', async () => {
    if (sessionContext.mode !== 'preview') {
      if (sessionContext.phase === 'training') await session.command('pause');
      else if (sessionContext.phase === 'paused') await session.command('resume');
      return;
    }
    state.playing = !state.playing; uiDirty = true;
    try {
      if (audio.getState().ready) {
        if (state.playing) await audio.resume(); else await audio.pause();
      }
    } catch (error) { state.error = '播放状态切换失败'; }
    announce(state.playing ? '从当前位置继续' : '画面和声音已暂停');
  });
  $('.signal-button').addEventListener('click', () => {
    if (sessionContext.mode !== 'preview') {
      if (sessionContext.mode === 'synthetic' && sessionContext.phase === 'training') session.command('inject_dropout');
      return;
    }
    state.valid = !state.valid;
    previewValid = state.valid;
    audio.setScore(state.score, state.valid);
    uiDirty = true;
    announce(state.valid ? '模拟信号已恢复，声场平滑恢复' : '模拟信号中断，缓慢保留基础氛围');
  });
  $('.fullscreen-button').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await root.requestFullscreen();
    } catch (_) { announce('可使用浏览器全屏查看'); }
  });
  document.addEventListener('fullscreenchange', () => {
    $('.fullscreen-button').textContent = document.fullscreenElement ? '退出全屏' : '全屏';
    thumbsDirty = true; uiDirty = true;
  });
  function mappedLevels(score) {
    // Static illustrations use the same score-dependent strength as the player.
    // Clicking a card still follows the real continuous, smoothed audio policy.
    if (ResonanceAudio.isLayeredProgram(state.programId)) return window.EasyGoingProfile.levelsFor(score);
    return ResonanceAudio.levelsFor(score);
  }
  function visualState(levels) {
    return typeof getResonanceVisualState === 'function'
      ? getResonanceVisualState(levels, { linearPresence: ResonanceAudio.isLayeredProgram(state.programId) })
      : { drumCount: Math.ceil(levels[1]*24), bassRings: Math.ceil(levels[2]*18), ambientExpansion: levels[0] };
  }
  function updateProgramPresentation() {
    const layered = ResonanceAudio.isLayeredProgram(state.programId);
    if (displayedProfile === layered) return;
    displayedProfile = layered;
    const titles = layered ? ['基础声部','扩展声部','完整声部'] : ['氛围 · OTHER','节奏 · DRUMS','低音 · BASS'];
    $$('.res-callout strong').forEach((label,i) => { label.textContent=titles[i]; });
    $$('.track-row>span').forEach((label,i) => { label.textContent=titles[i]; });
    main.setAttribute('aria-label',layered ? '三层音乐声场：青色基础声部、金色扩展声部与蓝色完整声部随实际声音淡入淡出' : '三层共振声场：青色薄纱随氛围声流动，金色光点随鼓点闪耀，蓝色核心随低音呼吸');
    const cards = [
      {score:40,title:'基础声部',copy:'第一声部完整展开，<br>作为持续的音乐底层。',label:'基础声部的青色声场'},
      {score:55,title:'两层合奏',copy:'前两声部完整展开，<br>听见更丰富的配器。',label:'基础与扩展声部的双色声场'},
      {score:70,title:'三层合奏',copy:'三层声部完整展开，<br>70 分以上保持强度。',label:'基础扩展与完整声部的三层声场'}
    ];
    presets.forEach((preset,i) => {
      const card=cards[i];
      preset.score=layered ? card.score : preset.originalScore;
      preset.button.dataset.score=String(preset.score);
      preset.button.querySelector('.preset-copy').innerHTML=layered ? '<strong><em>'+card.score+'</em> · '+card.title+'</strong><span>'+card.copy+'</span>' : preset.originalCopy;
      preset.canvas.setAttribute('aria-label',layered ? card.label : preset.originalLabel);
      preset.button.querySelector('.preset-action').textContent=layered ? '试听此组合' : '预览此状态';
    });
    thumbsDirty=true;
  }
  function updateUI(audioState) {
    updateProgramPresentation();
    const layered = ResonanceAudio.isLayeredProgram(state.programId);
    const preview = sessionContext.mode === 'preview';
    const phase = sessionContext.phase;
    const calibrationPaused = phase==='paused' && String(sessionContext.snapshot?.resume_phase || '').startsWith('calibrating_');
    const sourceLabels = { preview:'手动预览', synthetic:'模拟数据', live:'实机数据', replay:'记录回放' };
    const shownScore = preview ? state.valid ? state.score : null : presentation.displayScore;
    $('.score').textContent = shownScore !== null ? Math.round(shownScore) : '—';
    $('.score-section').classList.toggle('score-held', !preview && presentation.held);
    $('.meditation-section').hidden=preview;
    $('.meditation-score').textContent=!preview && displayedMeditation!==null ? String(Math.round(displayedMeditation)) : '—';
    $('.meditation-section').classList.toggle('is-held',!preview && presentation.held);
    $('.score-caption').textContent = preview ? '手动试听' : phase==='calibrating_open' ? '前测视觉引导 · 等待校准' : phase !== 'training' ? '等待有效训练数据' : presentation.held ? '暂保持 · 非新测量' : presentation.status === 'live' ? '相对个人基线的反馈' : presentation.status === 'unavailable' ? '数据未更新 · 暂无测量' : presentation.recovering ? '确认信号恢复中' : '等待有效测量';
    $('.slider-value').textContent = Math.round(previewScore);
    $('.score-slider').value = previewScore;
    $('.preset-grid').hidden = !preview;
    $('.score-control').hidden = !preview;
    $('.score-slider').disabled = !preview;
    presets.forEach(preset => { preset.button.disabled = !preview; });
    $('.res-badge').textContent = phase==='calibrating_open' ? '睁眼前测 · 视觉引导' : sourceLabels[sessionContext.mode]+' · '+(preview ? '不限时试听' : '个人校准反馈');
    $('.score-section .panel-label>span').textContent = sourceLabels[sessionContext.mode];
    const training = sessionContext.snapshot?.training;
    const elapsed = Math.max(0, Math.floor((phase === 'completed' ? sessionContext.snapshot?.summary?.valid_seconds : training?.valid_seconds) || 0));
    const target = training?.target_seconds;
    const displayedSeconds = phase === 'completed' || target == null ? elapsed : Math.max(0,Math.ceil(target-elapsed));
    $('.time-section .panel-label').textContent = preview ? '试听模式' : phase === 'completed' ? '有效训练时长' : !['training','paused'].includes(phase) ? '训练状态' : target == null ? '有效训练已进行' : '有效训练剩余';
    $('.remaining').textContent = preview ? '不限时' : ['training','paused','completed'].includes(phase) ? String(Math.floor(displayedSeconds/60)).padStart(2,'0')+':'+String(displayedSeconds%60).padStart(2,'0') : '待开始';
    $('.continuous-symbol').textContent = preview || target == null ? '∞' : '◷';
    $('.volume-value').textContent = Math.round(state.volume*100)+'%';
    if (isOperator) $('.volume-slider').style.setProperty('--volume-fill', Math.max(0,Math.min(100,state.volume*100/Number($('.volume-slider').max)*100))+'%');
    const waitingForQuality = !preview && sessionContext.mode !== 'replay' && phase === 'paused' && sessionContext.snapshot?.quality?.valid !== true;
    $('.pause-text').textContent = waitingForQuality ? '等待稳定信号' : !preview && phase === 'completed' ? '本次已完成' : !preview && !['training','paused'].includes(phase) ? '等待训练' : state.playing ? '暂停' : '继续';
    $('.pause-icon').textContent = state.playing ? 'Ⅱ' : '▷';
    $('.pause-button').setAttribute('aria-pressed', String(!state.playing));
    $('.pause-button').hidden = isOperator && calibrationPaused;
    $('.pause-button').disabled = !preview && (!['training','paused'].includes(phase) || sessionContext.busy || waitingForQuality);
    $('.signal-button').hidden = !preview && (sessionContext.mode !== 'synthetic' || phase !== 'training');
    $('.signal-button').disabled = !preview && (phase !== 'training' || sessionContext.busy);
    $('.signal-button').textContent = preview ? (state.valid ? '模拟断连' : '恢复信号') : '测试信号中断';
    $('.signal-button').setAttribute('aria-pressed', String(preview && !state.valid));
    $('.signal-status>span').textContent = preview ? (state.valid ? '模拟信号良好' : '信号暂不可用') : phase==='paused' ? '训练已暂停' : phase==='completed' ? '本次训练结束' : phase!=='training' ? '等待训练数据' : presentation.status === 'live' ? (sessionContext.mode==='live' ? '信号良好' : sessionContext.mode==='replay' ? '回放数据有效' : '模拟数据有效') : presentation.status === 'unavailable' ? '数据暂未更新' : presentation.held ? '声场暂保持' : presentation.recovering ? '恢复确认中' : '基础氛围中';
    $('.signal-status').classList.toggle('signal-waiting', !preview && presentation.status !== 'live');
    // Ordinary artifacts never cover the training scene. Device disconnects
    // retain the separate backend-authoritative reconnect / manual-resume flow.
    $('.signal-overlay').hidden = !preview || state.valid;
    $('.signal-overlay>span').textContent = '声音平滑回到基础氛围';
    const showAdvice = !preview && phase === 'training' && (presentation.reminder || presentation.status === 'unavailable');
    $('.signal-advice').hidden = !showAdvice;
    $('.signal-advice').textContent = presentation.status === 'unavailable' ? '数据连接待恢复，当前未更新反馈。' : '信号尚未稳定，可检查额头与两侧耳后触点。';
    $('.audio-button').textContent = isOperator ? (state.sound ? '关闭被试音乐' : '开启被试音乐') : state.busy ? '加载完整音频…' : state.sound ? '关闭声音' : '开启声音';
    $('.audio-button').disabled = state.busy || state.programBusy || (isOperator && sessionContext.busy);
    $('.volume-slider').disabled = isOperator && sessionContext.busy;
    $('.audio-button').setAttribute('aria-pressed', String(state.sound));
    programSelect.disabled = state.busy || state.programBusy || sessionContext.busy;
    programSelect.value = state.pendingProgram || state.programId;
    const programTitle = programs.find(program => program.id === state.programId).title;
    const pendingTitle = programs.find(program => program.id === state.pendingProgram)?.title;
    $('.audio-note').textContent = state.error || (state.programBusy ? '正在切换至 ' + pendingTitle + '…' : state.busy ? '正在准备 ' + programTitle + ' · 完整三轨音频' : programTitle + ' · ' + (!preview && phase !== 'training' ? '训练外保持安静' : state.sound ? (state.playing ? '完整音频 · 连续播放' : '已暂停 · 保留当前位置') : '已静音 · 点击开启声音'));
    if (isOperator && !state.error) $('.audio-note').textContent=programTitle+' · 声音仅从被试窗口播放 · 音量仅控制训练音乐';
    $('.mode-note').textContent = layered ? (preview ? '不限时试听' : sourceLabels[sessionContext.mode])+' · 40 / 55 / 70 分依次叠满三层 · 音画平滑过渡' : preview ? '不限时试听 · 演示设定：80 分完整声场' : sourceLabels[sessionContext.mode]+' · 仅使用有效训练数据 · 80 分完整反馈';
    const visual = visualState(state.levels);
    $('.res-callout.other small').textContent = layered ? '基础声部持续陪伴，保持音乐流动' : '薄纱随氛围声逐渐展开';
    $('.res-callout.drums small').textContent = layered ? visual.drumCount+' 枚光点 · 随扩展声部渐入' : visual.drumCount+' 枚光点 · 随节拍轻闪';
    $('.res-callout.bass small').textContent = layered ? visual.bassRings+' 层波纹 · 随完整声部展开' : visual.bassRings+' 层波纹 · 随低频呼吸';
    $$('.track-row').forEach((row, i) => {
      const actual = isOperator ? session.workspace?.levels() : state.levels;
      const level = actual?.[i] ?? 0;
      const count = Math.round(level*6);
      row.querySelector('.level-number').textContent = isOperator ? actual ? Math.round(level*100)+'%' : '—' : count+'/6';
      row.querySelector('.level-meter').style.setProperty('--level',Math.round(level*100)+'%');
      row.querySelectorAll('i').forEach((dot, n) => dot.classList.toggle('on', n<count));
    });
    presets.forEach(preset => preset.button.setAttribute('aria-pressed', String(preset.score === state.score)));
    uiDirty = false;
  }
  function prepare(canvas, ctx) {
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return null;
    const dpr = Math.min(devicePixelRatio || 1, 1.5);
    const width = Math.round(rect.width*dpr), height = Math.round(rect.height*dpr);
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return rect;
  }
  let thumbsDirty = true;
  window.addEventListener('resonance-material-ready', () => { thumbsDirty = true; });
  new ResizeObserver(() => { thumbsDirty = true; }).observe(root);
  // Static state previews avoid running four dense scenes while audio is active.
  function drawThumbnails() {
    presets.forEach(preset => {
      const rect = prepare(preset.canvas, preset.ctx);
      if (rect) drawResonance(preset.ctx, rect.width, rect.height, 3, mappedLevels(preset.score), [.35,.42,.32], { ...options, valid: true, compact: true });
    });
    thumbsDirty = false;
  }
  let last = performance.now(), lastDraw = 0, lastUI = 0;
  const metrics = { drawFrames: 0, totalDrawMs: 0, maxDrawMs: 0, frameGaps: 0 };
  function frame(now) {
    const rawDt = (now-last)/1000, dt = Math.min(.1, rawDt); last = now;
    const calibrationGuide=sessionContext.mode!=='preview' && sessionContext.phase==='calibrating_open';
    if ((state.playing || calibrationGuide) && !document.hidden) state.visualTime += dt*(calibrationGuide?.45:1);
    const audioState = audio.getState();
    const targetScore = sessionContext.mode === 'preview' ? state.score : presentation.audioScore;
    const targetActive = sessionContext.mode === 'preview' ? state.valid : presentation.hasFeedback;
    const layered = ResonanceAudio.isLayeredProgram(state.programId);
    const desired = layered ? (audioState.levels || [.55,0,0]) : audioState.ready && audioState.levels ? audioState.levels : ResonanceAudio.levelsFor(targetScore, targetActive);
    if (layered) {
      // The audio envelope is already smooth and sample-clock scheduled. A
      // second visual filter would make a new instrument and its ring disagree.
      state.levels = desired.slice();
    } else if (state.playing || sessionContext.mode !== 'preview') for (let i=0; i<3; i++) state.levels[i] += (desired[i]-state.levels[i])*(1-Math.exp(-dt*(audioState.ready?4:1.1)));
    const energy = audioState.ready && audioState.energy ? audioState.energy : [.18,.12,.16];
    options.valid = calibrationGuide || (sessionContext.mode === 'preview' ? state.valid : sessionContext.phase === 'training');
    options.linearPresence = layered;
    if (!isOperator && !document.hidden && now-lastDraw >= 32) {
      const before = performance.now(), rect = prepare(main, mainCtx);
      // Fixed full visual participation is an eyes-open fixation stimulus only.
      // It is never sent to the sound engine, backend, saved score or quality gate.
      if (rect) drawResonance(mainCtx, rect.width, rect.height, state.visualTime, calibrationGuide?[1,1,1]:state.levels, calibrationGuide?[.08,.06,.08]:energy, options);
      const spent = performance.now()-before;
      metrics.drawFrames++; metrics.totalDrawMs += spent; metrics.maxDrawMs = Math.max(metrics.maxDrawMs, spent);
      if (rawDt>.1) metrics.frameGaps++;
      lastDraw=now;
    }
    if (!isOperator && thumbsDirty && !document.hidden) drawThumbnails();
    if (uiDirty || now-lastUI > 150) { updateUI(audioState); lastUI=now; }
    requestAnimationFrame(frame);
  }
  // A read-only diagnostic view supports repeatable desktop and sound checks.
  window.resonanceDiagnostics = () => ({
    score: state.score, playing: state.playing, sound: state.sound, valid: state.valid,
    programId: state.programId, programBusy: state.programBusy,
    mode:sessionContext.mode, phase:sessionContext.phase, sessionId:sessionContext.snapshot?.session_id || null,
    sessionStale:Boolean(sessionContext.stale), backendValid:sessionContext.valid,
    presentation: { ...presentation, policy: feedbackPolicy.config },
    visualGuide: sessionContext.mode!=='preview' && sessionContext.phase==='calibrating_open',
    visualLevels: sessionContext.mode!=='preview' && sessionContext.phase==='calibrating_open' ? [1,1,1] : state.levels.slice(), visual: visualState(sessionContext.mode!=='preview' && sessionContext.phase==='calibrating_open' ? [1,1,1] : state.levels),
    audio: audio.getState(),
    rendering: { ...metrics, meanDrawMs: metrics.drawFrames ? metrics.totalDrawMs/metrics.drawFrames : 0 }
  });
  window.addEventListener('pagehide', () => audio.dispose());
  updateUI(audio.getState());
  requestAnimationFrame(frame);
})();
