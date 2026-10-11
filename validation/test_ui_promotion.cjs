// Exercise the actual browser script with camera snapshots and server responses.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map();
const el=id=>{
  if(!elements.has(id))elements.set(id,{dataset:{},value:'',textContent:'',hidden:false,open:false,innerHTML:'',attributes:{},
    replaceChildren(){},prepend(){},appendChild(){},addEventListener(){},getContext(){return {clearRect(){}}},querySelectorAll(){return []},add(){},
    setAttribute(k,v){this.attributes[k]=v},showModal(){this.open=true},close(){this.open=false}});
  return elements.get(id);
};
const context={document:{querySelector:el,createElement:()=>el(Symbol()),addEventListener(){}},window:{addEventListener(){}},
  localStorage:{getItem(){},setItem(){},removeItem(){}},Date,Math,Option:function(){},AbortController,
  setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},fetch:()=>new Promise(()=>{}),Image:function(){}};
vm.createContext(context);
const html=fs.readFileSync(__dirname+'/results/lite-live/index.html','utf8');
vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const run=s=>vm.runInContext(s,context);
const base={session:'promotion-test',difficulty:'normal',fresh:true,state:'player_turn',message:'Choose a promotion.',
  errors:[],events:[],event_id:1,board_svg:'',expected_svg:'',history:[],camera_phase:'stable',player:'white',computer:'black'};
const proposal={id:'choice-1',route:'a7a8',piece:'q',representation:'pawn',computer:false,color:'white',
  choices:['q','r','b','n'].map(piece=>({piece,pawn_ready:true,piece_ready:false}))};
const show=g=>{context.fixture=g;run('playing=true;show(fixture)')};

