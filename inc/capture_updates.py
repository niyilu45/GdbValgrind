"""Local-file and HTTP compatible incremental capture updates."""

SCRIPT = r'''
<script>
(()=>{
 let running=true,timer;
 const area=document.getElementById('capture-items'),status=document.getElementById('capture-status');
 const nodes=new Map([...area.querySelectorAll('details[data-capture]')].map(n=>[n.dataset.capture,n]));
 window.aivCaptureUpdate=data=>{
  for(const item of data.items){
   let node=nodes.get(item.id);
   if(!node){
    node=document.createElement('details');node.dataset.capture=item.id;
    const title=document.createElement('summary');title.textContent=item.id;
    const body=document.createElement('pre');body.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';
    node.append(title,body);area.append(node);nodes.set(item.id,node);
   }
   const body=node.querySelector('pre');if(body.textContent!==item.text)body.textContent=item.text;
  }
  running=data.live;
  status.textContent=(running?'采集中，内容自动更新':'采集已结束')+' · 已保存 '+data.items.length+' 个现场';
 };
 function poll(){
  if(!running)return;
  const script=document.createElement('script');
  script.src='capture-updates.js?t='+Date.now();
  const finish=()=>{clearTimeout(timer);script.remove();if(running)timer=setTimeout(poll,2000)};
  script.onload=finish;script.onerror=finish;timer=setTimeout(finish,10000);
  document.head.append(script);
 }
 poll();
})();
</script>
'''
