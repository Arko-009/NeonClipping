const $=id=>document.getElementById(id),v=$('video'),c=$('source'),ctx=c.getContext('2d'),pc=$('preview').getContext('2d');
let id=null,still=null,sourceW=0,sourceH=0,sourceDuration=0,mode='cam',layout='stack',style='gta',drag=null,highlights=[],words=null,wordRange='',pendingImport=null,importToken=0,selectedTime=0;
let rectEdited=false,layoutClicked=false,layoutSpecs={},editingClip=null,layoutLoadSerial=0;
let rect={cam:{x:.72,y:.40,w:.28,h:.28},game:{x:.25,y:0,w:.50,h:1}};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const status=t=>$('status').textContent=t;
async function request(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.detail||'Request failed');return data;}
function signature(){return [id,Number($('start').value),Number($('end').value),$('language').value].join(':');}
function clearWords(){words=null;wordRange='';$('words').replaceChildren();}
function setRange(start,end){$('start').value=start;$('end').value=end;clearWords();}
function setSource(j,seek=0){editingClip=null;$('saved-edit').hidden=true;id=j.id;sourceW=j.w;sourceH=j.h;sourceDuration=j.duration;still=null;v.onloadeddata=()=>{if(seek>0&&seek<v.duration)v.currentTime=seek;};v.src='/media/'+id;c.width=j.w;c.height=j.h;$('empty-source').hidden=true;$('source-mode').textContent='SOURCE READY';}
async function requireSource(){if(pendingImport&&!id){status('Source is downloading. Your layout is saved; this step will continue when ready.');await pendingImport;}if(!id)throw Error('Add a video link or upload a file first.');}
function showFrame(frame,meta,button){const image=new Image();image.onload=()=>{still=image;sourceW=image.naturalWidth;sourceH=image.naturalHeight;c.width=sourceW;c.height=sourceH;sourceDuration=meta.duration;selectedTime=frame.time;$('empty-source').hidden=true;$('source-mode').textContent=frame.kind.includes('storyboard')?'EARLY FRAME · LOW RES':'PREVIEW FRAME';v.pause();document.querySelectorAll('.frame-btn').forEach(b=>b.classList.remove('active'));button.classList.add('active');};image.src=frame.url;}
function addFrames(frames,meta){const current=$('frames').children.length;frames.slice(current).forEach((frame,index)=>{const button=document.createElement('button');button.className='frame-btn';const image=document.createElement('img');image.src=frame.url;image.alt='Source frame at '+frame.time+' seconds';const label=document.createElement('small');label.textContent=frame.time+'s';button.append(image,label);button.onclick=()=>showFrame(frame,meta,button);$('frames').append(button);if(current===0&&index===0&&!id){showFrame(frame,meta,button);if(Number($('end').value)>meta.duration)$('end').value=meta.duration;}});}
$('link').onclick=async()=>{
 const url=$('url').value.trim();if(!url){status('Paste a video link first.');return;}
 const token=++importToken;id=null;still=null;clearWords();$('frames').replaceChildren();$('link').disabled=true;$('progress-wrap').hidden=false;$('progress').style.width='2%';$('link-status').textContent='Getting early source frames…';
 const operation=(async()=>{const {job}=await request('/link',{url});let completed=false;
  for(;;){const s=await request('/status/'+job);if(token!==importToken)return;if(s.preview_frames)addFrames(s.preview_frames,s.preview_meta);
   $('link-status').textContent=s.status==='done'?'Source ready. Your selected areas are preserved.':(s.message||s.stage||'Importing source');$('progress').style.width=(s.status==='done'?100:Math.min(95,(s.progress||.05)*300))+'%';
   if(s.status==='error')throw Error(s.error);
   if(s.status==='done'&&!completed){completed=true;setSource(s,selectedTime);if(!Number($('end').value)||Number($('end').value)>s.duration)$('end').value=Math.min(30,s.duration);$('link-status').textContent='Source ready. Your selected areas are preserved.';status('Source ready. Find highlights or export your selected range.');}
   if(completed&&(s.preview_status!=='loading'||s.preview_frames?.length>=4))break;
   await sleep(700);
  }
 })();pendingImport=operation;
 try{await operation;}catch(e){$('link-status').textContent=e.message;status(e.message);}finally{if(token===importToken){pendingImport=null;$('link').disabled=false;}}
 if(token===importToken&&id&&$('auto-analyze').checked&&!$('analyze').disabled)await $('analyze').onclick();
};
$('file').onchange=async()=>{try{if(!$('file').files[0])return;++importToken;pendingImport=null;id=null;still=null;$('link').disabled=false;$('frames').replaceChildren();status('Uploading source…');const form=new FormData();form.append('file',$('file').files[0]);const response=await fetch('/upload',{method:'POST',body:form});const j=await response.json();if(!response.ok)throw Error(j.detail);$('url').value='';setSource(j);setRange(0,Math.min(30,j.duration));$('link-status').textContent='File ready';status('Draw the facecam and gameplay areas.');}catch(e){status(e.message);}};
function showHighlights(jobs){highlights=jobs.flatMap(job=>(job.clips||[]).map(clip=>({...clip,job:job.job||job.id,video:job.title})));highlights=highlights.filter(h=>Number.isFinite(h.start)&&Number.isFinite(h.end));const select=$('recent');select.replaceChildren();if(!highlights.length){const option=document.createElement('option');option.textContent='No highlights yet — add a video and Find highlights';select.append(option);}highlights.forEach((h,index)=>{const option=document.createElement('option');option.value=index;option.textContent=h.start.toFixed(1)+'–'+h.end.toFixed(1)+'s · '+h.title+(h.score!==undefined?' · activity '+h.score:'');select.append(option);});}
async function openHighlight(){const h=highlights[Number($('recent').value)];if(!h)return;const j=await request('/highlight-source',{job:h.job});setSource(j,h.start);setRange(h.start,h.end);status('Moment loaded. Review the source and selected areas.');}
$('import').onclick=()=>openHighlight().catch(e=>status(e.message));
$('analyze').onclick=async()=>{const button=$('analyze');button.disabled=true;try{if(!id&&!pendingImport&&$('url').value.trim()){await $('link').onclick();}await requireSource();status('Finding activity highlights locally…');const {job}=await request('/analyze',{id,count:Number($('count').value),length:Number($('length').value),min_len:Number($('minlen').value)});for(;;){const s=await request('/analysis/'+job);status((s.stage||s.status)+' · '+Math.round((s.progress||0)*100)+'%');if(s.status==='error')throw Error(s.error);if(s.status==='done'){showHighlights([s]);await openHighlight();status('Highlights ready. Choose a moment, review it and export.');break;}await sleep(900);}}catch(e){status(e.message);}finally{button.disabled=false;}};
['cam','game'].forEach(k=>$(k).onclick=()=>{mode=k;['cam','game'].forEach(x=>$(x).classList.toggle('active',x===k));});
function point(e){const b=c.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))};}
c.onpointerdown=e=>{if(!still&&v.readyState<2)return;drag=point(e);c.setPointerCapture(e.pointerId);};c.onpointermove=e=>{if(!drag)return;rectEdited=true;const p=point(e);rect[mode]={x:Math.min(p.x,drag.x),y:Math.min(p.y,drag.y),w:Math.max(.01,Math.abs(p.x-drag.x)),h:Math.max(.01,Math.abs(p.y-drag.y))};};c.onpointerup=()=>drag=null;c.onpointercancel=()=>drag=null;
$('save').onclick=()=>{localStorage.setItem('ajay-crop',JSON.stringify({rect,top:$('top')?.value||32,layout}));status('Layout saved in this browser.');};$('load').onclick=()=>{try{const j=JSON.parse(localStorage.getItem('ajay-crop')||'null');if(j){rect=j.rect;rectEdited=true;if($('top'))$('top').value=j.top;setLayout(j.layout||'stack');status('Saved layout loaded.');}else status('No saved layout yet.');}catch(e){status('Saved layout could not be read.');}};
function setLayout(value){layout=value;if($('edit-top')&&$('top'))$('edit-top').value=$('top').value;document.querySelectorAll('[data-layout]').forEach(b=>b.classList.toggle('active',b.dataset.layout===layout));if($('height-control'))$('height-control').hidden=['blur','fill','game'].includes(layout);}
document.querySelectorAll('[data-layout]').forEach(b=>b.onclick=()=>{layoutClicked=true;setLayout(b.dataset.layout);});document.querySelectorAll('[data-style]').forEach(b=>b.onclick=()=>{style=b.dataset.style;document.querySelectorAll('[data-style]').forEach(x=>x.classList.toggle('active',x===b));});
v.onplay=()=>still=null;v.ontimeupdate=()=>{const end=Number($('end')?.value||0);if(end>Number($('start')?.value||0)&&v.currentTime>end&&end<v.duration)v.pause();};
['start','end','language'].forEach(key=>{const el=$(key);if(el)el.addEventListener('change',()=>{clearWords();if(key==='start'&&id)v.currentTime=Math.max(0,Math.min(v.duration||Infinity,Number(el.value)));});});
function cover(source,r,x,y,w,h){const sw=r.w*sourceW,sh=r.h*sourceH,ratio=Math.max(w/sw,h/sh),cw=w/ratio,ch=h/ratio;pc.drawImage(source,r.x*sourceW+(sw-cw)/2,r.y*sourceH+(sh-ch)/2,cw,ch,x,y,w,h);}
function captionPreview(){if(!$('captions')?.checked)return;let line='YOUR NEXT BIG MOMENT';if(words&&wordRange===signature()&&words.length){const time=Math.max(0,v.currentTime-Number($('start')?.value||0));let index=words.findIndex(w=>time>=w.start&&time<=w.end);if(index<0)index=0;line=words.slice(Math.floor(index/3)*3,Math.floor(index/3)*3+3).map(w=>w.text).join(' ');}const spec=layoutSpecs[layout];if(!spec)return;const scale=pc.canvas.width/spec.width,fs=(style==='clean'?58:72)*Math.min(spec.width/1080,spec.height/1920)*scale;pc.save();pc.textAlign='center';pc.textBaseline='middle';pc.font='900 '+fs+'px Arial';const max=pc.canvas.width*.9;while(pc.measureText(line).width>max){const size=parseFloat(pc.font.match(/(\d+(?:\.\d+)?)px/)[1]);pc.font='900 '+Math.max(8,size-1)+'px Arial';if(size<=8)break;}const position=Number($('caption-y')?.value)===1450?spec.caption_y:Number($('caption-y')?.value||1450)/1920*spec.height;pc.lineWidth=3;pc.strokeStyle='#160b22';pc.shadowBlur=style==='gta'?8:0;pc.shadowColor='#ff4ba5';pc.strokeText(line,pc.canvas.width/2,position*scale);pc.fillStyle=style==='gta'?'#58e8f7':style==='bold'?'#d4fb58':'white';pc.fillText(line,pc.canvas.width/2,position*scale);pc.restore();}
function hookPreview(){const text=$('hook')?.value?.trim()||'';if(!text)return;const spec=layoutSpecs[layout];if(!spec)return;const duration=Number($('hook-duration')?.value||8);if(!still&&v.currentTime-Number($('start')?.value||0)>duration)return;const factor=pc.canvas.width/spec.width,scale=Math.min(spec.width/1080,spec.height/1920);let raw=text.slice(0,60).toUpperCase(),lines=[],line='';const width=Math.max(12,Math.floor(spec.width/(82*scale*.6)));for(const word of raw.split(/\s+/)){if(line&&(line+' '+word).length>width){lines.push(line);line=word;}else line+=(line?' ':'')+word;}if(line)lines.push(line);lines=lines.slice(0,3);const size=Math.round((lines.length===1?78:lines.length===2?72:66)*scale)*factor,pad=Math.max(8,Math.round(26*scale))*factor,lh=Math.round(size*1.22);pc.save();pc.font='900 '+size+'px Arial';pc.textAlign='center';pc.textBaseline='top';const boxW=Math.min(pc.canvas.width*.94,Math.max(...lines.map(t=>pc.measureText(t).width))+pad*2),boxH=lh*lines.length+pad*2,y=Math.max(pad,Math.min(spec.hook_y*factor,pc.canvas.height-boxH-pad));if($('hook-background')?.checked!==false){pc.fillStyle='rgba(0,0,0,.88)';pc.fillRect((pc.canvas.width-boxW)/2,y,boxW,boxH);}pc.fillStyle='white';pc.strokeStyle='black';pc.lineWidth=2*factor;lines.forEach((value,i)=>{pc.strokeText(value,pc.canvas.width/2,y+pad+i*lh);pc.fillText(value,pc.canvas.width/2,y+pad+i*lh);});pc.restore();}
function paint(){const source=still||(v.readyState>=2?v:null);if(source){if(!still){sourceW=v.videoWidth;sourceH=v.videoHeight;}if(sourceW&&sourceH){ctx.drawImage(source,0,0,c.width,c.height);['cam','game'].forEach(k=>{const r=rect[k];ctx.strokeStyle=k==='cam'?'#ff4ba5':'#58e8f7';ctx.lineWidth=Math.max(2,c.width/350);ctx.strokeRect(r.x*c.width,r.y*c.height,r.w*c.width,r.h*c.height);});pc.clearRect(0,0,540,960);drawNativeLayout(source);captionPreview();hookPreview();}}if($('height-value')&&$('top'))$('height-value').textContent=$('top').value+'%';requestAnimationFrame(paint);}paint();
function collectWords(){if(words&&wordRange===signature()){const duration=Number($('end').value)-Number($('start').value);if(words.some(w=>!Number.isFinite(w.start)||!Number.isFinite(w.end)||w.start<0||w.end<=w.start||w.end>duration+.1))throw Error('Check edited subtitle start/end times before exporting.');return words;}return null;}
function renderWords(){const editor=$('words');editor.replaceChildren();const timing=$('timing-editor')?.checked;editor.classList.toggle('timing',timing);for(const word of words||[]){const input=document.createElement('input');input.value=word.text;input.title=word.start.toFixed(2)+'–'+word.end.toFixed(2)+'s';input.oninput=()=>word.text=input.value;if(!timing){editor.append(input);continue;}const row=document.createElement('div');row.className='word-timing';row.append(input);for(const key of ['start','end']){const field=document.createElement('input');field.type='number';field.step='.01';field.min='0';field.value=Math.round(word[key]*1000)/1000;field.setAttribute('aria-label',key+' '+word.text);field.onchange=()=>{word[key]=Number(field.value);field.setAttribute('aria-invalid',String(!Number.isFinite(word[key])||word.start<0||word.end<=word.start));};row.append(field);}const merge=document.createElement('button');merge.textContent='Merge next';const index=words.indexOf(word);merge.disabled=index===words.length-1;merge.onclick=()=>{const next=words[index+1];words.splice(index,2,{start:Math.min(word.start,next.start),end:Math.max(word.end,next.end),text:word.text+' '+next.text});renderWords();};const split=document.createElement('button');split.textContent='Split words';split.onclick=()=>{const tokens=word.text.trim().split(/\s+/);if(tokens.length<2){status('Type two or more words in this row to split it.');return;}const total=tokens.reduce((n,t)=>n+t.length+1,0);let time=word.start;const parts=tokens.map(t=>{const start=time;time+=(word.end-word.start)*(t.length+1)/total;return{start,end:time,text:t};});words.splice(index,1,...parts);renderWords();status('Words split with estimated within-cue timings; review them before applying.');};row.append(merge,split);editor.append(row);}}
async function generateSubtitles(){await requireSource();status('Starting local subtitles…');const range=signature();const {job}=await request('/transcribe',{id,start:Number($('start').value),end:Number($('end').value),language:$('language').value});for(;;){const s=await request('/status/'+job);status(s.message||s.status);if(s.status==='error')throw Error(s.error);if(s.status==='done'){if(signature()!==range)throw Error('Selected range changed. Generate subtitles for the current range.');words=s.words;wordRange=range;renderWords();const text=words.map(w=>w.text).join(' ');if(!$('clip-title').value)$('clip-title').value=text.split(/\s+/).slice(0,9).join(' ');if(!$('post-caption').value)$('post-caption').value=text.slice(0,220)+(text.length>220?'…':'')+'\n\n#Gaming #Clips';status(words.length?'Subtitles ready. Edit any word before exporting.':'No speech detected in this range.');return;}await sleep(1100);}}
$('transcribe').onclick=async()=>{$('transcribe').disabled=true;try{await generateSubtitles();}catch(e){status(e.message);}finally{$('transcribe').disabled=false;}};
function exportSettings(source,start,end){const b={id:source,...rect,layout,aspect:$('aspect')?.value||'vertical',resolution:Number($('resolution')?.value||1080),fps:Number($('fps')?.value||0),top:Number($('top')?.value||32)/100,start,end,captions:$('captions')?$('captions').checked:true,caption_style:style||'gta',caption_y:Number($('caption-y')?.value||1450),language:$('language')?.value||'',hook:$('hook')?.value||'',title:$('clip-title')?.value||'',post_caption:$('post-caption')?.value||''};if(source===id&&start===Number($('start')?.value||0)&&end===Number($('end')?.value||0)){const edited=collectWords();if(edited!==null)b.words=edited;}return b;}
async function runExport(body,prefix=''){const {job}=await request('/export',body);for(;;){const s=await request('/status/'+job);status(prefix+(s.message||'Rendering…'));if(s.status==='error')throw Error(s.error);if(s.status==='done')return s;await sleep(1000);}}
function showDownload(s){$('vtt').hidden=!s.srt;if(s.srt)$('vtt').href='/text/'+s.file+'/vtt';$('download').href='/media/'+s.file;$('download').hidden=false;$('srt').hidden=!s.srt;if(s.srt)$('srt').href=s.srt;}
$('export').onclick=async()=>{const button=$('export');button.disabled=true;$('download').hidden=true;$('srt').hidden=true;try{await requireSource();const s=await runExport(exportSettings(id,Number($('start').value),Number($('end').value)));showDownload(s);status('Ready · '+s.meta.w+' × '+s.meta.h+' · '+s.encoder+' · '+s.caption_words+' caption words');await refreshLibrary();}catch(e){status(e.message);}finally{button.disabled=false;}};
$('batch').onclick=async()=>{const button=$('batch');button.disabled=true;$('export').disabled=true;try{await requireSource();const selected=highlights[Number($('recent').value)];if(!selected)throw Error('Find highlights first.');const candidates=highlights.filter(h=>h.job===selected.job);const source=await request('/highlight-source',{job:selected.job});for(let i=0;i<candidates.length;i++){const h=candidates[i],body=exportSettings(source.id,h.start,h.end);body.title=($('clip-title').value||'Highlight')+' '+(i+1);const s=await runExport(body,'Clip '+(i+1)+' / '+candidates.length+' · ');showDownload(s);await refreshLibrary();}status(candidates.length+' clips exported. Open My clips to preview and download.');}catch(e){status(e.message);}finally{button.disabled=false;$('export').disabled=false;}};
// Library with many clips: only fetch a video's metadata once it scrolls near the screen.
const lazyObserver=('IntersectionObserver' in window)?new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.preload='metadata';e.target.load();lazyObserver.unobserve(e.target);}}),{rootMargin:'400px'}):null;
function appendVisualReview(parent,review){if(!review?.frames?.length)return;const details=document.createElement('details'),title=document.createElement('summary');title.textContent='AI frame observations';details.append(title);const grid=document.createElement('div');grid.className='vision-grid';review.frames.forEach((frame,index)=>{const figure=document.createElement('figure'),image=document.createElement('img'),caption=document.createElement('figcaption');image.loading='lazy';if(/^\/vision\/frame\/[a-f0-9]{64}\/[012]$/.test(frame.image||''))image.src=frame.image;image.alt='Scene sample '+(index+1);caption.textContent=frame.description;figure.append(image,caption);grid.append(figure);});details.append(grid);parent.append(details);}
function lazyVideo(v){if(lazyObserver){v.preload='none';lazyObserver.observe(v);}else v.preload='metadata';}
async function refreshLibrary(){const clips=await request('/library');$('library-count').textContent=clips.length;if($('top-library-count'))$('top-library-count').textContent=clips.length;const host=$('library');host.replaceChildren();if(!clips.length){const empty=document.createElement('div');empty.className='empty-library';empty.textContent='Your first clip belongs here. Export a moment to start your library.';host.append(empty);}for(const clip of clips){const card=document.createElement('article');card.className='clip-card';const video=document.createElement('video');video.src='/media/'+clip.id;video.controls=true;lazyVideo(video);const title=document.createElement('h3');title.textContent=clip.title;const meta=document.createElement('p');meta.textContent=(clip.meta.duration||0).toFixed(1)+' seconds · '+(clip.settings.layout||'stack')+' · '+new Date(clip.created*1000).toLocaleDateString();const links=document.createElement('div');links.className='card-links';const download=document.createElement('a');download.href='/media/'+clip.id;download.download='neonclipping-'+clip.id+'.mp4';download.textContent='↓ MP4';links.append(download);if(clip.srt){const sub=document.createElement('a');sub.href='/text/'+clip.id+'/srt';sub.textContent='↓ SRT';links.append(sub);}const edit=document.createElement('button');edit.textContent='Edit clip';edit.onclick=()=>openSavedClip(clip.id).catch(e=>status(e.message));links.append(edit);card.append(video,title,meta,links);appendVisualReview(card,clip.settings.visual_review);host.append(card);}}
async function refreshSources(){
 const res=await request('/sources');
 if($('sources-count'))$('sources-count').textContent=res.count;
 if($('top-sources-count'))$('top-sources-count').textContent=res.count;
 if($('sources-total-count'))$('sources-total-count').textContent=res.count;
 if($('sources-total-size'))$('sources-total-size').textContent=res.total_size_formatted;
 if($('sources-folder-path'))$('sources-folder-path').textContent=res.dir;
 const host=$('sources-grid');
 if(!host)return;
 host.replaceChildren();
 if(!res.sources?.length){
  const empty=document.createElement('div');
  empty.className='empty-library';
  empty.textContent='No source videos found on disk yet. Full downloaded videos and uploaded source files will appear here.';
  host.append(empty);
  return;
 }
 for(const s of res.sources){
  const card=document.createElement('article');card.className='source-card';
  const video=document.createElement('video');video.src='/media/'+s.id;video.controls=true;lazyVideo(video);
  const title=document.createElement('h3');title.textContent=s.title;
  const meta=document.createElement('div');meta.className='source-meta-row';
  meta.innerHTML=`<span>⏱ ${s.dur_formatted}</span><span>📐 ${s.w}×${s.h}</span><span class="source-size-badge">💾 ${s.size_formatted}</span>`+(s.clips_count?`<span>✂ ${s.clips_count} clip${s.clips_count>1?'s':''} made</span>`:'');
  const pathBox=document.createElement('div');pathBox.className='source-filepath';
  pathBox.innerHTML=`<span class="lbl">Location:</span> <code>${s.path}</code>`;
  if(s.url){
   const urlBox=document.createElement('div');urlBox.className='source-url-row';
   urlBox.innerHTML=`<span class="lbl">Source URL:</span> <a href="${s.url}" target="_blank" rel="noopener">${s.url}</a>`;
   card.append(video,title,meta,pathBox,urlBox);
  }else{
   card.append(video,title,meta,pathBox);
  }
  const actions=document.createElement('div');actions.className='source-actions';
  const makeBtn=document.createElement('button');makeBtn.className='primary source-btn';makeBtn.textContent='⚡ Create clips from this';
  makeBtn.onclick=()=>{
   document.querySelector('[data-view=editor]').click();
   setSource({id:s.id,w:s.w,h:s.h,duration:s.duration},0);
   setRange(0,Math.min(30,s.duration));
   status('Source "'+s.title+'" loaded from disk! Choose your areas or find highlights.');
  };
  const dl=document.createElement('a');dl.className='source-btn dl-btn';dl.href='/media/'+s.id;dl.download=s.filename;dl.textContent='↓ Download MP4';
  const delBtn=document.createElement('button');delBtn.className='source-btn del-btn';delBtn.textContent='🗑 Delete';
  delBtn.onclick=async()=>{
   if(!confirm(`Delete this source video from disk?\n\nFile: ${s.filename}\nSize: ${s.size_formatted}\n\nThis will free up disk space on this PC.`))return;
   delBtn.disabled=true;
   try{
    const r=await fetch('/sources/'+s.id,{method:'DELETE'});
    if(!r.ok)throw Error('Delete failed');
    status('Deleted source video '+s.filename+' ('+s.size_formatted+' freed).');
    await refreshSources();
   }catch(e){
    status('Could not delete: '+e.message);delBtn.disabled=false;
   }
  };
  actions.append(makeBtn,dl,delBtn);
  card.append(actions);
  host.append(card);
 }
}
let currentYtClip=null,ytPollTimer=null;
async function checkYouTubeStatus(){
 try{
  const res=await request('/youtube/status');
  const titleEl=$('yt-status-title'),subEl=$('yt-status-sub'),btn=$('yt-connect-btn');
  if(!titleEl||!btn)return;
  if(res.is_connected){
   const ch=res.channel||{};
   const title=ch.title||'Connected YouTube Channel';
   titleEl.innerHTML=`Connected: <span style="color:#58e8f7">${title}</span>`;
   subEl.textContent=`Subs: ${ch.subscribers||'0'} · Videos: ${ch.video_count||'0'} · Ready to upload Shorts`;
   btn.textContent='✅ Connected';
   btn.classList.add('connected');
   btn.onclick=null;
   if($('home-yt-title'))$('home-yt-title').textContent='Connected: '+title+' '+(ch.custom_url||'');
   if($('home-yt-sub'))$('home-yt-sub').textContent=`${ch.subscribers||'0'} Subs · ${ch.video_count||'0'} Videos · Ready to upload`;
   if($('top-yt-label'))$('top-yt-label').textContent='YouTube: '+title;
   if($('top-yt-pill')){$('top-yt-pill').style.color='#85e89d';$('top-yt-pill').style.borderColor='#236136';}
  }else{
   titleEl.textContent='YouTube Shorts Integration';
   subEl.textContent='Connect your channel with 1-click to upload Shorts directly from here.';
   btn.textContent='🔗 Connect YouTube Channel';
   btn.classList.remove('connected');
   if($('home-yt-title'))$('home-yt-title').textContent='Connect YouTube Channel';
   if($('home-yt-sub'))$('home-yt-sub').textContent='1-click direct upload to Shorts';
   if($('top-yt-label'))$('top-yt-label').textContent='Connect YouTube';
   btn.onclick=async()=>{
    btn.disabled=true;
    btn.textContent='Opening Google sign-in...';
    try{
     const authRes=await request('/youtube/auth-url');
     if(authRes.auth_url){
      window.location.href=authRes.auth_url;
     }
    }catch(err){
     status('YouTube connect failed: '+err.message);
     btn.disabled=false;
     btn.textContent='🔗 Connect YouTube Channel';
    }
   };
  }
 }catch(e){}
}

