// Exercise entry dialogs through the production page's real handlers.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),writes=[];
const el=k=>{if(!elements.has(k))elements.set(k,{value:'',dataset:{},open:false,children:[],textContent:'',innerHTML:'',
  add(){},replaceChildren(...items){this.children=items},append(...items){this.children.push(...items)},appendChild(item){this.children.push(item)},prepend(){},
  setAttribute(){},addEventListener(){},querySelectorAll(){return []},showModal(){this.open=true},close(){this.open=false},getContext(){return {clearRect(){}}}});return elements.get(k)};
const c={document:{querySelector:el,createElement:()=>el(Symbol()),addEventListener(){}},window:{addEventListener(){}},
 localStorage:{getItem(){},setItem(){},removeItem(){}},Date,Math,Option:function(){},AbortController,Image:function(){},
 setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},fetch:()=>new Promise(()=>{})};
vm.createContext(c);const html=fs.readFileSync(__dirname+'/results/lite-live/index.html','utf8');vm.runInContext(html.split('<script>')[1].split('</script>')[0],c);
const run=s=>vm.runInContext(s,c);
(async()=>{
 assert.ok(!html.includes('id="board-near"'),'Home must not ask for a side');
 run("$('#endgame').onclick()");assert.equal(el('#choose-player').open,true);assert.equal(writes.length,0);assert.equal(el('#confirm-player').disabled,true);
 run("$('#cancel-player').onclick()");assert.equal(el('#choose-player').open,false);assert.equal(writes.length,0);
 c.fetch=async(path,options={})=>{
  if(options.method!=='POST')return new Promise(()=>{});
  const data=JSON.parse(options.body);writes.push({path,data});
  return {ok:true,json:async()=>({session:'entry-'+writes.length,event_id:0,events:[],difficulty:'normal',fresh:false,
   state:path==='/api/start-game'?'setup':path==='/api/start-endgame'?'endgame_scan':'reconcile',message:'Ready',
   errors:[],history:[],board_svg:'',camera_phase:'loading',player:data.player||'black',computer:data.player==='black'?'white':'black'})};
 };
 run("$('#endgame').onclick();$('#chosen-player').value='black';$('#chosen-player').onchange()");
 assert.equal(el('#confirm-player').disabled,false);
 const first=run("$('#confirm-player').onclick()");await run("$('#confirm-player').onclick()");await first;
 assert.equal(writes.length,1);assert.equal(writes[0].path,'/api/start-endgame');assert.equal(writes[0].data.player,'black');
 assert.equal(el('#choose-player').open,false);
 run('screen(false)');await run("$('#start-game').onclick()");
 assert.equal(writes[1].path,'/api/start-game');assert.equal(writes[1].data.player,undefined);assert.equal(writes[1].data.board_near,undefined);
 run("screen(false);renderSavedGames([{id:'saved',name:'Saved game',saved_at:1,mode:'endgame'}])");
 await el('#saved-list').children[0].children[0].onclick();
 assert.equal(writes[2].path,'/api/load-game');assert.equal(writes[2].data.id,'saved');assert.equal(writes[2].data.player,undefined);
 assert.equal(el('#choose-player').open,false,'Load uses saved player without another question');
 // Replaying camera confidence changes must not flash Endgame instructions.
 const scan={session:'endgame-status',event_id:0,events:[],difficulty:'normal',fresh:true,
   state:'endgame_scan',message:'Place your position. I will scan the board.',errors:[],history:[],
   board_svg:'',camera_phase:'settling',player:'white',computer:'black',side_known:true};
 for(const state of ['endgame_scan','endgame_ready']){
   for(const camera_phase of ['settling','uncertain','stable','unreadable','settling','stable']){
     c.fixture={...scan,state,camera_phase};run('playing=true;show(fixture)');
     assert.equal(el('#board-notice').textContent,'Check the recognized pieces. Use Fix recognition for mistakes.');
     assert.equal(el('#board-notice').className,'action');
     assert.equal(el('#confirm-position').disabled,state!=='endgame_ready'||camera_phase!=='stable');
   }
 }
 // Other play/load screens must keep their action through confidence jitter too.
 for(const [extra,text,kind] of [
   [{state:'setup'},'Set up the board.','action'],
   [{state:'player_turn'},'Your turn.','action'],
   [{state:'computer_move',pending:'c2d2',message:'Black rook, c2 to d2.'},'Black rook, c2 to d2.','action'],
   [{state:'reconcile',strict_recovery:true},'Match the saved board. Ghost = place; × = remove.','action'],
   [{state:'restore',errors:['d1: detected White queen; expected White rook']},'Check the red squares.','error'],
 ]){
   for(const camera_phase of ['stable','settling','uncertain','unreadable','stable']){
     c.fixture={...scan,...extra,camera_phase};run('show(fixture)');
     assert.equal(el('#board-notice').textContent,text);
     assert.equal(el('#board-notice').className,kind);
   }
 }
 console.log('PASS: normal play, pending moves, setup, loaded positions and correction instructions remain visible across noisy frames');
 c.fixture={...scan,fresh:false,camera_phase:'stale'};run('show(fixture)');
 assert.match(el('#board-notice').textContent,/Camera unavailable/);
 assert.equal(el('#confirm-position').disabled,true);
 console.log('PASS: Endgame instructions stay fixed across noisy frames; camera-disconnect warning and confirmation guards retained');
 console.log('PASS: no home side selector; Endgame asks before camera starts; cancel/duplicate protection; New Game auto; Load keeps saved side');
})().catch(e=>{console.error(e);process.exitCode=1});
