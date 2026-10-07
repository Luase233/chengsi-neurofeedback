'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {CalibrationCueTracker,CalibrationCuePlayer,samples} = require('../calibration-cues.js');
function snapshot(phase, seq, extra = {}) {
  return {session_id:'cue-test',mode:'synthetic',phase,seq,
    calibration:{closed_seconds:0,open_seconds:0,valid:phase === 'ready'},...extra};
}
function kinds(cues) { return cues.map(cue => cue.kind + ':' + cue.stage); }

test('two server-confirmed calibration stages each receive one start and one completion', () => {
  const tracker = new CalibrationCueTracker();
  assert.deepEqual(tracker.observe(snapshot('connected',1)), []);
  assert.deepEqual(kinds(tracker.observe(snapshot('calibrating_closed',2))), ['start:closed']);
  assert.deepEqual(tracker.observe(snapshot('calibrating_closed',3)), []);
  assert.deepEqual(kinds(tracker.observe(snapshot('closed_complete',4))), ['complete:closed']);
  assert.deepEqual(kinds(tracker.observe(snapshot('calibrating_open',5))), ['start:open']);
  assert.deepEqual(kinds(tracker.observe(snapshot('ready',6))), ['complete:open']);
  assert.deepEqual(tracker.observe(snapshot('ready',7)), []);
});

test('existing ready state, replay, duplicate and old socket snapshots do not sound', () => {
  const tracker = new CalibrationCueTracker();
  assert.deepEqual(tracker.observe(snapshot('ready',10)), []);
  assert.deepEqual(tracker.observe(snapshot('calibrating_open',9)), []);
  assert.deepEqual(tracker.observe(snapshot('calibrating_open',10)), []);
  assert.deepEqual(tracker.observe(snapshot('ready',11)), []);
  assert.deepEqual(tracker.observe(snapshot('ready',1,{session_id:'new'})), []);
  assert.deepEqual(tracker.observe(snapshot('calibrating_closed',2,{mode:'replay'})), []);
});

test('pause, manual resume and reconnect do not pretend to start or complete a stage', () => {
  const tracker = new CalibrationCueTracker();
  tracker.observe(snapshot('connected',1)); tracker.observe(snapshot('calibrating_closed',2));
  for (const [index,phase] of ['paused','calibrating_closed','disconnected','connecting','connected','calibrating_closed'].entries()) {
    assert.deepEqual(tracker.observe(snapshot(phase,index+3)), [], phase);
  }
  assert.deepEqual(kinds(tracker.observe(snapshot('closed_complete',9))), ['complete:closed']);
});

test('cancellation and errors have no completion sound; rejected open stage can retry', () => {
  for (const phase of ['completed','error','disconnected','paused']) {
    const tracker = new CalibrationCueTracker();
    tracker.observe(snapshot('calibrating_closed',1));
    assert.deepEqual(tracker.observe(snapshot(phase,2)), []);
  }
  const tracker = new CalibrationCueTracker();
  tracker.observe(snapshot('closed_complete',1));
  assert.deepEqual(kinds(tracker.observe(snapshot('calibrating_open',2))), ['start:open']);
  assert.deepEqual(tracker.observe(snapshot('closed_complete',3,{error:'calibration rejected'})), []);
  assert.deepEqual(kinds(tracker.observe(snapshot('calibrating_open',4))), ['start:open']);
  assert.deepEqual(kinds(tracker.observe(snapshot('ready',5))), ['complete:open']);
});

test('intentional recalibration re-arms both cues, including a zero-second paused restart', () => {
  const tracker = new CalibrationCueTracker();
  tracker.observe(snapshot('ready',1));
  assert.deepEqual(kinds(tracker.observe(snapshot('calibrating_closed',2))), ['start:closed']);
  tracker.observe(snapshot('paused',3));
  assert.deepEqual(kinds(tracker.observe(snapshot('calibrating_closed',4,{quality:{reasons:['closed_calibration_started']}}))), ['start:closed']);
  assert.deepEqual(kinds(tracker.observe(snapshot('closed_complete',5))), ['complete:closed']);
});

