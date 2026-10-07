/* Original calibration earcons. This separate bus never changes training music. */
(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CalibrationCues = new api.CalibrationCuePlayer();
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  'use strict';
  const VERSION = 'calibration-earcons-v1.6';
  const instructionScript = root.document?.currentScript?.src;
  const INSTRUCTION_BASE = instructionScript ? new URL('assets/audio/instructions/', instructionScript).href : 'assets/audio/instructions/';
  const INSTRUCTION_IDS = new Set(['closed-start','open-start','closed-complete','open-complete','training-wait','training-start','training-round-complete','training-day-complete','recovery']);
  // Padding preserves the whole motif, but zero-valued samples alone cannot
  // guarantee that a Bluetooth receiver stays awake between the two cues.
  const LEAD_SECONDS = 0.8;
  const TAIL_SECONDS = 0.4;
  const MAX_RECOVERY_AGE_MS = 3500;
  const OUTPUT_KEEPALIVE_DC = 1 / 32768;
  // Soft two-note invitation; a longer ascending three-note confirmation.
  // These notes and synthesized timbre are original, not an Apple sound asset.
  const SPECS = Object.freeze({
    start: { duration:0.70, notes:[[0,659.255,0.39],[0.23,783.991,0.45]] },
    complete: { duration:0.94, notes:[[0,659.255,0.40],[0.19,830.609,0.44],[0.38,987.767,0.53]] },
  });

  function samples(kind, sampleRate = 48000) {
    const spec = SPECS[kind];
    if (!spec || !Number.isFinite(sampleRate) || sampleRate < 8000) throw new TypeError('Invalid calibration cue');
    const data = new Float32Array(Math.ceil((LEAD_SECONDS + spec.duration + TAIL_SECONDS) * sampleRate));
    for (const [offset, frequency, duration] of spec.notes) {
      const start = Math.round((LEAD_SECONDS + offset) * sampleRate), count = Math.floor(duration * sampleRate);
      for (let index = 0; index < count && start + index < data.length; index++) {
        const t = index / sampleRate;
        const attack = Math.sin(Math.min(1, t / 0.012) * Math.PI / 2) ** 2;
        const release = Math.sin(Math.min(1, (duration - t) / 0.15) * Math.PI / 2) ** 2;
        const envelope = attack * release * Math.exp(-t * 5.3);
        const angle = 2 * Math.PI * frequency * t;
        const timbre = 0.79 * Math.sin(angle) + 0.16 * Math.sin(2 * angle) + 0.05 * Math.sin(3 * angle);
        data[start + index] += 0.105 * envelope * timbre;
      }
    }
    return data;
  }

  class CalibrationCueTracker {
    constructor() { this.reset(); }
    reset() { this.previous = null; this.activeStage = null; this.completed = new Set(); this.run = 0; }
    observe(snapshot) {
      if (!snapshot?.session_id || snapshot.mode === 'replay' || snapshot.mode === 'preview') { this.reset(); return []; }
      const calibration = snapshot.calibration || {};
      const current = {
        id:snapshot.session_id, seq:Number(snapshot.seq), phase:snapshot.phase,
        closed:Number(calibration.closed_seconds) || 0, open:Number(calibration.open_seconds) || 0,
        valid:calibration.valid === true, reasons:snapshot.quality?.reasons || [],
        completedRounds:Number(snapshot.protocol?.completed_round_count) || 0,
        roundReason:snapshot.protocol?.last_round_summary?.reason,
        summaryReason:snapshot.summary?.reason,
      };
      const previous = this.previous;
      if (!previous || previous.id !== current.id) {
        this.reset(); this.previous = current;
        // Loading an existing page/state is not a new calibration command.
        this.activeStage = current.phase === 'calibrating_closed' ? 'closed' : current.phase === 'calibrating_open' ? 'open' : null;
        return [];
      }
      if (Number.isFinite(current.seq) && Number.isFinite(previous.seq) && current.seq <= previous.seq) return [];
      this.previous = current;
      const cues = [];
      const stage = current.phase === 'calibrating_closed' ? 'closed' : current.phase === 'calibrating_open' ? 'open' : null;
      if (stage && previous.phase !== current.phase) {
        const explicitStart = current.reasons.includes(stage === 'closed' ? 'closed_calibration_started' : 'open_calibration_started');
        const interrupted = ['paused','disconnected','connecting','connected'].includes(previous.phase);
        const continuing = this.activeStage === stage && interrupted && !explicitStart && current[stage] >= previous[stage];
        if (!continuing) {
          if (stage === 'closed') { this.run++; this.completed.clear(); }
          // An open-stage retry is a new test even after a rejected calibration.
          if (stage === 'open') this.completed.delete('open');
          cues.push({kind:'start',stage,run:this.run});
        }
        this.activeStage = stage;
      }
      const completedStage = previous.phase === 'calibrating_closed' && current.phase === 'closed_complete' ? 'closed'
        : previous.phase === 'calibrating_open' && current.phase === 'ready' && current.valid ? 'open' : null;
      if (completedStage && this.activeStage === completedStage && !this.completed.has(completedStage)) {
        this.completed.add(completedStage);
        cues.push({kind:'complete',stage:completedStage,run:this.run});
        this.activeStage = null;
      }
      // Only a newly completed effective-time target is a successful round.
      // Initial/restored snapshots, stopped rounds and reconnects stay silent.
      if (previous.phase === 'training' && current.completedRounds > previous.completedRounds) {
        const final = current.phase === 'completed' && current.summaryReason === 'training_valid_target_reached';
        const resting = current.phase === 'resting' && current.roundReason === 'training_valid_target_reached';
        if (final || resting) cues.push({kind:'complete',stage:'training',run:this.run,
          finishedRound:current.completedRounds,final});
      }
      if (['idle','completed','error'].includes(current.phase)) this.activeStage = null;
      // A rejected open stage returns to closed_complete; it is not a success.
      if (previous.phase === 'calibrating_open' && current.phase === 'closed_complete') this.activeStage = null;
      return cues;
    }
  }

  class CalibrationCuePlayer {
    constructor({ contextFactory, now, instructionLoader } = {}) {
      this.tracker = new CalibrationCueTracker();
      this.contextFactory = contextFactory || (() => {
        const Context = root.AudioContext || root.webkitAudioContext;
        return Context ? new Context({latencyHint:'interactive'}) : null;
      });
      this.context = null; this.unlocked = false; this.disposed = false;
      this.sources = new Set(); this.buffers = new Map(); this.nextAt = 0;
      this.played = {start:0,complete:0}; this.suppressed = 0; this.lastCue = null;
      this.now = now || (() => Date.now());
      this.pending = []; this.resuming = null; this.outputHeld = false;
      this.carrier = null; this.carrierBuffer = null; this.releaseTimer = null;
      this.ended = {start:0,complete:0}; this.recoveries = 0;
      this.instructionLoader = instructionLoader || null;
      this.instructionBuffers = new Map(); this.instructionLoads = new Map();
      this.sequenceJobs = new Set(); this.sequenceTail = Promise.resolve(); this.sequenceGeneration = 0;
      this.instructionPlayed = {}; this.instructionEnded = {}; this.lastInstructionError = null;
      this.completionFailure = null;
      this.lastInstructionRequestId = null;
      this.onStateChange = () => {
        if (!this.unlocked || this.disposed) return;
        // A prepared-stage acknowledgement must never follow interrupted audio.
        // The ordinary completion earcon's existing recovery path remains below.
        if (this.context?.state !== 'running' && this.sequenceJobs.size) {
          this.cancelSequences({keepCompletions:true});
          for (const job of this.sequenceJobs) if (job.metadata.completion) this.recoverCompletion(job);
        }
        if (this.context?.state === 'running') this.flushPending();
        else if (this.outputHeld || this.pending.some(cue => this.isFresh(cue))
          || this.sources.size && ['closed_complete','ready','resting','completed'].includes(this.tracker.previous?.phase)) this.recover();
      };
    }
    unlock() {
      // Call directly inside the click event, before awaiting HTTP or other work.
      if (this.disposed) return Promise.resolve(false);
      try {
        if (!this.context) {
          this.context = this.contextFactory();
          this.context?.addEventListener?.('statechange', this.onStateChange);
        }
        if (!this.context) return Promise.resolve(false);
        const resume = this.context.state === 'running' ? Promise.resolve() : this.context.resume();
        return Promise.resolve(resume).then(() => {
          this.unlocked = !this.disposed && this.context?.state === 'running';
          if (this.unlocked) this.completionFailure = null;
          return this.unlocked;
        }).catch(() => false);
      } catch (_) { return Promise.resolve(false); }
    }
    observe(snapshot, {suppressStart = false, cueMode = 'tone'} = {}) {
      if (this.tracker.previous && (!snapshot?.session_id || this.tracker.previous.id !== snapshot.session_id)) this.cancel();
      const requestId = snapshot?.presentation?.cue_request?.id || null;
      if (requestId && requestId !== this.lastInstructionRequestId) this.completionFailure = null;
      this.lastInstructionRequestId = requestId;
      if (snapshot?.phase?.startsWith('preparing_') && this.tracker.previous?.phase !== snapshot.phase) this.completionFailure = null;
      const cues = this.tracker.observe(snapshot);
      this.outputHeld = !!this.tracker.previous
        && ['preparing_closed','preparing_open','preparing_training','calibrating_closed','calibrating_open'].includes(this.tracker.previous.phase);
      const pendingCount = this.pending.length;
      this.pending = this.pending.filter(cue => this.isFresh(cue));
      this.suppressed += pendingCount - this.pending.length;
      if (this.outputHeld && this.unlocked) {
        if (this.context?.state === 'running') this.keepOutput();
        else this.recover();
      }
      for (const cue of cues) {
        if (cue.kind === 'start' && suppressStart) continue;
        const metadata = {...cue,sessionId:snapshot.session_id};
        if (cue.kind === 'complete' && cueMode === 'voice') {
          const instruction = cue.stage === 'training'
            ? (cue.final ? 'training-day-complete' : 'training-round-complete') : cue.stage+'-complete';
          const steps = [{kind:'complete'},{instruction}];
          if (cue.stage === 'open') steps.push({instruction:'training-wait'});
          this.enqueueSequence(steps, {...metadata,completion:true});
        } else this.play(cue.kind, metadata);
      }
      this.releaseOutputWhenIdle();
      return cues;
    }
    playInstruction(stage, mode = 'voice') {
      if (!['closed','open','training'].includes(stage) || !['voice','tone'].includes(mode)) return Promise.resolve(false);
      const steps = mode === 'voice' ? [{instruction:stage+'-start'},{kind:'start'}] : [{kind:'start'}];
      return this.enqueueSequence(steps, {stage,instruction:true});
    }
    playRecovery(mode = 'voice') {
      if (mode !== 'voice') return Promise.resolve(false);
      return this.enqueueSequence([{instruction:'recovery'}], {recovery:true});
    }
    playWaiting(mode = 'voice', metadata = {}) {
      if (mode !== 'voice') return Promise.resolve(true);
      return this.enqueueSequence([{instruction:'training-wait'}], {
        ...metadata,sessionId:metadata.sessionId || this.tracker.previous?.id,
        stage:'open',completion:true,waiting:true,
      });
    }
    completionFailureMessage(metadata) {
      if (metadata.stage === 'training') return '训练结束声音未完整播放，请工作人员提醒被试休息';
      return metadata.stage === 'closed'
        ? '结束声音未完整播放，需工作人员口头提示被试睁眼'
        : '训练准备语音未完整播放，请工作人员确认后继续';
    }
    enqueueSequence(steps, metadata = {}) {
      if (!this.unlocked || this.disposed || !this.context || this.context.state === 'closed'
        || this.context.state !== 'running' && !metadata.completion) {
        if (metadata.completion) this.recordCompletionFailure(metadata, this.completionFailureMessage(metadata));
        return Promise.resolve(false);
      }
      const generation = this.sequenceGeneration;
      let resolve;
      const done = new Promise(finish => { resolve = finish; });
      const job = {generation,steps,metadata,finished:false,resolve,done};
      this.sequenceJobs.add(job);
      const previous = this.sequenceTail;
      this.sequenceTail = done;
      this.keepOutput();
      previous.catch(() => {}).then(async () => {
        try {
          for (const step of steps) {
            if (!await this.ensureSequenceActive(job)) return this.finishSequence(job, false);
            const buffer = step.instruction ? await this.loadInstruction(step.instruction) : this.cueBuffer(step.kind);
            if (!await this.ensureSequenceActive(job)) return this.finishSequence(job, false);
            const ended = await new Promise(finish => {
              const scheduled = this.scheduleBuffer(buffer, step.kind || step.instruction, metadata, finish, job, !!step.instruction);
              if (!scheduled) finish(false);
            });
            if (!ended) return this.finishSequence(job, false);
          }
          this.finishSequence(job, true);
        } catch (error) {
          this.lastInstructionError = String(error?.message || error);
          job.failureReason = this.lastInstructionError;
          this.finishSequence(job, false);
        }
      });
      return done;
    }
    sequenceActive(job) {
      return !job.finished && job.generation === this.sequenceGeneration && !this.disposed
        && this.unlocked && this.context?.state === 'running';
    }
    async ensureSequenceActive(job) {
      if (this.sequenceActive(job)) return true;
      if (!job.finished && job.generation === this.sequenceGeneration && job.metadata.completion
        && !this.disposed && this.unlocked) return this.recoverCompletion(job);
      return false;
    }
    recoverCompletion(job) {
      if (job.recoveryTask) return job.recoveryTask;
      const interruptedAt = this.now();
      const active = () => !job.finished && job.generation === this.sequenceGeneration && !this.disposed && this.unlocked;
      job.recoveryTask = (async () => {
        this.recover();
        while (active() && this.context?.state !== 'running' && this.context?.state !== 'closed'
          && this.isFresh({kind:'complete',metadata:job.metadata,requestedAt:interruptedAt})) {
          await new Promise(resolve => setTimeout(resolve, 50));
        }
        const okay = active() && this.context?.state === 'running'
          && this.isFresh({kind:'complete',metadata:job.metadata,requestedAt:interruptedAt});
        if (!okay && active()) {
          job.failureReason = this.completionFailureMessage(job.metadata);
          this.finishSequence(job, false);
          for (const source of [...this.sources]) if (source.sequenceJob === job) {
            source.cueCancelled = true;
            try { source.stop(); } catch (_) {}
            source.cueFinish?.(false);
          }
        }
        return okay;
      })().finally(() => { job.recoveryTask = null; });
      return job.recoveryTask;
    }
    recordCompletionFailure(metadata, reason) {
      if (metadata.sessionId && metadata.sessionId !== this.tracker.previous?.id) return;
      this.completionFailure = {stage:metadata.stage,sessionId:metadata.sessionId || null,reason};
    }
    finishSequence(job, successful) {
      if (job.finished) return;
      job.finished = true; this.sequenceJobs.delete(job);
      if (!successful && job.metadata.completion && !job.cancelled) this.recordCompletionFailure(job.metadata,
        job.failureReason || this.completionFailureMessage(job.metadata));
      job.resolve(Boolean(successful && job.generation === this.sequenceGeneration && !this.disposed
        && this.unlocked && this.context?.state === 'running'));
      this.releaseOutputWhenIdle();
    }
    async loadInstruction(id) {
      if (!INSTRUCTION_IDS.has(id)) throw new TypeError('Unknown calibration instruction');
      if (this.instructionBuffers.has(id)) return this.instructionBuffers.get(id);
      if (this.instructionLoads.has(id)) return this.instructionLoads.get(id).promise;
      const controller = new AbortController();
      const promise = (async () => {
        let buffer;
        if (this.instructionLoader) buffer = await this.instructionLoader(id, this.context, controller.signal);
        else {
          const response = await root.fetch(INSTRUCTION_BASE + id + '.wav?v=aliyun-guidance-v4', {signal:controller.signal,cache:'no-cache'});
          if (!response.ok) throw new Error('语音文件加载失败：HTTP ' + response.status);
          buffer = await this.context.decodeAudioData(await response.arrayBuffer());
        }
        if (controller.signal.aborted || this.disposed) throw new Error('语音播放已取消');
        if (!buffer || !Number.isFinite(buffer.duration) || buffer.duration <= 0) throw new Error('语音文件无效');
        this.instructionBuffers.set(id, buffer);
        return buffer;
      })();
      const load = {promise,controller};
      this.instructionLoads.set(id, load);
      try { return await promise; }
      finally { if (this.instructionLoads.get(id) === load) this.instructionLoads.delete(id); }
    }
    cancelSequences({keepCompletions = false} = {}) {
      if (!keepCompletions) {
        this.sequenceGeneration++;
        for (const load of this.instructionLoads.values()) load.controller.abort();
        this.instructionLoads.clear();
      }
      for (const job of [...this.sequenceJobs]) if (!keepCompletions || !job.metadata.completion) {
        job.cancelled = true; this.finishSequence(job, false);
      }
      for (const source of [...this.sources]) if (source.sequenceJob && (!keepCompletions || !source.sequenceJob.metadata.completion)) {
        source.cueCancelled = true;
        try { source.stop(); } catch (_) {}
        source.cueFinish?.(false);
      }
      this.sequenceTail = keepCompletions ? [...this.sequenceJobs].at(-1)?.done || Promise.resolve() : Promise.resolve();
      this.nextAt = Math.max(this.context?.currentTime || 0, ...[...this.sources].map(source => source.cueEndAt || 0));
    }
    preview(kind = 'start') {
      if (!SPECS[kind]) return Promise.resolve(false);
      const unlock = this.unlock();
      return unlock.then(ready => ready && this.play(kind, {preview:true}));
    }
    play(kind, metadata = {}) {
      const context = this.context;
      if (!SPECS[kind] || !this.unlocked || !context || context.state === 'closed' || this.disposed) {
        this.suppressed++; return false;
      }
      if (context.state !== 'running') {
        this.pending.push({kind,metadata:{...metadata},requestedAt:this.now()});
        this.recover();
        return true;
      }
      return this.schedule(kind, metadata);
    }
    isFresh(cue) {
      if (this.disposed || !this.unlocked || this.now() - cue.requestedAt > MAX_RECOVERY_AGE_MS) return false;
      if (cue.metadata.preview) return true;
      const current = this.tracker.previous;
      if (!current || current.id !== cue.metadata.sessionId) return false;
      if (cue.metadata.stage === 'training') return cue.kind === 'start'
        ? current.phase === 'training'
        : current.completedRounds === cue.metadata.finishedRound
          && current.phase === (cue.metadata.final ? 'completed' : 'resting');
      if (cue.kind === 'start') return current.phase === 'calibrating_' + cue.metadata.stage;
      return cue.metadata.stage === 'closed'
        ? ['closed_complete','preparing_open','calibrating_open'].includes(current.phase)
        : current.phase === 'ready';
    }
    recover() {
      if (this.resuming || !this.unlocked || this.disposed || !this.context || this.context.state === 'closed') return;
      try {
        this.resuming = Promise.resolve(this.context.resume()).then(() => {
          if (this.context?.state === 'running' && !this.disposed) {
            this.recoveries++;
            if (this.outputHeld || this.sources.size) this.keepOutput();
            this.flushPending();
          }
        }).catch(() => { this.suppressed += this.pending.length; this.pending = []; })
          .finally(() => { this.resuming = null; });
      } catch (_) { this.suppressed += this.pending.length; this.pending = []; }
    }
    flushPending() {
      if (this.context?.state !== 'running' || this.disposed) return;
      const waiting = this.pending.splice(0);
      for (const cue of waiting) {
        if (this.isFresh(cue)) this.schedule(cue.kind, cue.metadata);
        else this.suppressed++;
      }
      this.releaseOutputWhenIdle();
    }
    keepOutput() {
      clearTimeout(this.releaseTimer); this.releaseTimer = null;
      if (this.carrier || !this.context || this.context.state !== 'running' || this.disposed) return;
      const context = this.context;
      if (!this.carrierBuffer) {
        const length = context.sampleRate;
        this.carrierBuffer = context.createBuffer(1, length, context.sampleRate);
        const data = this.carrierBuffer.getChannelData(0);
        data.fill(OUTPUT_KEEPALIVE_DC);
      }
      // Constant one-PCM-step offset keeps the graph non-zero without adding
      // broadband noise to speech or the silent baseline. Bluetooth wake-up
      // still depends on the receiver; padded complete buffers remain necessary.
      const source = context.createBufferSource();
      source.buffer = this.carrierBuffer; source.loop = true;
      source.connect(context.destination); source.start();
      this.carrier = source;
    }
    releaseOutputWhenIdle() {
      if (this.outputHeld || this.sources.size || this.pending.length || this.sequenceJobs.size || !this.carrier || this.releaseTimer) return;
      // Release only after the full source (including decay/tail) has ended,
      // then allow an additional drain interval. Never stop a cue on UI phase change.
      this.releaseTimer = setTimeout(() => {
        this.releaseTimer = null;
        if (!this.outputHeld && !this.sources.size && !this.pending.length && !this.sequenceJobs.size) this.stopOutput();
      }, 1000);
      this.releaseTimer?.unref?.();
    }
    stopOutput() {
      clearTimeout(this.releaseTimer); this.releaseTimer = null;
      if (this.carrier) { try { this.carrier.stop(); this.carrier.disconnect(); } catch (_) {} }
      this.carrier = null;
    }
    cueBuffer(kind) {
      const context = this.context;
      let buffer = this.buffers.get(kind);
      if (!buffer) {
        const data = samples(kind, context.sampleRate);
        buffer = context.createBuffer(1, data.length, context.sampleRate);
        buffer.getChannelData(0).set(data); this.buffers.set(kind, buffer);
      }
      return buffer;
    }
    schedule(kind, metadata) {
      return this.scheduleBuffer(this.cueBuffer(kind), kind, metadata);
    }
    scheduleBuffer(buffer, kind, metadata, onEnded = null, sequenceJob = null, isInstruction = false) {
      const context = this.context;
      if (!context || context.state !== 'running' || this.disposed) return false;
      // No overlap, including a completion immediately followed by the next test.
      // Bound manual preview spam; dropped events are never replayed after unlock.
      if (metadata.preview && this.nextAt - context.currentTime > 1.5) return false;
      this.keepOutput();
      const source = context.createBufferSource(); source.buffer = buffer;
      source.connect(context.destination);
      const startAt = Math.max(context.currentTime + 0.012, this.nextAt + 0.075);
      this.nextAt = startAt + buffer.duration;
      source.sequenceJob = sequenceJob; source.cueEndAt = this.nextAt;
      this.sources.add(source);
      let finished = false;
      source.cueFinish = natural => {
        if (finished) return;
        finished = true;
        source.disconnect(); this.sources.delete(source);
        const success = natural && !source.cueCancelled && !this.disposed;
        if (success) {
          if (isInstruction) this.instructionEnded[kind] = (this.instructionEnded[kind] || 0) + 1;
          else this.ended[kind]++;
        }
        onEnded?.(success);
        this.releaseOutputWhenIdle();
      };
      source.onended = () => source.cueFinish(true);
      try { source.start(startAt); }
      catch (error) { source.cueCancelled = true; source.cueFinish(false); throw error; }
      if (isInstruction) this.instructionPlayed[kind] = (this.instructionPlayed[kind] || 0) + 1;
      else this.played[kind]++;
      this.lastCue = {kind,...metadata,startAt,duration:buffer.duration};
      return true;
    }
    diagnostics() {
      return {version:VERSION,unlocked:this.unlocked,state:this.context?.state || 'not-created',
        played:{...this.played},ended:{...this.ended},suppressed:this.suppressed,lastCue:this.lastCue ? {...this.lastCue} : null,
        pending:this.pending.length,recoveries:this.recoveries,outputHeld:this.outputHeld,outputWarm:!!this.carrier,
        cueBusy:this.sequenceJobs.size > 0 || this.sources.size > 0 || this.pending.length > 0,
        instructionBusy:this.sequenceJobs.size > 0,instructionPlayed:{...this.instructionPlayed},instructionEnded:{...this.instructionEnded},
        instructionError:this.lastInstructionError,
        completionFailure:this.completionFailure ? {...this.completionFailure} : null,
        durations:{start:SPECS.start.duration,complete:SPECS.complete.duration},
        outputPadding:{leadSeconds:LEAD_SECONDS,tailSeconds:TAIL_SECONDS},disposed:this.disposed};
    }
    cancel() {
      // Explicit user exit/mute only, never a normal calibration phase change.
      this.cancelSequences();
      this.suppressed += this.pending.length; this.pending = []; this.outputHeld = false;
      for (const source of this.sources) {
        source.cueCancelled = true;
        try { source.stop(); source.disconnect(); } catch (_) {}
        source.cueFinish?.(false);
      }
      this.sources.clear(); this.stopOutput(); this.tracker.reset();
      this.completionFailure = null;
      this.lastInstructionRequestId = null;
      this.nextAt = this.context?.currentTime || 0;
    }
    dispose() {
      this.disposed = true; this.unlocked = false;
      this.cancel();
      this.buffers.clear(); this.instructionBuffers.clear(); this.carrierBuffer = null;
      this.context?.removeEventListener?.('statechange', this.onStateChange);
      if (this.context && this.context.state !== 'closed') this.context.close().catch(() => {});
    }
  }
  return {VERSION,CalibrationCueTracker,CalibrationCuePlayer,samples};
});
