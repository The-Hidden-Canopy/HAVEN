import {SharedSessionClient} from './session-contract.mjs';
const $=id=>document.getElementById(id), status=$('status');
const api=new SharedSessionClient({call:async(tool,args,request_id)=>{
  const r=await fetch('/api/sessions/call',{method:'POST',credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool,request_id,arguments:args})});
  const result=await r.json(); if(!r.ok||!result.ok){const error=new Error(result.code||'unavailable');error.notExecuted=[400,401,403,409].includes(r.status)||result.code==='session_provider_unavailable';throw error;}return result;
}});
let cursor=0,busy=false,selectionGeneration=0;
const key='haven.session.pending.v1';
const id=()=>crypto.randomUUID();
const selected=()=>{if(!$('sessions').value)throw new Error('Select an authorized session.');return $('sessions').value;};
async function guarded(fn){if(busy)return;busy=true;try{await fn();}catch(e){status.textContent=e.message==='access_denied'?'Access denied.':e.message==='session_provider_unavailable'?'The session provider is unavailable.':e.message;}finally{busy=false;}}
async function mutation(invoke,expectedSession){
  if(localStorage.getItem(key))throw new Error('Inspect the previous uncertain command before another request.');
  const session=selected(), request=id();
  if(expectedSession && expectedSession!==session)throw new Error('Reload the selected session before acting.');
  localStorage.setItem(key,JSON.stringify({session,request}));
  try{const result=await invoke(session,request);localStorage.removeItem(key);status.textContent='Host response: '+JSON.stringify(result);}
  catch(error){if(error.notExecuted||error.message==='capability_unavailable'){localStorage.removeItem(key);status.textContent='Request rejected before execution.';}else status.textContent='Outcome uncertain. Inspect the command before retrying.';}
}
function button(label,fn){const b=document.createElement('button');b.type='button';b.textContent=label;b.addEventListener('click',()=>guarded(fn));return b;}
$('load').addEventListener('click',()=>guarded(async()=>{
  await api.capabilities(id());const result=await api.sessions(id());$('sessions').replaceChildren();
  for(const row of result.sessions){const option=document.createElement('option');option.value=option.textContent=row.session_id;$('sessions').append(option);}status.textContent=result.sessions.length?'Select a session.':'No authorized sessions.';
}));
$('sessions').addEventListener('change',()=>{selectionGeneration++;cursor=0;$('messages').textContent='';$('jobs').replaceChildren();$('approvals').replaceChildren();});
$('snapshot').addEventListener('click',()=>guarded(async()=>{
  const session=selected(),generation=selectionGeneration,result=await api.snapshot(session,id());if(generation!==selectionGeneration)return;$('jobs').replaceChildren();$('approvals').replaceChildren();
  for(const job of result.jobs){const line=document.createElement('p');line.textContent=job.job_id+': '+job.state;
    line.append(button('Request cancellation',async()=>{if(confirm('Request cancellation of '+job.job_id+'?'))await mutation((s,r)=>api.cancel(s,job.job_id,r),session);}));$('jobs').append(line);}
  for(const approval of result.approvals){const line=document.createElement('p');line.textContent=(approval.description||approval.approval_id)+': '+approval.status;
    if(approval.status==='pending')for(const allow of [true,false])line.append(button(allow?'Approve':'Deny',async()=>{if(confirm((allow?'Approve: ':'Deny: ')+approval.description))await mutation((s,r)=>api.decide(s,approval.approval_id,approval.revision,allow,r),session);}));$('approvals').append(line);}
  status.textContent='Current host snapshot.';
}));
$('replay').addEventListener('click',()=>guarded(async()=>{const generation=selectionGeneration,result=await api.replay(selected(),cursor,id());if(generation!==selectionGeneration)return;for(const event of result.events)$('messages').textContent+=JSON.stringify(event)+'\n';cursor=result.next_cursor;status.textContent='Replayed through '+cursor;}));
$('send').addEventListener('click',()=>guarded(()=>mutation((s,r)=>api.submit(s,$('message').value,r))));
$('reconcile').addEventListener('click',()=>guarded(async()=>{
  const raw=localStorage.getItem(key);if(!raw){status.textContent='No uncertain command in this browser.';return;}
  const pending=JSON.parse(raw);if(!api.operations)await api.capabilities(id());const result=await api.commandStatus(pending.session,pending.request,id());
  status.textContent=result.state==='recorded'?'Command recorded: '+JSON.stringify(result.result):'Outcome remains unknown. The command has not been retried.';
  if(result.state==='recorded')localStorage.removeItem(key);
}));