let currentYtPublishMode = 'now';
let currentIgPublishMode = 'now';

function formatLocalDateTime(d) {
 const pad = n => String(n).padStart(2, '0');
 return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

window.applySchedulePreset = function(platform, preset) {
 const now = new Date();
 let target = new Date(now.getTime() + 2 * 3600 * 1000);
 if (preset === 2) {
  target = new Date(now.getTime() + 2 * 3600 * 1000);
 } else if (preset === 'tonight') {
  target = new Date();
  target.setHours(20, 0, 0, 0);
  if (target.getTime() <= now.getTime()) {
   target.setDate(target.getDate() + 1);
  }
 } else if (preset === 'tomorrow_morning') {
  target = new Date();
  target.setDate(target.getDate() + 1);
  target.setHours(10, 0, 0, 0);
 } else if (preset === 'tomorrow_evening') {
  target = new Date();
  target.setDate(target.getDate() + 1);
  target.setHours(18, 0, 0, 0);
 }
 const input = $(platform + '-schedule-datetime');
 if (input) input.value = formatLocalDateTime(target);
};

window.setYtPublishMode = function(mode) {
 currentYtPublishMode = mode;
 const nowBtn = $('yt-mode-now-btn');
 const schedBtn = $('yt-mode-schedule-btn');
 const container = $('yt-schedule-container');
 const privBox = $('yt-privacy-control-box');
 const submitBtn = $('yt-submit-upload-btn');

 if (mode === 'schedule') {
  if (nowBtn) nowBtn.classList.remove('active');
  if (schedBtn) schedBtn.classList.add('active');
  if (container) container.hidden = false;
  if (privBox) privBox.hidden = true;
  if (submitBtn) submitBtn.textContent = '📅 Schedule on YouTube Cloud';
  const dtInput = $('yt-schedule-datetime');
  if (dtInput && !dtInput.value) {
   applySchedulePreset('yt', 2);
  }
 } else {
  if (nowBtn) nowBtn.classList.add('active');
  if (schedBtn) schedBtn.classList.remove('active');
  if (container) container.hidden = true;
  if (privBox) privBox.hidden = false;
  if (submitBtn) submitBtn.textContent = '🚀 Upload to Shorts Now';
 }
};

window.setIgPublishMode = function(mode) {
 currentIgPublishMode = mode;
 const nowBtn = $('ig-mode-now-btn');
 const schedBtn = $('ig-mode-schedule-btn');
 const container = $('ig-schedule-container');
 const submitBtn = $('ig-submit-upload-btn');

 if (mode === 'schedule') {
  if (nowBtn) nowBtn.classList.remove('active');
  if (schedBtn) schedBtn.classList.add('active');
  if (container) container.hidden = false;
  if (submitBtn) submitBtn.textContent = '📅 Pre-Stage & Schedule Reel';
  const dtInput = $('ig-schedule-datetime');
  if (dtInput && !dtInput.value) {
   applySchedulePreset('ig', 2);
  }
 } else {
  if (nowBtn) nowBtn.classList.add('active');
  if (schedBtn) schedBtn.classList.remove('active');
  if (container) container.hidden = true;
  if (submitBtn) submitBtn.textContent = '🟣 Publish Reel Now';
 }
};

window.toggleScheduledQueue = function(platform) {
 const list = $(platform + '-scheduled-list');
 const chevron = $(platform + '-queue-chevron');
 if (!list) return;
 const isHidden = list.hidden;
 list.hidden = !isHidden;
 if (chevron) {
  if (isHidden) chevron.classList.add('open');
  else chevron.classList.remove('open');
 }
};

window.cancelScheduledPost = async function(id) {
 if (!confirm('Cancel this scheduled post?')) return;
 try {
  const res = await request('/publishing/cancel-scheduled', { id });
  if (res.ok) {
   status('Scheduled post cancelled.');
   await refreshSaved();
  }
 } catch (err) {
  alert('Failed to cancel: ' + err.message);
 }
};

function renderScheduledQueue(platform, items) {
 const badge = $(platform + '-scheduled-badge');
 if (badge) badge.textContent = items.length;
 const list = $(platform + '-scheduled-list');
 if (!list) return;
 list.replaceChildren();

 if (!items.length) {
  const empty = document.createElement('div');
  empty.className = 'empty-queue-msg';
  empty.textContent = platform === 'youtube' ? 'No scheduled YouTube Shorts.' : 'No scheduled Instagram Reels.';
  list.append(empty);
  return;
 }

 for (const item of items) {
  const card = document.createElement('div'); card.className = 'scheduled-item-card';
  const left = document.createElement('div'); left.className = 'scheduled-item-left';
  let timeStr = item.scheduled_time_str || '';
  if (item.scheduled_at) {
   const d = new Date(item.scheduled_at * 1000);
   timeStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } else if (timeStr && (timeStr.includes('T') || timeStr.endsWith('Z'))) {
   const d = new Date(timeStr);
   timeStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }
  const isErr = item.status === 'error';
  const statusHtml = isErr 
    ? `<span class="scheduled-item-time" style="color:#ff4ba5;font-weight:600">⚠️ Meta Blocked: ${item.error ? (item.error.includes('OAuthException') ? 'API access blocked (Need fresh token)' : item.error.slice(0, 45)) : 'Publish failed'}</span>` 
    : `<span class="scheduled-item-time">⏰ Release: ${timeStr}</span>`;
  left.innerHTML = `<span class="scheduled-item-title" title="${item.title}">${item.title}</span>${statusHtml}`;
  const right = document.createElement('div'); right.className = 'scheduled-item-right';
  const cancelBtn = document.createElement('button'); cancelBtn.className = 'scheduled-cancel-btn'; cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => cancelScheduledPost(item.id);
  right.append(cancelBtn);
  card.append(left, right);
  list.append(card);
 }
}

let currentBatchClips = [];
let batchPollTimer = null;

window.openYtBatchModal = async function() {
 const dialog = $('yt-batch-dialog');
 if (!dialog) return;
 $('yt-batch-progress-box').hidden = true;
 $('yt-batch-success-box').hidden = true;
 const btn = $('yt-batch-submit-btn');
 if (btn) {
  btn.disabled = false;
  btn.textContent = '🚀 Launch Cloud Autopilot';
  btn.onclick = launchBatchSchedule;
 }
 const gap = $('yt-batch-gap-select')?.value || 2;
 await loadBatchPreview(gap);
 dialog.showModal();
};

window.loadBatchPreview = async function(gapHours = 2) {
 const list = $('yt-batch-clips-list');
 const submitBtn = $('yt-batch-submit-btn');
 if (!list) return;
 list.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:10px">Loading top unposted clips...</div>';

 try {
  const res = await request(`/youtube/auto-schedule-preview?gap_hours=${gapHours}`);
  list.replaceChildren();
  currentBatchClips = res.clips || [];

  if (!res.can_schedule || !res.clips?.length) {
   const empty = document.createElement('div');
   empty.className = 'empty-queue-msg';
   if (res.remaining_quota === 0) {
    empty.textContent = 'Daily upload quota is full (0/6 remaining today). Quota resets at 1:30 PM IST.';
   } else {
    empty.textContent = 'All clips in your Saved folder have already been uploaded or scheduled!';
   }
   list.append(empty);
   if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'No Clips to Schedule';
   }
   return;
  }

  for (const c of res.clips) {
   const row = document.createElement('div'); row.className = 'batch-clip-row';
   const info = document.createElement('div'); info.className = 'batch-clip-info';
   const order = document.createElement('span'); order.className = 'batch-clip-order'; order.textContent = c.order;
   const meta = document.createElement('div'); meta.className = 'batch-clip-meta';
   meta.innerHTML = `<span class="batch-clip-title" title="${c.title}">${c.title}</span><span class="batch-clip-size">${c.filename} · ${c.size_mb} MB</span>`;
   info.append(order, meta);

   const time = document.createElement('span'); time.className = 'batch-clip-time';
   time.textContent = `⏰ ${c.publish_at_local}`;
   row.append(info, time);
   list.append(row);
  }

  if (submitBtn) {
   submitBtn.disabled = false;
   submitBtn.textContent = `🚀 Launch Cloud Autopilot (${res.clips.length} Shorts)`;
  }
 } catch (err) {
  list.innerHTML = `<div style="color:#ff4ba5;font-size:12px;padding:8px">Error loading preview: ${err.message}</div>`;
 }
};

