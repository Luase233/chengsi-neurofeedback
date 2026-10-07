/* Flat Muse fitting illustration. Teaching positions only; never live status.
   Canvas is transparent; caller owns clearing, scheduling and stage copy.
   assembly:true enables the non-looping 0–16 s Muse fitting sequence.
   The caller must explicitly enable headphones:true for the separate 17–23 s
   headphone step. surface is the opaque page background. */
(function () {
  'use strict';
  const clamp = x => Math.max(0, Math.min(1, x));
  const smooth = x => { const u = clamp(x); return u * u * (3 - 2 * u); };
  const ease = x => 1 - Math.pow(1 - clamp(x), 3);
  function rgba(value, alpha) {
    const color = String(value || '#e9eef5').trim();
    let c;
    if (/^#[0-9a-f]{3}$/i.test(color)) c = color.slice(1).split('').map(n => parseInt(n + n, 16));
    else if (/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(color)) c = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16));
    else if (/^rgba?\(/i.test(color)) c = color.slice(color.indexOf('(') + 1).split(/[\s,\/]+/).filter(Boolean).slice(0, 3).map(n => n.includes('%') ? parseFloat(n) * 2.55 : parseFloat(n));
    else if (/^color\(srgb\s/i.test(color)) c = color.replace(/^color\(srgb\s*/i, '').split(/[\s\/]+/).slice(0, 3).map(n => parseFloat(n) * 255);
    if (!c || c.some(n => !Number.isFinite(n))) c = [145, 154, 166];
    return `rgba(${c.map(n => Math.round(n)).join(',')},${alpha})`;
  }
  function path(ctx, draw, stroke, width, fill) {
    ctx.beginPath(); draw(ctx);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = width; ctx.stroke(); }
  }

  function drawHeadphones(ctx, { offsetY, foreground, muted, surface }) {
    // Proportions follow the supplied true side elevation, kept as flat line art.
    ctx.save(); ctx.translate(281, 245 + offsetY); ctx.scale(.232, .232); ctx.translate(-275, -770);
    const base = rgba(surface, 1), outline = rgba(foreground, .84);
    const solid = (shape, shade, weight = 10) => {
      path(ctx, shape, null, 0, base);
      path(ctx, shape, outline, weight, rgba(foreground, shade));
    };
    // Align the lowest point of the upper mesh curve with the scalp crown.
    // Translate the rigid headband and telescope the rod; keep the ear cup fixed.
    const scalpCrownY = 30 + 155 * .32;
    const meshCurveLowY = 124.722;
    const extension = (245 + (meshCurveLowY - 770) * .232 - scalpCrownY) / .232;
    ctx.save(); ctx.translate(0, -extension);
    const mesh = p => {
      p.moveTo(199, 91); p.bezierCurveTo(235, 132, 307, 140, 350, 91);
      p.lineTo(347, 163); p.bezierCurveTo(343, 199, 318, 217, 278, 220);
      p.bezierCurveTo(235, 220, 209, 202, 204, 166); p.closePath();
    };
    path(ctx, mesh, null, 0, rgba(foreground, .08));
    ctx.save(); ctx.beginPath(); mesh(ctx); ctx.clip();
    for (let x = 175; x <= 360; x += 15) {
      path(ctx, p => { p.moveTo(x, 86); p.lineTo(x + 30, 232); }, rgba(foreground, .24), 3);
      path(ctx, p => { p.moveTo(x + 30, 86); p.lineTo(x, 232); }, rgba(foreground, .17), 3);
    }
    ctx.restore();
    const yoke = p => {
      p.moveTo(168, 56); p.bezierCurveTo(168, 33, 198, 32, 200, 56);
      p.lineTo(202, 152); p.bezierCurveTo(203, 194, 223, 216, 274, 218);
      p.bezierCurveTo(326, 218, 347, 192, 349, 152); p.lineTo(348, 56);
      p.bezierCurveTo(348, 33, 380, 33, 380, 56); p.lineTo(376, 166);
      p.bezierCurveTo(374, 202, 361, 223, 335, 240);
      p.bezierCurveTo(319, 250, 302, 258, 301, 280); p.lineTo(301, 451);
      p.lineTo(247, 451); p.lineTo(246, 280);
      p.bezierCurveTo(246, 260, 229, 250, 212, 239);
      p.bezierCurveTo(184, 221, 172, 203, 170, 165); p.closePath();
    };
    solid(yoke, .22, 8);
    solid(p => p.roundRect(247, 450, 54, 15, 3), .4, 6);
    ctx.restore();
    // Extension changes this thin metal component only, leaving the ear cup fixed.
    solid(p => p.roundRect(262, 454 - extension, 24, 72 + extension, 11), .55, 7);
    path(ctx, p => { p.moveTo(268, 471 - extension); p.lineTo(268, 510); }, rgba(foreground, .76), 4);
    // A softly bowed, almost elliptical shell, rather than two nested boxes.
    const cup = p => {
      p.moveTo(276, 523);
      p.bezierCurveTo(333, 523, 395, 535, 423, 573);
      p.bezierCurveTo(460, 623, 470, 707, 472, 774);
      p.bezierCurveTo(474, 844, 465, 916, 435, 957);
      p.bezierCurveTo(405, 1000, 348, 1018, 277, 1018);
      p.bezierCurveTo(208, 1018, 149, 1001, 116, 961);
      p.bezierCurveTo(82, 920, 72, 850, 74, 778);
      p.bezierCurveTo(75, 706, 84, 622, 122, 575);
      p.bezierCurveTo(153, 536, 214, 523, 276, 523); p.closePath();
    };
    // Only one thin cushion edge remains on the rear; no decorative inner frame.
    ctx.save(); ctx.translate(-6, 3); solid(cup, .08, 7); ctx.restore();
    solid(cup, .15, 11);
    ctx.save(); ctx.translate(184, 530); ctx.rotate(-.32);
    solid(p => p.roundRect(-23, -8, 46, 13, 6), .36, 5);
    for (let x = -15; x <= 15; x += 6) path(ctx, p => {p.moveTo(x,-6);p.lineTo(x,2);}, rgba(foreground,.35),2);
    ctx.restore();
    ctx.save(); ctx.translate(365, 534); ctx.rotate(.28);
    solid(p => p.roundRect(-26, -5, 52, 7, 3), .32, 4); ctx.restore();
    ctx.restore();
  }

  window.drawWearGuide = function (ctx, width, height, time, options = {}) {
    const { accent = '#438cff', foreground = '#eef2f6', muted = '#667084',
      stage = 'intro', motion = 1, assembly = false, headphones = false,
      surface = '#10151d' } = options;
    const t = motion ? Math.max(0, Number(time) || 0) : 5;
    const st = motion ? Math.max(0, Number(options.sequenceTime ?? time) || 0) : (headphones ? 23 : 16);
    const forehead = assembly ? (st >= 4.8 && st < 8) || st >= 13 : stage === 'forehead' || stage === 'ready';
    const ear = assembly ? st >= 8.3 : stage === 'ear' || stage === 'ready';
    const dx = assembly ? 235 * (1 - smooth((st - .6) / 2.4)) : 0;
    const dy = assembly ? -48 * (1 - smooth((st - 3.2) / 1.6)) : 0;
    const nearExtension = assembly ? 88 * (1 - smooth((st - 8.3) / 2.1)) : 0;
    const farExtension = assembly ? 88 * (1 - smooth((st - 10.8) / 1.8)) : 0;
    const nearMoving = assembly && st >= 8.3 && st <= 10.4;
    const farMoving = assembly && st >= 10.8 && st <= 12.6;
    const farOpacity = assembly && motion
      ? .44 * smooth((st - 7.7) / .55) * (1 - smooth((st - 12.6) / .4)) : 0;
    const s = Math.min(width / 650, height / 500);
    const ink = rgba(foreground, .82), quiet = rgba(foreground, .055);
    ctx.save(); ctx.translate((width - 650 * s) / 2, (height - 500 * s) / 2); ctx.scale(s, s);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.save(); ctx.translate(20, 30); ctx.scale(.32, .32);

    // The supplied official side profile remains absolutely still throughout.
    const body = p => {
      p.moveTo(516, 1220);
      p.bezierCurveTo(559, 1143, 593, 1087, 609, 1029);
      p.bezierCurveTo(632, 944, 615, 860, 586, 804);
      p.bezierCurveTo(541, 720, 508, 668, 498, 589);
      p.bezierCurveTo(482, 481, 504, 393, 548, 315);
      p.bezierCurveTo(608, 207, 711, 155, 831, 155);
      p.bezierCurveTo(1001, 155, 1154, 202, 1225, 306);
      p.bezierCurveTo(1248, 340, 1264, 369, 1274, 403);
      p.bezierCurveTo(1283, 446, 1290, 496, 1283, 546);
      p.bezierCurveTo(1279, 580, 1254, 612, 1268, 648);
      p.bezierCurveTo(1284, 686, 1320, 718, 1344, 747);
      p.bezierCurveTo(1362, 772, 1350, 794, 1323, 801);
      p.bezierCurveTo(1292, 808, 1271, 817, 1283, 839);
      p.bezierCurveTo(1296, 859, 1300, 875, 1281, 887);
      p.bezierCurveTo(1270, 893, 1270, 897, 1277, 911);
      p.bezierCurveTo(1288, 940, 1279, 950, 1261, 952);
      p.bezierCurveTo(1251, 953, 1253, 962, 1256, 980);
      p.bezierCurveTo(1264, 1028, 1244, 1060, 1204, 1064);
      p.bezierCurveTo(1163, 1070, 1113, 1064, 1070, 1056);
      p.bezierCurveTo(1000, 1085, 947, 1141, 928, 1244);
    };
    path(ctx, p => { body(p); p.lineTo(516, 1220); p.closePath(); }, null, 0, quiet);
    path(ctx, body, ink, 14);
    path(ctx, p => { p.moveTo(846, 872); p.bezierCurveTo(864, 971, 946, 1019, 1070, 1056); }, ink, 13);
    path(ctx, p => { p.moveTo(1097, 565); p.bezierCurveTo(1150, 532, 1206, 530, 1254, 558); }, ink, 13);

    // Independent device layer. The front pad is fixed during length adjustment.
    ctx.save(); ctx.translate(dx / .32, dy / .32);
    function rearArm(p) {
      p.moveTo(1060, 457);
      p.bezierCurveTo(960, 477, 850, 487, 769, 505);
      p.bezierCurveTo(701, 522, 678, 587, 670, 660);
      p.bezierCurveTo(662, 727, 672, 751, 725, 760);
      p.lineTo(753, 715);
      p.bezierCurveTo(723, 681, 720, 612, 745, 564);
      p.bezierCurveTo(768, 521, 816, 526, 855, 553);
      p.bezierCurveTo(922, 531, 1003, 517, 1060, 507);
      p.closePath();
    }
    // Dashed, slightly offset hidden-side construction is a teaching convention,
    // only present during adjustment. This is a second temple arm, not a strap
    // around the back of the head. It recedes once both arms are adjusted.
    if (farOpacity > .001) {
      ctx.save(); ctx.globalAlpha = farOpacity;
      ctx.translate(-farExtension - 16, farExtension * .17 - 35);
      ctx.setLineDash([18, 18]);
      path(ctx, rearArm, rgba(farMoving ? accent : foreground, 1), 8);
      ctx.setLineDash([]);
      path(ctx, p => { p.moveTo(715, 718); p.lineTo(708, 744); p.quadraticCurveTo(720, 752, 736, 750); }, rgba(farMoving ? accent : foreground, 1), 18);
      ctx.restore();
    }

    // Telescoping inner rail fills the changing overlap, never stretching skin.
    path(ctx, p => {
      p.moveTo(1060, 484); p.lineTo(1042 - nearExtension, 484 + nearExtension * .17);
    }, rgba(foreground, .54), 37);
    ctx.save(); ctx.translate(-nearExtension, nearExtension * .17);
    path(ctx, rearArm, null, 0, rgba(foreground, .8));
    // A sleeve boundary makes the sliding length change readable.
    path(ctx, p => { p.moveTo(1015, 466); p.lineTo(1023, 510); }, rgba(nearMoving ? accent : muted, nearMoving ? .92 : .65), 7);
    path(ctx, p => {
      p.moveTo(715, 718); p.lineTo(708, 744); p.quadraticCurveTo(720, 752, 736, 750);
    }, rgba(ear ? accent : foreground, 1), 22);
    ctx.restore();

    // Fixed forehead band overlaps the movable rail at the factory joint.
    path(ctx, p => {
      p.moveTo(1272, 399);
      p.bezierCurveTo(1203, 423, 1124, 442, 1041, 460);
      p.lineTo(1053, 509);
      p.bezierCurveTo(1132, 493, 1213, 475, 1281, 459);
      p.closePath();
    }, null, 0, rgba(foreground, .8));
    path(ctx, p => { p.moveTo(1274, 416); p.lineTo(1278, 450); }, rgba(forehead ? accent : foreground, 1), 22);
    ctx.restore();

    // Ear stays in front of the fitted near-side arm and never follows the band.
    path(ctx, p => {
      p.moveTo(888, 594);
      p.bezierCurveTo(868, 548, 821, 520, 781, 535);
      p.bezierCurveTo(742, 549, 720, 589, 730, 644);
      p.bezierCurveTo(738, 690, 766, 719, 801, 752);
      p.bezierCurveTo(821, 769, 832, 789, 854, 789);
      p.bezierCurveTo(878, 790, 895, 771, 895, 740);
    }, ink, 13);
    path(ctx, p => { p.moveTo(756, 706); p.quadraticCurveTo(775, 726, 758, 749); }, rgba(foreground, .58), 10);
    ctx.restore();

    // Headphones are a separate topmost layer. An opaque shell conceals the
    // ear and the portion of the Muse arm that would physically be behind it.
    if (assembly && headphones && st >= 17.2) {
      drawHeadphones(ctx, {
        offsetY: -335 * (1 - smooth((st - 17.2) / 3)),
        foreground, muted, surface
      });
    }

    // Non-assembly stage mode retains a single quiet pulse, without callouts.
    if (!assembly && motion && t > .1 && t < 1.35) {
      const pulse = clamp((t - .1) / 1.25);
      for (const [active, x, y] of [[forehead, 429, 168], [ear, 249, 269]]) {
        if (!active) continue;
        ctx.beginPath(); ctx.arc(x, y, 11 + 17 * ease(pulse), 0, Math.PI * 2);
        ctx.strokeStyle = rgba(accent, .45 * (1 - pulse)); ctx.lineWidth = 2.5; ctx.stroke();
      }
    }
    ctx.restore();
  };
})();

/* This eye follows cue/phase events. Drawing never schedules sound. */
window.drawParticipantEye = function (ctx, width, height, openness, options = {}) {
  const o = Math.max(0, Math.min(1, openness));
  const size = Math.min(width * .27, height * .34, 350), scale = size / 240;
  ctx.save(); ctx.translate(width / 2 - 120 * scale, height * .47 - 75 * scale); ctx.scale(scale, scale);
  ctx.strokeStyle = options.color || '#448ff2'; ctx.fillStyle = options.color || '#448ff2';
  ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.lineWidth = 6;
  const upper = 96 + (20 - 96) * o, lower = 96 + (119 - 96) * o;
  const outline = () => {
    ctx.moveTo(35,70); ctx.bezierCurveTo(80,upper,160,upper,205,70);
    ctx.bezierCurveTo(160,lower,80,lower,35,70); ctx.closePath();
  };
  ctx.save(); ctx.beginPath(); outline(); ctx.clip(); ctx.globalAlpha *= o;
  ctx.beginPath(); ctx.arc(120,70,23,0,Math.PI*2); ctx.lineWidth = 5.5; ctx.stroke();
  ctx.beginPath(); ctx.arc(120,70,9,0,Math.PI*2); ctx.fill(); ctx.restore();
  ctx.beginPath(); ctx.moveTo(35,70); ctx.bezierCurveTo(80,upper,160,upper,205,70); ctx.stroke();
  ctx.save(); ctx.globalAlpha *= o;
  ctx.beginPath(); ctx.moveTo(35,70); ctx.bezierCurveTo(80,lower,160,lower,205,70); ctx.stroke(); ctx.restore();
  ctx.save(); ctx.globalAlpha *= 1-o;
  [[65,81,-6,10],[100,89,-2,12],[140,89,2,12],[175,81,6,10]].forEach(([x,y,dx,dy]) => {
    ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(x+dx,y+dy); ctx.stroke();
  });
  ctx.restore(); ctx.restore();
};
