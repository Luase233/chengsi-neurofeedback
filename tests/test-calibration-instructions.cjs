'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {CalibrationCuePlayer} = require('../calibration-cues.js');
const settle = () => new Promise(resolve=>setImmediate(resolve));
const snapshot = (phase,seq,session_id='voice-test')=>({phase,seq,session_id,mode:'synthetic',calibration:{valid:phase==='ready'}});

function fixture(loader) {
  const ctx = {state:'suspended',currentTime:0,sampleRate:48000,destination:{},sources:[],
    resume(){this.state='running';return Promise.resolve();}, close(){this.state='closed';return Promise.resolve();},
    createBuffer(channels,length,rate){const data=new Float32Array(length);return {duration:length/rate,getChannelData:()=>data};},
    createBufferSource(){const source={connect(){},disconnect(){},start(at){this.at=at;},stop(){this.stopped=true;}};this.sources.push(source);return source;}};
  const player = new CalibrationCuePlayer({contextFactory:()=>ctx,
    instructionLoader:loader||((id,context)=>Object.assign(context.createBuffer(1,48000,48000),{instructionId:id}))});
  const active = ()=>[...player.sources];
  async function finish() {
    const source=active()[0];assert.ok(source,'Expected an active cue');
    ctx.currentTime=source.at+source.buffer.duration;source.onended();await settle();return source;
  }
  return {ctx,player,active,finish};
}

test('voice first, unchanged two-note cue second, true only after its complete tail',async()=>{
  const {player,active,finish}=fixture();
  assert.equal(await player.playInstruction('closed'),false);
  await player.unlock();let resolved=false;
  const completed=player.playInstruction('closed').then(ok=>{resolved=true;return ok;});
  await settle();assert.equal(active()[0].buffer.instructionId,'closed-start');assert.equal(resolved,false);
  const voice=await finish();assert.equal(active().length,1);assert.equal(active()[0].buffer.duration,1.9);
  assert.ok(active()[0].at>=voice.at+voice.buffer.duration+.074);assert.equal(resolved,false);
  await finish();assert.equal(await completed,true);
  assert.deepEqual(player.diagnostics().ended,{start:1,complete:0});player.dispose();
});

test('pure tone instruction also waits for natural completion',async()=>{
  const {player,active,finish}=fixture();await player.unlock();
  const completed=player.playInstruction('open','tone');await settle();
  assert.equal(active()[0].buffer.instructionId,undefined);assert.equal(active()[0].buffer.duration,1.9);
  await finish();assert.equal(await completed,true);assert.deepEqual(player.diagnostics().instructionPlayed,{});player.dispose();
});

test('suppressed calibration start cannot duplicate instruction cue; completion order is cue then voice',async()=>{
  const {player,active,finish}=fixture();await player.unlock();
  player.observe(snapshot('connected',1),{suppressStart:true,cueMode:'voice'});
  player.observe(snapshot('preparing_closed',2),{suppressStart:true,cueMode:'voice'});
  const start=player.playInstruction('closed');await settle();await finish();await finish();assert.equal(await start,true);
  player.observe(snapshot('calibrating_closed',3),{suppressStart:true,cueMode:'voice'});
  assert.equal(player.diagnostics().played.start,1);
  player.observe(snapshot('closed_complete',4),{suppressStart:true,cueMode:'voice'});await settle();
  assert.equal(active()[0].buffer.duration,2.14);
  await finish();assert.equal(active()[0].buffer.instructionId,'closed-complete');
  player.observe(snapshot('closed_complete',5),{suppressStart:true,cueMode:'voice'});
  await finish();assert.equal(player.diagnostics().instructionEnded['closed-complete'],1);
  assert.equal(player.diagnostics().played.complete,1);player.dispose();
});

test('next stage waits for the previous completion voice instead of cutting it off',async()=>{
  const {player,active,finish}=fixture();await player.unlock();
  player.observe(snapshot('calibrating_closed',1),{suppressStart:true,cueMode:'voice'});
  player.observe(snapshot('closed_complete',2),{suppressStart:true,cueMode:'voice'});await settle();await finish();
  const closing=active()[0];assert.equal(closing.buffer.instructionId,'closed-complete');
  player.observe(snapshot('preparing_open',3),{suppressStart:true,cueMode:'voice'});
  const opening=player.playInstruction('open');await settle();assert.equal(active()[0],closing);assert.equal(active().length,1);
  await finish();assert.equal(active()[0].buffer.instructionId,'open-start');
  await finish();await finish();assert.equal(await opening,true);player.dispose();
});