window.launchBatchSchedule = async function() {
 if (!currentBatchClips.length) return;
 const b = $('yt-batch-submit-btn');
 if (b) b.disabled = true;
 $('yt-batch-progress-box').hidden = false;
 $('yt-batch-progress-bar').style.width = '5%';
 $('yt-batch-status-text').textContent = 'Starting batch upload to YouTube Cloud...';
 $('yt-batch-success-box').hidden = true;

 try {
  const res = await request('/youtube/auto-schedule-batch', { clips: currentBatchClips });
  const batchId = res.batch_id;
  if (batchPollTimer) clearInterval(batchPollTimer);

  batchPollTimer = setInterval(async () => {
   try {
    const st = await request('/youtube/batch-status/' + batchId);
    $('yt-batch-progress-bar').style.width = Math.max(5, st.progress || 0) + '%';
    $('yt-batch-status-text').textContent = st.message || st.status;

    if (st.status === 'done') {
     clearInterval(batchPollTimer);
     $('yt-batch-progress-bar').style.width = '100%';
     $('yt-batch-status-text').innerHTML = '🎉 <span style="color:#58e8f7">All Shorts Uploaded &amp; Scheduled!</span>';
     const succBox = $('yt-batch-success-box');
     succBox.hidden = false;
     succBox.innerHTML = `
      <div style="line-height:1.6">
        <span style="color:#58e8f7;font-weight:700">☁ 100% YouTube Cloud Autopilot Active!</span><br>
        <span style="font-size:11.5px;color:#eee">All ${st.completed_clips.length} Shorts are safely stored on Google servers and scheduled at 2-hour intervals.<br>
        <b>You can now turn off your PC completely</b> — YouTube will release them automatically on time!</span>
      </div>
     `;
     if (b) {
      b.disabled = false;
      b.textContent = '✔ Batch Complete (Close)';
      b.onclick = () => $('yt-batch-dialog').close();
     }
     status(`Batch of ${st.completed_clips.length} Shorts scheduled on YouTube Cloud!`);
     await refreshSaved();
    } else if (st.status === 'error') {
     clearInterval(batchPollTimer);
     $('yt-batch-status-text').innerHTML = `<span style="color:#ff4ba5">Failed: ${st.error}</span>`;
     if (b) b.disabled = false;
    }
   } catch (err) {
    clearInterval(batchPollTimer);
    if (b) b.disabled = false;
   }
  }, 1000);
 } catch (err) {
  $('yt-batch-status-text').textContent = 'Error: ' + err.message;
  if (b) b.disabled = false;
 }
};

