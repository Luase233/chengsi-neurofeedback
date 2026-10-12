/* Staff-only presentation. Navigation never issues acquisition commands. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const time = value => {const n=Math.max(0,Math.floor(Number(value)||0));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');};
  const date = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '日期未记录';
  const fmt = value => finite(value) ? value.toFixed(1) : '—';
  const legend = '<div class="chart-legend"><i class="attention-curve"></i>专注 <i class="relaxation-curve"></i>放松</div>';
  const importPhases = ['idle','completed','error'];
  const importModes = {live:'实机数据',synthetic:'模拟数据',replay:'记录回放'};
  const baselineStatus = {restored:'个人基线已恢复；后续编号、设备和算法条件兼容时可复用。',existing:'已保留本机已有的个人基线。',not_included:'此记录不包含可恢复的个人基线。',unusable:'此记录的个人基线无法恢复；后续训练需要重新前测。'};
  const importError = (status,code) => ({active_session:'当前会话尚未结束，请完成本次训练后再导入。',session_conflict:'这份记录与本机已有记录冲突，未覆盖已有数据。请核对所选导出文件。',baseline_conflict:'这份记录的个人基线与本机已有基线冲突，未覆盖已有数据。请核对被试编号和导出文件。'}[code] || {409:'当前正在运行会话，或此记录与已有记录冲突。请在会话结束后重试，并核对所选导出文件。',422:'这个 ZIP 不是可导入的训练导出记录，或其中的数据已损坏。请重新选择完整导出的 ZIP。',413:'所选 ZIP 太大，无法导入。请改用不含 EDF 的普通记录导出包。',415:'请选择系统导出的 ZIP 记录文件。'}[status] || '导入暂时未完成。请确认本地服务正常后重试；重复选择同一个文件不会重复添加记录。');
  const importWarnings = {baseline_unusable:'归档中的基线无法复用，后续训练需要重新前测。',baseline_reference_only:'EDF 中的历史前测仅作为参考片段，未增加为新的训练会话。',attachments_not_added:'同一会话已存在，保留原导入记录；本次包里的额外 EDF 附件未重复归档。'};
  class ResonanceOperatorWorkspace {
    constructor(session) {
      this.session=session;this.root=session.root;this.q=s=>this.root.querySelector(s);
      this.points=[];this.segment=0;this.lastSample=null;this.chartKey='';this.telemetry=null;this.previewFrame=null;this.previewRequest=0;this.showWearControls=false;
      this.course=document.createElement('section');this.course.className='operator-course-panel';this.course.setAttribute('aria-label','训练周期与当日安排');this.q('.operator-stage-header').after(this.course);
      this.notice=document.createElement('p');this.notice.className='operator-notice';this.notice.setAttribute('role','status');this.notice.hidden=true;this.q('.operator-stage-header').after(this.notice);
      this.dialog=document.createElement('dialog');this.dialog.className='operator-inspect';
      this.dialog.innerHTML='<header><h2></h2><button type="button" class="res-small-button" data-inspect-close>关闭</button></header><div class="operator-inspect-body"></div>';
      this.root.appendChild(this.dialog);
      this.dialog.querySelector('[data-inspect-close]').addEventListener('click',()=>this.dialog.close());
      this.dialog.addEventListener('click',event=>{if(event.target===this.dialog)this.dialog.close();});
      this.importInput=this.q('[data-import-file]');this.importButton=this.q('[data-import-data]');this.importHint=this.q('[data-import-hint]');
      this.importInput?.addEventListener('change',()=>{
        const file=this.importInput.files?.[0];this.importInput.value='';
        if(file)this.importFile(file);
      });
      this.importInput?.addEventListener('cancel',()=>{this.importInput.value='';});
      this.channel=typeof BroadcastChannel==='function'?new BroadcastChannel('chengsi-ui-guidance'):null;
      if(this.channel)this.channel.onmessage=event=>{
        const d=event.data;if(d?.type==='preview-frame'){this.acceptPreview(d);return;}
        if(d?.type!=='telemetry'||d.sessionId!==this.session.sessionId||!Array.isArray(d.levels)||d.levels.length!==3||!d.levels.every(finite))return;
        this.telemetry={...d,received:performance.now(),levels:d.levels.map(v=>Math.max(0,Math.min(1,v)))};
      };
      this.previewTimer=setInterval(()=>this.fetchPreview(),1500);
      this.root.addEventListener('click',event=>{
        const importButton=event.target.closest('[data-import-data]');
        if(importButton&&!importButton.disabled)this.chooseImport();
        if(event.target.closest('[data-import-history]')&&!this.importBusy&&this.importResult)this.showHistory({participantId:this.importResult.participant_id,mode:this.importResult.mode});
        const replayButton=event.target.closest('[data-import-replay]');
        if(replayButton&&!replayButton.disabled&&this.importResult)this.prepareImportedReplay();
        const guide=event.target.closest('[data-guide-step]');
        if(guide&&!guide.disabled)this.playGuidance(guide.dataset.guideStep);
        if(event.target.closest('.operator-view-baseline'))this.showBaseline();
        const nav=event.target.closest('[data-workspace-view]');
        if(nav){if(nav.dataset.workspaceView==='history')this.showHistory();else if(nav.dataset.workspaceView==='settings'){const settings=this.q('.operator-settings');settings.open=true;settings.scrollIntoView({block:'nearest',behavior:'smooth'});settings.querySelector('select,button')?.focus();}else this.dialog.close();}
        const step=event.target.closest('[data-workspace-step]');
        if(step&&!step.disabled){if(step.dataset.workspaceStep==='baseline'&&this.session.snapshot?.calibration?.valid)this.showBaseline();else if(step.dataset.workspaceStep==='fit'){const controls=this.q('.operator-guide-controls');if(this.session.snapshot?.phase==='ready'){this.showWearControls=true;if(controls)controls.hidden=false;const confirmations=this.q('.operator-wear-confirmation');if(confirmations)confirmations.hidden=false;}(controls?.hidden?this.q('.operator-contacts'):controls)?.scrollIntoView({block:'nearest',behavior:'smooth'});if(!controls?.hidden)this.q('[data-guide-step]')?.focus();}else{this.dialog.close();this.q('.res-stage')?.scrollIntoView({block:'start',behavior:'smooth'});}}
      });
    }
    importBlockReason() {
      if(this.importBusy)return '正在导入数据，请稍候。';
      if(this.session.busy)return '当前操作完成后可导入。';
      if(!this.session.online)return '本地服务连接后可导入。';
      if(this.session.otherSession||!importPhases.includes(this.session.snapshot?.phase||'idle'))return '当前会话尚未结束，完成后可导入。';
      return '';
    }
    updateImportControls() {
      const reason=this.importBlockReason();
      if(this.importButton){this.importButton.disabled=Boolean(reason);this.importButton.setAttribute('aria-busy',String(Boolean(this.importBusy)));this.importButton.querySelector('span').textContent=this.importBusy?'正在导入…':'导入数据';}
      if(this.importHint)this.importHint.textContent=reason||'支持普通记录和含 EDF 的导出 ZIP。';
      const replay=this.dialog.querySelector('[data-import-replay]');if(replay)replay.disabled=Boolean(reason);
    }
    chooseImport() {
      const reason=this.importBlockReason();
      if(reason){this.inspect('导入数据','<p>'+esc(reason)+'</p>');return;}
      // Reset before opening as well as after selection: cancelling the picker
      // must not prevent choosing the same ZIP on the next attempt.
      this.importInput.value='';this.importInput.click();
    }
    async importFile(file) {
      if(!file||this.importBusy||this.session.busy)return;
      const reason=this.importBlockReason();
      if(reason){this.inspect('导入数据','<p>'+esc(reason)+'</p>');return;}
      if(!/\.zip$/i.test(file.name)||!file.size){this.inspect('导入数据','<p>请选择完整导出的 ZIP 记录文件，普通记录和含 EDF 的导出包均可。</p>');return;}
      if(file.size>64*1024*1024){this.inspect('导入数据','<p>'+importError(413)+'</p>');return;}
      this.importBusy=true;this.session.busy=true;this.session.emit();
      this.inspect('导入数据','<p role="status">正在检查并导入记录，请稍候…</p><p class="operator-note">导入完成后可查看训练记录或选择回放。</p>');
      this.dialog.setAttribute('aria-busy','true');
      try {
        // Verify current server state too; this page's snapshot may be stale.
        const current=await this.session.request('/api/state'),snapshot=current.snapshot||current;
        if(!importPhases.includes(snapshot.phase)){
          this.session.reconcile?.(snapshot,true);
          this.inspect('导入数据','<p>当前会话尚未结束，请完成本次训练后再导入。</p>');return;
        }
        const response=await fetch('/api/sessions/import',{method:'POST',headers:{'Content-Type':'application/zip'},body:file});
        const data=await response.json().catch(()=>null);
        if(!response.ok){const error=new Error(importError(response.status,data?.code));error.importMessage=true;throw error;}
        if(!data||!['imported','already_imported'].includes(data.status)||typeof data.session_id!=='string'||typeof data.participant_id!=='string'||!importModes[data.mode])throw new Error('Invalid import response');
        this.importResult=data;
        const previousError=this.session.error;
        await this.session.loadSessions();
        if(this.disposed)return;
        const refreshed=this.session.sessions.some(record=>record.session_id===data.session_id);
        if(!refreshed)this.session.error=previousError;
        this.showImportResult(data,refreshed);
      } catch(error) {
        if(!this.disposed)this.inspect('导入数据','<p role="alert">'+esc(error.importMessage?error.message:importError(0))+'</p>');
      } finally {
        this.importBusy=false;this.session.busy=false;
        if(!this.disposed){this.dialog.setAttribute('aria-busy','false');this.session.emit();this.updateImportControls();}
      }
    }
    showImportResult(data,refreshed=true) {
      const title=data.status==='already_imported'?'这份记录已在本机，无需再次导入。':'训练记录已导入。';
      const warnings=Array.isArray(data.warnings)?[...new Set(data.warnings)].map(code=>importWarnings[code]).filter(Boolean):[];
      this.inspect('导入数据','<p class="operator-import-success" role="status">'+title+'</p><dl class="operator-import-summary"><div><dt>被试编号</dt><dd>'+esc(data.participant_id)+'</dd></div><div><dt>数据来源</dt><dd>'+esc(importModes[data.mode])+'</dd></div><div><dt>个人基线</dt><dd>'+esc(baselineStatus[data.baseline_status]||baselineStatus.unusable)+'</dd></div></dl>'+warnings.map(message=>'<p class="operator-note">'+esc(message)+'</p>').join('')+(!refreshed?'<p class="operator-note">记录已保存，列表暂时未更新；可稍后刷新回放记录。</p>':'')+'<div class="operator-import-actions"><button type="button" class="session-primary" data-import-history>查看训练记录</button><button type="button" class="res-small-button" data-import-replay>记录回放</button></div><p class="operator-note">导入不会开始采集。回放需在选择记录后手动载入。</p>');
    }
    async prepareImportedReplay() {
      const reason=this.importBlockReason(),data=this.importResult;
      if(!data||reason){if(reason)this.inspect('记录回放','<p>'+esc(reason)+'</p>');return;}
      const changed=await this.session.setMode('replay',{finishActive:false});
      if(!changed)return;
      if(!this.session.sessions.some(record=>record.session_id===data.session_id)){this.inspect('记录回放','<p>记录列表暂时无法读取，请关闭后刷新回放记录。</p>');return;}
      this.session.form.replay=data.session_id;this.session.form.participant=data.participant_id;this.session.saveParticipant();this.session.renderKey='';this.session.emit();
      this.dialog.close();this.q('.res-stage')?.scrollIntoView({block:'start',behavior:'smooth'});this.q('[data-field="replay"]')?.focus();
    }
    async playGuidance(step) {
      if(this.wearRequest)return;
      const sessionId=this.session.snapshot?.session_id??this.session.sessionId??null;
      this.wearRequest=true;this.wearError='';this.session.emit();
      try {
        {
          const data=await this.session.request('/api/presentation/guidance',{step,session_id:sessionId});
          const snapshot=data.snapshot||data;
          if((snapshot.session_id??null)!==sessionId||(this.session.snapshot?.session_id??this.session.sessionId??null)!==sessionId)throw new Error('会话已切换，请在当前会话重新播放佩戴引导。');
          this.session.displayPresentation=snapshot.presentation||null;
          if(snapshot.session_id&&snapshot.session_id===this.session.sessionId)this.session.accept(snapshot);
        }
        // The server broadcasts the request to local windows and paired iPads.
        this.root.querySelectorAll('[data-guide-step]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.guideStep===step)));
      } catch(error) {this.wearError=error.message||'佩戴引导暂时无法播放，请重试。';}
      finally {this.wearRequest=false;this.session.emit();}
    }
    acceptsPreviewIdentity(frame) {
      const presentation=this.session.displayPresentation||this.session.snapshot?.presentation||{};
      const owner=presentation.participant_client_id||presentation.client_id;
      return (frame.sessionId??null)===(this.session.sessionId??null)&&(!owner||frame.clientId===owner);
    }
    async fetchPreview(){
      if(this.previewLoading||this.disposed||document.hidden)return;
      this.previewLoading=true;
      try{
        const data=await this.session.request('/api/presentation/preview');
        if(this.disposed||!data.frame||!finite(data.age_ms)||data.age_ms>3500)return;
        const frame=data.frame;
        // The iPad's wall clock can differ from the operator computer's clock.
        this.acceptPreview({clientId:frame.client_id,sessionId:frame.session_id,
          phase:frame.phase,wearStep:frame.wearStep,frame:frame.image,capturedAt:Date.now()-data.age_ms});
      }catch(_){/* A missing thumbnail never interrupts the training workflow. */}
      finally{this.previewLoading=false;}
    }
    acceptPreview(frame) {
      if(!this.acceptsPreviewIdentity(frame)||typeof frame.clientId!=='string'||!frame.clientId||!finite(frame.capturedAt)||Math.abs(Date.now()-frame.capturedAt)>4000||typeof frame.frame!=='string'||frame.frame.length>260000||!/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(frame.frame))return;
      if(this.previewFrame&&frame.capturedAt<this.previewFrame.capturedAt)return;
      const request=++this.previewRequest,image=new Image(),received=performance.now();
      image.onload=()=>{if(request!==this.previewRequest||!this.acceptsPreviewIdentity(frame)||!image.naturalWidth||image.naturalWidth>480||image.naturalHeight>640)return;this.previewFrame={image,received,capturedAt:frame.capturedAt,sessionId:frame.sessionId,clientId:frame.clientId,phase:frame.phase};if(typeof frame.wearStep==='string')this.root.querySelectorAll('[data-guide-step]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.guideStep===frame.wearStep)));this.drawPreview();};
      image.src=frame.frame;
    }
    levels() {return this.telemetry&&performance.now()-this.telemetry.received<3000&&this.telemetry.sessionId===this.session.sessionId ? this.telemetry.levels : null;}
    inspect(title,html) {this.inspectTitle=title;this.dialog.querySelector('h2').textContent=title;this.dialog.querySelector('.operator-inspect-body').innerHTML=html;if(!this.dialog.open)this.dialog.showModal();}
    showBaseline() {
      const b=this.session.snapshot?.calibration;
      this.inspect('首次前测',b?.valid?'<p>'+esc(this.session.form.participant)+' · '+esc(date(b.created_at))+'</p><div class="session-chart"><div class="chart-heading"><span>同一套个人基线</span>'+legend+'</div>'+window.ResonanceSessionCharts.scoreChart(b.trajectory||[],{stages:true,title:'首次前测专注与放松'})+'</div><p class="operator-note">后续训练沿用此基线。重新佩戴或需要重新建立基线时，由工作人员主动操作。</p>':'<p>首次前测尚未完成，请按当前流程继续。</p>');
    }
    async showHistory({participantId,mode}={}) {
      const identity=participantId||this.session.snapshot?.participant_id||this.session.form.participant;
      const source=mode||this.session.mode,request=this.historyInspectionRequest=(this.historyInspectionRequest||0)+1;
      this.inspect('训练记录','<p>正在读取 '+esc(identity)+' 的记录…</p>');
      try {
        const data=await this.session.request('/api/participant-history?participant_id='+encodeURIComponent(identity)+'&mode='+encodeURIComponent(source));
        if(!this.dialog.open||this.inspectTitle!=='训练记录'||request!==this.historyInspectionRequest)return;
        const entries=Array.isArray(data.entries)?data.entries:[];
        this.inspect('训练记录','<p>'+esc(identity)+' · '+({live:'实机数据',synthetic:'模拟数据',replay:'记录回放'}[source]||'')+'</p>'+(entries.length?'<div class="operator-records"><table><thead><tr><th>时间</th><th>平均专注</th><th>平均放松</th><th>有效训练</th><th>基线</th><th>记录</th></tr></thead><tbody>'+entries.slice().reverse().map(e=>'<tr><td>'+esc(date(e.created_at))+'</td><td>'+fmt(e.summary?.mean_score)+'</td><td>'+fmt(e.summary?.mean_meditation)+'</td><td>'+time(e.summary?.valid_seconds)+'</td><td>'+esc((e.baseline_id||e.summary?.baseline_id||'未记录').slice(0,8))+'</td><td><a href="/api/sessions/'+encodeURIComponent(e.session_id)+'/export">导出</a></td></tr>').join('')+'</tbody></table></div><p class="operator-note">历次趋势在训练结果中查看，只比较同一基线和算法的记录。</p>':'<p>当前编号与数据来源下暂无训练记录。</p>'));
      } catch (_) {if(this.dialog.open&&this.inspectTitle==='训练记录'&&request===this.historyInspectionRequest)this.inspect('训练记录','<p>记录暂时无法读取，请关闭后重试。</p>');}
    }
    record(context) {
      const s=context.snapshot;
      const roundIdentity=(s?.session_id || '')+':'+(s?.protocol?.round_number || 1);
      if(this.id!==roundIdentity){this.id=roundIdentity;this.points=[];this.segment=0;this.lastSample=null;this.chartKey='';this.telemetry=null;this.previewFrame=null;this.previewRequest++;this.showWearControls=false;this.wearError='';}
      if(context.phase!=='training'||!context.valid){
        if(this.points.length&&this.points.at(-1).valid){this.segment++;this.points.push({time:this.points.at(-1).time,valid:false,gap:true});}
        return;
      }
      const end=s.feedback.window_end,stamp=finite(end)?end:s.feedback.generated_at;
      if(stamp==null||stamp===this.lastSample)return;
      const elapsed=s.training?.wall_elapsed_seconds??s.training?.elapsed_seconds;
      if(!finite(elapsed))return;
      if(finite(end)&&finite(this.lastSample)&&end<=this.lastSample){this.segment++;return;}
      this.lastSample=stamp;
      this.points.push({time:elapsed,score:s.feedback.score,meditation:s.feedback.meditation,valid:true,stage:'training',segment:this.segment});
      if(this.points.length>2400)this.points.splice(0,this.points.length-2400);
    }
    update(context) {
      this.updateImportControls();
      this.record(context);
      const s=context.snapshot,phase=context.phase,b=s?.calibration||{};
      const plan=s?.protocol || this.session.planPreview;
      this.course.hidden=context.mode==='replay' || (s?s.protocol?.plan!=='daily':this.session.form.protocol!=='daily');
      if(!this.course.hidden){
        const completed=Number(plan?.completed_round_count || 0), totalDays=Number(plan?.total_days || 16);
        const recommended=ResonanceAudio.programs.find(p=>p.id===plan?.recommended_program_id)?.title;
        const cycle=plan?'计划第 '+Number(plan.week_number || 1)+' 周 · 第 '+Number(plan.day_in_week || 1)+' 个训练日':'4 周 · 每周 4 个训练日';
        const roundSeconds=Number(plan?.round_target_seconds || this.session.form.training || 60);
        const status=plan?.day_complete?'今日计划已完成，下个训练日再继续':phase==='resting'?'休息 '+time(Math.ceil(plan.rest_remaining_seconds || 0))+' · 到时手动开始':completed?'今日已完成 '+completed+' / 4 轮':'今日 4 轮，每轮有效 '+time(roundSeconds);
        this.course.innerHTML='<div class="course-heading"><strong>'+cycle+'</strong><span>已完成 '+Number(plan?.completed_days || 0)+' / '+totalDays+' 个训练日</span></div><div class="course-rounds">'+[1,2,3,4].map(n=>'<span class="'+(n<=completed?'done':n===Number(plan?.round_number || 1)?'current':'')+'">'+(n<=completed?'✓':'0'+n)+'</span>').join('<i aria-hidden="true"></i>')+'<strong>'+status+'</strong></div><p>'+ (Number(plan?.completed_days || 0)>=16?'四周训练已完成，请在训练结束后一周内安排原方案后测。':(recommended?'本周方案曲目：'+esc(recommended)+'；可在右侧选择试听曲目。':'')+'轮间休息 01:00；每天至少 '+time(roundSeconds*4+180)+'，首次前测与引导另计。')+'</p>';
      }
      if(this.lastPhase!==phase){this.lastPhase=phase;this.showWearControls=false;}
      const training=phase==='training'||phase==='paused'&&!String(s?.resume_phase||'').startsWith('calibrating_');
      const current=training||['preparing_training','resting'].includes(phase)?'training':phase==='completed'?'results':['preparing_closed','calibrating_closed','closed_complete','preparing_open','calibrating_open','ready'].includes(phase)||phase==='paused'?'baseline':'fit';
      const set=(selector,text)=>{const el=this.q(selector);if(el)el.textContent=text;};
      set('.operator-participant-label','被试 '+(s?.participant_id||this.session.form.participant));
      set('.operator-source-label',{live:'实机采集',synthetic:'模拟演示',replay:'记录回放'}[context.mode]||'');
      set('.operator-device-meta',[s?.connection?.device_name||'Muse 2',s?.connection?.sample_rate?s.connection.sample_rate+' Hz':null].filter(Boolean).join(' · '));
      set('.operator-stage-title h1',({idle:'佩戴检查',connecting:'连接设备',connected:'佩戴检查',preparing_closed:'闭眼前测',calibrating_closed:'闭眼前测',closed_complete:'准备睁眼前测',preparing_open:'睁眼前测',calibrating_open:'睁眼前测',ready:'等待开始训练',preparing_training:'训练开始引导',resting:'轮间休息',training:context.mode==='replay'?'记录回放':'训练进行中',paused:training?'训练已暂停':'前测已暂停',completed:'训练结果',disconnected:'连接待恢复',error:'需要处理'})[phase]||'本次训练');
      set('.operator-baseline-note',b.valid?(b.reused?'沿用首次个人基线':'本次已建立个人基线'):'等待个人基线');
      const baselineSummary=this.q('.operator-baseline-summary');if(baselineSummary)baselineSummary.hidden=!b.valid||phase==='completed';
      this.notice.textContent=this.wearError||(training?(this.session.error||this.session.notice||''):'');this.notice.hidden=!this.notice.textContent;
      const baselineButton=this.q('.operator-view-baseline');if(baselineButton)baselineButton.disabled=!b.valid;
      this.root.querySelectorAll('[data-workspace-step]').forEach(button=>{
        const step=button.dataset.workspaceStep;button.setAttribute('aria-current',step===current?'step':'false');
        button.disabled=(step==='training'&&!training)||(step==='results'&&phase!=='completed')||(step==='baseline'&&!b.valid&&current!=='baseline');
        const li=button.closest('li');
        li?.classList.toggle('active',step===current);
        const complete=(step==='fit'&&['baseline','training','results'].includes(current))||(step==='baseline'&&b.valid)||(step==='training'&&phase==='completed');
        li?.classList.toggle('completed',complete&&step!==current);
        const marker=button.querySelector('b');if(marker)marker.textContent=complete&&step!==current?'✓':String(['fit','baseline','training','results'].indexOf(step)+1);
      });
      const guideEnabled=['idle','connecting','connected','ready','closed_complete','completed'].includes(phase);
      const guideControls=this.q('.operator-guide-controls'),showGuide=guideEnabled&&!['completed','closed_complete'].includes(phase)&&(phase!=='ready'||this.showWearControls);if(guideControls)guideControls.hidden=!showGuide;
      const confirmation=this.q('.operator-wear-confirmation');if(confirmation)confirmation.hidden=!showGuide;
      const wear=context.presentation?.wear_confirmation||s?.presentation?.wear_confirmation||{};
      this.root.querySelectorAll('[data-wear-confirmation]').forEach(row=>{const value=wear[row.dataset.wearConfirmation],confirmed=value===true||value?.confirmed===true;row.classList.toggle('is-confirmed',confirmed);row.querySelector('strong').textContent=confirmed?'被试已确认':'待确认';});
      this.root.querySelectorAll('[data-guide-step]').forEach(button=>button.disabled=!guideEnabled||this.wearRequest);
      set('.session-live-title',phase==='paused'?'训练已暂停':'正在保存训练记录');
      set('.session-live-detail',phase==='paused'?'个人基线已保留，确认信号稳定后继续':'');
      const elapsed=s?.training?.valid_seconds||0,target=s?.training?.target_seconds;
      const total=finite(target)&&target>0?target:context.mode==='replay'?null:60;
      const progress=total?Math.min(100,elapsed/total*100):0;
      this.q('.operator-metrics').innerHTML='<div class="operator-timing-primary"><dt>有效训练</dt><dd>'+time(elapsed)+(total?' <span>/ '+time(total)+'</span>':'')+'</dd></div><div class="operator-timing-total"><dt>总耗时</dt><dd>'+ (finite(s?.training?.wall_elapsed_seconds)?time(s.training.wall_elapsed_seconds):'—')+'</dd></div>'+(total?'<div class="operator-progress-group"><div class="operator-progress" role="progressbar" aria-label="有效训练进度" aria-valuemin="0" aria-valuemax="'+total+'" aria-valuenow="'+Math.min(total,elapsed)+'"><i style="width:'+progress+'%"></i></div><strong>'+Math.round(progress)+'%</strong></div>':'');
      const key=[this.points.length,this.points.at(-1)?.time,this.points.at(-1)?.valid].join('|');
      if(key!==this.chartKey){this.chartKey=key;this.q('.operator-live-chart').innerHTML=window.ResonanceSessionCharts.scoreChart(this.points,{title:'专注与放松实时曲线'})+'<div class="operator-chart-caption">'+legend+'<span>本窗口实时记录 · 中断处留空</span></div>';}
      this.drawPreview();
    }
    drawPreview() {
      const canvas=this.q('.operator-preview canvas');if(!canvas)return;
      const frame=this.previewFrame,fresh=frame&&performance.now()-frame.received<4000&&this.acceptsPreviewIdentity(frame);
      const rect=canvas.getBoundingClientRect();if(!rect.width)return;
      const dpr=Math.min(devicePixelRatio||1,1.5),pixelWidth=Math.round(rect.width*dpr),pixelHeight=Math.round(rect.height*dpr);
      const stamp=[fresh?frame.received:0,pixelWidth,pixelHeight].join('|');
      if(this.previewStamp===stamp)return;this.previewStamp=stamp;
      canvas.width=pixelWidth;canvas.height=pixelHeight;
      const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
      ctx.fillStyle='#11151b';ctx.fillRect(0,0,rect.width,rect.height);
      if(fresh){const scale=Math.min(rect.width/frame.image.naturalWidth,rect.height/frame.image.naturalHeight),width=frame.image.naturalWidth*scale,height=frame.image.naturalHeight*scale;ctx.drawImage(frame.image,(rect.width-width)/2,(rect.height-height)/2,width,height);}
      else{ctx.fillStyle='#a2adbc';ctx.font='500 13px "Segoe UI", "Microsoft YaHei", sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('等待被试画面',rect.width/2,rect.height/2);}
      canvas.setAttribute('aria-label',fresh?'被试端当前画面同步预览':'等待被试画面');
    }
    dispose(){this.disposed=true;clearInterval(this.previewTimer);this.previewRequest++;this.channel?.close();this.dialog.remove();}
  }
  window.ResonanceOperatorWorkspace=ResonanceOperatorWorkspace;
})();
