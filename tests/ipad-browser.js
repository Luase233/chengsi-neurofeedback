'use strict';
// Run against an isolated synthetic-data server. No iPad or Xcode required.
const {webkit,chromium,request}=require('playwright');
const engine=process.env.CHENGSI_BROWSER||'chromium';
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const base=process.env.CHENGSI_URL||'http://127.0.0.1:8778';
const out=process.env.CHENGSI_TEST_OUTPUT||'runtime/browser-checks';
(async()=>{
 await fs.mkdir(out,{recursive:true});
 const api=await request.newContext({baseURL:base});
 async function get(path){const r=await api.get(path);assert.equal(r.status(),200,await r.text());return r.json();}
 async function post(path,data){const r=await api.post(path,{data});assert.equal(r.status(),200,await r.text());return r.json();}
 const initial=await get('/api/state');
 assert.ok(['idle','completed','error'].includes(initial.phase),'Use an idle test server, not an active training session');
 const connection=await get('/api/connection');assert.ok(connection.participant_urls.length);
 const browser=await (engine==='webkit'?webkit.launch({headless:true}):chromium.launch({headless:true,channel:process.env.CHENGSI_CHROME_CHANNEL||'chrome'}));
 const operatorContext=await browser.newContext({viewport:{width:1440,height:1000}});
 const context=await browser.newContext({viewport:{width:810,height:1080},deviceScaleFactor:2,isMobile:true,hasTouch:true,reducedMotion:'reduce'});
 operatorContext.setDefaultTimeout(30000);context.setDefaultTimeout(30000);
 const operator=await operatorContext.newPage(),page=await context.newPage();
 const errors=[];for(const p of [operator,page])p.on('pageerror',e=>errors.push(e.message));
 let sid;
 try{
  console.log('Opening operator');await operator.goto(base+'/operator.html');console.log('Operator loaded');
  await operator.locator('.ipad-connection summary').click();
  console.log('Waiting QR');await operator.waitForFunction(()=>document.querySelector('.ipad-qr').naturalWidth>0);console.log('QR loaded');
  console.log('Opening participant');await page.goto(connection.participant_urls[0]);
  await page.waitForFunction(()=>window.participantDiagnostics?.().transportReady);
  assert.ok(!page.url().includes('pair='),'Pair token removed from address');
  assert.equal(await page.evaluate(()=>isSecureContext),false,'Actually exercise LAN HTTP');
  assert.equal(await page.evaluate(()=>typeof crypto.randomUUID),'undefined');
  console.log('PASS pairing and insecure-context startup');
  await page.locator('.participant-enable').tap();
  await page.waitForFunction(()=>window.participantDiagnostics().ready&&window.participantDiagnostics().lease,{},{timeout:30000});
  console.log('PASS actual Web Audio decode/unlock');
  if(await operator.locator('[data-action="new"]').isVisible())await operator.locator('[data-action="new"]').click();
  await operator.locator('[data-field="participant"]').fill('IPAD-E2E-'+Date.now());
  await operator.locator('[data-field="protocol"]').selectOption('single');
  // Use the real operator workflow, shortening only explicit synthetic test durations.
  await operator.route('**/api/sessions',async route=>{const req=route.request();if(req.method()!=='POST')return route.continue();const payload=req.postDataJSON();assert.equal(payload.mode,'synthetic');await route.continue({postData:JSON.stringify({...payload,calibration_seconds:5,training_seconds:15,protocol_plan:'single'})});});
  const created=operator.waitForResponse(r=>r.url().endsWith('/api/sessions')&&r.request().method()==='POST');
  await operator.locator('[data-action="connect"]').click();const createdResponse=await created;assert.equal(createdResponse.status(),200,await createdResponse.text());const s=await createdResponse.json();sid=s.session_id;

  const command=(command,extra={})=>post('/api/sessions/'+sid+'/commands',{command,command_id:randomUUID(),...extra});
  await command('set_presentation',{cue_mode:'voice',volume:0,sound_enabled:true});
  await page.waitForFunction(id=>window.participantDiagnostics().sessionId===id,sid);
  await operator.waitForFunction(()=>!document.querySelector('[data-guide-step="headband"]').disabled);
  await operator.locator('[data-guide-step="headband"]').click();
  await page.waitForFunction(()=>window.participantDiagnostics().visualStep==='headband');
  const sizes=[[810,1080],[1080,810],[820,1180],[1180,820],[834,1194],[1194,834],[1024,1366],[1366,1024],[1080,720]];
  for(const [width,height] of sizes){
   await page.setViewportSize({width,height});
   const layout=await page.evaluate(()=>{const button=document.querySelector('.participant-confirm'),r=button.getBoundingClientRect(),tools=document.querySelector('.participant-tools');return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom,iw:innerWidth,ih:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth,opacity:getComputedStyle(tools).opacity};});
   assert.ok(layout.x>=0&&layout.y>=0&&layout.right<=width+1&&layout.bottom<=height+1,JSON.stringify(layout));
   assert.ok(layout.height>=44&&!layout.overflow,JSON.stringify(layout));assert.ok(Number(layout.opacity)>=.7,JSON.stringify(layout));
   await page.screenshot({path:out+'/wear-'+width+'x'+height+'.png'});
  }
  console.log('PASS 9 iPad portrait/landscape/toolbar viewports');
  await page.setViewportSize({width:810,height:1080});
  await page.locator('.participant-confirm').tap();
  await operator.locator('[data-guide-step="headphones"]').click();
  await page.waitForFunction(()=>window.participantDiagnostics().visualStep==='headphones');
  await page.locator('.participant-confirm').tap();
  await operator.locator('[data-guide-step="ready"]').click();
  await page.waitForFunction(()=>window.participantDiagnostics().visualStep==='ready');
  await operator.waitForFunction(()=>document.querySelector('.operator-preview canvas').getAttribute('aria-label')==='被试端当前画面同步预览',{},{timeout:12000});
  console.log('PASS cross-origin wearing confirmations and remote thumbnail');
  async function waitState(predicate,timeout=60000){const end=Date.now()+timeout;let s;while(Date.now()<end){s=await get('/api/state');if(predicate(s))return s;await new Promise(r=>setTimeout(r,250));}throw Error('state timed out: '+JSON.stringify({phase:s.phase,presentation:s.presentation,quality:s.quality}));}
  await waitState(s=>s.quality.valid&&s.presentation.participant_ready);
  await operator.locator('[data-action="calibrate_closed"]').click();
  await waitState(s=>s.phase==='closed_complete');console.log('PASS spoken closed calibration cue, ack and completion');
  await waitState(s=>s.quality.valid&&s.presentation.participant_ready);
  await operator.locator('[data-action="calibrate_open"]').click();
  await waitState(s=>s.phase==='ready'&&s.presentation.participant_ready);console.log('PASS spoken open calibration cue and personal baseline');
  await operator.locator('[data-action="start_training"]').click();
  await waitState(s=>s.phase==='training'&&s.feedback.valid);
  await page.screenshot({path:out+'/training-ipad8.png'});
  await command('pause');await page.waitForFunction(()=>window.participantDiagnostics().phase==='paused');
  await context.setOffline(true);await page.waitForFunction(()=>!window.participantDiagnostics().lease,{},{timeout:8000});
  await context.setOffline(false);await page.waitForFunction(()=>window.participantDiagnostics().lease&&window.participantDiagnostics().ready,{},{timeout:15000});
  const resumed=await waitState(s=>s.presentation.participant_ready&&s.quality.valid);assert.equal(resumed.phase,'paused');
  await command('resume');await waitState(s=>s.phase==='completed');
  console.log('PASS pause, network interruption, reconnect and training completion');
  const exported=await api.get('/api/sessions/'+sid+'/export?edf=true');assert.equal(exported.status(),200,await exported.text());
  await fs.writeFile(out+'/synthetic-session.zip',await exported.body());
  await operator.screenshot({path:out+'/operator-results.png'});
  const history=await get('/api/participant-history?participant_id='+encodeURIComponent(s.participant_id)+'&mode=synthetic');assert.equal(history.entries.length,1);
  assert.deepEqual(errors,[]);
  const result={engine:engine+' desktop browser with touch and viewport emulation; not an iPad simulator',viewports:sizes,session:sid,checks:['LAN HTTP pairing','Web Audio WAV decoding and user gesture unlock','QR','wear guidance and confirmation','remote preview','voice cues and calibration','training','pause and network reconnect','saved history','EDF export'],errors};
  await fs.writeFile(out+'/result.json',JSON.stringify(result,null,2));
  console.log('PASS saved history and EDF; no browser errors');
 }catch(e){await page.screenshot({path:out+'/failure.png'}).catch(()=>{});console.log('diagnostics',await page.evaluate(()=>window.participantDiagnostics?.()).catch(()=>null));throw e;}
 finally{if(sid){const s=await get('/api/state').catch(()=>null);if(s&&!['completed','error','idle'].includes(s.phase))await post('/api/sessions/'+sid+'/commands',{command:'finish',command_id:randomUUID()}).catch(()=>{});}await browser.close();await api.dispose();}
})().catch(e=>{console.error(e);process.exitCode=1;});
