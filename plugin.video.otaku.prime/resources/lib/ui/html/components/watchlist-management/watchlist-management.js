(function () {
  var rows = document.getElementById("watchlist-rows");
  if (!rows) return;

  var search = document.getElementById("watchlist-search");
  var status = document.getElementById("watchlist-status");
  var previous = document.getElementById("watchlist-previous");
  var next = document.getElementById("watchlist-next");
  var pageStatus = document.getElementById("watchlist-page-status");
  var modal = document.getElementById("series-modal");
  var entries = [];
  var page = 1;
  var pageSize = 8;
  var returnFocus = null;

  var providers = [
    { id: "anilist", label: "AniList", color: "#4ba3ff", url: "https://anilist.co/anime/" },
    { id: "mal", label: "MyAnimeList", color: "#5d78d6", url: "https://myanimelist.net/anime/" },
    { id: "kitsu", label: "Kitsu", color: "#f36f5d", url: "https://kitsu.app/anime/" },
    { id: "simkl", label: "Simkl", color: "#24b4c7", url: "https://simkl.com/anime/" }
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

  function titleOf(entry) {
    return entry.english_name || entry.romaji_name || entry.native_name || "Untitled";
  }

  function visiblePageSize() {
    var top = rows.getBoundingClientRect().top;
    return Math.max(3, Math.floor((window.innerHeight - top - 145) / 59));
  }

  function providerLink(provider, entry) {
    var id = entry[provider.id + "_id"];
    var link = document.createElement(id ? "a" : "div");
    link.className = "provider-link" + (id ? "" : " unavailable");
    link.style.setProperty("--provider-color", provider.color);
    if (id) {
      link.href = provider.url + encodeURIComponent(String(id));
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", "Open " + titleOf(entry) + " on " + provider.label);
    }
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
    link.appendChild(label);
    link.appendChild(arrow);
    return link;
  }

  function openModal(entry, trigger) {
    returnFocus = trigger;
    setText("series-modal-title", titleOf(entry));
    setText("series-modal-summary", entry.romaji_name && entry.romaji_name !== titleOf(entry) ? entry.romaji_name : entry.native_name || "Canonical Prime watchlist identity");
    setText("series-modal-status", entry.status);
    setText("series-modal-progress", String(entry.progress || 0) + (entry.episode_count != null ? " / " + entry.episode_count : " episodes"));
    setText("series-modal-format", entry.media_format || "Unknown");
    setText("series-modal-release", entry.release_date || "Unknown");
    setText("series-modal-english", entry.english_name);
    setText("series-modal-romaji", entry.romaji_name);
    setText("series-modal-native", entry.native_name);
    setText("series-modal-sources", value(entry.connected_providers, "No connected sources").split(",").filter(Boolean).length + " connected");
    setText("series-modal-local-id", "Prime ID  " + entry.local_id);
    document.getElementById("series-modal-conflict").hidden = !entry.has_conflict;
    var identityConflict = document.getElementById("series-modal-identity-conflict");
    identityConflict.hidden = entry.identity_resolution_status !== "CONFLICT";
    identityConflict.textContent = entry.identity_resolution_status === "CONFLICT"
      ? "Catalog identity conflict: " + value(entry.identity_resolution_error,
        "Simkl pointed to a different anime, so Prime kept the watchlist provider identity.")
      : "";
    var links = document.getElementById("series-modal-provider-links");
    links.textContent = "";
    providers.forEach(function (provider) { links.appendChild(providerLink(provider, entry)); });
    modal.hidden = false;
    document.querySelector(".series-modal-close").focus();
  }

  function closeModal() {
    if (modal.hidden) return;
    modal.hidden = true;
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    returnFocus = null;
  }

  function render() {
    var term = search.value.trim().toLowerCase();
    var wanted = status.value;
    var visible = entries.filter(function (entry) {
      var haystack = [titleOf(entry), entry.romaji_name, entry.native_name, entry.anilist_id,
        entry.mal_id, entry.kitsu_id, entry.simkl_id, entry.connected_providers].join(" ").toLowerCase();
      return (!term || haystack.indexOf(term) !== -1) && (!wanted || entry.status === wanted);
    });
    pageSize = visiblePageSize();
    var pages = Math.max(1, Math.ceil(visible.length / pageSize));
    page = Math.max(1, Math.min(page, pages));
    var pageEntries = visible.slice((page - 1) * pageSize, page * pageSize);
    rows.textContent = "";
    if (!pageEntries.length) {
      var empty = document.createElement("tr");
      textCell(empty, "No matching watchlist entries.", "muted").colSpan = 6;
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
      var sub = document.createElement("span");
      sub.textContent = entry.romaji_name && entry.romaji_name !== strong.textContent ? entry.romaji_name : (entry.release_date || "Open details");
      title.appendChild(strong);
      title.appendChild(sub);
      row.appendChild(title);
      var idText = [entry.anilist_id ? "AL " + entry.anilist_id : "", entry.mal_id ? "MAL " + entry.mal_id : "",
        entry.kitsu_id ? "Kitsu " + entry.kitsu_id : "", entry.simkl_id ? "Simkl " + entry.simkl_id : ""].filter(Boolean).join(" · ");
      textCell(row, idText, "provider-item");
      textCell(row, entry.media_format || "Unknown");
      textCell(row, entry.status);
      textCell(row, String(entry.progress || 0) + (entry.episode_count != null ? " / " + entry.episode_count : ""));
      textCell(row, entry.connected_providers + (entry.has_conflict ? " · conflict" : ""));
      rows.appendChild(row);
    });
    pageStatus.textContent = "Page " + page + " of " + pages + " · " + visible.length + " items";
    previous.disabled = page <= 1;
    next.disabled = page >= pages;
  }

  search.addEventListener("input", function () { page = 1; render(); });
  status.addEventListener("change", function () { page = 1; render(); });
  previous.addEventListener("click", function () { if (page > 1) { page -= 1; render(); } });
  next.addEventListener("click", function () { page += 1; render(); });
  window.addEventListener("resize", render);
  modal.addEventListener("click", function (event) {
    if (event.target.hasAttribute("data-modal-close")) closeModal();
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

  fetch("/api/watchlist/items", { headers: { "Accept": "application/json" } })
    .then(function (response) { if (!response.ok) throw new Error("Could not load watchlist table"); return response.json(); })
    .then(function (payload) { entries = payload.entries || []; render(); })
    .catch(function (error) {
      rows.textContent = "";
      var row = document.createElement("tr");
      textCell(row, error.message, "muted").colSpan = 6;
      rows.appendChild(row);
    });
}());
