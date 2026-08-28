(function () {
  "use strict";

  var grid = document.getElementById("library-grid");
  if (!grid) return;

  var count = document.getElementById("library-count");
  var message = document.getElementById("library-message");
  var seriesModal = document.getElementById("library-series-modal");
  var episodeModal = document.getElementById("library-episode-modal");
  var expandedSeasons = new Set();
  var state = {
    series: [],
    query: "",
    openSeriesId: null,
    detail: null,
    busyTiles: false,
    busyDetail: false,
    stopped: false
  };

  function text(value, fallback) {
    if (value === null || value === undefined || String(value).trim() === "") {
      return fallback || "—";
    }
    return String(value);
  }

  function formatDate(value) {
    if (!value) return "—";
    var raw = String(value);
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
    if (match) return match[3] + "." + match[2] + "." + match[1];
    var parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw;
    return parsed.toLocaleString([], {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit"
    });
  }

  function runtime(value) {
    if (value === null || value === undefined || value === "") return "Not resolved yet";
    var minutes = Number(value);
    if (!Number.isFinite(minutes) || minutes <= 0) return text(value, "Not resolved yet");
    return Math.round(minutes) + " min";
  }

  function normalizedStatus(value) {
    return String(value || "").trim().toLowerCase().replace(/[ _-]+/g, " ");
  }

  function airingLabel(item) {
    if (item && item.next_episode_release_date) {
      var episode = item.next_episode_number !== null && item.next_episode_number !== undefined
        ? "Episode " + item.next_episode_number + " · " : "";
      return "Running · " + episode + formatDate(item.next_episode_release_date);
    }
    var status = normalizedStatus(item && (item.library_status || item.air_status));
    if (["finished", "finished airing", "ended", "released", "completed", "complete"].indexOf(status) >= 0) {
      return "Finished airing";
    }
    if (["running", "airing", "ongoing", "current"].indexOf(status) >= 0) return "Running";
    return "Airing status pending";
  }

  function statusClass(item) {
    if (item && item.next_episode_release_date) return "running";
    var status = normalizedStatus(item && (item.library_status || item.air_status));
    return ["finished", "finished airing", "ended", "released", "completed", "complete"].indexOf(status) >= 0
      ? "finished" : "";
  }

  function element(tag, className, value) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  function showMessage(value, isError) {
    if (!message) return;
    if (!value) {
      message.hidden = true;
      message.textContent = "";
      message.classList.remove("warning");
      return;
    }
    message.hidden = false;
    message.textContent = value;
    message.classList.toggle("warning", !!isError);
  }

  async function fetchJson(url) {
    var response = await fetch(url, { headers: { "Accept": "application/json" }, cache: "no-store" });
    var payload = null;
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || "Prime library request failed.");
    }
    return payload;
  }

  function filteredSeries() {
    var query = state.query.trim().toLocaleLowerCase();
    if (!query) return state.series.slice();
    return state.series.filter(function (item) {
      return [item.title, item.english_name, item.romaji_name, item.publish_year]
        .filter(function (value) { return value !== null && value !== undefined; })
        .some(function (value) { return String(value).toLocaleLowerCase().indexOf(query) >= 0; });
    });
  }

  function renderTiles() {
    var rows = filteredSeries();
    grid.replaceChildren();
    if (count) {
      count.textContent = state.series.length === 1
        ? "1 mediated series"
        : state.series.length + " mediated series";
    }
    if (!rows.length) {
      var empty = element("div", "library-empty");
      empty.textContent = state.series.length
        ? "No library titles match this search."
        : "The library is empty. Tiles will appear here as the mediator resolves series.";
      grid.appendChild(empty);
      return;
    }

    rows.forEach(function (item) {
      var tile = element("button", "library-tile");
      tile.type = "button";
      tile.dataset.seriesId = item.local_id;
      tile.setAttribute("aria-label", "Open " + text(item.title, "series") + " details");

      var top = element("div", "library-tile-top");
      top.appendChild(element("h3", "library-tile-title", text(item.title, "Untitled series")));
      top.appendChild(element("span", "library-tile-year", item.publish_year ? String(item.publish_year) : "Year pending"));
      tile.appendChild(top);

      var body = element("div", "library-tile-next");
      body.textContent = item.next_episode_release_date
        ? "Next: E" + text(item.next_episode_number, "?") + " · " + formatDate(item.next_episode_release_date)
        : airingLabel(item);
      tile.appendChild(body);

      var meta = element("div", "library-tile-meta");
      meta.appendChild(element("span", "library-chip " + statusClass(item),
        item.next_episode_release_date ? "Running" : airingLabel(item)));
      meta.appendChild(element("span", "library-chip",
        text(item.season_count, "0") + (Number(item.season_count) === 1 ? " season" : " seasons")));
      meta.appendChild(element("span", "library-chip",
        text(item.episode_count, "0") + (Number(item.episode_count) === 1 ? " episode" : " episodes")));
      tile.appendChild(meta);

      tile.addEventListener("click", function () { openSeries(item.local_id); });
      grid.appendChild(tile);
    });
  }

  async function loadTiles() {
    if (state.busyTiles || state.stopped || document.hidden) return;
    state.busyTiles = true;
    try {
      var payload = await fetchJson("/api/library/series");
      state.series = Array.isArray(payload.series) ? payload.series : [];
      renderTiles();
      showMessage("");
      if (state.openSeriesId) await loadSeriesDetail(state.openSeriesId, true);
    } catch (error) {
      showMessage(error.message || "Could not load the Prime library.", true);
    } finally {
      state.busyTiles = false;
    }
  }

  function setSeriesText(id, value, fallback) {
    var node = document.getElementById(id);
    if (node) node.textContent = text(value, fallback);
  }

  function renderCast(cast) {
    var root = document.getElementById("library-series-cast");
    var castCount = document.getElementById("library-cast-count");
    if (!root) return;
    root.replaceChildren();
    cast = Array.isArray(cast) ? cast : [];
    if (castCount) castCount.textContent = cast.length ? cast.length + " credited" : "";
    if (!cast.length) {
      root.appendChild(element("p", "library-muted", "No cast metadata resolved yet."));
      return;
    }
    cast.forEach(function (entry) {
      var card = element("div", "library-cast-card");
      card.appendChild(element("strong", "", text(entry.person_name, "Unknown actor")));
      card.appendChild(element("span", "", text(entry.character_name, "Character not resolved")));
      root.appendChild(card);
    });
  }

  function episodeButton(series, season, episode) {
    var button = element("button", "library-episode-row");
    button.type = "button";
    button.appendChild(element("span", "library-episode-index", "E" + String(episode.episode_number).padStart(2, "0")));
    button.appendChild(element("span", "library-episode-name", text(episode.title, "Episode " + episode.episode_number)));
    button.appendChild(element("span", "library-episode-date", formatDate(episode.release_date)));
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      openEpisode(series, season, episode);
    });
    return button;
  }

  function renderSeasons(series) {
    var root = document.getElementById("library-series-seasons");
    var seasonCount = document.getElementById("library-season-count");
    if (!root) return;
    root.replaceChildren();
    var seasons = Array.isArray(series.seasons) ? series.seasons : [];
    if (seasonCount) seasonCount.textContent = seasons.length + (seasons.length === 1 ? " season" : " seasons");
    if (!seasons.length) {
      root.appendChild(element("p", "library-muted", "Season placement is still being resolved."));
      return;
    }

    seasons.forEach(function (season) {
      var wrapper = element("div", "library-season");
      var seasonId = String(season.local_id);
      var open = expandedSeasons.has(seasonId);
      wrapper.classList.toggle("expanded", open);

      var toggle = element("button", "library-season-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      var label = element("span", "library-season-name");
      label.appendChild(element("strong", "", season.season_number === 0
        ? "Specials"
        : "Season " + text(season.season_number, "?")));
      label.appendChild(element("span", "", text(season.english_name || season.romaji_name, "Title pending")));
      toggle.appendChild(label);

      var episodes = Array.isArray(season.episodes) ? season.episodes : [];
      var seasonMeta = element("span", "library-season-meta");
      seasonMeta.textContent = episodes.length + (episodes.length === 1 ? " episode" : " episodes");
      if (season.next_episode_release_date) {
        seasonMeta.textContent += " · Next E" + text(season.next_episode_number, "?") + " " + formatDate(season.next_episode_release_date);
      }
      toggle.appendChild(seasonMeta);
      toggle.appendChild(element("span", "library-season-chevron", "›"));

      var episodeRoot = element("div", "library-episodes");
      episodeRoot.hidden = !open;
      episodes.forEach(function (episode) {
        episodeRoot.appendChild(episodeButton(series, season, episode));
      });
      if (!episodes.length) episodeRoot.appendChild(element("p", "library-muted library-episode-empty", "Episodes are still being resolved."));

      toggle.addEventListener("click", function () {
        var nextOpen = !expandedSeasons.has(seasonId);
        if (nextOpen) expandedSeasons.add(seasonId); else expandedSeasons.delete(seasonId);
        wrapper.classList.toggle("expanded", nextOpen);
        toggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
        episodeRoot.hidden = !nextOpen;
      });

      wrapper.appendChild(toggle);
      wrapper.appendChild(episodeRoot);
      root.appendChild(wrapper);
    });
  }

  function renderSeriesDetail(series) {
    state.detail = series;
    var title = text(series.english_name || series.title || series.romaji_name, "Untitled series");
    var year = series.publish_year ? String(series.publish_year) : "Year pending";
    setSeriesText("library-series-title", title);
    setSeriesText("library-series-subtitle", title + (series.publish_year ? " (" + year + ")" : ""));
    setSeriesText("library-series-english", series.english_name || series.title, "Not resolved yet");
    setSeriesText("library-series-year", series.publish_year, "Not resolved yet");
    setSeriesText("library-series-runtime", runtime(series.runtime_minutes));
    setSeriesText("library-series-airing", airingLabel(series));
    setSeriesText("library-series-overview", series.overview, "Metadata has not been resolved yet.");
    setSeriesText("library-series-local-id", "Prime series · " + series.local_id);
    renderCast(series.cast);
    renderSeasons(series);
  }

  async function loadSeriesDetail(localId, silent) {
    if (state.busyDetail || !localId) return;
    state.busyDetail = true;
    try {
      var payload = await fetchJson("/api/library/series/" + encodeURIComponent(localId));
      if (state.openSeriesId !== localId) return;
      renderSeriesDetail(payload.series);
    } catch (error) {
      if (!silent) showMessage(error.message || "Could not load series details.", true);
    } finally {
      state.busyDetail = false;
    }
  }

  async function openSeries(localId) {
    state.openSeriesId = localId;
    state.detail = null;
    if (seriesModal) seriesModal.hidden = false;
    document.body.classList.add("library-modal-open");
    setSeriesText("library-series-title", "Loading series…");
    setSeriesText("library-series-subtitle", "Mediator catalogue");
    await loadSeriesDetail(localId, false);
  }

  function closeSeries() {
    closeEpisode();
    state.openSeriesId = null;
    state.detail = null;
    if (seriesModal) seriesModal.hidden = true;
    document.body.classList.remove("library-modal-open");
  }

  function openEpisode(series, season, episode) {
    if (!episodeModal) return;
    var seriesTitle = text(series.english_name || series.title || series.romaji_name, "Series");
    var seriesHeading = seriesTitle + (series.publish_year ? " (" + series.publish_year + ")" : "");
    setSeriesText("library-episode-series", seriesHeading);
    setSeriesText("library-episode-title", episode.title, "Episode " + episode.episode_number);
    setSeriesText("library-episode-number", "Episode " + String(episode.episode_number).padStart(2, "0"));
    setSeriesText("library-episode-release", formatDate(episode.release_date));
    setSeriesText("library-episode-runtime", runtime(episode.runtime_minutes));
    setSeriesText("library-episode-overview", episode.overview, "Metadata has not been resolved yet.");
    setSeriesText("library-episode-local-id", "Prime episode · " + episode.local_id + " · Season " + text(season.season_number, "?"));
    episodeModal.hidden = false;
  }

  function closeEpisode() {
    if (episodeModal) episodeModal.hidden = true;
  }

  document.querySelectorAll("[data-library-series-close]").forEach(function (node) {
    node.addEventListener("click", closeSeries);
  });
  document.querySelectorAll("[data-library-episode-close]").forEach(function (node) {
    node.addEventListener("click", closeEpisode);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (episodeModal && !episodeModal.hidden) closeEpisode();
    else if (seriesModal && !seriesModal.hidden) closeSeries();
  });

  window.addEventListener("prime:search", function (event) {
    if (!event.detail || event.detail.context !== "library") return;
    state.query = event.detail.value || "";
    renderTiles();
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) loadTiles();
  });
  window.addEventListener("beforeunload", function () { state.stopped = true; });

  loadTiles();
  window.setInterval(loadTiles, 3000);
}());
