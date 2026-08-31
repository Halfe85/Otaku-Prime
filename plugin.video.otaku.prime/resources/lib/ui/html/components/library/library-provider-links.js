(function () {
  "use strict";

  var modal = document.getElementById("library-series-modal");
  var identityNode = document.getElementById("library-series-local-id");
  var root = document.getElementById("library-series-providers");
  if (!modal || !identityNode || !root) return;

  var providers = {
    anilist: { label: "AniList", base: "https://anilist.co/anime/" },
    mal: { label: "MyAnimeList", base: "https://myanimelist.net/anime/" },
    kitsu: { label: "Kitsu", base: "https://kitsu.app/anime/" },
    simkl: { label: "Simkl", base: "https://simkl.com/anime/" }
  };
  var requestSerial = 0;

  function hasValue(value) {
    return value !== null && value !== undefined && String(value).trim() !== "";
  }

  function directIdentity(item, provider) {
    if (!item) return null;
    if (hasValue(item[provider + "_id"])) return String(item[provider + "_id"]);
    if (provider === "anilist" && hasValue(item.root_anilist_id)) {
      return String(item.root_anilist_id);
    }
    if (provider === "simkl" && hasValue(item.root_simkl_id)) {
      return String(item.root_simkl_id);
    }
    return null;
  }

  function orderedSeasons(item) {
    var seasons = Array.isArray(item && item.seasons) ? item.seasons.slice() : [];
    return seasons.sort(function (left, right) {
      var leftNumber = Number(left && left.season_number);
      var rightNumber = Number(right && right.season_number);
      var leftSpecial = Number.isFinite(leftNumber) && leftNumber === 0 ? 1 : 0;
      var rightSpecial = Number.isFinite(rightNumber) && rightNumber === 0 ? 1 : 0;
      if (leftSpecial !== rightSpecial) return leftSpecial - rightSpecial;
      if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber;
      if (Number.isFinite(leftNumber)) return -1;
      if (Number.isFinite(rightNumber)) return 1;
      return 0;
    });
  }

  function providerIdentity(item, provider) {
    var direct = directIdentity(item, provider);
    if (direct) return direct;

    var seasons = orderedSeasons(item);
    for (var index = 0; index < seasons.length; index += 1) {
      var season = seasons[index] || {};
      if (hasValue(season[provider + "_id"])) return String(season[provider + "_id"]);
    }
    for (var seasonIndex = 0; seasonIndex < seasons.length; seasonIndex += 1) {
      var episodes = Array.isArray(seasons[seasonIndex] && seasons[seasonIndex].episodes)
        ? seasons[seasonIndex].episodes : [];
      for (var episodeIndex = 0; episodeIndex < episodes.length; episodeIndex += 1) {
        if (hasValue(episodes[episodeIndex] && episodes[episodeIndex][provider + "_id"])) {
          return String(episodes[episodeIndex][provider + "_id"]);
        }
      }
    }
    return null;
  }

  function resetLinks() {
    root.querySelectorAll("[data-library-provider]").forEach(function (slot) {
      slot.removeAttribute("href");
      slot.removeAttribute("target");
      slot.removeAttribute("rel");
      slot.removeAttribute("data-provider-id");
      slot.setAttribute("aria-disabled", "true");
      slot.tabIndex = -1;
    });
  }

  function applyLinks(item) {
    root.querySelectorAll("[data-library-provider]").forEach(function (slot) {
      var provider = slot.dataset.libraryProvider;
      var config = providers[provider];
      var providerId = config ? providerIdentity(item, provider) : null;
      if (!config || !providerId) {
        slot.removeAttribute("href");
        slot.removeAttribute("target");
        slot.removeAttribute("rel");
        slot.removeAttribute("data-provider-id");
        slot.setAttribute("aria-disabled", "true");
        slot.tabIndex = -1;
        return;
      }
      slot.href = config.base + encodeURIComponent(providerId);
      slot.target = "_blank";
      slot.rel = "noopener noreferrer";
      slot.dataset.providerId = providerId;
      slot.setAttribute("aria-disabled", "false");
      slot.tabIndex = 0;
      slot.title = "Open " + config.label + " ID " + providerId;
      slot.setAttribute("aria-label", slot.title);
    });
  }

  function currentLibraryIdentity() {
    var match = /^Prime\s+(series|movie)\s+·\s+([0-9a-f]+)$/i.exec(
      String(identityNode.textContent || "").trim()
    );
    return match ? { type: match[1].toLowerCase(), id: match[2].toLowerCase() } : null;
  }

  async function refreshLinks() {
    var current = currentLibraryIdentity();
    var serial = ++requestSerial;
    resetLinks();
    if (!current || modal.hidden) return;

    var endpoint = current.type === "movie"
      ? "/api/library/movies/" + encodeURIComponent(current.id)
      : "/api/library/series/" + encodeURIComponent(current.id);
    try {
      var response = await fetch(endpoint, {
        headers: { "Accept": "application/json" },
        cache: "no-store"
      });
      var payload = await response.json();
      if (serial !== requestSerial || !response.ok || !payload || !payload.ok) return;
      var item = current.type === "movie" ? payload.movie : payload.series;
      if (item) applyLinks(item);
    } catch (_) {
      if (serial === requestSerial) resetLinks();
    }
  }

  new MutationObserver(function () {
    refreshLinks();
  }).observe(identityNode, { childList: true, characterData: true, subtree: true });

  new MutationObserver(function () {
    if (modal.hidden) {
      requestSerial += 1;
      resetLinks();
    } else {
      refreshLinks();
    }
  }).observe(modal, { attributes: true, attributeFilter: ["hidden"] });

  resetLinks();
}());
