/* Presentation only: never changes measurement validity, stored scores or time. */
(function (root, factory) {
  const Policy = factory();
  if (typeof module === 'object' && module.exports) module.exports = Policy;
  else root.ResonanceFeedbackPolicy = Policy;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  class ResonanceFeedbackPolicy {
    constructor({ holdMs = 3000, remindMs = 12000, recoveryWindows = 2 } = {}) {
      this.config = Object.freeze({ version:'calm-feedback-v1.0', holdMs, remindMs, recoveryWindows });
      this.key = null;
      this.clear();
    }
    clear(windowEnd = null) {
      this.lastWindow = Number.isFinite(windowEnd) ? windowEnd : null;
      this.lastScore = null;
      this.healthy = false;
      this.invalidSince = null;
      this.recoveryCount = 0;
      this.reminded = false;
    }
    result(status, displayScore = null, audioScore = 0, hasFeedback = false, extra = {}) {
      return { status, displayScore, audioScore, hasFeedback, held: status === 'hold',
        recovering: this.recoveryCount > 0, recoveryCount: this.recoveryCount,
        reminder: false, announceReminder: false, ...extra };
    }
    update(context, now) {
      const windowEnd = context.snapshot?.feedback?.window_end;
      const key = context.mode + ':' + (context.snapshot?.session_id || 'none');
      if (key !== this.key) { this.clear(); this.key = key; }
      const connection = context.snapshot?.connection?.status;
      const unavailable = context.stale || context.online === false || ['disconnected', 'error', 'connecting'].includes(connection);
      if (context.mode === 'preview' || context.phase !== 'training' || unavailable) {
        // A repeated pre-pause or pre-disconnect window cannot resume feedback.
        this.clear(windowEnd);
        return this.result(context.phase === 'training' && unavailable ? 'unavailable' : 'inactive');
      }
      const newWindow = Number.isFinite(windowEnd) && (this.lastWindow === null || windowEnd > this.lastWindow);
      if (newWindow) this.lastWindow = windowEnd;
      const valid = context.valid === true && Number.isFinite(context.score);
      if (valid && newWindow) {
        this.recoveryCount++;
        if (this.healthy || this.recoveryCount >= this.config.recoveryWindows) {
          this.lastScore = Math.max(0, Math.min(100, context.score));
          this.healthy = true;
          this.invalidSince = null;
          this.reminded = false;
          this.recoveryCount = 0;
        }
      } else if (!valid) {
        this.healthy = false;
        this.recoveryCount = 0;
      }
      if (this.healthy) return this.result('live', this.lastScore, this.lastScore, true);
      // An isolated good window never restarts the grace period. Recovery needs
      // consecutive, distinct valid windows, not repeated WebSocket snapshots.
      if (this.invalidSince === null) this.invalidSince = now;
      const invalidMs = Math.max(0, now - this.invalidSince);
      const reminder = invalidMs >= this.config.remindMs;
      const announceReminder = reminder && !this.reminded;
      if (reminder) this.reminded = true;
      const extra = { invalidMs, reminder, announceReminder };
      if (invalidMs <= this.config.holdMs && this.lastScore !== null) {
        return this.result('hold', this.lastScore, this.lastScore, true, extra);
      }
      return this.result(this.recoveryCount ? 'recovering' : 'neutral', null, 0, false, extra);
    }
  }
  return ResonanceFeedbackPolicy;
});
