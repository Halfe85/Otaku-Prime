(function () {
  var rows = document.getElementById("watchlist-rows");
  if (!rows) return;

  var status = document.getElementById("watchlist-status");
  var previous = document.getElementById("watchlist-previous");
  var next = document.getElementById("watchlist-next");
  var pageStatus = document.getElementById("watchlist-page-status");
  var modal = document.getElementById("series-modal");
  var entries = [];
  var page = 1;
  var pageSize = 8;
  var returnFocus = null;
  var searchTerm = "";
  var currentEntry = null;
  var loaded = false;
  var loading = false;
  var stopped = false;

  var providers = [
    { id: "anilist", label: "AniList", icon: "/ui/components/watchlist-management/assets/anilist.png", url: "https://anilist.co/anime/" },
    { id: "mal", label: "MyAnimeList", icon: "/ui/components/watchlist-management/assets/mal.png", url: "https://myanimelist.net/anime/" },
    { id: "kitsu", label: "Kitsu", icon: "/ui/components/watchlist-management/assets/kitsu.png", url: "https://kitsu.app/anime/" },
    { id: "simkl", label: "Simkl", icon: "/ui/components/watchlist-management/assets/simkl.png", url: "https://simkl.com/anime/" }
  ];

  function value(value, fallback) {
    return value == null || value === "" ? (fallback || "—") : String(value);
  }

  function setText(id, content) {
    document.getElementById(id).textContent = value(content);
  }

  function textCell(row, content, className) {
    var cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value(content);
    row.appendChild(cell);
    return cell;
  }

  function alternativeTitles(entry) {
    var titles = entry && entry.alternative_titles;
    if (Array.isArray(titles)) return titles.filter(Boolean);
    if (typeof titles === "string" && titles) {
      try {
        var decoded = JSON.parse(titles);
        return Array.isArray(decoded) ? decoded.filter(Boolean) : [titles];
      } catch (_) {
        return [titles];
      }
    }
    return [];
  }

  function titleOf(entry) {
    var alternatives = alternativeTitles(entry);
    return entry.english_name || entry.preferred_name || entry.romaji_name ||
      entry.native_name || alternatives[0] || "Untitled";
  }

  function visiblePageSize() {
    var top = rows.getBoundingClientRect().top;
    var controlsTop = document.getElementById("watchlist-pagination").getBoundingClientRect().top;
    return Math.max(3, Math.floor((controlsTop - top - 10) / 59));
  }

  function providerLink(provider, entry) {
    var id = entry[provider.id + "_id"];
    var link = document.createElement(id ? "a" : "div");
    link.className = "provider-link" + (id ? "" : " unavailable");
    if (id) {
      link.href = provider.url + encodeURIComponent(String(id));
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", "Open " + titleOf(entry) + " on " + provider.label);
    }
    var icon = document.createElement("img");
    icon.className = "provider-icon";
    icon.src = provider.icon;
    icon.alt = "";
    var label = document.createElement("span");
    label.className = "provider-link-label";
    var name = document.createElement("span");
    name.textContent = provider.label;
    var providerId = document.createElement("strong");
    providerId.textContent = id ? "ID " + id : "ID unavailable";
    label.appendChild(name);
    label.appendChild(providerId);
    var arrow = document.createElement("span");
    arrow.className = "provider-link-arrow";
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = id ? "↗" : "—";
    link.appendChild(icon);
    link.appendChild(label);
    link.appendChild(arrow);
    return link;
  }

  function openModal(entry, trigger) {
    currentEntry = entry;
    returnFocus = trigger;
    setText("series-modal-title", titleOf(entry));
    setText("series-modal-status", entry.status);
    setText("series-modal-progress", String(entry.progress || 0) + (entry.episode_count != null ? " / " + entry.episode_count : " episodes"));
    setText("series-modal-format", entry.media_format || "Unknown");
    setText("series-modal-release", entry.release_date || "Unknown");
    setText("series-modal-english", entry.english_name);
    setText("series-modal-preferred", entry.preferred_name);
    setText("series-modal-romaji", entry.romaji_name);
    setText("series-modal-native", entry.native_name);
    setText("series-modal-alternatives", alternativeTitles(entry).join(" · "));
    setText("series-modal-local-id", "Prime ID  " + entry.local_id);
    document.getElementById("series-modal-conflict").hidden = !entry.has_conflict;
    var identityConflict = document.getElementById("series-modal-identity-conflict");
    var hasIdentityConflict = String(entry.identity_resolution_status || "").indexOf("CONFLICT") === 0;
    identityConflict.hidden = !hasIdentityConflict;
    identityConflict.textContent = hasIdentityConflict
      ? "Catalog identity conflict: " + value(entry.identity_resolution_error,
        "Simkl pointed to a different anime, so Prime kept the watchlist provider identity.")
      : "";
    var links = document.getElementById("series-modal-provider-links");
    links.textContent = "";
    providers.forEach(function (provider) {
      if (entry[provider.id + "_id"]) links.appendChild(providerLink(provider, entry));
    });
    modal.hidden = false;
    document.querySelector(".series-modal-close").focus();
  }

  function closeModal() {
    if (modal.hidden) return;
    modal.hidden = true;
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    returnFocus = null;
    currentEntry = null;
  }

  function connectedProviders(entry) {
    var connected = String(entry.connected_providers || "").split(",");
    return providers.filter(function (provider) { return connected.indexOf(provider.id) !== -1; });
  }

  function active() {
    var panel = document.getElementById("panel-watchlist-management");
    return !stopped && !document.hidden && panel && !panel.hidden;
  }

  function providerIcons(entry) {
    var group = document.createElement("span");
    group.className = "provider-icons";
    connectedProviders(entry).forEach(function (provider) {
      var icon = document.createElement("img");
      icon.src = provider.icon;
      icon.alt = provider.label;
      icon.title = provider.label;
      group.appendChild(icon);
    });
    return group;
  }

  function render() {
    var term = searchTerm.trim().toLowerCase();
    var wanted = status.value;
    var visible = entries.filter(function (entry) {
      var haystack = [titleOf(entry), entry.english_name, entry.preferred_name,
        entry.romaji_name, entry.native_name].concat(alternativeTitles(entry), [entry.anilist_id,
        entry.mal_id, entry.kitsu_id, entry.simkl_id, entry.connected_providers]).join(" ").toLowerCase();
      return (!term || haystack.indexOf(term) !== -1) && (!wanted || entry.status === wanted);
    });
    pageSize = visiblePageSize();
    var pages = Math.max(1, Math.ceil(visible.length / pageSize));
    page = Math.max(1, Math.min(page, pages));
    var pageEntries = visible.slice((page - 1) * pageSize, page * pageSize);
    rows.textContent = "";
    if (!pageEntries.length) {
      var empty = document.createElement("tr");
      textCell(empty, "No matching watchlist entries.", "muted").colSpan = 5;
      rows.appendChild(empty);
    }
    pageEntries.forEach(function (entry) {
      var row = document.createElement("tr");
      row.tabIndex = 0;
      row.dataset.watchlistItem = entry.local_id;
      row.setAttribute("aria-label", "Open details for " + titleOf(entry));
      row.addEventListener("click", function () { openModal(entry, row); });
      row.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openModal(entry, row);
        }
      });
      var title = document.createElement("td");
      title.className = "watchlist-title";
      var strong = document.createElement("strong");
      strong.textContent = titleOf(entry);
      title.appendChild(strong);
      row.appendChild(title);
      var providerCell = document.createElement("td");
      providerCell.appendChild(providerIcons(entry));
      row.appendChild(providerCell);
      textCell(row, entry.media_format || "Unknown");
      textCell(row, entry.status);
      textCell(row, String(entry.progress || 0) + (entry.episode_count != null ? " / " + entry.episode_count : ""));
      rows.appendChild(row);
    });
    if (active()) pageStatus.textContent = "Page " + page + " of " + pages + " · " + visible.length + " items";
    previous.disabled = page <= 1;
    next.disabled = page >= pages;
    window.dispatchEvent(new CustomEvent("prime:watchlist-rendered"));
  }

  window.addEventListener("prime:search", function (event) {
    if (!event.detail || event.detail.context !== "watchlist-management") return;
    searchTerm = event.detail.value || "";
    page = 1;
    render();
  });
  status.addEventListener("change", function () { page = 1; render(); });
  previous.addEventListener("click", function () { if (page > 1) { page -= 1; render(); } });
  next.addEventListener("click", function () { page += 1; render(); });
  window.addEventListener("resize", render);
  modal.addEventListener("click", function (event) {
    if (event.target.hasAttribute("data-modal-close")) closeModal();
  });
  document.getElementById("series-modal-progress").addEventListener("click", function () {
    if (!currentEntry) return;
    var editingEntry = currentEntry;
    var button = this;
    var input = document.createElement("input");
    input.type = "number";
    input.className = "series-progress-input";
    input.min = "0";
    if (editingEntry.episode_count != null) input.max = String(editingEntry.episode_count);
    input.value = String(editingEntry.progress || 0);
    button.replaceWith(input);
    input.focus();
    input.select();
    function restore() {
      input.replaceWith(button);
      button.textContent = String(editingEntry.progress || 0) +
        (editingEntry.episode_count != null ? " / " + editingEntry.episode_count : " episodes");
    }
    function save() {
      var progress = Number(input.value);
      if (!Number.isInteger(progress) || progress < 0 ||
          (editingEntry.episode_count != null && progress > Number(editingEntry.episode_count))) {
        input.setCustomValidity("Enter a valid episode number.");
        input.reportValidity();
        return;
      }
      input.disabled = true;
      fetch("/api/watchlist/items/" + encodeURIComponent(editingEntry.local_id) + "/progress", {
        method: "POST",
        headers: { "Accept": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ progress: progress })
      }).then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) throw new Error(payload.message || "Could not update progress");
          return payload;
        });
      }).then(function (payload) {
        editingEntry.progress = payload.item.progress;
        restore();
        render();
      }).catch(function (error) {
        input.disabled = false;
        input.setCustomValidity(error.message);
        input.reportValidity();
      });
    }
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); save(); }
      if (event.key === "Escape") { event.preventDefault(); restore(); button.focus(); }
    });
    input.addEventListener("blur", function () { if (!input.disabled) restore(); });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !modal.hidden) closeModal();
    if (event.key === "Tab" && !modal.hidden) {
      var focusable = Array.prototype.slice.call(modal.querySelectorAll("a[href],button:not([disabled])"));
      if (!focusable.length) return;
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });

  function loadEntries() {
    if (loaded || loading || !active()) return;
    loading = true;
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timeout = window.setTimeout(function () { if (controller) controller.abort(); }, 8000);
    fetch("/api/watchlist/items", {
      headers: { "Accept": "application/json" }, cache: "no-store",
      signal: controller ? controller.signal : undefined
    })
      .then(function (response) {
        if (!response.ok) throw new Error("Could not load watchlist table");
        return response.json();
      })
      .then(function (payload) {
        entries = payload.entries || [];
        loaded = true;
        render();
      })
      .catch(function (error) {
        rows.textContent = "";
        var row = document.createElement("tr");
        var message = error && error.name === "AbortError"
          ? "Watchlist request timed out. Change tabs to retry."
          : (error.message || "Could not load watchlist table");
        textCell(row, message, "muted").colSpan = 5;
        rows.appendChild(row);
      })
      .finally(function () {
        window.clearTimeout(timeout);
        loading = false;
      });
  }

  window.addEventListener("prime:tabchange", function (event) {
    if (event.detail && event.detail.id === "watchlist-management") {
      if (loaded) render();
      else loadEntries();
    } else {
      pageStatus.textContent = "";
    }
  });
  document.addEventListener("visibilitychange", function () { if (active()) loadEntries(); });
  window.addEventListener("beforeunload", function () { stopped = true; });
  loadEntries();
}());
