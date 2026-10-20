// Pure JavaScript unit checks: fake network and DOM, no browser or user data.
const vm=require('vm'),fs=require('fs'),assert=require('assert/strict');
const code=fs.readFileSync('app/static/app.js','utf8');
const extract=(name,next)=>code.slice(code.indexOf('async function '+name+'('),code.indexOf('async function '+next+'('));
async function apiChecks(){
 let expire,cleared=0;
 const context={AbortController,Error,TypeError,setTimeout:cb=>(expire=cb,1),clearTimeout:()=>cleared++,fetch:async()=>({ok:true,json:async()=>({ok:true})})};
 vm.createContext(context);vm.runInContext(extract('api','action'),context);
 assert.equal((await context.api('/synthetic')).ok,true);assert.equal(cleared,1);
 context.fetch=async()=>({ok:false,status:503,json:async()=>{throw Error('private HTML')}});
 await assert.rejects(()=>context.api('/synthetic'),/本地程序响应异常/);
 context.fetch=async()=>{throw new TypeError('private network trace')};
 await assert.rejects(()=>context.api('/synthetic'),/确认启动窗口/);
 context.fetch=async(p,opts)=>new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(Error('aborted'))));
 const pending=context.api('/synthetic');expire();await assert.rejects(()=>pending,/任务可能仍在运行/);
}
async function orderChecks(){
 const nodes={},replies=[];
 const context={ordersRequest:0,state:{groups:['甲群'],scans:[]},data:[],bucket:'matched',selected:null,
   api:()=>new Promise(resolve=>replies.push(resolve)),savedCounts:()=>({processed:1}),table:rows=>rows.map(o=>o.id).join(','),
   $:s=>nodes[s]||(nodes[s]={}),empty:()=>'',renderDetail:()=>{},JSON};
 vm.createContext(context);vm.runInContext(extract('loadOrders','loadFavorites'),context);
 const first=context.loadOrders(),second=context.loadOrders();
 replies[1]({orders:[{id:'new',match:{bucket:'matched'}}]});await second;
 replies[0]({orders:[{id:'old',match:{bucket:'matched'}}]});await first;
 assert.equal(context.data[0].id,'new');assert.equal(nodes['#orders-content'].innerHTML,'new');
 const scoped=context.loadOrders();context.state.groups=['乙群'];replies[2]({orders:[{id:'wrong-source',match:{bucket:'matched'}}]});await scoped;
 assert.equal(context.data[0].id,'new');
}
(async()=>{await apiChecks();await orderChecks();console.log(JSON.stringify({ok:true,checks:['finite_request_wait','invalid_response_recovery','offline_message','mutation_timeout_no_auto_retry','late_response_ignored','group_scope_checked']}))})().catch(error=>{console.error(error);process.exitCode=1});
