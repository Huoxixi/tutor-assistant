'use strict';
const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={subject:'科目',grade:'年级',city:'城市',area:'区域/街道/地铁站',location:'教学地区',mode:'授课方式',hourly_rate:'最低时薪（元）',lesson_rate:'最低单次课酬（元）',monthly_rate:'最低月薪（元）',agency_fee:'最高中介费（固定元数）',weekly_sessions:'每周次数',duration_hours:'每次时长（小时）',term:'长期/短期',trial:'是否接受试课',schedule:'可授课时段',exclude_keywords:'排除关键词'};
const stateNames={complete:'读取范围已覆盖',incomplete:'采集不完整',cancelled:'已取消',queued:'等待中',reading:'正在读取',interrupted:'上次任务中断',running:'正在扫描'};
const readStateName=report=>['complete','incomplete'].includes(report.state)&&(report.count>0||report.groups?.some(g=>g.count>0))?'已读取':(stateNames[report.state]||report.state);
const messageNames={received:'等待解析',error:'解析失败',unparsed:'此类型未解析',processed:'已解析',ignored:'普通聊天'};
let state={},view='orders',bucket='matched',data=[],favoriteData=[],selected=null,timer=null;
let stopping=false,taskStarting=false,taskKind=null,authorizationSaving=false,ordersRequest=0,favoritesRequest=0,stateRequest=0,wechatSettingsOpen=false;
const favoriteSaving=new Set();
let regions=[],regionIndex={},selectedRegions=[],selectedGroups=new Set(),groupCatalog=[],lastCatalog='',groupSaving=false;
const subjects=['全科','作业辅导','语文','数学','英语','物理','化学','生物','历史','地理','政治','科学'];
const grades=['一年级','二年级','三年级','四年级','五年级','六年级','初一','初二','初三','高一','高二','高三'];
const fact=(order,key)=>order.facts[key]?.value;
const arr=value=>Array.isArray(value)?value.join('、'):value??'';
const split=value=>String(value).split(/[,，、;；\n]/).map(x=>x.trim()).filter(Boolean);
const formatTime=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'发布时间待确认';
const compactTime=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'时间待确认';
function uiIcon(name){
  const paths={location:'<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',person:'<circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3Z"/>',star:'<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.4-5.7-3-5.7 3 1.1-6.4-4.6-4.5 6.4-.9Z"/>',book:'<path d="M12 5C9 3 5 3 2 4v16c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1v16"/>',pay:'<rect x="4" y="7" width="16" height="14" rx="2"/><path d="M8 7V3h8v4M9 12l3 3 3-3M12 15v3"/>',filter:'<path d="M3 5h18l-7 8v7l-4-2v-5Z"/>'};
  return '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(paths[name]||paths.book)+'</svg>';
}
function favoriteButton(order){
  const enabled=Boolean(order.favorite_at),label=enabled?'取消收藏':'收藏';
  return '<button type="button" class="favorite-button" data-favorite="'+esc(order.id)+'" data-enabled="'+!enabled+'" aria-pressed="'+enabled+'" aria-label="'+label+'订单 '+esc(order.reference||'无单号')+'" title="'+label+'"'+(state.busy||taskStarting||favoriteSaving.has(order.id)?' disabled':'')+'>'+uiIcon('star')+(enabled?'已收藏':'收藏')+'</button>';
}
function priceMarkup(order){
  for(const [key,unit] of [['hourly_rate','元/小时'],['lesson_rate','元/次'],['monthly_rate','元/月']]){
    if(fact(order,key)!=null)return esc(pretty(fact(order,key)))+'<span class="money-unit">'+unit+'</span>';
  }
  return esc(price(order));
}
function syncTabs(){
  $$('[data-bucket]').forEach(b=>{
    const active=b.dataset.bucket===bucket;
    b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;
  });
}
function pretty(value){
  if(Array.isArray(value)){
    if(value.length&&typeof value[0]==='object')return value.map(v=>v.day?('周'+'一二三四五六日'[v.day-1]+' '+v.start+'–'+v.end):[v.teacher_type,v.education,v.hourly_rate!=null?v.hourly_rate+'元/小时':''].filter(Boolean).join(' ')).join('；');
    return value.join('、');
  }
  if(value&&typeof value==='object'){
    if(value.min!=null&&value.max!=null)return value.min===value.max?String(value.min):value.min+'–'+value.max;
    if(value.min!=null)return '≥'+value.min;
    if(value.max!=null)return '≤'+value.max;
    return JSON.stringify(value);
  }
  if(typeof value==='boolean')return value?'是':'否';
  return String(value??'未说明');
}
function notify(text,error=false){
  const el=$('#notice');el.hidden=false;el.className='toast'+(error?' error':'');
  el.setAttribute('role',error?'alert':'status');
  el.innerHTML='<span class="toast-message">'+(error?'<strong>操作未完成</strong>':'')+esc(text)+'</span><button type="button" class="toast-dismiss" aria-label="关闭提醒"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>';
  el.querySelector('.toast-dismiss').onclick=()=>{el.hidden=true};
  clearTimeout(timer);if(!error)timer=setTimeout(()=>{el.hidden=true},3200);
}
async function api(path,method='GET',body){
  const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),30000);
  try{
    const response=await fetch(path,{method,signal:controller.signal,headers:{'Content-Type':'application/json','X-Tutor-Client':'local-ui'},...(body===undefined?{}:{body:JSON.stringify(body)})});
    let result;try{result=await response.json()}catch{throw new Error('本地程序响应异常，请重试或重新打开启动窗口')}
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:response.status>=500?'本地程序暂时未能完成操作，请稍后重试':'填写内容格式不正确，请检查后重试');
    return result;
  }catch(error){
    if(controller.signal.aborted)throw new Error('请求等待超时，任务可能仍在运行，请查看任务状态后重试');
    if(error instanceof TypeError)throw new Error('无法连接本地程序，请确认启动窗口仍然打开');
    throw error;
  }finally{clearTimeout(deadline)}
}
async function action(fn,button){
  if(button)button.disabled=true;
  try{await fn()}catch(error){notify(error.message||'操作失败，请重试',true)}
  finally{if(button)button.disabled=false;syncTaskControls()}
}
function savedCounts(){return state.selected_counts||state.counts||{}}
function renderWeChatAuthorization(){
  const authorized=state.wechat_authorization?.authorized===true;
  $('#wechat-settings-toggle').hidden=!authorized;
  $('#wechat-settings-toggle').setAttribute('aria-expanded',String(wechatSettingsOpen));
  $('#wechat-authorization').hidden=!['orders','groups'].includes(view)||(view==='orders'&&authorized&&!wechatSettingsOpen);
  $('#wechat-authorization').classList.toggle('warn',!authorized);
  $('#wechat-authorization-status').textContent=authorized?'微信已授权 · 仅本次运行':'微信读取未授权';
  $('#connection').hidden=!authorized;
  $('#wechat-consent-copy').hidden=authorized;
  $('#authorize-wechat').hidden=authorized;
  $('#authorize-wechat').disabled=authorizationSaving||!state.wechat_authorization;
  $('#authorize-wechat').textContent=authorizationSaving?'正在授权…':'同意并授权';
  $('#revoke-wechat').hidden=!authorized;$('#revoke-wechat').disabled=authorizationSaving;
  $('#check-wechat').hidden=!authorized;$('#check-wechat').disabled=authorizationSaving||state.busy||taskStarting;
  $$('[data-retry-group]').forEach(button=>button.disabled=!authorized||state.busy||taskStarting||authorizationSaving);
}
function renderProgress(){
  const p=state.processing||{state:'idle'};
  const active=Boolean(state.busy||taskStarting);
  const starting=taskStarting&&!state.busy;
  const filtering=(taskKind==='filter'&&active)||(state.busy&&p.state==='processing');
  for(const id of ['#filter','#filter-selected']){
    $(id).innerHTML=filtering?'<span class="spinner" aria-hidden="true"></span>正在筛选…':'开始筛选';
    $(id).classList.toggle('button-loading',Boolean(filtering));
    $(id).setAttribute('aria-busy',String(Boolean(filtering)));
  }
  $('#orders-content').setAttribute('aria-busy',String(Boolean(filtering)));
  const counts=savedCounts();
  $('#workflow-hint').hidden=active||['extracted','extraction_partial','extraction_cancelled'].includes(p.state)||Boolean(state.groups?.length&&Object.values(counts).some(Boolean)&&!(counts.received||counts.error||state.parser?.pending_reparse));
  $('#cancel').hidden=!state.busy;$('#cancel').disabled=stopping;
  $('#processing').hidden=!active&&p.state==='idle';
  $('#processing').classList.toggle('active',active);
  $('#processing').classList.toggle('inline-alert',!active&&['partial','extraction_partial','interrupted'].includes(p.state));
  $('#processing-spinner').hidden=!active||stopping;
  $('#processing-text').textContent=stopping?'正在停止，已保存结果保留':starting?(taskKind==='filter'?'正在筛选已保存的信息…':'正在开始提取信息…'):({processing:state.parser?.mode==='local'?'正在本地识别和筛选':'正在筛选',partial:'部分解析失败，可重试',cancelled:'已停止解析'})[p.state]||p.detail||'';
  if(!starting&&p.total)$('#processing-text').textContent+=' · '+p.processed+'/'+p.total+'条'+(p.errors?' · '+p.errors+'条失败':'');
  else if(!starting&&['reading','extracted','extraction_partial','extraction_cancelled'].includes(p.state))$('#processing-text').textContent+=' · 已保存 '+(p.processed||0)+' 条';
  $('#stop-processing').hidden=!state.busy&&p.state!=='cancelled';
  $('#stop-processing').disabled=!state.busy||stopping;
  $('#stop-processing').textContent=stopping?'正在停止…':state.busy?'停止':'已停止';
}
function syncTaskControls(){
  const busy=state.busy||taskStarting;
  const authorized=state.wechat_authorization?.authorized===true&&!authorizationSaving;
  const saved=Object.values(savedCounts()).reduce((sum,count)=>sum+count,0);
  $('#scan').disabled=busy||!authorized;$('#scan-selected').disabled=busy||!authorized||groupSaving||!selectedGroups.size;
  $('#filter').disabled=busy||!saved;$('#filter-selected').disabled=busy||groupSaving||!selectedGroups.size||!saved;
  $('#retry-process').disabled=busy;
  $('#preferences-form button[type="submit"]').disabled=busy;
  $$('[data-favorite]').forEach(button=>button.disabled=busy||favoriteSaving.has(button.dataset.favorite));
  const stale=state.parser?.pending_reparse||0;
  const pending=(savedCounts().received||0)+(savedCounts().error||0)+stale;
  $('#workflow-hint').textContent=!state.groups?.length?'请先在“微信群”选择来源。':!saved?'尚无消息，请先提取。':stale?'识别规则已更新，点击“开始筛选”重新识别 '+stale+' 条旧消息（先自动备份）':pending?pending+' 条待筛选，点击“开始筛选”。':'已保存 '+saved+' 条信息';
  $('#workflow-hint').classList.toggle('inline-alert',!state.groups?.length||!saved||Boolean(pending));
  $('#workflow-hint').hidden=Boolean(state.groups?.length&&saved&&!pending);
  renderWeChatAuthorization();renderProgress();
}
async function refreshState(){
  const request=++stateRequest,next=await api('/api/state');
  if(request!==stateRequest)return;
  state=next;
  $('#scan').textContent='开始提取信息';
  $('#scan-selected').textContent='开始提取信息';
  $('#scan').disabled=state.busy;
  if(!state.busy){stopping=false;if(!taskStarting)taskKind=null}
  const counts=savedCounts();
  $('#retry-process').hidden=!(counts.error||0);
  $('#retry-process').disabled=state.busy;
  renderScans();renderSummary();syncGroups();syncTaskControls();
}
function renderSummary(){
  const rules=state.preferences?.rules||[];
  const ruleIcon=field=>field==='location'?'location':field.endsWith('_rate')?'pay':field==='subject'||field==='grade'?'book':'filter';
  $('#rule-summary').innerHTML=rules.length?rules.map(r=>'<span class="chip">'+uiIcon(ruleIcon(r.field))+'<span>'+esc(names[r.field]||r.field)+' '+esc(r.field==='location'?r.value.map(code=>regionIndex[code]?.label||code).join('、'):pretty(r.value))+' · '+(r.mode==='must'?'必须':'优先')+'</span></span>').join(''):'<span class="muted">尚未设置需求</span>';
  const latest=state.scans?.[0];
  $('#source-context-text').textContent=state.groups?.length?'已选 '+state.groups.length+' 个群'+(latest?.end?' · 最近提取 '+compactTime(latest.end):' · 尚未提取信息'):'尚未选择微信群';
}
function renderScans(){
  const scan=state.scans?.[0];
  const incomplete=scan&&(scan.state==='incomplete'||scan.groups?.some(g=>g.state==='incomplete'));
  $('.scan-panel').classList.toggle('scan-warning',Boolean(incomplete));
  $('#scan-summary').textContent=scan?readStateName(scan)+(incomplete?' · 近48小时范围未确认':'')+' · '+formatTime(scan.end):'尚未提取微信消息';
  $('#scan-reports').innerHTML=scan?scan.groups.map(g=>'<div class="scan-report"><strong>'+esc(g.group)+' · '+esc(readStateName(g))+'</strong><div>'+esc(g.state==='incomplete'&&g.count>0?'已保存消息，可继续整理和筛选；48小时范围尚未确认':g.detail)+' · 已保存 '+(g.count||0)+' 条</div><div class="muted">最早 '+esc(formatTime(g.earliest))+' ／ 最晚 '+esc(formatTime(g.latest))+' ／ 时间不明 '+(g.unknown_times||0)+' 条</div>'+(g.state==='incomplete'||g.state==='cancelled'?'<div class="actions"><button data-retry-group="'+esc(g.group)+'">'+(g.count>0?'继续读取':'重试这个群')+'</button></div>':'')+'</div>').join(''):'<p class="muted">首次扫描前，请在“微信群”选择来源。</p>';
}
async function checkConnection(){
  if(state.wechat_authorization?.authorized!==true){renderWeChatAuthorization();return}
  const result=await api('/api/connection');
  if(state.wechat_authorization?.authorized!==true)return;
  $('#connection').textContent=result.adapter_installed?(result.wechat_running?'微信进程已启动，读取能力需提取时确认':'请登录电脑版微信'):'微信读取组件未安装';
  $('#connection').classList.toggle('attention-text',!result.adapter_installed||!result.wechat_running);
}
async function setWeChatAuthorization(authorized){
  if(authorizationSaving)return;
  authorizationSaving=true;syncTaskControls();
  try{
    state.wechat_authorization=await api('/api/wechat/authorization','PUT',{authorized});
    $('#connection').textContent=authorized?'已授权，正在检查微信读取环境…':'已撤销授权';
    await refreshState();
    if(authorized){await checkConnection();notify('已授权。选择群聊后，点击“开始提取信息”即可')}
    else notify('已撤销授权，正在提取的任务会停止；已保存的信息保留');
  }finally{authorizationSaving=false;syncTaskControls()}
}
const sidebarMedia=window.matchMedia('(max-width:650px)');
let sidebarCollapsed=false;
try{sidebarCollapsed=localStorage.getItem('tutor-sidebar-collapsed')==='1'}catch{}
function syncSidebarLayout(){
  const mobile=sidebarMedia.matches;
  $('.app-shell').classList.toggle('sidebar-collapsed',!mobile&&sidebarCollapsed);
  const button=$('#collapse-sidebar');
  const label=mobile?'关闭侧边栏':sidebarCollapsed?'展开侧边栏':'收起侧边栏';
  button.setAttribute('aria-label',label);button.title=label;
  button.setAttribute('aria-expanded',String(mobile?$('#toggle-nav').getAttribute('aria-expanded')==='true':!sidebarCollapsed));
}
function setSidebarOpen(open){
  const mobile=sidebarMedia.matches;
  const expanded=mobile&&open;
  const sidebar=$('#site-sidebar');
  const wasInside=sidebar.contains(document.activeElement);
  const wasOpen=sidebar.classList.contains('is-open');
  sidebar.classList.toggle('is-open',expanded);
  sidebar.inert=mobile&&!expanded;
  $('.workspace').inert=expanded;
  if(expanded){sidebar.setAttribute('role','dialog');sidebar.setAttribute('aria-modal','true')}
  else{sidebar.removeAttribute('role');sidebar.removeAttribute('aria-modal')}
  if(mobile&&!expanded)sidebar.setAttribute('aria-hidden','true');else sidebar.removeAttribute('aria-hidden');
  $('#toggle-nav').setAttribute('aria-expanded',String(expanded));
  $('#nav-backdrop').hidden=!expanded;
  document.body.classList.toggle('nav-open',expanded);
  syncSidebarLayout();
  if(expanded)sidebar.querySelector('.nav.active')?.focus();
  else if(mobile&&(wasInside||wasOpen))$('#toggle-nav').focus();
  else if(!mobile&&document.activeElement===$('#toggle-nav'))sidebar.querySelector('.nav.active')?.focus();
}
$('#collapse-sidebar').addEventListener('click',()=>{
  if(sidebarMedia.matches){setSidebarOpen(false);return}
  sidebarCollapsed=!sidebarCollapsed;
  try{localStorage.setItem('tutor-sidebar-collapsed',sidebarCollapsed?'1':'0')}catch{}
  syncSidebarLayout();
});
$('#toggle-nav').addEventListener('click',()=>setSidebarOpen($('#toggle-nav').getAttribute('aria-expanded')!=='true'));
$('#nav-backdrop').addEventListener('click',()=>setSidebarOpen(false));
sidebarMedia.addEventListener('change',()=>setSidebarOpen(false));
document.addEventListener('keydown',event=>{
  const sidebar=$('#site-sidebar');
  if(!sidebarMedia.matches||!sidebar.classList.contains('is-open'))return;
  if(event.key==='Escape'){event.preventDefault();setSidebarOpen(false);return}
  if(event.key!=='Tab')return;
  const stops=[...sidebar.querySelectorAll('button:not(:disabled),a[href]')].filter(el=>el.getClientRects().length);
  const first=stops[0],last=stops[stops.length-1];
  if(!first)return;
  if(!sidebar.contains(document.activeElement)||(event.shiftKey?document.activeElement===first:document.activeElement===last)){
    event.preventDefault();(event.shiftKey?last:first).focus();
  }
});
setSidebarOpen(false);
async function go(next){
  if(!['orders','favorites','preferences','groups','travel'].includes(next))next='orders';
  view=next;selected=null;
  $('#page-context').textContent={orders:'家教订单',favorites:'我的收藏',preferences:'接单设置',groups:'微信群',travel:'通勤计算'}[next];
  $('#page-subtitle').hidden=next!=='orders';
  const activeView=$('#view-'+next),heading=activeView.querySelector('.heading-row')||activeView.querySelector('h1');
  if(['orders','groups'].includes(next))heading.insertAdjacentElement('afterend',$('#wechat-authorization'));
  if(['travel','favorites'].includes(next))heading.insertAdjacentElement('afterend',$('#travel-authorization'));
  $('#wechat-authorization').hidden=!['orders','groups'].includes(next);
  renderWeChatAuthorization();
  $('#travel-authorization').hidden=!['travel','favorites'].includes(next);
  setSidebarOpen(false);
  $$('.view').forEach(el=>el.hidden=el.id!=='view-'+next);
  $$('.nav').forEach(el=>{const active=el.dataset.view===next;el.classList.toggle('active',active);if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current')});
  if(next==='orders')await loadOrders();
  if(next==='favorites')await loadFavorites();
  if(next==='preferences')fillPreferences();
  if(next==='groups')fillGroups();
  window.scrollTo({top:0});
}
function empty(title,description,buttons=''){
  return '<div class="empty"><h2>'+esc(title)+'</h2><p class="muted">'+esc(description)+'</p><div class="actions">'+buttons+'</div></div>';
}
function title(order){return [arr(fact(order,'grade')),arr(fact(order,'subject'))].filter(Boolean).join(' · ')||'待核对家教单'}
function price(order){
  for(const [key,unit] of [['hourly_rate','元/小时'],['lesson_rate','元/次'],['monthly_rate','元/月']]){
    if(fact(order,key)!=null)return pretty(fact(order,key))+' '+unit;
  }
  if(fact(order,'rate_options'))return pretty(fact(order,'rate_options'));
  return '课酬未说明';
}
function requirements(order){
  return ['teacher_gender','school_tier','teacher_type','education','english_score','teaching_language'].filter(k=>fact(order,k)!=null).map(k=>(k==='teacher_gender'?'教员'+pretty(fact(order,k)):k==='english_score'?'高考英语≥'+pretty(fact(order,k)):pretty(fact(order,k)))+(order.facts[k].mode==='prefer'?'优先':'')).join(' · ')||'未注明';
}
function teachingLocation(order){
  const location=fact(order,'location');
  return typeof location==='string'&&location.trim()?location.trim():[fact(order,'city'),fact(order,'area')].filter(Boolean).join(' · ')||'位置未说明';
}
function table(orders,favorites=false){
  return '<div class="order-list" aria-label="'+(favorites?'收藏订单':'订单列表')+'">'+orders.map(o=>{
    const match=o.match,changed=match.bucket==='rejected';
    const badge=o.status==='closed'?'已出 / 已结束':o.expired?'已超过48小时':changed?'不符合当前条件':match.bucket==='matched'?'通过筛选':'待确认';
    const issues=changed?[o.status==='closed'?'原消息标明此单已出或已结束':'当前条件已变化，请重新核对',...match.conflicts]:match.unknowns;
    const sources=new Set(o.sources.map(s=>s.group)).size;
    const locationNote=match.location_status==='unconfirmed'?'<span class="cell-sub attention-text">地区待核对 · 尚未通过</span>':'';
    const secondary=[fact(o,'weekly_sessions')!=null?pretty(fact(o,'weekly_sessions'))+'次/周':'',fact(o,'duration_hours')!=null?pretty(fact(o,'duration_hours'))+'小时/次':''].filter(Boolean).join(' · ');
    const detail='<button type="button" class="order-open" data-detail="'+esc(o.id)+'" data-favorites="'+favorites+'" aria-expanded="'+(selected===o.id)+'" aria-label="查看 '+esc(o.reference||'无单号')+' '+esc(title(o))+' 原文">'+esc(title(o))+'</button>';
    return '<article class="order-item'+(selected===o.id?' selected':'')+'" data-order-row="'+esc(o.id)+'"><div class="order-meta"><span class="reference">'+esc(o.reference||'未提供单号')+'</span><span class="badge '+(match.bucket==='matched'&&!o.expired&&o.status!=='closed'?'':'pending')+'">'+esc(badge)+'</span><time title="'+esc(formatTime(o.updated_at))+'">'+esc(compactTime(o.updated_at))+'</time></div><div class="order-title"><h2>'+detail+'</h2><strong class="money">'+priceMarkup(o)+'</strong></div><dl class="order-facts"><div><dt>'+uiIcon('location')+'<span class="sr-only">授课位置</span></dt><dd class="teaching-location">'+esc(teachingLocation(o))+locationNote+'</dd></div><div><dt>'+uiIcon('clock')+'<span class="sr-only">上课安排</span></dt><dd>'+esc(fact(o,'schedule')?pretty(fact(o,'schedule')):'具体时段待核对')+(secondary?'<span class="cell-sub">'+esc(secondary)+'</span>':'')+'</dd></div><div><dt>'+uiIcon('person')+'<span class="sr-only">教员要求</span></dt><dd>'+esc(requirements(o))+'</dd></div></dl>'+(fact(o,'focus_subjects')?'<p class="cell-sub">侧重'+esc(arr(fact(o,'focus_subjects')))+'</p>':'')+(issues.length||o.expired?'<p class="order-issues">'+esc([o.expired?'已超过48小时，请确认是否仍可接单':'',...issues].filter(Boolean).join('；'))+'</p>':'')+(o.favorite_at?(window.TutorTravel?.summary(o.id)||''):'')+'<div class="order-bottom"><span class="cell-sub">'+esc([fact(o,'student_gender')?'学员'+fact(o,'student_gender'):'',fact(o,'students_count')?fact(o,'students_count')+'名学员':'',sources+'个来源群',o.possible_duplicates.length?'有疑似重复单':''].filter(Boolean).join(' · '))+'</span><div class="cell-actions">'+(favorites&&o.favorite_at?'<button data-travel-open="'+esc(o.id)+'" data-travel-favorites="true">计算通勤</button>':'')+favoriteButton(o)+'</div></div></article>';
  }).join('')+'</div>';
}
async function loadOrders(){
  const request=++ordersRequest,scope=JSON.stringify(state.groups||[]);
  const result=await api('/api/orders');
  if(request!==ordersRequest||scope!==JSON.stringify(state.groups||[]))return;
  data=result.orders.filter(o=>o.match.bucket!=='rejected');
  for(const name of ['matched','pending'])$('#count-'+name).textContent=data.filter(o=>o.match.bucket===name).length;
  const current=data.filter(o=>o.match.bucket===bucket);
  const counts=savedCounts();
  const reports=(state.scans?.[0]?.groups||[]).filter(g=>(state.groups||[]).includes(g.group));
  const noMessages=reports.length&&reports.every(g=>!(g.count||0))&&reports.some(g=>g.state==='incomplete');
  let description='先提取微信消息，再筛选已保存的信息。';
  if((counts.received||0)+(counts.error||0)>0)description='已有消息等待筛选，请点击上方“开始筛选”，无需重新提取。';
  else if(bucket==='matched'&&data.some(o=>o.match.bucket==='pending'))description='还有 '+data.filter(o=>o.match.bucket==='pending').length+' 单待确认，可切换到“待确认”核对原文。';
  else if(Object.values(counts).some(Boolean))description='可调整筛选条件。';
  $('#orders-content').innerHTML=current.length?table(current):noMessages&&!data.length?empty('尚未读取到群消息',reports.find(g=>g.state==='incomplete')?.detail||'群消息读取失败，请查看读取状态。','<button data-open-scan="true">查看读取状态</button>'):empty(bucket==='matched'?'暂时没有通过筛选的单子':'暂时没有待确认的单子',description,'<button data-view="preferences">设置接单需求</button><button data-view="groups">选择微信群</button>');
  const detail=$('#order-detail');
  if(selected&&current.some(o=>o.id===selected))renderDetail(selected,false,false);else{selected=null;detail.hidden=true}
}
async function loadFavorites(){
  const request=++favoritesRequest;
  const result=await api('/api/orders?favorites=true');
  if(request!==favoritesRequest)return;
  favoriteData=result.orders;
  $('#favorites-content').innerHTML=favoriteData.length?table(favoriteData,true):empty('还没有收藏','在订单清单中收藏后，可在这里长期查看。');
  if(selected&&favoriteData.some(o=>o.id===selected))renderDetail(selected,true,false);else $('#favorite-detail').hidden=true;
}
function renderDetail(id,favorites=false,scroll=true){
  selected=id;
  const order=(favorites?favoriteData:data).find(o=>o.id===id);
  if(!order)return;
  const target=$(favorites?'#favorite-detail':'#order-detail');
  const match=order.match;
  const notices=[...new Set([...match.unknowns,...match.conflicts])];
  if(match.bucket==='rejected')notices.unshift(order.status==='closed'?'原消息标明此单已出或已结束':'条件已变，请重新核对');
  if(order.expired)notices.push('订单已超过48小时，请确认是否仍可接单');
  const caution=notices.length?'<div class="inline-alert order-caution"><strong>接单前请核对</strong><ul>'+notices.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul></div>':'';
  target.hidden=false;
  const primary=order.sources.find(source=>!source.is_update)||order.sources[0];
  const rawSource=source=>'<article class="source"><p class="muted">'+esc(source.group)+' · '+esc(source.sender)+' · '+esc(formatTime(source.sent_at))+'</p><div class="raw">'+esc(source.text)+'</div></article>';
  const badge=order.status==='closed'?'已出 / 已结束':order.expired?'已超过48小时':match.bucket==='matched'?'通过筛选':match.bucket==='pending'?'待确认':'不符合当前条件';
  const summary='<dl class="reader-facts"><div><dt>'+uiIcon('pay')+'课酬</dt><dd>'+esc(price(order))+'</dd></div><div><dt>'+uiIcon('location')+'授课地点</dt><dd>'+esc(teachingLocation(order))+'</dd></div><div><dt>'+uiIcon('person')+'教员要求</dt><dd>'+esc(requirements(order))+'</dd></div></dl>';
  target.innerHTML='<section class="detail-panel"><div class="heading-row"><div class="reader-title"><h2>'+esc(order.reference||'无单号')+' · 原消息</h2><span class="badge '+(match.bucket==='matched'&&!order.expired&&order.status!=='closed'?'':'pending')+'">'+esc(badge)+'</span></div><div class="reader-actions">'+favoriteButton(order)+'<button data-close-detail="'+(favorites?'favorite':'order')+'">收起详情</button></div></div>'+caution+'<div class="order-original">'+(primary?rawSource(primary):'<p>原消息暂不可用</p>')+'</div>'+(order.sources.length>1?'<details><summary>补充消息与其他来源（'+(order.sources.length-1)+'）</summary>'+order.sources.filter(source=>source!==primary).map(rawSource).join('')+'</details>':'')+'<section class="reader-checks" aria-label="字段核对与筛选依据"><h3>字段核对与筛选依据</h3>'+summary+'<details class="local-checks"><summary>查看完整字段与原文依据</summary><ul>'+match.reasons.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul>'+'<table class="evidence-table"><thead><tr><th>字段</th><th>值</th><th>原文依据</th></tr></thead><tbody>'+Object.entries(order.facts).map(([key,f])=>'<tr><td>'+esc(state.labels?.[key]||key)+(f.mode==='prefer'?'（偏好）':'')+'</td><td>'+esc(pretty(f.value))+'</td><td>'+esc(f.evidence)+'</td></tr>').join('')+'</tbody></table></details></section></section>';

  $$('[data-order-row]').forEach(row=>{const active=row.dataset.orderRow===id;row.classList.toggle('selected',active);row.querySelector('[data-detail]')?.setAttribute('aria-expanded',String(active))});
  target.insertAdjacentHTML('beforeend',window.TutorTravel?.detail(id,favorites,Boolean(order.favorite_at))||'');
  if(scroll){
    if(window.matchMedia('(max-width:999px)').matches)target.scrollIntoView({behavior:'auto',block:'start'});
    else target.closest('.order-reader').scrollTop=0;
    const heading=target.querySelector('h2');heading.tabIndex=-1;heading.focus({preventScroll:true});
  }
}
function fillPreferences(){
  const prefs=state.preferences||{profile:{},rules:[]};
  $('#preferences-form [name=teacher_gender]').value=prefs.profile.teacher_gender||'';
  const tiers=Array.isArray(prefs.profile.school_tier)?prefs.profile.school_tier:[prefs.profile.school_tier];
  $('#preferences-form [name=school_tier]').value=tiers.includes('一本')&&tiers.includes('双非')?'双非一本':tiers[0]||'';
  const pay=prefs.rules.find(r=>['hourly_rate','lesson_rate','monthly_rate'].includes(r.field));
  $('#pay-unit').value=pay?.field||'hourly_rate';
  $('#pay-min').value=typeof pay?.value==='number'?pay.value:pay?.value.min??'';
  $('#pay-max').value=pay?.value.max??'';
  const chosen=prefs.rules.find(r=>r.field==='subject')?.value||[];
  $('#subject-options').innerHTML=subjects.map(subject=>'<label class="choice"><input type="checkbox" value="'+esc(subject)+'"'+(chosen.includes(subject)?' checked':'')+'>'+esc(subject)+'</label>').join('');
  $('#extra-subjects').value=chosen.filter(s=>!subjects.includes(s)).join('、');
  syncSubjects();
  const options='<option value="">不限</option>'+grades.map((g,i)=>'<option value="'+i+'">'+g+'</option>').join('');
  $('#grade-from').innerHTML=options;$('#grade-to').innerHTML=options;
  const chosenGrades=prefs.rules.find(r=>r.field==='grade')?.value||[];
  const indices=chosenGrades.map(g=>grades.indexOf(g)).filter(i=>i>=0);
  $('#grade-from').value=indices.length?Math.min(...indices):'';
  $('#grade-to').value=indices.length?Math.max(...indices):'';
  selectedRegions=[...(prefs.rules.find(r=>r.field==='location')?.value||[])];
  renderRegionPicker();renderSelectedRegions();
}
function renderRegionPicker(level='province'){
  const province=$('#region-province'),city=$('#region-city'),county=$('#region-county');
  if(level==='province')province.innerHTML='<option value="">全国</option>'+regions.map(p=>'<option value="'+p.code+'">'+esc(p.name)+'</option>').join('');
  if(level!=='county'){
    const p=regions.find(p=>p.code===province.value);
    city.innerHTML='<option value="">全省</option>'+(p?.children||[]).map(c=>'<option value="'+c.code+'">'+esc(c.name)+'</option>').join('');
    city.disabled=!p;
  }
  const p=regions.find(p=>p.code===province.value),c=p?.children.find(c=>c.code===city.value);
  county.innerHTML='<option value="">全市</option>'+(c?.children||[]).map(c=>'<option value="'+c.code+'">'+esc(c.name)+'</option>').join('');
  county.disabled=!c;
}
function renderSelectedRegions(){
  $('#selected-regions').innerHTML=selectedRegions.length?selectedRegions.map(code=>'<span class="chip">'+esc(regionIndex[code]?.label||code)+' <button type="button" data-remove-region="'+code+'" aria-label="移除'+esc(regionIndex[code]?.label||code)+'">移除</button></span>').join(''):'<span class="muted">全国</span>';
}
function fillGroups(){
  $('#group-count').textContent='已选 '+selectedGroups.size+' 个';
  $('#group-choice').innerHTML=groupCatalog.length?groupCatalog.map(name=>'<div class="group-item"><label class="group-checkbox"><input type="checkbox" name="selected-group" value="'+esc(name)+'"'+(selectedGroups.has(name)?' checked':'')+'><span>'+esc(name)+'</span></label><button class="remove-group" data-remove-group="'+esc(name)+'" aria-label="移除'+esc(name)+'">移除</button></div>').join(''):'<p class="group-empty muted">添加群名后，在这里勾选。</p>';
  setGroupControls();
}
function setGroupControls(){
  $$('#group-choice input,#group-choice button,#add-group').forEach(el=>el.disabled=groupSaving||state.busy||taskStarting);
  syncTaskControls();
}
function syncGroups(){
  if(groupSaving)return;
  const signature=JSON.stringify([state.group_catalog||[],state.groups||[]]);
  if(lastCatalog!==signature){lastCatalog=signature;groupCatalog=[...(state.group_catalog||[])];selectedGroups=new Set(state.groups||[]);fillGroups()}
  setGroupControls();
}
function syncSubjects(){
  const all=$('#subject-options input[value="全科"]');
  if(!all)return;
  $$('#subject-options input').filter(el=>el!==all).forEach(el=>{
    if(all.checked){el.dataset.previous=String(el.checked);el.checked=true}
    else if(el.disabled){el.checked=el.dataset.previous==='true'}
    el.disabled=all.checked;
  });
  $('#extra-subjects').disabled=all.checked;
}
async function startScan(groups){
  if(state.busy||taskStarting)return;
  if(state.wechat_authorization?.authorized!==true){notify('请先同意并授权使用电脑微信',true);$('#authorize-wechat').focus();return}
  if(!groups.length){await go('groups');notify('请选择需要关注的微信群');return}
  taskKind='extract';taskStarting=true;syncTaskControls();
  try{await api('/api/scans','POST',{groups});await refreshState();await go('orders');notify('已开始提取信息，完成后请点击“开始筛选”')}
  finally{taskStarting=false;if(!state.busy)taskKind=null;syncTaskControls()}
}
async function startFilter(){
  if(state.busy||taskStarting)return;
  if(!state.groups?.length){await go('groups');notify('请选择需要筛选的微信群');return}
  if(!Object.values(savedCounts()).some(Boolean)){notify('尚无已保存的信息，请先点击“开始提取信息”');return}
  taskKind='filter';taskStarting=true;syncTaskControls();
  try{await api('/api/process','POST',{});await refreshState();await go('orders');notify('已开始筛选本机保存的信息')}
  finally{taskStarting=false;if(!state.busy)taskKind=null;syncTaskControls()}
}
async function changeGroups(method,body){
  groupSaving=true;setGroupControls();
  try{
    const result=await api('/api/groups',method,body);
    state.groups=result.groups;state.group_catalog=result.group_catalog;
    ordersRequest++;
    await refreshState();await loadOrders();
  }finally{groupSaving=false;syncGroups();fillGroups()}
}
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.id==='wechat-settings-toggle'){wechatSettingsOpen=!wechatSettingsOpen;renderWeChatAuthorization();return}
  if(button.dataset.view)return action(()=>go(button.dataset.view),button);
  if(button.dataset.openScan){const panel=$('.scan-panel');panel.open=true;panel.scrollIntoView({block:'nearest'});$('#scan-summary').focus();return}
  if(button.dataset.bucket){bucket=button.dataset.bucket;selected=null;syncTabs();return action(loadOrders)}
  if(button.dataset.detail)return renderDetail(button.dataset.detail,button.dataset.favorites==='true');
  if(button.dataset.closeDetail){$(button.dataset.closeDetail==='favorite'?'#favorite-detail':'#order-detail').hidden=true;const opener=$$('.view:not([hidden]) [data-detail]').find(el=>el.dataset.detail===selected);opener?.focus({preventScroll:true});$$('[data-order-row]').forEach(el=>el.classList.remove('selected'));$$('[data-detail]').forEach(el=>el.setAttribute('aria-expanded','false'));selected=null;return}
  if(button.dataset.favorite){
    const id=button.dataset.favorite;if(favoriteSaving.has(id))return;
    favoriteSaving.add(id);
    return action(async()=>{try{await api('/api/orders/'+id+'/favorite','PUT',{enabled:button.dataset.enabled==='true'});await(view==='favorites'?loadFavorites():loadOrders())}finally{favoriteSaving.delete(id)}},button);
  }
  if(button.dataset.removeRegion){selectedRegions=selectedRegions.filter(code=>code!==button.dataset.removeRegion);renderSelectedRegions();return}
  if(button.dataset.retryGroup)return action(()=>startScan([button.dataset.retryGroup]),button);
  if(button.dataset.removeGroup)return action(()=>changeGroups('DELETE',{name:button.dataset.removeGroup}),button);
});
document.addEventListener('keydown',event=>{
  const tab=event.target.closest('[role=tab]');if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();const tabs=$$('[role=tab]'),index=tabs.indexOf(tab);
  const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  tabs[next].focus();tabs[next].click();
});
$('#region-province').addEventListener('change',()=>renderRegionPicker('city'));
$('#region-city').addEventListener('change',()=>renderRegionPicker('county'));
$('#add-region').addEventListener('click',()=>{
  const code=$('#region-county').value||$('#region-city').value||$('#region-province').value;
  if(!code){selectedRegions=[];renderSelectedRegions();return}
  if(!selectedRegions.includes(code))selectedRegions.push(code);renderSelectedRegions();
});
$('#preferences-form').addEventListener('submit',event=>{event.preventDefault();action(async()=>{
  if(state.busy||taskStarting)throw new Error('任务进行中，请完成或停止后再保存接单条件');
  const profile={},rules=[];
  const gender=$('[name=teacher_gender]').value,tier=$('[name=school_tier]').value;
  if(gender)profile.teacher_gender=gender;
  if(tier)profile.school_tier=tier==='双非一本'?['一本','双非']:[tier];
  const min=$('#pay-min').value,max=$('#pay-max').value;
  if(min!==''||max!==''){
    const value={};if(min!=='')value.min=Number(min);if(max!=='')value.max=Number(max);
    if(value.min!=null&&value.max!=null&&value.min>value.max)throw new Error('最低薪资不能高于最高薪资');
    rules.push({field:$('#pay-unit').value,value,mode:'must'});
  }
  const chosen=$('#subject-options input[value="全科"]').checked?['全科']:[...new Set([...$$('#subject-options input:checked').map(x=>x.value),...split($('#extra-subjects').value)])];
  if(chosen.length)rules.push({field:'subject',value:chosen,mode:'must'});
  const from=$('#grade-from').value,to=$('#grade-to').value;
  if(from!==''||to!==''){
    const lower=from===''?0:Number(from),upper=to===''?grades.length-1:Number(to);
    if(lower>upper)throw new Error('年级范围顺序不正确');
    rules.push({field:'grade',value:grades.slice(lower,upper+1),mode:'must'});
  }
  if(selectedRegions.length)rules.push({field:'location',value:selectedRegions,mode:'must'});
  await api('/api/preferences','PUT',{profile,rules});await refreshState();
  if(state.groups?.length&&Object.values(savedCounts()).some(Boolean)){await startFilter();notify('接单条件已保存，正在筛选已保存的信息')}
  else{await go('orders');notify('接单条件已保存，请选择群聊并提取消息后筛选')}
},event.submitter)});
$('#group-choice').addEventListener('change',event=>{
  if(event.target.name!=='selected-group')return;
  const next=new Set(selectedGroups);
  event.target.checked?next.add(event.target.value):next.delete(event.target.value);
  if(next.size>10){event.target.checked=false;notify('每次最多选择10个群',true);return}
  action(()=>changeGroups('PUT',{groups:[...next]}));
});
$('#add-group-form').addEventListener('submit',event=>{event.preventDefault();action(async()=>{
  const name=$('#manual-group-name').value.trim();if(!name){$('#manual-group-name').focus();return}
  await changeGroups('POST',{name});$('#manual-group-name').value='';$('#manual-group-name').focus();notify('群名已保存');
},event.submitter)});
$('#subject-options').addEventListener('change',event=>{if(event.target.value==='全科')syncSubjects()});
$('#authorize-wechat').addEventListener('click',()=>action(()=>setWeChatAuthorization(true),$('#authorize-wechat')));
$('#revoke-wechat').addEventListener('click',()=>action(()=>setWeChatAuthorization(false),$('#revoke-wechat')));
$('#check-wechat').addEventListener('click',()=>action(checkConnection,$('#check-wechat')));
$('#scan-selected').addEventListener('click',()=>action(()=>startScan([...selectedGroups]),$('#scan-selected')));
$('#scan').addEventListener('click',()=>action(()=>startScan(state.groups||[]),$('#scan')));
$('#filter').addEventListener('click',()=>action(startFilter,$('#filter')));
$('#filter-selected').addEventListener('click',()=>action(startFilter,$('#filter-selected')));
async function stopTask(){
  if(stopping||!state.busy)return;
  stopping=true;
  $('#stop-processing').disabled=true;$('#stop-processing').textContent='正在停止…';
  $('#cancel').disabled=true;
  $('#processing-text').textContent='正在停止，已保存结果保留';
  try{await api('/api/cancel','POST',{});await refreshState()}
  catch(error){stopping=false;$('#stop-processing').disabled=!state.busy;$('#stop-processing').textContent='停止';$('#cancel').disabled=false;notify(error.message||'停止失败，请重试',true)}
}
$('#cancel').addEventListener('click',stopTask);
$('#stop-processing').addEventListener('click',stopTask);
$('#retry-process').addEventListener('click',()=>action(startFilter,$('#retry-process')));
async function boot(){
  try{
    const result=await api('/api/regions');regions=result.provinces;
    for(const p of regions){
      regionIndex[p.code]={label:p.name};
      for(const c of p.children){
        regionIndex[c.code]={label:p.name===c.name?c.name:p.name+' / '+c.name};
        for(const d of c.children)regionIndex[d.code]={label:regionIndex[c.code].label+' / '+d.name};
      }
    }
    await refreshState();selectedGroups=new Set(state.groups||[]);await checkConnection();fillGroups();
    await go(['#travel','#model'].includes(location.hash)?'travel':'orders');
  }catch(error){notify('连接失败：'+error.message,true)}
  let polling=false;
  setInterval(async()=>{
    if(polling)return;polling=true;
    try{const wasBusy=state.busy;await refreshState();if(wasBusy||state.busy){if(view==='orders')await loadOrders();if(view==='favorites')await loadFavorites()}}
    catch(error){notify(error.message||'连接中断，请确认本地程序仍在运行',true)}
    finally{polling=false}
  },3000);
}
boot();