let currentIgBatchClips = [];
let igBatchPollTimer = null;

window.openIgBatchModal = async function() {
 const dialog = $('ig-batch-dialog');
 if (!dialog) return;
 $('ig-batch-progress-box').hidden = true;
 $('ig-batch-success-box').hidden = true;
 const btn = $('ig-batch-submit-btn');
 if (btn) {
  btn.disabled = false;
  btn.textContent = '🚀 Launch Meta Autopilot';
  btn.onclick = launchIgBatchSchedule;
 }
 const gap = $('ig-batch-gap-select')?.value || 2;
 await loadIgBatchPreview(gap);
 dialog.showModal();
};

window.loadIgBatchPreview = async function(gapHours = 2) {
 const list = $('ig-batch-clips-list');
 const submitBtn = $('ig-batch-submit-btn');
 if (!list) return;
 list.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:10px">Loading top unposted clips...</div>';

 try {
  const res = await request(`/instagram/auto-schedule-preview?gap_hours=${gapHours}&count=10`);
  list.replaceChildren();
  currentIgBatchClips = res.clips || [];

  if (!res.can_schedule || !res.clips?.length) {
   const empty = document.createElement('div');
   empty.className = 'empty-queue-msg';
   if (res.remaining_quota === 0) {
    empty.textContent = '24-hour velocity limit is reached (0/50 remaining). Please wait a few hours.';
   } else {
    empty.textContent = 'All clips in your Saved folder have already been uploaded or scheduled!';
   }
   list.append(empty);
   if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'No Clips to Schedule';
   }
   return;
  }

  for (const c of res.clips) {
   const row = document.createElement('div'); row.className = 'batch-clip-row';
   const info = document.createElement('div'); info.className = 'batch-clip-info';
   const order = document.createElement('span'); order.className = 'batch-clip-order ig'; order.textContent = c.order;
   const meta = document.createElement('div'); meta.className = 'batch-clip-meta';
   meta.innerHTML = `<span class="batch-clip-title" title="${c.title}">${c.title}</span><span class="batch-clip-size">${c.filename} · ${c.size_mb} MB</span>`;
   info.append(order, meta);

   const time = document.createElement('span'); time.className = 'batch-clip-time ig';
   time.textContent = `⏰ ${c.scheduled_time_str}`;
   row.append(info, time);
   list.append(row);
  }

  if (submitBtn) {
   submitBtn.disabled = false;
   submitBtn.textContent = `🚀 Launch Meta Autopilot (${res.clips.length} Reels)`;
  }
 } catch (err) {
  list.innerHTML = `<div style="color:#ff4ba5;font-size:12px;padding:8px">Error loading preview: ${err.message}</div>`;
 }
};

window.launchIgBatchSchedule = async function() {
 if (!currentIgBatchClips.length) return;
 const b = $('ig-batch-submit-btn');
 if (b) b.disabled = true;
 $('ig-batch-progress-box').hidden = false;
 $('ig-batch-progress-bar').style.width = '5%';
 $('ig-batch-status-text').textContent = 'Starting batch pre-staging to Meta Cloud...';
 $('ig-batch-success-box').hidden = true;

 try {
  const res = await request('/instagram/auto-schedule-batch', { clips: currentIgBatchClips });
  const batchId = res.batch_id;
  if (igBatchPollTimer) clearInterval(igBatchPollTimer);

  igBatchPollTimer = setInterval(async () => {
   try {
    const st = await request('/instagram/batch-status/' + batchId);
    $('ig-batch-progress-bar').style.width = Math.max(5, st.progress || 0) + '%';
    $('ig-batch-status-text').textContent = st.message || st.status;

    if (st.status === 'done') {
     clearInterval(igBatchPollTimer);
     $('ig-batch-progress-bar').style.width = '100%';
     $('ig-batch-status-text').innerHTML = '🎉 <span style="color:#ff86be">All Reels Pre-Staged on Meta Cloud!</span>';
     const succBox = $('ig-batch-success-box');
     succBox.hidden = false;
     succBox.innerHTML = `
      <div style="line-height:1.6">
        <span style="color:#ff86be;font-weight:700">☁ 100% Meta Cloud Autopilot Active!</span><br>
        <span style="font-size:11.5px;color:#eee">All ${st.completed_clips.length} Reels are safely cached on Meta servers and scheduled at 2-hour intervals.<br>
        <b>You can now turn off your PC completely</b> — Meta &amp; GitHub Actions will release them automatically on time!</span>
      </div>
     `;
     if (b) {
      b.disabled = false;
      b.textContent = '✔ Batch Complete (Close)';
      b.onclick = () => $('ig-batch-dialog').close();
     }
     status(`Batch of ${st.completed_clips.length} Reels pre-staged on Meta Cloud!`);
     await refreshSaved();
    } else if (st.status === 'error') {
     clearInterval(igBatchPollTimer);
     $('ig-batch-status-text').innerHTML = `<span style="color:#ff4ba5">Failed: ${st.error}</span>`;
     if (b) b.disabled = false;
    }
   } catch (err) {
    clearInterval(igBatchPollTimer);
    if (b) b.disabled = false;
   }
  }, 2000);
 } catch (err) {
  $('ig-batch-status-text').textContent = 'Error: ' + err.message;
  if (b) b.disabled = false;
 }
};

function openYtUploadModal(c){
 currentYtClip=c;
 $('yt-modal-filename').textContent=c.filename;
 $('yt-title-input').value=(c.title?`${c.title} #shorts`:`${c.stem} #shorts`).slice(0,100);
 $('yt-desc-input').value=`${c.title||c.stem}\n\nCreated with NeonClipping.\n#shorts #gaming #gta6 #viral`;
 $('yt-privacy-select').value='public';
 $('yt-upload-progress-box').hidden=true;
 $('yt-upload-success-link').hidden=true;
 $('yt-submit-upload-btn').disabled=false;
 setYtPublishMode('now');
 $('yt-dialog').showModal();
}

$('yt-submit-upload-btn').onclick=async()=>{
 if(!currentYtClip)return;
 const b=$('yt-submit-upload-btn');
 b.disabled=true;
 $('yt-upload-progress-box').hidden=false;
 $('yt-upload-progress-bar').style.width='5%';
 $('yt-upload-status-text').textContent='Starting YouTube upload...';
 $('yt-upload-success-link').hidden=true;

 try{
  const isSchedule = currentYtPublishMode === 'schedule';
  let publishAtIso = null;
  if (isSchedule) {
   const dtVal = $('yt-schedule-datetime')?.value;
   if (!dtVal) {
    throw Error('Please select a valid date and time to schedule this Short.');
   }
   const targetTime = new Date(dtVal);
   if (targetTime.getTime() <= Date.now() + 60000) {
    throw Error('Scheduled time must be at least 2 minutes into the future.');
   }
   publishAtIso = targetTime.toISOString();
  }

  const payload = {
   filename: currentYtClip.filename,
   title: $('yt-title-input').value.trim(),
   description: $('yt-desc-input').value.trim(),
   privacy: $('yt-privacy-select').value
  };
  if (publishAtIso) {
   payload.publish_at = publishAtIso;
  }

  const res = await request('/youtube/upload', payload);
  if(!res.ok)throw Error('Upload request failed');
  const taskId=res.task_id;
  if(ytPollTimer)clearInterval(ytPollTimer);
  ytPollTimer=setInterval(async()=>{
   try{
    const st=await request('/youtube/upload-status/'+taskId);
    $('yt-upload-progress-bar').style.width=Math.max(5,st.progress||0)+'%';
    $('yt-upload-status-text').textContent=st.message||st.status;
    if(st.status==='done'){
     clearInterval(ytPollTimer);
     $('yt-upload-progress-bar').style.width='100%';
     const linkBox=$('yt-upload-success-link');
     linkBox.hidden=false;

     if (st.scheduled) {
      $('yt-upload-status-text').innerHTML='🎉 <span style="color:#58e8f7">Scheduled on YouTube Cloud!</span>';
      linkBox.innerHTML=`<div style="line-height:1.6"><span style="color:#58e8f7;font-weight:700">☁ YouTube Cloud Schedule Verified!</span><br><span style="font-size:11px;color:#eee">Google servers will automatically publish this Short publicly at <b>${new Date(st.publish_at).toLocaleString()}</b>.<br>You can safely shut down your PC anytime.</span><br><a href="${st.shorts_url}" target="_blank" rel="noopener" style="display:inline-block;margin-top:6px">🎬 View Private Video on YouTube: ${st.shorts_url} ↗</a></div>`;
      b.disabled=false;
      b.textContent='Schedule Another';
      status('Short scheduled on YouTube Cloud for '+new Date(st.publish_at).toLocaleString());
     } else {
      $('yt-upload-status-text').innerHTML='🎉 <span style="color:#58e8f7">Upload Complete!</span>';
      linkBox.innerHTML=`<a href="${st.shorts_url}" target="_blank" rel="noopener">🎬 Open Short on YouTube: ${st.shorts_url} ↗</a>`;
      b.disabled=false;
      b.textContent='Upload Again';
      status('Short uploaded to YouTube: '+st.shorts_url);
     }
     await refreshSaved();
    }else if(st.status==='error'){
     clearInterval(ytPollTimer);
     $('yt-upload-status-text').innerHTML=`<span style="color:#ff4ba5">Failed: ${st.error}</span>`;
     b.disabled=false;
    }
   }catch(err){
    clearInterval(ytPollTimer);
    b.disabled=false;
   }
  },1000);
 }catch(e){
  $('yt-upload-status-text').textContent='Error: '+e.message;
  b.disabled=false;
 }
};

let currentIgClip=null,igPollTimer=null;

async function checkInstagramStatus(){
 try{
  const res=await request('/instagram/status');
  const labelEl=$('top-ig-label');
  const statusEl=$('ig-connect-status-text');
  const disconnectBtn=$('ig-disconnect-btn');
  const igTitleEl=$('ig-status-title'), igSubEl=$('ig-status-sub'), igBtnEl=$('ig-connect-btn');

  if(res.is_connected){
   const user=res.username||'techmedia09';
   if(labelEl)labelEl.textContent=`Instagram: @${user}`;
   const permBadge = res.is_permanent ? ' <span style="background:rgba(74,222,128,0.2);color:#4ade80;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:6px;font-weight:700">🔒 Permanent</span>' : '';
   if(statusEl)statusEl.innerHTML=`Connected: <span style="color:#ff86be">@${user}</span> (ID: ${res.account_id})${permBadge}`;
   if(disconnectBtn)disconnectBtn.style.display='inline-block';

   if(igTitleEl)igTitleEl.innerHTML=`Connected: <span style="color:#ff86be">@${user}</span>${permBadge}`;
   if(igSubEl)igSubEl.textContent=`Page: ${res.page_name||'Tech Media'} · Permanent Never-Expiring Token · 100% Ready`;
   if(igBtnEl){
    igBtnEl.textContent='✅ Connected';
    igBtnEl.classList.add('connected');
    igBtnEl.onclick=()=>openIgConnectModal();
   }
  }else{
   if(labelEl)labelEl.textContent='Instagram: Connect';
   if(statusEl)statusEl.innerHTML=`<span style="color:var(--muted)">Not connected. Paste your Meta User Access Token below.</span>`;
   if(disconnectBtn)disconnectBtn.style.display='none';

   if(igTitleEl)igTitleEl.textContent='Instagram Reels Integration';
   if(igSubEl)igSubEl.textContent='Connect your Instagram Creator account to publish Reels directly with 1-click.';
   if(igBtnEl){
    igBtnEl.textContent='🔗 Connect Instagram';
    igBtnEl.classList.remove('connected');
    igBtnEl.onclick=()=>openIgConnectModal();
   }
  }
 }catch(e){}
}

function openIgConnectModal(){
 $('ig-connect-result-msg').hidden=true;
 checkInstagramStatus();
 $('ig-connect-dialog').showModal();
}

if($('top-ig-pill'))$('top-ig-pill').onclick=()=>openIgConnectModal();

