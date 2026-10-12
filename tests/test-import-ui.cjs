'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),test=require('node:test');
const workspaceSource=fs.readFileSync(require.resolve('../operator-workspace.js'),'utf8');
const sessionSource=fs.readFileSync(require.resolve('../session-client.js'),'utf8');
function element(){
  const children=new Map();
  return {innerHTML:'',textContent:'',value:'',open:false,disabled:false,dataset:{},listeners:{},attributes:{},
    classList:{toggle(){}},setAttribute(key,value){this.attributes[key]=value;},after(){},appendChild(){},remove(){},
    addEventListener(name,listener){this.listeners[name]=listener;},click(){this.clicks=(this.clicks||0)+1;},
    close(){this.open=false;},showModal(){this.open=true;},scrollIntoView(){},focus(){this.focused=true;},
    querySelector(selector){if(!children.has(selector))children.set(selector,element());return children.get(selector);},querySelectorAll(){return [];}};
}
function fixture({phase='completed',serverPhase=phase,status='imported',baseline='restored',http=200,code,warnings=[]}={}){
  const root=element(),calls=[];
  const result={status,session_id:'imported-record',participant_id:'DEMO9',mode:'live',baseline_status:baseline,warnings};
  const response={ok:http>=200&&http<300,status:http,json:async()=>http===200?result:{code,detail:'INTERNAL C:/records/private/hash-abc'}};
  const sandbox={window:{},document:{createElement:element},setInterval:()=>1,clearInterval(){},console,performance:{now:()=>0},Date,
    fetch:async(path,options)=>{calls.push({path,options});return response;}};
  vm.createContext(sandbox);vm.runInContext(sessionSource,sandbox);vm.runInContext(workspaceSource,sandbox);
  const session=Object.assign(Object.create(sandbox.window.ResonanceSession.prototype),{root,q:s=>root.querySelector(s),online:true,busy:false,
    mode:'live',sessionId:'old-record',snapshot:{session_id:'old-record',participant_id:'OLD',phase},form:{participant:'OLD',replay:''},sessions:[],otherSession:null,
    emit(){},saveParticipant(){calls.push({action:'save-participant'});},command(){calls.push({action:'command'});},create(){calls.push({action:'create'});},
    request:async path=>{calls.push({path});if(path==='/api/state')return {phase:serverPhase};if(path==='/api/sessions')return {sessions:[{session_id:result.session_id,participant_id:result.participant_id,mode:'live'}]};if(path.startsWith('/api/participant-history'))return {entries:[]};throw Error('Unexpected request '+path);}});
  const workspace=Object.assign(Object.create(sandbox.window.ResonanceOperatorWorkspace.prototype),{session,root,q:s=>root.querySelector(s),dialog:element(),importInput:element(),importButton:element(),importHint:element(),
    inspect(title,html){this.inspectTitle=title;this.markup=html;this.dialog.open=true;}});
  return {session,workspace,result,calls,sandbox,root,file:{name:'training.zip',size:500,type:'application/zip'}};
}

