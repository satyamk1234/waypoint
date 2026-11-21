'use strict';
const $=s=>document.querySelector(s);
let session=localStorage.getItem('waypoint-session');
let lastJobs=[];
const notice=message=>{ $('#notice').textContent=message; $('#notice').hidden=!message; };
async function api(path,options={}){
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json','X-Session-ID':session||'',...(options.headers||{})}});
  if(!response.ok){let value;try{value=await response.json();}catch{value={detail:'Request failed'};}throw new Error(typeof value.detail==='string'?value.detail:'Please check the submitted fields.');}
  return response.status===204?null:response.json();
}
function el(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function applyPreferences(prefs){const form=$('#preferences-form');for(const name of ['role','location','country','employment_type'])form.elements[name].value=prefs[name]||'';form.elements.remote.value=prefs.remote==null?'':String(prefs.remote);form.elements.skills.value=prefs.skills.join(', ');form.elements.real_only.checked=prefs.real_only!==false;}
function bubble(role,message){const item=el('div','bubble '+role,message);$('#messages').append(item);$('#messages').scrollTop=$('#messages').scrollHeight;return item;}
function safeLink(url,label,className='source-credit'){
  try {const parsed=new URL(url);if(!['https:','http:'].includes(parsed.protocol))return null;
    const link=el('a',className,label);link.href=url;link.target='_blank';link.rel='noopener noreferrer';return link;
  }catch{return null;}
}
function details(job){
  const root=$('#details-content');
  root.replaceChildren(el('h2','',job.title),el('p','company',job.company+' · '+job.location),el('p','',job.salary));
  root.append(el('p','job-meta',job.location_scope==='worldwide'?'Country eligibility: worldwide':job.location_scope==='countries'?'Eligible countries: '+job.countries.join(', '):'Country eligibility: not specified'));
  if(job.timezones.length)root.append(el('p','job-meta','Accepted timezone offsets (UTC hours): '+job.timezones.join(', ')));
  if(job.published_at)root.append(el('p','job-meta','Updated by source: '+new Date(job.published_at*1000).toLocaleDateString()));
  if(job.expires_at)root.append(el('p','job-meta','Listed expiry: '+new Date(job.expires_at*1000).toLocaleDateString()+'. The employer may close the job earlier.'));
  root.append(el('p','',job.description));
  if(job.source!=='sample'){
    const source=safeLink(job.source_url||job.url,'Source: '+job.source);if(source)root.append(source,el('br'));
    const apply=safeLink(job.url,'View original posting / apply','primary');if(apply)root.append(apply);
  }else root.append(el('p','job-meta','Fictional sample job for demonstrating the project.'));
  $('#details').showModal();
}
function renderJobs(jobs){lastJobs=jobs;const root=$('#results');root.replaceChildren();$('#results-title').textContent=jobs.length?'Your matches':'No matching roles';$('#results-subtitle').textContent=jobs.length?'Ranked against your search and saved preferences.':'Try a different search or adjust your preferences.';if(!jobs.length){const empty=el('div','empty-state');empty.append(el('div','empty-icon','⌕'),el('h3','','A fresh search might help.'),el('p','','We keep your saved filters strict. You can change them on the left.'));root.append(empty);return;}for(const job of jobs){const card=el('article','job-card');const top=el('div','card-top');top.append(el('span','company-icon',job.company.slice(0,1)));if(job.source==='sample')top.append(el('span','sample-label','Sample job'));else{const credit=safeLink(job.source_url||job.url,job.source);if(credit)top.append(credit);}const tags=el('div','tags');for(const value of [job.remote?'Remote':'On-site',job.employment_type.replaceAll('_',' '),...job.skills.slice(0,2)])tags.append(el('span','tag',value));const bottom=el('div','card-bottom');bottom.append(el('small','',job.location));const button=el('button','','View details');button.addEventListener('click',()=>details(job));bottom.append(button);card.append(top,el('h3','',job.title),el('div','company',job.company),tags,el('p','reason',job.reasons.join(' · ')),bottom);root.append(card);}}
async function searchJobs(query){const button=$('#search-form button');button.disabled=true;notice('Finding your matches…');try{const data=await api('/search',{method:'POST',body:JSON.stringify({query,limit:12,mode:'rerank'})});renderJobs(data.jobs);notice('');}catch(error){notice(error.message);}finally{button.disabled=false;}}
$('#search-form').addEventListener('submit',async event=>{event.preventDefault();await ready;searchJobs($('#query').value.trim());});
document.querySelectorAll('[data-query]').forEach(button=>button.addEventListener('click',async()=>{await ready;$('#query').value=button.dataset.query;searchJobs(button.dataset.query);}));
$('#preferences-form').addEventListener('submit',async event=>{event.preventDefault();await ready;const values=Object.fromEntries(new FormData(event.target));const payload={role:values.role.trim()||null,location:values.location.trim()||null,country:values.country.trim()||null,real_only:event.target.elements.real_only.checked,remote:values.remote===''?null:values.remote==='true',employment_type:values.employment_type||null,skills:values.skills.split(',').map(s=>s.trim()).filter(Boolean)};try{applyPreferences(await api('/preferences',{method:'PATCH',body:JSON.stringify(payload)}));$('#saved-status').textContent='Saved';notice('Preferences saved. Your next search will use these filters.');if(lastJobs.length){renderJobs([]);$('#results-subtitle').textContent='Preferences changed. Run a new search for updated matches.';}}catch(error){notice(error.message);}});
$('#chat-form').addEventListener('submit',async event=>{event.preventDefault();await ready;const message=$('#chat-input').value.trim();if(!message)return;$('#send').disabled=true;$('#chat-input').value='';bubble('user',message);const pending=bubble('assistant','Thinking through your preferences…');try{const answer=await api('/chat',{method:'POST',body:JSON.stringify({message})});pending.textContent=answer.message;applyPreferences(answer.preferences);if(answer.jobs.length)renderJobs(answer.jobs);else if(answer.trace.some(item=>item.tool==='search_jobs'&&item.ok))renderJobs([]);}catch(error){pending.classList.add('error');pending.textContent=error.message;}finally{$('#send').disabled=false;$('#messages').scrollTop=$('#messages').scrollHeight;}});
$('#close-details').addEventListener('click',()=>$('#details').close());
$('#reset').addEventListener('click',async()=>{await ready;if(!confirm('Delete this session, conversation, and saved preferences?'))return;try{await api('/session',{method:'DELETE'});localStorage.removeItem('waypoint-session');location.reload();}catch(error){notice(error.message);}});
async function updateHealth(){
  const health=await api('/health');
  $('#job-count').textContent=health.real_jobs+' real · '+(health.searchable_jobs-health.real_jobs)+' other indexed';
  $('#chat-status').textContent=health.chat_configured?'Groq configured':'Groq key needed';
  if(!health.chat_configured)$('#chat-help').textContent='Add your Groq key to .env and restart to enable chat.';
  const states=health.sources.map(source=>{
    const date=source.last_success?new Date(source.last_success.endsWith('Z')||source.last_success.includes('+')?source.last_success:source.last_success+'Z').toLocaleString():'never';
    const coverage=source.detail.complete_snapshot===false?' · limited feed':'';
    return source.source+': '+source.status+' · last successful refresh '+date+coverage;
  });
  $('#refresh-status').textContent=(health.refresh_enabled?'Refreshes every '+health.refresh_hours+' hours while the server runs. ':'Automatic refresh is off. ')+states.join(' | ');
  if(!health.real_jobs)notice('No fresh real jobs are indexed yet. Check the refresh status or run the import command in the README.');
  return health;
}
async function init(){try{await updateHealth();if(!session){session=(await api('/sessions',{method:'POST'})).session_id;localStorage.setItem('waypoint-session',session);}let prefs;try{prefs=await api('/preferences');}catch(error){if(!error.message.includes('Create a session'))throw error;session=(await api('/sessions',{method:'POST'})).session_id;localStorage.setItem('waypoint-session',session);prefs=await api('/preferences');}applyPreferences(prefs);for(const item of await api('/history'))bubble(item.role,item.content);}catch(error){notice('Could not initialize: '+error.message);}}
const ready=init();

setInterval(()=>updateHealth().catch(()=>{}),60000);