test('cancel resolves false during either voice or tone, with no fake ended counter',async()=>{
  for(const duringTone of [false,true]) {
    const {player,active,finish}=fixture();await player.unlock();
    const done=player.playInstruction('closed');await settle();if(duringTone)await finish();
    const source=active()[0];player.cancel();assert.equal(await done,false);assert.equal(source.stopped,true);
    source.onended();assert.equal(player.diagnostics().ended.start,0);assert.equal(active().length,0);
    assert.equal(player.diagnostics().instructionBusy,false);player.dispose();
  }
});

test('cancel while loading cannot play stale speech or block a new tone request',async()=>{
  let release;const waiting=new Promise(resolve=>{release=resolve;});
  const {player,ctx,active,finish}=fixture(async()=>waiting);await player.unlock();
  const old=player.playInstruction('closed');await settle();player.cancel();assert.equal(await old,false);
  const next=player.playInstruction('open','tone');await settle();assert.equal(active().length,1);
  release(ctx.createBuffer(1,48000,48000));await settle();assert.equal(active().length,1);
  await finish();assert.equal(await next,true);assert.equal(player.diagnostics().instructionPlayed['closed-start'],undefined);player.dispose();
});

test('context interruption and load failure return false, never a prepared-stage acknowledgement',async()=>{
  const {player,ctx,active}=fixture();await player.unlock();const done=player.playInstruction('closed');await settle();
  const source=active()[0];ctx.state='suspended';player.onStateChange();assert.equal(await done,false);assert.equal(source.stopped,true);player.dispose();
  const broken=fixture(async()=>{throw new Error('missing WAV');});await broken.player.unlock();
  assert.equal(await broken.player.playInstruction('open'),false);assert.equal(broken.active().length,0);
  assert.match(broken.player.diagnostics().instructionError,/missing WAV/);broken.player.dispose();
});

test('switching sessions stops old queued completion speech; loading ready does not replay it',async()=>{
  const {player,active}=fixture();await player.unlock();
  player.observe(snapshot('calibrating_open',1),{suppressStart:true,cueMode:'voice'});
  player.observe(snapshot('ready',2),{suppressStart:true,cueMode:'voice'});await settle();assert.equal(active().length,1);
  player.observe(snapshot('ready',1,'different-session'),{suppressStart:true,cueMode:'voice'});await settle();
  assert.equal(active().length,0);assert.equal(player.diagnostics().instructionBusy,false);
  assert.equal(player.diagnostics().instructionPlayed['open-complete'],undefined);player.dispose();
});

test('all Alibaba voice assets match hashes, padding and the exact demonstrated earcons',()=>{
  const directory=path.resolve(__dirname,'../assets/audio/instructions');
  const metadata=JSON.parse(fs.readFileSync(path.join(directory,'metadata.json'),'utf8'));
  assert.equal(metadata.engine,'Alibaba DashScope TTS');assert.equal(metadata.voice,'Cherry');assert.equal(metadata.instructions.length,9);
  assert.equal(metadata.instructions.find(item=>item.id==='training-start').containsDemonstration,false);
  assert.doesNotMatch(metadata.instructions.find(item=>item.id==='training-wait').text,/两分钟/);
  const start=fs.readFileSync(path.join(directory,'cues/start.wav')).subarray(44);
  const complete=fs.readFileSync(path.join(directory,'cues/complete.wav')).subarray(44);
  for(const voice of metadata.instructions) {
    const buffer=fs.readFileSync(path.join(directory,voice.file));
    assert.equal(crypto.createHash('sha256').update(buffer).digest('hex'),voice.sha256);
    assert.equal(buffer.toString('ascii',0,4),'RIFF');assert.equal(buffer.readUInt32LE(24),24000);
    const count=buffer.readUInt32LE(40)/2;let peak=0;
    for(let i=0;i<count;i++) {const value=buffer.readInt16LE(44+i*2);peak=Math.max(peak,Math.abs(value));
      if(i<24000*.45||i>=count-24000*.25)assert.equal(value,0);}
    assert.ok(peak/32768<=.28002&&peak/32768>.2799);
    assert.equal(count/24000,voice.duration);
    if (voice.containsDemonstration) {
      const pcm=buffer.subarray(44),startAt=pcm.indexOf(start),completeAt=pcm.indexOf(complete);
      assert.ok(startAt>=24000*.45*2,'spoken instruction precedes the start example');
      assert.ok(completeAt>startAt+start.length,'spoken action separates the two complete examples');
      assert.ok(pcm.length>completeAt+complete.length+24000*.25*2,'spoken action follows the completion example');
      assert.match(voice.text,/当听到/);assert.doesNotMatch(voice.text,/听到.*提示音/);
    }
  }
});

