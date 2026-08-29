(function () {
  var toggle=document.getElementById("log-toggle");
  var close=document.getElementById("app-log-close");
  var windowElement=document.getElementById("app-log-window");
  var messages=document.getElementById("app-log-messages");
  var state=document.getElementById("app-log-state");
  var jump=document.getElementById("app-log-jump");
  if (!toggle || !windowElement || !messages || !jump) return;
  var lastId=0,timer=null,unreadBelow=false;
  function atBottom() {
    return messages.scrollHeight-messages.scrollTop-messages.clientHeight<=24;
  }
  function setUnreadBelow(value) {
    unreadBelow=Boolean(value);
    jump.hidden=!unreadBelow;
  }
  function scrollToNewest(behavior) {
    messages.scrollTo({top:messages.scrollHeight,behavior:behavior||"auto"});
    setUnreadBelow(false);
  }
  function append(entry) {
    var item=document.createElement("article"); item.className="log-message "+String(entry.level||"").toLowerCase();
    var meta=document.createElement("div"); meta.className="meta";
    [entry.created_at,entry.level,entry.source].forEach(function(value){var span=document.createElement("span");span.textContent=value||"";meta.appendChild(span);});
    var text=document.createElement("p"); text.textContent=entry.message||""; item.appendChild(meta);item.appendChild(text);messages.appendChild(item);
    lastId=Math.max(lastId,Number(entry.id)||0);
  }
  function poll() {
    fetch("/api/logs?after="+lastId,{headers:{"Accept":"application/json"}}).then(function(response){
      if (!response.ok) throw new Error("Log connection unavailable"); return response.json();
    }).then(function(payload){
      var entries=payload.entries||[];
      var shouldFollow=atBottom();
      entries.forEach(append);state.textContent="Live updates connected";
      if (entries.length) {
        if (shouldFollow) scrollToNewest("auto");
        else setUnreadBelow(true);
      }
    }).catch(function(error){state.textContent=error.message;});
  }
  function openLogs(){windowElement.classList.add("open");windowElement.setAttribute("aria-hidden","false");toggle.setAttribute("aria-expanded","true");poll();timer=setInterval(poll,3000);}
  function closeLogs(){windowElement.classList.remove("open");windowElement.setAttribute("aria-hidden","true");toggle.setAttribute("aria-expanded","false");state.textContent="Live updates paused";clearInterval(timer);timer=null;}
  toggle.addEventListener("click",function(){windowElement.classList.contains("open")?closeLogs():openLogs();});
  close.addEventListener("click",closeLogs);
  jump.addEventListener("click",function(){scrollToNewest("smooth");messages.focus({preventScroll:true});});
  messages.addEventListener("scroll",function(){if(unreadBelow&&atBottom())setUnreadBelow(false);},{passive:true});
  document.addEventListener("keydown",function(event){if(event.key==="Escape")closeLogs();});
}());
