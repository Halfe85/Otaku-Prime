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
      var panel = document.getElementById("panel-" + tab.getAttribute("data-tab"));
      if (panel) panel.hidden = !selected;
    });
    if (search) {
      search.value = "";
      search.disabled = !searchContexts[id];
      search.placeholder = searchContexts[id] || "Search current view";
      search.dataset.context = id;
      window.dispatchEvent(new CustomEvent("prime:search", {
        detail: { context: id, value: "" }
      }));
    }
    if (history.replaceState) history.replaceState(null, "", "#" + id);
    window.dispatchEvent(new CustomEvent("prime:tabchange", { detail: { id: id } }));
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      selectTab(tab.getAttribute("data-tab"));
    });
  });

  var requested = location.hash.slice(1);
  if (document.getElementById("panel-" + requested)) {
    selectTab(requested);
  } else {
    var active = tabs.filter(function (tab) {
      return tab.classList.contains("active");
    })[0];
    if (active) selectTab(active.getAttribute("data-tab"));
  }

  if (search) {
    search.addEventListener("input", function () {
      window.dispatchEvent(new CustomEvent("prime:search", {
        detail: { context: search.dataset.context, value: search.value }
      }));
    });
  }

  var dobCard = document.getElementById("date-of-birth-settings");
  var dobForm = document.getElementById("age-content-form");
  var birthDate = document.getElementById("age-birth-date");
  var dobSave = document.getElementById("age-content-save");
  var feedback = document.getElementById("age-content-feedback");
  var policy = null;
  var matureCard = null;
  var matureToggle = null;
  var matureValue = null;

  function policyMessage(next) {
    if (!next) return "Date of birth is not configured.";
    if (next.storage_error) {
      return "The saved date of birth is locked but could not be read.";
    }
    if (next.birth_date_locked) {
      return "Date of birth is saved and locked to this operating-system user profile.";
    }
    return "Choose your date of birth. It can only be saved once.";
  }

  function removeMatureControl() {
    if (matureCard && matureCard.parentNode) matureCard.parentNode.removeChild(matureCard);
    matureCard = null;
    matureToggle = null;
    matureValue = null;
  }

  function createMatureControl() {
    if (matureCard || !dobCard) return;

    var card = document.createElement("article");
    card.className = "card preference-card";
    card.id = "mature-content-settings";

    var badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "Preferences";
    card.appendChild(badge);

    var heading = document.createElement("h3");
    heading.textContent = "Mature content";
    card.appendChild(heading);

    var label = document.createElement("label");
    label.className = "preference-switch";

    var input = document.createElement("input");
    input.id = "mature-content-toggle";
    input.type = "checkbox";
    input.value = "1";
    label.htmlFor = input.id;
    label.appendChild(input);

    var track = document.createElement("span");
    track.className = "preference-switch-track";
    track.setAttribute("aria-hidden", "true");
    track.appendChild(document.createElement("span"));
    label.appendChild(track);

    var value = document.createElement("span");
    value.id = "mature-content-value";
    value.className = "preference-switch-value";
    label.appendChild(value);
    card.appendChild(label);

    input.addEventListener("change", async function () {
      var nextValue = input.checked ? 1 : 0;
      input.disabled = true;
      try {
        var next = await writePolicy({ mature: nextValue });
        applyPolicy(next, false);
      } catch (error) {
        input.checked = !!(policy && Number(policy.mature) === 1);
        input.disabled = false;
      }
    });

    dobCard.insertAdjacentElement("afterend", card);
    matureCard = card;
    matureToggle = input;
    matureValue = value;
  }

  function syncMatureControl(next) {
    if (!next || !next.mature_allowed) {
      removeMatureControl();
      return;
    }
    createMatureControl();
    if (matureToggle) {
      matureToggle.checked = Number(next.mature) === 1;
      matureToggle.disabled = false;
    }
    if (matureValue) {
      matureValue.textContent = Number(next.mature) === 1 ? "Enabled" : "Disabled";
    }
  }

  function applyPolicy(next, announce) {
    policy = next || {};
    var locked = !!policy.birth_date_locked;

    if (birthDate) {
      birthDate.value = policy.birth_date || "";
      birthDate.disabled = locked;
      birthDate.setAttribute("aria-readonly", locked ? "true" : "false");
    }
    if (dobSave) {
      dobSave.disabled = locked;
      dobSave.textContent = locked ? "Date of birth locked" : "Save date of birth";
    }
    syncMatureControl(policy);

    if (feedback && announce) feedback.textContent = policyMessage(policy);

    window.dispatchEvent(new CustomEvent("prime:agepolicychange", { detail: policy }));
    window.dispatchEvent(new CustomEvent("prime:maturechange", {
      detail: { mature: Number(policy.mature) === 1 ? 1 : 0 }
    }));
  }

  async function policyFetch(url, options) {
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timeout = window.setTimeout(function () {
      if (controller) controller.abort();
    }, 8000);
    try {
      var request = options || {};
      request.credentials = "same-origin";
      request.cache = "no-store";
      request.signal = controller ? controller.signal : undefined;
      var response = await fetch(url, request);
      var payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || "Could not access the date-of-birth setting.");
      }
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new Error("Date-of-birth request timed out.");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function readPolicy() {
    if (!dobForm) return;
    try {
      var payload = await policyFetch("/api/preferences/age-policy", {
        headers: { "Accept": "application/json" }
      });
      applyPolicy(payload.policy, false);
      if (feedback) feedback.textContent = policyMessage(payload.policy);
    } catch (error) {
      if (feedback) feedback.textContent = error.message || "Could not load date of birth.";
    }
  }

  async function writePolicy(body) {
    var payload = await policyFetch("/api/preferences/age-policy", {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body || {})
    });
    return payload.policy;
  }

  if (dobForm) {
    dobForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (policy && policy.birth_date_locked) {
        if (feedback) feedback.textContent = "Date of birth is already locked and cannot be changed.";
        return;
      }
      if (!birthDate || !birthDate.value) {
        if (feedback) feedback.textContent = "Choose a date of birth first.";
        return;
      }
      if (dobSave) dobSave.disabled = true;
      if (feedback) feedback.textContent = "Saving date of birth…";
      try {
        var next = await writePolicy({ birth_date: birthDate.value });
        applyPolicy(next, true);
      } catch (error) {
        if (feedback) feedback.textContent = error.message || "Could not save date of birth.";
      } finally {
        if (dobSave) dobSave.disabled = !!(policy && policy.birth_date_locked);
      }
    });
  }

  readPolicy();
}());
