// Exercise browser audio transitions without a camera or loudspeaker.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),tones=[],spoken=[],timers=[],clearedIntervals=[];let closedAudio=0,reloaded=0,canceledSpeech=0;
const el=id=>{if(!elements.has(id))elements.set(id,{dataset:{},value:'',textContent:'',hidden:false,open:false,innerHTML:'',replaceChildren(){},listeners:{},addEventListener(name,fn){this.listeners[name]=fn},getContext(){return {clearRect(){}}},querySelectorAll(){return []},add(){},setAttribute(){},showModal(){this.open=true},close(){this.open=false}});return elements.get(id)};
const context={document:{listeners:{},querySelector:el,createElement:()=>({}),addEventListener(name,fn){this.listeners[name]=fn}},window:{listeners:{},addEventListener(name,fn){this.listeners[name]=fn},location:{replace(path){assert.equal(path,'/');reloaded++}}},localStorage:{getItem(){return this.owner},setItem(k,v){this.owner=v},removeItem(){this.owner=null}},Date,Math,Option:function(){},AbortController,setInterval(){return Math.random()},clearInterval(id){clearedIntervals.push(id)},setTimeout(fn){timers.push(fn);return timers.length},clearTimeout(){},fetch:()=>new Promise(()=>{}),Image:function(){},SpeechSynthesisUtterance:function(t){this.text=t}};
const synthesis={getVoices:()=>[],addEventListener(){},cancel(){canceledSpeech++},speak(u){spoken.push(u.text)},speaking:false};
context.speechSynthesis=context.window.speechSynthesis=synthesis;context.window.SpeechSynthesisUtterance=context.SpeechSynthesisUtterance;
context.window.AudioContext=function(){this.state='running';this.currentTime=0;this.destination={};this.resume=()=>{this.state='running';return Promise.resolve()};this.suspend=()=>{this.state='suspended';return Promise.resolve()};this.close=()=>{closedAudio++;this.state='closed';return Promise.resolve()};this.createOscillator=()=>({frequency:{set value(v){tones.push(v)}},connect(target){return target},start(){},stop(){}});this.createGain=()=>({gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}})};
vm.createContext(context);const html=fs.readFileSync(__dirname+'/results/lite-live/index.html','utf8');vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const run=s=>vm.runInContext(s,context);
(async()=>{
const base={session:'test',difficulty:'normal',fresh:true,state:'player_turn',message:'Your turn.',errors:[],events:[],event_id:0,board_svg:'',expected_svg:'',history:[],camera_phase:'stable',player:'white',computer:'black'};
context.fixture=base;run('playing=true;show(fixture);enable()');await Promise.resolve();assert.deepEqual(tones,[1320,1760]);tones.length=0;
context.fixture={...base,event_id:1,events:[{id:1,kind:'correct'}]};run('show(fixture)');assert.deepEqual(tones,[1320,1760]);timers.pop()();assert.equal(spoken.pop(),'Your turn.');
context.fixture={...base,state:'computer_move',pending:'g1f3',message:'White knight, g1 to f3.',event_id:2};run('show(fixture)');assert.equal(spoken.pop(),'White knight, g 1 to f 3.');run('reminderAt=0;remind()');assert.equal(spoken.pop(),'White knight, g 1 to f 3.');
context.fixture={...context.fixture,errors:['g1: detected White knight; expected empty'],event_id:3};run('show(fixture)');assert.deepEqual(tones.slice(-2),[880,880]);assert.equal(spoken.length,0);const count=tones.length;run('show(fixture);remind()');assert.equal(tones.length,count);run('errorReminderAt=0;remind()');assert.equal(tones.length,count+2);assert.equal(spoken.length,0);
context.fixture={...base,fresh:false,pending:'g1f3'};run('show(fixture);reminderAt=0;remind()');assert.equal(spoken.length,0);
console.log('PASS: success chime + turn, default English voice, move repetition, silent errors, error deduplication, stale-camera silence');

const beforeMute=tones.length;run("$('#voice').onclick();ding();speak('Muted')");assert.equal(tones.length,beforeMute);assert.equal(spoken.length,0);assert.match(el('#voice').innerHTML,/m16 9/);run("$('#voice').onclick()");await Promise.resolve();assert.equal(tones.length,beforeMute+2);assert.match(el('#voice').innerHTML,/M15 8/);console.log('PASS: enable feedback, mute silence, speaker states');run("localStorage.setItem('chess-voice-owner','other-page');window.listeners.storage({key:'chess-voice-owner'})");assert.equal(run('voice'),false);assert.equal(run('muted'),true);assert.equal(run('audio.state'),'suspended');const lostCount=spoken.length;run("speak('Old page')");assert.equal(spoken.length,lostCount);
run("$('#voice').onclick()");await Promise.resolve();run("document.hidden=true;document.listeners.visibilitychange()");assert.equal(run('voice'),false);assert.equal(run('muted'),false);assert.equal(context.localStorage.owner,null);run('document.hidden=false;document.listeners.visibilitychange()');assert.equal(run('voice'),true);run('mutePlayback();document.listeners.visibilitychange()');assert.equal(run('voice'),false);
run("$('#voice').onclick()");await Promise.resolve();run("$('#voice').onclick()");assert.equal(context.localStorage.owner,null);assert.equal(run('audio.state'),'suspended');console.log('PASS: ownership loss stays muted, background page stops, mute releases all same-origin pages');
const homeCount=tones.length;run('playing=false;errorBeep();ding();remind()');assert.equal(tones.length,homeCount);assert.equal(spoken.length,0);console.log('PASS: error reminders every three seconds and no audio on home screen');
context.fixture={...base,orientation_status:'mismatch',orientation_detail:'Board flipped. Turn it back to its original direction, or click Restart Game.',camera_phase:'orienting'};
run('playing=true;show(fixture)');assert.equal(el('#orientation-confirm').open,true);assert.equal(el('#state').textContent,'Board flipped');assert.match(el('#board-notice').textContent,/Restart Game/);assert.equal(el('#board-notice').className,'error');
run('transitionPending="new";$("#orientation-confirm").close();show(fixture)');assert.equal(el('#orientation-confirm').open,false);run('transitionPending=null');
context.fixture={...base,orientation_status:'detected'};run('show(fixture)');assert.equal(el('#orientation-confirm').open,false);assert.equal(el('#state').textContent,'Your turn');
console.log('PASS: flip dialog, blocked-turn label, restart flow, restored-board dismissal');
// Verify the destructive transitions require the chosen save decision.
const writes=[];let failSave=false;
context.fixture={...base,can_save:true,session:'reviewable-game'};
context.fetch=async(path,options={})=>{
  if(options.method==='POST')writes.push(path);
  if(path==='/api/save-game'&&failSave)return {ok:false,json:async()=>({error:'Disk full'})};
  const result=path==='/api/library'?{games:[],can_resume:true}:path==='/api/exit-game'?{stopping:true}:path==='/api/save-game'?{saved_id:'saved'}:context.fixture;
  return {ok:true,json:async()=>result};
};
run("libraryPromise=null;playing=true;voice=false;show(fixture);askToSave('new');$('#save-cancel').onclick()");
assert.equal(writes.length,0);assert.equal(run('current.session'),'reviewable-game');
run("askToSave('new');flowBusy=true");let prevented=false;el('#save-dialog').listeners.cancel({preventDefault(){prevented=true}});assert.equal(prevented,true);assert.equal(run('transitionPending'),'new');run('flowBusy=false');
failSave=true;await run("$('#save-yes').onclick()");assert.deepEqual(writes,['/api/save-game']);assert.equal(run('current.session'),'reviewable-game');assert.equal(run('transitionPending'),'new');assert.equal(el('#save-feedback').textContent,'Disk full');
failSave=false;await run("$('#save-yes').onclick()");assert.deepEqual(writes.slice(1),['/api/save-game','/api/new-game']);
run("askToSave('exit')");await run("$('#save-no').onclick()");assert.equal(writes.at(-1),'/api/exit-game');assert.equal(run('playing'),false);assert.equal(writes.filter(p=>p==='/api/save-game').length,2);
assert.equal(reloaded,1);assert.equal(closedAudio,1);assert.equal(clearedIntervals.length,4);assert.equal(run('audio'),null);assert.equal(run('voice'),false);const speechBefore=spoken.length;run("remind();speak('Residual speech');ding()");assert.equal(spoken.length,speechBefore);console.log('PASS: exit closes audio, clears all polling/reminder timers, suppresses residual speech and reloads home');
// A true application quit uses the shutdown endpoint, rather than the home endpoint.
context.fixture={...base,can_save:true,session:'quit-game'};
run('playing=true;flowBusy=false;current=fixture');run("askToSave('quit')");await run("$('#save-no').onclick()");
assert.equal(writes.at(-1),'/api/quit-app');assert.equal(run('playing'),false);assert.equal(reloaded,1);
assert.equal(el('#start-game').disabled,true);assert.match(el('#home-note').textContent,/closed/);
console.log('PASS: Quit Application stops playback and leaves a closed page, without restarting the service');
context.fixture={...base,fresh:false,session:'default-sound'};
for(const path of ['/api/start-game','/api/resume-game','/api/load-game','/api/start-endgame']){
  run('muted=true;voice=false;playing=false;flowBusy=false');
  await run('enterGame('+JSON.stringify(path)+')');
  assert.equal(run('voice'),true);assert.equal(run('muted'),false);assert.equal(run('audio.state'),'running');
}
console.log('PASS: all game entry points default to sound on after a previous mute');
console.log('PASS: Cancel preserves game, saving blocks accidental cancellation, failed save prevents reset, successful save precedes reset, Dont Save exits without writing');

})().catch(e=>{console.error(e);process.exitCode=1});
