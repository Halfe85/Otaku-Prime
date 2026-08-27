(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-tab]"));
  var search = document.getElementById("prime-search");
  var searchContexts = { "watchlist-management": "Search watchlist titles" };
  function selectTab(id) {
    tabs.forEach(function (tab) {
      var selected = tab.getAttribute("data-tab") === id;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      document.getElementById("panel-" + tab.getAttribute("data-tab")).hidden = !selected;
    });
    if (search) {
      search.value = "";
      search.disabled = !searchContexts[id];
      search.placeholder = searchContexts[id] || "Search current view";
      search.dataset.context = id;
      window.dispatchEvent(new CustomEvent("prime:search", { detail: { context: id, value: "" } }));
    }
    if (history.replaceState) history.replaceState(null, "", "#" + id);
  }
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () { selectTab(tab.getAttribute("data-tab")); });
  });
  var requested = location.hash.slice(1);
  if (document.getElementById("panel-" + requested)) selectTab(requested);
  else {
    var active = tabs.filter(function (tab) { return tab.classList.contains("active"); })[0];
    if (active) selectTab(active.getAttribute("data-tab"));
  }
  if (search) search.addEventListener("input", function () {
    window.dispatchEvent(new CustomEvent("prime:search", {
      detail: { context: search.dataset.context, value: search.value }
    }));
  });
}());
