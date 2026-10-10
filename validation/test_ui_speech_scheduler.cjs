// Native speech starts/cancels asynchronously; never model cancel() as a synchronous stop.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
function harness(){
  let now=10000,id=0,cancels=0,maxVoices=0;const tasks=new Map(),elements=new Map(),requested=[],live=new Set();
  const el=k=>{if(!elements.has(k))elements.set(k,{value:'',dataset:{},open:false,add(){},replaceChildren(){},prepend(){},appendChild(){},setAttribute(){},addEventListener(){},querySelectorAll(){return []},showModal(){this.open=true},close(){this.open=false},getContext(){return {clearRect(){}}}});return elements.get(k)};
  const later=(fn,delay=0,interval=0)=>{tasks.set(++id,{fn,time:now+delay,interval});return id};
  const synthesis={speaking:false,pending:false,getVoices:()=>[],addEventListener(){},
    speak(u){requested.push(u.text);live.add(u);this.pending=true;maxVoices=Math.max(maxVoices,live.size);later(()=>{if(!live.has(u))return;this.pending=false;this.speaking=true;u.onstart?.();later(()=>{if(!live.delete(u))return;this.speaking=live.size>0;u.onend?.()},1800)},120)},
    cancel(){cancels++;const canceled=[...live];later(()=>{for(const u of canceled){if(live.delete(u))u.onerror?.({error:'interrupted'})}this.speaking=live.size>0;this.pending=false},180)}
  };
  const c={document:{hidden:false,listeners:{},querySelector:el,createElement:()=>el(Symbol()),addEventListener(k,f){this.listeners[k]=f}},window:{addEventListener(){},location:{}},localStorage:{getItem(){return this.owner},setItem(k,v){this.owner=v},removeItem(){this.owner=null}},Date:class extends Date{static now(){return now}},Math,Option:function(){},AbortController,Image:function(){},fetch:()=>new Promise(()=>{}),SpeechSynthesisUtterance:function(t){this.text=t},speechSynthesis:synthesis,setTimeout:(f,d)=>later(f,d),clearTimeout:i=>tasks.delete(i),setInterval:(f,d)=>later(f,d,d),clearInterval:i=>tasks.delete(i)};
  Object.assign(c.window,{speechSynthesis:synthesis,SpeechSynthesisUtterance:c.SpeechSynthesisUtterance});
  vm.createContext(c);const html=fs.readFileSync(process.env.CHESS_UI||__dirname+'/results/lite-live/index.html','utf8');vm.runInContext(html.split('<script>')[1].split('</script>')[0],c);
  const run=s=>vm.runInContext(s,c),advance=ms=>{const end=now+ms;for(;;){const next=[...tasks].filter(([,t])=>t.time<=end).sort((a,b)=>a[1].time-b[1].time)[0];if(!next)break;const [i,t]=next;now=t.time;if(t.interval)t.time+=t.interval;else tasks.delete(i);t.fn()}now=end};
  const base={session:'s',fen:'position-1',difficulty:'normal',fresh:true,state:'computer_move',pending:'b8a6',message:'Black knight, b8 to a6.',errors:[],events:[],event_id:1,board_svg:'',history:[],camera_phase:'stable'};
  const show=g=>{c.fixture=g;run('show(fixture)')};
  show(base);run('playing=true;enable(false)');show(base);run('sayCurrent()');
  return {run,show,advance,base,requested,get cancels(){return cancels},get maxVoices(){return maxVoices}};
}
const jitter=harness();jitter.advance(300);const before=jitter.cancels;
jitter.show({...jitter.base,event_id:2});
assert.equal(jitter.cancels,before,'An unrelated recognition event must not cancel the current move announcement');
jitter.advance(20000);assert.equal(jitter.requested.length,1,'Waiting for a physical move must not automatically repeat speech');
jitter.show({...jitter.base,event_id:1});assert.equal(jitter.run('eventId'),2,'Ignore an older response');
jitter.run("$('#repeat').onclick();$('#repeat').onclick()");jitter.advance(5000);assert.equal(jitter.requested.length,2,'Double click Repeat must schedule one intentional replay');
assert.equal(jitter.maxVoices,1);
console.log('PASS: recognition jitter, stale events, 20-second wait and double-click Repeat produce no duplicate requests');
const next=harness();next.advance(300);next.show({...next.base,fen:'position-2',pending:'g8h6',message:'Black knight, g8 to h6.',event_id:2});
assert.equal(next.requested.length,1,'Wait for native completion before submitting the next instruction');next.advance(2500);assert.equal(next.requested.length,2);assert.equal(next.maxVoices,1);
console.log('PASS: distinct instructions are serialized even while native speech is pending or speaking');
const cancel=harness();cancel.advance(300);cancel.run('mutePlayback();muted=false;enable(false);sayCurrent(true)');
assert.equal(cancel.requested.length,1,'Do not submit speech in the same turn as native cancellation');cancel.advance(3000);assert.equal(cancel.requested.length,2);assert.equal(cancel.maxVoices,1);
cancel.run('document.hidden=true;document.listeners.visibilitychange()');cancel.advance(5000);assert.equal(cancel.requested.length,2);
console.log('PASS: delayed native cancellation and backgrounding cannot overlap or restart speech');
const transient=harness();transient.advance(2500);transient.show({...transient.base,fresh:false});transient.advance(300);transient.show(transient.base);transient.advance(10000);assert.equal(transient.requested.length,1);
transient.show({...transient.base,pending_arrived:true,event_id:3});transient.advance(5000);assert.equal(transient.requested.length,1);
console.log('PASS: camera freshness recovery and completed-move previews do not repeat the move');
const entry=harness();entry.run('cancelSpeech();announcedInstructions.clear();flowBusy=true');entry.show({...entry.base,session:'new-game'});entry.advance(1000);const previous=entry.requested.length;entry.run('flowBusy=false');entry.advance(3000);assert.equal(entry.requested.length,previous+1);
console.log('PASS: game entry defers its first instruction until startup completes');

// Camera confidence is not a speech lifecycle event. The old implementation
// canceled during settling, then suppressed the canceled instruction forever.
const settling=harness();settling.advance(300);const cancelCount=settling.cancels;
for(const camera_phase of ['settling','uncertain','settling','stable']){
  settling.show({...settling.base,camera_phase,recognition_failed:true});settling.advance(300);
}
assert.equal(settling.cancels,cancelCount,'Confidence jitter must not interrupt a move announcement');
settling.advance(3000);assert.equal(settling.requested.length,1);
settling.show({...settling.base,fen:'next-uncertain-turn',camera_phase:'settling',recognition_failed:true,pending:'g8h6',message:'Black knight, g8 to h6.'});
settling.advance(2500);assert.equal(settling.requested.length,2);
assert.equal(settling.requested.at(-1),'Black knight, g 8 to h 6.');
settling.run("$('#repeat').onclick()");settling.advance(2500);assert.equal(settling.requested.length,3);
assert.equal(settling.maxVoices,1);
console.log('PASS: settling/uncertain/failure do not cancel or block known instructions, including Repeat');
