const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map();
function el(id){
 if(!elements.has(id))elements.set(id,{checked:true,hidden:false,disabled:false,textContent:'',dataset:{},width:1,height:1,draws:0,
  classList:{toggle(){}},addEventListener(name,fn){this[name]=fn},setAttribute(){},
  getContext(){const self=this;return {clearRect(){self.draws=0},drawImage(image){self.draws++;self.image=image.src}}}});
 return elements.get(id);
}
const context={document:{getElementById:el},window:{},AbortController,setTimeout,clearTimeout,Date,Promise,
 fetch:async()=>({ok:true,json:async()=>({rectify_board:true})}),
 Image:class {naturalWidth=768;naturalHeight=768;set src(value){this.source=value;this.onload()}get src(){return this.source}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname+'/results/lite-live/board-perspective.js','utf8'),context);
const api=context.window.BoardPerspective,flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 assert.equal(el('rectify-board').checked,true);assert.equal(el('rectified-view').hidden,false);
 let written;
 context.fetch=async(url,options)=>{written=JSON.parse(options.body);return {ok:true,json:async()=>({rectify_board:false})}};
 el('rectify-board').checked=false;await el('rectify-board').change();
 assert.equal(written.rectify_board,false);assert.equal(el('rectified-view').hidden,true);assert.equal(api.frame({}),false);
 context.fetch=async()=>({ok:false,json:async()=>({error:'Disk unavailable'})});
 el('rectify-board').checked=true;await el('rectify-board').change();
 assert.equal(el('rectify-board').checked,false);assert.equal(el('perspective-feedback').textContent,'Disk unavailable');
 api.settings({rectify_board:true});
 let now=Date.now()/1000;
 context.fetch=async()=>({ok:true,json:async()=>({capture_time:now,original:'source-bytes',model_input:'model-bytes',rectification:{enabled:true,applied:true,detail:'Corrected',state:'tracking'}})});
 assert.equal(api.frame({capture_time:now}),true);await flush();
 assert.equal(el('camera').image,'data:image/jpeg;base64,source-bytes');
 assert.equal(el('rectified-camera').image,'data:image/jpeg;base64,model-bytes');
 assert.equal(el('perspective-status').textContent,'Corrected');
 now+=.1;
 context.fetch=async()=>({ok:true,json:async()=>({capture_time:now,original:'raw',model_input:'raw',rectification:{enabled:true,applied:false,detail:'Using original',state:'searching'}})});
 api.frame({capture_time:now});await flush();
 assert.match(el('rectified-caption').textContent,/original fallback/);
 // In-flight response must not resurrect a stopped or disabled view.
 let resolve;
 context.fetch=()=>new Promise(r=>resolve=r);
 api.frame({capture_time:now+.1});api.stop();
 resolve({ok:true,json:async()=>({capture_time:now+.1,original:'old',model_input:'old',rectification:{enabled:true,applied:true}})});
 await flush();assert.equal(el('rectified-camera').draws,0);
 // Stale camera data is never presented as a fresh corrected view.
 api.frame({capture_time:Date.now()/1000-10});await flush();
 assert.match(el('perspective-status').textContent,/fresh frame/);
 assert.equal(el('rectified-camera').draws,0);
 console.log('PASS: perspective default, settings rollback, exact paired images, fallback, stale and late response guards');
})().catch(e=>{console.error(e);process.exit(1)});
