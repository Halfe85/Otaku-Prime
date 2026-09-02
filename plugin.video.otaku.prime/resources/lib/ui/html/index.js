document.documentElement.classList.add("js");

(function () {
  "use strict";
  var grid = document.getElementById("library-grid");
  if (!grid) return;

  var policy = { age: null, mature: 0, mature_allowed: false };
  var items = new Map();
  var loading = false;

  function terms(value) {
    return Array.isArray(value) ? value.map(function (item) { return String(item || "").trim(); }) : [];
  }

  function rating(item) {
    item = item || {};
    var raw = String(item.age_rating || "").trim().toUpperCase();
    var compact = raw.replace(/\s+/g, "");
    var hentai = terms(item.genres).some(function (value) {
      return value.toLowerCase() === "hentai";
    });
    if (/^RX/.test(compact) || item.mature || hentai) return "RX";
    if (/^R\+/.test(compact)) return "R+";
    if (compact === "R" || /^R[-(]/.test(compact)) return "R";
    if (/^PG-?13/.test(compact)) return "PG-13";
    if (compact === "PG" || /^PG[-(]/.test(compact)) return "PG";
    if (compact === "G" || /^G[-(]/.test(compact)) return "G";
    return raw || null;
  }

  function shouldBlur(item) {
    var value = rating(item);
    var age = policy.age === null || policy.age === undefined ? null : Number(policy.age);
    if (value === "RX") return !(age !== null && age >= 18 && Number(policy.mature) === 1);
    if (value === "R" || value === "R+") return age === null || age < 15;
    // PG-13 is withheld from Kodi below 10 but remains visually unblurred in Prime.
    return false;
  }

  function localIdFromText(node) {
    var match = String(node && node.textContent || "").toLowerCase().match(/\b[0-9a-f]{6}\b/);
    return match ? match[0] : "";
  }

  function applyBlur() {
    grid.querySelectorAll(".library-tile[data-series-id]").forEach(function (tile) {
      var item = items.get(String(tile.dataset.seriesId || "").toLowerCase());
      if (item) tile.classList.toggle("mature-artwork-blurred", shouldBlur(item));
    });

    var hero = document.getElementById("library-series-hero");
    var localId = localIdFromText(document.getElementById("library-series-local-id"));
    var detail = items.get(localId);
    if (hero && detail) hero.classList.toggle("mature-artwork-blurred", shouldBlur(detail));
  }

  async function json(url) {
    var response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      cache: "no-store"
    });
    var payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.message || "Prime request failed");
    return payload;
  }

  async function refresh() {
    if (loading || document.hidden) return;
    loading = true;
    try {
      var result = await Promise.all([
        json("/api/preferences/age-policy"),
        json("/api/library/series"),
        json("/api/library/movies")
      ]);
      policy = result[0].policy || policy;
      items.clear();
      (result[1].series || []).concat(result[2].movies || []).forEach(function (item) {
        if (item && item.local_id) items.set(String(item.local_id).toLowerCase(), item);
      });
      applyBlur();
    } catch (_) {
      // The main Library component owns visible request errors. The age overlay
      // deliberately stays silent and retries on the next normal refresh.
    } finally {
      loading = false;
    }
  }

  var observer = new MutationObserver(function () { applyBlur(); });
  observer.observe(grid, { childList: true, subtree: true });
  var modalId = document.getElementById("library-series-local-id");
  if (modalId) observer.observe(modalId, { childList: true, subtree: true, characterData: true });

  window.addEventListener("prime:agepolicychange", function (event) {
    policy = event && event.detail ? event.detail : policy;
    applyBlur();
  });
  window.addEventListener("prime:maturechange", function (event) {
    if (event && event.detail) policy.mature = Number(event.detail.mature) === 1 ? 1 : 0;
    applyBlur();
  });
  window.addEventListener("prime:tabchange", function (event) {
    if (event && event.detail && event.detail.id === "library") refresh();
  });

  refresh();
  window.setInterval(refresh, 5000);
}());