test('training preparation keeps output alive and acknowledges only after speech and full start earcon',async()=>{
  const {player,active,finish}=fixture();await player.unlock();
  player.observe(snapshot('ready',1),{suppressStart:true,cueMode:'voice'});
  player.observe(snapshot('preparing_training',2),{suppressStart:true,cueMode:'voice'});
  assert.equal(player.diagnostics().outputHeld,true);
  let resolved=false;
  const done=player.playInstruction('training').then(ok=>{resolved=true;return ok;});
  await settle();assert.equal(active()[0].buffer.instructionId,'training-start');assert.equal(resolved,false);
  await finish();assert.equal(active()[0].buffer.duration,1.9);assert.equal(resolved,false);
  await finish();assert.equal(await done,true);
  player.observe(snapshot('training',3),{suppressStart:true,cueMode:'voice'});
  assert.equal(player.diagnostics().played.start,1);player.dispose();
});

test('training completion uses a full earcon then the appropriate rest or daily voice once',async()=>{
  for(const final of [false,true]) {
    const {player,active,finish}=fixture();await player.unlock();
    const state=(phase,seq,count)=>({...snapshot(phase,seq),protocol:{completed_round_count:count,
      last_round_summary:{reason:'training_valid_target_reached'}},summary:{reason:'training_valid_target_reached'}});
    player.observe(state('training',1,0),{cueMode:'voice'});
    player.observe(state(final?'completed':'resting',2,1),{cueMode:'voice'});await settle();
    assert.equal(active()[0].buffer.duration,2.14);await finish();
    const id=final?'training-day-complete':'training-round-complete';
    assert.equal(active()[0].buffer.instructionId,id);await finish();
    player.observe(state(final?'completed':'resting',3,1),{cueMode:'voice'});await settle();
    assert.equal(player.diagnostics().instructionEnded[id],1);
    assert.equal(player.diagnostics().played.complete,1);assert.equal(active().length,0);player.dispose();
  }
});

test('training guidance interrupted before the final earcon cannot acknowledge a start',async()=>{
  const {player,ctx,active,finish}=fixture();await player.unlock();
  const done=player.playInstruction('training');await settle();await finish();
  const source=active()[0];ctx.state='suspended';player.onStateChange();
  assert.equal(await done,false);assert.equal(source.stopped,true);
  assert.equal(player.diagnostics().ended.start,0);player.dispose();
});

test('neutral recovery uses no success tone, waits for its tail, and supports cancellation',async()=>{
  const {player,active,finish}=fixture();await player.unlock();
  assert.equal(await player.playRecovery('tone'),false);assert.equal(active().length,0);
  const done=player.playRecovery('voice');await settle();assert.equal(active()[0].buffer.instructionId,'recovery');
  await finish();assert.equal(await done,true);assert.deepEqual(player.diagnostics().played,{start:0,complete:0});
  const cancelled=player.playRecovery('voice');await settle();player.cancel();assert.equal(await cancelled,false);player.dispose();
});

test('voice completion arriving while suspended recovers once and preserves full sequence',async()=>{
  const {player,ctx,active,finish}=fixture();await player.unlock();
  player.observe(snapshot('calibrating_closed',1),{suppressStart:true,cueMode:'voice'});
  let resume;ctx.state='suspended';ctx.resume=()=>new Promise(resolve=>{resume=()=>{ctx.state='running';resolve();};});
  player.onStateChange();player.observe(snapshot('closed_complete',2),{suppressStart:true,cueMode:'voice'});
  await settle();assert.equal(active().length,0);resume();await new Promise(resolve=>setTimeout(resolve,65));
  assert.equal(active().length,1);assert.equal(active()[0].buffer.duration,2.14);
  await finish();assert.equal(active()[0].buffer.instructionId,'closed-complete');await finish();
  player.observe(snapshot('closed_complete',3),{suppressStart:true,cueMode:'voice'});await settle();
  assert.equal(player.diagnostics().played.complete,1);assert.equal(player.diagnostics().instructionEnded['closed-complete'],1);
  assert.equal(player.diagnostics().completionFailure,null);player.dispose();
});

