(function(){
  "use strict";
  var latest=[];
  var busy=false;
  var stopped=false;
  var timer=null;

  function active(){
    var panel=document.getElementById("panel-watchlist-management");
    return !stopped&&!document.hidden&&panel&&!panel.hidden;
  }
  function label(entry){
    if(Number(entry.added_to_library||0)===1) return {text:"Added to library",kind:"added"};
    if(Number(entry.mediator_ready||0)===1) return {text:"Ready for library",kind:"ready"};
    return {text:"Processing",kind:"processing"};
  }
  function decorate(entries){
    latest=entries||latest; var byId={};
    latest.forEach(function(entry){byId[String(entry.local_id)]=entry;});
    document.querySelectorAll("tr[data-watchlist-item]").forEach(function(row){
      var entry=byId[String(row.dataset.watchlistItem)]; if(!entry) return;
      var cell=row.querySelector(".watchlist-title"); if(!cell) return;
      var tag=cell.querySelector(".watchlist-library-tag");
      if(!tag){tag=document.createElement("span");tag.className="watchlist-library-tag";cell.appendChild(tag);}
      var state=label(entry);
      var className="watchlist-library-tag "+state.kind;
      var title=entry.special_locator ? "Simkl reference "+(entry.simkl_reference_id||"—")+" · "+entry.special_locator : "";
      if(tag.className!==className) tag.className=className;
      if(tag.textContent!==state.text) tag.textContent=state.text;
      if(tag.title!==title) tag.title=title;
    });
  }
  function refresh(){
    if(!active()||busy) return;
    busy=true;
    var controller=typeof AbortController!=="undefined"?new AbortController():null;
    var timeout=window.setTimeout(function(){if(controller) controller.abort();},5000);
    fetch("/api/watchlist/states",{
      headers:{"Accept":"application/json"},cache:"no-store",
      signal:controller?controller.signal:undefined
    })
      .then(function(response){if(!response.ok) throw new Error("watchlist state unavailable");return response.json();})
      .then(function(payload){
        var entries=payload.entries||[];
        decorate(entries);
        window.dispatchEvent(new CustomEvent("prime:watchlist-state",{detail:{entries:entries}}));
      })
      .catch(function(){/* Main watchlist UI owns user-visible request errors. */})
      .finally(function(){window.clearTimeout(timeout);busy=false;});
  }
  function start(){
    window.addEventListener("prime:watchlist-rendered",function(){decorate(latest);});
    window.addEventListener("prime:tabchange",function(event){
      if(event.detail&&event.detail.id==="watchlist-management") refresh();
    });
    document.addEventListener("visibilitychange",function(){if(active()) refresh();});
    window.addEventListener("beforeunload",function(){stopped=true;if(timer) window.clearInterval(timer);});
    refresh(); timer=window.setInterval(refresh,15000);
  }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",start,{once:true}); else start();
}());
