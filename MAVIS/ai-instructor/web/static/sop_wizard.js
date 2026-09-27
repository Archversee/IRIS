/* ================= SOP -> rules wizard =================
   Instructor area tab "Aircraft SOPs" + a 3-stage full-screen wizard:
     1 Upload & process   (PDF -> local OCR/LLM -> draft rules, progress polled)
     2 Verify             (instructor approves / edits / rejects every rule)
     3 Bind to aircraft   (verified ruleset -> profiles/aircraft/<id>.yaml)
   Talks only to MAVIS's own /sop/* API (sop/routes.py). Loaded after the main
   script: uses its $(), confirmDialog() and hideConfirm(). */
(function(){
"use strict";
const W={meta:null,status:null,stage:1,rsId:null,rs:null,job:null,poll:null,origRule:null,
         filter:"draft",selId:null,view:"image",acSel:null,bound:null,dirty:false};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=n=>n==null?"":(Math.round(n*100)/100).toString();

async function api(method,path,body,form){
  const opt={method,headers:{}};
  if(form){opt.body=form;}
  else if(body!==undefined){opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(body);}
  const r=await fetch("/sop"+path,opt);
  let j=null;try{j=await r.json();}catch(_){}
  if(!r.ok){const e=new Error((j&&j.detail)||(r.status+" "+r.statusText));e.status=r.status;throw e;}
  return j;
}
function toast(title,msg){confirmDialog(title,msg,[{label:"OK",cls:"primary",fn:hideConfirm}]);}

/* ---------- vocabulary / plain-language rule summary ---------- */
function metricInfo(id){return (W.meta&&W.meta.metrics.find(m=>m.id===id))||{label:id||"",unit:""};}
const UNIT_TXT={kt:"kt",ft:"ft","ft/min":"ft/min",deg:"°",rpm:"RPM",frac:"",bool:""};
function val(metric,v){
  if(v==null)return "?";
  const u=metricInfo(metric).unit;
  if(u==="frac")return Math.round(v*100)+"%";
  if(u==="bool")return v?"yes":"no";
  return fmt(v)+(u==="deg"?"°":" "+(UNIT_TXT[u]||u));
}
function unitSuffix(metric){const u=metricInfo(metric).unit;return u==="deg"?"°":(u==="frac"||u==="bool"?"":" "+(UNIT_TXT[u]||u));}
const phaseTxt=p=>p.toLowerCase().replace(/_/g," ").replace(/^./,c=>c.toUpperCase());
const OP_TXT={">=":"at least","<=":"at most","==":"exactly",">":"above","<":"below"};
function whenTxt(r){
  const c=(r.conditions||[]).map(x=>`${metricInfo(x.metric).label.toLowerCase()} ${OP_TXT[x.op]||x.op} ${val(x.metric,x.value)}`);
  return c.length?" while "+c.join(" and "):"";
}
function phasesTxt(r){return (r.phases&&r.phases.length)?" during "+r.phases.map(phaseTxt).join(", "):" at all times";}
function describe(r){
  const m=metricInfo(r.metric).label;
  switch(r.kind){
    case "band":{
      const hi=r.tol_hi,lo=r.tol_lo;
      const u=unitSuffix(r.metric);
      const tol=(hi==null&&lo==null)?" (no tolerance given)":
        (hi===lo?` ± ${fmt(hi)}${u}`:` +${fmt(hi??0)} / −${fmt(lo??0)}${u}`);
      return `Hold ${m.toLowerCase()} at ${val(r.metric,r.target)}${tol}${phasesTxt(r)}${whenTxt(r)}.`;}
    case "limit":{
      const a=r.min_value,b=r.max_value;
      if(r.metric==="vs"&&a!=null&&a<0&&b==null)          // SOPs say "descent rate", we store -fpm
        return `Keep the descent rate at or below ${fmt(-a)} ft/min${phasesTxt(r)}${whenTxt(r)}.`;
      if(a!=null&&b!=null&&a===-b&&b>0)
        return `Keep ${m.toLowerCase()} within ±${val(r.metric,b)}${phasesTxt(r)}${whenTxt(r)}.`;
      const rng=a!=null&&b!=null?`between ${val(r.metric,a)} and ${val(r.metric,b)}`:
        (b!=null?`at or below ${val(r.metric,b)}`:`at or above ${val(r.metric,a)}`);
      return `Keep ${m.toLowerCase()} ${rng}${phasesTxt(r)}${whenTxt(r)}.`;}
    case "event":
      return `${m} must be ${OP_TXT[r.op]||r.op} ${val(r.metric,r.value)}`+
        (r.gate_phase?` by the start of ${phaseTxt(r.gate_phase).toLowerCase()}`:"")+`${whenTxt(r)}.`;
    case "capture":{
      const a=r.min_value,b=r.max_value;
      const rng=a!=null&&b!=null?`between ${val(r.metric,a)} and ${val(r.metric,b)}`:
        (b!=null?`at most ${val(r.metric,b)}`:`at least ${val(r.metric,a)}`);
      return `At ${String(r.capture_at||"?").replace(/_/g," ")}, ${m.toLowerCase()} should be ${rng}.`;}
    default: return "Procedure the simulator can't observe — shown for the instructor to assess by eye or orally.";
  }
}
const STATUS_TXT={draft:"Needs review",approved:"Approved",rejected:"Rejected",auto_rejected:"Auto-rejected"};

/* ---------- instructor-area tab: list of rulesets ---------- */
async function renderTab(){
  const wrap=$("sopTabList");if(!wrap)return;
  wrap.innerHTML='<p class="sopEmpty">Loading…</p>';
  let list,binds;
  try{[list,binds]=await Promise.all([api("GET","/rulesets"),api("GET","/bindings")]);}
  catch(e){wrap.innerHTML=`<div class="sopErr">${esc(e.message)}</div>`;return;}
  const bound=binds.length?`<p class="sopNote" style="margin-bottom:10px">Active: ${binds.map(b=>
    `<b>${esc(acName(b.aircraft_profile))}</b> ← ${esc(b.filename)} v${b.version}`).join(" · ")}</p>`:"";
  if(!list.length){wrap.innerHTML=bound+'<p class="sopEmpty">No SOPs yet. Upload one to get started.</p>';return;}
  wrap.innerHTML=bound+'<div class="sopList">'+list.map(rs=>{
    const n=rs.n_draft+rs.n_approved+rs.n_rejected;
    const meta=rs.status==="processing"?`Processing… ${rs.chunks_done}/${rs.chunks_total} sections`:
      rs.status==="failed"?esc(rs.error||"Failed"):
      `${rs.n_approved} approved · ${rs.n_draft} to review · ${rs.n_rejected} rejected`+
      (rs.n_auto_rejected?` · ${rs.n_auto_rejected} auto-rejected`:"");
    return `<button class="sopItem" data-rs="${rs.id}">
      <b>${esc(rs.filename)} <span style="color:var(--dim);font-weight:500">v${rs.version}</span></b>
      <span class="meta">${meta}${n?"":""} · ${new Date(rs.created_at).toLocaleDateString()}${rs.created_by?" · "+esc(rs.created_by):""}</span>
      <span class="right"><span class="sopChip ${rs.status}">${rs.status==="draft"?"Needs verification":rs.status}</span>
        ${(rs.bound_to||[]).map(a=>`<span class="sopBound">● ${esc(acName(a))}</span>`).join("")}</span>
    </button>`;}).join("")+"</div>";
  wrap.querySelectorAll(".sopItem").forEach(b=>b.onclick=()=>openRuleset(b.dataset.rs));
}
function acName(id){const a=W.meta&&W.meta.aircraft.find(x=>x.id===id);return a?a.name:id;}

/* ---------- wizard shell ---------- */
async function ensureMeta(){if(!W.meta)W.meta=await api("GET","/meta");}
function open(){$("sopOverlay").classList.add("on");document.addEventListener("keydown",onKey);}
function close(){
  const go=()=>{hideConfirm();restoreOrig();$("sopOverlay").classList.remove("on");stopPoll();
    document.removeEventListener("keydown",onKey);W.dirty=false;renderTab();};
  if(W.dirty)confirmDialog("Discard changes?","You have unsaved edits to this rule.",[
    {label:"Keep editing",fn:hideConfirm},{label:"Discard",cls:"danger",fn:go}]);
  else go();
}
function stopPoll(){if(W.poll){clearTimeout(W.poll);W.poll=null;}}
function head(){
  const doneTo=W.rs?(W.rs.status==="verified"?(W.rs.bound_to&&W.rs.bound_to.length?3:2):1):0;
  $("sopSteps").innerHTML=[["Upload & process"],["Verify rules"],["Bind to aircraft"]].map(([t],i)=>{
    const n=i+1;const cls=(W.stage===n?"on ":"")+(n<=doneTo&&W.stage!==n?"done":"");
    return `<div class="sopStep ${cls}"><i>${n<=doneTo&&W.stage!==n?"✓":n}</i><span>${t}</span></div>`;}).join("");
  $("sopDocName").textContent=W.rs?`${W.rs.filename} · v${W.rs.version}`:(W.job?W.job.filename:"");
}
function foot(html){$("sopFoot").innerHTML=html;}

async function startNew(){
  try{await ensureMeta();}catch(e){toast("SOP rules unavailable",e.message);return;}
  Object.assign(W,{stage:1,rsId:null,rs:null,job:null,selId:null,dirty:false,filter:"draft"});
  open();renderStage1();
}
async function openRuleset(id){
  try{await ensureMeta();W.rs=await api("GET","/rulesets/"+id);}catch(e){toast("Could not open",e.message);return;}
  Object.assign(W,{rsId:id,job:null,selId:null,dirty:false,filter:"draft"});
  open();
  if(W.rs.status==="processing"){W.stage=1;renderStage1(true);}
  else if(W.rs.status==="failed"){W.stage=1;renderStage1();}
  else if(W.rs.status==="verified"){W.stage=3;renderStage3();}
  else {W.stage=2;renderStage2();}
}

/* ---------- stage 1: upload & process ---------- */
async function renderStage1(resumeOnly){
  W.stage=1;head();
  const b=$("sopBody");
  if(resumeOnly||W.job){renderProgress();return;}
  b.innerHTML=`<div class="sopS1">
    <div class="sopHealth" id="sopHealth"><span class="sopChip">Checking local services…</span></div>
    ${W.rs&&W.rs.status==="failed"?`<div class="sopErr">Last attempt failed: ${esc(W.rs.error||"unknown error")}</div>`:""}
    <label class="sopDrop" id="sopDrop">
      <input type="file" id="sopFile" accept="application/pdf,.pdf" hidden>
      <h3>Drop the aircraft SOP here</h3>
      <p>PDF, up to 50 MB. Typed or scanned pages both work — scanned pages are read with OCR.<br>Everything runs on this machine; nothing is sent to the internet.</p>
    </label>
    <p class="sopNote">MAVIS reads every page, the local model <b id="sopModel">…</b> drafts rules from it, and each rule is checked against the SOP text before you see it.
      Nothing is used in a lesson until you <b>verify</b> the rules and <b>bind</b> them to an aircraft.</p>
  </div>`;
  foot(`<span class="grow"></span><button class="sopBtn" id="sopCancel1">Cancel</button>`);
  $("sopCancel1").onclick=close;
  const drop=$("sopDrop"),file=$("sopFile");
  file.onchange=()=>file.files[0]&&upload(file.files[0]);
  drop.ondragover=e=>{e.preventDefault();drop.classList.add("over");};
  drop.ondragleave=()=>drop.classList.remove("over");
  drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");const f=e.dataTransfer.files[0];f&&upload(f);};
  try{
    const s=W.status=await api("GET","/status");
    if(!$("sopHealth"))return;            // user already dropped a file; screen moved on
    $("sopModel").textContent=s.model;
    $("sopHealth").innerHTML=[
      [s.db,"Rules database",s.db?"":"Postgres not reachable or db/schema_sop_rules.sql not applied"],
      [s.llm,"Local LLM "+s.model,s.llm?"":"Not running in Ollama — you can still upload and add rules by hand"],
      [s.ocr,"OCR (Tesseract)",s.ocr?"":"Not installed — scanned pages will be skipped"],
      [!s.flying,"No flight in progress",s.flying?"Processing pauses until the flight ends":""],
    ].map(([ok,t,why])=>`<span class="sopChip ${ok?"ok":"bad"}" title="${esc(why)}">${esc(t)}</span>`).join("")
     +(s.busy?'<span class="sopChip draft">Another SOP is being processed</span>':"");
  }catch(e){const h=$("sopHealth");if(h)h.innerHTML=`<div class="sopErr">${esc(e.message)}</div>`;}
}
async function upload(f){
  if(!/\.pdf$/i.test(f.name)&&f.type!=="application/pdf"){toast("Not a PDF","Choose the SOP as a PDF file.");return;}
  const fd=new FormData();fd.append("file",f);
  $("sopDrop").innerHTML=`<h3>Uploading ${esc(f.name)}…</h3>`;
  try{W.job=await api("POST","/upload",undefined,fd);}
  catch(e){toast("Upload failed",e.message);renderStage1();return;}
  renderProgress();
}
const STAGES=[["extracting","Read pages"],["parsing","Draft rules"],["validating","Check rules"],["done","Ready"]];
function renderProgress(){
  head();
  const j=W.job;
  const rs=W.rs;
  const stage=j?j.stage:"parsing";
  const done=j?j.done:(rs?rs.chunks_done:0),total=j?j.total:(rs?rs.chunks_total:0);
  const pct=stage==="done"?100:(total?Math.round(100*done/total):5);
  const idx=STAGES.findIndex(s=>s[0]===(stage==="paused"?"parsing":stage));
  $("sopBody").innerHTML=`<div class="sopS1">
    <div class="sopProg">
      <div class="lbl"><b>${esc(j?j.filename:rs.filename)}</b><span>${j?Math.round(j.elapsed_s)+" s":""}</span></div>
      <div class="sopBar"><i style="width:${pct}%"></i></div>
      <p class="sopNote" style="margin-top:10px">${esc(j?(j.detail||"Starting…"):"Processing in the background")}</p>
      <div class="sopStages">${STAGES.map((s,i)=>`<span class="${i<idx||stage==="done"?"done":(i===idx?"on":"")}">${i<idx||stage==="done"?"✓ ":""}${s[1]}</span>`).join("")}</div>
    </div>
    ${stage==="paused"?'<div class="sopWarn">Paused while a flight is in progress — it continues automatically once the lesson ends.</div>':""}
    ${j&&j.error?`<div class="sopErr">${esc(j.error)}</div>`:""}
    ${j&&j.warnings.length?j.warnings.map(w=>`<div class="sopWarn">${esc(w)}</div>`).join(""):""}
    ${stage==="done"&&j&&j.counts&&j.counts.draft!=null?`<p class="sopNote">${j.counts.draft} rules drafted${j.counts.auto_rejected?`, ${j.counts.auto_rejected} discarded automatically because they couldn't be traced to the SOP text`:""}.</p>`:""}
  </div>`;
  const failed=stage==="failed";
  foot(`<span class="grow">${stage==="done"?"":"You can close this window — processing continues in the background."}</span>
    ${failed?'<button class="sopBtn" id="sopRetry">Try another file</button>':""}
    <button class="sopBtn" id="sopHide">${stage==="done"?"Close":"Close"}</button>
    ${stage==="done"&&(j?j.ruleset_id:rs)?'<button class="sopBtn primary" id="sopToReview">Review rules →</button>':""}`);
  $("sopHide").onclick=close;
  if(failed)$("sopRetry").onclick=()=>{W.job=null;W.rs=null;renderStage1();};
  const nx=$("sopToReview");if(nx)nx.onclick=()=>openRuleset(j?j.ruleset_id:rs.id);
  if(!["done","failed"].includes(stage)){
    stopPoll();
    W.poll=setTimeout(async()=>{
      try{
        if(W.job)W.job=await api("GET","/jobs/"+W.job.id);
        else{W.rs=await api("GET","/rulesets/"+W.rsId);if(W.rs.status!=="processing"){openRuleset(W.rsId);return;}}
      }catch(e){if(W.job&&e.status===404){W.job=null;}}
      if($("sopOverlay").classList.contains("on")&&W.stage===1)renderProgress();
    },1000);
  }
}

/* ---------- stage 2: verify ---------- */
const FILTERS=[["draft","Needs review"],["approved","Approved"],["rejected","Rejected"],["auto_rejected","Auto-rejected"],["all","All"]];
function rules(){return (W.rs&&W.rs.rules)||[];}
function visible(){return rules().filter(r=>W.filter==="all"||r.review_status===W.filter);}
function frozen(){return W.rs&&W.rs.status!=="draft";}

function renderStage2(){
  W.stage=2;head();
  const rs=W.rs;
  const counts=Object.fromEntries(FILTERS.map(([k])=>[k,k==="all"?rules().length:rules().filter(r=>r.review_status===k).length]));
  if(!counts[W.filter]&&W.filter!=="all")W.filter=counts.draft?"draft":"all";
  const vis=visible();
  if(!vis.find(r=>r.id===W.selId))W.selId=vis.length?vis[0].id:null;
  let lastPage=-1;
  const listHtml=vis.length?vis.map(r=>{
    const hdr=r.source_page!==lastPage?`<div class="sopPageHdr">${r.source_page?"Page "+r.source_page:"Added by instructor"}</div>`:"";
    lastPage=r.source_page;
    const flags=(r.issues||[]).filter(i=>!i.startsWith("Auto-rejected")).slice(0,3)
      .map(i=>`<span title="${esc(i)}">${esc(i.length>34?i.slice(0,32)+"…":i)}</span>`).join("");
    return hdr+`<button class="sopRule ${r.id===W.selId?"sel":""} st-${r.review_status}" data-id="${r.id}">
      <span class="dot ${r.severity}" title="${r.severity}"></span><b>${esc(r.title)}</b>
      <span class="st ${r.review_status}">${STATUS_TXT[r.review_status]}</span>
      <span class="sum">${esc(describe(r))}</span>${flags?`<span class="flags">${flags}</span>`:""}</button>`;
  }).join(""):`<p class="sopEmpty">${W.filter==="draft"?"Nothing left to review.":"No rules here."}</p>`;

  $("sopBody").innerHTML=`<div class="sopS2">
    <div class="sopRules">
      ${rs.error?`<div class="sopWarn" style="margin-bottom:10px">${esc(rs.error)}</div>`:""}
      <div class="sopFilters">${FILTERS.map(([k,t])=>`<button data-f="${k}" class="${W.filter===k?"on":""}">${t} ${counts[k]}</button>`).join("")}</div>
      <div class="sopRuleList" id="sopRuleList">${listHtml}</div>
      ${frozen()?"":`<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
        <button class="sopBtn small" id="sopAdd">+ Add rule by hand</button>
        <button class="sopBtn small good" id="sopApproveClean" title="Approve every rule that needs review and has no warnings">Approve all without warnings</button></div>`}
    </div>
    <div class="sopEdit" id="sopEdit"></div>
  </div>`;
  $("sopBody").querySelectorAll("[data-f]").forEach(b=>b.onclick=()=>guard(()=>{W.filter=b.dataset.f;W.selId=null;renderStage2();}));
  $("sopBody").querySelectorAll(".sopRule").forEach(b=>b.onclick=()=>guard(()=>select(+b.dataset.id)));
  if(!frozen()){$("sopAdd").onclick=()=>guard(addRule);$("sopApproveClean").onclick=approveClean;}
  renderEditor();

  const decided=counts.approved+counts.rejected;
  const total=counts.approved+counts.rejected+counts.draft;
  if(frozen()){
    foot(`<span class="grow">Verified by ${esc(rs.verified_by||"instructor")} — rules are locked. Revise to create an editable copy.</span>
      <button class="sopBtn" id="sopRevise">Revise</button><button class="sopBtn primary" id="sopToBind">Bind to aircraft →</button>`);
    $("sopRevise").onclick=revise;$("sopToBind").onclick=()=>renderStage3();
  }else{
    foot(`<span class="grow">${decided} of ${total} decided · ${counts.approved} approved
      <span class="sopKeys" style="margin-left:10px"><kbd>J</kbd>/<kbd>K</kbd> move · <kbd>A</kbd> approve · <kbd>R</kbd> reject</span></span>
      <button class="sopBtn bad" id="sopDiscard">Delete draft</button>
      <button class="sopBtn primary" id="sopVerify" ${counts.draft||!counts.approved?"disabled":""}
        title="${counts.draft?counts.draft+" rule(s) still need a decision":(!counts.approved?"Approve at least one rule":"")}">Mark as verified →</button>`);
    $("sopVerify").onclick=verify;$("sopDiscard").onclick=discard;
  }
}
function restoreOrig(){
  if(!W.origRule)return;
  const i=W.rs.rules.findIndex(x=>x.id===W.origRule.id);
  if(i>=0)W.rs.rules[i]=W.origRule;
  W.origRule=null;
}
function guard(fn){
  if(!W.dirty){restoreOrig();fn();return;}
  confirmDialog("Discard changes?","You have unsaved edits to this rule.",[
    {label:"Keep editing",fn:hideConfirm},{label:"Discard",cls:"danger",fn:()=>{hideConfirm();W.dirty=false;restoreOrig();fn();}}]);
}
function select(id){W.selId=id;W.dirty=false;
  document.querySelectorAll(".sopRule").forEach(b=>b.classList.toggle("sel",+b.dataset.id===id));
  const el=document.querySelector(`.sopRule[data-id="${id}"]`);el&&el.scrollIntoView({block:"nearest"});
  renderEditor();}

function numField(id,label,v,metric,dis){
  const u=metric?metricInfo(metric).unit:"";
  return `<div class="sopField"><label for="${id}">${label}</label><div class="unit">
    <input id="${id}" type="number" step="any" value="${v==null?"":fmt(v)}" ${dis}><em>${esc(u==="frac"?"0–1":(UNIT_TXT[u]??u))}</em></div></div>`;
}
function sel(id,label,opts,cur,dis,wide){
  return `<div class="sopField ${wide?"wide":""}"><label for="${id}">${label}</label><select id="${id}" ${dis}>
    ${opts.map(([v,t])=>`<option value="${esc(v)}" ${String(v)===String(cur??"")?"selected":""}>${esc(t)}</option>`).join("")}</select></div>`;
}
function renderEditor(){
  const box=$("sopEdit");if(!box)return;
  const r=rules().find(x=>x.id===W.selId);
  if(!r){box.innerHTML='<p class="sopEmpty">Select a rule to review it.</p>';return;}
  const M=W.meta;
  const ro=frozen()||r.review_status==="rejected";
  const dis=ro?"disabled":"";
  const quoteEditable=!ro&&(r.origin==="manual"||r.review_status==="auto_rejected");
  const k=r.kind;
  const metricOpts=M.metrics.map(m=>[m.id,m.label]);
  let nums="";
  if(k==="band")nums=numField("sfTarget","Target",r.target,r.metric,dis)+numField("sfTolHi","Allowed above (+)",r.tol_hi,r.metric,dis)+numField("sfTolLo","Allowed below (−)",r.tol_lo,r.metric,dis);
  if(k==="limit")nums=numField("sfMin","Minimum",r.min_value,r.metric,dis)+numField("sfMax","Maximum",r.max_value,r.metric,dis);
  if(k==="event")nums=sel("sfOp","Must be",M.ops.map(o=>[o,OP_TXT[o]]),r.op,dis)+numField("sfValue","Value",r.value,r.metric,dis)
      +sel("sfGate","By start of",[["","(any time)"],...M.phases.map(p=>[p,phaseTxt(p)])],r.gate_phase,dis);
  if(k==="capture")nums=sel("sfCapture","Measured at",M.capture_at.map(c=>[c,c.replace(/_/g," ")]),r.capture_at,dis)
      +numField("sfMin","Minimum",r.min_value,r.metric,dis)+numField("sfMax","Maximum",r.max_value,r.metric,dis);
  const conds=(r.conditions||[]).map((c,i)=>`<div class="sopCond" data-i="${i}">
      <select class="cM" ${dis}>${metricOpts.map(([v,t])=>`<option value="${v}" ${v===c.metric?"selected":""}>${esc(t)}</option>`).join("")}</select>
      <select class="cO" ${dis}>${M.ops.map(o=>`<option ${o===c.op?"selected":""}>${o}</option>`).join("")}</select>
      <input class="cV" type="number" step="any" value="${fmt(c.value)}" ${dis}>
      ${ro?"":`<button class="sopBtn small cX" title="Remove condition">✕</button>`}</div>`).join("");
  const issues=(r.issues||[]).map(i=>`<div class="${i.startsWith("Auto-rejected")||i.startsWith("Needs fixing")?"sopErr":"sopWarn"}">${esc(i)}</div>`).join("");

  box.innerHTML=`<div class="sopCard">
      <div style="display:flex;gap:8px;align-items:center"><h3 style="flex:1">${esc(r.title)}</h3>
        <span class="sopChip">${r.origin==="manual"?"Added by instructor":"Drafted by LLM"}</span>
        <span class="sopChip ${r.review_status==="approved"?"verified":(r.review_status==="draft"?"draft":"failed")}">${STATUS_TXT[r.review_status]}</span></div>
      <p class="sopPlain">${esc(describe(r))}</p>
      ${issues?`<div class="sopIssues">${issues}</div>`:""}
      <div class="sopGrid">
        <div class="sopField wide"><label for="sfTitle">Title</label><input id="sfTitle" value="${esc(r.title)}" maxlength="120" ${dis}></div>
        ${sel("sfKind","Rule type",[["band","Hold target ± tolerance"],["limit","Stay within limits"],["event","Must happen by a phase"],["capture","Check at a moment"],["unobservable","Not observable in sim"]],k,dis)}
        ${k!=="unobservable"?sel("sfMetric","Measured value",metricOpts,r.metric,dis):""}
        ${sel("sfSeverity","Severity",M.severities.map(s=>[s,s[0].toUpperCase()+s.slice(1)]),r.severity,dis)}
        ${nums}
        <div class="sopField wide"><label>Applies during <span style="text-transform:none;letter-spacing:0;font-weight:400">(none selected = every phase)</span></label>
          <div class="sopPhases" id="sfPhases">${M.phases.map(p=>`<button type="button" data-p="${p}" class="${(r.phases||[]).includes(p)?"on":""}" ${dis}>${phaseTxt(p)}</button>`).join("")}</div></div>
        ${k!=="unobservable"?`<div class="sopField wide"><label>Only while</label><div id="sfConds">${conds||'<span class="sopNote">No conditions</span>'}</div>
          ${ro?"":'<button class="sopBtn small" id="sfAddCond" style="align-self:flex-start">+ Condition</button>'}</div>`:""}
        <div class="sopField wide"><label for="sfQuote">Source in the SOP${r.source_page?" — page "+r.source_page:""}${r.quote_score!=null&&r.quote_score<1?` (${Math.round(r.quote_score*100)}% match)`:""}</label>
          ${quoteEditable?`<textarea id="sfQuote" rows="3" placeholder="Paste the exact SOP sentence this rule comes from">${esc(r.source_quote||"")}</textarea>
            <input id="sfPage" type="number" min="1" placeholder="Page" value="${r.source_page||""}" style="max-width:120px">`
          :`<div class="sopQuote">${r.source_quote?esc(r.source_quote):'<span class="sopNote">No source — added by hand</span>'}</div>`}</div>
        <div class="sopField wide"><label for="sfNote">Reviewer note</label><textarea id="sfNote" rows="2" ${frozen()?"disabled":""} placeholder="Optional — why you changed or rejected it">${esc(r.reviewer_note||"")}</textarea></div>
      </div>
      ${frozen()?"":`<div class="sopActions">
        ${r.review_status==="auto_rejected"?'<button class="sopBtn primary" id="sfRestore">Fix &amp; restore to review</button>':
          r.review_status==="rejected"?'<button class="sopBtn" id="sfUnreject">Undo reject</button>':
          `<button class="sopBtn primary" id="sfApprove">${W.dirty?"Save & approve":"Approve"}</button>
           <button class="sopBtn" id="sfSave">Save changes</button>
           <button class="sopBtn bad" id="sfReject">Reject</button>`}</div>`}
    </div>
    ${r.source_page?`<div class="sopCard"><div class="sopSrcHead"><h3>SOP page ${r.source_page}</h3>
      <div class="sopSeg"><button data-v="image" class="${W.view==="image"?"on":""}">Page image</button><button data-v="text" class="${W.view==="text"?"on":""}">Extracted text</button></div></div>
      <div id="sopSrc"><p class="sopEmpty">Loading page…</p></div></div>`:""}`;

  // live edits
  const markDirty=()=>{W.dirty=true;const a=$("sfApprove");if(a)a.textContent="Save & approve";};
  box.querySelectorAll("input,select,textarea").forEach(el=>el.addEventListener("input",markDirty));
  $("sfKind")&&($("sfKind").onchange=()=>keepEdits(r));
  $("sfMetric")&&($("sfMetric").onchange=()=>{keepEdits(r);});
  box.querySelectorAll("#sfPhases button").forEach(b=>b.onclick=()=>{if(ro)return;b.classList.toggle("on");markDirty();});
  box.querySelectorAll(".cX").forEach(b=>b.onclick=()=>{b.closest(".sopCond").remove();markDirty();});
  $("sfAddCond")&&($("sfAddCond").onclick=()=>{const e=collect(r,true);keepEdits(r,[...(e.conditions||[]),{metric:"flaps_deg",op:">=",value:0}]);});
  $("sfApprove")&&($("sfApprove").onclick=()=>save(r,"approved"));
  $("sfSave")&&($("sfSave").onclick=()=>save(r,null));
  $("sfReject")&&($("sfReject").onclick=()=>save(r,"rejected",true));
  $("sfRestore")&&($("sfRestore").onclick=()=>save(r,"draft"));
  $("sfUnreject")&&($("sfUnreject").onclick=()=>save(r,"draft",true));
  box.querySelectorAll(".sopSeg button").forEach(b=>b.onclick=()=>{W.view=b.dataset.v;box.querySelectorAll(".sopSeg button").forEach(x=>x.classList.toggle("on",x===b));loadSource(r);});
  if(r.source_page)loadSource(r);
}
/* re-render the form with the kind/metric just picked, keeping what was typed */
function keepEdits(r,newConds){
  if(!W.origRule||W.origRule.id!==r.id)W.origRule=JSON.parse(JSON.stringify(r));
  const e=collect(r,true);
  const keep={...r,...e};
  if(newConds)keep.conditions=newConds;
  delete keep._edit;
  const idx=W.rs.rules.findIndex(x=>x.id===r.id);
  W.rs.rules[idx]=keep;
  W.dirty=true;renderEditor();
}
function num(id){const el=$(id);if(!el)return undefined;const v=el.value.trim();return v===""?null:Number(v);}
function collect(r,soft){
  const body={title:$("sfTitle").value.trim()||"Untitled rule",kind:$("sfKind").value,severity:$("sfSeverity").value,
    phases:[...document.querySelectorAll("#sfPhases button.on")].map(b=>b.dataset.p),
    reviewer_note:$("sfNote").value.trim()||null};
  if(body.kind!=="unobservable"){
    body.metric=$("sfMetric")?$("sfMetric").value:r.metric;
    for(const [k,id] of [["target","sfTarget"],["tol_hi","sfTolHi"],["tol_lo","sfTolLo"],["min_value","sfMin"],["max_value","sfMax"],["value","sfValue"]]){
      const v=num(id);body[k]=v===undefined?null:v;}
    body.op=$("sfOp")?$("sfOp").value:null;
    body.gate_phase=$("sfGate")?($("sfGate").value||null):null;
    body.capture_at=$("sfCapture")?$("sfCapture").value:null;
    body.conditions=[...document.querySelectorAll("#sfConds .sopCond")].map(el=>({
      metric:el.querySelector(".cM").value,op:el.querySelector(".cO").value,value:Number(el.querySelector(".cV").value||0)}));
  }else{body.metric=null;body.conditions=[];}
  if($("sfQuote")){body.source_quote=$("sfQuote").value.trim()||null;const p=num("sfPage");body.source_page=p||null;}
  if(soft){r._edit=body;return body;}
  return body;
}
async function save(r,status,statusOnly){
  const body=statusOnly?{}:collect(r);
  if(status)body.review_status=status;
  try{
    const out=await api("PATCH","/rules/"+r.id,body);
    const i=W.rs.rules.findIndex(x=>x.id===r.id);W.rs.rules[i]=out;W.dirty=false;W.origRule=null;
    // move on to the next rule that still needs review
    if(status==="approved"||status==="rejected"){
      const next=visible().find(x=>x.review_status==="draft"&&x.id!==r.id);
      if(next)W.selId=next.id;
    }
    renderStage2();
  }catch(e){toast(status==="approved"?"Can't approve yet":"Could not save",e.message);}
}
async function approveClean(){
  const ids=rules().filter(r=>r.review_status==="draft"&&!(r.issues||[]).length).map(r=>r.id);
  if(!ids.length){toast("Nothing to bulk-approve","Every rule left to review has a warning — check those one by one.");return;}
  confirmDialog("Approve "+ids.length+" rules?","These rules have no warnings: their quote matches the SOP word for word and their numbers appear in it. You are still responsible for them.",[
    {label:"Cancel",fn:hideConfirm},{label:"Approve "+ids.length,cls:"primary",fn:async()=>{hideConfirm();
      try{const r=await api("POST",`/rulesets/${W.rsId}/review`,{ids,status:"approved"});
        W.rs=await api("GET","/rulesets/"+W.rsId);renderStage2();
        if(r.skipped.length)toast("Some rules skipped",r.skipped.map(s=>s.reason).join("\n"));
      }catch(e){toast("Could not approve",e.message);}}}]);
}
async function addRule(){
  try{
    const out=await api("POST",`/rulesets/${W.rsId}/rules`,{kind:"limit",title:"New rule",metric:"ias",severity:"caution"});
    W.rs.rules.push(out);W.filter="draft";W.selId=out.id;renderStage2();
    setTimeout(()=>{const t=$("sfTitle");t&&(t.focus(),t.select());},50);
  }catch(e){toast("Could not add rule",e.message);}
}
async function verify(){
  const n=rules().filter(r=>r.review_status==="approved").length;
  confirmDialog("Verify these rules?",`${n} approved rules will be locked as version ${W.rs.version} of this SOP. You can bind them to an aircraft next.`,[
    {label:"Cancel",fn:hideConfirm},{label:"Verify",cls:"primary",fn:async()=>{hideConfirm();
      try{await api("POST",`/rulesets/${W.rsId}/verify`);W.rs=await api("GET","/rulesets/"+W.rsId);renderStage3();}
      catch(e){toast("Could not verify",e.message);}}}]);
}
async function revise(){
  try{const r=await api("POST",`/rulesets/${W.rsId}/revise`);openRuleset(r.ruleset_id);}
  catch(e){toast("Could not revise",e.message);}
}
function discard(){
  confirmDialog("Delete this draft?","The drafted rules and your review decisions for this version will be deleted. The uploaded PDF is kept.",[
    {label:"Cancel",fn:hideConfirm},{label:"Delete",cls:"danger",fn:async()=>{hideConfirm();
      try{await api("DELETE","/rulesets/"+W.rsId);W.dirty=false;close();}catch(e){toast("Could not delete",e.message);}}}]);
}
async function loadSource(r){
  const box=$("sopSrc");if(!box)return;
  const doc=W.rs.document_id,p=r.source_page;
  if(W.view==="image"){
    box.innerHTML=`<div class="sopPage"><img alt="SOP page ${p}" src="/sop/documents/${doc}/pages/${p}/image?q=${encodeURIComponent((r.source_quote||"").slice(0,200))}"></div>`;
    return;
  }
  try{
    const pg=await api("GET",`/documents/${doc}/pages/${p}`);
    if(W.selId!==r.id)return;
    let html=esc(pg.text);
    const q=(r.source_quote||"").trim();
    if(q){
      // highlight by a whitespace-tolerant match of the quote's start
      const words=q.split(/\s+/).slice(0,10).map(w=>esc(w).replace(/[.*+?^${}()|[\]\\]/g,"\\$&"));
      try{
        const rx=new RegExp(words.join("\\s+")+"[^]*?(?:\\.|$)","i");
        html=html.replace(rx,m=>`<mark>${m}</mark>`);
      }catch(_){}
    }
    box.innerHTML=(pg.source==="ocr"?`<div class="sopWarn" style="margin-bottom:8px">Read with OCR${pg.ocr_confidence?` (${Math.round(pg.ocr_confidence)}% confidence)`:""} — compare with the page image.</div>`:"")
      +`<div class="sopPageText">${html}</div>`;
    const m=box.querySelector("mark");m&&m.scrollIntoView({block:"center"});
  }catch(e){box.innerHTML=`<div class="sopErr">${esc(e.message)}</div>`;}
}
function onKey(e){
  if(W.stage!==2||frozen())return;
  if(/INPUT|TEXTAREA|SELECT/.test((e.target&&e.target.tagName)||""))return;
  if($("confirmOverlay").style.display==="flex")return;
  const vis=visible();const i=vis.findIndex(r=>r.id===W.selId);
  const k=e.key.toLowerCase();
  if(k==="j"||e.key==="ArrowDown"){e.preventDefault();guard(()=>vis[i+1]&&select(vis[i+1].id));}
  else if(k==="k"||e.key==="ArrowUp"){e.preventDefault();guard(()=>vis[i-1]&&select(vis[i-1].id));}
  else if(k==="a"&&$("sfApprove")){e.preventDefault();$("sfApprove").click();}
  else if(k==="r"&&$("sfReject")){e.preventDefault();$("sfReject").click();}
}

/* ---------- stage 3: bind to aircraft ---------- */
async function renderStage3(){
  W.stage=3;head();
  const rs=W.rs;
  const approved=rules().filter(r=>r.review_status==="approved");
  const byKind=k=>approved.filter(r=>r.kind===k).length;
  let binds=[];try{binds=await api("GET","/bindings");}catch(_){}
  const cur=Object.fromEntries(binds.map(b=>[b.aircraft_profile,b]));
  if(!W.acSel)W.acSel=(rs.bound_to&&rs.bound_to[0])||(W.meta.aircraft[0]&&W.meta.aircraft[0].id);
  $("sopBody").innerHTML=`<div class="sopS3">
    <div class="sopSummary">
      <div class="sopStat"><b>${approved.length}</b><span>verified rules</span></div>
      <div class="sopStat"><b>${byKind("band")+byKind("limit")}</b><span>targets &amp; limits</span></div>
      <div class="sopStat"><b>${byKind("event")+byKind("capture")}</b><span>procedure checks</span></div>
      <div class="sopStat"><b>${byKind("unobservable")}</b><span>for you to assess</span></div>
    </div>
    <div><h3 style="font-size:.95rem;margin-bottom:10px">Which aircraft does this SOP apply to?</h3>
      <div class="sopAircraft">${W.meta.aircraft.map(a=>{
        const b=cur[a.id];const vs=a.v_speeds||{};
        return `<button class="sopAc ${W.acSel===a.id?"sel":""}" data-ac="${a.id}"><b>${esc(a.name)}</b>
          <small>${vs.vr?`Vr ${vs.vr} · Vy ${vs.vy} · Vne ${vs.vne} kt`:""}${a.flaps?` · flaps ${a.flaps.join("/")}°`:""}</small>
          <small>${b?(b.ruleset_id===rs.id?'<span style="color:var(--good)">● This SOP is active</span>':`Currently: ${esc(b.filename)} v${b.version} — will be replaced`):"No SOP bound yet"}</small></button>`;}).join("")}</div></div>
    <div id="sopAcCheck"></div>
  </div>`;
  $("sopBody").querySelectorAll(".sopAc").forEach(b=>b.onclick=()=>{W.acSel=b.dataset.ac;renderStage3();});
  const active=cur[W.acSel]&&cur[W.acSel].ruleset_id===rs.id;
  foot(`<span class="grow">Only verified, approved rules are ever loaded in a lesson.</span>
    <button class="sopBtn" id="sopBack">← Rules</button>
    ${active?'<button class="sopBtn bad" id="sopUnbind">Unbind</button><button class="sopBtn primary" id="sopFinish">Done</button>'
      :`<button class="sopBtn primary" id="sopBind" ${W.acSel?"":"disabled"}>Bind to ${esc(acName(W.acSel))}</button>`}`);
  $("sopBack").onclick=()=>renderStage2();
  if(active){$("sopFinish").onclick=close;$("sopUnbind").onclick=unbind;}
  else $("sopBind").onclick=bind;
  if(W.acSel){
    try{
      const c=await api("GET",`/rulesets/${rs.id}/aircraft-check/${W.acSel}`);
      if(W.stage!==3)return;
      $("sopAcCheck").innerHTML=c.warnings.length
        ?`<p class="sopNote" style="margin-bottom:8px"><b>Check against ${esc(acName(W.acSel))}'s profile</b></p>`+c.warnings.map(w=>`<div class="sopWarn" style="margin-bottom:6px">${esc(w)}</div>`).join("")
        :`<p class="sopNote">✓ No conflicts with ${esc(acName(W.acSel))}'s speeds, flap detents or ceiling.</p>`;
    }catch(e){$("sopAcCheck").innerHTML=`<div class="sopErr">${esc(e.message)}</div>`;}
  }
}
async function bind(){
  try{
    await api("POST",`/rulesets/${W.rs.id}/bind`,{aircraft_profile:W.acSel});
    W.rs=await api("GET","/rulesets/"+W.rs.id);head();
    const n=rules().filter(r=>r.review_status==="approved").length;
    $("sopBody").innerHTML=`<div class="sopDone"><div class="tick">✓</div>
      <h3>${esc(acName(W.acSel))} now uses ${esc(W.rs.filename)} v${W.rs.version}</h3>
      <p>${n} verified rules are active for this aircraft. Any SOP previously bound to it has been replaced.</p></div>`;
    foot(`<span class="grow"></span><button class="sopBtn" id="sopBack2">← Back</button><button class="sopBtn primary" id="sopFinish2">Done</button>`);
    $("sopBack2").onclick=()=>renderStage3();$("sopFinish2").onclick=close;
  }catch(e){toast("Could not bind",e.message);}
}
async function unbind(){
  confirmDialog("Unbind this SOP?",`${acName(W.acSel)} will have no SOP rules until another verified SOP is bound.`,[
    {label:"Cancel",fn:hideConfirm},{label:"Unbind",cls:"danger",fn:async()=>{hideConfirm();
      try{await api("DELETE","/bindings/"+encodeURIComponent(W.acSel));W.rs=await api("GET","/rulesets/"+W.rs.id);renderStage3();}
      catch(e){toast("Could not unbind",e.message);}}}]);
}

/* ---------- wiring ---------- */
$("sopClose").onclick=close;
$("sopOverlay").addEventListener("click",e=>{if(e.target===$("sopOverlay"))close();});
$("sopUploadBtn").onclick=startNew;
window.sopWizard={renderTab:async()=>{try{await ensureMeta();}catch(_){}renderTab();},open:startNew,openRuleset,_W:W,_describe:describe};
})();