if($('ig-connect-submit-btn')){
 $('ig-connect-submit-btn').onclick=async()=>{
  const token=$('ig-token-input').value.trim();
  const appId=$('ig-appid-input')?$('ig-appid-input').value.trim():'';
  const secret=$('ig-secret-input')?$('ig-secret-input').value.trim():'';
  const msgEl=$('ig-connect-result-msg');
  const btn=$('ig-connect-submit-btn');
  if(!token){
   alert('Please paste your Meta User Access Token.');
   return;
  }
  btn.disabled=true;
  btn.textContent='Connecting...';
  msgEl.hidden=false;
  msgEl.style.color='var(--cyan)';
  msgEl.textContent='Verifying with Meta Graph API...';
  try{
   const r=await request('/instagram/connect',{
    access_token:token,
    app_id:appId,
    app_secret:secret
   });
   msgEl.style.color='#85e89d';
   msgEl.textContent=`🎉 Connected to @${r.username}! Account ID: ${r.account_id}`;
   btn.disabled=false;
   btn.textContent='🔗 Update Token';
   $('ig-token-input').value='';
   await checkInstagramStatus();
   setTimeout(()=>$('ig-connect-dialog').close(),1500);
  }catch(e){
   msgEl.style.color='#ff4ba5';
   msgEl.textContent='Error: '+e.message;
   btn.disabled=false;
   btn.textContent='🔗 Connect Account';
  }
 };
}

if($('ig-disconnect-btn')){
 $('ig-disconnect-btn').onclick=async()=>{
  if(!confirm('Disconnect Instagram account?'))return;
  try{
   await request('/instagram/disconnect',{});
   await checkInstagramStatus();
   $('ig-connect-result-msg').hidden=false;
   $('ig-connect-result-msg').style.color='var(--muted)';
   $('ig-connect-result-msg').textContent='Instagram disconnected.';
  }catch(e){alert(e.message);}
 };
}

function openIgUploadModal(c){
 currentIgClip=c;
 $('ig-modal-filename').textContent=c.filename;
 const rawTitle = (c.title || c.stem || '').replace(/ - [a-f0-9]+$/i, '');
 $('ig-caption-input').value=`${rawTitle}\n\n🔥 Created with NeonClipping Studio\n#gamingmemes #funnyreels #gamerlife #reelsindia`;
 $('ig-share-feed-checkbox').checked=true;
 $('ig-upload-progress-box').hidden=true;
 $('ig-upload-success-link').hidden=true;
 $('ig-submit-upload-btn').disabled=false;
 setIgPublishMode('now');
 $('ig-upload-dialog').showModal();
}

if($('ig-submit-upload-btn')){
 $('ig-submit-upload-btn').onclick=async()=>{
  if(!currentIgClip)return;
  const b=$('ig-submit-upload-btn');
  b.disabled=true;
  $('ig-upload-progress-box').hidden=false;
  $('ig-upload-progress-bar').style.width='10%';
  $('ig-upload-status-text').textContent='Starting secure Cloudflare tunnel...';
  $('ig-upload-success-link').hidden=true;

  try{
   const isSchedule = currentIgPublishMode === 'schedule';
   let scheduledAtSec = null;
   let scheduledTimeStr = null;
   if (isSchedule) {
    const dtVal = $('ig-schedule-datetime')?.value;
    if (!dtVal) {
     throw Error('Please select a valid date and time to schedule this Reel.');
    }
    const targetTime = new Date(dtVal);
    if (targetTime.getTime() <= Date.now() + 60000) {
     throw Error('Scheduled time must be at least 2 minutes into the future.');
    }
    scheduledAtSec = Math.floor(targetTime.getTime() / 1000);
    scheduledTimeStr = targetTime.toLocaleString();
   }

   const payload = {
    filename: currentIgClip.filename,
    caption: $('ig-caption-input').value.trim(),
    share_to_feed: $('ig-share-feed-checkbox').checked
   };
   if (scheduledAtSec) {
    payload.scheduled_at = scheduledAtSec;
    payload.scheduled_time_str = scheduledTimeStr;
   }

   const res=await request('/instagram/publish', payload);
   const taskId=res.task_id;
   if(igPollTimer)clearInterval(igPollTimer);
   igPollTimer=setInterval(async()=>{
    try{
     const st=await request('/instagram/task/'+taskId);
     $('ig-upload-progress-bar').style.width=Math.max(10,st.progress||0)+'%';
     $('ig-upload-status-text').textContent=st.message||st.status;
     if(st.status==='done'){
      clearInterval(igPollTimer);
      $('ig-upload-progress-bar').style.width='100%';
      const linkBox=$('ig-upload-success-link');
      linkBox.hidden=false;

      if (st.scheduled) {
       $('ig-upload-status-text').innerHTML='🎉 <span style="color:#ff86be">Pre-Staged on Meta Cloud!</span>';
       linkBox.innerHTML=`<div style="line-height:1.6"><span style="color:#ff86be;font-weight:700">☁ Meta Cloud Pre-Staging Complete!</span><br><span style="font-size:11px;color:#eee">Video is safely cached on Meta servers. NeonClipping will automatically publish it at <b>${st.scheduled_time_str}</b> with zero video re-upload.</span></div>`;
       b.disabled=false;
       b.textContent='Schedule Another';
       status('Reel scheduled on Meta Cloud for '+st.scheduled_time_str);
      } else {
       $('ig-upload-status-text').innerHTML='🎉 <span style="color:#ff86be">Reel Published to Instagram!</span>';
       linkBox.innerHTML=`<a href="${st.permalink}" target="_blank" rel="noopener">🟣 Open Reel on Instagram: ${st.permalink} ↗</a>`;
       b.disabled=false;
       b.textContent='Publish Again';
       status('Reel published to Instagram: '+st.permalink);
      }
      await refreshSaved();
     }else if(st.status==='error'){
      clearInterval(igPollTimer);
      $('ig-upload-status-text').innerHTML=`<span style="color:#ff4ba5">Failed: ${st.error}</span>`;
      b.disabled=false;
     }
    }catch(err){
     clearInterval(igPollTimer);
     b.disabled=false;
    }
   },2000);
  }catch(e){
   $('ig-upload-status-text').textContent='Error: '+e.message;
   b.disabled=false;
  }
 };
}

async function refreshSaved(){
 const res=await request('/saved');
 if($('saved-count'))$('saved-count').textContent=res.count;
 if($('top-saved-count'))$('top-saved-count').textContent=res.count;
 if($('saved-total-count'))$('saved-total-count').textContent=res.count;
 if($('saved-total-size'))$('saved-total-size').textContent=res.total_size_formatted;
 if($('home-saved-badge'))$('home-saved-badge').textContent=res.count;
 if($('home-stat-saved-count'))$('home-stat-saved-count').textContent=res.count+' Ready Clips';
 if($('home-stat-saved-size'))$('home-stat-saved-size').textContent=res.total_size_formatted+' in Saved';
 if($('saved-folder-path-display'))$('saved-folder-path-display').textContent=res.dir;
 const host=$('saved-grid');
 await checkYouTubeStatus();
 await checkInstagramStatus();

 try {
  const [quotas, schedData] = await Promise.all([
   request('/publishing/quotas').catch(() => null),
   request('/publishing/scheduled').catch(() => null)
  ]);
  if (quotas) {
   if ($('yt-quota-text')) $('yt-quota-text').textContent = `${quotas.youtube.used} / ${quotas.youtube.limit} Shorts`;
   if ($('yt-quota-reset-text')) $('yt-quota-reset-text').textContent = `Resets in ${quotas.youtube.reset_in_text} (1:30 PM IST)`;
   if ($('yt-quota-bar')) $('yt-quota-bar').style.width = Math.min(100, quotas.youtube.percentage) + '%';

   if ($('ig-quota-text')) $('ig-quota-text').textContent = `${quotas.instagram.used} / ${quotas.instagram.limit} Reels`;
   if ($('ig-quota-bar')) $('ig-quota-bar').style.width = Math.min(100, quotas.instagram.percentage) + '%';
   if ($('ig-quota-reset-text') && quotas.instagram.refill_sub) $('ig-quota-reset-text').textContent = quotas.instagram.refill_sub;
  }
  if (schedData && Array.isArray(schedData.scheduled)) {
   renderScheduledQueue('yt', schedData.scheduled.filter(x => x.platform === 'youtube'));
   renderScheduledQueue('ig', schedData.scheduled.filter(x => x.platform === 'instagram'));
  }

  // Update Auto-Pilot Button
  try {
   const preview = await request('/youtube/auto-schedule-preview').catch(() => null);
   const autoBtn = $('yt-auto-schedule-btn');
   const autoText = $('yt-auto-schedule-text');
   if (autoBtn && autoText && preview) {
    if (preview.can_schedule && preview.count > 0) {
     autoBtn.disabled = false;
     autoText.textContent = `⚡ Auto-Schedule Top ${preview.count} Shorts (2h Gap)`;
    } else if (preview.remaining_quota === 0) {
     autoBtn.disabled = true;
     autoText.textContent = '⏳ Daily Quota Full (6/6 used) · Resets 1:30 PM IST';
    } else {
     autoBtn.disabled = true;
     autoText.textContent = '✔ All Saved Clips Already Scheduled';
    }
   }

   const igPreview = await request('/instagram/auto-schedule-preview').catch(() => null);
   const igAutoBtn = $('ig-auto-schedule-btn');
   const igAutoText = $('ig-auto-schedule-text');
   if (igAutoBtn && igAutoText && igPreview) {
    if (igPreview.can_schedule && igPreview.count > 0) {
     igAutoBtn.disabled = false;
     igAutoText.textContent = `⚡ Auto-Schedule Top ${igPreview.count} Reels (2h Gap)`;
    } else if (igPreview.remaining_quota === 0) {
     igAutoBtn.disabled = true;
     igAutoText.textContent = '⏳ 24h Quota Full (50/50 used)';
    } else {
     igAutoBtn.disabled = true;
     igAutoText.textContent = '✔ All Saved Clips Already Scheduled';
    }
   }
  } catch (e) {}
 } catch (e) {
  console.warn('Quota/Schedule fetch error:', e);
 }
 if(!host)return;
 host.replaceChildren();
 if(!res.clips?.length){
  const empty=document.createElement('div');
  empty.className='empty-library';
  empty.textContent='No saved clips in the Saved folder yet. When clips are exported, an upload-ready copy will appear here.';
  host.append(empty);
  return;
 }
 for(const c of res.clips){
  const card=document.createElement('article');card.className='clip-card saved-clip-card';
  const video=document.createElement('video');video.src='/media-saved/'+encodeURIComponent(c.filename);video.controls=true;lazyVideo(video);
  const title=document.createElement('h3');title.textContent=c.title;title.title=c.title;
  const meta=document.createElement('p');meta.textContent=c.size_formatted+' · '+new Date(c.created*1000).toLocaleDateString();
  const fileBox=document.createElement('div');fileBox.className='source-filepath';fileBox.style.marginBottom='8px';
  fileBox.innerHTML=`<span class="lbl">File:</span> <code title="${c.filename}">${c.filename}</code>`;

  // 1. Primary Publishing Action Row (50/50 clean grid)
  const publishRow=document.createElement('div');publishRow.className='card-publish-row';
  const ytBtn=document.createElement('button');
  ytBtn.className='yt-upload-action-btn';
  ytBtn.innerHTML='🚀 Shorts';
  ytBtn.title='Upload to YouTube Shorts';
  ytBtn.onclick=()=>openYtUploadModal(c);

  const igBtn=document.createElement('button');
  igBtn.className='ig-upload-action-btn';
  igBtn.innerHTML='🟣 Reel';
  igBtn.title='Publish to Instagram Reels';
  igBtn.onclick=()=>openIgUploadModal(c);
  publishRow.append(ytBtn, igBtn);

  // 2. Secondary Utility Row (Download MP4 / SRT on left, Delete on right)
  const toolsRow=document.createElement('div');toolsRow.className='card-tools-row';
  const toolsLeft=document.createElement('div');toolsLeft.className='card-tools-left';
  const download=document.createElement('a');download.href='/media-saved/'+encodeURIComponent(c.filename);download.download=c.filename;download.textContent='↓ MP4';toolsLeft.append(download);
  if(c.has_srt){
   const sub=document.createElement('a');sub.href='/media-saved/'+encodeURIComponent(c.srt_filename);sub.download=c.srt_filename;sub.textContent='↓ SRT';toolsLeft.append(sub);
  }
  const delBtn=document.createElement('button');delBtn.className='source-btn del-btn card-del-btn';delBtn.textContent='🗑 Delete';
  delBtn.onclick=async()=>{
   if(!confirm(`Delete this clip from the Saved folder?\n\nFile: ${c.filename}\nSize: ${c.size_formatted}`))return;
   delBtn.disabled=true;
   try{
    const r=await fetch('/saved/'+encodeURIComponent(c.filename),{method:'DELETE'});
    if(!r.ok)throw Error('Delete failed');
    status('Deleted '+c.filename+' from Saved folder.');
    await refreshSaved();
   }catch(e){
    status('Could not delete: '+e.message);delBtn.disabled=false;
   }
  };
  toolsRow.append(toolsLeft, delBtn);

  card.append(video,title,meta,fileBox,publishRow,toolsRow);
  host.append(card);
 }
}
function switchView(viewName){
 if(viewName==='editor')viewName='video';
 if(viewName==='publish')viewName='saved';

 document.querySelectorAll('[data-view]').forEach(x=>{
  x.classList.toggle('active',x.dataset.view===viewName||(viewName==='video'&&x.dataset.view==='editor'));
 });

 if($('home-view'))$('home-view').hidden=(viewName!=='home');
 if($('video-view'))$('video-view').hidden=(viewName!=='video');
 if($('editor-view'))$('editor-view').hidden=(viewName!=='video');
 if($('stream-view'))$('stream-view').hidden=(viewName!=='stream');
 if($('saved-view'))$('saved-view').hidden=(viewName!=='saved');
 if($('sources-view'))$('sources-view').hidden=(viewName!=='sources');
 if($('library-view'))$('library-view').hidden=(viewName!=='library');

 if(viewName==='library')refreshLibrary().catch(e=>status(e.message));
 if(viewName==='saved')refreshSaved().catch(e=>status(e.message));
 if(viewName==='sources')refreshSources().catch(e=>status(e.message));
 if(viewName==='home'){refreshSaved().catch(e=>{});refreshSources().catch(e=>{});}
}

