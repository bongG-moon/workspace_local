import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Script,createContext} from 'node:vm';

const html=readFileSync(new URL('./diagnostic.html',import.meta.url),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script);new Script(script);
const events=[],listeners={},windowListeners={},nodes={};let time=0;
const state={innerWidth:1000,innerHeight:600,devicePixelRatio:1.5};
function element(id='') {return {id,style:{},className:'',innerHTML:'',textContent:'',children:[],classList:{toggle(){}},append(el){this.children.push(el);},querySelectorAll(){return this.children.filter(x=>x.className==='hit');},remove(){},getBoundingClientRect(){return{left:parseFloat(this.style.left)/100*state.innerWidth-27,top:parseFloat(this.style.top)/100*state.innerHeight-27,width:54,height:54};}};}
for(const id of ['board','hint','summary'])nodes[id]=element(id);
const context=createContext({...state,performance:{now:()=>++time},visualViewport:{scale:1},document:{getElementById:id=>nodes[id],createElement:()=>element(),addEventListener:(type,fn)=>listeners[type]=fn},window:{chrome:{webview:{postMessage:o=>events.push({...o})}},addEventListener:(type,fn)=>windowListeners[type]=fn}});
new Script(script).runInContext(context);
const api=context.window.Diagnostic;
const input=(type,extra={})=>listeners[type]({type,isTrusted:true,button:0,buttons:type==='pointerdown'?1:0,pointerId:1,detail:1,clientX:500,clientY:300,...extra});
const click=(x,y,extra={})=>{input('pointerdown',{clientX:x,clientY:y,...extra});input('pointerup',{clientX:x,clientY:y,...extra});input('click',{clientX:x,clientY:y,...extra});};
api.start(1);click(500,300);click(130,144);click(870,144);click(130,456);click(870,456);
assert.equal(events.at(-1).kind,'complete');assert.equal(events.at(-1).measured,5);
for(const e of events.filter(e=>e.kind==='click')){assert.equal(e.deltaX,0);assert.equal(e.deltaY,0);assert.equal(e.paired,true);}
api.start(2);input('click');assert.equal(events.at(-1).target,1);assert.equal(events.at(-1).paired,false);
input('pointerdown');input('pointercancel',{button:-1});assert.equal(events.at(-1).kind,'pointercancel');input('pointerup');input('click');assert.equal(events.at(-1).paired,false);
click(500,300,{detail:2});assert.equal(events.at(-1).duplicate,true);click(515,290);assert.equal(events.at(-1).target,1);assert.equal(events.at(-1).deltaX,15);assert.equal(events.at(-1).deltaY,-10);
// A stale up/click after a manual skip must not consume the next target.
input('pointerdown');api.skip();input('pointerup');input('click');assert.equal(events.at(-1).target,3);assert.equal(events.at(-1).paired,false);
click(-40,50);assert.equal(events.at(-1).target,3);assert.equal(events.at(-1).clientX,-40);
api.skip();api.skip();assert.equal(events.at(-1).kind,'complete');assert.equal(events.at(-1).measured,2);
api.start(3);windowListeners.resize();assert.equal(events.at(-1).kind,'interrupted');const count=events.length;click(500,300);assert.equal(events.length,count);
api.start(4);for(let i=0;i<1200;i++)input('pointerdown');assert.equal(events.filter(e=>e.round===4&&e.kind==='pointerdown').length,1000);
let prevented=false;listeners.keydown({key:'F8',ctrlKey:false,preventDefault(){prevented=true;}});assert.ok(prevented);assert.equal(events.at(-1).command,'skip');
console.log('PASS: page syntax, five-point coordinates, unpaired/cancel/duplicate input, skip pairing, outside-target capture, resize interruption, event cap and shortcuts.');
