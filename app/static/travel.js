/* Optional Amap feature. Local UI polling never invokes the provider. */
(()=>{
  let current={},busy=false,originChoices=[],pendingOrder=null,originSearching=false;
  const results=new Map(),destinations=new Map(),errors=new Map();
  const ready=()=>current.authorized&&current.key_configured;
  const identity=()=>JSON.stringify([current.session,current.version]);
  const fresh=result=>result&&result.session===current.session&&result.version===current.version;
  const distance=result=>(result.distance_m/1000).toFixed(1)+' 公里';
  const duration=result=>Math.ceil(result.duration_seconds/60)+' 分钟';
  const value=id=>fresh(results.get(id))?results.get(id):null;
  function summaryText(id){const result=value(id);return result?esc(result.mode_label)+'约 '+distance(result)+' · '+duration(result):''}
  function summary(id){return '<span class="cell-sub travel-summary" data-travel-summary="'+esc(id)+'">'+summaryText(id)+'</span>'}
  function detail(id,favorites=false,favorited=false){
    if(!favorited)return '';
    const result=value(id),destination=destinations.get(id),choices=destination?.candidates||[];
    const message=errors.get(id);
    return '<section class="travel-result" data-travel-order="'+esc(id)+'" data-travel-favorites="'+favorites+'"><h3>通勤计算</h3>'+
      (result?'<p class="travel-distance">'+esc(result.mode_label)+'约 '+distance(result)+' · '+duration(result)+'</p><p>'+esc(result.origin)+' → '+esc(result.destination)+'</p><p class="foot">高德 · '+esc(formatTime(result.calculated_at))+'</p><p class="inline-alert"><strong>按查询时刻估算，不代表上课时耗时或接单资格。</strong></p>':'<p class="foot">仅计算这条收藏订单。</p>')+
      (message?'<p class="inline-alert error-text" role="alert"><strong>查询未完成</strong> '+esc(message)+'</p>':'')+
      (!ready()||!current.origin?'<button data-travel-open="'+esc(id)+'" data-travel-favorites="'+favorites+'">先设置出发点和高德服务</button>':'<button type="button" data-travel-preview="'+esc(id)+'" data-travel-favorites="'+favorites+'" '+(busy?'disabled':'')+'>'+(busy?'<span class="spinner" aria-hidden="true"></span> 正在查询…':'查询家教地点')+'</button>')+
      (choices.length?(destination.query?'<p class="travel-query">检索地址：'+esc(destination.query)+'</p>':'')+'<div class="inline-alert"><strong>先核对授课地点</strong><p>确认与原消息一致后再计算，将向高德发送起终点坐标。</p></div><div class="travel-choices">'+choices.map(c=>'<div class="travel-choice"><div><strong>'+esc(c.label)+'</strong><span class="cell-sub">'+esc(c.city)+' · '+esc(c.level)+'</span></div><button type="button" data-travel-route="'+esc(id)+'" data-travel-candidate="'+esc(c.id)+'" '+(busy?'disabled':'')+'>用此位置计算</button></div>').join('')+'</div>':'')+'</section>';
  }
  function panels(){
    $$('[data-travel-order]').forEach(el=>{el.outerHTML=detail(el.dataset.travelOrder,el.dataset.travelFavorites==='true',true)});
    $$('[data-travel-summary]').forEach(el=>{el.innerHTML=summaryText(el.dataset.travelSummary)});
  }
  function render(){
    $('#travel-authorize').hidden=Boolean(current.authorized)||!current.key_configured;
    $('#travel-authorize').disabled=busy||!current.key_configured;
    $('#travel-revoke').hidden=!current.authorized;
    // Allow revocation while waiting for a provider response.
    $('#travel-revoke').disabled=false;
    $('#travel-consent-status').textContent=current.authorized?'高德已授权 · 仅本次运行':'高德查询尚未授权';
    $('#travel-authorization').classList.toggle('warn',!current.authorized);
    $('#travel-consent-copy').hidden=Boolean(current.authorized);
    $('#travel-authorization-key-status').hidden=Boolean(current.key_configured);
    $('#travel-setup-key').hidden=Boolean(current.key_configured);
    $('#travel-setup-key').disabled=busy;
    $('#travel-key-status').textContent=current.key_load_error?'Key读取失败，请重新填写。':current.key_configured?'Key已在本机加密保存':'尚未配置 Key';
    $('#travel-key-status').classList.toggle('attention-text',Boolean(current.key_load_error));
    $('#travel-origin-status').textContent=current.origin?'当前出发点：'+current.origin.label+' · '+current.origin.city:'尚未设置出发点';
    const search=$('#travel-search-origin');
    search.disabled=busy||!ready();
    search.innerHTML=originSearching?'<span class="spinner" aria-hidden="true"></span> 正在查询出发点…':'查询并选择出发点';
    search.classList.toggle('button-loading',originSearching);
    search.setAttribute('aria-busy',String(originSearching));
    const blocked=!ready();
    $('#travel-origin-prerequisite').hidden=!blocked;
    $('#travel-origin-prerequisite-copy').textContent=current.key_load_error?'高德 Key 读取失败，请重新保存。':!current.key_configured?'请先保存高德 Key，再查询出发点。':'本次运行尚未授权，暂不能查询出发点。';
    $('#travel-origin-setup').textContent=current.key_configured?'前往授权':'配置高德服务';
    $('#travel-origin-setup').disabled=busy;
    search.title=blocked?$('#travel-origin-prerequisite-copy').textContent:busy?'正在处理通勤操作，请稍候':'查询并选择出发点';
    $('#travel-settings-form button[type=submit]').disabled=busy;
    $('#travel-clear-key').disabled=busy||!current.key_configured;
    $('#travel-clear-origin').disabled=busy||!current.origin;
    $('#travel-origin-choices').innerHTML=originChoices.length?'<p>请核对位置，确认后保存为出发点：</p>'+originChoices.map(c=>'<div class="travel-choice"><div><strong>'+esc(c.label)+'</strong><span class="cell-sub">'+esc(c.city)+' · '+esc(c.level)+'</span></div><button type="button" data-travel-origin="'+esc(c.id)+'" '+(busy?'disabled':'')+'>设为出发点</button></div>').join(''):'';
    renderNextStep();panels();
  }
  function openServiceSetup(){
    if(current.key_configured){
      $('#travel-authorization').scrollIntoView({block:'start'});
      $('#travel-authorize').focus({preventScroll:true});return;
    }
    $('#travel-service-settings').open=true;
    $('#travel-service-settings').scrollIntoView({block:'start'});
    $('#travel-key').focus({preventScroll:true});
  }
  $('#travel-origin-setup').addEventListener('click',openServiceSetup);
  $('#travel-setup-key').addEventListener('click',openServiceSetup);
  function renderNextStep(){
    let hint,label;
    $('.travel-workflow').hidden=!ready();
    if(!current.key_configured){hint='先保存高德 Web 服务 Key。';label='配置高德服务'}
    else if(!current.authorized){hint='请授权地址与路线查询。';label='前往高德授权'}
    else if(!current.origin){hint='请设置固定出发点。';label='设置固定出发点'}
    else{hint='出发点：'+current.origin.label;label=pendingOrder?'返回这条收藏计算':'去收藏里计算'}
    $('#travel-next-hint').textContent=hint;$('#travel-next-step').textContent=label;
    $('#travel-next-step').disabled=busy;
  }
  $('#travel-next-step').addEventListener('click',async()=>{
    if(!current.key_configured||!current.authorized){
      openServiceSetup();return;
    }
    if(!current.origin){$('#travel-origin-section').scrollIntoView({block:'start'});$('#travel-city').focus({preventScroll:true});return}
    if(pendingOrder){
      const target=pendingOrder;await go(target.favorites?'favorites':'orders');
      const orders=target.favorites?favoriteData:data;
      if(orders.some(order=>order.id===target.id)){renderDetail(target.id,target.favorites);document.querySelector('[data-travel-order]')?.scrollIntoView({block:'start'})}
      else{pendingOrder=null;notify('这条订单已不在当前来源，请从清单重新选择')}
    }else await go('favorites');
  });
  function accept(next){
    if(next.session===current.session&&next.version<current.version)return;
    const previous=identity();current=next;
    if(previous!==identity()||!current.authorized){results.clear();destinations.clear();errors.clear();originChoices=[]}
    render();
  }
  async function load(){accept(await api('/api/travel'))}
  async function run(fn,id){
    if(busy)return;
    busy=true;if(id)errors.delete(id);render();
    try{await fn()}
    catch(error){if(id)errors.set(id,error.message);notify(error.message||'通勤查询未完成',true)}
    finally{busy=false;render()}
  }
  async function preview(id,favorites){
    if(!ready()||!current.origin){pendingOrder={id,favorites};await go('travel');renderNextStep();$('#travel-next-step').focus({preventScroll:true});return}
    renderDetail(id,favorites,false);
    await run(async()=>{
      const version=identity();destinations.delete(id);results.delete(id);
      const answer=await api('/api/travel/orders/'+encodeURIComponent(id)+'/destinations','POST',{});
      await load();if(version!==identity())throw new Error('授权或出发点已变化，请重新查询');
      destinations.set(id,answer);
    },id);
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.travelOpen)return preview(button.dataset.travelOpen,button.dataset.travelFavorites==='true');
    if(button.dataset.travelOrigin)return run(async()=>{
      accept(await api('/api/travel/origin','PUT',{candidate_id:button.dataset.travelOrigin}));
      notify(pendingOrder?'出发点已保存，可以返回这条订单计算':'出发点已保存，可从订单点击“计算通勤”');
    });
    if(button.dataset.travelPreview)return preview(button.dataset.travelPreview,button.dataset.travelFavorites==='true');
    if(button.dataset.travelRoute)return run(async()=>{
      const id=button.dataset.travelRoute;
      const result=await api('/api/travel/orders/'+encodeURIComponent(id)+'/route','POST',{candidate_id:button.dataset.travelCandidate});
      await load();if(!fresh(result))throw new Error('授权或出发点已变化，结果已忽略');
      results.set(id,result);destinations.delete(id);pendingOrder=null;notify('通勤路线已计算，原筛选结论保留');
    },button.dataset.travelRoute);
  });
  $('#travel-settings-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{
    const body={mode:$('#travel-mode').value};if($('#travel-key').value)body.api_key=$('#travel-key').value;
    accept(await api('/api/travel/settings','PUT',body));$('#travel-key').value='';$('#travel-key-guide').open=false;notify('高德设置已保存，不会自动发起查询');
  })});
  $('#travel-authorize').addEventListener('click',()=>run(async()=>{accept(await api('/api/travel/authorization','PUT',{authorized:true}));notify('已授权，查询或计算时才发送位置')}));
  $('#travel-revoke').addEventListener('click',async()=>{
    try{accept(await api('/api/travel/authorization','PUT',{authorized:false}));notify('已撤销位置授权，后续请求已拦截')}
    catch(error){notify(error.message,true)}
  });
  $('#travel-clear-key').addEventListener('click',()=>run(async()=>{accept(await api('/api/travel/settings','PUT',{mode:current.mode||'transit',api_key:''}));$('#travel-key').value='';notify('高德Key已清除')}));
  $('#travel-clear-origin').addEventListener('click',()=>run(async()=>{accept(await api('/api/travel/origin','DELETE'));notify('出发点已清除')}));
  $('#travel-origin-form').addEventListener('submit',event=>{
    event.preventDefault();
    if(busy)return;
    if(!ready()){openServiceSetup();return}
    run(async()=>{
      originSearching=true;originChoices=[];render();
      try{
        const version=identity();const answer=await api('/api/travel/origin/search','POST',{city:$('#travel-city').value.trim(),address:$('#travel-address').value.trim()});
        await load();if(version!==identity())throw new Error('授权或设置已变化，请重新查询');originChoices=answer.candidates;
      }finally{originSearching=false}
    });
  });
  window.TutorTravel={summary,detail};
  async function bootTravel(){
    try{await load();$('#travel-mode').value=current.mode||'transit';$('#travel-key-guide').open=false;$('#travel-service-settings').open=!current.key_configured}
    catch(error){notify('通勤设置加载失败：'+error.message,true)}
    setInterval(()=>load().catch(()=>{}),3000);
  }
  bootTravel();
})();
