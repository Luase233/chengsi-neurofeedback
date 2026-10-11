'use strict';
// Isolated browser check: static project files, mock read-only API, muted output.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'runtime/browser-scenes-audio');

(async()=>{
  await fs.mkdir(output,{recursive:true});
  const server=http.createServer(async(req,res)=>{
    const url=new URL(req.url,'http://localhost');
    if(url.pathname==='/scene-check'){
      res.setHeader('Content-Type','text/html; charset=utf-8');
      res.end('<!doctype html><style>html,body{margin:0;background:#000}canvas{width:100vw;height:auto}</style><canvas id="scene" width="1606" height="979"></canvas><script src="/training-scenes.js"></script>');
      return;
    }
    const file=path.resolve(root,'.'+(url.pathname==='/'?'/index.html':decodeURIComponent(url.pathname)));
    if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}
    try{
      res.setHeader('Content-Type',({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.json':'application/json','.wav':'audio/wav'})[path.extname(file)]||'application/octet-stream');
      res.end(await fs.readFile(file));
    }catch(_){res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  let browser;
  try{
    browser=await chromium.launch({headless:true,channel:process.env.CHENGSI_CHROME_CHANNEL||'msedge',args:['--mute-audio']});
    const page=await browser.newPage({viewport:{width:1606,height:979}}),errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    const results={scenes:{}};
    for(const mode of ['lake-trees','lake-house']){
      await page.goto(base+'/scene-check');
      await page.waitForFunction(()=>Object.values(TrainingScenes.diagnostics().assets).every(asset=>asset.loaded&&asset.animated&&!asset.error));
      const result=await page.evaluate(mode=>{
        const canvas=document.querySelector('#scene'),ctx=canvas.getContext('2d');
        let now=0;
        const frame=motion=>{
          now+=100;
          if(!TrainingScenes.draw(ctx,canvas.width,canvas.height,{mode,now,motion}))throw Error('Scene unavailable');
          return ctx.getImageData(0,0,canvas.width,canvas.height).data;
        };
        const first=frame(true);
        for(let i=0;i<79;i++)frame(true);
        const last=frame(true);
        function region(a,b,box){
          let changed=0,total=0,sum=0;
          for(let y=Math.floor(box[1]*canvas.height);y<Math.floor(box[3]*canvas.height);y++){
            for(let x=Math.floor(box[0]*canvas.width);x<Math.floor(box[2]*canvas.width);x++){
              const offset=(y*canvas.width+x)*4;
              const delta=(Math.abs(a[offset]-b[offset])+Math.abs(a[offset+1]-b[offset+1])+Math.abs(a[offset+2]-b[offset+2]))/3;
              if(delta>=3)changed++;
              sum+=delta;total++;
            }
          }
          return {changedFraction:changed/total,meanDifference:sum/total};
        }
        const fixed=mode==='lake-trees'?[[.4,.48,.47,.64],[.78,.42,.85,.55],[.2,.71,.3,.76]]:
          [[.45,.5,.65,.62],[.27,.58,.35,.65],[.03,.75,.13,.86],[.91,.55,.98,.75],[.76,.52,.82,.56]];
        const water=mode==='lake-trees'?[.2,.83,.8,.97]:[.5,.79,.7,.94];
        const moving={sky:region(first,last,[.12,.05,.82,.22]),water:region(first,last,water)};
        const stationary=fixed.map(box=>region(first,last,box));
        const paused=frame(false);
        const pause=region(last,paused,[0,0,1,1]);
        return {moving,stationary,pause,time:TrainingScenes.diagnostics().time};
      },mode);
      assert.ok(result.moving.sky.changedFraction>.1,mode+' cloud movement should be visible');
      assert.ok(result.moving.water.changedFraction>.1,mode+' water movement should be visible');
      assert.ok(result.stationary.every(region=>region.meanDifference===0),mode+' solid objects must stay fixed: '+JSON.stringify(result.stationary));
      assert.equal(result.pause.meanDifference,0,mode+' pause freezes current frame');
      results.scenes[mode]=result;
      await page.screenshot({path:path.join(output,mode+'.png')});
    }
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.goto(base+'/scene-check');
    await page.waitForFunction(()=>Object.values(TrainingScenes.diagnostics().assets).every(asset=>asset.loaded&&asset.animated));
    const staticTime=await page.evaluate(()=>{
      const c=document.querySelector('#scene'),ctx=c.getContext('2d');
      for(let i=0;i<100;i++)TrainingScenes.draw(ctx,c.width,c.height,{now:i*100,motion:true});
      return TrainingScenes.diagnostics().time;
    });
    assert.equal(staticTime,0,'Reduced motion disables scene animation');
    results.reducedMotion=true;
    results.frameRates={};
    for(const [device,cores,target] of [['normal',8,30],['lowPower',4,24]]){
      const ratePage=await browser.newPage();
      await ratePage.addInitScript(cores=>{
        Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>cores});
        Object.defineProperty(navigator,'deviceMemory',{get:()=>cores});
        window.sceneDrawCount=0;
        const draw=WebGLRenderingContext.prototype.drawArrays;
        WebGLRenderingContext.prototype.drawArrays=function(...args){window.sceneDrawCount++;return draw.apply(this,args);};
      },cores);
      await ratePage.goto(base+'/scene-check');
      await ratePage.waitForFunction(()=>Object.values(TrainingScenes.diagnostics().assets).every(asset=>asset.loaded&&asset.animated));
      const frames=await ratePage.evaluate(()=>{
        const c=document.querySelector('#scene'),ctx=c.getContext('2d');
        TrainingScenes.draw(ctx,c.width,c.height,{now:0});
        const before=sceneDrawCount;
        for(let i=1;i<=30;i++)TrainingScenes.draw(ctx,c.width,c.height,{now:i*1000/30});
        const moving=sceneDrawCount-before,pauseStart=sceneDrawCount;
        for(let i=31;i<=60;i++)TrainingScenes.draw(ctx,c.width,c.height,{now:i*1000/30,motion:false});
        const paused=sceneDrawCount-pauseStart,resumeStart=sceneDrawCount;
        for(let i=61;i<=90;i++)TrainingScenes.draw(ctx,c.width,c.height,{now:i*1000/30});
        return {moving,paused,resumed:sceneDrawCount-resumeStart};
      });
      assert.equal(frames.moving,target,device+' keeps frame scheduling remainder');
      assert.equal(frames.paused,0,device+' pause reuses frozen render');
      assert.equal(frames.resumed,target,device+' resumes normal frame cadence');
      results.frameRates[device]=frames;
      await ratePage.close();
    }
    await page.emulateMedia({reducedMotion:'no-preference'});
    await page.route('**/api/**',route=>{
      assert.equal(route.request().method(),'GET','This check must not write sessions');
      return route.fulfill({contentType:'application/json',body:JSON.stringify({phase:'idle',seq:1,mode:'synthetic',presentation:{},connection:{},quality:{valid:false}})});
    });
    await page.addInitScript(()=>{
      localStorage.setItem('chengsi-resonance-preferences',JSON.stringify({programId:'easy-going',score:40,volume:.35}));
      window.WebSocket=class{constructor(){setTimeout(()=>this.onopen?.(),10);}close(){}};
    });
    await page.goto(base+'/');
    await page.waitForFunction(()=>window.resonanceDiagnostics?.().programId==='easy-going');
    assert.equal(await page.locator('.program-select option[value="easy-going"]').count(),1);
    await page.getByRole('button',{name:'开启声音',exact:true}).click();
    await page.waitForFunction(()=>resonanceDiagnostics().audio.ready,{},{timeout:90000});
    const initial=await page.evaluate(()=>resonanceDiagnostics().audio);
    assert.equal(initial.feedbackProfile,'easy-going-continuous-v2');
    assert.equal(initial.activeSourceCount,3);
    assert.equal(initial.cycleDuration,60);
    results.audio={initial,levels:[]};
    for(const [score,expected] of [[40,[1,0,0]],[55,[1,1,0]],[70,[1,1,1]]]){
      await page.locator('.preset-card[data-score="'+score+'"]').click();
      await page.waitForFunction(expected=>resonanceDiagnostics().audio.levels.every((level,i)=>Math.abs(level-expected[i])<.01),expected,{timeout:12000});
      const audio=await page.evaluate(()=>resonanceDiagnostics().audio);
      assert.equal(audio.activeSourceCount,3,'Gain changes must preserve simultaneous looping sources');
      results.audio.levels.push({score,levels:audio.levels});
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify(results,null,2)+'\n');
    console.log(JSON.stringify({scenes:results.scenes,reducedMotion:true,frameRates:results.frameRates,audio:{cycleDuration:60,sourceCount:3,levels:results.audio.levels}},null,2));
  }finally{
    await browser?.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
