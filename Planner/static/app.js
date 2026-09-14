/* Oztudy frontend — extracted from templates/index.html. */
(function(){
  var $ = function(id){ return document.getElementById(id); };

  var selected = [];
  var duration = 2;
  var mode = "practice";
  var criterion = "A";
  var appSettings = null;
  var lastTopics = null;
  window.activeTimers = {};
  var wallInt = null;
  var lastPlan = null;
  var timerState = {
    activeBlockId: null,
    isRunning: false,
    remainingSeconds: 0,
    targetEndTs: 0
  };
  var TIMER_LS_KEY = "oztudy_timer_v1";
  function saveTimerState(){
    try {
      if (!timerState.activeBlockId){ localStorage.removeItem(TIMER_LS_KEY); return; }
      var b = (typeof findBlock === "function") ? findBlock(timerState.activeBlockId) : null;
      localStorage.setItem(TIMER_LS_KEY, JSON.stringify({
        blockId: timerState.activeBlockId,
        isRunning: !!timerState.isRunning,
        remainingSeconds: timerState.remainingSeconds || 0,
        targetEndTs: timerState.targetEndTs || 0,
        elapsed: b ? (b.elapsed || 0) : 0,
        started_at: b ? (b.started_at || 0) : 0,
        savedAt: Date.now()
      }));
    } catch(e){}
  }
  function loadTimerState(){
    try {
      var raw = localStorage.getItem(TIMER_LS_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch(e){ return null; }
  }
  function clearTimerState(){
    try { localStorage.removeItem(TIMER_LS_KEY); } catch(e){}
  }
  function restoreTimerState(){
    var saved = loadTimerState();
    if (!saved || !saved.blockId) return;
    // Drop stale saves (>12h) — prevents resurrecting yesterday's timer.
    if (saved.savedAt && (Date.now() - saved.savedAt) > 12 * 3600 * 1000){ clearTimerState(); return; }
    var b = (typeof findBlock === "function") ? findBlock(saved.blockId) : null;
    if (!b || b.status === "done" || b.status === "pushed"){ clearTimerState(); return; }
    if (typeof saved.elapsed === "number" && saved.elapsed > 0 && !(b.elapsed > 0)) b.elapsed = saved.elapsed;
    if (saved.started_at && !b.started_at && saved.isRunning) b.started_at = saved.started_at;
    timerState.activeBlockId = b.id;
    renderAnchorId = b.id;
    if (saved.isRunning && saved.targetEndTs){
      var remaining = Math.max(0, Math.round((saved.targetEndTs - Date.now()) / 1000));
      // If the timer expired while away, clamp to 0 — tickTimer will fire completion.
      timerState.remainingSeconds = remaining;
      timerState.targetEndTs = remaining > 0 ? saved.targetEndTs : 0;
      timerState.isRunning = remaining > 0;
      if (remaining <= 0) timerState.remainingSeconds = 0;
    } else {
      timerState.isRunning = false;
      timerState.targetEndTs = 0;
      timerState.remainingSeconds = Math.max(0, Math.round(blockSeconds(b) - (b.elapsed || 0)));
    }
    if (typeof moveTimerPanelTo === "function") moveTimerPanelTo(b);
    if (typeof applyCardState === "function") applyCardState(b);
    if (timerState.isRunning) startBlockTimer(b.id);
    else if (typeof writeTimer === "function") writeTimer(b, timerState.remainingSeconds);
  }
  window.addEventListener("beforeunload", saveTimerState);
  document.addEventListener("visibilitychange", function(){ if (document.hidden) saveTimerState(); });
  var renderAnchorId = null;
  var audioCtx = null;
  var completionFired = false;
  var zenActive = false;
  var zenBlockId = null;
  var zenAbandonPending = null;
  var zenAbandoning = false;
  var zenModalOpen = false;
  var lofiNodes = null;
  var spaceNodes = null;

  function ensureAudio(){
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch(e){ audioCtx = null; }
    return audioCtx;
  }

  function playChime(){
    var ctx = ensureAudio();
    if (!ctx) return;
    function strike(freq, delay){
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      var t = ctx.currentTime + delay;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.22, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 1.4);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 1.5);
    }
    strike(880, 0);
    strike(1318.51, 0.01);
    strike(1174.66, 0.3);
    strike(1760, 0.31);
  }

  function notifyComplete(block){
    var title = "Mola Zamanı";
    var who = (block.subject ? block.subject + " · " : "") + block.topic;
    var breakMin = (appSettings && appSettings.break_min) || 10;
    var body = "Time for a break! " + who + " seansı tamamlandı. " + breakMin + " dk dinlen.";
    if ("Notification" in window && Notification.permission === "granted"){
      try { new Notification(title, { body: body, icon: "/static/icon-192.png" }); } catch(e){}
    }
    setMsg(title + " " + block.topic + " seansı tamamlandı — mola vakti!", "ok");
  }

  function startLoFi(){
    if (lofiNodes) return;
    var ctx = ensureAudio(); if (!ctx) return;
    var master = ctx.createGain(); master.gain.value = 0.065; master.connect(ctx.destination);
    var nodes = [];
    [130.81, 164.81, 196.00].forEach(function(f, i){
      var o = ctx.createOscillator(); o.type = "sine"; o.frequency.value = f; o.detune.value = (i - 1) * 7;
      var g = ctx.createGain(); g.gain.value = 0.55;
      var lfo = ctx.createOscillator(); lfo.type = "sine"; lfo.frequency.value = 0.14 + i * 0.08;
      var lg = ctx.createGain(); lg.gain.value = 0.14;
      lfo.connect(lg); lg.connect(g.gain); o.connect(g); g.connect(master);
      o.start(); lfo.start(); nodes.push(o, lfo);
    });
    lofiNodes = { master: master, oscs: nodes };
  }
  function stopLoFi(){
    if (!lofiNodes) return;
    lofiNodes.oscs.forEach(function(o){ try { o.stop(); } catch(e){} });
    lofiNodes.master.disconnect(); lofiNodes = null;
  }
  function startSpace(){
    if (spaceNodes) return;
    var ctx = ensureAudio(); if (!ctx) return;
    var master = ctx.createGain(); master.gain.value = 0.055; master.connect(ctx.destination);
    var nodes = [];
    [36.7, 55, 73.42].forEach(function(f){
      var o = ctx.createOscillator(); o.type = "sine"; o.frequency.value = f;
      var g = ctx.createGain(); g.gain.value = 0.5;
      var lfo = ctx.createOscillator(); lfo.type = "sine"; lfo.frequency.value = 0.04 + Math.random() * 0.06;
      var lg = ctx.createGain(); lg.gain.value = 0.11;
      lfo.connect(lg); lg.connect(g.gain); o.connect(g); g.connect(master);
      o.start(); lfo.start(); nodes.push(o, lfo);
    });
    spaceNodes = { master: master, oscs: nodes };
  }
  function stopSpace(){
    if (!spaceNodes) return;
    spaceNodes.oscs.forEach(function(o){ try { o.stop(); } catch(e){} });
    spaceNodes.master.disconnect(); spaceNodes = null;
  }

  function fetchSchool(){
    fetch("/api/school").then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success"){ renderSchool(d.school); renderWeekend(d.school); }
    }).catch(function(){});
  }
  function fetchVersion(){
    fetch("/api/version").then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success" && d.version){
        var el = $("nav-ver");
        if (el) el.textContent = "v" + d.version;
      }
    }).catch(function(){});
  }
  function renderSchool(s){
    var ss = $("school-strip");
    if (!ss){ return; }
    if (!s || !s.date){ ss.className = "school-strip"; ss.hidden = true; return; }
    ss.className = "school-strip show" + (s.status === "holiday" ? " off" : "");
    ss.hidden = false;
    if (s.status === "school"){
      $("ss-badge").textContent = "DERS GÜNÜ";
      $("ss-subjects").textContent = "Bugün: " + (s.subjects_today || []).join(" · ");
      $("ss-meta").textContent = s.day + " · okul " + s.school_window + " · öğle " + s.lunch + " · çalışma sonrası";
      $("ss-note").textContent = "yarın: " + (s.subjects_next_day || []).join(" · ");
    } else if (s.status === "weekend"){
      $("ss-badge").textContent = "HAFTA SONU PROGRAMI";
      $("ss-subjects").textContent = (s.subjects_today && s.subjects_today.length ? "Bugün: " + s.subjects_today.join(" · ") : "Hafta sonu programı yok");
      $("ss-meta").textContent = s.day + " · tüm gün serbest";
      $("ss-note").textContent = "yarın: " + (s.subjects_next_day || []).join(" · ");
    } else {
      $("ss-badge").textContent = "SERBEST";
      $("ss-subjects").textContent = (s.status_label || "Serbest gün");
      $("ss-meta").textContent = s.day + " · tüm gün serbest";
      $("ss-note").textContent = "";
    }
  }
  function renderWeekend(s){
    var card = $("weekend-card");
    if (!card) return;
    var wp = s && s.weekend_program;
    if (!wp || !s.date){ card.hidden = true; card.classList.remove("show"); return; }
    card.hidden = false; card.classList.add("show");
    var dots = {
      "MATH":"#6366F1","ENG":"#0EA5E9","TUR":"#F43F5E","PHY":"#F87171","BIO":"#34D399","CHE":"#A78BFA",
      "GER":"#F59E0B","HIS":"#D97706","GEO":"#14B8A6","DT":"#8B5CF6","ECL":"#06B6D4",
      "VIA/MUS":"#EC4899","PE":"#22C55E","R&E":"#64748B"
    };
    ["sat","sun"].forEach(function(day){
      var box = $("weekend-" + day);
      if (!box) return;
      box.innerHTML = "";
      (wp[day] || []).forEach(function(code){
        var sp = document.createElement("span"); sp.className = "weekend-pill";
        var dot = document.createElement("span"); dot.className = "wdot";
        dot.style.background = dots[code] || "#94A3B8";
        sp.appendChild(dot); sp.appendChild(document.createTextNode(code));
        box.appendChild(sp);
      });
    });
  }

  function fetchGame(){
    fetch("/api/game").then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success") renderGame(d);
    }).catch(function(){});
  }
  function renderGame(g){
    var gs = $("gc-streak");
    if (gs){
      if (g.streak > 0){ gs.textContent = g.streak + " gün seri"; gs.hidden = false; }
      else gs.hidden = true;
    }
    var bc = $("gc-badges");
    if (bc){
      var BADGE_TR = {
        "Space Cadet": "İlk Seans", "IB Survivor": "5 Seans",
        "Comet Chaser": "3 Gün Seri", "Week Warrior": "5 Gün Seri",
        "10-Day Streak": "10 Gün Seri", "Star Navigator": "500 XP",
        "Outpost Architect": "8 Modül", "Weekly Target": "Haftalık Hedef"
      };
      bc.innerHTML = "";
      (g.badges || []).forEach(function(bg){
        var el = document.createElement("span");
        el.className = "gc-badge";
        var lab = BADGE_TR[bg] || bg;
        if (lab !== bg || bg.indexOf("Streak") !== -1) el.classList.add("lab");
        el.textContent = lab;
        bc.appendChild(el);
      });
    }
  }

  function resetZenState(fullClean){
    zenModalOpen = false;
    zenAbandonPending = null;
    zenAbandoning = false;
    $("zen-confirm").hidden = true;
    $("zen-overlay").hidden = true;
    stopLoFi(); stopSpace(); stopAmbient();
    if (fullClean){
      zenActive = false;
      zenBlockId = null;
      timerState.activeBlockId = null;
      timerState.isRunning = false;
      timerState.targetEndTs = 0;
      timerState.remainingSeconds = 0;
      completionFired = false;
      $("zen-splash").hidden = true;
    }
  }
  function enterZenMode(block){
    resetZenState(false);
    zenActive = true; zenBlockId = block.id;
    var zl = $("zen-lofi"), zs2 = $("zen-space");
    if (zl) zl.classList.remove("on");
    if (zs2) zs2.classList.remove("on");
    var zsub = $("zen-subject"), ztop = $("zen-topic"), zcd = $("zen-countdown");
    if (zsub) zsub.textContent = block.subject || "";
    if (ztop) ztop.textContent = block.topic || "";
    if (zcd) zcd.textContent = fmt(timerState.remainingSeconds || blockSeconds(block));
    var zf = $("zen-fill"); if (zf) zf.style.width = "0%";
    var zs = $("zen-status"); if (zs) zs.textContent = "Seans devam ediyor...";
    applyTimerTheme(appSettings ? appSettings.timer_theme : "none");
    $("zen-overlay").hidden = false;
    playAmbient(currentAmbient());
    ensureAudio();
  }
  function exitZenMode(){
    resetZenState(false);
    zenActive = false; zenBlockId = null;
  }
  function openZenConfirm(){
    if (!zenActive || !zenBlockId) return;
    zenModalOpen = true;
    zenAbandoning = false;
    $("zen-confirm").hidden = false;
  }
  function closeZenConfirm(){
    if (!zenModalOpen) return;
    zenModalOpen = false;
    zenAbandonPending = null;
    $("zen-confirm").hidden = true;
  }
  function doAbandonZen(){
    var blk = zenAbandonPending;
    if (!blk || zenAbandoning) return;
    zenAbandoning = true;
    resetZenState(true);
    setMsg("Seans sonlandırıldı — inşaat modülü hasarlandı.", "ok");
    adjust({ action: "done", id: blk.id, zen_abandon: true }, function(){
      fetchGame();
    });
  }
  function showZenSplash(title, detail, fullSuccess){
    resetZenState(false);
    zenActive = false; zenBlockId = null;
    $("zen-splash-title").textContent = title;
    $("zen-splash-detail").textContent = detail;
    $("zen-splash-icon").textContent = "";
    $("zen-splash").hidden = false;
    fetchGame();
  }

  function localDayKey(d){
    d = d || new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function loadStreak(){
    try {
      var raw = localStorage.getItem("planner_streak_count");
      if (raw){ var s = JSON.parse(raw); if (typeof s.count === "number" && s.last) return s; }
    } catch(e){}
    return { count: 0, last: "" };
  }

  function saveStreak(st){
    try { localStorage.setItem("planner_streak_count", JSON.stringify(st)); } catch(e){}
  }

  function renderStreak(){
    var st = loadStreak();
    var today = localDayKey();
    var yd = new Date(); yd.setDate(yd.getDate() - 1);
    var yKey = localDayKey(yd);
    var eff = (st.last === today || st.last === yKey) ? st.count : 0;
    var el = $("streak");
    if (el) el.textContent = eff + " Günlük Seri";
  }

  function todayPlanComplete(){
    var blocks = lastPlan || [];
    var study = [];
    blocks.forEach(function(b){
      if (b.type === "study" && b.status !== "pushed") study.push(b);
    });
    if (!study.length) return false;
    var allDone = true;
    study.forEach(function(b){ if (b.status !== "done") allDone = false; });
    return allDone;
  }

  function bumpStreak(){
    if (!todayPlanComplete()) return;
    var st = loadStreak();
    var today = localDayKey();
    var yd = new Date(); yd.setDate(yd.getDate() - 1);
    var yKey = localDayKey(yd);
    if (st.last === today) return;
    st.count = (st.last === yKey) ? st.count + 1 : 1;
    st.last = today;
    saveStreak(st);
    renderStreak();
  }

  function updateStats(){
    var blocks = lastPlan || [];
    var target = 0, doneSec = 0, doneCount = 0, totalCount = 0;
    var nowSec = Math.floor(Date.now() / 1000);
    blocks.forEach(function(b){
      if (b.type !== "study" || b.status === "pushed") return;
      totalCount++;
      target += b.duration;
      if (b.status === "done"){ doneCount++; doneSec += b.duration * 60; }
      else if (b.active){ doneSec += Math.min(b.duration * 60, blockElapsedTotal(b, nowSec)); }
    });
    $("st-total").textContent = target;
    $("st-done").textContent = Math.round(doneSec / 60);
    $("st-count").textContent = doneCount + "/" + totalCount;
    var pct = target ? Math.min(100, (doneSec / (target * 60)) * 100) : 0;
    var fill = $("st-fill");
    if (fill){
      var wpct = pct.toFixed(1) + "%";
      if (fill.style.width !== wpct) fill.style.width = wpct;
    }
    var pc = $("st-pct");
    if (pc) pc.textContent = Math.round(pct) + "%";
  }

  function setMsg(text, kind){
    var m = $("msg");
    m.textContent = text || "";
    m.className = kind || "";
  }

  function findSel(subject, topic){
    for (var i = 0; i < selected.length; i++){
      if (selected[i].subject === subject && selected[i].topic === topic) return selected[i];
    }
    return null;
  }

  function rebuildChips(){
    var box = $("sel-topics");
    box.innerHTML = "";
    selected.forEach(function(it){
      var badge = document.createElement("span");
      badge.className = "badge c-" + it.confidence;

      var top = document.createElement("span");
      top.className = "b-top";
      top.textContent = it.topic;

      var x = document.createElement("button");
      x.type = "button";
      x.className = "xch";
      x.textContent = "\u00d7";
      x.addEventListener("click", function(){ toggleTopic(it.subject, it.topic); });
      top.appendChild(x);
      badge.appendChild(top);

      var cf = document.createElement("span");
      cf.className = "cf";
      [["red","Zorlanıyorum"],["yellow","Orta"],["green","Hakimim"]].forEach(function(ci){
        var b = document.createElement("button");
        b.type = "button";
        b.className = ci[0];
        b.title = ci[1];
        b.innerHTML = "<i></i>" + ci[1];
        if (it.confidence === ci[0]) b.classList.add("on");
        b.addEventListener("click", function(){ it.confidence = ci[0]; rebuildChips(); });
        cf.appendChild(b);
      });
      badge.appendChild(cf);

      box.appendChild(badge);
    });
    if (!selected.length){
      var e = document.createElement("span");
      e.className = "sel-empty";
      e.textContent = "Henüz konu seçilmedi.";
      box.appendChild(e);
    }
    $("sel-count").textContent = selected.length;
    $("go").disabled = selected.length === 0;
  }

  function findPill(subject, topic){
    var pills = document.querySelectorAll(".pill[data-topic]");
    for (var i = 0; i < pills.length; i++){
      if (pills[i].dataset.subject === subject && pills[i].dataset.topic === topic) return pills[i];
    }
    return null;
  }

  function toggleTopic(subject, topic){
    var it = findSel(subject, topic);
    var pill = findPill(subject, topic);
    if (it){
      selected.splice(selected.indexOf(it), 1);
      if (pill) pill.classList.remove("active");
    } else {
      selected.push({ subject: subject, topic: topic, confidence: "yellow" });
      if (pill) pill.classList.add("active");
    }
    rebuildChips();
  }

  function injectTopic(subject, topic){
    var it = findSel(subject, topic);
    if (it){ it.confidence = "red"; }
    else {
      selected.push({ subject: subject, topic: topic, confidence: "red" });
      var pill = findPill(subject, topic);
      if (pill) pill.classList.add("active");
    }
    rebuildChips();
    setMsg("Zayıf konu plana eklendi.", "ok");
    $("go").click();
  }

  document.addEventListener("click", function(e){
    var pill = e.target.closest(".pill[data-topic]");
    if (pill){ toggleTopic(pill.dataset.subject, pill.dataset.topic); return; }
    var dur = e.target.closest("#dur-pills .pill[data-dur]");
    if (dur){
      document.querySelectorAll("#dur-pills .pill").forEach(function(p){ p.classList.remove("active"); });
      dur.classList.add("active");
      duration = parseInt(dur.dataset.dur, 10);
      var startMin = parseTimeToMin($("win-s").value);
      $("win-e").value = minToTimeStr(startMin + duration * 60);
      updateDefaultText();
      return;
    }
    var m = e.target.closest("#mode-pills .pill[data-mode]");
    if (m){
      document.querySelectorAll("#mode-pills .pill").forEach(function(p){ p.classList.remove("active"); });
      m.classList.add("active");
      mode = m.dataset.mode;
      return;
    }
    var c = e.target.closest("#cri-pills .pill[data-cri]");
    if (c){
      document.querySelectorAll("#cri-pills .pill").forEach(function(p){ p.classList.remove("active"); });
      c.classList.add("active");
      criterion = c.dataset.cri;
    }
  });

  $("win-s").addEventListener("input", syncPillFromTimes);
  $("win-e").addEventListener("input", syncPillFromTimes);

  $("sel-clear").addEventListener("click", function(){
    selected.forEach(function(it){ var p = findPill(it.subject, it.topic); if (p) p.classList.remove("active"); });
    selected = [];
    rebuildChips();
  });

  function blockEl(b){
    var div = document.createElement("div");
    div.className = "pblock";
    div.setAttribute("data-bid", b.id);
    var running = !!b.active;
    var timerPaused = !b.active && (b.elapsed || 0) > 0;
    if (b.type === "break") div.className += " break";
    else {
      div.className += " study c-" + b.confidence;
      if (b.isReview) div.className += " review";
      if (b.isRescheduled) div.className += " rescheduled";
      if (b.status === "pushed") div.className += " pushed";
      if (running) div.className += " active running";
      if (timerPaused) div.className += " timer-paused";
      if (b.status === "done") div.className += " done";
    }

    var row = document.createElement("div");
    row.className = "prow";

    var time = document.createElement("div");
    time.className = "ptime";
    time.textContent = b.time;
    row.appendChild(time);

    var mid = document.createElement("div");
    mid.className = "pmid";
    if (b.type === "break"){
      var br = document.createElement("div");
      br.className = "ptitle";
      br.textContent = "Mola";
      mid.appendChild(br);
    } else {
      var title = document.createElement("div");
      title.className = "ptitle";
      title.textContent = b.topic;
      var sub = document.createElement("div");
      sub.className = "psub";
      if (b.isReview){
        sub.textContent = b.subject + " · " + b.duration + " dk";
        var rbadge = document.createElement("span");
        rbadge.className = "review-badge";
        rbadge.textContent = (b.reviewLabel || "Spaced Review");
        sub.appendChild(rbadge);
      } else {
        sub.textContent = b.subject + " · " + b.duration + " dk";
        if (b.isRescheduled){
          var rres = document.createElement("span");
          rres.className = "res-badge";
          rres.textContent = "Rescheduled";
          sub.appendChild(rres);
        }
      }
      var tag = document.createElement("div");
      tag.className = "ptag";
      if (b.isReview){
        tag.className += " review";
        tag.textContent = "Spaced Review · " + b.duration + " dk";
      } else if (b.isRescheduled){
        tag.className += " rescheduled";
        tag.textContent = "Rescheduled";
      } else if (b.status === "pushed"){
        tag.className += " m";
        tag.textContent = "Yarına itelendi";
      } else {
        tag.className += " " + b.confidence[0];
        tag.textContent = {red:"Zorlanıyorum · Deep Focus", yellow:"Orta · Practice", green:"Hakimim · Quick Review"}[b.confidence];
      }
      mid.appendChild(title);
      mid.appendChild(sub);
      mid.appendChild(tag);
      if (b.status !== "pushed"){
        var stat = document.createElement("div");
        stat.className = "pstat";
        stat.setAttribute("data-bid", b.id);
        if (b.status === "done"){
          stat.className += " done";
          stat.textContent = "Tamamlandı";
        }
        mid.appendChild(stat);
      }
    }
    var notes = document.createElement("div");
    notes.className = "pnotes";
    if (b.type === "study" && b.active){
      notes.textContent = "Derin odak sürüyor — " + b.note;
    } else {
      notes.textContent = b.note;
    }
    mid.appendChild(notes);
    row.appendChild(mid);

    if (b.type === "study" && running){
      var go = document.createElement("span");
      go.className = "pgo";
      go.textContent = "ŞİMDİ";
      row.appendChild(go);
    }
    div.appendChild(row);

    if (b.type === "study" && b.id === renderAnchorId) div.appendChild(timerPanel(b));

    if (b.type === "study"){
      var acts = document.createElement("div");
      acts.className = "pacts";

      var a1 = document.createElement("button");
      a1.type = "button";
      if (running){
        a1.textContent = "Durdur";
        a1.classList.add("pause-btn");
      } else {
        a1.textContent = (b.elapsed || 0) > 0 ? "Devam Et" : "Şimdi Başlat";
        if (b.status !== "pushed") a1.classList.add("play-btn");
      }
      a1.addEventListener("click", function(){
        if (b.active) localStop(b);
        else localStart(b);
      });
      acts.appendChild(a1);

      var a2 = document.createElement("button");
      a2.type = "button";
      a2.textContent = b.status === "pushed" ? "Bugüne Döndür" : "Yarın Etiketiyle İtele";
      if (b.status !== "pushed") a2.classList.add("warn");
      a2.addEventListener("click", function(){
        adjust({ action: b.status === "pushed" ? "restore" : "push", id: b.id });
      });
      acts.appendChild(a2);

      var a3 = document.createElement("button");
      a3.type = "button";
      a3.textContent = b.status === "done" ? "Geri Al" : "Tamamlandı";
      a3.addEventListener("click", function(){
        adjust({ action: "done", id: b.id });
      });
      acts.appendChild(a3);

      div.appendChild(acts);

      if (b.status === "done"){
        div.appendChild(metricsEl(b));
      }
    }

    return div;
  }

  function timerPanel(b){
    var nowSec = Math.floor(Date.now() / 1000);
    var running = !!b.active;
    var remaining = (timerState.activeBlockId === b.id)
      ? timerState.remainingSeconds
      : Math.max(0, Math.round(blockSeconds(b) - blockElapsedTotal(b, nowSec)));
    var panel = document.createElement("div");
    panel.className = "timer-panel";

    var badge = document.createElement("span");
    badge.className = "timer-state-badge " + (running ? "run" : "pause");
    badge.textContent = running ? "RUNNING" : "PAUSED";
    panel.appendChild(badge);

    var ct = document.createElement("span");
    ct.className = "ctimer";
    ct.id = "timer-display";
    ct.setAttribute("data-bid", b.id);
    ct.textContent = fmt(remaining);
    panel.appendChild(ct);

    var prog = document.createElement("div");
    prog.className = "progress";
    var fill = document.createElement("div");
    fill.className = "progress-fill";
    fill.setAttribute("data-bid", b.id);
    fill.style.width = Math.min(100, (blockElapsedTotal(b, nowSec) / blockSeconds(b)) * 100).toFixed(1) + "%";
    prog.appendChild(fill);
    panel.appendChild(prog);

    var qacts = document.createElement("div");
    qacts.className = "timer-quick-acts";

    var ext = document.createElement("button");
    ext.type = "button";
    ext.className = "qbtn ext";
    ext.textContent = "+5 Dk";
    ext.title = "Sayacı 5 dakika uzat";
    ext.addEventListener("click", function(){
      if (timerState.activeBlockId !== b.id) return;
      nudgeTimer(b, 5);
    });
    qacts.appendChild(ext);

    var neg = document.createElement("button");
    neg.type = "button";
    neg.className = "qbtn neg";
    neg.textContent = "-5 Dk";
    neg.title = "Sayacı 5 dakika kısalt";
    neg.addEventListener("click", function(){
      if (timerState.activeBlockId !== b.id) return;
      nudgeTimer(b, -5);
    });
    qacts.appendChild(neg);

    var fin = document.createElement("button");
    fin.type = "button";
    fin.className = "qbtn fin";
    fin.textContent = "Erken Bitir";
    fin.title = "Seansı erken tamamla (sıradaki blok otomatik başlamaz)";
    fin.addEventListener("click", function(){
      if (timerState.activeBlockId !== b.id) return;
      finishEarly(b);
    });
    qacts.appendChild(fin);

    panel.appendChild(qacts);
    return panel;
  }

  function applyCardState(b){
    var div = document.querySelector('.pblock[data-bid="' + b.id + '"]');
    if (!div) return;
    var running = !!b.active;
    var paused = !b.active && (b.elapsed || 0) > 0;
    div.classList.toggle("active", running);
    div.classList.toggle("running", running);
    div.classList.toggle("timer-paused", paused);

    var acts = div.querySelector(".pacts");
    var a1 = acts ? acts.querySelector("button:first-child") : null;
    if (a1){
      a1.textContent = running ? "Durdur" : paused ? "Devam Et" : "Şimdi Başlat";
      a1.classList.toggle("pause-btn", running);
      a1.classList.toggle("play-btn", !running && b.status !== "pushed");
    }
    var resetBtn = acts ? acts.querySelector('button[data-act="reset"]') : null;
    if (resetBtn){
      resetBtn.remove();
    }

    var row = div.querySelector(".prow");
    var go = div.querySelector(".pgo");
    if (running && !go && row){
      go = document.createElement("span");
      go.className = "pgo";
      go.textContent = "ŞİMDİ";
      row.appendChild(go);
    } else if (!running && go){
      go.remove();
    }
    var notes = div.querySelector(".pnotes");
    if (notes) notes.textContent = running ? "Derin odak sürüyor — " + b.note : b.note;
  }

  function moveTimerPanelTo(b){
    var old = document.querySelector(".timer-panel");
    if (old) old.remove();
    var div = document.querySelector('.pblock[data-bid="' + b.id + '"]');
    if (!div) return;
    var acts = div.querySelector(".pacts");
    div.insertBefore(timerPanel(b), acts || null);
  }

  function localStart(b){
    if (!b || b.active) return;
    if (b.type !== "study" || b.status === "done" || b.status === "pushed") return;
    var nowSec = Math.floor(Date.now() / 1000);
    var blocks = lastPlan || [];
    blocks.forEach(function(x){
      if (x !== b && x.type === "study" && x.active){
        x.elapsed = (x.elapsed || 0) + Math.max(0, nowSec - (x.started_at || nowSec));
        x.active = false;
        delete x.started_at;
        applyCardState(x);
        clearBlockTimer(x.id);
      }
    });
    b.active = true;
    b.started_at = nowSec;
    var remaining = Math.max(0, Math.round(blockSeconds(b) - blockElapsedTotal(b, nowSec)));
    completionFired = false;
    timerState.activeBlockId = b.id;
    timerState.isRunning = true;
    timerState.remainingSeconds = remaining;
    timerState.targetEndTs = Date.now() + remaining * 1000;
    renderAnchorId = b.id;
    moveTimerPanelTo(b);
    applyCardState(b);
    enterZenMode(b);
    updateStats();
    startBlockTimer(b.id);
    saveTimerState();
    syncTimer(b, "start");
  }

  function localStop(b){
    if (!b || !b.active) return;
    clearBlockTimer(b.id);
    var nowSec = Math.floor(Date.now() / 1000);
    b.elapsed = (b.elapsed || 0) + Math.max(0, nowSec - (b.started_at || nowSec));
    b.active = false;
    delete b.started_at;
    completionFired = false;
    timerState.isRunning = false;
    timerState.targetEndTs = 0;
    timerState.remainingSeconds = Math.max(0, Math.round(blockSeconds(b) - (b.elapsed || 0)));
    renderAnchorId = b.id;
    moveTimerPanelTo(b);
    applyCardState(b);
    exitZenMode();
    updateStats();
    saveTimerState();
    syncTimer(b, "stop");
  }

  function localReset(b){
    if (!b) return;
    clearBlockTimer(b.id);
    b.active = false;
    delete b.started_at;
    b.elapsed = 0;
    b.duration = b.origDuration || b.duration;
    completionFired = false;
    timerState.isRunning = false;
    timerState.targetEndTs = 0;
    timerState.remainingSeconds = blockSeconds(b);
    if (timerState.activeBlockId === b.id){
      timerState.activeBlockId = null;
      renderAnchorId = null;
    }
    clearTimerState();
    applyCardState(b);
    var old = document.querySelector(".timer-panel");
    if (old && old.closest('.pblock[data-bid="' + b.id + '"]')) old.remove();
    updateStats();
    syncTimer(b, "reset");
  }

  function reconcilePlan(plan){
    if (!plan || !plan.blocks) return;
    var anchor = timerState.activeBlockId;
    (lastPlan || []).forEach(function(x){
      var match = null;
      for (var i = 0; i < plan.blocks.length; i++){
        if (plan.blocks[i].id === x.id){ match = plan.blocks[i]; break; }
      }
      if (match){
        if (x.active && x.id === anchor && timerState.isRunning){
          x.duration = match.duration;
        } else {
          x.elapsed = match.elapsed || 0;
          x.active = match.active || false;
          x.duration = match.duration;
          if (match.started_at) x.started_at = match.started_at;
          else delete x.started_at;
        }
      }
    });
  }

  function syncTimer(b, action){
    fetch("/api/blocks/adjust", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, id: b.id })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){ setMsg(d.message || "Hata oluştu.", "err"); return; }
      reconcilePlan(d.plan);
      if (d.message) setMsg(d.message, "ok");
      fetchDrawer();
      fetchGame();
      // FEATURE 1: timer-completed study block → ask difficulty.
      if (action === "done") maybeFeedback(d.plan, b.id);
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  function renderPlan(plan){
    var tl = $("timeline");
    tl.innerHTML = "";
    clearAllTimers();
    if (wallInt){ clearInterval(wallInt); wallInt = null; }
    var enabled = !!(plan && plan.blocks && plan.blocks.length);
    $("shift15").disabled = !enabled;
    $("shift30").disabled = !enabled;
    lastPlan = plan ? plan.blocks : null;
    lastTopics = (plan && plan.input && plan.input.topics) ? plan.input.topics : null;
    updateStats();
    bumpStreak();
    timerState.remainingSeconds = 0;
    timerState.targetEndTs = 0;
    if (!plan){
      var e = document.createElement("div");
      e.className = "empty";
      e.innerHTML = "<b>Henüz plan yok</b>Soldan konuları seç ve \u201cPlanı Oluştur\u201d.";
      tl.appendChild(e);
      if (appSettings && appSettings.study_min && appSettings.break_min){
        $("tl-note").textContent = appSettings.study_min + " dk odak blokları · " + appSettings.break_min + " dk molalar";
      } else {
        $("tl-note").textContent = "45 dk odak blokları · 10 dk molalar";
      }
      return;
    }
    $("tl-note").textContent = plan.meta.note;
    if (plan.meta && plan.meta.school) renderSchool(plan.meta.school);
    renderAnchorId = null;
    var runningNow = plan.blocks.filter(function(b){ return b.type === "study" && b.active; });
    if (runningNow.length){
      renderAnchorId = runningNow[0].id;
      timerState.activeBlockId = runningNow[0].id;
      timerState.isRunning = true;
    } else {
      timerState.isRunning = false;
      var keepBlock = null;
      plan.blocks.forEach(function(b){
        if (b.type === "study" && b.id === timerState.activeBlockId && (b.elapsed || 0) > 0) keepBlock = b;
      });
      if (keepBlock){
        renderAnchorId = keepBlock.id;
        timerState.activeBlockId = keepBlock.id;
        timerState.remainingSeconds = Math.max(0, Math.round(blockSeconds(keepBlock) - (keepBlock.elapsed || 0)));
      } else {
        var firstPaused = null;
        plan.blocks.forEach(function(b){
          if (!firstPaused && b.type === "study" && (b.elapsed || 0) > 0) firstPaused = b;
        });
        renderAnchorId = firstPaused ? firstPaused.id : null;
        timerState.activeBlockId = renderAnchorId;
        if (firstPaused){
          timerState.remainingSeconds = Math.max(0, Math.round(blockSeconds(firstPaused) - (firstPaused.elapsed || 0)));
        }
      }
    }
    plan.blocks.forEach(function(b){ tl.appendChild(blockEl(b)); });
    $("st-total").textContent = plan.stats.total_min;
    $("st-done").textContent = plan.stats.done_min;
    $("st-count").textContent = plan.stats.done_count + "/" + plan.blocks.filter(function(b){ return b.type === "study" && b.status !== "pushed"; }).length;
    if (timerState.isRunning && timerState.activeBlockId) startBlockTimer(timerState.activeBlockId);
    startWallClock();
    refreshStats();
  }

  function fmt(sec){
    sec = Math.max(0, Math.floor(sec));
    return String(Math.floor(sec / 60)).padStart(2, "0") + ":" + String(sec % 60).padStart(2, "0");
  }

  function blockWindow(b){
    var d = new Date();
    d.setHours(0, 0, 0, 0);
    var base = d.getTime();
    return { startTs: base + b.start * 60000, endTs: base + b.end * 60000 };
  }

  function blockElapsedTotal(b, nowSec){
    var el = b.elapsed || 0;
    if (b.active && b.started_at){
      el += Math.max(0, nowSec - b.started_at);
    }
    return el;
  }

  function blockSeconds(b){
    var mins = Math.max(1, (b.duration > 0) ? b.duration : (b.end - b.start));
    return mins * 60;
  }

  function parseTimeToMin(s){
    var p = s.split(":");
    return parseInt(p[0],10)*60 + parseInt(p[1],10);
  }

  function minToTimeStr(total){
    total = ((total % 1440) + 1440) % 1440;
    return String(Math.floor(total/60)).padStart(2,"0") + ":" + String(total%60).padStart(2,"0");
  }

  function updateDefaultText(){
    var span = document.querySelector(".win .t");
    if (span) span.textContent = "(varsayılan " + $("win-s").value + " - " + $("win-e").value + ")";
  }

  function syncPillFromTimes(){
    var sMin = parseTimeToMin($("win-s").value);
    var eMin = parseTimeToMin($("win-e").value);
    var diff = eMin - sMin;
    if (diff <= 0) diff += 1440;
    var matched = false;
    document.querySelectorAll("#dur-pills .pill[data-dur]").forEach(function(p){
      var d = parseInt(p.dataset.dur,10);
      if (d*60 === diff){ p.classList.add("active"); matched = true; }
      else p.classList.remove("active");
    });
    if (matched) duration = diff / 60;
    updateDefaultText();
  }

  function writeTimer(b, remaining){
    var id = b.id;
    var str = fmt(remaining);
    var ct = document.querySelector('.ctimer[data-bid="' + id + '"]');
    if (ct && ct.innerText !== str) ct.innerText = str;
    var td = document.getElementById("timer-display");
    if (td && td.innerText !== str) td.innerText = str;
    var fill = document.querySelector('.progress-fill[data-bid="' + id + '"]');
    if (fill){
      var pct = Math.min(100, (blockElapsedTotal(b, Math.floor(Date.now() / 1000)) / blockSeconds(b)) * 100);
      var wpct = pct.toFixed(1) + "%";
      if (fill.style.width !== wpct) fill.style.width = wpct;
    }
    if (zenActive && zenBlockId === id){
      var zc = $("zen-countdown"); if (zc) zc.innerText = str;
      var zf = $("zen-fill"); if (zf){
        var tot = blockSeconds(b);
        var zp = tot ? ((tot - remaining) / tot) * 100 : 0;
        zf.style.width = Math.min(100, zp).toFixed(1) + "%";
      }
      var zs = $("zen-status"); if (zs && zs.innerText !== "Seans devam ediyor...") zs.innerText = "Seans devam ediyor...";
    }
  }

  function findBlock(id){
    var blocks = lastPlan || [];
    for (var i = 0; i < blocks.length; i++){
      if (blocks[i].id === id) return blocks[i];
    }
    return null;
  }

  function clearBlockTimer(id){
    var t = window.activeTimers[id];
    if (t){ clearInterval(t); delete window.activeTimers[id]; }
  }

  function clearAllTimers(){
    Object.keys(window.activeTimers).forEach(function(id){ clearBlockTimer(id); });
  }

  function startBlockTimer(id){
    clearBlockTimer(id);
    window.activeTimers[id] = setInterval(function(){ tickTimer(id); }, 1000);
    tickTimer(id);
  }

  function tickTimer(id){
    var b = findBlock(id);
    if (!b){ clearBlockTimer(id); return; }
    if (!timerState.isRunning || timerState.activeBlockId !== id){
      clearBlockTimer(id);
      return;
    }
    if (!timerState.targetEndTs){
      var init = blockSeconds(b);
      var el = blockElapsedTotal(b, Math.floor(Date.now() / 1000));
      timerState.remainingSeconds = Math.max(0, Math.round(init - el));
      timerState.targetEndTs = Date.now() + timerState.remainingSeconds * 1000;
    } else {
      timerState.remainingSeconds = Math.max(0, Math.round((timerState.targetEndTs - Date.now()) / 1000));
    }
    writeTimer(b, timerState.remainingSeconds);
    // Persist every 5s so a crash resumes close to where it left off
    // (plus beforeunload/visibilitychange for the exact moment of exit).
    if (timerState.remainingSeconds % 5 === 0) saveTimerState();
    if (timerState.remainingSeconds <= 0 && !completionFired){
      clearBlockTimer(id);
      onTimerComplete(b);
    }
  }

  function updateWallClock(){
    var blocks = lastPlan || [];

    blocks.forEach(function(b){
      if (b.type === "study" && b.status === "pushed") return;

      var el = document.querySelector('.pblock[data-bid="' + b.id + '"]');
      var now = Date.now();
      var win = blockWindow(b);
      var wall;
      if (now < win.startTs) wall = "up";
      else if (now >= win.endTs) wall = "co";
      else wall = "ac";

      if (el){
        el.classList.remove("w-up", "w-ac", "w-co");
        el.classList.add("w-" + wall);
      }

      var chip = document.querySelector('.pstat[data-bid="' + b.id + '"]');
      if (chip && b.status !== "done"){
        chip.className = "pstat " + wall;
        chip.textContent = wall === "up" ? "Başlaması bekleniyor"
          : wall === "ac" ? "Planlanan aralık aktif" : "Planlanan süresi geçti";
      }
    });
  }

  function startWallClock(){
    if (wallInt){ clearInterval(wallInt); wallInt = null; }
    updateWallClock();
    wallInt = setInterval(updateWallClock, 30000);
  }

  function dayLabel(ts){
    var days = Math.floor((Date.now() / 1000 - ts) / 86400);
    if (days <= 0) return "bugün";
    if (days === 1) return "1 gün önce";
    return days + " gün önce";
  }

  function itemRow(w, buttons, tom){
    var row = document.createElement("div");
    row.className = "witem";
    var sub = document.createElement("span");
    sub.className = "ws";
    sub.textContent = w.subject;
    var t = document.createElement("span");
    t.className = "wt";
    t.textContent = w.topic;
    var d = document.createElement("span");
    d.className = "wd";
    d.textContent = (w.time ? w.time + " · " : "") + (tom ? "yarın için" : dayLabel(w.added));
    row.appendChild(sub);
    row.appendChild(t);
    row.appendChild(d);
    var meta = document.createElement("span");
    meta.className = "wmeta";
    buttons.forEach(function(btn){
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn-sm " + (btn.pln ? "btn-pln" : "");
      b.textContent = btn.label;
      b.addEventListener("click", btn.fn);
      meta.appendChild(b);
    });
    row.appendChild(meta);
    return row;
  }

  function renderDrawer(d){
    var weak = d.weak || [];
    var tomorrow = d.tomorrow || [];
    var box = $("weak-list");
    box.innerHTML = "";
    $("weak-count").textContent = weak.length;

    if (tomorrow.length){
      var sec = document.createElement("div");
      sec.className = "dr-section tom";
      var h = document.createElement("h3");
      h.innerHTML = "Yarına İtelendi <span class='n'>(" + tomorrow.length + ")</span>";
      sec.appendChild(h);
      tomorrow.forEach(function(w){
        sec.appendChild(itemRow(w, [
          { label: "Bugün Planla", pln: true, fn: function(){ injectTopic(w.subject, w.topic); } },
          { label: "Kaldır", fn: function(){
              fetch("/api/tomorrow", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ remove: true, subject: w.subject, topic: w.topic }) })
              .then(function(r){ return r.json(); }).then(function(dd){ if (dd.status === "success") fetchDrawer(); });
          } }
        ], true));
      });
      box.appendChild(sec);
    }

    if (weak.length){
      var sec2 = document.createElement("div");
      sec2.className = "dr-section";
      var h2 = document.createElement("h3");
      h2.innerHTML = "Zayıf Konular <span class='n'>(" + weak.length + ")</span>";
      sec2.appendChild(h2);
      weak.forEach(function(w){
        sec2.appendChild(itemRow(w, [
          { label: "Bugün Planla", pln: true, fn: function(){ injectTopic(w.subject, w.topic); } },
          { label: "Kaldır", fn: function(){
              fetch("/api/weak", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ remove: true, subject: w.subject, topic: w.topic }) })
              .then(function(r){ return r.json(); }).then(function(dd){ if (dd.status === "success") fetchDrawer(); });
          } }
        ], false));
      });
      box.appendChild(sec2);
    }

    if (!weak.length && !tomorrow.length){
      var e = document.createElement("div");
      e.className = "empty";
      e.innerHTML = "<b>Zayıf konu yok</b>Çalışırken bir konuda \u201cZorlanıyorum\u201d seç veya bir bloğu yarına itele.";
      box.appendChild(e);
    }
  }

  function fetchDrawer(){
    fetch("/api/drawer").then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success") renderDrawer(d);
    }).catch(function(){});
  }

  function fetchReviews(){
    fetch("/api/reviews").then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success" && d.reviews.length){
        var subjects = d.reviews.map(function(r){ return r.subject + " · " + r.topic; });
        $("rb-text").textContent = d.reviews.length + " Spaced Review bekliyor (" + subjects.join(", ") + ") — timeline'a ekle?";
        $("review-banner").classList.add("show");
      }
    }).catch(function(){});
  }

  function scheduleReviews(){
    fetch("/api/reviews/schedule", {method:"POST", headers:{"Content-Type":"application/json"}})
      .then(function(r){ return r.json(); }).then(function(d){
        if (d.status === "success"){
          $("review-banner").classList.remove("show");
          setMsg("✓ " + d.message, "ok");
          if (d.plan) renderPlan(d.plan);
        } else {
          setMsg(d.message || "Hata oluştu.", "err");
        }
      }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  function escHtml(s){
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  function fmtMinTotal(m){
    m = Math.max(0, Math.round(m || 0));
    if (m >= 60){
      var h = Math.floor(m / 60), r = m % 60;
      return r ? h + " s " + r + " dk" : h + " saat";
    }
    return m + " dk";
  }

  function metricInput(label, val, onCommit){
    var wrap = document.createElement("label");
    wrap.className = "metric";
    var span = document.createElement("span");
    span.textContent = "✓ " + label;
    var input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.step = "1";
    input.setAttribute("inputmode", "numeric");
    input.placeholder = "0";
    if (val) input.value = val;
    var deb = null;
    function commit(){
      var v = parseInt(input.value, 10);
      if (isNaN(v) || v < 0) v = 0;
      onCommit(v);
    }
    input.addEventListener("input", function(){
      if (deb) clearTimeout(deb);
      deb = setTimeout(commit, 650);
    });
    input.addEventListener("change", commit);
    wrap.appendChild(span);
    wrap.appendChild(input);
    return wrap;
  }

  function saveMetrics(id, field, value){
    var payload = { id: id };
    payload[field] = value;
    fetch("/api/blocks/metrics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success"){
        setMsg("✓ Kaydedildi (" + field + ": " + value + ")", "ok");
        refreshStats();
      }
      else setMsg(d.message || "Hata oluştu.", "err");
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  function switchTab(pane){
    var tw = $("tab-wiz"), ts = $("tab-stat"), tr = $("tab-report"), ts2 = $("tab-settings");
    if (tw) tw.classList.toggle("active", pane === "wiz");
    if (ts) ts.classList.toggle("active", pane === "stat");
    if (tr) tr.classList.toggle("active", pane === "report");
    if (ts2) ts2.classList.toggle("active", pane === "settings");
    var an = $("analytics-panel");
    if (an){
      an.hidden = pane !== "stat";
      an.classList.toggle("show", pane === "stat");
      if (pane === "stat" && an.hidden === false) an.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    var rp = $("report-panel");
    if (rp){
      rp.hidden = pane !== "report";
      rp.classList.toggle("show", pane === "report");
      if (pane === "report" && rp.hidden === false) rp.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    var sp = $("settings-panel");
    if (sp){
      sp.hidden = pane !== "settings";
      sp.classList.toggle("show", pane === "settings");
      if (pane === "settings" && sp.hidden === false) sp.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    if (pane === "stat") fetchStats();
    if (pane === "report") fetchReport();
  }

  function renderSubjectBars(subjects, boxId){
    var box = $(boxId || "st-subject-bars");
    box.innerHTML = "";
    if (!subjects || !subjects.length){
      box.innerHTML = "<div class='empty' style='padding:16px 10px;font-size:13px'><b>Veri yok</b>İlk bloğu tamamladığında dağılım burada görünür.</div>";
      return;
    }
    var max = 1;
    subjects.forEach(function(s){ if (s.minutes > max) max = s.minutes; });
    subjects.forEach(function(s){
      var row = document.createElement("div");
      row.className = "bar-row";
      var label = document.createElement("span");
      label.className = "bar-label";
      label.textContent = s.subject;
      var track = document.createElement("div");
      track.className = "bar-track";
      var fill = document.createElement("div");
      fill.className = "bar-fill";
      fill.style.width = Math.max(3, (s.minutes / max) * 100) + "%";
      track.appendChild(fill);
      var val = document.createElement("span");
      val.className = "bar-val";
      val.textContent = fmtMinTotal(s.minutes) + " · %" + s.percent;
      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(val);
      box.appendChild(row);
    });
  }

  function renderDayBars(days, boxId, withTitle){
    var box = $(boxId || "st-day-bars");
    box.innerHTML = "";
    if (!days || !days.length) return;
    var max = 1;
    days.forEach(function(x){ if (x.minutes > max) max = x.minutes; });
    days.forEach(function(x){
      var col = document.createElement("div");
      col.className = "day-col";
      if (withTitle) col.title = (x.date || "") + " · " + fmtMinTotal(x.minutes);
      var fill = document.createElement("div");
      fill.className = "day-fill";
      fill.style.height = Math.max(3, (x.minutes / max) * 100) + "%";
      var mn = document.createElement("span");
      mn.className = "day-min";
      mn.textContent = x.minutes > 0 ? String(Math.round(x.minutes)) : "";
      var sub = null;
      if ((x.questions > 0) || (x.pages > 0)){
        sub = document.createElement("span");
        sub.className = "day-sub";
        sub.textContent = (x.questions > 0 ? "S" + x.questions : "") + (x.pages > 0 ? "·P" + x.pages : "");
      }
      var lb = document.createElement("span");
      lb.className = "day-lbl";
      lb.textContent = x.day;
      col.appendChild(fill);
      col.appendChild(mn);
      if (sub) col.appendChild(sub);
      col.appendChild(lb);
      box.appendChild(col);
    });
  }

  function refreshStats(){
    var an = $("analytics-panel");
    if (an && !an.hidden) fetchStats();
    var rp = $("report-panel");
    if (rp && !rp.hidden) fetchReport();
  }

  function fetchStats(){
    fetch("/api/stats").then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success") return;
      $("st-today").textContent = fmtMinTotal(d.today.minutes);
      $("st-today-sub").textContent = d.today.done + " seans · " + d.today.questions + " soru";
      $("st-week").textContent = fmtMinTotal(d.week.minutes);
      $("st-week-sub").textContent = d.week.done + " seans · " + d.week.pages + " sayfa";
      $("st-qs").textContent = d.week.questions;
      $("st-qsub").textContent = d.week.pages + " okuma sayfası";
      renderSubjectBars(d.subjects);
      renderDayBars(d.days);
      fetchPeak();
      fetchHeatmap();
    }).catch(function(){});
  }

  var reportRange = "week";

  function renderReportTopics(topics){
    var box = $("rp-topics");
    box.innerHTML = "";
    if (!topics || !topics.length){
      box.innerHTML = "<div class='empty' style='padding:10px 2px;font-size:12px'>Konu kaydı yok.</div>";
      return;
    }
    topics.forEach(function(t, i){
      var row = document.createElement("div");
      row.className = "rp-topic";
      var rk = document.createElement("span");
      rk.className = "rp-rank";
      rk.textContent = String(i + 1);
      var tt = document.createElement("span");
      tt.className = "rp-tt";
      var chip = document.createElement("span");
      chip.className = "rp-ts";
      chip.textContent = t.subject;
      tt.appendChild(chip);
      tt.appendChild(document.createTextNode(t.topic));
      var tm = document.createElement("span");
      tm.className = "rp-tm";
      tm.textContent = fmtMinTotal(t.minutes) + " · " + t.sessions + " seans";
      row.appendChild(rk);
      row.appendChild(tt);
      row.appendChild(tm);
      box.appendChild(row);
    });
  }

  function fetchReport(){
    fetch("/api/report?range=" + encodeURIComponent(reportRange)).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success") return;
      $("rp-range-title").textContent = d.range.title;
      $("rp-hours").textContent = fmtMinTotal(d.totals.minutes);
      $("rp-hours-sub").textContent = d.range.days_active + " aktif gün / " + d.range.days_total + " gün";
      $("rp-sessions").textContent = d.totals.sessions;
      $("rp-sessions-sub").textContent = d.totals.questions + " soru · " + d.totals.pages + " sayfa";
      $("rp-questions").textContent = d.totals.questions;
      $("rp-questions-sub").textContent = d.totals.pages + " sayfa okuma";
      $("rp-avg").textContent = fmtMinTotal(d.totals.avg_minutes);
      $("rp-cons").textContent = "%" + d.highlights.consistency;
      $("rp-cons-sub").textContent = d.range.days_active + "/" + d.range.days_total + " gün";
      $("rp-streak").textContent = d.highlights.streak;
      var best = d.highlights.best_day;
      $("rp-best").textContent = best ? best.label + " · " + fmtMinTotal(best.minutes) : "—";
      $("rp-best-sub").textContent = best ? best.date : "henüz veri yok";
      var top = d.highlights.top_subject;
      $("rp-topsubj").textContent = top ? top.subject : "—";
      $("rp-topsubj-sub").textContent = top ? fmtMinTotal(top.minutes) + " · %" + top.percent : "henüz veri yok";
      $("rp-level").textContent = "Sv. " + d.highlights.level;
      $("rp-level-sub").textContent = d.highlights.xp + " XP · " + d.highlights.badges.length + " rozet";
      renderDayBars(d.days, "rp-day-bars", true);
      renderSubjectBars(d.subjects, "rp-subject-bars");
      renderReportTopics(d.topics);
      $("rp-empty").hidden = d.totals.sessions > 0;
    }).catch(function(){});
  }

  function fetchOverdue(){
    fetch("/api/overdue").then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success" && d.count){
        var labels = d.overdue.map(function(o){ return o.subject + " · " + o.topic; });
        var shown = labels.slice(0,3).join(", ");
        var extra = d.count > 3 ? " ve " + (d.count - 3) + " tane daha" : "";
        $("cb-text").textContent = d.count + " gecikmiş konu var (" + shown + extra + "). Catch-Up Modu etkinleştir?";
        $("catchup-bar").classList.add("show");
      }
    }).catch(function(){});
  }

  function activateCatchUp(){
    fetch("/api/catchup", {method:"POST", headers:{"Content-Type":"application/json"}})
      .then(function(r){ return r.json(); }).then(function(d){
        if (d.status === "success"){
          $("catchup-bar").classList.remove("show");
          setMsg("✓ Catch-Up tamamlandı.", "ok");
          fetchDrawer();
          showCatchupModal(d.summary || {});
        } else {
          setMsg(d.message || "Hata oluştu.", "err");
        }
      }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  function showCatchupModal(s){
    var lines = [];
    if (s.note) lines.push("• " + s.note);
    if (s.days && Object.keys(s.days).length){
      var dstr = Object.keys(s.days).map(function(d){ return s.days[d].label; }).join(" / ");
      lines.push("• " + (s.missed || 0) + " gecikmiş konu sonraki 3 güne eşit dağıtıldı: " + dstr);
      Object.keys(s.days).forEach(function(d){
        s.days[d].topics.forEach(function(t){
          lines.push("   - " + t + " · " + s.days[d].label);
        });
      });
    }
    if (!lines.length) lines.push("• Gecikmiş konu bulunamadı.");
    $("m-list").innerHTML = lines.map(function(l){ return "<li>" + escHtml(l) + "</li>"; }).join("");
    $("m-sub").textContent = "Değişiklik özeti — kuyruğa eklenenler sonraki günler açıldığında plana \"Rescheduled\" etiketiyle dahil edilir.";
    $("overlay").classList.add("show");
  }

  function nudgeTimer(block, deltaMin){
    if (!block || block.status === "done" || block.status === "pushed") return;
    completionFired = false;
    var deltaSec = deltaMin * 60;
    if (timerState.isRunning){
      timerState.targetEndTs += deltaSec * 1000;
      timerState.remainingSeconds = Math.max(0, Math.floor((timerState.targetEndTs - Date.now()) / 1000));
    } else {
      timerState.remainingSeconds = Math.max(0, timerState.remainingSeconds + deltaSec);
      timerState.targetEndTs = 0;
    }
    var orig = block.origDuration || block.duration || 5;
    if (timerState.isRunning){
      block.duration = Math.min(orig + 60, Math.max(1, Math.ceil(timerState.remainingSeconds / 60)));
    } else {
      var totalSecPaused = Math.max(1, timerState.remainingSeconds + (block.elapsed || 0));
      block.duration = Math.min(orig + 60, Math.max(1, Math.ceil(totalSecPaused / 60)));
    }
    writeTimer(block, timerState.remainingSeconds);
    updateStats();
    persistTimerAdjust(block.id, deltaMin);
  }

  function persistTimerAdjust(id, deltaMin){
    fetch("/api/blocks/adjust", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "extend", id: id, delta: deltaMin })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){ setMsg(d.message || "Hata oluştu.", "err"); return; }
      reconcilePlan(d.plan);
      fetchGame();
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  function finishEarly(block){
    if (!block || block.status === "done") return;
    localDone(block);
    syncTimer(block, "done");
  }

  function localDone(b){
    if (!b || b.status === "done") return;
    clearBlockTimer(b.id);
    b.status = "done";
    b.active = false;
    b.elapsed = 0;
    delete b.started_at;
    completionFired = false;
    timerState.isRunning = false;
    timerState.targetEndTs = 0;
    if (timerState.activeBlockId === b.id){
      timerState.activeBlockId = null;
      renderAnchorId = null;
    }
    exitZenMode();
    applyDoneState(b);
    setMsg("✓ Seans tamamlandı.", "ok");
    updateStats();
  }

  function applyDoneState(b){
    var div = document.querySelector('.pblock[data-bid="' + b.id + '"]');
    if (!div) return;
    div.classList.add("done");
    div.classList.remove("active", "running", "timer-paused");
    var chip = div.querySelector(".pstat");
    if (chip){ chip.className = "pstat done"; chip.textContent = "Tamamlandı"; }
    var acts = div.querySelector(".pacts");
    if (acts){
      var a1 = acts.querySelector("button:first-child");
      if (a1){ a1.textContent = "Şimdi Başlat"; a1.classList.remove("pause-btn"); a1.classList.add("play-btn"); }
      var rb = acts.querySelector('button[data-act="reset"]');
      if (rb) rb.remove();
      var a3 = acts.lastElementChild;
      if (a3) a3.textContent = "Geri Al";
    }
    var old = div.querySelector(".timer-panel");
    if (old) old.remove();
    if (!div.querySelector(".pmetrics")) div.appendChild(metricsEl(b));
  }

  function metricsEl(b){
    var m = document.createElement("div");
    m.className = "pmetrics";
    m.appendChild(metricInput("Soru", b.questions, function(v){ saveMetrics(b.id, "questions", v); }));
    m.appendChild(metricInput("Sayfa", b.pages, function(v){ saveMetrics(b.id, "pages", v); }));
    return m;
  }

  function onTimerComplete(block){
    if (completionFired) return;
    completionFired = true;
    clearBlockTimer(block.id);
    clearTimerState();
    playChime();
    notifyComplete(block);
    if (zenActive){
      adjust({ action: "done", id: block.id }, function(){
        exitZenMode();
        setMsg("✓ Seans tamamlandı.", "ok");
        fetchGame();
      });
    } else {
      adjust({ action: "stop", id: block.id });
    }
  }

  function adjust(payload, after){
    fetch("/api/blocks/adjust", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){ setMsg(d.message || "Hata oluştu.", "err"); return; }
      if (d.plan) renderPlan(d.plan);
      if (d.message) setMsg(d.message, "ok");
      if (d.hasOwnProperty("weak") || d.hasOwnProperty("tomorrow")) renderDrawer(d);
      else fetchDrawer();
      fetchGame();
      // FEATURE 1: freshly completed study block → ask difficulty.
      if (payload && payload.action === "done" && !payload.zen_abandon) maybeFeedback(d.plan, payload.id);
      if (after) after(d);
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  /* ============ FEATURE 1: Dynamic Topic Confidence ============ */
  var _feedbackBlockId = null;
  function maybeFeedback(plan, blockId){
    if (!plan || !plan.blocks || !blockId) return;
    if ($("feedback-overlay") && $("feedback-overlay").classList.contains("show")) return;
    var hit = null;
    plan.blocks.forEach(function(b){ if (b.id === blockId) hit = b; });
    if (hit && hit.type === "study" && hit.status === "done" && !hit.zen_abandon) openFeedbackModal(blockId);
  }
  function openFeedbackModal(blockId){
    _feedbackBlockId = blockId;
    var btns = document.querySelectorAll("#feedback-overlay .feedback-btns button");
    Array.prototype.forEach.call(btns, function(b){ b.disabled = false; });
    $("feedback-overlay").classList.add("show");
  }
  function closeFeedbackModal(){
    _feedbackBlockId = null;
    var o = $("feedback-overlay");
    if (o) o.classList.remove("show");
  }
  function showToast(text){
    var t = $("toast");
    if (!t) return;
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(t._h);
    t._h = setTimeout(function(){ t.classList.remove("show"); }, 2600);
  }
  function sendFeedback(difficulty){
    var id = _feedbackBlockId;
    if (!id) return;
    var btns = document.querySelectorAll("#feedback-overlay .feedback-btns button");
    Array.prototype.forEach.call(btns, function(b){ b.disabled = true; });
    fetch("/api/blocks/" + encodeURIComponent(id) + "/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ difficulty: difficulty })
    }).then(function(r){ return r.json(); }).then(function(d){
      Array.prototype.forEach.call(btns, function(b){ b.disabled = false; });
      if (d.status !== "success"){ setMsg(d.message || "Hata oluştu.", "err"); return; }
      closeFeedbackModal();
      showToast("Anladım! Programını ayarlayacağım.");
      if (d.plan) renderPlan(d.plan);
    }).catch(function(){
      Array.prototype.forEach.call(btns, function(b){ b.disabled = false; });
      setMsg("Bağlantı hatası.", "err");
    });
  }

  /* ============ FEATURE 2: Peak Performance Mapping ============ */
  function fetchPeak(){
    var box = $("peak-chart"), ins = $("peak-insight");
    if (!box) return;
    fetch("/api/insights/peak").then(function(r){ return r.json(); }).then(function(d){
      if (!d || d.status !== "success") return;
      box.innerHTML = "";
      var empty = !d.best_hours.length && !d.worst_hours.length;
      if (empty){
        box.innerHTML = "<div class='empty' style='padding:8px 2px;font-size:12px'>" + d.insight + "</div>";
        if (ins) ins.hidden = true;
        return;
      }
      (d.hourly_stats || []).forEach(function(s){
        var bar = document.createElement("div");
        bar.className = "peak-bar " + (s.rate > 70 ? "good" : (s.rate >= 40 ? "mid" : "bad"));
        bar.title = s.hour + ":00 — %" + s.rate + " tamamlama";
        var fill = document.createElement("i");
        fill.style.height = Math.max(4, Math.round(s.rate * 0.9)) + "px";
        var lab = document.createElement("b");
        lab.textContent = s.hour;
        bar.appendChild(fill);
        bar.appendChild(lab);
        box.appendChild(bar);
      });
      if (ins){ ins.textContent = d.insight; ins.hidden = false; }
    }).catch(function(){});
  }

  /* ============ FEATURE 4: Study Heatmap ============ */
  var _heatmapData = null;
  var _TR_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
  function heatmapColor(min){
    if (min >= 121) return "hm-4";
    if (min >= 61) return "hm-3";
    if (min >= 31) return "hm-2";
    if (min >= 1) return "hm-1";
    return "hm-0";
  }
  function heatmapTip(iso, min){
    var p = String(iso).split("-");
    var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
    return dt.getDate() + " " + _TR_MONTHS[dt.getMonth()] + " — " + min + " dk";
  }
  function renderHeatmap(){
    var grid = $("heatmap-grid");
    if (!grid || !_heatmapData) return;
    var weeks = (window.innerWidth && window.innerWidth < 768) ? 4 : 12;
    var data = _heatmapData.slice(-weeks * 7);
    grid.innerHTML = "";
    if (data.length){
      var first = new Date(data[0].day + "T12:00:00");
      var pad = (first.getDay() + 6) % 7; // Monday-first offset
      for (var i = 0; i < pad; i++){
        var ph = document.createElement("i");
        ph.className = "hm";
        ph.style.visibility = "hidden";
        grid.appendChild(ph);
      }
    }
    data.forEach(function(cell){
      var c = document.createElement("i");
      c.className = "hm " + heatmapColor(cell.minutes);
      c.title = heatmapTip(cell.day, cell.minutes);
      grid.appendChild(c);
    });
  }
  function fetchHeatmap(){
    if (!$("heatmap-grid")) return;
    fetch("/api/stats/heatmap").then(function(r){ return r.json(); }).then(function(d){
      if (!d || d.status !== "success" || !d.data) return;
      _heatmapData = d.data;
      renderHeatmap();
    }).catch(function(){});
  }
  var _hmResizeT = null;
  window.addEventListener("resize", function(){
    clearTimeout(_hmResizeT);
    _hmResizeT = setTimeout(renderHeatmap, 200);
  });

  /* ============ FEATURE 3: Ambient Focus Sounds ============
     Offline WebAudio synthesis (the Pixabay CDN URLs returned 403, so no
     remote file is used). rain/whitenoise/library are filtered-noise beds,
     lofi reuses the existing oscillator preset. Picker and the manual
     Lo-Fi/Deep-Space buttons share one channel: enabling one stops the
     other. Never throws; failures only console.warn. */
  var ambientNodes = null;
  function ambientNoiseBuffer(ctx, brown){
    var len = Math.floor(ctx.sampleRate * 2);
    var buf = ctx.createBuffer(1, len, ctx.sampleRate);
    var d = buf.getChannelData(0), last = 0;
    for (var i = 0; i < len; i++){
      var w = Math.random() * 2 - 1;
      if (brown){ last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; }
      else d[i] = w * 0.6;
    }
    return buf;
  }
  function currentAmbient(){
    return (appSettings && appSettings.ambient_sound) || "none";
  }
  function markAmbientButtons(){
    var cur = currentAmbient();
    Array.prototype.forEach.call(document.querySelectorAll("#zen-sound-picker button"), function(b){
      if (b.getAttribute("data-sound") === cur) b.classList.add("on");
      else b.classList.remove("on");
    });
  }
  function silenceManualOsc(){
    stopLoFi(); stopSpace();
    var zl = $("zen-lofi"), zs = $("zen-space");
    if (zl) zl.classList.remove("on");
    if (zs) zs.classList.remove("on");
  }
  function playAmbient(sound){
    stopAmbient();
    silenceManualOsc();
    if (!sound || sound === "none"){ markAmbientButtons(); return; }
    if (sound === "lofi"){ startLoFi(); markAmbientButtons(); return; }
    var ctx = ensureAudio();
    if (!ctx){ console.warn("[oztudy] ambient audio unavailable"); markAmbientButtons(); return; }
    try {
      var master = ctx.createGain();
      master.gain.value = (sound === "library") ? 0.05 : 0.12;
      master.connect(ctx.destination);
      var src = ctx.createBufferSource();
      src.buffer = ambientNoiseBuffer(ctx, sound !== "whitenoise");
      src.loop = true;
      var nodes = [src];
      if (sound === "rain"){
        var hp = ctx.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 300;
        var lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 1400;
        src.connect(hp); hp.connect(lp); lp.connect(master);
        var lfo = ctx.createOscillator(); lfo.type = "sine"; lfo.frequency.value = 0.4;
        var lg = ctx.createGain(); lg.gain.value = 0.03;
        lfo.connect(lg); lg.connect(master.gain); lfo.start(); nodes.push(lfo);
      } else if (sound === "library"){
        var lp2 = ctx.createBiquadFilter(); lp2.type = "lowpass"; lp2.frequency.value = 400;
        src.connect(lp2); lp2.connect(master);
      } else {
        src.connect(master);
      }
      src.start();
      ambientNodes = { master: master, nodes: nodes };
    } catch(err){ console.warn("[oztudy] ambient audio failed:", err); }
    markAmbientButtons();
  }
  function stopAmbient(){
    if (ambientNodes){
      ambientNodes.nodes.forEach(function(n){ try { n.stop(); } catch(e){} });
      try { ambientNodes.master.disconnect(); } catch(e){}
      ambientNodes = null;
    }
    var a = $("ambient-audio");
    if (a){ try { a.pause(); } catch(e){} }
  }
  function saveAmbient(sound){
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ambient_sound: sound })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success"){ appSettings = d.settings; markAmbientButtons(); }
    }).catch(function(){});
  }

  function applyTheme(theme){
    var root = document.documentElement;
    if (!root) return;
    if (theme === "dark") root.setAttribute("data-theme", "dark");
    else root.removeAttribute("data-theme");
  }

  /* Calm timer backgrounds (Beach/Forest/Space/None). The CSS ::before photo
     is fetched lazily — only when the data-timer-theme selector matches on
     open. A JS Image() preload on first open avoids a flash; "none" keeps
     the plain overlay. Same theme drives the Zen focus screen. */
  var TIMER_THEME_IMAGES = {
    beach: "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=1600&q=60&auto=format&fit=crop",
    forest: "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1600&q=60&auto=format&fit=crop",
    space: "https://images.unsplash.com/photo-1462331940025-496dfbfc7564?w=1600&q=60&auto=format&fit=crop"
  };
  var _timerThemePreloaded = {};
  function applyTimerTheme(theme){
    var ov = $("zen-overlay");
    if (!ov) return;
    if (!theme || theme === "none" || !TIMER_THEME_IMAGES[theme]){
      ov.removeAttribute("data-timer-theme");
      return;
    }
    if (!_timerThemePreloaded[theme] && typeof Image !== "undefined"){
      _timerThemePreloaded[theme] = true;
      try { var im = new Image(); im.src = TIMER_THEME_IMAGES[theme]; } catch(e){}
    }
    ov.setAttribute("data-timer-theme", theme);
  }

  function fillSettingsForm(st){
    if (!st) return;
    var s = $("set-study"), br = $("set-break"), wS = $("set-win-s"), wE = $("set-win-e"), H = $("set-hours");
    if (s) s.value = st.study_min;
    if (br) br.value = st.break_min;
    if (wS) wS.value = st.win_start;
    if (wE) wE.value = st.win_end;
    if (H) H.value = st.duration_h;
    var tt = $("set-timer-theme");
    if (tt) tt.value = (st.timer_theme && TIMER_THEME_IMAGES[st.timer_theme]) ? st.timer_theme : "none";
    document.querySelectorAll("input[name='set-theme']").forEach(function(r){ r.checked = (r.value === st.theme); });
  }

  function applySettingsToWizard(st){
    if (!st) return;
    if ($("win-s")) $("win-s").value = st.win_start;
    if ($("win-e")) $("win-e").value = st.win_end;
    duration = st.duration_h;
  }

  function loadSettings(){
    fetch("/api/settings").then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success") return;
      appSettings = d.settings;
      applyTheme(appSettings.theme);
      applyTimerTheme(appSettings.timer_theme);
      markAmbientButtons();
      fillSettingsForm(appSettings);
      applySettingsToWizard(appSettings);
    }).catch(function(){});
  }

  var MONTHS_TR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
  function fmtExamDate(ds, withYear){
    var p = String(ds || "").split("-");
    if (p.length !== 3) return ds || "";
    var m = parseInt(p[1], 10) - 1;
    var s = parseInt(p[2], 10) + " " + MONTHS_TR[(m >= 0 && m < 12) ? m : 0];
    if (withYear) s += " " + p[0];
    return s;
  }
  function renderExamCountdown(next){
    var card = $("next-exam");
    if (!card) return;
    if (!next){
      card.hidden = true;
      card.className = "next-exam";
      return;
    }
    card.hidden = false;
    card.className = "next-exam show" + (next.ongoing ? " ongoing" : "");
    var t = $("ne-title"), r = $("ne-range"), c = $("ne-count");
    if (t) t.textContent = next.ongoing ? "Sınav bu hafta: " + next.label : next.label;
    if (r) r.textContent = fmtExamDate(next.start) + " – " + fmtExamDate(next.end, true);
    if (c) c.textContent = next.ongoing ? "BU HAFTA" : (next.days_until + " gün");
  }
  function fillExamsForm(exams){
    var box = $("set-exams");
    if (!box || !exams) return;
    box.value = exams.map(function(w){
      return String(w.start) + " " + String(w.end) + " " + String(w.label || "");
    }).join("\n");
  }
  function parseExamsForm(){
    var box = $("set-exams");
    var out = [];
    if (!box || !box.value.trim()) return out;
    box.value.split("\n").forEach(function(line){
      line = line.trim();
      if (!line) return;
      var parts = line.split(/\s+/);
      if (parts.length < 2) return;
      var start = parts[0], end = parts[1];
      if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) return;
      var label = parts.slice(2).join(" ");
      out.push({ start: start, end: end, label: label || "" });
    });
    return out;
  }
  function loadExams(){
    fetch("/api/exams").then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success") return;
      renderExamCountdown(d.next);
      fillExamsForm(d.exams);
    }).catch(function(){});
  }
  function saveExamsFromForm(){
    var exams = parseExamsForm();
    if (!exams.length) return;
    fetch("/api/exams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exams: exams })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){ setMsg(d.message || "Sınavlar kaydedilemedi.", "err"); return; }
      renderExamCountdown(d.next);
      fillExamsForm(d.exams);
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  var HABIT_DAY_NAMES = ["Pzr", "Pzt", "Sal", "Çar", "Prş", "Cum", "Cmt"];
  var habitState = null;
  function habitIconHtml(h){
    var ic = (h && h.icon) || "flag";
    if (["gym", "moon", "book", "flag"].indexOf(ic) === -1) ic = "flag";
    return '<svg class="ic"><use href="#i-' + ic + '"/></svg>';
  }
  function renderHabits(data){
    var list = $("habits-list");
    if (!list) return;
    var habits = (data && data.habits) || [];
    var track = (data && data.track) || {};
    var days = (data && data.days) || [];
    var streaks = (data && data.streaks) || {};
    var today = (data && data.today) || localDayKey();
    list.innerHTML = "";
    if (!habits.length){
      var e = document.createElement("div");
      e.className = "empty";
      e.textContent = "Alışkanlık eklemek için Ayarlar'a gidin.";
      list.appendChild(e);
      return;
    }
    var hrow = document.createElement("div");
    hrow.className = "habit-row head";
    var hmeta = document.createElement("div");
    hmeta.className = "hmeta";
    hmeta.style.visibility = "hidden";
    var hsp = document.createElement("span");
    hsp.className = "hname";
    hsp.textContent = ".";
    hmeta.appendChild(hsp);
    hrow.appendChild(hmeta);
    var hright = document.createElement("div");
    hright.className = "hright";
    var hgrid = document.createElement("div");
    hgrid.className = "hgrid";
    days.forEach(function(ds){
      var dt = new Date(ds + "T12:00:00");
      var k = document.createElement("span");
      k.className = "hk" + (ds === today ? " today" : "");
      k.textContent = HABIT_DAY_NAMES[(dt.getDay() % 7)];
      hgrid.appendChild(k);
    });
    hright.appendChild(hgrid);
    var hb = document.createElement("span");
    hb.className = "hb";
    hb.textContent = "Bugün";
    hright.appendChild(hb);
    hrow.appendChild(hright);
    list.appendChild(hrow);

    var doneToday = 0;
    habits.forEach(function(h){
      var row = document.createElement("div");
      row.className = "habit-row";
      var meta = document.createElement("div");
      meta.className = "hmeta";
      var chip = document.createElement("span");
      chip.className = "hico";
      chip.style.background = h.color || "var(--primary)";
      chip.innerHTML = habitIconHtml(h);
      var nm = document.createElement("span");
      nm.className = "hname";
      nm.textContent = h.label;
      meta.appendChild(chip);
      meta.appendChild(nm);
      if (h.time){
        var tm = document.createElement("span");
        tm.className = "httime";
        tm.textContent = h.time;
        meta.appendChild(tm);
      }
      var sp = document.createElement("span");
      sp.className = "hstreak";
      sp.textContent = (streaks[h.id] || 0) + " gün";
      meta.appendChild(sp);
      row.appendChild(meta);
      var right = document.createElement("div");
      right.className = "hright";
      var grid = document.createElement("div");
      grid.className = "hgrid";
      var isTodayDone = false;
      days.forEach(function(ds){
        var on = track[ds] && track[ds].indexOf(h.id) !== -1;
        if (ds === today && on) isTodayDone = true;
        var c = document.createElement("span");
        c.className = "hc" + (on ? " on" : "") + (ds === today ? " today" : "");
        if (on){
          c.style.background = h.color || "var(--primary)";
          c.style.borderColor = h.color || "var(--primary)";
        } else {
          c.style.background = "transparent";
          c.style.borderColor = h.color || "var(--border)";
        }
        grid.appendChild(c);
      });
      right.appendChild(grid);
      var btn = document.createElement("button");
      btn.type = "button";
      if (isTodayDone){
        btn.className = "htoday on";
        btn.textContent = "Yapıldı";
      } else {
        btn.className = "htoday";
        btn.textContent = "Bugünü İşaretle";
      }
      btn.title = "Bugün " + h.label + " yaptığını işaretle";
      btn.addEventListener("click", function(){ toggleHabit(h.id, today); });
      right.appendChild(btn);
      row.appendChild(right);
      list.appendChild(row);
      if (isTodayDone) doneToday++;
    });
    var sub = $("habits-sub");
    if (sub){
      sub.textContent = "bugün " + doneToday + "/" + habits.length;
      sub.classList.toggle("all", habits.length > 0 && doneToday === habits.length);
    }
  }
  function fillHabitsForm(habits){
    var box = $("set-habits");
    if (!box) return;
    box.value = (habits || []).map(function(h){
      return (h.label || "") + (h.time ? " " + h.time : "");
    }).join("\n");
  }
  function parseHabitsForm(){
    var box = $("set-habits");
    var out = [];
    if (!box || !box.value.trim()) return out;
    box.value.split("\n").forEach(function(line){
      line = line.trim();
      if (!line) return;
      var m = line.match(/^(.+?)\s+(\d{2}:\d{2})$/);
      if (m) out.push({ label: m[1].trim(), time: m[2] });
      else out.push({ label: line, time: "" });
    });
    return out;
  }
  function loadHabits(){
    fetch("/api/habits").then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success") return;
      habitState = { habits: d.habits, track: d.track, days: d.days, today: d.today, streaks: d.streaks };
      renderHabits(habitState);
      fillHabitsForm(d.habits);
    }).catch(function(){});
  }
  function toggleHabit(id, date){
    if (!id || !date) return;
    fetch("/api/habits/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id, date: date })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success") return;
      if (habitState){
        habitState.track = d.track;
        habitState.streaks = d.streaks;
        habitState.today = d.today;
      }
      renderHabits(habitState);
    }).catch(function(){});
  }
  function saveHabitsFromForm(){
    var habits = parseHabitsForm();
    if (!habits.length) return;
    fetch("/api/habits/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ habits: habits })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){ setMsg(d.message || "Alışkanlıklar kaydedilemedi.", "err"); return; }
      habitState = { habits: d.habits, track: d.track, days: d.days, today: d.today, streaks: d.streaks };
      renderHabits(habitState);
      fillHabitsForm(d.habits);
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  function notify(title, body){
    if ("Notification" in window && Notification.permission === "granted"){
      try { new Notification(title, { body: body, icon: "/static/icon-192.png" }); } catch(e){}
    }
  }
  var habitNotified = {};
  function habitReminderTick(){
    if (!habitState || !(habitState.habits || []).length) return;
    var now = new Date();
    var nowMin = now.getHours() * 60 + now.getMinutes();
    var today = localDayKey(now);
    habitState.habits.forEach(function(h){
      if (!h.time) return;
      var m = h.time.match(/^(\d{2}):(\d{2})$/);
      if (!m) return;
      var t = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
      var done = (habitState.track[today] || []).indexOf(h.id) !== -1;
      var key = today + "|" + h.id;
      if (done || habitNotified[key]) return;
      if (nowMin >= t && nowMin <= t + 30){
        habitNotified[key] = true;
        notify("Alışkanlık Saati", h.label + " " + h.time + " — yaptıysan bugün sütununa işaretle.");
      }
    });
  }
  setInterval(habitReminderTick, 30000);

  function rebuildPlanWithSettings(){
    if (!lastTopics || !lastTopics.length){
      setMsg("Plan yenilemek için önce bir plan oluştur.", "err");
      return;
    }
    setMsg("Ayarlara göre plan yenileniyor…");
    fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topics: lastTopics,
        start: $("win-s").value,
        end: $("win-e").value,
        duration_h: duration,
        mode: "practice",
        criterion: "A"
      })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){ setMsg(d.message || "Hata oluştu.", "err"); return; }
      setMsg("✓ Plan, kişisel ayarlarla yenilendi.", "ok");
      renderPlan(d.plan);
      fetchDrawer();
      fetchGame();
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  function saveSettings(){
    var st = { theme: "light" };
    var r = document.querySelector("input[name='set-theme']:checked");
    if (r) st.theme = r.value;
    var s = $("set-study"), br = $("set-break"), wS = $("set-win-s"), wE = $("set-win-e"), H = $("set-hours");
    st.study_min = parseInt(s ? s.value : 45, 10) || 45;
    st.break_min = parseInt(br ? br.value : 10, 10) || 10;
    var prevWin = (appSettings && appSettings.win_start + appSettings.win_end) || "";
    var prevDur = (appSettings && appSettings.duration_h) || 0;
    var prevStudy = appSettings ? appSettings.study_min : 45;
    var prevBreak = appSettings ? appSettings.break_min : 10;
    st.win_start = wS ? wS.value : "17:00";
    st.win_end = wE ? wE.value : "21:00";
    st.duration_h = parseInt(H ? H.value : 4, 10) || 4;
    var tts = $("set-timer-theme");
    st.timer_theme = (tts && tts.value) || "none";
    if (TIMER_THEME_IMAGES[st.timer_theme] === undefined && st.timer_theme !== "none") st.timer_theme = "none";
    var changed = st.study_min !== prevStudy || st.break_min !== prevBreak ||
                  (st.win_start + st.win_end) !== prevWin || st.duration_h !== prevDur;
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(st)
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){ setMsg("Ayarlar kaydedilemedi.", "err"); return; }
      appSettings = d.settings;
      applyTheme(appSettings.theme);
      applyTimerTheme(appSettings.timer_theme);
      markAmbientButtons();
      fillSettingsForm(appSettings);
      applySettingsToWizard(appSettings);
      if (changed){ rebuildPlanWithSettings(); }
      else setMsg("✓ Ayarlar kaydedildi.", "ok");
      saveExamsFromForm();
      saveHabitsFromForm();
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  $("shift15").addEventListener("click", function(){ adjust({ action: "shift", offset: 15 }); });
  $("shift30").addEventListener("click", function(){ adjust({ action: "shift", offset: 30 }); });
  var _rb = $("rb-add"); if (_rb) _rb.addEventListener("click", function(){ scheduleReviews(); });
  var _cb = $("cb-btn"); if (_cb) _cb.addEventListener("click", function(){ activateCatchUp(); });
  var _overlay = $("overlay");
  var _mclose = $("m-close");
  if (_mclose) _mclose.addEventListener("click", function(){ if (_overlay) _overlay.classList.remove("show"); });
  if (_overlay) _overlay.addEventListener("click", function(e){ if (e.target === _overlay) _overlay.classList.remove("show"); });
  var _tabW = $("tab-wiz"), _tabS = $("tab-stat");
  if (_tabW) _tabW.addEventListener("click", function(){ switchTab("wiz"); });
  if (_tabS) _tabS.addEventListener("click", function(){ switchTab("stat"); });
  var _tabR = $("tab-report");
  if (_tabR) _tabR.addEventListener("click", function(){ switchTab("report"); });
  var _rc = $("report-collapse");
  if (_rc) _rc.addEventListener("click", function(){ switchTab("wiz"); });
  var _rpr = $("report-print");
  if (_rpr) _rpr.addEventListener("click", function(){ window.print(); });
  Array.prototype.forEach.call(document.querySelectorAll("#rp-pills .rp-pill"), function(p){
    p.addEventListener("click", function(){
      Array.prototype.forEach.call(document.querySelectorAll("#rp-pills .rp-pill"), function(q){ q.classList.remove("active"); });
      p.classList.add("active");
      reportRange = p.getAttribute("data-range") || "week";
      fetchReport();
    });
  });
  var _tabSet = $("tab-settings");
  if (_tabSet) _tabSet.addEventListener("click", function(){ switchTab("settings"); });
  /* FEATURE 1 wiring: difficulty buttons + skip + backdrop dismiss (no save). */
  Array.prototype.forEach.call(document.querySelectorAll("#feedback-overlay .feedback-btns button"), function(b){
    b.addEventListener("click", function(){ sendFeedback(b.getAttribute("data-difficulty")); });
  });
  var _fbSkip = $("feedback-skip");
  if (_fbSkip) _fbSkip.addEventListener("click", closeFeedbackModal);
  var _fbOverlay = $("feedback-overlay");
  if (_fbOverlay) _fbOverlay.addEventListener("click", function(e){ if (e.target === _fbOverlay) closeFeedbackModal(); });
  /* FEATURE 3 wiring: ambient picker + load-failure warning (never crashes). */
  Array.prototype.forEach.call(document.querySelectorAll("#zen-sound-picker button"), function(b){
    b.addEventListener("click", function(){
      var sound = b.getAttribute("data-sound") || "none";
      playAmbient(sound);
      saveAmbient(sound);
    });
  });
  var _amb = $("ambient-audio");
  if (_amb) _amb.addEventListener("error", function(){ console.warn("[oztudy] ambient audio failed to load"); });
  markAmbientButtons();  var _setClose = $("settings-close");
  if (_setClose) _setClose.addEventListener("click", function(){ switchTab("wiz"); });
  var _setSave = $("settings-save");
  if (_setSave) _setSave.addEventListener("click", function(){ saveSettings(); });
  var _expBtn = $("export-btn");
  if (_expBtn) _expBtn.addEventListener("click", function(){
    fetch("/api/export").then(function(r){ return r.json(); }).then(function(d){
      if (!d || d.status !== "success") { setMsg("Export başarısız.", "err"); return; }
      var blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
      var a = document.createElement("a");
      var day = (d.exported_at || new Date().toISOString().slice(0, 10));
      a.href = URL.createObjectURL(blob);
      a.download = "oztudy-export-" + day + ".json";
      document.body.appendChild(a);
      a.click();
      setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 500);
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  });
  var _sc = $("stats-collapse");
  if (_sc) _sc.addEventListener("click", function(){ switchTab("wiz"); });
  var _zlofi = $("zen-lofi");
  if (_zlofi) _zlofi.addEventListener("click", function(){
    var on = _zlofi.classList.toggle("on");
    // Manual preset and picker share one channel: enabling one silences the other.
    stopAmbient();
    if (appSettings) appSettings.ambient_sound = "none";
    markAmbientButtons();
    if (on){ stopSpace(); if ($("zen-space")) $("zen-space").classList.remove("on"); startLoFi(); } else stopLoFi();
  });
  var _zspace = $("zen-space");
  if (_zspace) _zspace.addEventListener("click", function(){
    var on = _zspace.classList.toggle("on");
    stopAmbient();
    if (appSettings) appSettings.ambient_sound = "none";
    markAmbientButtons();
    if (on){ stopLoFi(); if ($("zen-lofi")) $("zen-lofi").classList.remove("on"); startSpace(); } else stopSpace();
  });
  var _zab = $("zen-abandon");
  if (_zab) _zab.addEventListener("click", function(){
    if (!zenActive || !zenBlockId) return;
    var blocks = lastPlan || [];
    var blk = null;
    blocks.forEach(function(x){ if (x.id === zenBlockId) blk = x; });
    if (blk){ zenAbandonPending = blk; openZenConfirm(); }
  });
  var _zpause = $("zen-pause");
  if (_zpause) _zpause.addEventListener("click", function(){
    if (!zenActive || !zenBlockId) return;
    var blocks = lastPlan || [];
    var blk = null;
    blocks.forEach(function(x){ if (x.id === zenBlockId) blk = x; });
    if (blk) localStop(blk);
  });
  var _zco = $("zen-confirm-ok");
  if (_zco) _zco.addEventListener("click", function(){ doAbandonZen(); });
  var _zcc = $("zen-confirm-cancel");
  if (_zcc) _zcc.addEventListener("click", function(){ closeZenConfirm(); });
  var _zok = $("zen-btn-ok");
  if (_zok) _zok.addEventListener("click", function(){
    $("zen-splash").hidden = true;
  });
  var _rst = $("stats-reset");
  if (_rst) _rst.addEventListener("click", function(){
    var ok = typeof window.confirm === "function" && window.confirm(
      "Tüm istatistik verileri silinsin mi?\nSoru, sayfa ve çalışma süresi kayıtları sıfırlanır; timeline etkilenmez."
    );
    if (!ok) return;
    fetch("/api/stats/reset", { method: "POST" }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success"){
        setMsg("✓ İstatistikler sıfırlandı.", "ok");
        fetch("/api/plan").then(function(r){ return r.json(); }).then(function(p){
          if (p.status === "success") renderPlan(p.plan);
          refreshStats();
        }).catch(function(){ refreshStats(); });
      }
      else setMsg(d.message || "Hata oluştu.", "err");
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  });

  function postBlocks(payload){
    fetch("/api/blocks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status !== "success"){
        if (payload.clear) renderPlan(null);
        return;
      }
      if (payload.clear) renderPlan(null);
      else renderPlan(d.plan);
      // FEATURE 1: status-toggled study block → ask difficulty when done.
      if (payload && payload.status === "done") maybeFeedback(d.plan, payload.id);
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  }

  $("go").addEventListener("click", function(){
    if (!selected.length){ setMsg("Önce en az bir konu seç.", "err"); return; }
    var start = $("win-s").value, end = $("win-e").value;
    var s = parseInt(start.split(":")[0], 10) * 60 + parseInt(start.split(":")[1], 10);
    var e = parseInt(end.split(":")[0], 10) * 60 + parseInt(end.split(":")[1], 10);
    if (e <= s){ setMsg("Bitiş saati başlangıçtan sonra olmalı.", "err"); return; }
    var goBtn = $("go");
    if (goBtn) goBtn.disabled = true;
    setMsg("Plan oluşturuluyor…");
    fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topics: selected,
        start: start,
        end: end,
        duration_h: duration,
        mode: mode,
        criterion: criterion
      })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (goBtn) goBtn.disabled = false;
      if (d.status !== "success"){ setMsg(d.message || "Hata oluştu.", "err"); return; }
      setMsg("✓ Plan hazır · " + d.plan.created, "ok");
      renderPlan(d.plan);
      renderDrawer(d);
    }).catch(function(){ if (goBtn) goBtn.disabled = false; setMsg("Bağlantı hatası.", "err"); });
  });

  $("reset").addEventListener("click", function(){
    if ((lastPlan && lastPlan.length) && typeof window.confirm === "function" &&
        !window.confirm("Aktif plan silinsin mi?\nZaman çizelgesi temizlenir; geçmiş ve istatistikler korunur.")) return;
    selected.forEach(function(it){ var p = findPill(it.subject, it.topic); if (p) p.classList.remove("active"); });
    selected = [];
    rebuildChips();
    $("win-s").value = "17:00";
    $("win-e").value = "19:00";
    duration = 2;
    syncPillFromTimes();
    mode = "practice";
    document.querySelectorAll("#mode-pills .pill").forEach(function(p){ p.classList.toggle("active", p.dataset.mode === mode); });
    criterion = "A";
    document.querySelectorAll("#cri-pills .pill").forEach(function(p){ p.classList.toggle("active", p.dataset.cri === criterion); });
    updateDefaultText();
    fetch("/api/blocks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear: true })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.status === "success"){
        renderPlan(null);
        setMsg("Plan ve seçimler sıfırlandı.", "ok");
      } else {
        setMsg(d.message || "Plan korundu.", "ok");
      }
    }).catch(function(){ setMsg("Bağlantı hatası.", "err"); });
  });

  renderStreak();
  if ("Notification" in window && Notification.permission === "default"){
    Notification.requestPermission();
  }
  document.body.addEventListener("click", function(){ ensureAudio(); });
  fetch("/api/plan").then(function(r){ return r.json(); }).then(function(d){
    if (d.status === "success"){ renderPlan(d.plan); restoreTimerState(); }
  }).catch(function(){ try { restoreTimerState(); } catch(e){} });
  fetchDrawer();
  fetchReviews();
  fetchOverdue();
  fetchGame();
  fetchSchool();
  fetchVersion();
  loadSettings();
  loadExams();
  loadHabits();
  rebuildChips();
})();
