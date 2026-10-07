/* A: luminous fabric resonance. Canvas-only scene; gains/envelopes come from the
   player. The moving silk and dust are decorative, not additional EEG signals. */
(function (root) {
  'use strict';
  var TAU = Math.PI * 2, meshes = Object.create(null), dust = [], seed = 318017;
  function random() { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }
  function clamp(x) { return Math.max(0, Math.min(1, Number(x) || 0)); }
  function smooth(x) { x=clamp(x);return x*x*(3-2*x); }
  // Audio smoothing approaches zero asymptotically. Share this deadband between
  // scene geometry and the UI counts so an absent layer actually reaches zero.
  function activeGain(x) { return clamp((clamp(x)-.003)/.997); }
  // Fixed, progressively interleaved positions avoid re-spacing every existing
  // node when a new one appears. Adjacent gains only fade the boundary node.
  var drumOrder=[0,12,6,18,3,15,9,21,1,13,7,19,4,16,10,22,2,14,8,20,5,17,11,23];
  root.getResonanceVisualState=function(levels,opts){
    levels=levels||[0,0,0];
    var a=clamp(levels[0]),d=activeGain(levels[1]),b=activeGain(levels[2]);
    var linear=opts&&opts.linearPresence;
    return {drumCount:Math.ceil(d*24),ambientExpansion:.79+.21*(linear?a:Math.sqrt(a)),bassRings:b>0?(linear?Math.ceil(b*18):Math.max(2,Math.ceil(b*18))):0};
  };
  function rgba(c, a) { return 'rgba(' + c + ',' + Math.max(0, Math.min(1, a)) + ')'; }
  for (var n = 0; n < 4800; n++) dust.push({ a: random() * TAU, b: random(), c: random(), d: random(), e: random(), f: random() });
  function halo(ctx, x, y, r, color, opacity) {
    var g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(color, opacity));
    g.addColorStop(0.18, rgba(color, opacity * 0.61));
    g.addColorStop(0.5, rgba(color, opacity * 0.19));
    g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  function clothPoint(theta, v, layer, phase, r, energy, gain, target, offset) {
    var twist = theta * 3 + phase * 0.53 + layer * 1.91;
    var body = r * (0.885 + layer * 0.025 + Math.sin(theta * 5 - phase + layer * 1.5) * 0.040);
    var width = r * (0.080 + gain * 0.058 + energy * 0.008);
    var radial = body + v * width * Math.cos(twist) + r * (.012+gain*.016) * Math.sin(theta * 8 + v * 1.85 + phase * 0.8 + layer);
    var z = v * width * 1.4 * Math.sin(twist) + r * 0.023 * Math.sin(theta * 3 - phase + layer);
    target[offset] = Math.cos(theta) * radial + z * 0.18;
    target[offset + 1] = Math.sin(theta) * radial * 0.96 + z * 0.43;
  }
  root.drawResonance = function (ctx, w, h, t, levels, audioEnergy, opts) {
    opts = opts || {}; levels = levels || [0,0,0]; audioEnergy = audioEnergy || [0,0,0];
    var valid = opts.valid !== false;
    var intensity = 0.4 + clamp(opts.intensity == null ? 1 : opts.intensity) * 0.6;
    var presence = intensity * (valid ? 1 : 0.29);
    // Legacy songs use their established visibility curve. Layered compositions
    // follow the actual fade envelope without visually inflating quiet layers.
    var ambientGain=clamp(levels[0]), drumGain=activeGain(levels[1]), bassGain=activeGain(levels[2]);
    var visualState=root.getResonanceVisualState(levels,opts),linear=opts.linearPresence===true;
    var ambient=linear?ambientGain:ambientGain>0?(.46+.54*Math.pow(ambientGain,.4)):0;
    var drum=linear?drumGain:drumGain>0?Math.pow(drumGain,.38)*smooth(drumGain/.025):0;
    var bass=linear?bassGain:bassGain>0?Math.pow(bassGain,.32)*smooth(bassGain/.025):0;
    var ae = clamp(audioEnergy[0]), de = clamp(audioEnergy[1]), be = clamp(audioEnergy[2]);
    var teal = valid ? '96,211,231' : '125,157,166';
    var pale = valid ? '161,241,250' : '157,176,181';
    var gold = valid ? '255,177,79' : '176,157,129';
    var ivory = valid ? '255,225,170' : '192,185,173';
    var blue = valid ? '78,170,255' : '133,157,180';
    var tiny = opts.compact === true || w < 300;
    var radius = tiny ? Math.min(w,h) * 0.40 : Math.min(w * 0.34, h * 0.44);
    var cx = w * (tiny ? 0.5 : 0.45), cy = h * 0.5, phase = t * 0.17;
    var i,j,k,a,rr,x,y,d,p,alpha;
    ctx.save(); ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1; ctx.shadowBlur = 0;
    ctx.fillStyle = opts.previewBackground ?? '#020d16'; ctx.fillRect(0,0,w,h); ctx.translate(cx,cy);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    halo(ctx,0,0,radius*1.4,'22,87,111',0.22);
    ctx.globalCompositeOperation = 'lighter';
    if (opts.particles !== false) {
      ctx.fillStyle = rgba(teal,0.11*presence); ctx.beginPath();
      for(i=0;i<(tiny?55:220);i++) { p=dust[i]; d=tiny?0.4:0.6; ctx.rect((p.b-.5)*w*1.1,(p.c-.5)*h*1.12,d,d); }
      ctx.fill();
    }
    if (ambient > .002) {
      var clothAlpha=ambient*presence,clothRadius=radius*visualState.ambientExpansion;
      halo(ctx,0,0,radius*1.17,teal,.035*clothAlpha);
      if (!tiny) {
        for(i=0;i<6;i++) {
          ctx.beginPath(); ctx.moveTo(-w*.62,radius*(.17+i*.054));
          ctx.bezierCurveTo(-radius*1.26,-radius*(.38+i*.018+Math.sin(phase)*.06),radius*.68,radius*(1.02+i*.025),w*.67,radius*(.23+i*.017));
          ctx.strokeStyle=rgba(teal,clothAlpha*(i===2?.10:.025)); ctx.lineWidth=i===2?1.1:.7; ctx.stroke();
        }
      }
      var texturedSilk=false;
      if(typeof root.drawResonanceSilk==='function') {
        ctx.save();
        try { texturedSilk=root.drawResonanceSilk(ctx,clothRadius,t,ambientGain,ae,opts)===true; }
        catch(silkError) { texturedSilk=false; }
        finally { ctx.restore(); }
      }
      if(!texturedSilk) {
      // Filled twisted surfaces provide dimensional translucency. Long and cross
      // fibers lie on those surfaces, producing folding silk rather than circles.
      var columns=tiny?73:145, rows=tiny?13:25, cacheKey=columns+':'+rows;
      var mesh=meshes[cacheKey]||(meshes[cacheKey]=new Float32Array(columns*rows*2));
      for(var layer=0;layer<3;layer++) {
        for(k=0;k<rows;k++) for(j=0;j<columns;j++) {
          clothPoint(j/(columns-1)*TAU,k/(rows-1)*2-1,layer,phase,clothRadius,ae,ambientGain,mesh,(k*columns+j)*2);
        }
        var step=tiny?2:4;
        for(k=0;k<rows-1;k+=step) for(j=0;j<columns-1;j+=2) {
          var nj=Math.min(j+2,columns-1),nk=Math.min(k+step,rows-1);
          var p0=(k*columns+j)*2,p1=(k*columns+nj)*2,p2=(nk*columns+nj)*2,p3=(nk*columns+j)*2;
          a=j/(columns-1)*TAU;
          var fold=Math.abs(Math.sin(a*3+phase*.53+layer*1.91)), grazing=Math.pow(fold,7);
          // Broad illuminated facets supply the main visible material. Their
          // brightness is greater than the wire detail laid over them below.
          ctx.fillStyle=rgba(teal,clothAlpha*(.080+fold*.125+grazing*.195));
          ctx.beginPath(); ctx.moveTo(mesh[p0],mesh[p0+1]); ctx.lineTo(mesh[p1],mesh[p1+1]);
          ctx.lineTo(mesh[p2],mesh[p2+1]); ctx.lineTo(mesh[p3],mesh[p3+1]); ctx.closePath();ctx.fill();
        }
        // Soft specular ribbons span several fibers, like light on folded silk.
        for(var shine=0;shine<4;shine++) {
          ctx.strokeStyle=rgba(pale,clothAlpha*(shine===1?.105:.060));
          ctx.lineWidth=radius*(tiny?.028:.021);
          ctx.shadowColor=rgba(teal,.45);ctx.shadowBlur=tiny?2:6;
          ctx.beginPath();
          for(j=0;j<columns;j++) {
            var shineF=Math.max(0,Math.min(rows-1,(.16+shine*.215+Math.sin(j/(columns-1)*TAU*3+phase+shine)*.045)*(rows-1)));
            var shineK=Math.floor(shineF),shineMix=shineF-shineK;
            var shineAt=(shineK*columns+j)*2,shineNext=(Math.min(shineK+1,rows-1)*columns+j)*2;
            var shineX=mesh[shineAt]*(1-shineMix)+mesh[shineNext]*shineMix;
            var shineY=mesh[shineAt+1]*(1-shineMix)+mesh[shineNext+1]*shineMix;
            if(!j)ctx.moveTo(shineX,shineY);else ctx.lineTo(shineX,shineY);
          }
          ctx.closePath();ctx.stroke();ctx.shadowBlur=0;
        }
        for(k=0;k<rows;k++) {
          var edge=k===0||k===rows-1, center=Math.abs(k/(rows-1)-.5)*2;
          alpha=clothAlpha*(edge?.135:.006+center*center*.015);
          ctx.strokeStyle=rgba(edge?pale:teal,alpha); ctx.lineWidth=edge?(tiny?.5:.7):(tiny?.28:.4);
          ctx.beginPath();
          for(j=0;j<columns;j++) { var pi=(k*columns+j)*2; if(!j)ctx.moveTo(mesh[pi],mesh[pi+1]);else ctx.lineTo(mesh[pi],mesh[pi+1]); }
          ctx.closePath(); if(edge){ctx.shadowBlur=tiny?3:5;ctx.shadowColor=rgba(teal,.5);}ctx.stroke();ctx.shadowBlur=0;
        }
        ctx.strokeStyle=rgba(pale,clothAlpha*.012);ctx.lineWidth=tiny?.25:.35;ctx.beginPath();
        for(j=0;j<columns-1;j+=3) for(k=0;k<rows;k++) {
          var at=(k*columns+j)*2;if(!k)ctx.moveTo(mesh[at],mesh[at+1]);else ctx.lineTo(mesh[at],mesh[at+1]);
        }
        ctx.stroke();
      }
      }
      if(opts.particles!==false) {
        var dustLimit=(tiny?580:4000)*(texturedSilk?1/3:1)*(.15+.85*ambientGain);
        var dustCount=Math.ceil(dustLimit);
        for(var group=0;group<4;group++) {
          ctx.fillStyle=rgba(group===3?pale:teal,clothAlpha*(.36+group*.20));ctx.beginPath();
          for(i=group;i<dustCount;i+=4) {
            p=dust[i];a=p.a+phase*(.10+p.c*.06);
            rr=clothRadius*(.77+p.b*(.18+ambientGain*.20)+Math.sin(a*5-phase+p.d*3)*(.016+ambientGain*.019));
            x=Math.cos(a)*rr;y=Math.sin(a)*rr*.96;
            var curl=Math.sin(a*3+phase+p.e*6)*radius*.016;
            d=((tiny?.23:.34)+p.f*(tiny?.23:.48))*smooth((dustLimit-i)/20);if(p.f>.97)d*=1.45;
            x+=curl;y-=curl*.7;ctx.moveTo(x+d,y);ctx.arc(x,y,d,0,TAU);
          }
          ctx.fill();
        }
      }
    }
    if(drumGain>0) {
      var drumAlpha=drum*presence,beat=Math.pow(de,.68),orbit=radius*(.563+beat*.006);
      for(k=0;k<7;k++) {
        ctx.strokeStyle=rgba(k===3?ivory:gold,drumAlpha*(k===3?.48:.13+beat*.10));
        ctx.lineWidth=k===3?(tiny?.9:1.65):.75;ctx.beginPath();
        for(j=0;j<=120;j++) {
          a=j/120*TAU;rr=orbit+(k-3)*radius*.008;
          rr+=Math.sin(a*(5+k%3)+phase*(k%2?1:-1)+k)*radius*.008;
          x=Math.cos(a)*rr;y=Math.sin(a)*rr;if(!j)ctx.moveTo(x,y);else ctx.lineTo(x,y);
        }
        if(k===3){ctx.shadowColor=rgba(gold,.6);ctx.shadowBlur=tiny?3:6;}ctx.stroke();ctx.shadowBlur=0;
      }
      if(opts.particles!==false) {
        ctx.fillStyle=rgba(gold,drumAlpha*(.64+beat*.24));ctx.beginPath();
        var goldDust=(tiny?180:1050)*drumGain;
        for(i=0;i<Math.ceil(goldDust);i++) {
          p=dust[i+1700];a=p.a-phase*.18;rr=radius*(.43+p.b*.29);
          d=((tiny?.17:.24)+p.c*(tiny?.17:.39))*smooth((goldDust-i)/20);x=Math.cos(a)*rr;y=Math.sin(a)*rr;ctx.moveTo(x+d,y);ctx.arc(x,y,d,0,TAU);
        }
        ctx.fill();
      }
      for(i=0;i<24;i++) {
        var nodePresence=smooth((drumGain*24-i)/.90);if(nodePresence===0)continue;
        a=drumOrder[i]/24*TAU+phase*.12+.13;x=Math.cos(a)*orbit;y=Math.sin(a)*orbit;
        var hot=(.81+Math.sin(i*1.7+t*.24)*.05+beat*.14)*nodePresence*presence*(linear?drumGain:1);
        halo(ctx,x,y,radius*(.080+beat*.012),gold,hot*.88);
        halo(ctx,x,y,radius*(.047+beat*.008),gold,hot*1.05);
        halo(ctx,x,y,radius*.027,ivory,hot*1.22);
        ctx.fillStyle=rgba(ivory,hot);ctx.beginPath();
        ctx.ellipse(x,y,radius*(.012+beat*.002),radius*(.017+beat*.003),a,0,TAU);ctx.fill();
      }
    }
    if(bassGain>0) {
      var bassAlpha=bass*presence,core=radius*(.119+.050*bassGain+Math.sin(t*.42)*.0025+be*.005);
      halo(ctx,0,0,radius*.41,blue,bassAlpha*(.23+be*.10));
      for(k=0;k<18;k++) {
        var ringPresence=k<2&&!linear?smooth(bassGain*18):smooth(bassGain*18-k);
        if(ringPresence===0)continue;
        ctx.strokeStyle=rgba(blue,bassAlpha*ringPresence*(.055+(1-k/18)*.21+be*.055));
        ctx.lineWidth=k%5===0?.9:.45;ctx.beginPath();
        for(j=0;j<=96;j++) {
          a=j/96*TAU;rr=core*(1.14+k*(.046+bassGain*.010));
          rr+=radius*.009*Math.sin(a*5+phase*1.2+k*.5)+radius*.0035*Math.sin(a*13-phase+k);
          if(!j)ctx.moveTo(Math.cos(a)*rr,Math.sin(a)*rr);else ctx.lineTo(Math.cos(a)*rr,Math.sin(a)*rr);
        }
        ctx.closePath();ctx.stroke();
      }
      ctx.globalCompositeOperation='source-over';ctx.globalAlpha=bassAlpha;
      var sphere=ctx.createRadialGradient(-core*.16,-core*.24,core*.06,0,0,core);
      sphere.addColorStop(0,'#203d62');sphere.addColorStop(.36,'#173352');sphere.addColorStop(.67,'#285c91');
      sphere.addColorStop(.88,valid?'#489fdf':'#647e93');sphere.addColorStop(.98,valid?'#9edbff':'#9dabb5');sphere.addColorStop(1,'#38658a');
      ctx.fillStyle=sphere;ctx.beginPath();ctx.arc(0,0,core,0,TAU);ctx.fill();
      ctx.globalAlpha=1;ctx.globalCompositeOperation='lighter';
      halo(ctx,-core*.36,-core*.39,core*.53,'177,224,255',bassAlpha*.20);
      ctx.strokeStyle=rgba('142,213,255',bassAlpha*.8);ctx.lineWidth=tiny?.8:1.35;
      ctx.shadowColor=rgba(blue,.9);ctx.shadowBlur=tiny?5:13;ctx.beginPath();ctx.arc(0,0,core*.996,0,TAU);ctx.stroke();ctx.shadowBlur=0;
      if(opts.particles!==false) {
        ctx.fillStyle=rgba('158,218,255',bassAlpha*.46);ctx.beginPath();
        for(i=0;i<(tiny?65:280);i++) {
          p=dust[i+2300];a=p.a+phase*.24;rr=core*(.35+p.b*1.85);x=Math.cos(a)*rr;y=Math.sin(a)*rr;
          d=tiny?.45:.65+p.c*.42;if(rr<core*.83&&p.c<.8)continue;ctx.rect(x,y,d,d);
        }
        ctx.fill();
      }
    }
    ctx.restore();
  };
})(typeof window!=='undefined'?window:globalThis);
