(function () {
  var rows=document.getElementById("watchlist-rows"); if(!rows)return;
  var search=document.getElementById("watchlist-search"),status=document.getElementById("watchlist-status"),previous=document.getElementById("watchlist-previous"),next=document.getElementById("watchlist-next"),pageStatus=document.getElementById("watchlist-page-status"),entries=[],page=1,pageSize=8;
  function textCell(row,value,className){var cell=document.createElement("td");if(className)cell.className=className;cell.textContent=value==null||value===""?"—":String(value);row.appendChild(cell);return cell;}
  function titleOf(entry){return entry.english_name||entry.romaji_name||entry.native_name||"Untitled";}
  function visiblePageSize(){var top=rows.getBoundingClientRect().top,available=window.innerHeight-top-145;return Math.max(3,Math.floor(available/59));}
  function render(){
    var term=search.value.trim().toLowerCase(),wanted=status.value;
    var visible=entries.filter(function(entry){var haystack=[titleOf(entry),entry.romaji_name,entry.native_name,entry.provider,entry.provider_item_id].join(" ").toLowerCase();return(!term||haystack.indexOf(term)!==-1)&&(!wanted||entry.list_status===wanted);});
    pageSize=visiblePageSize();var pages=Math.max(1,Math.ceil(visible.length/pageSize));page=Math.max(1,Math.min(page,pages));var pageEntries=visible.slice((page-1)*pageSize,page*pageSize);rows.textContent="";
    if(!pageEntries.length){var empty=document.createElement("tr");textCell(empty,"No matching watchlist entries.","muted").colSpan=6;rows.appendChild(empty);}
    pageEntries.forEach(function(entry){
      var row=document.createElement("tr"),title=document.createElement("td");title.className="watchlist-title";var strong=document.createElement("strong");strong.textContent=titleOf(entry);var sub=document.createElement("span");sub.textContent=entry.romaji_name&&entry.romaji_name!==strong.textContent?entry.romaji_name:(entry.release_date||"");title.appendChild(strong);title.appendChild(sub);row.appendChild(title);
      var provider=textCell(row,entry.provider+" · "+entry.provider_item_id,"provider-item");provider.dataset.provider=entry.provider;
      textCell(row,entry.media_format||"Unknown");textCell(row,entry.list_status);textCell(row,String(entry.progress||0)+(entry.episode_count!=null?" / "+entry.episode_count:""));textCell(row,entry.release_date||"—");rows.appendChild(row);
    });
    pageStatus.textContent="Page "+page+" of "+pages+" · "+visible.length+" items";previous.disabled=page<=1;next.disabled=page>=pages;
  }
  search.addEventListener("input",function(){page=1;render();});status.addEventListener("change",function(){page=1;render();});previous.addEventListener("click",function(){if(page>1){page-=1;render();}});next.addEventListener("click",function(){page+=1;render();});window.addEventListener("resize",render);
  fetch("/api/watchlist/items",{headers:{"Accept":"application/json"}}).then(function(response){if(!response.ok)throw new Error("Could not load watchlist table");return response.json();}).then(function(payload){entries=payload.entries||[];render();}).catch(function(error){rows.textContent="";var row=document.createElement("tr");textCell(row,error.message,"muted").colSpan=7;rows.appendChild(row);});
}());
