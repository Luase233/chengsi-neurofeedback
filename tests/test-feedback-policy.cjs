'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const Policy = require('../feedback-policy.js');

function context(windowEnd, valid = true, score = 72, extra = {}) {
  return { mode:'live', phase:'training', online:true, stale:false, valid,
    score:valid ? score : null,
    snapshot:{session_id:'test',connection:{status:'connected'},feedback:{window_end:windowEnd},training:{valid_seconds:20}},
    ...extra };
}
function ready() {
  const policy = new Policy();
  policy.update(context(1), 0);
  assert.equal(policy.update(context(2), 1000).status, 'live');
  return policy;
}

test('startup stays neutral until two distinct valid windows; no invented high score', () => {
  const policy = new Policy();
  assert.equal(policy.update(context(1, false), 0).hasFeedback, false);
  const first = policy.update(context(2, true, 85), 1000);
  assert.equal(first.displayScore, null);
  assert.equal(first.audioScore, 0);
  assert.equal(first.recoveryCount, 1);
  for (let now = 1250; now < 2000; now += 250) {
    assert.equal(policy.update(context(2, true, 85), now).hasFeedback, false);
  }
  assert.equal(policy.update(context(3, true, 65), 2000).displayScore, 65);
});

test('short artifacts hold presentation only and stop holding after three seconds', () => {
  const policy = ready();
  const input = context(3, false);
  const before = JSON.stringify(input);
  const result = policy.update(input, 2000);
  assert.equal(result.status, 'hold');
  assert.equal(result.displayScore, 72);
  assert.equal(result.hasFeedback, true);
  assert.equal(JSON.stringify(input), before, 'no measurement or valid-time mutation');
  assert.equal(input.score, null);
  assert.equal(input.valid, false);
  assert.equal(policy.update(context(4, false), 5000).held, true);
  const neutral = policy.update(context(4, false), 5001);
  assert.equal(neutral.status, 'neutral');
  assert.equal(neutral.displayScore, null);
  assert.equal(neutral.audioScore, 0);
  assert.equal(neutral.hasFeedback, false);
});

test('recovery requires consecutive different window_end values, not socket repeats', () => {
  const policy = ready();
  policy.update(context(3, false), 2000);
  assert.equal(policy.update(context(4, true, 80), 3000).held, true);
  assert.equal(policy.update(context(4, true, 80), 3250).recoveryCount, 1);
  assert.equal(policy.update(context(4, true, 80), 3500).recoveryCount, 1);
  const result = policy.update(context(5, true, 66), 4000);
  assert.equal(result.status, 'live');
  assert.equal(result.displayScore, 66);
});

test('alternating one good and one bad window cannot extend hold or hide the reminder', () => {
  const policy = ready();
  policy.update(context(3, false), 2000);
  let announcements = 0;
  for (let second = 3; second <= 20; second++) {
    const result = policy.update(context(second + 1, second % 2 === 1, 95), second * 1000);
    announcements += Number(result.announceReminder);
    if (second > 5) {
      assert.equal(result.hasFeedback, false);
      assert.equal(result.displayScore, null);
    }
    assert.equal(result.reminder, second >= 14);
  }
  assert.equal(announcements, 1);
  assert.equal(policy.update(context(30, true, 55), 21000).status, 'recovering');
  assert.equal(policy.update(context(31, true, 55), 22000).status, 'live');
  assert.equal(policy.update(context(32, false), 23000).reminder, false);
  assert.equal(policy.update(context(33, false), 35000).announceReminder, true);
});

test('pause, phase exit, stale feed and connection failures immediately clear held feedback', () => {
  for (const changes of [
    {phase:'paused'}, {phase:'completed'}, {phase:'disconnected'}, {phase:'calibrating_open'},
    {stale:true}, {online:false},
    {snapshot:{...context(3).snapshot,connection:{status:'disconnected'}}},
  ]) {
    const policy = ready();
    policy.update(context(3, false), 2000);
    const output = policy.update(context(3, false, null, changes), 2250);
    assert.equal(output.hasFeedback, false);
    assert.equal(output.displayScore, null);
    assert.equal(policy.update(context(3, true, 95), 2500).hasFeedback, false, 'old window cannot restart feedback');
    assert.equal(policy.update(context(4, true, 90), 3000).hasFeedback, false);
    assert.equal(policy.update(context(5, true, 60), 4000).displayScore, 60);
  }
});

test('a new session never carries the previous score or recovery window count', () => {
  const policy = ready();
  const next = context(1, true, 45, {snapshot:{...context(1).snapshot,session_id:'new-session'}});
  const result = policy.update(next, 2000);
  assert.equal(result.displayScore, null);
  assert.equal(result.hasFeedback, false);
  assert.equal(result.recoveryCount, 1);
  assert.equal(policy.update({...next,snapshot:{...next.snapshot,feedback:{window_end:2}}}, 3000).displayScore, 45);
});

test('missing window identity and duplicate or regressing windows cannot confirm recovery', () => {
  const policy = new Policy();
  assert.equal(policy.update(context(undefined), 0).recoveryCount, 0);
  assert.equal(policy.update(context(5), 1000).recoveryCount, 1);
  assert.equal(policy.update(context(5), 1250).hasFeedback, false);
  assert.equal(policy.update(context(4), 1500).hasFeedback, false);
  assert.equal(policy.update(context(6), 2000).status, 'live');
});
