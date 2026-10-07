/* Backend-authoritative session flow. Preview never creates or writes a session. */
(() => {
  'use strict';
  const modes = { preview: '声场预览', synthetic: '模拟全流程', live: 'Muse 2 实机', replay: '记录回放' };
  const phases = { idle:'准备连接', connecting:'正在连接', connected:'设备已连接', preparing_closed:'闭眼前测引导中', preparing_open:'睁眼前测引导中', preparing_training:'训练开始引导中', resting:'轮间休息', calibrating_closed:'闭眼校准', closed_complete:'闭眼校准完成', calibrating_open:'睁眼校准', ready:'校准完成', training:'训练中', paused:'训练已暂停', completed:'训练完成', disconnected:'连接已断开', error:'需要处理' };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time = value => { const n = Math.max(0, Math.floor(Number(value)||0)); return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0'); };
  const number = value => Number.isFinite(Number(value)) && value !== null ? Number(value).toFixed(1) : '—';
  const duration = value => finite(value) ? Number(value).toFixed(1)+' 秒' : '—';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const dateLabel = value => { const d = new Date(value); return Number.isFinite(d.getTime()) ? d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '日期未记录'; };
  // Preserve missing observations and recording segments: held UI values never
  // enter charts, and disconnected periods must never look like measurements.
  function chartSegments(points, field) {
    const segments = []; let current = [], previous = null;
    for (const p of points) {
      const usable = p && p.valid !== false && !p.gap && finite(p.time) && finite(p[field]);
      const breaks = usable && previous && (p.segment !== previous.segment || p.stage !== previous.stage || p.time <= previous.time || p.time-previous.time > 3);
      if (!usable || breaks) { if (current.length) segments.push(current); current = []; }
      if (usable) current.push(p);
      previous = usable ? p : null;
    }
    if (current.length) segments.push(current);
    return segments;
  }
  function scoreChart(points = [], { title='专注与放松', peak=null, stages=false } = {}) {
    const observations = points.filter(p=>p && finite(p.time));
    if (!observations.some(p=>p.valid!==false && (finite(p.score)||finite(p.meditation)))) return '<div class="session-chart-empty">暂无可展示的有效曲线</div>';
    const end = Math.max(1,...observations.map(p=>p.time));
    const x = value => 42+Math.max(0,value)/end*910;
    const y = value => 173-Math.max(0,Math.min(100,value))*1.4;
    let markup = '<svg viewBox="0 0 990 206" role="img" aria-label="'+escape(title)+'"><title>'+escape(title)+'；无效数据处断线</title>';
    for (const value of [0,50,100]) markup += '<path d="M42 '+y(value)+'H952" class="chart-grid"/><text x="30" y="'+(y(value)+4)+'" text-anchor="end">'+value+'</text>';
    if (stages) {
      for (const stage of ['closed','open']) {
        const subset = observations.filter(p=>String(p.stage||'').includes(stage));
        if (subset.length) { const start = subset[0].time, finish = subset[subset.length-1].time; markup += '<rect x="'+x(start)+'" y="26" width="'+Math.max(1,x(finish)-x(start))+'" height="149" class="stage-band '+stage+'"/><text x="'+(x(start)+8)+'" y="20" class="stage-label">'+(stage==='closed'?'闭眼对照':'睁眼基线')+'</text>'; }
      }
    }
    if (finite(peak?.threshold)) markup += '<path d="M42 '+y(peak.threshold)+'H952" class="chart-threshold"/><text x="950" y="'+Math.max(24,y(peak.threshold)-6)+'" text-anchor="end" class="peak-label">峰值区间 ≥ '+number(peak.threshold)+'</text>';
    for (const field of ['score','meditation']) for (const segment of chartSegments(points,field)) {
      const d = segment.map((p,i)=>(i?'L':'M')+x(p.time).toFixed(2)+' '+y(p[field]).toFixed(2)).join(' ');
      markup += '<path data-series="'+field+'" d="'+d+'" class="chart-line '+(field==='score'?'attention-curve':'relaxation-curve')+'"/>';
      if (segment.length===1) markup += '<circle cx="'+x(segment[0].time)+'" cy="'+y(segment[0][field])+'" r="2" class="chart-dot '+(field==='score'?'attention-curve':'relaxation-curve')+'"/>';
    }
    if (finite(peak?.peak_value) && finite(peak?.peak_time)) markup += '<circle cx="'+x(peak.peak_time)+'" cy="'+y(peak.peak_value)+'" r="5" class="peak-dot"/><title>最高专注分 '+number(peak.peak_value)+'，出现在 '+time(peak.peak_time)+'</title>';
    for (const fraction of [0,.25,.5,.75,1]) markup += '<text x="'+x(end*fraction)+'" y="197" text-anchor="middle">'+time(end*fraction)+'</text>';
    return markup+'</svg>';
  }
  const baselineKey = entry => (entry.baseline_id || entry.summary?.baseline_id || ('legacy:'+entry.session_id))+'|'+(entry.calibration_method || entry.summary?.calibration_method || 'legacy');
  function historyGroups(entries) {
    const groups = new Map();
    for (const entry of entries) { const key=baselineKey(entry); if (!groups.has(key)) groups.set(key,[]); groups.get(key).push(entry); }
    return [...groups.entries()].map(([key,items])=>({key,items:items.sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)))}));
  }
  function historyChart(entries, field, label, seconds=false) {
    const values=entries.map(entry=>field.startsWith('peak.') ? entry.summary?.peak?.[field.slice(5)] : entry.summary?.[field]);
    const ceiling=seconds ? Math.max(10,...values.filter(finite))*1.15 : 100;
    const x=i=>32+i/Math.max(1,entries.length-1)*890, y=v=>109-v/ceiling*88;
    let path='',lastValid=false;
    values.forEach((v,i)=>{if (!finite(v)) {lastValid=false;return;} path+=(lastValid?'L':'M')+x(i)+' '+y(v)+' ';lastValid=true;});
    return '<div class="history-mini-chart"><span>'+label+(seconds?' · 秒':' · 分')+'</span><svg viewBox="0 0 950 139" role="img" aria-label="'+label+'历次变化"><path d="M32 109H922" class="chart-grid"/><path d="'+path+'" class="chart-line '+(field==='mean_meditation'?'relaxation-curve':'attention-curve')+'"/>'+values.map((v,i)=>finite(v)?'<circle cx="'+x(i)+'" cy="'+y(v)+'" r="3.5" class="chart-dot '+(field==='mean_meditation'?'relaxation-curve':'attention-curve')+'"/><text x="'+x(i)+'" y="'+(y(v)-9)+'" text-anchor="middle">'+number(v)+'</text><text x="'+x(i)+'" y="132" text-anchor="middle">第 '+(i+1)+' 次</text>':'').join('')+'</svg></div>';
  }
  window.ResonanceSessionCharts = {chartSegments,scoreChart,historyGroups};
  const qualityLabels = {
    not_connected:'请先连接头环', connecting:'正在连接数据源', connect_failed:'连接未成功，请检查设备',
    filter_warmup:'正在等待信号稳定', flatline:'接触信号较弱，请检查头环佩戴', saturation:'信号超出范围，请调整接触',
    amplitude_artifact:'检测到幅度干扰，请检查对应触点并等待稳定', large_step:'检测到信号跳变，请检查对应触点并等待稳定',
    non_finite_sample:'采样暂不可用，等待恢复', packet_gap:'蓝牙数据有间断，等待稳定', timestamp_gap:'数据有间断，等待稳定',
    non_monotonic_timestamp:'数据时间异常，等待重新同步', insufficient_band_power:'有效脑电信号不足，请检查接触',
    insufficient_residual_signal:'有效信号不足，请检查对应触点',
    feedback_stale:'反馈数据暂未更新', data_timeout:'未收到新数据，请检查连接', synthetic_dropout:'模拟信号已中断',
    user_disconnected:'连接已断开', user_paused:'训练已暂停', training_started:'正在准备训练数据',
    closed_calibration_started:'正在采集闭眼基线', open_calibration_started:'正在采集睁眼基线',
  };
  const qualityText = reasons => Array.isArray(reasons) ? [...new Set(reasons.map(reason => qualityLabels[reason] || (/[\u3400-\u9fff]/.test(reason) ? reason : '正在等待稳定的有效信号')))].join(' · ') : '';
  const contactPoints = [['TP9','左耳后'],['AF7','左前额'],['AF8','右前额'],['TP10','右耳后']];
  const contactReasons = { flatline:'信号较弱', saturation:'信号超出范围', amplitude_artifact:'幅度干扰', large_step:'信号跳变', non_finite_sample:'采样暂不可用', insufficient_residual_signal:'有效信号不足' };
  const hasPowerlineWarning = channel => Array.isArray(channel?.warnings) && channel.warnings.includes('powerline_50hz');
  const contactSummary = quality => {
    const affected = contactPoints.filter(([channel]) => quality?.channel_quality?.[channel]?.valid === false);
    const powerline = contactPoints.filter(([channel]) => quality?.channel_quality?.[channel]?.valid === true && hasPowerlineWarning(quality.channel_quality[channel]));
    const messages = [];
    if (affected.length) messages.push(affected.map(([channel,position]) => position+'（'+channel+'）').join('、')+'需调整：请检查这些触点与皮肤的接触，并等待信号稳定。');
    if (powerline.length) messages.push(powerline.map(([channel,position]) => position+'（'+channel+'）').join('、')+'工频较强，已作滤波处理，仍建议检查接触。'+(quality?.valid ? '当前信号可采集。' : affected.length ? '' : qualityText(quality?.reasons)));
    if (messages.length) return messages.join(' ');
    return quality?.valid ? '信号可用 · 正在采集有效数据' : qualityText(quality?.reasons) || '正在等待稳定信号';
  };
  const contactPanel = () => '<section class="session-contacts" aria-label="四个脑电通道的接触状态"><div class="contact-heading">实时触点状态<span>按最近一段信号判断</span></div><div class="contact-grid">'+contactPoints.map(([channel,position])=>'<div class="contact-card" data-channel="'+channel+'" data-contact-state="waiting"><div class="contact-label"><strong>'+position+'</strong><span>'+channel+'</span></div><div class="contact-state">等待数据</div><div class="contact-reason">等待新信号</div></div>').join('')+'</div></section>';
  const operatorContactPanel = () => '<section class="operator-signal" aria-label="四个脑电通道的实时状态"><div class="operator-signal-list" role="list">'+contactPoints.map(([channel,position])=>'<div class="operator-signal-row" role="listitem" data-channel="'+channel+'" data-contact-state="waiting"><span class="operator-signal-dot" aria-hidden="true"></span><div class="operator-signal-label"><strong>'+position+'</strong><span>'+channel+'</span></div><span class="operator-signal-state">等待数据</span></div>').join('')+'</div><details class="operator-signal-details" hidden><summary>查看信号说明</summary><p class="operator-signal-summary"></p></details></section>';
  function dailyRoundTable(protocol) {
    if(protocol?.plan!=='daily')return '';
    const rounds=protocol.completed_rounds || [];
    const rows=rounds.map(round=>{const m=round.summary || {};return '<tr><td>第 '+Number(round.round_number)+' 轮</td><td>'+number(m.mean_score)+'</td><td>'+number(m.mean_meditation)+'</td><td>'+time(m.valid_seconds)+'</td><td>'+time(m.wall_elapsed_seconds)+'</td></tr>';}).join('');
    return '<section class="daily-round-summary"><div class="daily-round-heading"><strong>今日 '+Number(protocol.completed_round_count || 0)+' / '+Number(protocol.rounds_target || 4)+' 轮</strong><span>累计有效 '+time(protocol.daily_valid_seconds)+' · 今日总耗时 '+time(protocol.daily_wall_seconds)+' · 平均专注 '+number(protocol.daily_mean_score)+' · 平均放松 '+number(protocol.daily_mean_meditation)+'</span></div>'+(rows?'<table><thead><tr><th>轮次</th><th>平均专注</th><th>平均放松</th><th>有效时间</th><th>本轮耗时</th></tr></thead><tbody>'+rows+'</tbody></table>':'')+'</section>';
  }
  class ResonanceSession {
    constructor({ root, onChange, unlockAudio, getProgram, getPresentation }) {
      this.root = root; this.onChange = onChange; this.unlockAudio = unlockAudio; this.getProgram = getProgram;
      this.isOperator = document.documentElement.dataset.view === 'operator';
      this.getPresentation = getPresentation; this.displayPresentation = null;
      this.mode = this.isOperator ? 'live' : 'preview'; this.snapshot = null; this.sessionId = null; this.receivedAt = 0;
      this.busy = false; this.error = ''; this.notice = ''; this.otherSession = null;
      this.online = false; this.ws = null; this.closed = false; this.stateCheck = null;
      this.sessions = []; this.devices = []; this.form = { participant:'DEMO-001', training:'60', protocol:this.isOperator?'daily':'single', address:'', replay:'' };
      this.planPreview=null;this.planRequest=0;this.resultRound=0;
      try { this.form.participant=localStorage.getItem('chengsi-participant-id') || this.form.participant; } catch (_) {}
      this.resultTab='training'; this.history=[];this.dailyHistory=[]; this.historyGroup=''; this.historyStatus='idle'; this.historyRequest=null;
      this.edfBusy=false;this.exportError='';
      this.q = selector => root.querySelector(selector);
      if (this.isOperator) this.q('.operator-contacts').innerHTML = operatorContactPanel();
      this.q('.session-mode').addEventListener('change', event => this.setMode(event.target.value));
      this.q('.session-drawer').addEventListener('click', event => this.handleAction(event));
      this.q('.session-drawer').addEventListener('input', event => { if (event.target.dataset.field) this.form[event.target.dataset.field] = event.target.value; });
      this.q('.session-drawer').addEventListener('change', event => {
        if (event.target.dataset.field) { this.form[event.target.dataset.field] = event.target.value; if(event.target.dataset.field==='participant') this.saveParticipant(); if(['participant','training','protocol'].includes(event.target.dataset.field)) this.loadPlan(); }
        if(event.target.classList.contains('result-round-select')) {this.resultRound=Number(event.target.value);this.renderKey='';this.emit();}
        if (event.target.classList.contains('history-group-select')) { this.historyGroup=event.target.value; this.renderKey=''; this.emit(); }
      });
      this.q('.session-finish').addEventListener('click', () => this.command('finish'));
      this.q('.session-recalibrate').addEventListener('click', () => this.command('calibrate_closed'));
      this.workspace = this.isOperator && window.ResonanceOperatorWorkspace ? new window.ResonanceOperatorWorkspace(this) : null;
      this.timer = setInterval(() => this.emit(), 250);
      this.initialize();
      window.addEventListener('pagehide', () => this.dispose());
    }
    async request(path, body) {
      const response = await fetch(path, body === undefined ? { cache:'no-store' } : { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
      const data = await response.json();
      if (!response.ok) {
        const previousId = this.sessionId;
        if (data.snapshot) this.reconcile(data.snapshot, true);
        const error = new Error(data.error?.message || data.error || data.detail || '本地服务暂不可用');
        error.sessionChanged = Boolean(previousId && previousId !== this.sessionId);
        throw error;
      }
      return data;
    }
    async loadPlan() {
      if(this.mode==='replay'||this.mode==='preview'){this.planPreview=null;return;}
      const token=++this.planRequest, participant=this.form.participant.trim();
      if(!participant)return;
      try {
        const data=await this.request('/api/training-plan?participant_id='+encodeURIComponent(participant)+'&mode='+encodeURIComponent(this.mode)+'&training_seconds='+encodeURIComponent(this.form.training));
        if(token===this.planRequest){this.planPreview=data;this.renderKey='';this.emit();}
      } catch (_) {if(token===this.planRequest)this.planPreview=null;}
    }
    async initialize() {
      try {
        await this.request('/api/health'); this.online = true; this.loadPlan();
        const data = await this.request('/api/state'); const snapshot = data.snapshot || data;
        this.displayPresentation = snapshot.presentation || null;
        if (snapshot.session_id && snapshot.phase !== 'idle') {
          this.mode = snapshot.mode; this.sessionId = snapshot.session_id; this.accept(snapshot);
        }
        this.connectSocket();
      } catch (_) { this.online = false; this.error = ''; this.connectSocket(); }
      this.emit();
    }
    connectSocket() {
      if (this.closed) return;
      const url = new URL('/ws/live', location.href); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = this.ws = new WebSocket(url);
      ws.onopen = async () => {
        this.online = true;
        await this.checkState();
        this.emit();
      };
      ws.onmessage = event => { try {
        const data = JSON.parse(event.data); const snapshot = data.snapshot || data;
        this.displayPresentation = snapshot.presentation || null;
        if (this.sessionId && snapshot.session_id === this.sessionId) this.accept(snapshot);
        // Confirm an ID change by HTTP: an older queued socket frame must not
        // discard a just-created session whose POST already returned.
        else if (this.sessionId || (this.otherSession && (snapshot.session_id !== this.otherSession || ['idle','completed','error'].includes(snapshot.phase)))) this.checkState();
        else this.emit();
      } catch (_) {} };
      ws.onclose = () => { this.online = false; this.emit(); if (!this.closed) this.reconnectTimer = setTimeout(() => this.connectSocket(), 1500); };
      ws.onerror = () => { this.online = false; this.emit(); };
    }
    accept(snapshot) {
      if (!snapshot || !snapshot.session_id) return;
      if (this.snapshot?.session_id === snapshot.session_id && Number(snapshot.seq) < Number(this.snapshot.seq)) return;
      this.snapshot = snapshot; this.sessionId = snapshot.session_id; this.receivedAt = performance.now();
      this.otherSession = null;
      this.error = snapshot.error?.message || snapshot.error || '';
      if (snapshot.participant_id && snapshot.mode!=='replay') { this.form.participant=snapshot.participant_id; this.saveParticipant(); }
      this.displayPresentation = snapshot.presentation || null;
      if (!this.isOperator && !snapshot.presentation?.dual_screen) window.CalibrationCues?.observe(snapshot);
      this.emit();
      if (snapshot.phase==='completed' && this.historySession!==snapshot.session_id) { this.historySession=snapshot.session_id; this.loadHistory(); }
    }
    async checkState() {
      if (this.stateCheck || this.closed) return this.stateCheck;
      const expectedId = this.sessionId, expectedOther = this.otherSession;
      this.stateCheck = (async () => {
        try {
          const data = await this.request('/api/state');
          if (this.sessionId === expectedId && this.otherSession === expectedOther) this.reconcile(data.snapshot || data);
        }
        catch (_) { /* Socket freshness still invalidates feedback while offline. */ }
      })();
      try { await this.stateCheck; } finally { this.stateCheck = null; }
    }
    reconcile(snapshot, authoritativeConflict = false) {
      if (snapshot) this.displayPresentation = snapshot.presentation || null;
      if (!snapshot || (!this.sessionId && !this.otherSession && !(authoritativeConflict && snapshot.session_id))) return;
      if (this.sessionId && snapshot.session_id === this.sessionId) { this.accept(snapshot); return; }
      const anotherActive = snapshot.session_id && !['idle','completed','error'].includes(snapshot.phase);
      window.CalibrationCues?.cancel();
      this.sessionId = null; this.snapshot = null; this.receivedAt = 0; this.error = '';
      this.otherSession = anotherActive ? snapshot.session_id : null;
      this.notice = anotherActive
        ? '本地服务当前正在运行另一会话。请刷新页面同步该会话；当前页面已停止使用上次的分数。'
        : '本地服务已重新连接，上次会话已中断。已保存的记录仍可在记录回放中查看，请重新开始或选择回放。';
      this.renderKey = ''; this.emit();
    }
    context() {
      const s = this.snapshot; const phase = s?.phase || 'idle';
      const arrivalAge = performance.now() - this.receivedAt;
      const sentAge = s?.sent_at ? Date.now() - Date.parse(s.sent_at) : arrivalAge;
      const feedbackAge = Number.isFinite(s?.feedback?.age_ms) ? s.feedback.age_ms + arrivalAge : s?.feedback?.generated_at ? Date.now()-Date.parse(s.feedback.generated_at) : sentAge;
      const liveSampleAge = this.mode === 'live' ? (Number.isFinite(s?.feedback?.window_end) ? Date.now() - s.feedback.window_end * 1000 : Infinity) : 0;
      const stale = !s || !this.online || arrivalAge > 2500 || !Number.isFinite(sentAge) || sentAge > 2500 || !Number.isFinite(feedbackAge) || feedbackAge > 2500 || liveSampleAge > 2500;
      const score = s?.feedback?.score;
      const valid = this.mode !== 'preview' && phase === 'training' && !stale && s?.quality?.valid === true && s?.feedback?.valid === true && typeof score === 'number' && Number.isFinite(score);
      return { mode:this.mode, phase, snapshot:s, presentation:this.displayPresentation, busy:this.busy, stale, valid, score:valid ? Math.max(0,Math.min(100,score)) : null, online:this.online, qualityMessage:contactSummary(s?.quality) };
    }
    emit() { const context = this.context(); this.render(context); this.onChange(context); }
    saveParticipant() { try { localStorage.setItem('chengsi-participant-id',this.form.participant.trim() || 'DEMO-001'); } catch (_) {} }
    async loadHistory() {
      const s=this.snapshot; if (!s || this.historyRequest) return;
      this.historyStatus='loading'; this.renderKey='';
      const id=s.session_id;
      this.historyRequest=(async()=>{
        try {
          const data=await this.request('/api/participant-history?participant_id='+encodeURIComponent(s.participant_id || this.form.participant)+'&mode='+encodeURIComponent(s.mode));
          if(this.sessionId!==id) return;
          this.history=Array.isArray(data.entries)?data.entries:[]; this.dailyHistory=Array.isArray(data.daily_entries)?data.daily_entries:[];this.historyStatus='ready';
          const current=baselineKey({...s,...s.summary}); this.historyGroup=historyGroups(this.history).some(g=>g.key===current)?current:'';
        } catch (_) { if(this.sessionId===id) this.historyStatus='error'; }
        finally {this.historyRequest=null;this.renderKey='';this.emit();}
      })();
      return this.historyRequest;
    }
    async setMode(mode) {
      if (this.busy || !modes[mode]) { this.q('.session-mode').value = this.mode; return; }
      if (this.sessionId && !['completed','idle'].includes(this.snapshot?.phase)) {
        const previous = this.mode;
        try { await this.command('finish', true); } catch (_) { this.q('.session-mode').value = previous; return; }
      }
      window.CalibrationCues?.cancel();
      this.mode = mode; this.snapshot = null; this.sessionId = null; this.error = ''; this.renderKey = '';
      this.emit();
      if (mode === 'replay') await this.loadSessions(); else this.loadPlan();
    }
    async loadSessions() {
      try { const data = await this.request('/api/sessions'); this.sessions = data.sessions || []; if (!this.form.replay && this.sessions.length) this.form.replay = this.sessions[0].session_id; }
      catch (error) { this.error = error.message; }
      this.renderKey = ''; this.emit();
    }
    async scan() {
      this.busy = true; this.error = ''; this.emit();
      try { const data = await this.request('/api/devices'); this.devices = data.devices || []; if (data.error) this.error = String(data.error); else if (!this.devices.length) this.error = '未发现 Muse 2。请确认头环已开机、蓝牙可用，然后重新扫描。'; if (this.devices.length === 1 && !this.form.address) this.form.address = this.devices[0].address; }
      catch (error) { this.error = error.message; }
      finally { this.busy = false; this.renderKey = ''; this.emit(); }
    }
    async create() {
      if (this.busy || this.mode === 'preview' || this.otherSession) return;
      if (!this.isOperator) window.CalibrationCues?.unlock();
      this.busy = true; this.error = ''; this.notice = ''; this.emit();
      this.exportError='';
      try {
        this.saveParticipant();
        const payload = { mode:this.mode, participant_id:this.form.participant.trim() || 'DEMO-001', program_id:this.getProgram(), calibration_seconds:60, training_seconds:Number(this.form.training)||60, protocol_plan:this.mode==='replay'?'single':this.form.protocol, training_scene:this.getPresentation?.().training_scene || 'lake-trees', dual_screen:this.isOperator };
        if (this.mode === 'live') { if (!this.form.address.trim()) throw new Error('请扫描并选择要连接的 Muse 2，或填写已确认的设备地址。'); payload.device_address = this.form.address.trim(); }
        if (this.mode === 'replay') { if (!this.form.replay) throw new Error('请先选择一份可回放的训练记录'); payload.replay_session_id = this.form.replay; payload.training_seconds = null; }
        const preferences = this.getPresentation?.();
        const data = await this.request('/api/sessions', payload); this.accept(data.snapshot || data);
        if (this.isOperator && preferences) {
          const configured = await this.request('/api/sessions/'+encodeURIComponent(this.sessionId)+'/commands', {command:'set_presentation',command_id:crypto.randomUUID(),...preferences});
          this.accept(configured.snapshot || configured);
        }
      } catch (error) { this.error = error.message; }
      finally { this.busy = false; this.renderKey = ''; this.emit(); }
    }
    async command(command, rethrow = false, extra = {}) {
      if (this.busy || !this.sessionId) return;
      if (command === 'finish' || command === 'disconnect') window.CalibrationCues?.cancel();
      const cueUnlock=(!this.isOperator && !this.snapshot?.presentation?.dual_screen && (command==='calibrate_closed' || command==='calibrate_open' || command==='start_training')) ? window.CalibrationCues?.unlock() : null;
      const commandedId = this.sessionId;
      this.busy = true; this.error = ''; this.emit();
      try {
        if (cueUnlock) await cueUnlock;
        // This call begins synchronously inside the click gesture. Sound remains
        // muted until authoritative training phase and the user's sound choice.
        if (command === 'start_training' || command === 'resume') await this.unlockAudio();
        if(command==='start_training' && this.mode!=='replay' && !this.isOperator && !this.snapshot?.presentation?.dual_screen){
          const complete=await window.CalibrationCues?.playInstruction('training',this.getPresentation?.().cue_mode || 'voice');
          if(complete!==true)throw new Error('训练开始提示未完整播放，请重新开始。');
        }
        const data = await this.request('/api/sessions/'+encodeURIComponent(this.sessionId)+'/commands', { command, command_id:crypto.randomUUID(), ...extra });
        this.accept(data.snapshot || data);
      } catch (error) {
        // A restarted backend can no longer finish our previous ID. Its fresh
        // snapshot already cleared that binding, so navigation may continue.
        const bindingChanged = error.sessionChanged || this.sessionId !== commandedId;
        if (command === 'finish' && bindingChanged) return;
        this.error = bindingChanged ? this.notice : error.message;
        if (rethrow) throw error;
      }
      finally { this.busy = false; this.emit(); }
    }
    async setProgram(programId) { if (this.sessionId && !['completed','idle'].includes(this.snapshot?.phase)) await this.command('set_program', true, { program_id:programId }); }
    async setPresentation(settings) { if (this.sessionId && !['completed','idle'].includes(this.snapshot?.phase)) await this.command('set_presentation', true, settings); }
    async newSession() {
      if (this.busy) return;
      if (this.sessionId && !['completed','idle'].includes(this.snapshot?.phase)) {
        try { await this.command('finish', true); } catch (_) { return; }
      }
      window.CalibrationCues?.cancel();
      this.snapshot = null; this.sessionId = null; this.error = ''; this.exportError=''; this.resultRound=0;this.renderKey = ''; this.emit();this.loadPlan();
    }
    async exportEdf() {
      if(this.edfBusy || !this.sessionId) return;
      const id=this.sessionId; this.edfBusy=true;this.exportError='';this.renderKey='';this.emit();
      try {
        const response=await fetch('/api/sessions/'+encodeURIComponent(id)+'/export?edf=true',{cache:'no-store'});
        if(!response.ok) {
          let message='原始 EDF 导出失败，请稍后重试';
          try {const body=await response.json();message=body.error?.message || body.error || body.detail || message;}catch(_){}
          throw new Error(typeof message==='string'?message:JSON.stringify(message));
        }
        if(!(response.headers.get('content-type') || '').includes('application/zip')) throw new Error('EDF 导出未返回完整压缩包，请重试');
        const blob=await response.blob(), url=URL.createObjectURL(blob), link=document.createElement('a');
        link.href=url;link.download='chengsi-'+id+'-edf.zip';document.body.appendChild(link);link.click();link.remove();
        setTimeout(()=>URL.revokeObjectURL(url),60000);
      } catch(error) {if(this.sessionId===id) this.exportError='EDF 导出失败：'+error.message;}
      finally {this.edfBusy=false;this.renderKey='';this.emit();}
    }
    handleAction(event) {
      const button = event.target.closest('[data-action]'); if (!button || this.busy) return;
      const action = button.dataset.action;
      if (action==='cue-start' || action==='cue-complete') { window.CalibrationCues?.unlock(); window.CalibrationCues?.preview(action==='cue-start'?'start':'complete'); }
      else if (action.startsWith('tab-')) { this.resultTab=action.slice(4); this.renderKey=''; this.emit(); if(this.resultTab==='history' && this.historyStatus==='idle') this.loadHistory(); }
      else if (action==='history-retry') this.loadHistory();
      else if (action==='export-edf') this.exportEdf();
      else if (action === 'connect') this.create();
      else if (action === 'scan') this.scan();
      else if (action === 'records') this.loadSessions();
      else if (action === 'reload') location.reload();
      else if (action === 'new') this.newSession();
      else this.command(action);
    }
    render(context) {
      const { mode, phase, snapshot:s, stale } = context; const preview = mode === 'preview';
      this.root.classList.toggle('session-workflow', !preview);
      this.root.dataset.phase = phase;
      this.q('.session-mode').value = mode; this.q('.session-mode').disabled = this.busy;
      this.q('.session-phase').textContent = preview ? '不限时试听' : mode === 'replay' && phase === 'ready' ? '记录已载入' : mode === 'replay' && phase === 'training' ? '回放中' : phases[phase] || phase;
      this.q('.backend-status').textContent = this.online ? '本地服务已连接' : '本地服务未连接';
      this.q('.backend-status').classList.toggle('offline', !this.online);
      this.q('.session-caption').textContent = this.error || this.exportError || this.notice || (preview ? '自由试听音乐与反馈变化' : stale && phase === 'training' ? '数据暂不可用 · 反馈退回基础氛围' : mode === 'live' ? 'Muse 2 脑电 · 个人校准 · 实时反馈' : mode === 'replay' ? '历史记录回放 · 不代表当前脑电' : '合成脑电演示 · 不代表实测数据');
      const step = preview ? 2 : ['training','paused','preparing_training','resting'].includes(phase) ? 2 : phase === 'completed' ? 3 : ['preparing_closed','preparing_open','calibrating_closed','closed_complete','calibrating_open','ready'].includes(phase) ? 1 : 0;
      this.root.querySelectorAll('.res-steps li').forEach((li,i) => { li.classList.toggle('active', i===step); if(i===step) li.setAttribute('aria-current','step'); else li.removeAttribute('aria-current'); });
      const calibrationPaused = phase==='paused' && String(s?.resume_phase || '').startsWith('calibrating_');
      const inTraining = phase==='training' || (phase==='paused' && !calibrationPaused);
      this.q('.session-live-strip').hidden = preview || !inTraining;
      this.q('.session-live-title').textContent = mode === 'replay' ? (phase === 'paused' ? '回放已暂停' : '记录回放中') : phase === 'paused' ? '训练已暂停' : '训练进行中';
      this.q('.session-live-detail').textContent = modes[mode]+' · 有效训练 '+time(s?.training?.valid_seconds)+(mode==='replay'?'':' / '+time(s?.training?.target_seconds))+' · 总耗时 '+(finite(s?.training?.wall_elapsed_seconds)?time(s.training.wall_elapsed_seconds):'—')+(phase === 'paused' ? (mode === 'replay' ? ' · 可从当前位置继续回放' : s?.quality?.valid ? ' · 信号稳定，可手动继续' : ' · 基线已保留，等待稳定信号') : '');
      this.q('.session-live-detail').title=finite(s?.training?.wall_elapsed_seconds)?'':'该记录未保存总耗时';
      this.q('.session-finish').disabled = this.busy;
      this.q('.session-recalibrate').hidden = phase !== 'paused' || mode === 'replay';
      this.q('.session-recalibrate').disabled = this.busy;
      const drawer = this.q('.session-drawer'); drawer.hidden = preview || inTraining;
      const key = [mode,phase,s?.program_id || this.getProgram(),this.busy,this.edfBusy,Boolean(this.error || this.notice),this.otherSession,this.sessions.length,this.devices.length,this.form.protocol,this.form.training,this.planPreview?.training_day_number,this.planPreview?.day_complete,s?.protocol?.round_number,s?.protocol?.completed_round_count,Math.ceil(s?.protocol?.rest_remaining_seconds || 0),this.resultRound,s?.calibration?.baseline_id,s?.calibration?.reused,this.resultTab,this.historyStatus,this.historyGroup,this.history.length,s?.summary ? JSON.stringify(s.summary) : ''].join('|');
      if (key !== this.renderKey) {
        this.renderKey = key;
        const button = (action,label,primary=false) => '<button type="button" data-action="'+action+'" class="'+(primary?'session-primary':'res-small-button')+'" '+(this.busy?'disabled':'')+'>'+label+'</button>';
        const cueHelp = this.isOperator ? '<div class="calibration-cue-help">确认四路信号与被试端声音就绪，再开始引导。</div>' : '<div class="calibration-cue-help"><span>听到两音开始，三音结束 · 完成提示后再睁眼</span><div>'+button('cue-start','试听开始音')+button('cue-complete','试听完成音')+'</div><small>试听先有短暂静音，便于蓝牙输出准备。</small></div>';
        const baseline=s?.calibration || {};
        const baselineNote=baseline.baseline_id ? '<div class="baseline-note">'+(baseline.reused?'已载入个人基线':'个人基线已保存')+' · '+escape(s?.participant_id || this.form.participant)+'<span>'+escape(dateLabel(baseline.created_at))+' · '+escape(baseline.baseline_id.slice(0,8))+'</span></div>' : '';
        const kicker = '<div class="session-kicker">'+escape(modes[mode])+' / '+escape(s?.session_id ? '会话 '+s.session_id.slice(0,8) : '新训练')+'</div>';
        let content = '';
        if (!s || phase === 'idle') {
          const device = mode === 'live' ? '<label>设备地址<input data-field="address" value="'+escape(this.form.address)+'" placeholder="扫描选择或填写 Muse 2 地址"></label><div class="session-device-list">'+this.devices.map(d=>'<button type="button" class="res-small-button" data-address="'+escape(d.address)+'">'+escape(d.name || 'Muse 2')+' · '+escape(d.address)+'</button>').join('')+'</div>'+button('scan','扫描 Muse 2') : '';
          const replay = mode === 'replay' ? '<label>回放记录<select data-field="replay">'+(this.sessions.length ? this.sessions.map(x=>'<option value="'+escape(x.session_id)+'" '+(x.session_id===this.form.replay?'selected':'')+'>'+escape((x.mode==='live'?'实机':x.mode==='synthetic'?'模拟':'回放')+' · '+(x.participant_id || x.session_id.slice(0,8))+' · '+(x.created_at || '').slice(0,16))+'</option>').join('') : '<option value="">暂无记录</option>')+'</select></label>'+button('records','刷新记录') : '';
          content = '<h1>'+ (mode === 'live' ? '连接你的 Muse 2' : mode === 'replay' ? '再次走进这段声场' : '开始一次完整体验')+'</h1><p>'+ (mode === 'live' ? '佩戴头环并保持接触稳定。使用同一参与者编号，可直接载入已保存的个人基线。' : mode === 'replay' ? '选择已保存的记录，使用当时的数据驱动画面和声音。' : '首次建立个人基线，后续训练复用。所有脑电与分数均由模拟源产生。')+'</p><div class="session-form"><label>匿名参与者编号<input data-field="participant" maxlength="48" value="'+escape(this.form.participant)+'"></label>'+(mode === 'replay' ? '' : '<label>训练安排<select data-field="protocol"><option value="daily" '+(this.form.protocol==='daily'?'selected':'')+'>正式训练日 · 4 轮</option><option value="single" '+(this.form.protocol==='single'?'selected':'')+'>单轮体验</option></select></label><label>每轮有效时间<select data-field="training"><option value="60" '+(this.form.training==='60'?'selected':'')+'>1 分钟 · 原方案</option><option value="120" '+(this.form.training==='120'?'selected':'')+'>2 分钟 · 延长配置</option></select></label>')+device+replay+'</div><div class="session-actions">'+button('connect',this.busy?'正在连接…':mode==='replay'?'载入回放':'连接并继续',true)+'</div><small>'+(mode === 'replay' ? '使用记录中保存的个人校准 · 播放训练片段至结束' : '首次前测：闭眼 60 秒 → 睁眼 60 秒；之后使用同一基线训练')+'</small>';
        } else if (phase === 'connected') {
          content = '<div class="session-orbit">◎</div><h1>连接就绪，建立首次前测</h1><p>先检查四个触点。闭眼 60 秒采集静息对照，再睁眼 60 秒建立训练基线。</p><div class="session-device-info"></div>'+contactPanel()+'<div class="contact-summary"></div>'+cueHelp+'<div class="session-actions">'+button('calibrate_closed',this.isOperator?'佩戴确认，播放闭眼引导':'开始闭眼前测',true)+button('disconnect','断开连接')+'</div><small>两个阶段只累计合格数据时间，开始与完成时播放短提示音。</small>';
        } else if (phase === 'preparing_closed' || phase === 'preparing_open' || phase === 'preparing_training') {
          content='<h1>'+escape(phases[phase])+'</h1><p>被试窗口正在播放'+(s?.presentation?.cue_mode==='tone'?'短提示音':'语音引导与短提示音')+'。完整播完后才开始本段计时，当前不累计有效时间。</p><div class="session-actions">'+button('cancel_preparation','取消本段引导')+'</div><small>如果取消或出现播放故障，请口头告知被试睁眼，等待重新开始。</small>';
        } else if (calibrationPaused) {
          content='<h1>前测已暂停</h1><p>本段有效时间已保留。请检查被试窗口和佩戴情况。若被试改变了睁闭眼状态，先口头说明应恢复的状态，再继续；需要完整引导时可重新前测。</p><div class="session-actions">'+button('resume','确认被试状态并继续',true)+button('calibrate_closed','重新前测')+button('finish','结束本次')+'</div>';
        } else if (phase === 'calibrating_closed' || phase === 'calibrating_open') {
          content = '<div class="session-orbit '+(phase==='calibrating_closed'?'closed-eye':'')+'">'+(phase==='calibrating_closed'?'◡':'◎')+'</div><h1>'+(phase==='calibrating_closed'?'轻轻闭眼，保持放松':'睁开眼睛，安静注视')+'</h1><p>'+(phase==='calibrating_closed'?'保持舒适坐姿，听到三音完成提示后再睁眼，无需估计时间。':'自然看向画面，正常眨眼、放松下颌，完成时会有三音提示。')+'</p><div class="calibration-clock">00:00 <span>/ 01:00</span></div><div class="calibration-progress"><i></i></div>'+contactPanel()+'<div class="calibration-quality contact-summary"></div><small>正在采集'+(phase==='calibrating_closed'?'闭眼对照':'睁眼基线')+' · 两段完成后展示专注与放松曲线</small>'+(this.isOperator?'<div class="session-actions">'+button('pause','暂停前测')+button('finish','结束本次')+'</div>':'');
        } else if (phase === 'closed_complete') {
          content = '<div class="session-orbit">✓</div><h1>第一段完成，请睁开眼睛</h1><p>接下来自然看向画面、正常眨眼，完成 60 秒睁眼静息前测，建立训练主基线。</p>'+cueHelp+'<div class="session-actions">'+button('calibrate_open',this.isOperator?'播放睁眼引导并开始':'开始睁眼前测',true)+'</div>';
        } else if (phase === 'ready') {
          content = '<h1>'+(mode === 'replay' ? '记录已载入，声场即将重现' : baseline.reused ? '沿用基线，继续这次训练' : '首次前测完成')+'</h1><p>'+(mode === 'replay' ? '使用原记录的校准参数与训练数据，不表示当前佩戴者的脑电。' : baseline.reused ? '使用相同标尺观察历次变化。四路信号稳定后即可开始，无需重复前测。' : '个人基线已建立，后续同编号训练将自动复用。下方两条曲线使用这套基线回算。')+'</p>'+baselineNote+(mode==='replay'?'':'<div class="ready-quality contact-summary"></div>')+'<div class="session-chart calibration-chart"><div class="chart-heading"><span>首次前测</span><div class="chart-legend"><i class="attention-curve"></i>专注分 <i class="relaxation-curve"></i>放松分</div></div>'+scoreChart(baseline.trajectory || [],{title:'首次前测专注分与放松分',stages:true})+'</div><div class="session-actions">'+button('start_training',mode === 'replay' ? '开始回放' : '播放提示，开始第 '+(s?.protocol?.round_number || 1)+' 轮 · '+time(s?.training?.target_seconds),true)+(mode==='replay'?'':button('calibrate_closed','主动重新前测'))+'</div><small>'+(ResonanceAudio.isLayeredProgram(s?.program_id || this.getProgram()) ? '40／55／70 分依次叠满三层' : '80 分达到完整反馈')+' · 分数表示相对个人基线的状态</small>';
        } else if (phase === 'resting') {
          const protocol=s.protocol || {}, last=(protocol.completed_rounds || []).at(-1)?.summary || {};
          content='<h1>第 '+Number(protocol.completed_round_count || 0)+' 轮已完成</h1><p>休息一分钟，自然放松。音乐已暂停，本轮数据已保存，下一轮继续使用同一基线。</p><div class="round-rest-time"><strong>'+time(Math.ceil(protocol.rest_remaining_seconds || 0))+'</strong><span>'+(protocol.rest_remaining_seconds>0?'休息剩余时间':'休息完成，等待工作人员开始')+'</span></div>'+dailyRoundTable(protocol)+'<div class="session-actions">'+button('start_training','播放提示，开始第 '+Number(protocol.round_number || 1)+' 轮',true)+button('finish','今天先到这里')+'</div><div class="session-chart"><div class="chart-heading"><span>上一轮专注与放松</span></div>'+scoreChart(last.score_trajectory || [],{title:'上一轮训练曲线',peak:last.peak})+'</div>';
        } else if (phase === 'completed') {
          const rounds=s.protocol?.completed_rounds || [], selectedRound=rounds.find(r=>r.round_number===this.resultRound), summary = selectedRound?.summary || s.summary || {}, peak=summary.peak || {};
          const roundSelector=rounds.length?'<label class="round-result-select">查看单轮结果<select class="result-round-select"><option value="0">最后一轮</option>'+rounds.map(r=>'<option value="'+r.round_number+'" '+(this.resultRound===r.round_number?'selected':'')+'>第 '+r.round_number+' 轮</option>').join('')+'</select></label>':'';
          const peakStatsPresent=summary.peak && typeof summary.peak==='object' && !Array.isArray(summary.peak);
          const metrics=[['平均专注分',number(summary.mean_score)],['平均放松分',number(summary.mean_meditation)],['最高专注分',number(peak.peak_value)],['峰值累计时间',duration(peak.total_peak_duration)],['最长连续峰值',duration(peak.longest_peak_duration)]];
          const legend='<div class="chart-legend"><i class="attention-curve"></i>专注分 <i class="relaxation-curve"></i>放松分</div>';
          let chart='';
          if(this.resultTab==='history') {
            const groups=historyGroups(this.history), selected=groups.find(g=>g.key===this.historyGroup) || groups[0];
            if(this.historyStatus==='loading' || this.historyStatus==='idle') chart='<div class="session-chart-empty">正在读取历次训练…</div>';
            else if(this.historyStatus==='error') chart='<div class="session-chart-empty">历史记录暂不可用 '+button('history-retry','重新读取')+'</div>';
            else if(s.protocol?.plan==='daily') {
              const daily=this.dailyHistory.filter(d=>d.complete && !d.protocol?.mixed_baselines && d.baseline_id===baseline.baseline_id && d.protocol?.round_target_seconds===s.protocol.round_target_seconds);
              const points=daily.map(d=>({created_at:d.training_date,summary:{mean_score:d.mean_score,mean_meditation:d.mean_meditation}}));
              chart=points.length?'<div class="chart-heading"><span>完整训练日 · 四轮按有效时间加权</span></div><div class="history-charts">'+historyChart(points,'mean_score','日均专注分')+historyChart(points,'mean_meditation','日均放松分')+'</div><div class="history-dates">'+daily.map((d,i)=>'<span>第 '+(i+1)+' 日 · '+escape(d.training_date)+'</span>').join('')+'</div><div class="chart-note">同一基线与每轮时长；中途续训按日期合并，不把第四轮分数当作日均。场景和曲目变化请结合导出记录解读。</div>':'<div class="session-chart-empty">尚无同一基线下完成四轮的训练日，已完成轮次会保留在今日汇总中。</div>';
            }
            else if(!selected) chart='<div class="session-chart-empty">暂无同一参与者、同一数据来源的记录</div>';
            else {
              this.historyGroup=selected.key;
              chart='<div class="history-toolbar"><label>比较基线 <select class="history-group-select" aria-label="选择历次训练比较基线">'+groups.map(g=>'<option value="'+escape(g.key)+'" '+(g.key===selected.key?'selected':'')+'>'+escape((g.items[0].baseline_id || g.items[0].summary?.baseline_id) ? '基线 '+(g.items[0].baseline_id || g.items[0].summary.baseline_id).slice(0,8) : '旧记录 · 独立基线')+' · '+g.items.length+' 次</option>').join('')+'</select></label><span>仅比较同编号、同来源、同基线及算法的训练</span></div><div class="history-charts">'+historyChart(selected.items,'mean_score','平均专注分')+historyChart(selected.items,'mean_meditation','平均放松分')+historyChart(selected.items,'peak.total_peak_duration','峰值累计时间',true)+historyChart(selected.items,'peak.longest_peak_duration','最长连续峰值',true)+'</div><div class="history-dates">'+selected.items.map((entry,i)=>'<span>第 '+(i+1)+' 次 · '+escape(dateLabel(entry.created_at))+'</span>').join('')+'</div><div class="chart-note">个人峰值阈值随每次最高分变化；结合平均分一起观察。请结合每轮时长、训练画面与曲目解读变化；每日汇总和单轮记录分别比较。</div>';
            }
          } else {
            const initial=this.resultTab==='baseline';
            chart='<div class="chart-heading"><span>'+(initial?'首次前测 · 同一基线回算':'第 '+(selectedRound?.round_number || s.protocol?.round_number || 1)+' 轮 · 无效片段保留空缺')+'</span>'+legend+'</div>'+scoreChart(initial ? (summary.calibration_trajectory || baseline.trajectory || []) : (summary.score_trajectory || []),{title:initial?'前测专注分与放松分':'训练专注分与放松分',peak:initial?null:peak,stages:initial})+'<div class="chart-note">'+(initial?'闭眼静息对照与睁眼训练基线；两条曲线使用同一套个人参数。':finite(peak.threshold)?'峰值区间 ≥ '+number(peak.threshold)+' 分（最高分 − 5） · 峰值出现在 '+time(peak.peak_time)+' · 无效数据不计峰值时长':peakStatsPresent?'没有足够的有效数据计算峰值。':'该记录尚未提供峰值统计。')+'</div>';
          }
          content=dailyRoundTable(s.protocol)+roundSelector+'<div class="result-heading"><div><h1>'+(s.protocol?.plan==='daily'?(s.protocol.day_complete?'今日四轮训练已完成':'今日训练已保存'):'这一段训练，已完成')+'</h1><p>'+escape(modes[mode])+' · '+escape(s.participant_id || this.form.participant)+' · '+escape(dateLabel(s.created_at))+'</p></div><div class="result-times"><span>有效训练 <strong>'+time(summary.valid_seconds)+'</strong> / '+time(s?.training?.target_seconds)+'</span><span title="'+(finite(summary.wall_elapsed_seconds)?'从开始训练到结束的总耗时':'旧记录未保存总耗时')+'">总耗时 <strong>'+(finite(summary.wall_elapsed_seconds)?time(summary.wall_elapsed_seconds):'—')+'</strong></span></div></div><div class="session-result-stats">'+metrics.map(([label,value])=>'<div><strong>'+value+'</strong><span>'+label+'</span></div>').join('')+'</div><div class="result-tabs" role="tablist" aria-label="结果曲线">'+[['training','本次训练'],['baseline','首次前测'],['history','历次趋势']].map(([id,label])=>'<button type="button" role="tab" aria-selected="'+(this.resultTab===id)+'" data-action="tab-'+id+'">'+label+'</button>').join('')+'</div><div class="session-chart result-chart" role="tabpanel">'+chart+'</div><div class="result-footer"><div class="session-actions"><a class="session-primary" href="/api/sessions/'+encodeURIComponent(s.session_id)+'/export">导出完整记录</a><button type="button" class="res-small-button" data-action="export-edf" '+(this.edfBusy?'disabled':'')+'>'+(this.edfBusy?'正在打包 EDF…':'导出原始脑电 EDF')+'</button>'+button('new','再开始一次')+'</div><small>EDF 按阶段分文件 · 保留原始采样<br>基线 '+escape(summary.baseline_id?.slice(0,8) || '旧记录')+' · 保留原算法与标尺<br>训练表现不代表已证实的认知提升</small></div>';
        } else {
          content = '<div class="session-orbit">'+(phase==='connecting'?'◌':'!')+'</div><h1>'+escape(phases[phase] || '连接状态变化')+'</h1><p class="session-connection-message"></p><div class="session-actions">'+(phase==='connecting'?'':button('reconnect','重新连接',true)+button('new','重新配置'))+'</div><small>'+(mode === 'live' ? 'Muse 2 连接失败时不会切换为模拟数据。' : '保持所选数据来源，请检查记录或服务后重试。')+'</small>';
        }
        drawer.innerHTML = kicker+content+'<div class="session-error" role="status"></div>';
        drawer.querySelectorAll('[data-address]').forEach(button => button.addEventListener('click',()=>{this.form.address=button.dataset.address;drawer.querySelector('[data-field="address"]').value=this.form.address;}));
      }
      const error = drawer.querySelector('.session-error'); if (error) {
        const message = this.error || this.exportError || this.notice;
        if (error.dataset.message !== message || error.dataset.other !== String(this.otherSession)) {
          error.dataset.message = message; error.dataset.other = String(this.otherSession); error.textContent = message;
          if (this.otherSession) { const reload = document.createElement('button'); reload.type='button'; reload.dataset.action='reload'; reload.className='res-small-button'; reload.textContent='刷新并同步当前会话'; reload.style.marginTop='10px'; reload.style.display='block'; error.appendChild(reload); }
        }
        error.hidden = !message;
      }
      const connect = drawer.querySelector('[data-action="connect"]'); if(connect) connect.disabled=this.busy || Boolean(this.otherSession) || (this.form.protocol==='daily' && this.planPreview?.day_complete===true);
      const deviceInfo = drawer.querySelector('.session-device-info'); if(deviceInfo) deviceInfo.textContent = [s?.connection?.device_name, s?.connection?.sample_rate ? s.connection.sample_rate+' Hz' : '',Array.isArray(s?.connection?.channels)?s.connection.channels.join(' / '):''].filter(Boolean).join(' · ');
      const message = drawer.querySelector('.session-connection-message'); if(message) message.textContent = s?.connection?.message || this.error || '请检查设备和本地服务后重试。';
      const signalAge = s?.sent_at ? Date.now() - Date.parse(s.sent_at) : Infinity;
      const contactsFresh = this.online && performance.now() - this.receivedAt <= 2500 && Number.isFinite(signalAge) && signalAge <= 2500;
      const displayReady=!s?.presentation?.dual_screen || (this.online && context.presentation?.participant_ready===true);
      const start=drawer.querySelector('[data-action="start_training"]'); if(start) start.disabled=this.busy || !displayReady || (phase==='resting' && (s?.protocol?.rest_remaining_seconds || 0)>0) || (mode!=='replay' && (!contactsFresh || s?.quality?.valid!==true));
      this.root.querySelectorAll('[data-action="calibrate_closed"],[data-action="calibrate_open"],[data-action="resume"],.session-recalibrate').forEach(button => {
        button.disabled=this.busy || !displayReady || (mode!=='replay' && (!contactsFresh || s?.quality?.valid!==true));
        button.title=!displayReady ? '请先打开并启用被试窗口声音' : (!contactsFresh || s?.quality?.valid!==true) ? '先检查四路触点，等待信号可用' : '';
      });
      if (this.isOperator) {
        const p=context.presentation, ready=this.online && p?.participant_ready===true;
        const status=this.q('.operator-display-status');
        status.textContent=ready ? '画面与声音已就绪' : phase.startsWith('preparing_') ? '正在播放开始引导' : p?.client_id ? '等待声音就绪' : '等待打开并启用被试窗口';
        status.title=p?.message || '';
        if (p?.reason && !phase.startsWith('preparing_')) status.textContent+=' · 请确认被试状态';
        status.classList.toggle('ready',ready);
        if(this.q('.operator-training-scene'))this.q('.operator-training-scene').disabled=this.busy || phase==='training' || phase.startsWith('preparing_');
        this.q('.operator-cue-mode').disabled=this.busy || phase.startsWith('preparing_') || phase.startsWith('calibrating_');
        this.q('.operator-live-details').hidden=!inTraining;
        this.workspace?.update(context);
        const bands=s?.feedback?.bands || {};
        this.q('.operator-bands').textContent=[s?.connection?.device_name,s?.connection?.sample_rate ? s.connection.sample_rate+' Hz' : '',Array.isArray(s?.connection?.channels)?s.connection.channels.join(' / '):'',...Object.entries(bands).filter(([,value])=>finite(value)).map(([band,value])=>band+' '+number(value)+' μV²')].filter(Boolean).join(' · ');
      }
      this.root.querySelectorAll('.contact-card').forEach(card => {
        const channel = contactsFresh ? s?.quality?.channel_quality?.[card.dataset.channel] : null;
        const powerline = hasPowerlineWarning(channel);
        const state = channel?.valid === true ? powerline ? 'warning' : 'stable' : channel?.valid === false ? 'adjust' : 'waiting';
        card.dataset.contactState = state;
        card.querySelector('.contact-state').textContent = state === 'stable' ? '信号可用' : state === 'warning' ? '可采集 · 工频较强' : state === 'adjust' ? '需调整' : '等待数据';
        const reasons = Array.isArray(channel?.reasons) ? [...new Set(channel.reasons.map(reason => contactReasons[reason] || '信号需稳定'))] : [];
        if (powerline && state === 'adjust') reasons.push('工频较强');
        card.querySelector('.contact-reason').textContent = state === 'stable' ? '当前信号合格' : state === 'warning' ? '已作滤波 · 仍建议检查接触' : state === 'adjust' ? reasons.join(' · ') || '请检查此处接触' : '等待新信号';
      });
      this.root.querySelectorAll('.contact-summary').forEach(contactMessage => { contactMessage.textContent = contactsFresh ? contactSummary(s?.quality) : '正在等待新的触点数据，请检查连接。'; });
      if (this.isOperator) {
        let needsAttention = !contactsFresh || s?.quality?.valid !== true;
        this.root.querySelectorAll('.operator-signal-row').forEach(row => {
          const channel = contactsFresh ? s?.quality?.channel_quality?.[row.dataset.channel] : null;
          const powerline = hasPowerlineWarning(channel);
          const state = channel?.valid === true ? powerline ? 'warning' : 'stable' : channel?.valid === false ? 'adjust' : 'waiting';
          row.dataset.contactState = state;
          row.querySelector('.operator-signal-state').textContent = state === 'stable' ? '可用' : state === 'warning' ? '可用 · 工频' : state === 'adjust' ? '需调整' : '等待数据';
          const reasons = Array.isArray(channel?.reasons) ? [...new Set(channel.reasons.map(reason => contactReasons[reason] || '信号需稳定'))] : [];
          row.title = state === 'stable' ? '当前信号合格' : state === 'warning' ? '工频较强，已作滤波处理，仍建议检查接触' : state === 'adjust' ? reasons.join(' · ') || '请检查此处接触' : '正在等待新的触点数据';
          needsAttention ||= state !== 'stable';
        });
        const details = this.q('.operator-signal-details');
        details.hidden = !needsAttention;
        details.querySelector('.operator-signal-summary').textContent = contactsFresh ? contactSummary(s?.quality) : '正在等待新的触点数据，请检查连接。';
      }
      if (phase === 'calibrating_closed' || phase === 'calibrating_open') {
        const elapsed = s?.calibration?.[phase==='calibrating_closed'?'closed_seconds':'open_seconds'] || 0;
        const target = s?.calibration?.target_seconds || 60;
        const clock = drawer.querySelector('.calibration-clock'); if(clock) clock.innerHTML = time(elapsed)+' <span>/ '+time(target)+'</span>';
        const progress = drawer.querySelector('.calibration-progress i'); if(progress) progress.style.width=Math.min(100,elapsed/target*100)+'%';
      }
    }
    dispose() { this.closed=true; clearInterval(this.timer); clearTimeout(this.reconnectTimer); this.ws?.close(); this.workspace?.dispose(); window.CalibrationCues?.dispose(); }
  }
  window.ResonanceSession = ResonanceSession;
})();