document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>switchView(b.dataset.view));
document.querySelectorAll('[data-go-view]').forEach(b=>b.onclick=()=>switchView(b.dataset.goView));
document.addEventListener('click',e=>{
 const btn=e.target.closest('[data-go-view]');
 if(btn)switchView(btn.dataset.goView);
});

if($('sidebar-toggle')){
 $('sidebar-toggle').onclick=()=>{
  const sb=document.querySelector('.sidebar');
  const mn=document.querySelector('main');
  const isC=sb.classList.toggle('collapsed');
  mn.classList.toggle('sidebar-collapsed',isC);
  try{localStorage.setItem('neon_sidebar_collapsed',isC?'1':'0');}catch(e){}
 };
 try{
  if(localStorage.getItem('neon_sidebar_collapsed')==='1'){
   document.querySelector('.sidebar')?.classList.add('collapsed');
   document.querySelector('main')?.classList.add('sidebar-collapsed');
  }
 }catch(e){}
}

if($('btn-mode-auto')&&$('btn-mode-manual')){
 $('btn-mode-auto').onclick=()=>{
  $('btn-mode-auto').classList.add('active');
  $('btn-mode-manual').classList.remove('active');
  $('video-auto-container').hidden=false;
  $('video-manual-container').hidden=true;
 };
 $('btn-mode-manual').onclick=()=>{
  $('btn-mode-manual').classList.add('active');
  $('btn-mode-auto').classList.remove('active');
  $('video-auto-container').hidden=true;
  $('video-manual-container').hidden=false;
 };
}

$('refresh-library').onclick=()=>refreshLibrary().catch(e=>status(e.message));if($('clear-library'))$('clear-library').onclick=async()=>{const n=$('library-count')?.textContent||'all';if(!confirm('Clear all '+n+' clips from the data cache folder?\n\nNOTE: Upload-ready copies in your "Saved" folder will remain safe!'))return;$('clear-library').disabled=true;try{const res=await request('/library/clear',{});status('Cleared '+res.count+' clip(s) from data cache ('+res.freed_mb+' MB freed).');await refreshLibrary();}catch(e){status('Could not clear clips: '+e.message);}finally{$('clear-library').disabled=false;}};if($('refresh-saved'))$('refresh-saved').onclick=()=>refreshSaved().catch(e=>status(e.message));
if($('ai-retitle-saved'))$('ai-retitle-saved').onclick=async()=>{
 $('ai-retitle-saved').disabled=true;
 status('🤖 Local AI (Ollama) is generating viral Gen-Z titles for your clips...');
 try{
  const res=await request('/saved/ai-retitle',{});
  if(res.count>0){
   status('✨ Renamed '+res.count+' clip(s) with viral Gen-Z titles!');
  }else{
   status('All clips already have custom or viral titles!');
  }
  await refreshSaved();
 }catch(e){
  status('Could not re-title clips: '+e.message);
 }finally{
  $('ai-retitle-saved').disabled=false;
 }
};if($('clear-saved'))$('clear-saved').onclick=async()=>{const n=$('saved-total-count')?.textContent||'all';const totalSize=$('saved-total-size')?.textContent||'';if(!confirm('Delete ALL '+n+' saved clips from the Saved folder ('+totalSize+')?\n\nThis will permanently delete all rendered upload-ready clips in your "Saved" folder.'))return;$('clear-saved').disabled=true;try{const res=await request('/saved/clear',{});status('Deleted '+res.count+' saved file(s) ('+(res.freed_gb>=1?res.freed_gb+' GB':res.freed_mb+' MB')+' freed).');await refreshSaved();}catch(e){status('Could not clear saved clips: '+e.message);}finally{$('clear-saved').disabled=false;}};if($('refresh-sources'))$('refresh-sources').onclick=()=>refreshSources().catch(e=>status(e.message));if($('clear-sources'))$('clear-sources').onclick=async()=>{const totalSize=$('sources-total-size')?.textContent||'all';if(!confirm('Delete ALL full source videos from disk ('+totalSize+')?\n\nThis will free up substantial disk space on your PC.'))return;$('clear-sources').disabled=true;try{const res=await request('/sources/clear',{});status('Deleted '+res.count+' source video(s) ('+(res.freed_gb||res.freed_mb+' MB')+' freed).');await refreshSources();}catch(e){status('Could not clear sources: '+e.message);}finally{$('clear-sources').disabled=false;}};
request('/highlights').then(showHighlights).catch(e=>status(e.message));refreshLibrary().catch(e=>status(e.message));refreshSaved().catch(e=>status(e.message));refreshSources().catch(e=>status(e.message));switchView('home');
const qParams=new URLSearchParams(window.location.search);
if(qParams.has('code')){
 const code=qParams.get('code');
 const state=qParams.get('state');
 window.history.replaceState({},document.title,window.location.pathname);
 // Switch to Saved tab immediately
 document.querySelector('[data-view=saved]')?.click();
 const titleEl=$('yt-status-title'),subEl=$('yt-status-sub'),btn=$('yt-connect-btn');
 if(titleEl)titleEl.textContent='Connecting to YouTube...';
 if(subEl)subEl.textContent='Verifying credentials with Google. Please wait...';
 if(btn){btn.disabled=true;btn.textContent='Authenticating...';}
 status('Authorizing with YouTube...');
 request('/youtube/exchange-token',{code,state}).then(res=>{
  if(res.ok){
   status('🎉 Connected to YouTube: '+(res.channel?.title||'Channel'));
   refreshSaved().catch(e=>status(e.message));
  }
 }).catch(err=>{
  status('YouTube connection failed: '+err.message);
  if(subEl)subEl.textContent='Connection failed: '+err.message;
  if(btn){btn.disabled=false;btn.textContent='🔗 Retry Connect';}
  alert('YouTube Connection Notice:\n\n' + err.message + '\n\nPlease click "Connect YouTube Channel" to complete sign-in.');
 });
}

$('make').onclick=async()=>{const url=$('url')?.value?.trim()||'';if(!url&&!id){status('Paste a video link or upload a file first.');return;}
 const b=$('make');b.disabled=true;$('make-results').hidden=false;$('make-results').querySelector('.star-ask')?.remove();$('make-grid').replaceChildren();$('make-progress').style.width='2%';
 try{const {job}=await request('/make',{...(url?{url}:{id}),count:Number($('count')?.value||4),mode:$('mode')?.value||'fast',moment_profile:$('moment-profile')?.value||'balanced',accurate:$('accurate')?.value||'fast',min_len:Number($('minlen')?.value||30),max_len:Number($('length')?.value||60),layout:makeLayout(),aspect:$('aspect')?.value||'vertical',resolution:Number($('resolution')?.value||1080),fps:Number($('fps')?.value||0),cam:rect?.cam||{x:.72,y:.40,w:.28,h:.28},game:rect?.game||{x:.25,y:0,w:.50,h:1},top:Number($('top')?.value||32)/100,caption_style:style||'gta',language:($('language')?.value==='auto'?'':$('language')?.value)||''});
  const shown=new Set();
  for(;;){const s=await request('/make/'+job);$('make-status').textContent=(s.stage||s.status)+' · '+Math.round((s.progress||0)*100)+'%';$('make-progress').style.width=Math.max(2,(s.progress||0)*100)+'%';
   (s.clips||[]).forEach(c=>{if(shown.has(c.file))return;shown.add(c.file);const d=document.createElement('div');d.className='mk';d.dataset.file=c.file;
    if(c.error){d.textContent='Failed: '+c.error.slice(0,120);}else{const vid=document.createElement('video');vid.src='/media/'+c.file;vid.controls=true;vid.preload='metadata';const t=document.createElement('b');t.textContent=c.title;const m=document.createElement('small');m.textContent=Math.round(c.start)+'–'+Math.round(c.end)+'s · score '+c.score+(c.hook?' · "'+c.hook+'"':'');const a=document.createElement('a');a.href='/media/'+c.file;a.download='neonclipping-'+c.file+'.mp4';a.textContent='Download MP4';const rs=document.createElement('small');rs.textContent=c.reason||'';const vb=document.createElement('div');vb.className='viral';const ax=c.axes||{};const fmt=v=>v==null?'–':v;vb.title='Viral score is a heuristic: loudness, shouting, what is said, on-screen action and a local AI read. Your star ratings tune it.';vb.innerHTML='<b>🔥 Viral '+(c.viral??'–')+'</b><span>Energy '+fmt(ax.energy)+'</span><span>Action '+fmt(ax.action)+'</span><span>Words '+fmt(ax.words)+'</span><span>AI '+fmt(ax.ai)+'</span>';const stars=document.createElement('div');stars.className='stars';stars.title='Rate this clip: it teaches NeonClipping what a good clip is';
    for(let n=1;n<=5;n++){const sb=document.createElement('button');sb.textContent='★';sb.onclick=async()=>{try{const r=await request('/feedback',{file:c.file,rating:n});[...stars.children].forEach((x,i)=>x.classList.toggle('on',i<n));fb.textContent='Thanks! Learning from '+r.ratings+' rating'+(r.ratings===1?'':'s')+(r.fitted?' · '+(r.insight[0]||''):' (needs 8 to start adapting)');}catch(e){fb.textContent=e.message;}};stars.append(sb);}
    const fb=document.createElement('small');fb.className='fb';fb.textContent='Rate this clip ↑';const edit=document.createElement('button');edit.textContent='Edit clip';edit.onclick=()=>openSavedClip(d.dataset.file).catch(e=>status(e.message));d.append(vid,t,vb,m,rs,a,edit,stars,fb);}
    appendVisualReview(d,c.visual_review);$('make-grid').append(d);});
   if(s.status==='error')throw Error(s.error);if(s.status==='done'){if(!(s.clips||[]).length){$('make-status').textContent=s.stage||'No moments matched. Broaden your filters and try again.';break;}$('make-status').textContent='Done · '+(s.clips||[]).length+' clips in '+s.elapsed_seconds+'s · also in My clips ('+(s.engine||'')+')';if(!$('make-results').querySelector('.star-ask')){const q=document.createElement('div');q.className='star-ask';q.innerHTML='Free and open-source. If these clips saved you time, <a href="https://github.com/iamajmeena/neonclipping" target="_blank" rel="noopener">★ star NeonClipping on GitHub</a> and follow <a href="https://www.instagram.com/iamajmeena/" target="_blank" rel="noopener">@iamajmeena</a> for more tools.';$('make-results').append(q);}break;}
   await sleep(1000);}}catch(e){$('make-status').textContent=e.message;}finally{b.disabled=false;}};