(async()=>{
  show({...base,promotion:proposal});
  assert.equal(el('#promotion-card').hidden,false);
  assert.equal(el('#confirm-promotion').disabled,false);
  assert.equal(el('#confirm-promotion').textContent,'Confirm queen · keep pawn');
  assert.match(el('#promotion-help').textContent,/No spare piece needed/);
  run("$('#promote-n').onclick()");
  show({...base,promotion:proposal,event_id:2});base.event_id=2;
  assert.equal(run('promotionPiece'),'n','camera polling preserves the user choice');
  assert.equal(el('#promote-n').attributes['aria-pressed'],'true');
  run("$('#promotion-piece').onclick()");
  assert.equal(el('#confirm-promotion').disabled,false);
  assert.match(el('#promotion-help').textContent,/white knight on A8/);
  show({...base,promotion:{...proposal,choices:proposal.choices.map(c=>({...c,piece_reason:'Check occupancy and physical piece on A8.'}))}});
  assert.match(el('#promotion-ready').textContent,/accepts this position manually/);
  assert.equal(el('#confirm-promotion').disabled,false);
  show({...base,promotion:{...proposal,choices:proposal.choices.map(c=>({...c,pawn_ready:false,piece_ready:c.piece==='n'}))}});
  assert.equal(el('#confirm-promotion').disabled,false);
  for(const override of [{fresh:false},{camera_phase:'settling'},{orientation_status:'mismatch'}]){
    show({...base,...override,promotion:proposal});
    assert.equal(el('#confirm-promotion').disabled,false);
  }
  // A new move starts with its own default, not a stale selection from the last one.
  show({...base,camera_phase:'uncertain',promotion:{...proposal,id:'choice-2'}});
  assert.equal(run('promotionPiece'),'q');assert.equal(run('promotionRepresentation'),'pawn');
  // A classifier's real-piece guess must not silently choose Replace piece.
  show({...base,promotion:{...proposal,id:'guessed-queen',representation:'piece'}});
  assert.equal(run('promotionRepresentation'),'pawn');
  run("$('#promote-r').onclick()");
  show({...base,promotion:{...proposal,id:'guessed-queen',representation:'piece'}});
  assert.equal(run('promotionPiece'),'r');
  assert.equal(run('promotionRepresentation'),'pawn');
  assert.equal(el('#confirm-promotion').textContent,'Confirm rook · keep pawn');
  const rookWrites=[];
  context.fetch=async(path,options)=>{rookWrites.push({path,data:JSON.parse(options.body)});return {ok:true,json:async()=>({...base,promotion:null})}};
  await run("$('#confirm-promotion').onclick()");
  assert.equal(rookWrites[0].path,'/api/confirm-promotion');
  assert.equal(rookWrites[0].data.piece,'r');
  assert.equal(rookWrites[0].data.representation,'pawn');
  show({...base,promotion:{...proposal,id:'explicit-replacement'}});
  run("$('#promote-r').onclick();$('#promotion-piece').onclick()");
  show({...base,promotion:{...proposal,id:'explicit-replacement'}});
  assert.equal(run('promotionRepresentation'),'piece','explicit physical choice survives polling');
  show({...base,camera_phase:'uncertain',promotion:{...proposal,id:'choice-2'}});
  const writes=[];
  let finishPoll;
  const old={...base,promotion:{...proposal,id:'choice-2'}};
  context.fetch=path=>path==='/api/game'?new Promise(resolve=>{finishPoll=resolve}):Promise.resolve({ok:true,json:async()=>({})});
  const poll=run('tick()');
  let finish;
  context.fetch=(path,options)=>{writes.push({path,data:JSON.parse(options.body)});return new Promise(resolve=>{finish=resolve})};
  const pending=run("$('#confirm-promotion').onclick()");
  assert.equal(el('#confirm-promotion').disabled,true);
  await run("$('#confirm-promotion').onclick()");
  assert.equal(writes.length,1,'double click sends one confirmation');
  assert.equal(writes[0].path,'/api/confirm-promotion');
  assert.equal(writes[0].data.promotion_id,'choice-2');
  assert.equal(writes[0].data.manual,true,'uncertain camera allows explicit manual confirmation');
  assert.equal(writes[0].data.piece,'q');assert.equal(writes[0].data.representation,'pawn');
  finish({ok:false,json:async()=>({error:'Board changed. Clear your hands.'})});await pending;
  assert.equal(el('#promotion-feedback').hidden,false);
  assert.equal(el('#promotion-feedback').textContent,'Board changed. Clear your hands.');
  assert.equal(el('#promotion-card').hidden,false);
  context.fetch=async()=>({ok:true,json:async()=>({...base,promotion:null,proxies:[{square:'a8',piece:'Q',name:'White queen'}]})});
  await run("$('#confirm-promotion').onclick()");
  assert.equal(el('#promotion-card').hidden,true);
  assert.match(el('#proxy-status').textContent,/White queen — pawn on A8/);
  finishPoll({ok:true,json:async()=>old});await poll;
  assert.equal(el('#promotion-card').hidden,true,'a delayed camera poll must not reopen a confirmed promotion');
  show({...base,proxies:[{square:'d5',piece:'Q',name:'White queen'},{square:'b8',piece:'R',name:'White rook'}]});
  assert.match(el('#proxy-status').textContent,/pawn on D5/);
  assert.match(el('#proxy-status').textContent,/pawn on B8/);
  show({...base,proxies:[]});assert.equal(el('#proxy-status').hidden,true);
  const computer={...proposal,id:'computer',computer:true,piece:'n',choices:[{piece:'n',pawn_ready:true,piece_ready:false}]};
  show({...base,state:'computer_move',pending:'a7a8n',promotion:computer});
  assert.equal(el('#promote-q').disabled,true);assert.equal(el('#promote-n').disabled,false);
  assert.match(el('#promotion-title').textContent,/Computer promotes to knight/);
  assert.equal(el('#confirm-promotion').textContent,'Confirm knight · keep pawn');
  console.log('PASS: promotion defaults, choice persistence, physical-piece matching, camera guards, fixed engine choice, duplicate-click prevention, failed confirmation, proxy position updates');
})().catch(e=>{console.error(e);process.exitCode=1});
