/* Easy Going: continuous 40 / 55 / 70 full-level anchors, one audio-clock envelope. */
(() => {
  'use strict';
  const CONFIG = Object.freeze({ version: 'easy-going-continuous-v2',
    fullLevelScores: Object.freeze([40, 55, 70]), baseFloor: 0.55,
    transitionSeconds: 2.5, trackGainCaps: Object.freeze([0.47, 0.47, 0.47]) });
  const LABELS = Object.freeze(['基础声部', '扩展声部', '完整声部']);
  const clamp = n => Math.max(0, Math.min(1, n));
  const blend = (a, b, p) => a.map((value, i) => value + (b[i] - value) * p);
  const gainsFor = levels => levels.map((level, i) => level * CONFIG.trackGainCaps[i]);
  const stageFor = levels => levels[2] > 0.000001 ? 3 : levels[1] > 0.000001 ? 2 : 1;
  const equal = (a, b) => a.every((value, i) => Math.abs(value - b[i]) < 1e-9);

  function levelsFor(score, valid = true) {
    if (!valid || !Number.isFinite(score)) return [CONFIG.baseFloor, 0, 0];
    const [a, b, c] = CONFIG.fullLevelScores;
    return [CONFIG.baseFloor + (1 - CONFIG.baseFloor) * clamp(score / a),
      clamp((score - a) / (b - a)), clamp((score - b) / (c - b))];
  }

  class EasyGoingProfile {
    constructor() {
      // Starting and switching tracks begin quietly; the first update ramps
      // toward the selected score. No threshold unlock or fixed mix stages.
      this.from = this.to = levelsFor(0, false);
      this.start = this.end = 0;
      this.mode = 'initial';
    }

    snapshot(now) {
      const p = this.end > this.start ? clamp((now - this.start) / (this.end - this.start)) : 1;
      const levels = blend(this.from, this.to, p);
      return { layerStage: stageFor(this.to), appliedStage: stageFor(levels),
        levels, gains: gainsFor(levels), targetGains: gainsFor(this.to),
        transition: now < this.end ? { start: this.start, end: this.end,
          fromStage: stageFor(this.from), toStage: stageFor(this.to) } : null };
    }

    update(now, score, valid = true, allowReward = true) {
      valid = Boolean(valid) && Number.isFinite(score);
      const mode = !valid ? 'invalid' : allowReward ? 'live' : 'hold';
      const current = this.snapshot(now);
      const target = mode === 'hold' ? current.levels : levelsFor(score, valid);
      let event = null;
      // An unchanged (including >=70) target never restarts a fade. Held EEG
      // freezes the current sound; unavailable data fades back to the soft base.
      const changed = mode === 'hold' ? this.mode !== 'hold' : !equal(target, this.to);
      if (changed) {
        this.from = current.levels;
        this.to = [...target];
        this.start = now;
        this.end = mode === 'hold' || equal(this.from, this.to) ? now : now + CONFIG.transitionSeconds;
        event = { type: this.end > now ? 'ramp' : 'hold', now,
          from: current.gains, to: gainsFor(target), start: now, end: this.end };
      }
      this.mode = mode;
      return { ...this.snapshot(now), event };
    }
  }
  EasyGoingProfile.config = CONFIG;
  EasyGoingProfile.labels = LABELS;
  EasyGoingProfile.gainCaps = CONFIG.trackGainCaps;
  EasyGoingProfile.levelsFor = levelsFor;
  window.EasyGoingProfile = EasyGoingProfile;
})();