test('only newly achieved training rounds sound, including one distinct daily completion', () => {
  const tracker = new CalibrationCueTracker();
  const round = (phase,seq,count,reason='training_valid_target_reached') => snapshot(phase,seq,{
    protocol:{completed_round_count:count,last_round_summary:{reason}},summary:{reason},
  });
  assert.deepEqual(tracker.observe(round('preparing_training',1,0)), []);
  assert.deepEqual(tracker.observe(round('training',2,0)), []);
  const first = tracker.observe(round('resting',3,1));
  assert.deepEqual(kinds(first), ['complete:training']);
  assert.equal(first[0].finishedRound,1);assert.equal(first[0].final,false);
  assert.deepEqual(tracker.observe(round('resting',4,1)), []);
  tracker.observe(round('training',5,1));
  const last=tracker.observe(round('completed',6,2));
  assert.equal(last[0].final,true);assert.equal(last[0].finishedRound,2);
  assert.deepEqual(tracker.observe(round('completed',7,2)), []);
  for(const phase of ['resting','completed']) {
    const refreshed=new CalibrationCueTracker();
    assert.deepEqual(refreshed.observe(round(phase,10,2)), []);
    assert.deepEqual(refreshed.observe(round(phase,11,2)), []);
  }
  for(const phase of ['completed','disconnected','paused']) {
    const stopped=new CalibrationCueTracker();stopped.observe(round('training',1,0));
    assert.deepEqual(stopped.observe(round(phase,2,1,'stopped_by_user')), []);
  }
});

test('original sounds are short, click-free at boundaries and have ample clipping headroom', () => {
  for (const rate of [44100,48000]) for (const kind of ['start','complete']) {
    const data = samples(kind,rate);
    assert.ok(data.length / rate <= 2.141);
    assert.ok(data.slice(0, Math.floor(0.8*rate)).every(value=>value===0), 'Bluetooth output lead-in');
    assert.ok(data.slice(-Math.floor(0.4*rate)).every(value=>value===0), 'Full decay and output drain tail');
    assert.equal(data[0],0); assert.equal(data[data.length - 1],0);
    let maximum = 0, energy = 0, delta = 0;
    for (let index = 0; index < data.length; index++) {
      assert.ok(Number.isFinite(data[index]));
      maximum = Math.max(maximum,Math.abs(data[index])); energy += data[index] ** 2;
      if (index) delta = Math.max(delta,Math.abs(data[index]-data[index-1]));
    }
    assert.ok(maximum > 0.04 && maximum < 0.2, String(maximum));
    assert.ok(energy > 0); assert.ok(delta < 0.04, String(delta));
  }
});

function fakeContext() {
  return {state:'suspended',currentTime:0,sampleRate:48000,destination:{},sources:[],resumes:0,
    resume() {this.resumes++;this.state='running';return Promise.resolve();},
    close() {this.state='closed';return Promise.resolve();},
    createBuffer(channels,length,rate) {const data = new Float32Array(length);return {duration:length/rate,getChannelData:() => data};},
    createBufferSource() {const source = {connect(){},disconnect(){},start(at){this.at=at;},stop(){this.stopped=true;}};this.sources.push(source);return source;},
  };
}

test('unlock starts synchronously in gesture; consecutive cues serialize with no overlap', async () => {
  const context = fakeContext(); const player = new CalibrationCuePlayer({contextFactory:() => context});
  const unlocked = player.unlock(); assert.equal(context.resumes,1); await unlocked;
  player.observe(snapshot('connected',1)); player.observe(snapshot('calibrating_closed',2));
  player.observe(snapshot('closed_complete',3)); player.observe(snapshot('calibrating_open',4));
  const cues = context.sources.filter(source => !source.loop);
  assert.equal(cues.length,3);
  for (let index = 1; index < cues.length; index++) {
    assert.ok(cues[index].at > cues[index-1].at + cues[index-1].buffer.duration);
  }
  assert.deepEqual(player.diagnostics().played,{start:2,complete:1});
  player.dispose(); assert.equal(context.state,'closed'); assert.ok(context.sources.every(source => source.stopped));
});

