// Exercise browser audio transitions without a camera or loudspeaker.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),tones=[],spoken=[],timers=[],clearedIntervals=[];let closedAudio=0,reloaded=0,canceledSpeech=0,stoppedTones=0,now=10000;
class Clock extends Date {static now(){return now}}
const el=id=>{if(!elements.has(id))elements.set(id,{dataset:{},value:'',textContent:'',hidden:false,open:false,innerHTML:'',replaceChildren(){},prepend(){},appendChild(){},listeners:{},addEventListener(name,fn){this.listeners[name]=fn},getContext(){return {clearRect(){}}},querySelectorAll(){return []},add(){},setAttribute(){},showModal(){this.open=true},close(){this.open=false}});return elements.get(id)};
const context={document:{listeners:{},querySelector:el,createElement:()=>el(Symbol()),addEventListener(name,fn){this.listeners[name]=fn}},window:{listeners:{},addEventListener(name,fn){this.listeners[name]=fn},location:{replace(path){assert.equal(path,'/');reloaded++}}},localStorage:{getItem(){return this.owner},setItem(k,v){this.owner=v},removeItem(){this.owner=null}},Date:Clock,Math,Option:function(){},AbortController,setInterval(){return Math.random()},clearInterval(id){clearedIntervals.push(id)},setTimeout(fn){timers.push(fn);return timers.length},clearTimeout(){},fetch:()=>new Promise(()=>{}),Image:function(){},SpeechSynthesisUtterance:function(t){this.text=t}};
const synthesis={getVoices:()=>[],addEventListener(){},cancel(){canceledSpeech++},speak(u){spoken.push(u.text);u.onstart?.();u.onend?.()},speaking:false};
context.speechSynthesis=context.window.speechSynthesis=synthesis;context.window.SpeechSynthesisUtterance=context.SpeechSynthesisUtterance;
context.window.AudioContext=function(){this.state='running';this.currentTime=0;this.destination={};this.resume=()=>{this.state='running';return Promise.resolve()};this.suspend=()=>{this.state='suspended';return Promise.resolve()};this.close=()=>{closedAudio++;this.state='closed';return Promise.resolve()};this.createOscillator=()=>({frequency:{set value(v){tones.push(v)}},connect(target){return target},start(){},stop(at){if(at===undefined)stoppedTones++},disconnect(){}});this.createGain=()=>({gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}})};
vm.createContext(context);const html=fs.readFileSync(__dirname+'/results/lite-live/index.html','utf8');vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const run=s=>vm.runInContext(s,context);

(async()=>{
const base={session:'warning-test',difficulty:'normal',fresh:true,state:'player_turn',message:'Your turn.',errors:[],events:[],event_id:0,board_svg:'',expected_svg:'',history:[],camera_phase:'stable',player:'white',computer:'black'};
context.fixture=base;run('playing=true;show(fixture);enable(false)');await Promise.resolve();tones.length=0;spoken.length=0;
const message='Move recognized: king e1 to d2. The rook on d2 is protected by the bishop on c3. Your king cannot move into check. Return your king to e1. Put back the captured rook on d2. Then choose another move.';
now+=1000;context.fixture={...base,state:'restore',message,move_warning:{uci:'e1d2'},errors:['e1: detected empty; expected White king','d2: detected White king; expected Black rook'],event_id:1,events:[{id:1,kind:'move_warning'}]};
run('show(fixture)');
assert.equal(el('#board-notice').textContent,message);
assert.equal(el('#board-notice').className,'warning');
assert.match(el('#state').textContent,/Move recognized/);
assert.equal(tones.length,0);
assert.equal(spoken.length,1);
assert.match(spoken[0],/protected by the bishop/);
const cancels=canceledSpeech;now+=6000;
run('show(fixture);remind()');
assert.equal(spoken.length,1);assert.equal(tones.length,0);assert.equal(canceledSpeech,cancels);
run("$('#repeat').onclick()");assert.equal(spoken.length,2);
context.fixture={...context.fixture,fresh:false};run('show(fixture)');
assert.match(el('#board-notice').textContent,/Camera unavailable/);assert.equal(run('queuedSpeech'),null);
context.fixture={...base,event_id:2};run('show(fixture)');
assert.equal(el('#board-notice').textContent,'Your turn.');assert.equal(el('#board-notice').className,'action');
console.log('PASS: distinct move warning, spoken reason once, no error beep, repeat button, stale-camera silence, restored-board dismissal');
})().catch(e=>{console.error(e);process.exitCode=1});
