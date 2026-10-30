import {mountLocalTraceGlass} from '/traceglass/mount-web.js';
const root=document.querySelector('#traceglass-root'), status=document.querySelector('#feature-status');
let unmount=null, busy=false;
async function refresh() {
  try {
    const r=await (await fetch('/api/features',{credentials:'same-origin',redirect:'error'})).json();
    const row=r.ok && r.features?.find(x=>x.feature_id==='traceglass');
    status.textContent=!row?'TraceGlass is unavailable.':row.state.enabled?'Enabled':row.state.installed_version?'Installed, disabled':'Available, disabled';
    if (row?.state.enabled && !unmount) unmount=mountLocalTraceGlass(root);
    if (!row?.state.enabled && unmount) {unmount();unmount=null;}
  } catch (_) { status.textContent='The host is unavailable. Refresh before retrying.'; }
}
for (const button of document.querySelectorAll('[data-action]')) button.addEventListener('click',async()=>{
  if (busy) return; busy=true; button.disabled=true;
  try {
    const action=button.dataset.action;
    const r=await (await fetch('/api/features/action',{method:'POST',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,feature_id:'traceglass',...(action==='stage'?{version:'0.1.0'}:{})})})).json();
    if (!r.ok) {status.textContent='The action did not finish. Refresh current state before retrying.';return;}
    await refresh();
  } catch (_) {status.textContent='Outcome uncertain. Refresh state before retrying.';}
  finally {busy=false;button.disabled=false;}
});
document.querySelector('#feature-refresh').addEventListener('click',refresh);
void refresh();
