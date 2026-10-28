/* Local-only timing canvas. The Python model remains the source of truth. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const COLORS = ["#138b88", "#6574ba", "#b28b4d", "#7e75b3", "#5099aa", "#b17688", "#71966d"];
  const UNITS = {fs:1, ps:1e3, ns:1e6, us:1e9, ms:1e12, s:1e15};
  const SUFFIX = {f:"fs", fs:"fs", p:"ps", ps:"ps", n:"ns", ns:"ns", u:"us", us:"us", "µ":"us", "µs":"us", "μ":"us", "μs":"us", m:"ms", ms:"ms", s:"s"};
  const INPUT_SUFFIX = {fs:"f", ps:"p", ns:"n", us:"u", ms:"m", s:"s"};
  const DENSE_EDGE_GAP_PX = 6, DETAIL_EDGE_GAP_PX = 14;
  let token = new URLSearchParams(location.hash.slice(1)).get("token");
  if (token) { sessionStorage.setItem("timing-token", token); history.replaceState(null, "", location.pathname); }
  else token = sessionStorage.getItem("timing-token");
  let state = null, mode = "select", zoom = 1, width = 900, rowOverride = null;
  let selected = null, selectedSignal = null, selectedRelation = null, relations = null;
  let busy = false, blocked = false, drag = null, relationRequest = 0, vaRequest = 0;
  let pendingDraft = null, toastTimer = null, cursorTime = null, dialogHandler = null;
  let initialPeriod = null, snapTouched = false, serverOnline = true;
  let suppressPointerUntil = 0, suppressDoubleClickUntil = 0;
  const draftKey = "timing-pending-" + location.port;
  const scroll = $("timeline-scroll");

  function escapeHTML(value) { return String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
  function parseTime(value, unit = state?.project.unit || "ns") {
    const match = /^([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-zµμ]*)$/.exec(String(value).trim());
    if (!match || String(value).length > 80) throw new Error("时间格式无效，请输入 1n、200p 或 0.5u。");
    const resolved = match[2] ? SUFFIX[match[2]] : unit;
    if (!resolved) throw new Error("单位请使用小写 f、p、n、u、m、s，也可写 ns、ps。");
    const [mantissa, exponent = "0"] = match[1].replace(/^\+/, "").toLowerCase().split("e");
    const [whole, fraction = ""] = mantissa.split(".");
    const coefficient = BigInt((whole || "0") + fraction);
    if (coefficient === 0n) return 0;
    const power = Number(exponent) - fraction.length + Math.round(Math.log10(UNITS[resolved]));
    if (!Number.isFinite(power) || power > 16 || power < -80) throw new Error("时间超出范围或精度小于 1 fs。");
    let ticks;
    if (power >= 0) ticks = coefficient * 10n ** BigInt(power);
    else { const divisor = 10n ** BigInt(-power); if (coefficient % divisor) throw new Error("时间精度不能小于 1 fs。"); ticks = coefficient / divisor; }
    if (ticks > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("时间超过支持范围（约 9 秒）。");
    return Number(ticks);
  }
  function exactTime(ticks, unit = state?.project.unit || "ns", input = false) {
    const divisor = BigInt(UNITS[unit]), n = BigInt(Math.round(ticks));
    const decimalPlaces = Math.round(Math.log10(UNITS[unit]));
    let text = String(n / divisor);
    const rest = n % divisor;
    if (rest) text += "." + String(rest).padStart(decimalPlaces, "0").replace(/0+$/, "");
    return text + (input ? INPUT_SUFFIX[unit] : " " + unit);
  }
  function shortTime(ticks) {
    const unit = [...Object.keys(UNITS)].reverse().find(u => ticks >= UNITS[u]) || "fs";
    return Number((ticks / UNITS[unit]).toPrecision(5)) + " " + unit;
  }
  function niceStep(raw) {
    if (raw <= 1) return 1;
    const magnitude = 10 ** Math.floor(Math.log10(raw));
    return Math.max(1, Math.round(([1,2,5,10].find(n => n * magnitude >= raw) || 10) * magnitude));
  }
  function defaultSnap(period) { return niceStep(period / 1000); }
  function showToast(message, duration = 4200) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").hidden = true, duration); }
  function showAlert(message) { $("alert-text").textContent = message; $("alert").hidden = false; }
  function saveStatus(text, kind = "") { $("save-status").textContent = text; $("save-status").className = "save-status " + kind; }
  function updateButtons() {
    const unavailable = !state || busy || blocked;
    $("undo").disabled = unavailable || !state.can_undo;
    $("redo").disabled = unavailable || !state.can_redo;
    $("export").disabled = unavailable;
    $("settings").disabled = unavailable;
    $("add-signal").disabled = unavailable || state.project.signals.length >= 64;
    $("edit-edge").disabled = unavailable || !selected;
    $("done").disabled = unavailable;
  }
  function canEdit() {
    if (!state) return false;
    if (blocked) { showToast("请先处理未保存的修改。"); return false; }
    if (busy) { showToast("正在保存，请稍候。"); return false; }
    return true;
  }
  async function api(path, body) {
    if (!token) throw new Error("缺少会话凭据，请使用启动器重新打开窗口。");
    const response = await fetch(path, {method:body === undefined ? "GET" : "POST", headers:{"X-Timing-Token":token, ...(body === undefined ? {} : {"Content-Type":"application/json"})}, body:body === undefined ? undefined : JSON.stringify(body), cache:"no-store"});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || "请求未完成。"); error.status = response.status; error.data = data; throw error; }
    serverOnline = true;
    return data;
  }
  function storeDraft(draft) {
    pendingDraft = draft;
    try { if (draft) localStorage.setItem(draftKey, JSON.stringify(draft)); else localStorage.removeItem(draftKey); } catch (_) { /* In-memory copy still protects an active editing session. */ }
  }
  function conflict(message) {
    blocked = true;
    saveStatus("修改尚未保存", "error");
    $("conflict-message").textContent = message;
    $("conflict-draft").value = JSON.stringify(pendingDraft || {}, null, 2);
    if (!$("conflict-dialog").open) $("conflict-dialog").showModal();
    updateButtons();
  }
  async function edit(operation) {
    if (!canEdit()) return null;
    busy = true;
    storeDraft({revision:state.revision, project:clone(state.project), operation, saved_at:new Date().toISOString()});
    saveStatus("正在保存…", "busy"); updateButtons();
    try {
      const next = await api("/api/edit", {revision:state.revision, operation});
      storeDraft(null); rowOverride = null; applyState(next);
      return next;
    } catch (error) {
      rowOverride = null;
      if (error.status === 409) conflict("工程已在其他位置更新。本次操作没有覆盖新版本，请先保留需要的内容，再读取最新版本。");
      else if (!error.status || error.status >= 500) conflict("保存结果暂时无法确认：" + error.message + "。连接恢复后请读取最新版本核对，不会自动重复执行。");
      else { storeDraft(null); showAlert(error.message); saveStatus("本次修改未保存", "error"); renderTimeline(); }
      return null;
    } finally { busy = false; updateButtons(); }
  }
  function applyState(next, external = false) {
    if (!next?.project || (state && next.revision < state.revision)) return;
    const oldRevision = state?.revision;
    state = next;
    if (initialPeriod !== next.project.period_fs) {
      if (!snapTouched || initialPeriod === null || safeSnap() > next.project.period_fs) $("snap").value = exactTime(defaultSnap(next.project.period_fs), next.project.unit, true);
      initialPeriod = next.project.period_fs;
    }
    if (selectedSignal && !next.project.signals.some(s => s.id === selectedSignal)) selectedSignal = null;
    if (selected) {
      const signal = next.project.signals.find(s => s.id === selected.signalId);
      const edge = signal && getEdges(signal).find(e => e.itemId === selected.itemId);
      selected = edge ? {...edge, signalId:signal.id} : null;
    }
    $("session-name").textContent = next.session_name || "当前协作工程";
    $("module-name").textContent = next.project.module_name;
    $("project-meta").textContent = "周期 " + shortTime(next.project.period_fs) + "   ·   ↑ " + shortTime(next.project.rise_fs) + " / ↓ " + shortTime(next.project.fall_fs) + "   ·   " + next.project.vlow + "–" + next.project.vhigh + " V" + "   ·   " + (next.project.cycles ? "重复 " + next.project.cycles + " 次" : "持续重复");
    $("revision").textContent = "版本 " + next.revision;
    $("signal-count").textContent = next.project.signals.length + " 路信号";
    $("row-height").value = getRowHeight(); $("row-value").value = getRowHeight();
    const filter = $("signal-filter").value;
    $("signal-filter").innerHTML = '<option value="">全部信号</option>' + orderedSignals().map(s => '<option value="' + escapeHTML(s.id) + '">' + escapeHTML(s.name) + '</option>').join("");
    if (next.project.signals.some(s => s.id === filter)) $("signal-filter").value = filter;
    saveStatus("已保存");
    $("footer-status").textContent = "文件保存在本机 · 修改自动保存";
    renderTimeline(); updateButtons(); refreshRelations();
    if ($("va-panel").open) refreshVA();
    if (external && oldRevision !== undefined && oldRevision !== next.revision) showToast("已载入更新后的时序 · 版本 " + next.revision);
  }
  function orderedSignals() {
    if (!state) return [];
    const signals = state.project.signals, order = state.project.view?.signal_order || [];
    const byId = new Map(signals.map(s => [s.id,s]));
    return [...order.filter(id => byId.has(id)).map(id => byId.get(id)), ...signals.filter(s => !order.includes(s.id))];
  }
  function getRowHeight() { return clamp(Number(rowOverride || state?.project.view?.row_height || 64), 36, 128); }
  function nameWidth() { return parseInt(getComputedStyle(document.documentElement).getPropertyValue("--name-width"), 10); }
  function computeWidth() { width = Math.max(360, (scroll.clientWidth - nameWidth() - 22) * zoom); }
  function xAt(time) { return 14 + time / state.project.period_fs * (width - 28); }
  function timeAt(event, svg, snap = true) {
    const box = svg.getBoundingClientRect();
    let time = clamp((event.clientX - box.left - 14) / (width - 28), 0, 1) * state.project.period_fs;
    const step = snap && !event.altKey ? safeSnap() : 1;
    time = Math.round(time / step) * step;
    return Math.round(clamp(time, 0, state.project.period_fs));
  }
  function safeSnap() { try { return Math.max(1, parseTime($("snap").value)); } catch (_) { return defaultSnap(state?.project.period_fs || 1e8); } }
  function getSegments(signal) {
    let time = 0;
    return signal.items.map(item => { const start = time; time += item.duration_fs; return {itemId:item.id, level:item.level, start, end:time}; });
  }
  function getEdges(signal) {
    const segments = getSegments(signal), out = [];
    for (let i=0; i<segments.length-1; i++) if (segments[i].level !== segments[i+1].level) out.push({itemId:segments[i].itemId, time:segments[i].end, direction:segments[i+1].level ? "rise" : "fall"});
    return out;
  }
  function denseRegions(signal) {
    // Group only consecutive unresolved intervals, never across a long hold.
    const edges=getEdges(signal), regions=[];
    for(let i=0;i<edges.length-1;i++) {
      if(xAt(edges[i+1].time)-xAt(edges[i].time)>=DENSE_EDGE_GAP_PX) continue;
      const first=i;
      while(i+1<edges.length && xAt(edges[i+1].time)-xAt(edges[i].time)<DENSE_EDGE_GAP_PX) i++;
      regions.push({start:edges[first].time,end:edges[i].time,edges:edges.slice(first,i+1)});
    }
    return regions;
  }
  function denseAt(signal,time) {
    const x=xAt(time);
    return denseRegions(signal).find(region=>{
      const left=xAt(region.start),right=xAt(region.end),padding=Math.max(0,(6-(right-left))/2);
      return x>=left-padding && x<=right+padding;
    });
  }
  function updateZoomStatus() {
    if(!state) return;
    $("zoom-level").textContent=Number(zoom.toFixed(1))+"×";
    const visibleWidth=Math.max(1,scroll.clientWidth-nameWidth());
    const left=Math.round(clamp((scroll.scrollLeft-14)/(width-28),0,1)*state.project.period_fs);
    const right=Math.round(clamp((scroll.scrollLeft+visibleWidth-14)/(width-28),0,1)*state.project.period_fs);
    $("zoom-level").title="当前可见 "+shortTime(left)+" — "+shortTime(right);
  }
  function zoomDense(region,time) {
    const edges=region.edges;
    const nearest=edges.reduce((best,edge,index)=>Math.abs(edge.time-time)<Math.abs(edges[best].time-time)?index:best,0);
    const gaps=[];
    if(nearest>0) gaps.push(xAt(edges[nearest].time)-xAt(edges[nearest-1].time));
    if(nearest+1<edges.length) gaps.push(xAt(edges[nearest+1].time)-xAt(edges[nearest].time));
    const minimumGap=Math.max(Number.EPSILON,Math.min(...gaps));
    const target=clamp(Math.max(zoom*2,zoom*DETAIL_EDGE_GAP_PX/minimumGap),1,64);
    suppressPointerUntil=performance.now()+500;
    suppressDoubleClickUntil=performance.now()+1000;
    selected=null;
    if(target<=zoom) { repaintWaves(); showToast("已到当前最大放大倍率。这里仍用密集概览显示，不会直接拖动无法分辨的边沿。"); return; }
    zoom=target; renderTimeline();
    scroll.scrollLeft=Math.max(0,xAt(time)-(scroll.clientWidth-nameWidth())/2);
    updateZoomStatus();
    showToast("已放大点击位置。斜纹区域表示尚未分辨的密集脉冲，工程时序没有改变。");
  }
  function ticks() {
    const step = niceStep(state.project.period_fs / ((width - 28) / 90));
    const out = [];
    for (let n=0; n<=state.project.period_fs; n+=step) out.push(n);
    if (state.project.period_fs - (out.at(-1) || 0) > step * .45) out.push(state.project.period_fs);
    return out;
  }
  function guideTimes() {
    if (!$("show-guides").checked || !selectedRelation) return [];
    if (selectedRelation.type === "simultaneous") return [selectedRelation.value.time_fs];
    return [selectedRelation.value.first.time_fs, selectedRelation.value.second.time_fs];
  }
  function svgMarkup(signal, color) {
    const h = getRowHeight(), top = Math.max(10, h * .24), bottom = h - Math.max(10, h * .24), segments = getSegments(signal);
    const dense=denseRegions(signal), signalIndex=state.project.signals.findIndex(s=>s.id===signal.id);
    const levelY = level => level ? top : bottom;
    let path = "M " + xAt(0) + " " + levelY(segments[0].level);
    for (let i=0; i<segments.length; i++) {
      const seg = segments[i];
      if (i) path += " L " + xAt(seg.start) + " " + levelY(seg.level);
      path += " L " + xAt(i === segments.length-1 ? state.project.period_fs : seg.end) + " " + levelY(seg.level);
    }
    let content = ticks().map(t => '<line class="grid-line" x1="'+xAt(t)+'" x2="'+xAt(t)+'" y1="0" y2="'+h+'"/>').join("");
    if(dense.length) {
      let clip="",left=0;
      for(const region of dense) { const x=xAt(region.start); if(x>left) clip+='<rect x="'+left+'" y="0" width="'+(x-left)+'" height="'+h+'"/>'; left=xAt(region.end); }
      if(left<width) clip+='<rect x="'+left+'" y="0" width="'+(width-left)+'" height="'+h+'"/>';
      content+='<defs><clipPath id="wave-visible-'+signalIndex+'">'+clip+'</clipPath><pattern id="dense-hatch-'+signalIndex+'" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M -2 2 L 2 -2 M 0 8 L 8 0 M 6 10 L 10 6" stroke="'+color+'" stroke-opacity="0.24" stroke-width="1"/></pattern></defs>';
    }
    content += '<path class="wave-path" stroke="'+color+'" d="'+path+'"'+(dense.length?' clip-path="url(#wave-visible-'+signalIndex+')"':'')+'/>';
    if (h >= 55) for (const seg of segments) { const end = seg === segments.at(-1) ? state.project.period_fs : seg.end; if (xAt(end) - xAt(seg.start) > 65) content += '<text class="segment-label" x="'+((xAt(seg.start)+xAt(end))/2)+'" y="'+(seg.level ? bottom+13 : top-3)+'">'+shortTime(end-seg.start)+'</text>'; }
    for(const region of dense) {
      const exactX=xAt(region.start),exactWidth=xAt(region.end)-exactX;
      // A subpixel overview is a small clickable marker, not a stretched pulse.
      const w=Math.max(6,exactWidth),x=exactX-(w-exactWidth)/2;
      const label=w>185?"密集脉冲 · 点击放大":w>65?"密集脉冲":"";
      content+='<g class="dense-region" data-start-fs="'+region.start+'" data-end-fs="'+region.end+'" data-edge-count="'+region.edges.length+'" role="img" aria-label="密集脉冲，点击放大；包含 '+region.edges.length+' 个实际边沿"><title>密集脉冲 · 点击放大（'+region.edges.length+' 个边沿，非持续高电平）</title><rect x="'+x+'" y="'+(top-2)+'" width="'+w+'" height="'+(bottom-top+4)+'" fill="'+color+'" fill-opacity="0.035"/><rect x="'+x+'" y="'+(top-2)+'" width="'+w+'" height="'+(bottom-top+4)+'" fill="url(#dense-hatch-'+signalIndex+')" stroke="'+color+'" stroke-opacity="0.4" stroke-width="1"/>';
      if(label) content+='<text x="'+(x+w/2)+'" y="'+(h/2+3.5)+'" text-anchor="middle" fill="'+color+'" class="dense-label">'+label+'</text>';
      content+='</g>';
    }
    for (const t of guideTimes()) content += '<line class="guide-line" x1="'+xAt(t)+'" x2="'+xAt(t)+'" y1="0" y2="'+h+'"/>';
    if (selected?.signalId === signal.id) { const x = xAt(drag?.type === "boundary" ? drag.current : selected.time); content += '<line class="edge-selected" x1="'+x+'" x2="'+x+'" y1="3" y2="'+(h-3)+'"/><circle cx="'+x+'" cy="'+(h/2)+'" r="3.5" fill="#fff" stroke="#c28d3f" stroke-width="1.5"/>'; }
    if (drag?.type === "paint" && drag.signalId === signal.id) content += '<rect x="'+Math.min(xAt(drag.start),xAt(drag.current))+'" y="2" width="'+Math.abs(xAt(drag.start)-xAt(drag.current))+'" height="'+(h-4)+'" fill="'+color+'" fill-opacity="0.10" stroke="'+color+'" stroke-dasharray="3 3"/>';
    return content;
  }
  function renderTimeline() {
    if (!state || drag) return;
    const xScroll = scroll.scrollLeft, yScroll = scroll.scrollTop;
    computeWidth();
    const h = getRowHeight(), signals = orderedSignals();
    const ruler = ticks().map(t => '<line x1="'+xAt(t)+'" x2="'+xAt(t)+'" y1="26" y2="38" stroke="#ccd7df"/><text x="'+xAt(t)+'" y="19" text-anchor="'+(t===0 ? 'start' : t===state.project.period_fs ? 'end' : 'middle')+'" fill="#7e8d9d" font-size="10">'+shortTime(t)+'</text>').join("");
    $("timeline-content").style.width = (nameWidth()+width) + "px";
    $("timeline-content").innerHTML = '<div class="ruler-row"><div class="ruler-name">信号 / 显示顺序</div><svg class="ruler" width="'+width+'" height="38">'+ruler+'</svg></div>' + signals.map((signal,index) => {
      const color = COLORS[state.project.signals.findIndex(s => s.id === signal.id) % COLORS.length];
      return '<div class="track-row'+(selectedSignal===signal.id ? ' selected':'')+'" data-signal="'+escapeHTML(signal.id)+'" style="height:'+h+'px"><div class="track-label"><span class="signal-dot" style="background:'+color+'"></span><div class="signal-text" title="双击重命名"><b>'+escapeHTML(signal.name)+'</b>'+(h>=55 ? '<small>'+getEdges(signal).length+' 个边沿</small>':'')+'</div><div class="reorder"><button data-order="-1" title="上移" aria-label="'+escapeHTML(signal.name)+' 上移" '+(index===0?'disabled':'')+'>▲</button><button data-order="1" title="下移" aria-label="'+escapeHTML(signal.name)+' 下移" '+(index===signals.length-1?'disabled':'')+'>▼</button></div></div><svg class="wave-svg '+(mode==='select'?'select-mode':'paint-mode')+'" width="'+width+'" height="'+h+'" data-signal="'+escapeHTML(signal.id)+'" aria-label="'+escapeHTML(signal.name)+' 时序">'+svgMarkup(signal,color)+'</svg></div>';
    }).join("");
    scroll.scrollLeft = xScroll; scroll.scrollTop = yScroll;
    $("timeline-hint").textContent=signals.some(signal=>denseRegions(signal).length)?"斜纹区域为密集脉冲概览 · 点击放大查看真实边沿":"一个大周期 · 边沿位置表示跳变开始时刻";
    updateZoomStatus();
    for (const row of $("timeline-content").querySelectorAll(".track-row")) {
      const signalId = row.dataset.signal;
      row.querySelector(".signal-text").addEventListener("click", () => { selectedSignal=signalId; repaintWaves(); });
      row.querySelector(".signal-text").addEventListener("dblclick", () => renameSignal(signalId));
      for (const button of row.querySelectorAll("[data-order]")) button.addEventListener("click", () => reorder(signalId, Number(button.dataset.order)));
      const svg = row.querySelector("svg");
      svg.addEventListener("pointerdown", pointerDown);
      svg.addEventListener("pointermove", pointerMove);
      svg.addEventListener("pointerup", pointerUp);
      svg.addEventListener("pointercancel", cancelDrag);
      svg.addEventListener("dblclick", doubleClick);
      svg.addEventListener("pointerleave", () => { if (!drag) $("cursor-time").textContent = "—"; });
    }
    updateSelection();
  }
  function repaintWaves() {
    // Keep pointer targets alive between clicks so native dblclick still fires.
    if (!state || drag) return;
    for (const row of $("timeline-content").querySelectorAll(".track-row")) {
      const signal=state.project.signals.find(s=>s.id===row.dataset.signal);
      if (!signal) continue;
      row.classList.toggle("selected",selectedSignal===signal.id);
      row.querySelector(".wave-svg").innerHTML=svgMarkup(signal,COLORS[state.project.signals.indexOf(signal)%COLORS.length]);
    }
    updateSelection();
  }
  function updateSelection() {
    if (selected) {
      const signal = state.project.signals.find(s => s.id === selected.signalId);
      $("selection-info").textContent = signal.name + " · " + (selected.direction === "rise" ? "上升沿" : "下降沿") + " · " + exactTime(drag?.type === "boundary" ? drag.current : selected.time);
    } else $("selection-info").textContent = mode === "select" ? "拖动边沿调整时间；双击区间翻转电平。" : "在一条信号上拖出区间，松开鼠标后保存为" + (mode === "high" ? "高" : "低") + "电平。";
    updateButtons();
  }
  function paintPreview(signal, from, to, level) {
    const a = Math.min(from,to), b = Math.max(from,to), pieces = [];
    const segments = getSegments(signal);
    if (segments.at(-1).end < state.project.period_fs) segments.at(-1).end = state.project.period_fs;
    for (const seg of segments) {
      const cuts = [...new Set([seg.start,seg.end,clamp(a,seg.start,seg.end),clamp(b,seg.start,seg.end)])].sort((x,y)=>x-y);
      for (let i=0;i<cuts.length-1;i++) if (cuts[i+1]>cuts[i]) pieces.push({id:seg.itemId+"_"+i,type:"segment",level:cuts[i]>=a && cuts[i+1]<=b ? level : seg.level,duration_fs:cuts[i+1]-cuts[i]});
    }
    return {...signal,items:pieces};
  }
  function redrawDrag() {
    if (!drag) return;
    const svg = drag.svg, original = drag.signal;
    let preview = original;
    if (drag.type === "boundary" && drag.current !== drag.oldTime) {
      const segs = getSegments(original), i = segs.findIndex(s=>s.itemId===drag.itemId);
      preview = paintPreview(original,drag.oldTime,drag.current,drag.current>drag.oldTime ? segs[i].level : segs[i+1].level);
    } else if (drag.type === "paint") preview = paintPreview(original,drag.start,drag.current,drag.level);
    const color = COLORS[state.project.signals.findIndex(s=>s.id===drag.signalId)%COLORS.length];
    svg.innerHTML = svgMarkup(preview,color); updateSelection();
  }
  function pointerDown(event) {
    if (event.button!==0 || !canEdit()) return;
    if(performance.now()<suppressPointerUntil) { event.preventDefault(); return; }
    const svg = event.currentTarget, signal = state.project.signals.find(s=>s.id===svg.dataset.signal);
    const raw = timeAt(event,svg,false), x = event.clientX-svg.getBoundingClientRect().left;
    selectedSignal=signal.id;
    if (mode === "select") {
      const dense=denseAt(signal,raw);
      if(dense) { event.preventDefault(); zoomDense(dense,clamp(raw,dense.start,dense.end)); return; }
      const edge = getEdges(signal).reduce((best,e)=>Math.abs(xAt(e.time)-x)<Math.abs(xAt(best?.time??Infinity)-x)?e:best,null);
      if (!edge || Math.abs(xAt(edge.time)-x)>10) { selected=null; repaintWaves(); return; }
      const unresolved=denseRegions(signal).find(region=>edge.time>=region.start && edge.time<=region.end);
      if(unresolved) { event.preventDefault(); zoomDense(unresolved,edge.time); return; }
      selected={...edge,signalId:signal.id};
      drag={type:"boundary",svg,signal:clone(signal),signalId:signal.id,itemId:edge.itemId,oldTime:edge.time,current:edge.time,startX:event.clientX,moved:false,pointerId:event.pointerId};
    } else drag={type:"paint",svg,signal:clone(signal),signalId:signal.id,start:timeAt(event,svg),current:timeAt(event,svg),level:mode==="high"?1:0,startX:event.clientX,moved:false,pointerId:event.pointerId};
    svg.setPointerCapture(event.pointerId); event.preventDefault(); redrawDrag();
    $("cursor-time").textContent=exactTime(raw);
  }
  function pointerMove(event) {
    if (!state) return;
    const svg=event.currentTarget;
    if (drag && drag.svg===svg) {
      const dx=event.clientX-drag.startX;
      drag.moved ||= Math.abs(dx)>2;
      if(drag.type==="boundary") {
        const step=event.altKey?1:safeSnap();
        const delta=Math.round((dx/(width-28)*state.project.period_fs)/step)*step;
        drag.current=drag.moved?Math.round(clamp(drag.oldTime+delta,0,state.project.period_fs)):drag.oldTime;
      } else drag.current=timeAt(event,svg);
      $("cursor-time").textContent=exactTime(drag.current); redrawDrag();
    } else {
      cursorTime=timeAt(event,svg,false); $("cursor-time").textContent=exactTime(cursorTime);
      if (mode==="select") { const signal=state.project.signals.find(s=>s.id===svg.dataset.signal), x=event.clientX-svg.getBoundingClientRect().left; svg.style.cursor=denseAt(signal,cursorTime)?"zoom-in":getEdges(signal).some(e=>Math.abs(xAt(e.time)-x)<10)?"ew-resize":"default"; }
    }
  }
  async function pointerUp(event) {
    if (!drag || drag.svg!==event.currentTarget) return;
    const finished=drag; drag=null;
    if (finished.svg.hasPointerCapture(event.pointerId)) finished.svg.releasePointerCapture(event.pointerId);
    if (!finished.moved) { repaintWaves(); return; }
    if (finished.type==="boundary" && finished.current!==finished.oldTime) await edit({op:"boundary",signal_id:finished.signalId,item_id:finished.itemId,new_end_fs:finished.current});
    else if (finished.type==="paint" && finished.current!==finished.start) await edit({op:"paint",signal_id:finished.signalId,start_fs:Math.min(finished.start,finished.current),end_fs:Math.max(finished.start,finished.current),level:finished.level});
    renderTimeline();
  }
  function cancelDrag() { drag=null; renderTimeline(); }
  function doubleClick(event) {
    if (mode!=="select" || drag || !canEdit() || performance.now()<suppressDoubleClickUntil) return;
    const svg=event.currentTarget, signal=state.project.signals.find(s=>s.id===svg.dataset.signal), t=timeAt(event,svg,false), x=event.clientX-svg.getBoundingClientRect().left;
    if(denseAt(signal,t)) return;
    if (getEdges(signal).some(e=>Math.abs(xAt(e.time)-x)<10)) { openEdgeDialog(); return; }
    const item=getSegments(signal).find(s=>t>=s.start&&t<s.end)||getSegments(signal).at(-1);
    edit({op:"toggle",signal_id:signal.id,item_id:item.itemId});
  }
  async function reorder(signalId,delta) { if (!canEdit()) return; const order=orderedSignals().map(s=>s.id), index=order.indexOf(signalId), next=index+delta; if (next<0||next>=order.length) return; [order[index],order[next]]=[order[next],order[index]]; await edit({op:"view",signal_order:order}); }
  function field(label,name,value,options={}) {
    return '<label class="dialog-field">'+escapeHTML(label)+(options.select ? '<select name="'+name+'">'+options.select.map(option=>'<option value="'+escapeHTML(option)+'" '+(option===value?'selected':'')+'>'+escapeHTML(option)+'</option>').join("")+'</select>' : '<input name="'+name+'" value="'+escapeHTML(value)+'" '+(options.type?'type="'+options.type+'"':'type="text"')+' spellcheck="false" '+(options.min!==undefined?'min="'+options.min+'"':'')+'>')+(options.help?'<small>'+escapeHTML(options.help)+'</small>':'')+'</label>';
  }
  function openDialog(title,fields,handler) {
    if (!canEdit()) return;
    $("dialog-title").textContent=title; $("dialog-fields").innerHTML=fields; $("dialog-error").textContent="";
    $("dialog-submit").disabled=false; dialogHandler=handler; $("edit-dialog").showModal();
    const input=$("dialog-fields").querySelector("input"); if (input) { input.focus(); input.select(); }
  }
  function openEdgeDialog() {
    if (!selected || !canEdit()) return;
    const edge={...selected};
    openDialog("精确修改边沿",field("跳变开始时刻","time",exactTime(edge.time,state.project.unit,true),{help:"例如 1n、200p；精确输入不受鼠标吸附步长限制。"}),async values=>{
      const time=parseTime(values.time); if (time>state.project.period_fs) throw new Error("边沿不能超出大周期。");
      return await edit({op:"boundary",signal_id:edge.signalId,item_id:edge.itemId,new_end_fs:time});
    });
  }
  function renameSignal(signalId) {
    const signal=state.project.signals.find(s=>s.id===signalId);
    openDialog("重命名信号",field("信号名称","name",signal.name,{help:"使用英文、数字和下划线，首字符不能是数字。"}),values=>edit({op:"rename",signal_id:signalId,name:values.name.trim()}));
  }
  function settingsDialog() {
    if (!canEdit()) return; const p=state.project;
    const fields=field("Verilog-A 模块名","module_name",p.module_name)+'<div class="dialog-grid">'+field("大周期","period",exactTime(p.period_fs,p.unit,true))+field("大周期重复次数","cycles",p.cycles,{type:"number",min:0,help:"0 表示持续重复；有限次结束保持末态。"})+field("上升时间","rise",exactTime(p.rise_fs,p.unit,true))+field("下降时间","fall",exactTime(p.fall_fs,p.unit,true))+field("低电平 / V","vlow",p.vlow)+field("高电平 / V","vhigh",p.vhigh)+field("默认显示单位","unit",p.unit,{select:Object.keys(UNITS)})+'</div>';
    openDialog("工程设置",fields,values=>edit({op:"settings",values:{...values,cycles:Number(values.cycles),vlow:Number(values.vlow),vhigh:Number(values.vhigh)}}));
  }
  async function refreshRelations() {
    if (!state || drag || blocked) return;
    let threshold;
    try { parseTime($("threshold").value); threshold=$("threshold").value; } catch(error) { $("relation-summary").textContent=error.message; return; }
    const request=++relationRequest, revision=state.revision;
    const params=new URLSearchParams({threshold,kind:$("kind-filter").value});
    if ($("signal-filter").value) params.set("signal_id",$("signal-filter").value);
    try {
      const data=await api("/api/relations?"+params);
      if (request!==relationRequest || state.revision!==revision || (data.revision!==undefined && data.revision!==revision)) return;
      relations=data; selectedRelation=null;
      $("relation-summary").textContent="已显示 "+data.simultaneous.length+" 组同时 · "+data.near.length+" 对接近"+(data.truncated?" · 结果已截断，请缩小范围":"");
      const entries=[...data.simultaneous.map(value=>({type:"simultaneous",value})),...data.near.map(value=>({type:"near",value}))];
      $("relation-list").innerHTML=entries.length ? entries.map((entry,index)=> {
        const simultaneous=entry.type==="simultaneous", r=entry.value;
        const direction=e=>e.signal_name+(e.direction==="rise"?" ↑":" ↓");
        const names=simultaneous?r.edges.map(direction).join(" · "):direction(r.first)+" → "+direction(r.second);
        const desc=simultaneous?shortTime(r.time_fs):"Δ "+shortTime(r.delta_fs);
        const note=simultaneous?(r.edges.some(e=>e.boundary)?"周期衔接边沿":"相同的跳变开始时刻"):(shortTime(r.first.time_fs)+" → "+shortTime(r.second.time_fs)+(r.cross_cycle?"（下一周期）":""));
        return '<button class="relation-item '+entry.type+'" data-relation="'+index+'"><span class="relation-tag"><span>'+(simultaneous?'同时发生':'距离接近')+'</span><span>'+escapeHTML(desc)+'</span></span><span class="relation-names">'+escapeHTML(names)+'</span><small>'+escapeHTML(note)+'</small></button>';
      }).join("") : '<div class="relation-empty">这个范围内没有匹配的关系。<br>可调整接近阈值或信号范围。</div>';
      for (const button of $("relation-list").querySelectorAll("[data-relation]")) button.addEventListener("click",()=>{
        selectedRelation=entries[Number(button.dataset.relation)];
        for (const item of $("relation-list").querySelectorAll(".active")) item.classList.remove("active"); button.classList.add("active"); repaintWaves();
      });
      repaintWaves();
    } catch(error) { if (request===relationRequest) $("relation-summary").textContent="关系分析暂不可用："+error.message; }
  }
  async function refreshVA() {
    if (!state) return;
    const request=++vaRequest, revision=state.revision;
    try { const data=await api("/api/va"); if (request!==vaRequest || state.revision!==revision || data.revision!==revision) return; $("va-code").textContent=data.code; $("va-info").textContent="版本 "+data.revision+" · "+data.filename; } catch(error) { $("va-code").textContent=error.message; }
  }
  function setZoom(value,anchor=null) {
    if (!state || drag) return;
    const oldWidth=width, oldLeft=scroll.scrollLeft, center=anchor ?? (scroll.clientWidth-nameWidth())/2;
    zoom=clamp(value,1,64); renderTimeline();
    scroll.scrollLeft=(oldLeft+center)*(width/oldWidth)-center;
    updateZoomStatus();
  }
  async function poll() {
    if (!state || busy || drag || blocked || $("edit-dialog").open || ["INPUT","SELECT","TEXTAREA"].includes(document.activeElement.tagName)) return;
    const revision=state.revision;
    try {
      const next=await api("/api/state?since="+revision);
      if (busy || drag || blocked || $("edit-dialog").open || ["INPUT","SELECT","TEXTAREA"].includes(document.activeElement.tagName)) return;
      if (!next.unchanged) applyState(next,true);
      else if (!serverOnline || $("save-status").classList.contains("error")) saveStatus("已保存");
      serverOnline=true;
    } catch (_) { serverOnline=false; saveStatus("本机服务连接中断", "error"); }
  }

  $("alert-dismiss").addEventListener("click",()=>$("alert").hidden=true);
  for (const button of document.querySelectorAll("[data-mode]")) button.addEventListener("click",()=>{
    mode=button.dataset.mode; for(const b of document.querySelectorAll("[data-mode]")) b.classList.toggle("active",b===button); renderTimeline();
  });
  $("undo").addEventListener("click",()=>edit({op:"undo"})); $("redo").addEventListener("click",()=>edit({op:"redo"}));
  $("settings").addEventListener("click",settingsDialog); $("edit-edge").addEventListener("click",openEdgeDialog);
  $("snap").addEventListener("change",()=>{ try { if (parseTime($("snap").value)<1) throw new Error("吸附步长必须至少为 1 fs。"); snapTouched=true; } catch(error) { showAlert(error.message); $("snap").value=exactTime(defaultSnap(state.project.period_fs),state.project.unit,true); } });
  $("row-height").addEventListener("input",()=>{ if (busy||blocked) return; rowOverride=Number($("row-height").value); $("row-value").value=rowOverride; renderTimeline(); });
  $("row-height").addEventListener("change",()=>{ const height=Number($("row-height").value); if (canEdit()) edit({op:"view",row_height:height}); else {rowOverride=null;renderTimeline();} });
  $("zoom-in").addEventListener("click",()=>setZoom(zoom*1.5)); $("zoom-out").addEventListener("click",()=>setZoom(zoom/1.5)); $("zoom-fit").addEventListener("click",()=>{ setZoom(1); scroll.scrollLeft=0; });
  $("add-signal").addEventListener("click",()=>openDialog("添加信号",field("信号名称","name","signal_"+(state.project.signals.length+1))+field("初始电平","level","0",{select:["0","1"]}),values=>edit({op:"add_signal",name:values.name.trim(),level:Number(values.level)})));
  $("dialog-close").addEventListener("click",()=>$("edit-dialog").close()); $("dialog-cancel").addEventListener("click",()=>$("edit-dialog").close());
  $("dialog-form").addEventListener("submit",async event=>{
    event.preventDefault(); if (busy||blocked) return;
    $("dialog-error").textContent=""; $("dialog-submit").disabled=true;
    try { const result=await dialogHandler(Object.fromEntries(new FormData(event.currentTarget))); if(result) $("edit-dialog").close(); else $("dialog-error").textContent=blocked?"请先处理保存冲突。":$("alert-text").textContent; }
    catch(error) { $("dialog-error").textContent=error.message; }
    finally { $("dialog-submit").disabled=false; }
  });
  $("conflict-dialog").addEventListener("cancel",event=>event.preventDefault());
  $("copy-draft").addEventListener("click",async()=>{
    try { await navigator.clipboard.writeText($("conflict-draft").value); showToast("已复制保留的工程和操作。"); }
    catch (_) { $("conflict-draft").focus(); $("conflict-draft").select(); showToast("内容已选中，请按 Ctrl+C 复制。"); }
  });
  $("reload-state").addEventListener("click",async()=>{
    $("reload-state").disabled=true;
    try { const next=await api("/api/state"); storeDraft(null); blocked=false; $("conflict-dialog").close(); $("edit-dialog").close(); applyState(next); showToast("已读取最新工程。被拒绝的操作没有重新提交。"); }
    catch(error) { $("conflict-message").textContent="仍无法读取最新工程："+error.message; }
    finally { $("reload-state").disabled=false; }
  });
  for(const id of ["threshold","signal-filter","kind-filter"]) $(id).addEventListener("change",refreshRelations);
  $("show-guides").addEventListener("change",repaintWaves);
  $("va-panel").addEventListener("toggle",()=>{if($("va-panel").open)refreshVA();});
  $("export").addEventListener("click",async()=>{
    if(!canEdit())return; busy=true;updateButtons();
    try { const data=await api("/api/export",{revision:state.revision}); showToast("已导出 VA 与工程快照："+data.path,10000); $("footer-status").textContent="已导出："+data.filename; }
    catch(error) { showAlert(error.status===409?"工程已有更新，请等待窗口读取新版本后再导出。":error.message); }
    finally{busy=false;updateButtons();}
  });
  $("done").addEventListener("click",()=>{if(canEdit())showToast("当前版本 "+state.revision+" 已保存。请回到聊天里告诉我“改好了”，并说明下一步要求。",7000);});
  scroll.addEventListener("wheel",event=>{
    if(event.ctrlKey){event.preventDefault();setZoom(zoom*(event.deltaY<0?1.2:1/1.2),Math.max(0,event.clientX-scroll.getBoundingClientRect().left-nameWidth()));}
    else if(event.shiftKey){event.preventDefault();scroll.scrollLeft+=event.deltaY+event.deltaX;}
  },{passive:false});
  scroll.addEventListener("scroll",updateZoomStatus,{passive:true});
  document.addEventListener("keydown",event=>{
    if(["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName)||$("edit-dialog").open||$("conflict-dialog").open)return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="z"){event.preventDefault();if(event.shiftKey?state?.can_redo:state?.can_undo)edit({op:event.shiftKey?"redo":"undo"});}
    if(event.key==="Escape"&&drag)cancelDrag();
  });
  window.addEventListener("beforeunload",event=>{if(busy||drag||blocked){event.preventDefault();event.returnValue="";}});
  let resizeTimer; new ResizeObserver(()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>renderTimeline(),80);}).observe(scroll);
  (async()=>{
    try {
      const next=await api("/api/state"); applyState(next);
      try { pendingDraft=JSON.parse(localStorage.getItem(draftKey)||"null"); } catch (_) { pendingDraft=null; }
      if(pendingDraft)conflict("上次窗口关闭前有一次操作尚未确认保存。已读取当前工程，请保留需要的操作内容后核对最新版本。");
      setInterval(poll,2000);
    } catch(error){saveStatus("连接失败","error");showAlert(error.message);$("timeline-content").innerHTML='<div class="empty-state">无法读取本机会话。<br>请重新使用启动器打开这个窗口。</div>';}
  })();
})();