const PRESETS={tl:{cam:{x:0,y:0,w:.24,h:.32},game:{x:.24,y:0,w:.76,h:1}},tr:{cam:{x:.76,y:0,w:.24,h:.32},game:{x:0,y:0,w:.76,h:1}},
 bl:{cam:{x:0,y:.68,w:.24,h:.32},game:{x:.24,y:0,w:.76,h:1}},br:{cam:{x:.76,y:.68,w:.24,h:.32},game:{x:0,y:0,w:.76,h:1}},bc:{cam:{x:.37,y:.68,w:.26,h:.32},game:{x:0,y:0,w:1,h:.68}}};
let preset=null;
function applyPreset(k,save=true){preset=k;document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('on',b.dataset.preset===k));
 if(k==='none'){$('preset-note').textContent='Blurred full frame, no separate facecam.';document.querySelector('[data-layout=blur]')?.click();}
 else{rectEdited=true;rect={cam:{...PRESETS[k].cam},game:{...PRESETS[k].game}};$('preset-note').textContent='Preset set. Fine-tune by drawing in step 03 if your cam sits a bit differently.';document.querySelector('[data-layout=stack]')?.click();}
 if(save)try{localStorage.setItem('neonclipping-preset',k);}catch(e){}}
document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>applyPreset(b.dataset.preset));
try{const k=localStorage.getItem('neonclipping-preset');if(k)applyPreset(k,false);}catch(e){}

// Make clips uses what the user set up: preset or drawn boxes -> that layout; else the layout they clicked; else default stack (Face + gameplay).
function makeLayout(){return layout||'stack';}

async function loadNativeLayouts(){
 const serial=++layoutLoadSerial;const entries=await request('/layouts?top='+Number($('top')?.value||32)/100+'&aspect='+($('aspect')?.value||'vertical')+'&resolution='+($('resolution')?.value||1080));if(serial!==layoutLoadSerial)return;pc.canvas.width=540;pc.canvas.height=Math.round(540*entries[0].height/entries[0].width);
 layoutSpecs=Object.fromEntries(entries.map(x=>[x.id,x]));
 for(const host of [$('layouts'),$('edit-layouts')].filter(Boolean)){host.replaceChildren();
 for(const entry of entries){const button=document.createElement('button');button.className='choice';button.dataset.layout=entry.id;
 const icon=document.createElement('span');icon.className='mini native-layout-mini';icon.style.width=(52*entry.width/entry.height)+'px';icon.setAttribute('aria-hidden','true');
 if(entry.mode==='blur'){const backdrop=document.createElement('i');backdrop.className='layout-backdrop';const inset=document.createElement('i');inset.className='layout-inset';icon.append(backdrop,inset);}
 else for(const layer of entry.layers){const shape=document.createElement('i');shape.className='layout-'+layer.source+(layer.circle?' layout-circle':'');Object.assign(shape.style,{left:(layer.x/entry.width*100)+'%',top:(layer.y/entry.height*100)+'%',width:(layer.w/entry.width*100)+'%',height:(layer.h/entry.height*100)+'%'});icon.append(shape);}
 const name=document.createElement('b');name.textContent=entry.name;const desc=document.createElement('small');desc.textContent=entry.description;
 button.append(icon,name,desc);button.onclick=()=>{layoutClicked=true;setLayout(entry.id);};host.append(button);}}
 setLayout(layout);
}
function drawNativeLayout(source){
 const spec=layoutSpecs[layout];if(!spec)return;const pw=pc.canvas.width,ph=pc.canvas.height,sx=pw/spec.width,sy=ph/spec.height;
 if(spec.mode==='blur'){pc.save();pc.filter='blur(16px)';cover(source,{x:0,y:0,w:1,h:1},-15,-15,pw+30,ph+30);pc.restore();const ratio=Math.min(pw/sourceW,ph/sourceH),w=sourceW*ratio,h=sourceH*ratio;pc.drawImage(source,0,0,sourceW,sourceH,(pw-w)/2,(ph-h)/2,w,h);}
 else{pc.fillStyle='#000';pc.fillRect(0,0,pw,ph);for(const layer of spec.layers){pc.save();if(layer.circle){pc.beginPath();pc.arc((layer.x+layer.w/2)*sx,(layer.y+layer.h/2)*sy,Math.min(layer.w*sx,layer.h*sy)/2,0,Math.PI*2);pc.clip();}cover(source,layer.source==='full'?{x:0,y:0,w:1,h:1}:rect[layer.source],layer.x*sx,layer.y*sy,layer.w*sx,layer.h*sy);pc.restore();}}
 if($('safe-zones')?.checked){pc.save();pc.fillStyle='#ff4ba52b';pc.fillRect(0,0,pw,ph*.05);pc.fillRect(0,ph*.875,pw,ph*.125);pc.fillRect(pw*.87,ph*.26,pw*.13,ph*.615);pc.restore();}
}
if($('top'))$('top').addEventListener('input',()=>loadNativeLayouts().catch(e=>status(e.message)));
loadNativeLayouts().catch(e=>status(e.message));

async function openSavedClip(file){
 const saved=await request('/clips/'+file+'/edit'),s=saved.settings;
 if(saved.file&&saved.file!==file){swapEditedCards([{previous:file,file:saved.file}]);file=saved.file;}
 document.querySelector('[data-view=editor]')?.click();
 setSource(saved.source,Number(s.start));setRange(s.start,s.end);rect={cam:{...s.cam},game:{...s.game}};rectEdited=true;layoutClicked=true;
 if($('aspect'))$('aspect').value=s.aspect||'vertical';if($('resolution'))$('resolution').value=s.resolution||1080;if($('fps'))$('fps').value=s.fps||0;if($('top'))$('top').value=Math.round((s.top||.32)*100);if($('edit-top')&&$('top'))$('edit-top').value=$('top').value;await loadNativeLayouts();setLayout(s.layout||'stack');
 style=s.caption_style||'gta';document.querySelectorAll('[data-style]').forEach(b=>b.classList.toggle('active',b.dataset.style===style));
 if($('language'))$('language').value=s.language||'';if($('captions'))$('captions').checked=s.captions!==false;if($('caption-y'))$('caption-y').value=s.caption_y||1450;
 if($('hook'))$('hook').value=s.hook||'';if($('clip-title'))$('clip-title').value=s.title||'';if($('post-caption'))$('post-caption').value=s.post_caption||'';
 words=s.words||[];wordRange=signature();renderWords();editingClip=file;$('saved-edit').hidden=false;const n=Number(saved.video_clips)||1;$('apply-video').textContent=n>1?'Apply to all '+n+' clips of this video':'Apply to this clip';$('apply-one').hidden=n<=1;
 status(n>1?'Saved clip loaded. Your change will apply to all '+n+' clips of this video (each keeps its own words and title).':'Saved clip loaded. Change framing or captions; saved source and words are reused.');
}
function swapEditedCards(edited){
 for(const e of edited||[]){const olds=[e.previous,...(e.was||[])];const card=[...document.querySelectorAll('.mk')].find(x=>olds.includes(x.dataset.file));if(!card)continue;
  card.dataset.file=e.file;const v=card.querySelector('video');if(v){v.src='/media/'+e.file;v.load();}
  const dl=card.querySelector('a[download]');if(dl){dl.href='/media/'+e.file;dl.download='neonclipping-'+e.file+'.mp4';}
  const t=card.querySelector('b');if(t&&e.title&&!t.parentElement.classList.contains('viral'))t.textContent=e.title;}
}
async function applySavedEdit(scope){
 if(!editingClip)throw Error('Open Edit clip from My clips first.');
 const settings=exportSettings(id,Number($('start').value),Number($('end').value));
 const changes={};for(const k of ['layout','aspect','resolution','fps','top','cam','game','caption_style','caption_y','captions','hook','title','post_caption'])changes[k]=settings[k];
 const body={file:editingClip,scope,changes};const edited=collectWords();if(edited!==null)body.words=edited;
 $('apply-one').disabled=true;$('apply-video').disabled=true;
 try{const {job,clips}=await request('/clips/reedit',body);status('Updating '+clips+' saved clip(s) — no download, AI picking or transcription.');
 for(;;){const result=await request('/status/'+job);status(result.stage||result.status);if(result.status==='error')throw Error(result.error);if(result.status==='done'){swapEditedCards(result.clips);if(result.clips?.length){const changed=result.clips.find(c=>c.previous===editingClip)||result.clips[0];editingClip=changed.file;showDownload({file:changed.file,srt:changed.srt});}await refreshLibrary();status(clips+' clip(s) updated in '+result.elapsed_seconds+'s. Source, moments and subtitles reused.');break;}await sleep(500);}}
 finally{$('apply-one').disabled=false;$('apply-video').disabled=false;}
}
$('apply-one').onclick=()=>applySavedEdit('one').catch(e=>status(e.message));
$('apply-video').onclick=()=>applySavedEdit('video').catch(e=>status(e.message));

if($('edit-top'))$('edit-top').addEventListener('input',()=>{if($('top'))$('top').value=$('edit-top').value;loadNativeLayouts().catch(e=>status(e.message));});

// Optional donate button: only shown when donate.json (or env vars) has https links.
(async()=>{try{const d=await request('/donate');if(!d.razorpay&&!d.paypal)return;if(d.razorpay){$('don-rzp').href=d.razorpay;$('don-rzp').hidden=false;}if(d.paypal){$('don-pp').href=d.paypal;$('don-pp').hidden=false;}
 $('donate').hidden=false;$('donate').onclick=()=>$('donate-dialog').showModal();$('don-close').onclick=()=>$('donate-dialog').close();}catch(e){}})();

// ---- Live clipping (record + chat spikes + backdated clips)
let liveId=null,liveTimer=null;const liveShown=new Set();
function liveStars(d,file){const stars=document.createElement('div');stars.className='stars';const fb=document.createElement('small');fb.className='fb';fb.textContent='Rate this clip ↑';
 for(let n=1;n<=5;n++){const sb=document.createElement('button');sb.textContent='★';sb.onclick=async()=>{try{const r=await request('/feedback',{file,rating:n});[...stars.children].forEach((x,i)=>x.classList.toggle('on',i<n));fb.textContent='Thanks! Learning from '+r.ratings+' rating'+(r.ratings===1?'':'s');}catch(e){fb.textContent=e.message;}};stars.append(sb);}
 d.append(stars,fb);}
function liveCard(c){const d=document.createElement('div');d.className='mk';d.dataset.file=c.file;
 if(c.error){d.textContent='Clip failed: '+c.error;return d;}
 const vid=document.createElement('video');vid.src='/media/'+c.file;vid.controls=true;vid.preload='metadata';
 const t=document.createElement('b');t.textContent=c.title;
 const ax=c.axes||{},f=v=>v==null?'–':v;const vb=document.createElement('div');vb.className='viral';vb.innerHTML='<b>🔥 Viral '+(c.viral??'–')+'</b><span>Chat '+f(ax.chat)+'</span><span>Energy '+f(ax.energy)+'</span><span>Words '+f(ax.words)+'</span><span>AI '+f(ax.ai)+'</span>';
 const m=document.createElement('small');m.textContent=(c.why||'')+(c.hook?' · "'+c.hook+'"':'');
 const a=document.createElement('a');a.href='/media/'+c.file;a.download='neonclipping-live-'+c.file+'.mp4';a.textContent='Download MP4';
 const edit=document.createElement('button');edit.textContent='Edit clip';edit.onclick=()=>openSavedClip(d.dataset.file).catch(e=>status(e.message));
 d.append(vid,t,vb,m,a,edit);liveStars(d,c.file);return d;}
function liveStatus(s){const mm=String(Math.floor((s.elapsed||0)/60)).padStart(2,'0')+':'+String((s.elapsed||0)%60).padStart(2,'0');
 if(s.status==='error')return '⚠ '+(s.error||'Could not start');
 if(s.status==='starting')return s.stage;
 if(s.status==='recording')return '● Recording '+mm+' · chat '+s.chat_now+' msgs/10 s (normal '+s.baseline+') · chat '+s.chat+' · spikes '+s.spikes+' · clips '+s.clips.length+(s.pending?' · cutting '+s.pending+'…':'');
 return s.stage+' · clips '+s.clips.length;}
async function livePoll(){if(!liveId)return;try{const s=await request('/live/'+liveId);$('live-stats').textContent=liveStatus(s);
 s.clips.forEach((c,i)=>{if(liveShown.has(i))return;liveShown.add(i);$('live-grid').prepend(liveCard(c));});
 if(['stopped','ended','error'].includes(s.status)){clearInterval(liveTimer);liveTimer=null;$('live-start').disabled=false;}}catch(e){$('live-stats').textContent=e.message;clearInterval(liveTimer);$('live-start').disabled=false;}}
