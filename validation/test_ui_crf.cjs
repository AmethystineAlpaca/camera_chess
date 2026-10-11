// Automatic CRF flow exposes only the existing state correction editor.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map();
const el=id=>{
  if(!elements.has(id))elements.set(id,{dataset:{},value:'',textContent:'',hidden:false,open:false,innerHTML:'',attributes:{},
    replaceChildren(...children){this.children=children},prepend(){},appendChild(){},add(){},addEventListener(){},getContext(){return {clearRect(){}}},querySelectorAll(){return []},querySelector(){return el('svg')},
    setAttribute(k,v){this.attributes[k]=v},showModal(){this.open=true},close(){this.open=false}});
  return elements.get(id);
};
const context={document:{querySelector:el,createElement:()=>el(Symbol()),addEventListener(){}},window:{addEventListener(){}},
  localStorage:{getItem(){},setItem(){},removeItem(){}},Date,Math,Option:function(label,value){this.textContent=label;this.value=value},AbortController,
  setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},fetch:()=>new Promise(()=>{}),Image:function(){}};
vm.createContext(context);
const html=fs.readFileSync(__dirname+'/results/lite-live/index.html','utf8');
vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const run=s=>vm.runInContext(s,context);
const base={recognition_frame:[17,1234.5],session:'crf-test',position_token:'same-turn',difficulty:'normal',fresh:true,state:'thinking',message:'I am thinking.',
  errors:[],events:[],event_id:1,board_svg:'accepted-board',correction_svg:'',expected_svg:'',history:[{uci:'e2e4'}],camera_phase:'stable',
  player:'white',computer:'black',raw_placement:'4k3/8/8/8/4P3/8/8/4K3',observed_placement:'4k3/8/8/8/4P3/8/8/4K3',
  recognition_postprocess:{status:'stable',operation:'legal_move'}};
const show=g=>{context.fixture=g;run('playing=true;show(fixture)')};
(async()=>{
  const choices=['small','large','feature_mlp','square_mlp','move_mlp1','move_mlp2','cnn','attention'].map(id=>({id,label:id+' (71.02% accuracy on evaluation set)'}));
  context.fetch=async()=>({ok:true,json:async()=>({recognition_model:'small',recognition_models:choices})});
  await run('openSettings()');
  assert.equal(el('#recognition-model').value,'small');
  assert.deepEqual(Array.from(el('#recognition-model').children,c=>c.value),choices.map(c=>c.id));
  assert.ok(el('#recognition-model').children.every(c=>c.textContent.includes('accuracy on evaluation set')));
  let modelRequest;
  context.fetch=async(path,options)=>{
    modelRequest={path,data:JSON.parse(options.body)};
    return {ok:true,json:async()=>({recognition_model:'attention'})};
  };
  el('#recognition-model').value='attention';
  await el('#recognition-model').onchange();
  assert.equal(modelRequest.path,'/api/settings');
  assert.equal(modelRequest.data.recognition_model,'attention');
  assert.equal(el('#recognition-model').disabled,false);
  context.fetch=async()=>({ok:false,json:async()=>({error:'Model unavailable'})});
  el('#recognition-model').value='small';
  await el('#recognition-model').onchange();
  assert.equal(el('#recognition-model').value,'attention');
  assert.equal(el('#model-feedback').textContent,'Model unavailable');
  run('displayRecognitionModel(null)');
  assert.equal(el('#recognition-model').disabled,true);
  for(const obsolete of ['id="confirm-recognition"','id="recognition-review"','/api/confirm-recognition','Confirm physical mistake'])assert.ok(!html.includes(obsolete));
  show(base);
  assert.equal(el('#board').innerHTML,'accepted-board');
  assert.equal(el('#fix').disabled,false);
  const writes=[];
  context.fetch=async(path,options)=>{
    writes.push({path,data:JSON.parse(options.body)});
    if(path==='/api/correction-context')return {ok:true,json:async()=>({...base,correction_id:'draft-1'})};
    if(path==='/api/page-heartbeat')return {ok:true,json:async()=>({running:true,focus_id:'same'})};
    return {ok:true};
  };
  await run('openFix("e4")');
  assert.equal(el('#correction').open,true);
  run('choosePiece(".");pageBusy=false');
  await run('pageHeartbeat()');
  const heartbeat=writes.find(w=>w.path==='/api/page-heartbeat');
  assert.equal(heartbeat.data.correction_id,'draft-1');
  assert.equal(heartbeat.data.session,base.session);
  show({...base,state:'computer_move',pending:'e7e5',event_id:2});
  assert.equal(el('#fix-live-status').textContent,'');
  assert.equal(run('fixDraft.e4'),'.');
  // Cancellation must release the edit lease; failures retain the open draft.
  context.fetch=async path=>path==='/api/diagnostics'?{ok:true}:{ok:false};
  await run('closeFix()');
  assert.equal(el('#correction').open,true);
  assert.match(el('#fix-feedback').textContent,/resume play/);
  context.fetch=async(path,options)=>{writes.push({path,data:JSON.parse(options.body)});return {ok:true}};
  await run('closeFix()');
  assert.equal(el('#correction').open,false);
  assert.equal(writes.filter(w=>w.path==='/api/end-correction').at(-1).data.correction_id,'draft-1');
  context.fetch=async(path,options)=>{writes.push({path,data:JSON.parse(options.body)});return {ok:true,json:async()=>({...base,correction_id:'draft-1'})}};
  await run('openFix("e4")');
  assert.equal(writes.filter(w=>w.path==='/api/correction-context').at(-1).data.correction_id,'draft-1');
  assert.equal(run('fixDraft.e4'),'.');
  console.log('PASS: automatic recognition UI, one correction entry, edit lease renewal/release, draft reopening');
})().catch(error=>{console.error(error);process.exitCode=1});
