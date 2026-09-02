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
  var ageSave = document.getElementById("age-content-save");
  var ageValue = document.getElementById("age-content-value");
  var matureToggle = document.getElementById("mature-content-toggle");
  var matureValue = document.getElementById("mature-content-value");
  var feedback = document.getElementById("age-content-feedback");
  var policy = null;

  function policyMessage(next) {
    if (!next) return "Birth date not configured.";
    if (next.storage_error) return "Age profile is locked but could not be read. Mature content remains disabled.";
    if (next.age === null || next.age === undefined) return "Birth date not configured. It can be set once.";
    return "Age " + next.age + (next.birth_date_locked
      ? ". Birth date is locked to the operating-system user profile."
      : ". Kodi age policy is active.");
  }

  function applyPolicy(next, announce) {
    policy = next || {};
    var locked = !!policy.birth_date_locked;
    if (birthDate) {
      birthDate.value = policy.birth_date_display || "";
      birthDate.disabled = locked;
      birthDate.setAttribute("aria-readonly", locked ? "true" : "false");
    }
    if (ageSave) {
      ageSave.disabled = locked;
      ageSave.textContent = locked ? "Age locked" : "Set and lock age";
    }
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
    window.dispatchEvent(new CustomEvent("prime:maturechange", {
      detail: { mature: Number(policy.mature) === 1 ? 1 : 0 }
    }));
  }

  async function policyFetch(url, options) {
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timeout = window.setTimeout(function () { if (controller) controller.abort(); }, 8000);
    try {
      var request = options || {};
      request.credentials = "same-origin";
      request.cache = "no-store";
      request.signal = controller ? controller.signal : undefined;
      var response = await fetch(url, request);
      var payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || "Could not access age policy.");
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") throw new Error("Age policy request timed out.");
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function readPolicy() {
    if (!ageForm) return;
    try {
      var payload = await policyFetch("/api/preferences/age-policy", {
        headers: { "Accept": "application/json" }
      });
      applyPolicy(payload.policy, false);
      if (feedback) feedback.textContent = policyMessage(payload.policy);
    } catch (error) {
      if (feedback) feedback.textContent = error.message || "Could not load age policy.";
    }
  }

  async function writePolicy(body) {
    var payload = await policyFetch("/api/preferences/age-policy", {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    return payload.policy;
  }

  if (ageForm) ageForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (policy && policy.birth_date_locked) {
      if (feedback) feedback.textContent = "Birth date is already locked and cannot be changed.";
      return;
    }
    if (ageSave) ageSave.disabled = true;
    if (feedback) feedback.textContent = "Saving and locking age policy…";
    try {
      var next = await writePolicy({ birth_date: birthDate ? birthDate.value.trim() : "" });
      applyPolicy(next, true);
    } catch (error) {
      if (feedback) feedback.textContent = error.message || "Could not save age policy.";
    } finally {
      if (ageSave) ageSave.disabled = !!(policy && policy.birth_date_locked);
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
