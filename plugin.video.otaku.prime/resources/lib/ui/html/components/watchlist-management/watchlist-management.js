(function () {
  var rows=document.getElementById("watchlist-rows"); if(!rows)return;
  var search=document.getElementById("watchlist-search"),status=document.getElementById("watchlist-status"),message=document.getElementById("watchlist-message"),previous=document.getElementById("watchlist-previous"),next=document.getElementById("watchlist-next"),pageStatus=document.getElementById("watchlist-page-status"),entries=[],page=1,pageSize=8;
  function statuses(entry){return String(entry.provider_statuses||"").split(",").filter(Boolean);}
  function visiblePageSize(){var top=rows.getBoundingClientRect().top,available=window.innerHeight-top-145;return Math.max(3,Math.floor(available/59));}
  function render(){
    var term=search.value.trim().toLowerCase(),wanted=status.value;
    var visible=entries.filter(function(entry){var title=(entry.english_name||entry.romaji_name||entry.franchise_name||"").toLowerCase();return(!term||title.indexOf(term)!==-1)&&(!wanted||statuses(entry).some(function(value){return value.split(":")[1]===wanted;}));});
    pageSize=visiblePageSize();var pages=Math.max(1,Math.ceil(visible.length/pageSize));page=Math.min(page,pages);var pageEntries=visible.slice((page-1)*pageSize,page*pageSize);
    rows.textContent="";
    if(!pageEntries.length){var empty=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=7;cell.className="muted";cell.textContent="No matching watchlist entries.";empty.appendChild(cell);rows.appendChild(empty);}
    pageEntries.forEach(function(entry){
      var row=document.createElement("tr"),title=document.createElement("td");title.className="watchlist-title";
      var strong=document.createElement("strong");strong.textContent=entry.english_name||entry.romaji_name||"Untitled";var sub=document.createElement("span");sub.textContent=entry.franchise_name||"";title.appendChild(strong);title.appendChild(sub);row.appendChild(title);
      [String(entry.media_category||entry.media_format||"tv").replace("_"," "),entry.kodi_season_number===0?"00":entry.season_number,statuses(entry).map(function(value){return value.split(":")[1];}).join(", "),String(entry.watched_episodes)+" / "+String(entry.episode_count)].forEach(function(value){var cell=document.createElement("td");cell.textContent=value;row.appendChild(cell);});
      var providers=document.createElement("td"),pills=document.createElement("div");pills.className="provider-pills";String(entry.providers||"").split(",").filter(Boolean).forEach(function(value){var pill=document.createElement("span");pill.textContent=value;pills.appendChild(pill);});providers.appendChild(pills);row.appendChild(providers);
      var action=document.createElement("td"),toggle=document.createElement("input");toggle.type="checkbox";toggle.className="watch-toggle";toggle.checked=Boolean(entry.watched);toggle.setAttribute("aria-label","Mark "+strong.textContent+" watched");toggle.addEventListener("change",function(){setWatched(entry,toggle);});action.appendChild(toggle);row.appendChild(action);rows.appendChild(row);
    });
    pageStatus.textContent="Page "+page+" of "+pages;previous.disabled=page<=1;next.disabled=page>=pages;
  }
  function setWatched(entry,toggle){toggle.disabled=true;fetch("/api/watchlist/series/watch-status",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({local_id:entry.local_id,watched:toggle.checked})}).then(function(response){if(!response.ok)throw new Error("Could not update watch status");return response.json();}).then(function(payload){entry.watched=payload.watched?1:0;message.hidden=false;message.textContent="Watch status updated.";}).catch(function(error){toggle.checked=!toggle.checked;message.hidden=false;message.textContent=error.message;}).finally(function(){toggle.disabled=false;});}
  search.addEventListener("input",function(){page=1;render();});status.addEventListener("change",function(){page=1;render();});
  previous.addEventListener("click",function(){if(page>1){page-=1;render();}});next.addEventListener("click",function(){page+=1;render();});
  window.addEventListener("resize",function(){render();});
  fetch("/api/watchlist/series",{headers:{"Accept":"application/json"}}).then(function(response){if(!response.ok)throw new Error("Could not load watchlist");return response.json();}).then(function(payload){entries=payload.entries||[];render();}).catch(function(error){rows.innerHTML="<tr><td colspan=\"7\" class=\"muted\"></td></tr>";rows.querySelector("td").textContent=error.message;});
}());
