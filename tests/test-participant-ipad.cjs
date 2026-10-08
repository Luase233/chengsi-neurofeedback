'use strict';
const assert=require('node:assert/strict');
const test=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../participant.js'),'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function participant({pair=null,pairResponse=null,snapshot:initial={},broadcast=true,storage,viewport={width:810,height:1080}}={}){
  const requests=[],sockets=[],events=[],historyChanges=[],intervals=[];
  let now=100,animationFrame,reloads=0;
  const snapshot={session_id:'session-a',seq:1,phase:'connected',mode:'synthetic',
    sent_at:'2000-01-01T00:00:00Z',quality:{valid:true},feedback:{valid:true,score:70,age_ms:0},
    presentation:{dual_screen:true},...initial};
  const context2d=new Proxy({},{get:(_target,key)=>key==='measureText'?()=>({width:20}):()=>{}});
  const elements=new Map();
  function element(selector){
    if(elements.has(selector))return elements.get(selector);
    const listeners=new Map();
    const value={hidden:false,disabled:false,textContent:'',innerText:'',width:0,height:0,
      classList:{toggle(){}},setAttribute(){},getContext:()=>context2d,
      getBoundingClientRect:()=>({left:0,top:0,...viewport}),
      getClientRects:()=>[{}],closest:()=>null,querySelector:child=>element(selector+' '+child),
      addEventListener:(name,listener)=>listeners.set(name,listener),
      fire:(name,event={})=>listeners.get(name)?.(event),
      toDataURL:()=>'data:image/jpeg;base64,/9j/2Q=='};
    elements.set(selector,value);return value;
  }
  const documentEvents=new Map(),windowEvents=new Map();
  const document={hidden:false,body:{dataset:{}},documentElement:{},
    querySelector:element,createElement:()=>element('canvas-'+elements.size),
    addEventListener:(name,listener)=>documentEvents.set(name,listener)};
  const audioState={ready:false,playing:false,contextState:'uninitialized',programId:'clear-current-v04',levels:[0,0,0],energy:[0,0,0]};
  class Audio {
    static programs=[{id:'clear-current-v04'}];
    static isLayeredProgram=()=>false;
    getState(){return {...audioState};}
    setMuted(){} setScore(){} setVolume(){} selectProgram(){} dispose(){}
    start(){events.push('music-unlock');Object.assign(audioState,{ready:true,playing:true,contextState:'running'});return Promise.resolve();}
    resume(){if(audioState.failResume)return Promise.reject(new Error('Gesture required'));Object.assign(audioState,{playing:true,contextState:'running'});return Promise.resolve();}
    pause(){Object.assign(audioState,{playing:false,contextState:'suspended'});return Promise.resolve();}
  }
  const cueState={unlocked:false,state:'uninitialized',disposed:false,ended:{complete:0}};
  const cues={diagnostics:()=>({...cueState}),
    unlock:()=>{events.push('cue-unlock');Object.assign(cueState,{unlocked:true,state:'running'});return Promise.resolve(true);},
    cancel(){},dispose(){cueState.disposed=true;},observe:()=>[],playInstruction:()=>Promise.resolve(true)};
  const policyInputs=[];
  class Policy {
    result(){return {status:'inactive'};}
    clear(){}
    update(input){policyInputs.push(input);return {status:input.valid?'live':'inactive',audioScore:input.score,hasFeedback:input.valid};}
  }
  const location={href:'http://192.168.1.2:8765/participant.html'+(pair?'?pair='+pair:''),reload:()=>{reloads++;}};
  const sandbox={document,location,history:{replaceState:(_state,_unused,url)=>{
    historyChanges.push(url);location.href=new URL(url,location.href).href;
  }},crypto:{getRandomValues:bytes=>webcrypto.getRandomValues(bytes)},URL,Uint8Array,AbortController,Blob,
    navigator:{sendBeacon:()=>true},performance:{now:()=>now},devicePixelRatio:2,
    matchMedia:()=>({matches:true}),ResonanceAudio:Audio,ResonanceFeedbackPolicy:Policy,CalibrationCues:cues,
    TrainingScenes:{normalize:value=>value || 'lake-trees',isPhoto:()=>true,modes:[{id:'lake-trees',label:'湖岸'}],draw:()=>true,diagnostics:()=>({})},
    drawWearGuide(){},drawParticipantEye(){},drawResonance(){},
    getComputedStyle:()=>({opacity:'1',fontSize:'16',fontWeight:'400',color:'#fff',textAlign:'center'}),
    requestAnimationFrame:callback=>{animationFrame=callback;},
    setInterval:(callback,delay)=>{intervals.push({callback,delay});return intervals.length;},clearInterval(){},
    setTimeout:()=>1,clearTimeout(){},
    addEventListener:(name,listener)=>windowEvents.set(name,listener),
    fetch:async(url,options={})=>{
      const body=options.body?JSON.parse(options.body):undefined;requests.push({url,body});events.push(url);
      if(url==='/api/pair' && pairResponse)return pairResponse(requests.filter(request=>request.url==='/api/pair').length);
      if(url==='/api/presentation/heartbeat')snapshot.presentation.participant_client_id=body.client_id;
      return {ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(snapshot))};
    },
    WebSocket:class {constructor(url){this.url=String(url);sockets.push(this);events.push('websocket');}close(){}}
  };
  if(storage)sandbox.sessionStorage=storage;
  if(broadcast)sandbox.BroadcastChannel=class {addEventListener(){}postMessage(){}close(){}};
  sandbox.window=sandbox;vm.runInNewContext(source,sandbox,{filename:'participant.js'});
  return {requests,sockets,events,historyChanges,document,audioState,cueState,policyInputs,snapshot,
    diagnostic:()=>sandbox.participantDiagnostics(),element,
    click:selector=>element(selector).fire('click'),
    tick:async(delay)=>{for(const entry of intervals)if(entry.delay===delay)entry.callback();await flush();},
    frame:time=>{now=time;animationFrame(time);},
    receive:update=>{Object.assign(snapshot,update);sockets.at(-1).onmessage({data:JSON.stringify(snapshot)});},
    visibility:hidden=>{document.hidden=hidden;documentEvents.get('visibilitychange')();},
    pageEvent:(name,event={})=>windowEvents.get(name)?.(event),reloads:()=>reloads,
    previewSize:()=>({width:elements.get('canvas-2').width,height:elements.get('canvas-2').height})};
}

