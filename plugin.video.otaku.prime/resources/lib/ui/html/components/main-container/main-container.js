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

  var ageForm = document.getElementById("age-content-form");
  var birthDate = document.getElementById("age-birth-date");
  var ageValue = document.getElementById("age-content-value");
  var matureToggle = document.getElementById("mature-content-toggle");
  var matureValue = document.getElementById("mature-content-value");
  var feedback = document.getElementById("age-content-feedback");
  var policy = null;

  function policyMessage(next) {
    if (!next || next.age === null || next.age === undefined) return "Birth date not configured.";
    return "Age " + next.age + ". Kodi age policy is active.";
  }

  function applyPolicy(next, announce) {
    policy = next || {};
    if (birthDate) birthDate.value = policy.birth_date_display || "";
    if (ageValue) {
      ageValue.textContent = policy.age === null || policy.age === undefined
        ? "Not configured"
        : String(policy.age) + " years";
    }
    if (matureToggle) {
      matureToggle.checked = Number(policy.mature) === 1;
      matureToggle.disabled = !policy.mature_allowed;
    }
    if (matureValue) {
      if (!policy.mature_allowed) matureValue.textContent = "Mature filter unavailable (18+)";
      else matureValue.textContent = Number(policy.mature) === 1
        ? "Mature filter enabled"
        : "Mature filter disabled";
    }
    if (feedback && announce) feedback.textContent = policyMessage(policy);
    window.dispatchEvent(new CustomEvent("prime:agepolicychange", { detail: policy }));
    // Keep the existing Library listener compatible while the age-aware overlay
    // extends blur behavior to R/R+.
    window.dispatchEvent(new CustomEvent("prime:maturechange", {
      detail: { mature: Number(policy.mature) === 1 ? 1 : 0 }
    }));
  }

  async function readPolicy() {
    if (!ageForm) return;
    try {
      var response = await fetch("/api/preferences/age-policy", {
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
        cache: "no-store"
      });
      var payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || "Could not load age policy.");
      applyPolicy(payload.policy, false);
      if (feedback) feedback.textContent = policyMessage(payload.policy);
    } catch (error) {
      if (feedback) feedback.textContent = error.message || "Could not load age policy.";
    }
  }

  async function writePolicy(body) {
    var response = await fetch("/api/preferences/age-policy", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    var payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.message || "Could not save age policy.");
    return payload.policy;
  }

  if (ageForm) ageForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var button = ageForm.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    if (feedback) feedback.textContent = "Saving age policy…";
    try {
      var next = await writePolicy({ birth_date: birthDate ? birthDate.value.trim() : "" });
      applyPolicy(next, true);
    } catch (error) {
      if (feedback) feedback.textContent = error.message || "Could not save age policy.";
    } finally {
      if (button) button.disabled = false;
    }
  });

  if (matureToggle) matureToggle.addEventListener("change", async function () {
    var nextValue = matureToggle.checked ? 1 : 0;
    matureToggle.disabled = true;
    if (feedback) feedback.textContent = "Saving Mature filter…";
    try {
      var next = await writePolicy({ mature: nextValue });
      applyPolicy(next, true);
    } catch (error) {
      matureToggle.checked = policy && Number(policy.mature) === 1;
      matureToggle.disabled = !(policy && policy.mature_allowed);
      if (feedback) feedback.textContent = error.message || "Could not save Mature filter.";
    }
  });

  readPolicy();
}());