let liveFormat='stack';
function setFormatChoice(fmt){
 liveFormat=fmt;
 layout=fmt;
 document.querySelectorAll('.live-fmt-btn').forEach(b=>{
  b.classList.toggle('active',b.dataset.fmt===fmt);
 });
 if(typeof setLayout==='function')setLayout(fmt);
}
document.addEventListener('click',e=>{
 const btn=e.target.closest('.live-fmt-btn');
 if(btn&&btn.dataset.fmt)setFormatChoice(btn.dataset.fmt);
});

$('live-start').onclick=async()=>{const url=$('live-url')?.value?.trim()||'';if(!url){$('live-stats').textContent='Paste a Twitch or YouTube Live link first.';$('live-box').hidden=false;return;}
 $('live-start').disabled=true;$('live-box').hidden=false;$('live-grid').replaceChildren();liveShown.clear();$('live-stats').textContent='Starting the recorder…';
 try{const r=await request('/live/start',{url,layout:liveFormat||'stack',aspect:$('aspect')?.value||'vertical',resolution:Number($('resolution')?.value||1080),fps:Number($('fps')?.value||0),cam:rect?.cam||{x:.72,y:.40,w:.28,h:.28},game:rect?.game||{x:.25,y:0,w:.50,h:1},top:Number($('top')?.value||32)/100,caption_style:style||'gta',language:$('language')?.value||''});liveId=r.live;liveTimer=setInterval(livePoll,2000);livePoll();}
 catch(e){$('live-stats').textContent='⚠ '+e.message;$('live-start').disabled=false;if(/already running/.test(e.message)){try{const r=await request('/live-active');if(r.live){liveId=r.live;$('live-start').disabled=true;liveTimer=setInterval(livePoll,2000);livePoll();}}catch(_){}}}};
$('live-now').onclick=async()=>{if(!liveId)return;try{await request('/live/'+liveId+'/clip-now',{});$('live-stats').textContent='Cutting the last 30 s…';}catch(e){$('live-stats').textContent='⚠ '+e.message;}};
$('live-stop').onclick=async()=>{if(!liveId)return;try{await request('/live/'+liveId+'/stop',{});}catch(e){}livePoll();};

// Re-attach to a live session that is already running (after a page reload) so Stop always works.
(async()=>{try{const r=await request('/live-active');if(r.live){liveId=r.live;$('live-box').hidden=false;$('live-start').disabled=true;liveTimer=setInterval(livePoll,2000);livePoll();}}catch(e){}})();

// ---- Creators: a list of streamers that NeonClipping watches and clips automatically when they go live.
const creatorOpts=()=>({layout:typeof makeLayout==='function'?makeLayout():(liveFormat||'stack'),aspect:$('aspect')?.value||'vertical',resolution:Number($('resolution')?.value||1080),fps:Number($('fps')?.value||0),cam:rect?.cam||{x:.72,y:.40,w:.28,h:.28},game:rect?.game||{x:.25,y:0,w:.50,h:1},top:Number($('top')?.value||32)/100,caption_style:style||'gta',language:$('language')?.value||''});
let creatorSig='';const creatorFeedOpen=new Set();
function attachLive(id){if(liveId===id)return;liveId=id;liveShown.clear();$('live-grid').replaceChildren();$('live-box').hidden=false;$('live-start').disabled=true;clearInterval(liveTimer);liveTimer=setInterval(livePoll,2000);livePoll();}
function agoText(t){if(!t)return 'never';const m=Math.round((Date.now()/1000-t)/60);return m<1?'just now':m<60?m+' min ago':Math.round(m/60)+' h ago';}
function miniCard(clip){const d=document.createElement('div');d.className='mk';d.dataset.file=clip.id;
 const v=document.createElement('video');v.src='/media/'+clip.id;v.controls=true;lazyVideo(v);
 const t=document.createElement('b');t.textContent=clip.title;const m=document.createElement('small');m.textContent=(clip.meta.duration||0).toFixed(0)+' s · '+new Date(clip.created*1000).toLocaleString();
 const a=document.createElement('a');a.href='/media/'+clip.id;a.download='neonclipping-'+clip.id+'.mp4';a.textContent='Download MP4';
 const e=document.createElement('button');e.textContent='Edit clip';e.onclick=()=>openSavedClip(d.dataset.file).catch(x=>status(x.message));
 d.append(v,t,m,a,e);return d;}
async function fillCreatorFeed(id,grid){const lib=await request('/library');grid.replaceChildren();const mine=lib.filter(c=>(c.settings||{}).creator===id);
 if(!mine.length){const p=document.createElement('small');p.textContent='No clips yet. They appear here by themselves while this creator is live.';grid.append(p);return;}
 mine.forEach(c=>grid.append(miniCard(c)));}
function chipFor(c){const s=document.createElement('span');s.className='chip';
 if(c.status==='recording'){s.classList.add('recording');s.textContent='● Recording & clipping';}
 else if(c.status==='live'&&c.paused_until){s.classList.add('wait');s.textContent='LIVE · paused for '+Math.max(1,Math.round((c.paused_until-Date.now()/1000)/60))+' min (switch Auto off and on to resume now)';}
 else if(c.status==='live'&&c.waiting){s.classList.add('live');s.textContent='LIVE · waiting for the current stream to finish';}
 else if(c.status==='live'){s.classList.add('live');s.textContent=c.auto?'LIVE · starting…':'LIVE now (auto is off)';}
 else if(c.status==='unknown'){s.textContent='Checking…';}
 else{s.textContent='Offline · checked '+agoText(c.checked);}
 return s;}
async function loadCreators(force){
 const r=await request('/creators');
 const sig=JSON.stringify(r.creators.map(c=>[c.id,c.status,c.auto,c.clips,c.waiting,c.session,!!c.paused_until,Math.floor((c.checked||0)/30)]));
 const act=await request('/live-active').catch(()=>({}));if(act.live&&!liveId)attachLive(act.live);
 if(!force&&sig===creatorSig)return;creatorSig=sig;
 const host=$('creator-list');host.replaceChildren();
 if(!r.creators.length){const p=document.createElement('p');p.className='muted';p.textContent='No creators yet. Add a streamer above and NeonClipping will clip them whenever they go live.';host.append(p);return;}
 for(const c of r.creators){
  const row=document.createElement('div');row.className='creator-row';
  const top=document.createElement('div');top.className='creator-top';
  const nm=document.createElement('b');nm.textContent=c.name;const pl=document.createElement('span');pl.className='plat';pl.textContent=c.platform;
  const lab=document.createElement('label');const cb=document.createElement('input');cb.type='checkbox';cb.checked=!!c.auto;
  cb.onchange=()=>request('/creators/'+c.id+'/auto',{auto:cb.checked}).then(()=>loadCreators(true)).catch(e=>status(e.message));
  lab.append(cb,document.createTextNode('Auto-clip when live'));top.append(nm,pl,chipFor(c),lab);
  const acts=document.createElement('div');acts.className='creator-actions';
  const mk=(txt,fn)=>{const b=document.createElement('button');b.className='secondary';b.textContent=txt;b.onclick=()=>fn(b).catch(e=>{$('creator-note').textContent='⚠ '+e.message;});acts.append(b);return b;};
  mk('Check now',async b=>{b.disabled=true;$('creator-note').textContent='Checking '+c.name+'…';const x=await request('/creators/'+c.id+'/check',{});$('creator-note').textContent=c.name+(x.live?' is live right now.':' is not live right now.');await loadCreators(true);});
  mk('Use my current layout',async()=>{await request('/creators/'+c.id+'/layout',creatorOpts());$('creator-note').textContent='Saved your current layout, facecam boxes and caption style for '+c.name+'.';});
  if(c.session)mk('Open live view',async()=>{attachLive(c.session);$('live-box').scrollIntoView({behavior:'smooth'});});
  mk('Remove',async()=>{if(!confirm('Remove '+c.name+' from your list? Their clips stay in My clips.'))return;await request('/creators/'+c.id+'/remove',{});await loadCreators(true);});
  const det=document.createElement('details');det.className='creator-feed';const sm=document.createElement('summary');sm.textContent='Clips from '+c.name+' ('+c.clips+')';const grid=document.createElement('div');grid.className='make-grid';
  det.append(sm,grid);det.open=creatorFeedOpen.has(c.id);if(det.open)fillCreatorFeed(c.id,grid).catch(()=>{});
  det.ontoggle=()=>{if(det.open){creatorFeedOpen.add(c.id);fillCreatorFeed(c.id,grid).catch(()=>{});}else creatorFeedOpen.delete(c.id);};
  row.append(top,acts,det);host.append(row);}
}
$('creator-add').onclick=async()=>{const url=$('creator-url').value.trim();if(!url){$('creator-note').textContent='Type a Twitch name or paste a channel link first.';return;}
 try{await request('/creators',{url,...creatorOpts()});$('creator-url').value='';$('creator-note').textContent='Added. Checking if they are live…';await loadCreators(true);}catch(e){$('creator-note').textContent='⚠ '+e.message;}};
loadCreators(true).catch(()=>{});setInterval(()=>{if(!document.hidden)loadCreators().catch(()=>{});},8000);

$('download-bundle').onclick=async()=>{try{if(!editingClip)throw Error('Open a saved clip first.');status('Preparing this video’s saved clips — no rendering.');const result=await request('/bundles',{file:editingClip,scope:'video'});const link=$('bundle-link');link.href=result.download;link.hidden=false;link.textContent='Download '+result.clips+' clips + subtitles ZIP';status('Bundle ready. Original MP4s, subtitles and post copy are included.');}catch(e){status(e.message);}};

let queuePaused=false;
async function refreshQueue(){const result=await request('/queue');queuePaused=result.paused;$('queue-pause').textContent=queuePaused?'Resume queue':'Pause queue';const host=$('queue-list');host.replaceChildren();for(const row of result.jobs){const card=document.createElement('div');card.className='queue-item';const title=document.createElement('b');title.textContent=row.kind+' · '+(row.payload.url||row.payload.id||'Saved clip');const detail=document.createElement('small');detail.textContent=row.status+(row.result.stage?' · '+row.result.stage:'')+(row.result.error?' · '+row.result.error:'');card.append(title,detail);if(['error','interrupted','canceled'].includes(row.status)){const button=document.createElement('button');button.textContent='Retry';button.onclick=async()=>{await request('/queue/'+row.id+'/retry',{});refreshQueue();};card.append(button);}if(row.status==='queued'){const button=document.createElement('button');button.textContent='Cancel waiting job';button.onclick=async()=>{await request('/queue/'+row.id+'/cancel',{});refreshQueue();};card.append(button);}host.append(card);}}
$('queue-add').onclick=async()=>{try{const urls=$('queue-urls').value.split(/\n/).map(x=>x.trim()).filter(Boolean);if(!urls.length)throw Error('Paste at least one video link.');for(const url of urls){await request('/queue',{kind:'make',not_before:$('queue-time').value?new Date($('queue-time').value).getTime()/1000:0,payload:{url,count:Number($('count').value),mode:$('mode').value,accurate:$('accurate').value,min_len:Number($('minlen').value),max_len:Number($('length').value),...creatorOpts()}});}status(urls.length+' video(s) saved to the local processing queue.');await refreshQueue();}catch(e){status(e.message);}};
$('queue-pause').onclick=async()=>{try{await request('/queue/pause',{paused:!queuePaused});await refreshQueue();}catch(e){status(e.message);}};
$('queue-refresh').onclick=()=>refreshQueue().catch(e=>status(e.message));
$('queue-panel').addEventListener('toggle',()=>{if($('queue-panel').open)refreshQueue().catch(e=>status(e.message));});
setInterval(()=>{if($('queue-panel').open)refreshQueue().catch(()=>{});},2000);

for(const key of ['aspect','resolution']){const el=$(key);if(el)el.addEventListener('change',()=>loadNativeLayouts().catch(e=>status(e.message)));}

$('timing-editor').onchange=renderWords;
$('shift-captions').onclick=()=>{try{const current=collectWords();if(!current?.length)throw Error('Generate or import subtitles first.');const offset=Number($('caption-shift').value);if(!Number.isFinite(offset))throw Error('Choose a valid shift.');const shifted=current.map(w=>({...w,start:w.start+offset,end:w.end+offset}));const duration=Number($('end').value)-Number($('start').value);if(shifted.some(w=>w.start<0||w.end>duration+.1))throw Error('That shift places words outside the clip. Choose a smaller shift.');words=shifted;renderWords();status('Subtitle times shifted by '+offset+' seconds. Apply or export to update the video.');}catch(e){status(e.message);}};