test('blocked audio never queues stale success sounds; preview is independent of calibration state', async () => {
  const context = fakeContext(); const player = new CalibrationCuePlayer({contextFactory:() => context});
  player.observe(snapshot('connected',1)); player.observe(snapshot('calibrating_closed',2));
  assert.equal(player.diagnostics().suppressed,1);
  await player.unlock(); player.observe(snapshot('calibrating_closed',3));
  assert.equal(context.sources.filter(source=>!source.loop).length,0);
  assert.equal(await player.preview('complete'),true);
  assert.deepEqual(player.diagnostics().played,{start:0,complete:1});
  assert.equal(await player.preview('unrecognized'),false);
  player.dispose();
});

test('the non-zero output path remains alive throughout a minute-long pretest and the entire ending cue', async () => {
  const context = fakeContext(), player = new CalibrationCuePlayer({contextFactory:() => context});
  await player.unlock();
  player.observe(snapshot('connected',1)); player.observe(snapshot('calibrating_closed',2));
  const carrier = context.sources.find(source => source.loop);
  assert.ok(carrier);
  const carrierData = carrier.buffer.getChannelData(0);
  assert.ok(carrierData.some(value => value !== 0));
  assert.ok(carrierData.every(value => Math.abs(value) <= 2/32768));
  assert.ok(carrierData.every(value => value===1/32768),'keepalive cannot add audible broadband noise');
  [...player.sources][0].onended();
  context.currentTime = 60;
  player.observe(snapshot('calibrating_closed',3));
  assert.equal(player.sources.size,0); assert.equal(carrier.stopped,undefined);
  player.observe(snapshot('closed_complete',4));
  assert.equal(player.diagnostics().played.complete,1);
  assert.equal(carrier.stopped,undefined, 'Phase change must not shut down the output');
  const ending = [...player.sources][0];
  assert.ok(ending.buffer.duration > 2.13);
  ending.onended();
  assert.equal(player.diagnostics().ended.complete,1);
  assert.equal(carrier.stopped,undefined, 'Drain interval follows the complete source');
  player.dispose();
});

function deferredRecovery(context) {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  context.state = 'suspended';
  context.resume = () => promise;
  return () => { context.state = 'running'; resolve(); };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('an already unlocked interrupted context resumes and plays one complete fresh ending cue', async () => {
  const context = fakeContext(), player = new CalibrationCuePlayer({contextFactory:() => context});
  await player.unlock(); player.observe(snapshot('calibrating_open',1));
  const resume = deferredRecovery(context);
  player.observe(snapshot('ready',2));
  assert.equal(player.diagnostics().pending,1);
  assert.equal(player.diagnostics().played.complete,0);
  resume(); await settle();
  assert.equal(player.diagnostics().pending,0);
  assert.equal(player.diagnostics().played.complete,1);
  assert.ok([...player.sources][0].buffer.duration > 2.13);
  player.observe(snapshot('ready',3));
  assert.equal(player.diagnostics().played.complete,1);
  player.dispose();
});

test('expired, departed, replaced and disposed stages never replay a deferred completion', async () => {
  for (const reason of ['expired','training','completed','disconnected','replaced','cancelled','disposed']) {
    let now = 0;
    const context = fakeContext(), player = new CalibrationCuePlayer({contextFactory:() => context,now:()=>now});
    await player.unlock(); player.observe(snapshot('calibrating_open',1));
    const resume = deferredRecovery(context);
    player.observe(snapshot('ready',2));
    if (reason === 'expired') now = 4000;
    else if (reason === 'disposed') player.dispose();
    else if (reason === 'cancelled') player.cancel();
    else player.observe(snapshot(reason === 'replaced' ? 'ready' : reason,3,
      reason === 'replaced' ? {session_id:'different-session'} : {}));
    resume(); await settle();
    assert.equal(player.diagnostics().played.complete,0,reason);
    assert.equal(player.diagnostics().pending,0,reason);
    player.dispose();
  }
});

test('explicit user cancellation stops pending output without pretending the cue completed', async () => {
  const context = fakeContext(), player = new CalibrationCuePlayer({contextFactory:() => context});
  await player.unlock(); player.observe(snapshot('calibrating_open',1));
  player.observe(snapshot('ready',2));
  const source = [...player.sources][0];
  player.cancel(); source.onended();
  assert.equal(source.stopped,true);
  assert.equal(player.diagnostics().ended.complete,0);
  assert.equal(player.diagnostics().outputWarm,false);
  assert.equal(player.diagnostics().pending,0);
  assert.equal(player.sources.size,0);
  player.dispose();
});