test('an interrupted completion source resumes in place without restarting already played sound',async()=>{
  const {player,ctx,active,finish}=fixture();await player.unlock();
  player.observe(snapshot('calibrating_open',1),{suppressStart:true,cueMode:'voice'});
  player.observe(snapshot('ready',2),{suppressStart:true,cueMode:'voice'});await settle();
  const original=active()[0];let resume;ctx.state='suspended';ctx.resume=()=>new Promise(resolve=>{resume=()=>{ctx.state='running';resolve();};});
  player.onStateChange();assert.equal(original.stopped,undefined);assert.equal(active()[0],original);
  resume();await new Promise(resolve=>setTimeout(resolve,65));assert.equal(active()[0],original);
  await finish();await finish();assert.equal(player.diagnostics().played.complete,1);
  assert.equal(player.diagnostics().instructionEnded['open-complete'],1);
  assert.equal(active()[0].buffer.instructionId,'training-wait');
  assert.equal(player.diagnostics().cueBusy,true);
  await finish();assert.equal(player.diagnostics().instructionEnded['training-wait'],1);
  assert.equal(player.diagnostics().cueBusy,false);player.dispose();
});

test('reused baseline waiting guidance shares the sequence queue and remains busy through its full tail',async()=>{
  const {player,active,finish}=fixture();await player.unlock();
  player.observe(snapshot('ready',1),{suppressStart:true,cueMode:'voice'});
  assert.equal(await player.playWaiting('tone'),true);assert.equal(active().length,0);
  let resolved=false;
  const done=player.playWaiting('voice',{sessionId:'voice-test'}).then(ok=>{resolved=true;return ok;});
  await settle();assert.equal(active()[0].buffer.instructionId,'training-wait');
  assert.equal(resolved,false);assert.equal(player.diagnostics().cueBusy,true);
  await finish();assert.equal(await done,true);assert.equal(player.diagnostics().cueBusy,false);
  assert.equal(player.diagnostics().instructionEnded['training-wait'],1);player.dispose();
});

test('completion recovery expires visibly and a new request clears failure before playback admission',async()=>{
  const {player,ctx,active}=fixture();let now=0;player.now=()=>now;await player.unlock();
  player.observe(snapshot('calibrating_closed',1),{suppressStart:true,cueMode:'voice'});
  ctx.state='suspended';ctx.resume=()=>new Promise(()=>{});
  player.observe(snapshot('closed_complete',2),{suppressStart:true,cueMode:'voice'});await settle();
  now=4000;await new Promise(resolve=>setTimeout(resolve,65));
  assert.equal(active().length,0);assert.equal(player.diagnostics().completionFailure.stage,'closed');
  assert.equal(player.diagnostics().instructionBusy,false);
  player.observe({...snapshot('preparing_closed',3),presentation:{cue_request:{id:'retry-1'}}},{suppressStart:true,cueMode:'voice'});
  assert.equal(player.diagnostics().completionFailure,null);player.dispose();
});

test('a cancelled queued start cannot let its replacement overtake a recovering completion',async()=>{
  const {player,ctx,active,finish}=fixture();await player.unlock();
  player.observe(snapshot('calibrating_closed',1),{suppressStart:true,cueMode:'voice'});
  player.observe(snapshot('closed_complete',2),{suppressStart:true,cueMode:'voice'});await settle();
  const oldStart=player.playInstruction('open');await settle();
  let resume;ctx.state='suspended';ctx.resume=()=>new Promise(resolve=>{resume=()=>{ctx.state='running';resolve();};});
  player.onStateChange();assert.equal(await oldStart,false);
  resume();await new Promise(resolve=>setTimeout(resolve,65));
  const nextStart=player.playInstruction('open','tone');await settle();assert.equal(active().length,1);
  await finish();assert.equal(active()[0].buffer.instructionId,'closed-complete');
  await finish();assert.equal(active()[0].buffer.duration,1.9);
  await finish();assert.equal(await nextStart,true);player.dispose();
});
