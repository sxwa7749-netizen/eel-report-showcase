/* 页面会话只存于 sessionStorage，不记住登录；刷新保留，新进入需登录。 */
"use strict";
(()=>{
  const script=document.currentScript,scope=script.dataset.sessionScope;
  const key='demo.page-session.'+scope,pending=key+'.pending';
  const loginPage=script.hasAttribute('data-login-page'),required=script.dataset.sessionRequired!=='0';
  let usable=true;
  const read=()=>{try{return sessionStorage.getItem(key)||'';}catch{return '';}};
  const clear=()=>{try{sessionStorage.removeItem(key);sessionStorage.removeItem(pending);}catch{}};
  const enter=()=>{try{sessionStorage.removeItem(pending);}catch{}window.DemoPageSession.allowed=true;};
  window.DemoPageSession={allowed:!required,enter,clear};
  const originalFetch=window.fetch.bind(window);
  window.fetch=(input,options={})=>{
    const url=new URL(input instanceof Request?input.url:String(input),location.href);
    const protectedPath=url.pathname.startsWith('/api/')||url.pathname.startsWith('/eel/api/')||url.pathname.startsWith('/developer/api/')||url.pathname==='/developer/logout';
    if(url.origin===location.origin&&protectedPath){
      const headers=new Headers(input instanceof Request?input.headers:undefined);
      new Headers(options.headers).forEach((value,name)=>headers.set(name,value));
      if(read())headers.set('X-Page-Session',read());
      options={...options,headers};
    }
    return originalFetch(input,options);
  };
  addEventListener('pageshow',event=>{if(event.persisted&&window.DemoPageSession.allowed&&required){clear();location.replace('login');}});
  if(loginPage){
    clear();
    if(scope==='eel')try{['page','bossTab','bossFarm'].forEach(name=>sessionStorage.removeItem('demo.'+name));}catch{}
    document.querySelector('form').addEventListener('submit',event=>{
      try{
        const token=Array.from(crypto.getRandomValues(new Uint8Array(32)),v=>v.toString(16).padStart(2,'0')).join('');
        sessionStorage.setItem(key,token);sessionStorage.setItem(pending,'1');
        let field=event.currentTarget.querySelector('[name=tab_token]');
        if(!field){field=document.createElement('input');field.type='hidden';field.name='tab_token';event.currentTarget.append(field);}
        field.value=token;
      }catch{
        event.preventDefault();event.stopImmediatePropagation();
        let error=event.currentTarget.querySelector('.err');
        if(!error){error=document.createElement('p');error.className='err';event.currentTarget.append(error);}
        error.textContent='请允许本站使用当前页面会话存储后再登录。';
      }
    });
    return;
  }
  if(required){
    try{
      const reload=performance.getEntriesByType('navigation')[0]?.type==='reload';
      usable=!!read()&&(reload||sessionStorage.getItem(pending)==='1');
    }catch{usable=false;}
    if(!usable){clear();location.replace('login');return;}
    enter();
  }
})();
