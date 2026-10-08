/* Local operator pairing card. All QR generation stays on this computer. */
(() => {
  'use strict';
  const panel=document.querySelector('.ipad-connection');
  if(!panel)return;
  const q=selector=>panel.querySelector(selector),status=q('.ipad-connection-status');
  const select=q('.ipad-address'),link=q('.ipad-link'),qr=q('.ipad-qr'),copy=q('.ipad-copy');
  let loading=false;
  function showAddress(){
    link.value=select.value;
    qr.src='/api/connection/qr?url='+encodeURIComponent(select.value);
  }
  async function refresh(){
    if(loading)return;
    loading=true;status.textContent='正在读取连接地址…';
    try{
      const response=await fetch('/api/connection',{cache:'no-store'});
      if(!response.ok)throw new Error('连接地址暂不可用，请检查主程序。');
      const data=await response.json(),urls=data.participant_urls||[];
      for(const el of [q('.ipad-address-label'),link,qr,copy])el.hidden=!data.lan_enabled||!urls.length;
      select.replaceChildren();
      if(!data.lan_enabled){status.textContent='当前仅本机模式。关闭系统后，使用默认启动入口重新启动即可连接 iPad。';return;}
      if(!urls.length){status.textContent='未找到局域网地址。请连接 Wi-Fi 或有线网络后刷新。';return;}
      for(const url of urls){const option=document.createElement('option');option.value=url;option.textContent=new URL(url).host;select.appendChild(option);}
      showAddress();status.textContent='用 iPad 相机扫码打开。若无法连接，可切换下方地址重试。';
    }catch(error){status.textContent=error.message||'无法读取连接地址，请重试。';}
    finally{loading=false;}
  }
  select.addEventListener('change',showAddress);
  q('.ipad-refresh').addEventListener('click',refresh);
  panel.addEventListener('toggle',()=>{if(panel.open)refresh();});
  copy.addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(link.value);status.textContent='连接链接已复制。';}
    catch(_){link.focus();link.select();status.textContent='请复制已选中的连接链接。';}
  });
  qr.addEventListener('error',()=>{status.textContent='二维码未加载，可复制下面的连接链接。';});
})();
