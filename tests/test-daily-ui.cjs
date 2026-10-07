'use strict';
// Pure Node contract checks: no browser, device, audio output or session writes.
const assert=require('node:assert/strict'), fs=require('node:fs'),vm=require('node:vm');
const test=require('node:test');
const events=[];
const sandbox={window:{CalibrationCues:{unlock:async()=>true,playInstruction:async()=>{events.push('cue-ended');return true;}},ResonanceAudio:{isLayeredProgram:()=>true}},console,performance:{now:()=>1000},Date,crypto:{randomUUID:()=> 'test-command'}};
sandbox.ResonanceAudio=sandbox.window.ResonanceAudio;
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(require.resolve('../session-client.js'),'utf8'),sandbox);
const Class=sandbox.window.ResonanceSession;
function node(){const children=new Map();return {innerHTML:'',textContent:'',dataset:{},style:{},classList:{toggle(){}},setAttribute(){},removeAttribute(){},querySelector(selector){if(!children.has(selector))children.set(selector,node());return children.get(selector);},querySelectorAll(){return [];}};}
function fixture(){
  const root=node(),s=Object.assign(Object.create(Class.prototype),{root,q:selector=>root.querySelector(selector),isOperator:false,mode:'synthetic',online:true,busy:false,form:{participant:'test',training:'60',protocol:'daily'},sessions:[],devices:[],history:[],dailyHistory:[],historyStatus:'ready',historyGroup:'',resultTab:'training',resultRound:0,receivedAt:1000,getProgram:()=> 'clear-current-v04',emit(){},workspace:null});
  const summary={reason:'training_valid_target_reached',mean_score:65,mean_meditation:45,valid_seconds:60,wall_elapsed_seconds:65,peak:{peak_value:80,threshold:75,total_peak_duration:3,longest_peak_duration:2},score_trajectory:[],calibration_trajectory:[]};
  const state={session_id:'test',mode:'synthetic',phase:'resting',sent_at:new Date().toISOString(),participant_id:'test',training:{valid_seconds:60,target_seconds:60,wall_elapsed_seconds:65},calibration:{valid:true,baseline_id:'baseline',parameters:{}},quality:{valid:true},presentation:{dual_screen:false},protocol:{plan:'daily',round_number:2,completed_round_count:1,rounds_target:4,rest_remaining_seconds:60,daily_valid_seconds:60,completed_rounds:[{round_number:1,summary}]},summary};
  s.snapshot=state;s.sessionId=state.session_id;
  return {s,state,root,render:()=>s.render({mode:'synthetic',phase:state.phase,snapshot:state,stale:false,presentation:state.presentation})};
}
test('rest is visible and next round stays disabled until countdown ends',()=>{
  const f=fixture();f.render();const drawer=f.root.querySelector('.session-drawer');
  assert.match(drawer.innerHTML,/休息一分钟/);assert.match(drawer.innerHTML,/开始第 2 轮/);
  assert.equal(drawer.querySelector('[data-action="start_training"]').disabled,true);
  f.state.protocol.rest_remaining_seconds=0;f.render();
  assert.equal(drawer.querySelector('[data-action="start_training"]').disabled,false);
});
test('results distinguish daily weighted summary and selected individual round',()=>{
  const f=fixture();f.state.phase='completed';f.state.protocol.day_complete=true;f.state.protocol.daily_mean_score=68;
  f.state.protocol.completed_rounds.push({round_number:2,summary:{...f.state.summary,mean_score:71}});
  f.s.resultRound=2;f.render();let markup=f.root.querySelector('.session-drawer').innerHTML;
  assert.match(markup,/今日四轮训练已完成/);assert.match(markup,/平均专注 68/);assert.match(markup,/71\.0/);assert.match(markup,/第 2 轮/);assert.doesNotMatch(markup,/02:00/);
  f.s.resultTab='history';f.render();assert.match(f.root.querySelector('.session-drawer').innerHTML,/尚无同一基线下完成四轮/);
});
test('single-screen training request waits for full start cue and rejects interruption',async()=>{
  const {s}=fixture();s.form.protocol='single';s.getPresentation=()=>({cue_mode:'voice'});s.unlockAudio=async()=>events.push('audio-unlocked');
  s.request=async()=>{events.push('server-start');return s.snapshot;};s.accept=()=>{};
  events.length=0;await s.command('start_training');assert.deepEqual(events,['audio-unlocked','cue-ended','server-start']);
  sandbox.window.CalibrationCues.playInstruction=async()=>false;events.length=0;await s.command('start_training');
  assert.deepEqual(events,['audio-unlocked']);assert.match(s.error,/未完整播放/);
});
