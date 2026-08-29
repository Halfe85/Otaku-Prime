(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-tab]"));
  var search = document.getElementById("prime-search");
  var bottomStatus = document.getElementById("watchlist-page-status");
  var searchContexts = {
    "library": "Search library titles",
    "watchlist-management": "Search watchlist titles"
  };
  function selectTab(id) {
    if (bottomStatus) bottomStatus.textContent = "";
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
    window.dispatchEvent(new CustomEvent("prime:tabchange", { detail: { id: id } }));
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

  var matureToggle = document.getElementById("mature-content-toggle");
  var matureValue = document.getElementById("mature-content-value");
  var matureFeedback = document.getElementById("mature-content-feedback");
  if (matureToggle) matureToggle.addEventListener("change", async function () {
    var next = matureToggle.checked ? 1 : 0;
    matureToggle.disabled = true;
    if (matureFeedback) matureFeedback.textContent = "Saving…";
    try {
      var response = await fetch("/api/preferences/mature", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Accept": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ mature: next })
      });
      var payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || "Could not save setting.");
      next = Number(payload.preferences.mature) === 1 ? 1 : 0;
      matureToggle.checked = next === 1;
      if (matureValue) matureValue.textContent = "mature=" + next;
      if (matureFeedback) matureFeedback.textContent = next ? "18+ titles enabled." : "18+ titles hidden.";
      window.dispatchEvent(new CustomEvent("prime:maturechange", { detail: { mature: next } }));
    } catch (error) {
      matureToggle.checked = !matureToggle.checked;
      if (matureFeedback) matureFeedback.textContent = error.message || "Could not save setting.";
    } finally {
      matureToggle.disabled = false;
    }
  });
}());
