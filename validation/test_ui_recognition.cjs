// Submit the real editor handlers under label jitter, failed HTTP requests and duplicate clicks.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map();
const el=id=>{
  if(!elements.has(id))elements.set(id,{dataset:{},value:'',textContent:'',hidden:false,open:false,innerHTML:'',attributes:{},
    replaceChildren(...items){this.children=items},prepend(){},addEventListener(){},getContext(){return {clearRect(){}}},querySelectorAll(){return []},
    querySelector(){return el('svg')},appendChild(){},add(){},setAttribute(k,v){this.attributes[k]=v},showModal(){this.open=true},close(){this.open=false}});
  return elements.get(id);
};
const context={document:{querySelector:el,createElement:()=>el(Symbol()),addEventListener(){}},window:{addEventListener(){}},
  localStorage:{getItem(){},setItem(){},removeItem(){}},Date,Math,Option:function(){},AbortController,
  setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},fetch:()=>new Promise(()=>{}),Image:function(){}};
vm.createContext(context);
const html=fs.readFileSync(__dirname+'/results/lite-live/index.html','utf8');
vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const run=s=>vm.runInContext(s,context);
const base={recognition_frame:[17,1234.5],session:'recognition-test',position_token:'same-turn',difficulty:'normal',fresh:true,state:'computer_move',
  message:'Black rook, c2 to d2.',pending:'c2d2',errors:[],events:[],event_id:1,board_svg:'',correction_svg:'',
  expected_svg:'',history:[],camera_phase:'stable',player:'white',computer:'black',
  raw_placement:'4k3/8/8/8/8/8/3q4/4K3',observed_placement:'4k3/8/8/8/8/8/3q4/4K3'};
(async()=>{
const diagnostics=[];
context.fetch=async(path,options)=>{
  if(path==='/api/diagnostics'){diagnostics.push(JSON.parse(options.body));return {ok:true}}
  if(path==='/api/end-correction')return {ok:true};
  assert.equal(path,'/api/correction-context');
  const request=JSON.parse(options.body);assert.deepEqual(request.recognition_frame,base.recognition_frame);assert.equal(request.position_token,base.position_token);assert.equal(request.observed_placement,base.observed_placement);return {ok:true,json:async()=>({...base,correction_id:'test-context'})};
};
context.fixture=base;run('playing=true;show(fixture)');await run('openFix("d2")');run('choosePiece("r")');
assert.equal(run('fixDraft.d2'),'r');
context.fixture={...base,raw_placement:base.raw_placement.replace('3q','3n'),event_id:20};run('show(fixture)');base.event_id=20;
assert.equal(run('fixDraft.d2'),'r');assert.equal(run('fixSnapshot.raw_placement'),base.raw_placement);
assert.equal(el('#apply-fix').textContent,'Apply 1 correction');
run('$("#fix-square").value="e7";selectFix();choosePiece(".")');
assert.equal(el('#apply-fix').textContent,'Apply 2 corrections');
assert.equal(el('#fix-changes').children.length,2);
el('#fix-changes').children[1].onclick();assert.equal(run('fixDraft.e7'),undefined);
run('choosePiece(".")');assert.equal(el('#fix-changes').children.length,2);
await run('$("#cancel-fix").onclick()');assert.equal(el('#correction').open,false);
await run('openFix("d2")');assert.equal(run('fixDraft.d2'),'r');assert.equal(el('#fix-changes').children.length,2);
assert.ok(diagnostics.some(d=>d.event==='undo'));assert.ok(diagnostics.some(d=>d.event==='cancel'));

for(const color of ['white','black'])for(const piece of ['pawn','knight','bishop','rook','queen','king']){
  assert.ok(html.includes('id="palette-'+color+'-'+piece+'"'),'palette includes '+color+' '+piece);
}
  const writes=[];let finish;
  context.fetch=(path,options)=>{if(path==='/api/diagnostics'){diagnostics.push(JSON.parse(options.body));return Promise.resolve({ok:true})}writes.push({path,data:JSON.parse(options.body)});return new Promise(resolve=>finish=resolve)};
  const submit=run('$("#apply-fix").onclick()');await run('$("#apply-fix").onclick()');
  assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/correct-squares');
  assert.deepEqual(writes[0].data.changes,{d2:'r',e7:'.'});assert.equal(writes[0].data.position_token,'same-turn');assert.equal(writes[0].data.correction_id,'test-context');
  assert.equal(el('#apply-fix').disabled,true);
  finish({ok:false,json:async()=>({error:'Temporary connection error'})});await submit;
  assert.equal(el('#correction').open,true);assert.equal(run('fixDraft.d2'),'r');
  assert.equal(el('#fix-feedback').textContent,'Temporary connection error');
  assert.equal(el('#apply-fix').textContent,'Apply 2 corrections');
  context.fetch=async()=>({ok:true,json:async()=>({...base,pending_arrived:true})});
  await run('$("#apply-fix").onclick()');assert.equal(el('#correction').open,false);
  // An overlapping edit can reload the source; failed reloads keep the old draft.
  context.fetch=async()=>({ok:true,json:async()=>({...base,correction_id:'reload-before'})});
  await run('openFix("d2")');run('choosePiece("r")');
  context.fetch=async()=>({ok:false,json:async()=>({error:'Cannot fetch reading'})});
  await run('$("#refresh-fix").onclick()');
  assert.equal(run('fixDraft.d2'),'r');assert.equal(run('fixSnapshot.correction_id'),'reload-before');
  assert.equal(el('#correction').open,true);
  context.fetch=async()=>({ok:true,json:async()=>({...base,correction_id:'reload-after'})});
  await run('$("#refresh-fix").onclick()');
  assert.equal(run('Object.keys(fixDraft).length'),0);assert.equal(run('fixSnapshot.correction_id'),'reload-after');
  run('choosePiece("r")');
  let originalRequest,retryRequest;
  context.fetch=async(path,options)=>{originalRequest=JSON.parse(options.body);return {ok:true,json:async()=>({...base,correction_id:'reload-after',evidence_report:false,evidence_warning:'Disk full'})}};
  await run('$("#apply-fix").onclick()');
  assert.equal(el('#retry-fix-record').hidden,false);assert.equal(el('#correction').open,false);
  context.fetch=async(path,options)=>{retryRequest=JSON.parse(options.body);return {ok:true,json:async()=>({...base,correction_id:'reload-after',evidence_report:true,evidence_warning:null})}};
  await run('$("#retry-fix-record").onclick()');
  assert.deepEqual(retryRequest,originalRequest);
  assert.equal(el('#retry-fix-record').hidden,true);assert.equal(el('#fix-record-feedback').textContent,'Correction record saved.');
  console.log('PASS: complete palette, frozen editor, batch apply, jitter immunity, retry preservation, double-click guard');
})().catch(e=>{console.error(e);process.exitCode=1});