const tabStorage=()=>{
  const data=new Map();return {getItem:key=>data.get(key) ?? null,setItem:(key,value)=>data.set(key,value)};
};

test('LAN HTTP works without crypto.randomUUID and unlocks both audio contexts in the click',async()=>{
  const page=participant();await flush();
  assert.match(page.diagnostic().clientId,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(page.sockets[0].url,'ws://192.168.1.2:8765/ws/live');
  page.events.length=0;await page.click('.participant-enable');await flush();
  assert.deepEqual(page.events.slice(0,2),['cue-unlock','music-unlock']);
  assert.equal(page.diagnostic().entered,true);assert.equal(page.diagnostic().ready,true);
});

test('the participant identity survives a tab refresh and is separate in other tabs',async()=>{
  const storage=tabStorage();
  const first=participant({storage}),refreshed=participant({storage}),otherTab=participant({storage:tabStorage()});await flush();
  assert.equal(first.diagnostic().clientId,refreshed.diagnostic().clientId);
  assert.notEqual(first.diagnostic().clientId,otherTab.diagnostic().clientId);
});

test('denied or malformed session storage safely generates a fresh participant identity',async()=>{
  const denied={getItem(){throw new Error('SecurityError');},setItem(){throw new Error('SecurityError');}};
  const corrupt=tabStorage();corrupt.setItem('chengsi-participant-client-id','not-an-id');
  const privatePage=participant({storage:denied}),repairedPage=participant({storage:corrupt});await flush();
  for(const page of [privatePage,repairedPage]){
    assert.match(page.diagnostic().clientId,/^[0-9a-f-]{36}$/);
    assert.equal(page.diagnostic().transportReady,true);
  }
  assert.equal(corrupt.getItem('chengsi-participant-client-id'),repairedPage.diagnostic().clientId);
});

test('pairing completes before API, heartbeat or WebSocket access and removes the token',async()=>{
  let finish;
  const pending=new Promise(resolve=>{finish=resolve;});
  const page=participant({pair:'secret',pairResponse:()=>pending});
  await page.tick(3000);await page.tick(1000);
  assert.deepEqual(page.requests.map(item=>item.url),['/api/pair']);assert.equal(page.sockets.length,0);
  finish({ok:true,status:200,json:async()=>({paired:true})});await flush();
  assert.equal(page.requests[1].url,'/api/state');assert.equal(page.sockets.length,1);
  assert.deepEqual(page.historyChanges,['/participant.html']);assert.equal(page.diagnostic().transportReady,true);
});

test('a failed pair never opens a socket and a subsequent gesture can retry',async()=>{
  const page=participant({pair:'retry',pairResponse:async attempt=>({ok:attempt>1,status:attempt>1?200:401,
    json:async()=>attempt>1?{paired:true}:{detail:'expired'}})});await flush();
  assert.equal(page.diagnostic().transportReady,false);assert.equal(page.sockets.length,0);
  assert.match(page.element('.setup-status').textContent,/配对链接已失效/);
  await page.click('.participant-enable');await flush();
  assert.equal(page.diagnostic().transportReady,true);assert.equal(page.diagnostic().ready,true);
});

test('server guidance works across devices, is deduplicated and rejects other sessions',async()=>{
  const guidance={id:'guide-1',step:'headband',session_id:'session-a'};
  const page=participant({snapshot:{presentation:{dual_screen:true,guidance_request:guidance}}});await flush();
  assert.equal(page.diagnostic().visualStep,'headband');
  await page.click('.participant-enable');await flush();
  await page.click('.participant-confirm');await flush();
  assert.equal(page.diagnostic().visualStep,'headphones');
  page.receive({seq:2});assert.equal(page.diagnostic().visualStep,'headphones');
  page.receive({seq:3,presentation:{...page.snapshot.presentation,guidance_request:{id:'guide-2',step:'ready',session_id:'other'}}});
  assert.equal(page.diagnostic().visualStep,'headphones');
  page.receive({seq:4,presentation:{...page.snapshot.presentation,guidance_request:{id:'guide-3',step:'ready',session_id:'session-a'}}});
  assert.equal(page.diagnostic().visualStep,'ready');
});

test('training ignores a late wear instruction and freshness tolerates device clock skew',async()=>{
  const page=participant({snapshot:{phase:'training',mode:'live',
    feedback:{valid:true,score:70,age_ms:0,window_end:Date.parse('2000-01-01T00:00:00Z')/1000},
    presentation:{dual_screen:true,guidance_request:{id:'late',step:'headband',session_id:'session-a'}}}});await flush();
  assert.equal(page.diagnostic().visualStep,'waiting');
  assert.equal(page.policyInputs.at(-1).valid,true,'a correct server sample is fresh despite the iPad clock');
  page.frame(3000);await page.tick(200);
  assert.equal(page.policyInputs.at(-1).stale,true,'local monotonic time still expires a stalled snapshot');
});

test('claiming a new lease clears an already received ready instruction from the previous screen',async()=>{
  const page=participant({snapshot:{presentation:{dual_screen:true,
    guidance_request:{id:'old-owner-ready',step:'ready',session_id:'session-a'},
    wear_confirmation:{headband:true,headphones:true,client_id:'old-owner'}}}});await flush();
  assert.equal(page.diagnostic().visualStep,'ready');
  assert.equal(page.diagnostic().entered,false);
  // The first heartbeat claims this screen; the server withdraws old guidance
  // and confirmations before the user can enter the participant presentation.
  page.snapshot.presentation.guidance_request=null;
  page.snapshot.presentation.wear_confirmation={headband:false,headphones:false,client_id:null};
  await page.click('.participant-enable');await flush();
  assert.equal(page.diagnostic().entered,true);
  assert.equal(page.diagnostic().visualStep,'waiting');
  assert.equal(page.element('.participant-guide').hidden,true);
  page.receive({seq:2,presentation:{...page.snapshot.presentation,
    guidance_request:{id:'new-owner-headband',step:'headband',session_id:'session-a'}}});
  assert.equal(page.diagnostic().visualStep,'headband');
  assert.equal(page.element('.participant-confirm').hidden,false);
});

test('interrupted Safari audio is unready and can recover with a visible gesture button',async()=>{
  const page=participant();await flush();await page.click('.participant-enable');await flush();
  page.cueState.state='interrupted';page.audioState.contextState='interrupted';await page.tick(200);
  assert.equal(page.diagnostic().ready,false);assert.equal(page.element('.participant-resume').hidden,false);
  await page.click('.participant-resume');await flush();
  assert.equal(page.diagnostic().ready,true);assert.equal(page.element('.participant-resume').hidden,true);
  page.visibility(true);await flush();assert.equal(page.diagnostic().ready,false);
  assert.equal(page.requests.filter(item=>item.url==='/api/presentation/heartbeat').at(-1).body.visible,false);
});

test('a rejected automatic music resume also exposes the gesture recovery button',async()=>{
  const page=participant({snapshot:{phase:'training',presentation:{dual_screen:true,sound_enabled:true}}});
  await flush();await page.click('.participant-enable');await flush();
  Object.assign(page.audioState,{playing:false,contextState:'suspended',failResume:true});await page.tick(200);
  assert.equal(page.diagnostic().ready,false);assert.equal(page.element('.participant-resume').hidden,false);
  page.audioState.failResume=false;await page.click('.participant-resume');await flush();
  assert.equal(page.diagnostic().ready,true);assert.equal(page.element('.participant-resume').hidden,true);
});

test('remote preview works without BroadcastChannel, only while visible and at most every two seconds',async()=>{
  const page=participant({broadcast:false});await flush();page.frame(2100);
  assert.equal(page.requests.filter(item=>item.url==='/api/presentation/preview').length,0);
  await page.click('.participant-enable');await flush();page.frame(2200);await flush();
  let previews=page.requests.filter(item=>item.url==='/api/presentation/preview');assert.equal(previews.length,1);
  assert.equal(previews[0].body.client_id,page.diagnostic().clientId);assert.match(previews[0].body.image,/^data:image\/jpeg;base64,/);
  page.frame(3000);await flush();assert.equal(page.requests.filter(item=>item.url==='/api/presentation/preview').length,1);
  page.visibility(true);await flush();page.frame(5000);await flush();
  assert.equal(page.requests.filter(item=>item.url==='/api/presentation/preview').length,1);
});

test('full-screen iPad portrait previews fit the operator 480 by 640 bounds',async()=>{
  for(const viewport of [{width:820,height:1180},{width:1180,height:820}]){
    const page=participant({broadcast:false,viewport});await flush();await page.click('.participant-enable');await flush();
    page.frame(2200);await flush();const size=page.previewSize();
    assert.ok(size.width<=480 && size.height<=640);
    assert.ok(Math.abs(size.width/size.height-viewport.width/viewport.height)<.002);
    assert.equal(page.requests.filter(item=>item.url==='/api/presentation/preview').length,1);
  }
});

test('unsupported full screen gives Safari guidance and BFCache restores a fresh document',async()=>{
  const page=participant();await flush();await page.click('.participant-enable');await flush();
  assert.doesNotThrow(()=>page.click('.participant-fullscreen'));
  assert.match(page.element('.participant-status').textContent,/添加到主屏幕/);
  page.pageEvent('pagehide');page.pageEvent('pageshow',{persisted:true});assert.equal(page.reloads(),1);
});
