/* Full-length, phase-aligned three-stem playback. All gain scheduling uses AudioContext time. */
(() => {
  'use strict';
  const scriptURL = document.currentScript?.src || new URL('audio-engine.js', document.baseURI).href;
  const ASSET_BASE = new URL('assets/audio/', scriptURL);
  const clamp = (n, min = 0, max = 1) => Math.max(min, Math.min(max, n));
  const MIX_TAU = 1.05; // 95% of a change is reached in 3.15 s, after stability confirmation.
  const IDS = ['other', 'drums', 'bass'];
  const LAYERED_PROFILE = 'easy-going-continuous-v2';
  const PROGRAMS = Object.freeze(window.CHENGSI_AUDIO_PROGRAMS.map(Object.freeze));
  const isLayeredProgram = id => PROGRAMS.some(program => program.id === id && program.feedbackProfile === LAYERED_PROFILE);
  const PROGRAM_FADE_SECONDS = 3;
  const FULL_FEEDBACK_SCORE = 80;
  const crossesFullFeedback = (a, b) => (a >= FULL_FEEDBACK_SCORE) !== (b >= FULL_FEEDBACK_SCORE);

  function levelsFor(score, valid = true) {
    if (!valid) return [1 / 6, 0, 0];
    // Demo feedback reaches full participation at 80; the score scale remains 0–100.
    return [(1 + 5 * clamp((score - 30) / 30)) / 6, clamp((score - 30) / 30), clamp((score - 60) / (FULL_FEEDBACK_SCORE - 60))];
  }

  function gainsFor(levels, rms) {
    // Static stem balance; the ambience always has an audible floor. No fast AGC.
    const raw = [0.70 + 0.30 * levels[0], 0.65 * levels[1], 0.60 * levels[2]];
    const energy = Math.sqrt(raw.reduce((sum, gain, i) => sum + (gain * rms[i]) ** 2, 0));
    const compensation = Math.min(1, 0.059 / Math.max(energy, 1e-6));
    return raw.map(value => value * compensation);
  }

  function prepareLoop(buffer, meta) {
    const sr = buffer.sampleRate;
    if (meta.loopMode === 'native') {
      const start = Math.round(meta.loopStartSeconds * sr);
      const requestedEnd = Math.round(meta.loopEndSeconds * sr);
      // Browser resampling may floor the decoded length while the metadata
      // endpoint rounds up. Allow only that one-sample discrepancy.
      const end = Math.min(requestedEnd, buffer.length);
      if (![start, requestedEnd].every(Number.isFinite) || start < 0 || requestedEnd > buffer.length + 1 || end <= start) throw new Error('原生循环区间无效');
      return { buffer, loopStart: start / sr, loopEnd: end / sr };
    }
    const end = Math.min(buffer.length, Math.round(meta.loopEndSeconds * sr));
    const length = Math.round(meta.crossfadeSeconds * sr);
    const head = Math.round(meta.headStartSeconds * sr);
    const tail = end - length;
    if (![end, length, head, tail].every(Number.isFinite) || length < 2 || head < 0 || head + length >= tail) throw new Error('音频循环区间无效');
    for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
      const pcm = buffer.getChannelData(channel);
      for (let j = 0; j < length; j++) {
        const angle = (j / (length - 1)) * Math.PI / 2;
        pcm[tail + j] = pcm[tail + j] * Math.cos(angle) + pcm[head + j] * Math.sin(angle);
      }
    }
    return { buffer, loopStart: (head + length) / sr, loopEnd: end / sr };
  }

  class ResonanceAudio {
    constructor({ onStatus, programId = 'clear-current-v04' } = {}) {
      const program = PROGRAMS.find(item => item.id === programId);
      if (!program) throw new Error(`未知音频节目：${programId}`);
      this._onStatus = typeof onStatus === 'function' ? onStatus : () => {};
      this._state = { ready: false, loading: false, error: null, playing: false, audible: false,
        score: 72, levels: levelsFor(72), energy: [0, 0, 0], contextState: 'uninitialized',
        elapsed: 0, cycleDuration: programId === 'deep-learning' ? 226.65 : 0,
        programId: program.id, programTitle: program.title, switching: false };
      this._inputScore = this._acceptedScore = 72;
      this._valid = this._acceptedValid = true;
      this._allowReward = true;
      this._candidateScore = 72;
      this._candidateSince = 0;
      this._volume = 0.35;
      this._muted = false;
      this._targetLevels = levelsFor(72);
      this._rms = [0.0705543, 0.0997413, 0.0924926];
      this._targetGains = gainsFor(this._targetLevels, this._rms);
      this._actualGains = [...this._targetGains];
      this._disposed = false;
      this._ctx = null;
      this._startPromise = null;
      this._sources = [];
      this._trackGains = [];
      this._analysers = [];
      this._scratch = [];
      this._sourceStart = 0;
      this._activeGroup = null;
      this._groups = [];
      this._transition = null;
      this._desiredProgramId = programId;
      this._selectionVersion = 0;
      this._switchTask = null;
      this._switchAbort = null;
      this._pauseToken = 0;
      this._abort = new AbortController();
      this._lastTick = performance.now();
      this._previewClock = this._lastTick / 1000;
      this._previewLayers = null;
      if (program.feedbackProfile) this._resetPreviewLayers();
      this._timer = setInterval(() => this._tick(), 50);
    }

    _resetPreviewLayers() {
      if (!window.EasyGoingProfile) throw new Error('分层反馈模块尚未加载');
      this._previewLayers = new window.EasyGoingProfile({ origin: this._previewClock, initialScore: this._inputScore, valid: this._valid });
      this._applyLayerView(this._previewLayers.snapshot(this._previewClock));
    }

    _applyLayerView(view) {
      this._layerView = view;
      this._state.levels = [...view.levels];
      this._actualGains = [...view.gains];
      this._targetGains = [...view.targetGains];
    }

    async start() {
      if (this._disposed) throw new Error('音频引擎已释放');
      if (this._state.ready) { await this.resume(); return this.getState(); }
      if (this._startPromise) return this._startPromise;
      this._startPromise = this._initialize();
      try { return await this._startPromise; }
      catch (error) {
        this._state.loading = false;
        this._state.error = `音频加载失败：${error.message || error}`;
        this._notify();
        this._startPromise = null;
        throw error;
      }
    }

    async _initialize() {
      this._state.loading = true;
      this._state.error = null;
      this._notify();
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error('当前浏览器不支持 Web Audio');
      // Match the Opus assets instead of inheriting high-rate Windows device settings.
      this._ctx = this._ctx || new Context({ latencyHint: 'playback', sampleRate: 48000 });
      const ctx = this._ctx;
      // Resume within the original user gesture, before network/decode awaits.
      await ctx.resume();
      const loaded = await this._loadProgram(this._state.programId, this._abort.signal);
      if (this._disposed) throw new Error('音频引擎已释放');
      this._mix = ctx.createGain();
      this._limiter = ctx.createDynamicsCompressor();
      this._limiter.threshold.value = -8;
      this._limiter.knee.value = 5;
      this._limiter.ratio.value = 8;
      this._limiter.attack.value = 0.008;
      this._limiter.release.value = 0.35;
      this._master = ctx.createGain();
      this._master.gain.value = 0;
      this._mix.connect(this._limiter).connect(this._master).connect(ctx.destination);
      const group = this._createGroup(loaded, ctx.currentTime + 0.08, 1, loaded.demoStartSeconds ?? 0);
      this._activateGroup(group);
      this._master.gain.setTargetAtTime(this._muted ? 0 : this._volume, group.startTime, 0.55);
      this._state.ready = true;
      this._state.loading = false;
      this._state.playing = true;
      this._state.contextState = ctx.state;
      this._notify();
      return this.getState();
    }

    async _loadProgram(id, signal) {
      const program = PROGRAMS.find(item => item.id === id);
      if (!program) throw new Error(`未知音频节目：${id}`);
      const metadataURL = new URL(program.metadata, ASSET_BASE);
      const response = await fetch(metadataURL, { signal });
      if (!response.ok) throw new Error(`音频元数据 HTTP ${response.status}`);
      const meta = await response.json();
      const ids = program.feedbackProfile ? ['layer-1', 'layer-2', 'layer-3'] : IDS;
      if (program.feedbackProfile && (meta.feedbackProfile !== program.feedbackProfile || meta.loopMode !== 'native' || !(meta.beatGrid?.bpm > 0))) throw new Error('分层音频配置不完整');
      // Settle every in-flight decode before allowing the next selection to load.
      // decodeAudioData cannot be aborted; this bounds memory to current + next group.
      const settled = await Promise.allSettled(ids.map(async trackId => {
        const track = meta.tracks.find(item => item.id === trackId);
        if (!track) throw new Error(`缺少音轨 ${trackId}`);
        const fetched = await fetch(new URL(track.file, metadataURL), { signal });
        if (!fetched.ok) throw new Error(`${trackId} HTTP ${fetched.status}`);
        const encoded = await fetched.arrayBuffer();
        signal.throwIfAborted();
        return this._ctx.decodeAudioData(encoded);
      }));
      signal.throwIfAborted();
      const failed = settled.find(result => result.status === 'rejected');
      if (failed) throw failed.reason;
      const buffers = settled.map(result => result.value);
      if (this._disposed) throw new Error('音频引擎已释放');
      const shortest = Math.min(...buffers.map(buffer => buffer.duration));
      if (buffers.some(buffer => Math.abs(buffer.duration - shortest) > 0.02)) throw new Error('三轨音频长度不一致');
      const loops = buffers.map(buffer => prepareLoop(buffer, meta));
      const loopInfo = {
        firstPlayDuration: loops[0].loopEnd, loopStart: loops[0].loopStart,
        loopEnd: loops[0].loopEnd, crossfadeSeconds: meta.crossfadeSeconds,
        headStartSeconds: meta.headStartSeconds, channels: buffers.map(buffer => buffer.numberOfChannels),
        decodedSampleRate: buffers[0].sampleRate, originalDuration: meta.duration,
      };
      const rms = ids.map(trackId => meta.tracks.find(track => track.id === trackId).rms);
      if (rms.some(value => !Number.isFinite(value) || value <= 0)) throw new Error('音频响度数据无效');
      const demoStartSeconds = meta.demoStartSeconds;
      if (demoStartSeconds != null && (!Number.isFinite(demoStartSeconds) || demoStartSeconds < 0 || demoStartSeconds >= loopInfo.loopEnd)) throw new Error('节目起播位置无效');
      return { program, loops, loopInfo, rms, demoStartSeconds, beatGrid: meta.beatGrid || null };
    }

    _createGroup(loaded, startTime, initialGain, startOffset = 0) {
      const ctx = this._ctx;
      const group = { ...loaded, startTime, startOffset,
        loopInfo: { ...loaded.loopInfo, firstPlayDuration: loaded.loopInfo.loopEnd - startOffset, startOffset },
        sources: [], trackGains: [], analysers: [], scratch: [],
        programGain: ctx.createGain(), targetGains: gainsFor(levelsFor(this._inputScore, this._valid), loaded.rms), disposed: false };
      if (loaded.program.feedbackProfile) {
        group.layers = new window.EasyGoingProfile({ origin: startTime - startOffset, ...loaded.beatGrid, initialScore: this._inputScore, valid: this._valid });
        group.targetGains = group.layers.snapshot(startTime).gains;
      }
      group.programGain.gain.value = initialGain;
      group.programGain.connect(this._mix);
      loaded.loops.forEach(({ buffer, loopStart, loopEnd }, i) => {
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.loop = true;
        source.loopStart = loopStart;
        source.loopEnd = loopEnd;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0;
        const gain = ctx.createGain();
        gain.gain.value = group.targetGains[i];
        source.connect(analyser).connect(gain).connect(group.programGain);
        source.start(startTime, startOffset);
        group.sources.push(source);
        group.analysers.push(analyser);
        group.scratch.push(new Float32Array(analyser.fftSize));
        group.trackGains.push(gain);
      });
      this._groups.push(group);
      return group;
    }

    _activateGroup(group) {
      this._activeGroup = group;
      this._sources = group.sources;
      this._trackGains = group.trackGains;
      this._analysers = group.analysers;
      this._scratch = group.scratch;
      this._sourceStart = group.startTime;
      this._rms = group.rms;
      this._loopInfo = group.loopInfo;
      this._targetGains = group.targetGains;
      this._actualGains = [...group.targetGains];
      this._state.programId = group.program.id;
      this._state.programTitle = group.program.title;
      this._state.cycleDuration = group.loopInfo.loopEnd - group.loopInfo.loopStart;
      this._state.elapsed = 0;
      if (group.layers) this._applyLayerView(group.layers.snapshot(this._ctx.currentTime));
      else {
        this._layerView = null;
        this._targetLevels = levelsFor(this._inputScore, this._valid);
        this._state.levels = [...this._targetLevels];
        this._acceptedScore = this._candidateScore = this._inputScore;
        this._acceptedValid = this._candidateValid = this._valid;
      }
    }

    async selectProgram(id) {
      if (this._disposed) throw new Error('音频引擎已释放');
      const program = PROGRAMS.find(item => item.id === id);
      if (!program) throw new Error(`未知音频节目：${id}`);
      this._desiredProgramId = id;
      const version = ++this._selectionVersion;
      this._switchAbort?.abort();
      if (!this._state.ready) {
        this._state.programId = program.id;
        this._state.programTitle = program.title;
        this._state.cycleDuration = program.id === 'deep-learning' ? 226.65 : 0;
        this._state.error = null;
        if (program.feedbackProfile) this._resetPreviewLayers();
        else {
          this._layerView = null;
          this._targetLevels = levelsFor(this._inputScore, this._valid);
          this._targetGains = gainsFor(this._targetLevels, this._rms);
        }
        this._notify();
        if (this._startPromise) {
          await this._startPromise;
          if (version === this._selectionVersion && this._state.programId !== id) return this.selectProgram(id);
        }
        return this.getState();
      }
      const previous = this._switchTask;
      const task = (async () => {
        if (previous) await previous.catch(() => {});
        if (version !== this._selectionVersion || this._disposed) return this.getState();
        if (this._state.programId === id) return this.getState();
        const controller = new AbortController();
        this._switchAbort = controller;
        this._state.switching = true;
        this._state.error = null;
        this._notify();
        try {
          const loaded = await this._loadProgram(id, controller.signal);
          if (version !== this._selectionVersion || this._disposed) return this.getState();
          const old = this._activeGroup;
          const start = this._ctx.currentTime + 0.04;
          const next = this._createGroup(loaded, start, 0, loaded.demoStartSeconds ?? loaded.loops[0].loopStart);
          this._activateGroup(next);
          if (this._ctx.state === 'suspended') {
            // Replace silently while paused; never resume the user's context here.
            next.programGain.gain.setValueAtTime(1, this._ctx.currentTime);
            this._disposeGroup(old);
          } else {
            await new Promise(resolve => {
              const end = start + PROGRAM_FADE_SECONDS;
              const from = new Float32Array(129);
              const to = new Float32Array(129);
              for (let i = 0; i < from.length; i++) {
                const angle = i / (from.length - 1) * Math.PI / 2;
                from[i] = Math.cos(angle);
                to[i] = Math.sin(angle);
              }
              old.programGain.gain.setValueCurveAtTime(from, start, PROGRAM_FADE_SECONDS);
              next.programGain.gain.setValueCurveAtTime(to, start, PROGRAM_FADE_SECONDS);
              // End old voices on the audio thread, independent of JS timer frequency.
              old.sources.forEach(source => source.stop(end + 0.01));
              this._transition = { old, next, start, end, resolve };
            });
          }
          return this.getState();
        } catch (error) {
          if (!controller.signal.aborted && !this._disposed) {
            this._state.error = `切换失败，继续当前音乐：${error.message || error}`;
            throw error;
          }
          return this.getState();
        } finally {
          if (this._switchAbort === controller) this._switchAbort = null;
          this._state.switching = false;
          this._notify();
        }
      })();
      this._switchTask = task;
      await task;
      return this.getState();
    }

    _finishTransition() {
      const transition = this._transition;
      if (!transition) return;
      this._transition = null;
      transition.next.programGain.gain.cancelScheduledValues(this._ctx.currentTime);
      transition.next.programGain.gain.setValueAtTime(1, this._ctx.currentTime);
      this._disposeGroup(transition.old);
      transition.resolve();
    }

    _disposeGroup(group) {
      if (!group || group.disposed) return;
      group.disposed = true;
      group.sources.forEach(source => {
        try { source.stop(); source.disconnect(); source.buffer = null; } catch {}
      });
      group.trackGains.forEach(gain => gain.disconnect());
      group.analysers.forEach(analyser => analyser.disconnect());
      group.programGain.disconnect();
      group.loops = [];
      group.sources = [];
      group.scratch = [];
      this._groups = this._groups.filter(item => item !== group);
    }

    setScore(score, valid = true, { allowReward = true } = {}) {
      const value = Number(score);
      if (Number.isFinite(value)) this._inputScore = clamp(value, 0, 100);
      this._valid = Boolean(valid) && Number.isFinite(value);
      this._allowReward = Boolean(allowReward);
    }

    setVolume(value) {
      if (!Number.isFinite(Number(value))) return;
      this._volume = clamp(Number(value));
      this._updateMaster(0.35);
    }

    setMuted(value) {
      this._muted = Boolean(value);
      this._updateMaster(0.5);
    }

    _updateMaster(tau) {
      if (!this._master || this._ctx.state === 'closed') return;
      const target = this._muted || !this._state.playing ? 0 : this._volume;
      const now = Math.max(this._ctx.currentTime, this._sourceStart);
      this._master.gain.cancelScheduledValues(now);
      this._master.gain.setTargetAtTime(target, now, tau);
    }

    async pause() {
      if (!this._state.ready || !this._state.playing) return;
      const token = ++this._pauseToken;
      this._state.playing = false;
      this._updateMaster(0.035);
      await new Promise(resolve => setTimeout(resolve, 180));
      if (token !== this._pauseToken || this._disposed) return;
      await this._ctx.suspend();
      this._finishTransition();
      this._state.contextState = this._ctx.state;
      this._notify();
    }

    async resume() {
      if (!this._state.ready) return this.start();
      ++this._pauseToken;
      await this._ctx.resume();
      this._state.playing = true;
      this._state.contextState = this._ctx.state;
      this._updateMaster(0.4);
      this._notify();
    }

    _tick() {
      if (this._disposed) return;
      const now = performance.now();
      const dt = Math.min(0.25, Math.max(0.001, (now - this._lastTick) / 1000));
      this._lastTick = now;
      if (this._transition && (this._ctx.currentTime >= this._transition.end || this._ctx.state === 'suspended')) this._finishTransition();
      const layered = isLayeredProgram(this._state.programId);
      if (layered) {
        if (!this._state.ready) this._previewClock += dt;
        const controller = this._state.ready ? this._activeGroup?.layers : this._previewLayers;
        const time = this._state.ready ? this._ctx.currentTime : this._previewClock;
        if (controller && (!this._state.ready || this._ctx.state === 'running' && time >= this._activeGroup.startTime)) {
          const view = controller.update(time, this._inputScore, this._valid, this._allowReward);
          if (this._state.ready) {
            this._activeGroup.targetGains = [...view.targetGains];
            const schedule = (nodes, event) => {
              if (!event) return;
              nodes.forEach((node, i) => {
              const gain = node.gain;
              // The analytical envelope used by the renderer is identical to
              // these audio-clock ramps; polling never restarts a voice.
              gain.cancelScheduledValues(event.now);
              gain.setValueAtTime(event.from[i], event.now);
              if (event.type === 'ramp') {
                gain.setValueAtTime(event.from[i], event.start);
                gain.linearRampToValueAtTime(event.to[i], event.end);
              }
              });
            };
            schedule(this._trackGains, view.event);
          }
          this._applyLayerView(view);
        }
        this._acceptedScore = this._inputScore;
      } else {
      const changedValid = this._valid !== this._acceptedValid;
      // Crossing the full-feedback boundary must survive the score deadband;
      // stability confirmation and smooth gain ramps still apply.
      if (Math.abs(this._inputScore - this._candidateScore) > 1.5 || crossesFullFeedback(this._inputScore, this._candidateScore) || changedValid && this._candidateValid !== this._valid) {
        this._candidateScore = this._inputScore;
        this._candidateValid = this._valid;
        this._candidateSince = now;
      }
      const scoreChanged = Math.abs(this._candidateScore - this._acceptedScore) >= 2 || crossesFullFeedback(this._candidateScore, this._acceptedScore);
      if ((changedValid || scoreChanged) && now - this._candidateSince >= 450) {
        this._acceptedScore = this._candidateScore;
        this._acceptedValid = this._valid;
        this._targetLevels = levelsFor(this._acceptedScore, this._acceptedValid);
        this._targetGains = gainsFor(this._targetLevels, this._rms);
        if (this._ctx && this._state.ready) {
          const time = this._ctx.currentTime;
          this._groups.forEach(group => {
            if (group.layers) return;
            group.targetGains = gainsFor(this._targetLevels, group.rms);
            group.trackGains.forEach((gain, i) => {
              gain.gain.cancelScheduledValues(time);
              gain.gain.setTargetAtTime(group.targetGains[i], time, MIX_TAU);
            });
          });
        }
      }
      }
      // A suspended audio clock also freezes visual/diagnostic gain interpolation.
      const audioClockAdvancing = !this._state.ready || this._ctx?.state === 'running';
      const smooth = audioClockAdvancing ? 1 - Math.exp(-dt / MIX_TAU) : 0;
      this._state.score += (this._acceptedScore - this._state.score) * smooth;
      for (let i = 0; i < 3; i++) {
        if (!layered) {
        this._state.levels[i] += (this._targetLevels[i] - this._state.levels[i]) * smooth;
        this._actualGains[i] += (this._targetGains[i] - this._actualGains[i]) * smooth;
        }
        let energy = 0;
        if (this._state.playing && this._analysers[i]) {
          let combinedEnergy = 0;
          for (const group of this._groups) {
            const samples = group.scratch[i];
            group.analysers[i].getFloatTimeDomainData(samples);
            let square = 0;
            for (let sample = 0; sample < samples.length; sample++) square += samples[sample] * samples[sample];
            const rawRms = Math.sqrt(square / samples.length);
            let weight = 1;
            if (this._transition) {
              const progress = clamp((this._ctx.currentTime - this._transition.start) / PROGRAM_FADE_SECONDS);
              weight = group === this._transition.old ? Math.cos(progress * Math.PI / 2) : Math.sin(progress * Math.PI / 2);
            }
            combinedEnergy += (clamp(rawRms / (group.rms[i] * 1.8)) * weight) ** 2;
          }
          energy = clamp(Math.sqrt(combinedEnergy)) * this._state.levels[i];
        }
        const energyTau = energy > this._state.energy[i] ? 0.075 : 0.36;
        if (audioClockAdvancing) this._state.energy[i] += (energy - this._state.energy[i]) * (1 - Math.exp(-dt / energyTau));
      }
      this._state.contextState = this._ctx?.state || 'uninitialized';
      this._state.elapsed = this._ctx && this._state.ready ? Math.max(0, this._ctx.currentTime - this._sourceStart) : 0;
      this._state.audible = this._state.playing && this._ctx?.state === 'running' && !this._muted && this._volume > 0;
    }

    getState() {
      // Snapshot only: rendering never schedules or restarts audio.
      const program = PROGRAMS.find(item => item.id === this._state.programId);
      const layered = isLayeredProgram(program.id);
      return { ...this._state, levels: [...this._state.levels], energy: [...this._state.energy],
        feedbackProfile: layered ? program.feedbackProfile : null,
        layerStage: layered ? this._layerView?.layerStage || 1 : null,
        appliedStage: layered ? this._layerView?.appliedStage || 1 : null,
        layerLabels: layered ? [...window.EasyGoingProfile.labels] : null,
        layerTransition: layered ? this._layerView?.transition || null : null,
        beatGrid: layered ? this._activeGroup?.beatGrid || program.beatGrid : null,
        actualGain: [...this._actualGains], targetGain: [...this._targetGains],
        sourceCount: this._groups.reduce((count, group) => count + group.sources.length, 0),
        activeSourceCount: this._sources.length, decodedGroupCount: this._groups.length,
        requestedProgramId: this._desiredProgramId, contextSampleRate: this._ctx?.sampleRate || null,
        programCrossfadeSeconds: PROGRAM_FADE_SECONDS, volume: this._volume, muted: this._muted,
        loopInfo: this._loopInfo ? { ...this._loopInfo } : null,
        completedLoops: this._loopInfo ? Math.max(0, Math.floor((this._state.elapsed - this._loopInfo.firstPlayDuration) / this._state.cycleDuration) + 1) : 0 };
    }

    _notify() {
      try { this._onStatus(this.getState()); } catch (error) { console.error(error); }
    }

    dispose() {
      this._disposed = true;
      ++this._pauseToken;
      clearInterval(this._timer);
      this._abort.abort();
      this._switchAbort?.abort();
      this._finishTransition();
      [...this._groups].forEach(group => this._disposeGroup(group));
      this._sources = [];
      if (this._ctx && this._ctx.state !== 'closed') this._ctx.close();
      this._state.playing = false;
      this._state.audible = false;
      this._state.switching = false;
    }
  }

  ResonanceAudio.levelsFor = levelsFor;
  ResonanceAudio.programs = PROGRAMS;
  ResonanceAudio.isLayeredProgram = isLayeredProgram;
  ResonanceAudio.gainsFor = gainsFor;
  ResonanceAudio.prepareLoop = prepareLoop;
  window.ResonanceAudio = ResonanceAudio;
})();
