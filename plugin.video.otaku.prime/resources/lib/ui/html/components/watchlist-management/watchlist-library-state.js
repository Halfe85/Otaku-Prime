(function(){
  function label(entry){
    if(Number(entry.added_to_library||0)===1) return {text:"Added to library",kind:"added"};
    if(Number(entry.mediator_ready||0)===1) return {text:"Ready for library",kind:"ready"};
    return {text:"Processing",kind:"processing"};
  }
  function decorate(entries){
    var byId={};
    (entries||[]).forEach(function(entry){byId[String(entry.local_id)]=entry;});
    document.querySelectorAll("tr[data-watchlist-item]").forEach(function(row){
      var entry=byId[String(row.dataset.watchlistItem)];
      if(!entry) return;
      var cell=row.querySelector(".watchlist-title");
      if(!cell) return;
      var tag=cell.querySelector(".watchlist-library-tag");
      if(!tag){tag=document.createElement("span");tag.className="watchlist-library-tag";cell.appendChild(tag);}
      var state=label(entry);
      tag.className="watchlist-library-tag "+state.kind;
      tag.textContent=state.text;
      if(entry.special_locator){tag.title="Simkl reference "+(entry.simkl_reference_id||"—")+" · "+entry.special_locator;}
    });
  }
  function load(){
    fetch("/api/watchlist/items",{headers:{"Accept":"application/json"}})
      .then(function(response){if(!response.ok) throw new Error("watchlist state unavailable");return response.json();})
      .then(function(payload){
        decorate(payload.entries||[]);
        var root=document.getElementById("watchlist-rows");
        if(root){new MutationObserver(function(){decorate(payload.entries||[]);}).observe(root,{childList:true,subtree:true});}
      }).catch(function(){/* Main watchlist UI owns user-visible request errors. */});
  }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",load,{once:true}); else load();
}());