test('selection and cancellation reset the picker so the same ZIP can be chosen again',()=>{
  const f=fixture(),workspace=new f.sandbox.window.ResonanceOperatorWorkspace(f.session),input=workspace.importInput;
  let chosen;workspace.importFile=file=>{chosen=file;};input.files=[f.file];input.value='previous.zip';
  input.listeners.change();assert.equal(chosen,f.file);assert.equal(input.value,'');
  input.value='previous.zip';input.listeners.cancel();assert.equal(input.value,'');
  workspace.chooseImport();workspace.chooseImport();assert.equal(input.clicks,2);assert.equal(input.value,'');
});
test('ordinary and EDF export ZIPs use binary local upload and show concrete restored identity',async()=>{
  for(const name of ['ordinary.zip','training-with-edf.ZIP']){
    const f=fixture();f.file.name=name;await f.workspace.importFile(f.file);
    const upload=f.calls.find(call=>call.path==='/api/sessions/import');
    assert.equal(upload.options.method,'POST');assert.equal(upload.options.headers['Content-Type'],'application/zip');assert.equal(upload.options.body,f.file);
    assert.match(f.workspace.markup,/DEMO9/);assert.match(f.workspace.markup,/实机数据/);assert.match(f.workspace.markup,/个人基线已恢复/);
    assert.match(f.workspace.markup,/查看训练记录/);assert.match(f.workspace.markup,/记录回放/);
    assert.ok(f.calls.some(call=>call.path==='/api/sessions'));assert.equal(f.session.form.participant,'OLD');
    assert.ok(!f.calls.some(call=>call.action==='command'||call.action==='create'));assert.equal(f.session.busy,false);
  }
});
test('active phases and another active session prevent import without ending training',async()=>{
  for(const phase of ['connecting','connected','ready','calibrating_closed','training','paused','resting','disconnected']){
    const f=fixture({phase});await f.workspace.importFile(f.file);assert.equal(f.calls.length,0);assert.match(f.workspace.markup,/尚未结束/);
  }
  const f=fixture();f.session.otherSession='other';await f.workspace.importFile(f.file);assert.equal(f.calls.length,0);
});
test('server phase is rechecked before upload, including stale local completed state',async()=>{
  const f=fixture({phase:'completed',serverPhase:'training'});let reconciled=false;f.session.reconcile=()=>{reconciled=true;};
  await f.workspace.importFile(f.file);assert.ok(reconciled);assert.equal(f.calls.length,1);assert.match(f.workspace.markup,/尚未结束/);assert.equal(f.session.busy,false);
});
test('busy import cannot be sent twice and its busy state always clears',async()=>{
  const f=fixture();let resolveState;f.session.request=path=>new Promise(resolve=>{resolveState=()=>resolve({phase:'idle'});});
  const pending=f.workspace.importFile(f.file);assert.equal(f.session.busy,true);assert.equal(f.workspace.importBusy,true);
  await f.workspace.importFile(f.file);assert.equal(f.calls.length,0);
  f.session.loadSessions=async()=>{f.session.sessions=[{session_id:f.result.session_id}];};resolveState();await pending;
  assert.equal(f.calls.filter(call=>call.path==='/api/sessions/import').length,1);assert.equal(f.session.busy,false);assert.equal(f.workspace.importBusy,false);
});
test('duplicate ZIP describes existing record without creating or overwriting a session',async()=>{
  const f=fixture({status:'already_imported',baseline:'existing'});await f.workspace.importFile(f.file);
  assert.match(f.workspace.markup,/无需再次导入/);assert.match(f.workspace.markup,/已有的个人基线/);assert.ok(!f.calls.some(call=>call.action==='command'||call.action==='create'));
});
test('baseline states and EDF reference warning have readable labels with no internal identifiers',async()=>{
  for(const [baseline,expected] of [['not_included',/不包含可恢复/],['unusable',/需要重新前测/]]){
    const f=fixture({baseline,warnings:['baseline_reference_only','C:/secret/private-hash']});await f.workspace.importFile(f.file);
    assert.match(f.workspace.markup,expected);assert.match(f.workspace.markup,/仅作为参考片段/);assert.doesNotMatch(f.workspace.markup,/secret|private-hash|imported-record/);
  }
});
test('conflict, corrupt archive, oversized ZIP and network errors do not expose server detail',async()=>{
  for(const [http,code,expected] of [[409,'active_session',/会话尚未结束/],[409,'session_conflict',/记录冲突/],[409,'baseline_conflict',/基线冲突/],[422,'invalid_archive',/数据已损坏/],[413,'archive_too_large',/太大/]]){
    const f=fixture({http,code});await f.workspace.importFile(f.file);assert.match(f.workspace.markup,expected);assert.doesNotMatch(f.workspace.markup,/INTERNAL|private|hash-abc/);assert.equal(f.session.busy,false);
  }
  const f=fixture();f.session.request=async()=>{throw Error('C:/private/hash-abc');};await f.workspace.importFile(f.file);assert.match(f.workspace.markup,/本地服务正常/);assert.doesNotMatch(f.workspace.markup,/private|hash-abc/);
});
test('empty, non-ZIP and over-limit selections do not send requests',async()=>{
  for(const file of [{name:'record.csv',size:10},{name:'record.zip',size:0},{name:'record.zip',size:64*1024*1024+1}]){
    const f=fixture();await f.workspace.importFile(file);assert.equal(f.calls.length,0);assert.equal(f.session.busy,false);
  }
});
test('imported history reads the imported participant and source without changing current mode',async()=>{
  const f=fixture();await f.workspace.showHistory({participantId:'DEMO9',mode:'synthetic'});
  assert.equal(f.calls[0].path,'/api/participant-history?participant_id=DEMO9&mode=synthetic');assert.match(f.workspace.markup,/DEMO9/);assert.equal(f.session.mode,'live');
});
test('replay entry selects imported record and participant but never loads or starts it automatically',async()=>{
  const f=fixture({phase:'error'});f.workspace.importResult=f.result;await f.workspace.prepareImportedReplay();
  assert.equal(f.session.mode,'replay');assert.equal(f.session.form.replay,f.result.session_id);assert.equal(f.session.form.participant,'DEMO9');
  assert.ok(!f.calls.some(call=>call.action==='command'||call.action==='create'));assert.equal(f.workspace.dialog.open,false);
});
test('replay entry and non-finishing mode switch reject active training',async()=>{
  const f=fixture({phase:'training'});f.workspace.importResult=f.result;await f.workspace.prepareImportedReplay();
  assert.equal(f.session.mode,'live');assert.equal(f.calls.length,0);
  const changed=await f.session.setMode('replay',{finishActive:false});assert.equal(changed,false);assert.equal(f.calls.length,0);assert.equal(f.session.sessionId,'old-record');
});
