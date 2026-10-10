'use strict';
const $ = id => document.getElementById(id);
let lessons = [], current = 0, completed = new Set();
const requestedPort = new URLSearchParams(location.search).get('appPort');
const appPort = requestedPort && /^\d+$/.test(requestedPort) && Number(requestedPort)>0 && Number(requestedPort)<65536 ? requestedPort : '8000';
const sameServer = location.pathname.startsWith('/codelab');
const appUrl = hash => sameServer ? `/app${hash}` : `http://localhost:${appPort}/${hash}`;
const storageKey = 'mobileheal-codelab-progress-v1';
try { const stored = JSON.parse(localStorage.getItem(storageKey) || '[]'); if (Array.isArray(stored)) completed = new Set(stored.filter(Number.isInteger)); } catch (_) {}
const hero = '<div class="hero-kicker">BUILD IT · REVIEW IT · VERIFY IT</div>';
function persist() { try { localStorage.setItem(storageKey, JSON.stringify([...completed])); } catch (_) {} }
function updateProgress() { const count = [...completed].filter(i=>i>=0&&i<lessons.length).length; $('progress').max=lessons.length; $('progress').value=count; $('progress-text').textContent=`${count} / ${lessons.length}`; }
function nav() {
  const query = $('search').value.trim().toLowerCase(); let group = '', matches = 0;
  $('lessons').replaceChildren();
  lessons.forEach((lesson,i)=>{
    if(query&&!`${lesson.title} ${lesson.group}`.toLowerCase().includes(query)) return;
    matches++;
    if(group!==lesson.group) { const label=document.createElement('div'); label.className='group'; label.textContent=lesson.group; $('lessons').append(label); group=lesson.group; }
    const link=document.createElement('a'); link.href=`#${i}`; link.className=`lesson-link${completed.has(i)?' done':''}`;
    if(i===current) link.setAttribute('aria-current','step');
    const number=document.createElement('span'); number.className='lesson-number'; number.textContent=completed.has(i)?'✓':String(i+1).padStart(2,'0');
    const title=document.createElement('span'); title.className='lesson-title'; title.textContent=lesson.title;
    const duration=document.createElement('span'); duration.className='lesson-time'; duration.textContent=`${lesson.minutes}m`; duration.setAttribute('aria-label',`${lesson.minutes} minutes`);
    link.append(number,title,duration); $('lessons').append(link);
  });
  $('no-results').hidden=matches>0; updateProgress();
}
function toast(message) { $('toast').textContent=message; $('toast').style.display='block'; setTimeout(()=>$('toast').style.display='none',2400); }
function showLive() {
  current=-1; document.body.classList.add('live-mode'); $('live-link').setAttribute('aria-current','page');
  $('breadcrumb').textContent='LIVE DEMO APP'; $('duration').textContent=sameServer?'same server':`port ${appPort}`;
  document.title='Live demo app · MobileHeal Codelab';
  $('lesson').innerHTML=`<div class="live-frame"><div class="live-bar"><span><span class="live-dot"></span>MobileHeal is running${sameServer?' on this port':` on localhost:${appPort}`}</span><a href="${appUrl('#new')}" target="_blank" rel="noopener">Open full screen ↗</a></div>`
    +(sameServer?`<iframe src="${appUrl('#new')}" title="MobileHeal web app"></iframe>`
      :`<div class="live-off"><p>The tutorial is running on its own, so the app can’t be shown inside it.</p><p>Start both on one port with <code>bash start.sh</code>, or open the app in a new tab.</p><a class="btn-live" href="${appUrl('#new')}" target="_blank" rel="noopener">Open the app ↗</a></div>`)+'</div>';
  nav(); $('sidebar').classList.remove('open'); window.scrollTo({top:0});
}
function route(focus=false) {
  if(!lessons.length) return;
  if(location.hash==='#live') return showLive();
  document.body.classList.remove('live-mode'); $('live-link').removeAttribute('aria-current');
  const raw=location.hash.slice(1); const index=/^\d+$/.test(raw)?Number(raw):0;
  current=index>=0&&index<lessons.length?index:0;
  const lesson=lessons[current];
  $('lesson').innerHTML=(current===0?hero:'')+lesson.html;
  $('lesson').querySelectorAll('a').forEach(link=>{ const url=new URL(link.href,location.href); if(url.hostname==='localhost' && url.port==='8000'){ if(sameServer){link.href=appUrl(url.hash);} else {url.port=appPort;link.href=url.href;} } });
  $('breadcrumb').textContent=`${lesson.group} / LESSON ${String(current+1).padStart(2,'0')}`;
  $('duration').textContent=`${lesson.minutes} min`;
  document.title=`${lesson.title} · MobileHeal Codelab`;
  $('step-count').textContent=`${current+1} of ${lessons.length}`;
  $('back').href=`#${Math.max(0,current-1)}`; $('back').setAttribute('aria-disabled',String(current===0)); $('back').tabIndex=current===0?-1:0;
  $('next').href=`#${Math.min(lessons.length-1,current+1)}`;
  $('next').textContent=current===lessons.length-1?'Back to introduction ↺':`Next: ${lessons[current+1].title} →`;
  if(current===lessons.length-1) $('next').href='#0';
  $('complete').setAttribute('aria-pressed',String(completed.has(current)));
  $('complete').innerHTML=completed.has(current)?'<span aria-hidden="true">✓</span> Lesson complete':'<span aria-hidden="true">○</span> Mark lesson complete';
  setupDiagrams();
  nav(); $('sidebar').classList.remove('open'); $('menu').setAttribute('aria-expanded','false');
  window.scrollTo({top:0,behavior:'instant'});
  if(focus) $('lesson').focus({preventScroll:true});
}
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
function setDiagram(fig, playing) {
  const svg=fig.querySelector('svg.dg'); if(!svg||!svg.pauseAnimations) return;
  const btn=fig.querySelector('.dg-toggle');
  if(playing) svg.unpauseAnimations(); else svg.pauseAnimations();
  fig.classList.toggle('paused',!playing);
  if(btn){ btn.textContent=playing?'Pause':'Play'; btn.setAttribute('aria-pressed',String(!playing)); }
}
function setupDiagrams() {
  $('lesson').querySelectorAll('figure.diagram').forEach(fig=>{
    const svg=fig.querySelector('svg.dg');
    if(reduceMotion.matches){ svg.setCurrentTime(Number(svg.dataset.cycle||12)*0.94); setDiagram(fig,false); }
    else { svg.setCurrentTime(0); setDiagram(fig,true); }
  });
}
document.querySelector('.skip').addEventListener('click',event=>{event.preventDefault();$('lesson').focus();$('lesson').scrollIntoView();});
$('menu').addEventListener('click',()=>{ const open=$('sidebar').classList.toggle('open'); $('menu').setAttribute('aria-expanded',String(open)); });
$('search').addEventListener('input',nav);
$('complete').addEventListener('click',()=>{ completed.has(current)?completed.delete(current):completed.add(current); persist(); route(); });
$('reset-progress').addEventListener('click',()=>{ completed.clear(); persist(); route(); toast('Reading progress reset'); });
window.addEventListener('hashchange',()=>route(true));
$('lesson').addEventListener('click',async event=>{
  const copy=event.target.closest('.copy');
  if(copy) { const text=copy.closest('.codeblock').querySelector('pre code').textContent; try { await navigator.clipboard.writeText(text); copy.textContent='Copied ✓'; toast('Copied to clipboard'); } catch (_) { const selection=window.getSelection(); const range=document.createRange(); range.selectNodeContents(copy.closest('.codeblock').querySelector('pre')); selection.removeAllRanges(); selection.addRange(range); toast('Code selected. Press Ctrl+C or ⌘C to copy.'); } }
  const toggle=event.target.closest('.dg-toggle');
  if(toggle) { const fig=toggle.closest('figure'); setDiagram(fig, fig.classList.contains('paused')); return; }
  const replay=event.target.closest('.dg-replay');
  if(replay) { const fig=replay.closest('figure'); fig.querySelector('svg.dg').setCurrentTime(0); setDiagram(fig,true); return; }
  const image=event.target.closest('.image-open');
  if(image) { const img=image.querySelector('img'); $('large-image').src=img.src; $('large-image').alt=img.alt; $('image-caption').textContent=img.alt; $('lightbox').showModal(); }
});
$('close-image').addEventListener('click',()=>$('lightbox').close());
$('lightbox').addEventListener('click',event=>{if(event.target===$('lightbox')) $('lightbox').close();});
document.addEventListener('keydown',event=>{
  if(event.target.closest('input,textarea,select,button')||$('lightbox').open||event.ctrlKey||event.metaKey||event.altKey) return;
  if(event.key==='ArrowRight'&&current<lessons.length-1) location.hash=String(current+1);
  if(event.key==='ArrowLeft'&&current>0) location.hash=String(current-1);
  if(event.key==='Escape') { $('sidebar').classList.remove('open'); $('menu').setAttribute('aria-expanded','false'); }
});
(async()=>{
  try { const response=await fetch('steps.json'); if(!response.ok) throw new Error('Could not load lessons'); lessons=await response.json(); $('total-time').textContent=lessons.reduce((n,l)=>n+l.minutes,0); $('open-app').href=appUrl('#new'); route(); }
  catch (_) { $('lesson').innerHTML='<h2>Serve the guide locally</h2><p>From the repository root, run <code>bash scripts/serve-guide.sh</code> and open <a href="http://localhost:8080/">localhost:8080</a>. You can also <a href="guide.md">read the Markdown guide</a>.</p>'; }
})();
