(function () {
  "use strict";

  var grid = document.getElementById("library-grid");
  if (!grid) return;

  var shell = grid.closest(".library-shell");
  var count = document.getElementById("library-count");
  var message = document.getElementById("library-message");
  var seriesModal = document.getElementById("library-series-modal");
  var episodeModal = document.getElementById("library-episode-modal");
  var expandedSeasons = new Set();
  var state = {
    series: [],
    movies: [],
    kind: "series",
    query: "",
    openSeriesId: null,
    openType: null,
    detail: null,
    busyTiles: false,
    busyDetail: false,
    stopped: false,
    tileSignature: "",
    detailSignature: "",
    peopleTab: "characters",
    mature: shell && Number(shell.dataset.mature) === 1 ? 1 : 0
  };

  function active() {
    var panel = document.getElementById("panel-library");
    return !state.stopped && !document.hidden && panel && !panel.hidden;
  }

  function text(value, fallback) {
    if (value === null || value === undefined || String(value).trim() === "") {
      return fallback || "—";
    }
    return String(value);
  }

  function hasHentaiGenre(series) {
    return Array.isArray(series && series.genres) && series.genres.some(function (genre) {
      return String(genre || "").trim().toLowerCase() === "hentai";
    });
  }

  function blurMatureArtwork(series) {
    return state.mature === 0 && hasHentaiGenre(series);
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

  function artworkImage(url, className, alt, onReady, onMissing) {
    var image = element("img", className);
    image.alt = alt || "";
    image.loading = "lazy";
    image.decoding = "async";
    image.addEventListener("load", function () { if (onReady) onReady(image); }, { once: true });
    image.addEventListener("error", function () {
      image.remove();
      if (onMissing) onMissing();
    }, { once: true });
    if (url) image.src = String(url);
    else if (onMissing) onMissing();
    return image;
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
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timeout = window.setTimeout(function () { if (controller) controller.abort(); }, 8000);
    try {
      var response = await fetch(url, {
        headers: { "Accept": "application/json" }, cache: "no-store",
        signal: controller ? controller.signal : undefined
      });
      var payload = null;
      try { payload = await response.json(); } catch (_) { payload = {}; }
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || "Prime library request failed.");
      }
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new Error("Prime library request timed out.");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function filteredSeries() {
    var query = state.query.trim().toLocaleLowerCase();
    var source = state.kind === "movies" ? state.movies : state.series;
    if (!query) return source.slice();
    return source.filter(function (item) {
      return [item.title, item.english_name, item.romaji_name, item.publish_year]
        .filter(function (value) { return value !== null && value !== undefined; })
        .some(function (value) { return String(value).toLocaleLowerCase().indexOf(query) >= 0; });
    });
  }

  function renderTiles() {
    var rows = filteredSeries();
    var source = state.kind === "movies" ? state.movies : state.series;
    var noun = state.kind === "movies" ? "movie" : "series";
    grid.replaceChildren();
    if (count) {
      count.textContent = source.length === 1
        ? "1 mediated " + noun
        : source.length + " mediated " + noun;
    }
    if (!rows.length) {
      var empty = element("div", "library-empty");
      empty.textContent = source.length
        ? "No library titles match this search."
        : "This library is empty. Titles will appear here as the mediator resolves the watchlist.";
      grid.appendChild(empty);
      return;
    }

    rows.forEach(function (item) {
      var tile = element("button", "library-tile");
      tile.type = "button";
      tile.dataset.seriesId = item.local_id;
      tile.dataset.libraryType = state.kind === "movies" ? "movie" : "series";
      tile.setAttribute("aria-label", "Open " + text(item.title, noun) + " details");

      var title = text(item.title, "Untitled series");
      if (blurMatureArtwork(item)) tile.classList.add("mature-artwork-blurred");
      var artwork = element("div", "library-tile-art");
      if (item.poster_url) {
        artwork.appendChild(artworkImage(item.poster_url,"library-tile-poster",""));
      } else {
        tile.classList.add("missing-poster");
      }
      artwork.appendChild(element("span", "library-tile-shade"));
      tile.appendChild(artwork);

      if (item.clearlogo_url) {
        var logoWrap = element("div", "library-tile-logo-wrap");
        var logo = artworkImage(item.clearlogo_url,"library-tile-logo",title,function () {
          tile.classList.add("has-logo");
        });
        logoWrap.appendChild(logo);
        tile.appendChild(logoWrap);
      }

      var content = element("div", "library-tile-content");
      var top = element("div", "library-tile-top");
      var fallbackTitle = element("h3", "library-tile-title", title);
      top.appendChild(fallbackTitle);
      top.appendChild(element("span", "library-tile-year", item.publish_year ? String(item.publish_year) : "Year pending"));
      content.appendChild(top);

      var body = element("div", "library-tile-next");
      body.textContent = state.kind === "movies"
        ? (item.release_date ? "Released · " + formatDate(item.release_date) : airingLabel(item))
        : item.next_episode_release_date
        ? "Next: E" + text(item.next_episode_number, "?") + " · " + formatDate(item.next_episode_release_date)
        : airingLabel(item);
      content.appendChild(body);

      var meta = element("div", "library-tile-meta");
      meta.appendChild(element("span", "library-chip " + statusClass(item),
        item.next_episode_release_date ? "Running" : airingLabel(item)));
      if (state.kind === "movies") {
        meta.appendChild(element("span", "library-chip", runtime(item.runtime_minutes)));
      } else {
        meta.appendChild(element("span", "library-chip",
          text(item.season_count, "0") + (Number(item.season_count) === 1 ? " season" : " seasons")));
        meta.appendChild(element("span", "library-chip",
          text(item.episode_count, "0") + (Number(item.episode_count) === 1 ? " episode" : " episodes")));
      }
      content.appendChild(meta);
      tile.appendChild(content);

      tile.addEventListener("click", function () {
        openLibraryItem(state.kind === "movies" ? "movie" : "series",item.local_id);
      });
      grid.appendChild(tile);
    });
  }

  async function loadTiles() {
    if (state.busyTiles || !active()) return;
    state.busyTiles = true;
    try {
      var payloads = await Promise.all([
        fetchJson("/api/library/series"),fetchJson("/api/library/movies")
      ]);
      var nextSeries = Array.isArray(payloads[0].series) ? payloads[0].series : [];
      var nextMovies = Array.isArray(payloads[1].movies) ? payloads[1].movies : [];
      var signature = JSON.stringify([nextSeries,nextMovies]);
      if (signature !== state.tileSignature) {
        state.series = nextSeries;
        state.movies = nextMovies;
        state.tileSignature = signature;
        renderTiles();
      }
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

  function renderSeriesArtwork(series, title) {
    var hero = document.getElementById("library-series-hero");
    var banner = document.getElementById("library-series-banner");
    var logo = document.getElementById("library-series-logo");
    if (!hero || !banner || !logo) return;
    hero.classList.remove("has-banner","has-logo");
    hero.classList.toggle("mature-artwork-blurred",blurMatureArtwork(series));
    banner.hidden = true;
    banner.removeAttribute("src");
    logo.hidden = true;
    logo.removeAttribute("src");
    logo.alt = "";
    if (series.banner_url) {
      banner.onload = function () { banner.hidden = false; hero.classList.add("has-banner"); };
      banner.onerror = function () { banner.hidden = true; hero.classList.remove("has-banner"); };
      banner.src = String(series.banner_url);
    }
    if (series.clearlogo_url) {
      logo.onload = function () {
        logo.hidden = false;
        logo.alt = title;
        hero.classList.add("has-logo");
      };
      logo.onerror = function () { logo.hidden = true; hero.classList.remove("has-logo"); };
      logo.src = String(series.clearlogo_url);
    }
  }

  function castEntries(value) {
    return Array.isArray(value) ? value.filter(function (entry) {
      return entry && ((entry.character && entry.character.local_id) || entry.character_name);
    }) : [];
  }

  function fallbackCastCard(entry) {
    var card = element("div", "library-cast-card-fallback");
    var character = entry && entry.character && entry.character.name
      ? entry.character.name : entry && entry.character_name;
    var person = entry && entry.person && entry.person.name
      ? entry.person.name : entry && entry.person_name;
    card.appendChild(element("strong", "", text(character, "Character not resolved")));
    card.appendChild(element("span", "", text(person, "Unknown actor")));
    return card;
  }

  function castTile(entry) {
    if (window.PrimeUIElements && typeof window.PrimeUIElements.createCastTile === "function") {
      return window.PrimeUIElements.createCastTile(entry);
    }
    return fallbackCastCard(entry);
  }

  function fillCastRoot(root, cast, emptyMessage) {
    if (!root) return;
    root.replaceChildren();
    cast = castEntries(cast);
    if (!cast.length) {
      root.appendChild(element("p", "library-muted", emptyMessage || "No cast metadata resolved yet."));
      return;
    }
    cast.forEach(function (entry) {
      root.appendChild(castTile(entry));
    });
  }

  function peopleEntries(value) {
    return Array.isArray(value) ? value.filter(function (entry) {
      return entry && entry.local_id;
    }) : [];
  }

  function portraitLayer(entity, className, fallbackName) {
    var layer = element("span", "library-portrait-layer " + className);
    var name = text(entity && entity.name, fallbackName);
    layer.appendChild(element("span", "library-portrait-fallback", name.slice(0, 1).toUpperCase()));
    if (entity && entity.image_url) {
      var image = document.createElement("img");
      image.src = entity.image_url;
      image.alt = name;
      image.loading = "lazy";
      image.addEventListener("error", function () { image.remove(); }, { once: true });
      layer.appendChild(image);
    }
    return layer;
  }

  function portrait(entity, kind, alternateStaff) {
    var root = element("div", "library-person-portrait " + kind);
    var name = text(entity && entity.name, kind === "character" ? "Character" : "Staff");
    root.appendChild(portraitLayer(entity, "primary", name));
    if (alternateStaff && alternateStaff.image_url) {
      root.classList.add("has-staff-portrait");
      root.tabIndex = 0;
      root.title = "View " + text(alternateStaff.name, "linked staff");
      root.setAttribute("aria-label", name + "; focus to view staff " +
        text(alternateStaff.name, "member"));
      root.appendChild(portraitLayer(alternateStaff, "alternate", "Staff"));
    }
    return root;
  }

  function compactDate(value) {
    if (!value) return "";
    var raw = String(value);
    var full = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
    if (full) return full[3] + "." + full[2] + "." + full[1];
    var month = /^(\d{4})-(\d{2})$/.exec(raw);
    if (month) return month[2] + "." + month[1];
    return raw;
  }

  function relationshipLabel(value) {
    return String(value || "voice_actor").replace(/[_-]+/g, " ").replace(/\b\w/g, function (letter) {
      return letter.toUpperCase();
    });
  }

  function mediaLabel(link) {
    if (!link) return "Library";
    if (link.scope === "movie") return "Movie";
    if (link.scope === "episode") {
      var season = String(link.season_number === null || link.season_number === undefined ? "?" : link.season_number).padStart(2, "0");
      var episode = String(link.episode_number === null || link.episode_number === undefined ? "?" : link.episode_number).padStart(2, "0");
      return "S" + season + "E" + episode + (link.title ? " · " + link.title : "");
    }
    if (link.scope === "season") {
      return Number(link.season_number) === 0
        ? "Specials"
        : "Season " + text(link.season_number, "?");
    }
    return "Series";
  }

  function chipList(label, rows, valueForRow, emptyValue) {
    var group = element("div", "library-person-links");
    group.appendChild(element("span", "library-person-links-label", label));
    var values = element("div", "library-person-chips");
    if (!rows.length) {
      values.appendChild(element("span", "library-person-empty", emptyValue));
    } else {
      rows.forEach(function (row) {
        values.appendChild(element("span", "library-person-chip", valueForRow(row)));
      });
    }
    group.appendChild(values);
    return group;
  }

  function triviaBlock(value) {
    if (!value) return null;
    var details = element("details", "library-person-trivia");
    details.appendChild(element("summary", "", "Biography & trivia"));
    details.appendChild(element("p", "", value));
    return details;
  }

  function characterCard(character) {
    var card = element("article", "library-person-card character-card");
    var staff = peopleEntries(character.staff);
    var portraitStaff = staff.find(function (person) { return !!person.image_url; });
    var header = element("header", "library-person-header");
    header.appendChild(portrait(character, "character", portraitStaff));
    var heading = element("div", "library-person-heading");
    heading.appendChild(element("h4", "", text(character.name, "Unknown character")));
    var media = Array.isArray(character.media_links) ? character.media_links : [];
    heading.appendChild(element("p", "", staff.length + (staff.length === 1 ? " staff credit" : " staff credits") + " · " + media.length + (media.length === 1 ? " placement" : " placements")));
    header.appendChild(heading);
    card.appendChild(header);
    card.appendChild(chipList("Staff", staff, function (person) {
      var language = person.language ? " · " + person.language : "";
      return text(person.name, "Unknown staff") + " · " + relationshipLabel(person.credit_type) + language;
    }, "No linked staff"));
    card.appendChild(chipList("Appears in", media, mediaLabel, "No media placement"));
    var trivia = triviaBlock(character.trivia);
    if (trivia) card.appendChild(trivia);
    return card;
  }

  function staffCard(person) {
    var card = element("article", "library-person-card staff-card");
    var header = element("header", "library-person-header");
    header.appendChild(portrait(person, "staff"));
    var heading = element("div", "library-person-heading");
    heading.appendChild(element("h4", "", text(person.name, "Unknown staff")));
    var life=[];
    if (person.age !== null && person.age !== undefined) life.push("Age " + person.age);
    if (person.date_of_birth) life.push("Born " + compactDate(person.date_of_birth));
    if (person.date_of_death) life.push("Died " + compactDate(person.date_of_death));
    heading.appendChild(element("p", "", life.length ? life.join(" · ") : "Life details unavailable"));
    header.appendChild(heading);
    card.appendChild(header);
    var roles = Array.isArray(person.roles) ? person.roles : [];
    var media = Array.isArray(person.media_links) ? person.media_links : [];
    card.appendChild(chipList("Roles", roles, function (role) {
      return relationshipLabel(role.credit_type);
    }, "Staff role unavailable"));
    card.appendChild(chipList("Appears in", media, mediaLabel, "No media placement"));
    var trivia = triviaBlock(person.trivia);
    if (trivia) card.appendChild(trivia);
    return card;
  }

  function selectPeopleTab(tab) {
    state.peopleTab = tab === "staff" ? "staff" : "characters";
    document.querySelectorAll("[data-library-people-tab]").forEach(function (button) {
      var selected = button.dataset.libraryPeopleTab === state.peopleTab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", selected ? "true" : "false");
    });
    var charactersPanel = document.getElementById("library-characters-panel");
    var staffPanel = document.getElementById("library-staff-panel");
    if (charactersPanel) charactersPanel.hidden = state.peopleTab !== "characters";
    if (staffPanel) staffPanel.hidden = state.peopleTab !== "staff";
  }

  function renderPeople(series) {
    var characters = peopleEntries(series.characters);
    var staff = peopleEntries(series.staff);
    var characterRoot = document.getElementById("library-series-characters");
    var staffRoot = document.getElementById("library-series-staff");
    var characterCount = document.getElementById("library-character-count");
    var staffCount = document.getElementById("library-staff-count");
    var peopleCount = document.getElementById("library-people-count");
    if (characterCount) characterCount.textContent = String(characters.length);
    if (staffCount) staffCount.textContent = String(staff.length);
    if (peopleCount) peopleCount.textContent = characters.length + " characters · " + staff.length + " staff";
    if (characterRoot) {
      characterRoot.replaceChildren();
      if (!characters.length) characterRoot.appendChild(element("p", "library-muted", "No character metadata resolved yet."));
      characters.forEach(function (character) { characterRoot.appendChild(characterCard(character)); });
    }
    if (staffRoot) {
      staffRoot.replaceChildren();
      if (!staff.length) staffRoot.appendChild(element("p", "library-muted", "No staff metadata resolved yet."));
      staff.forEach(function (person) { staffRoot.appendChild(staffCard(person)); });
    }
    selectPeopleTab(state.peopleTab);
  }

  function resolveSeasonCast(series, season) {
    var seasonCast = castEntries(season && season.cast);
    if (seasonCast.length) return { entries: seasonCast, label: "Season cast", inherited: false };

    var seriesCast = castEntries(series && series.cast);
    if (seriesCast.length) return { entries: seriesCast, label: "Inherited from series", inherited: true };

    return { entries: [], label: "", inherited: false };
  }

  function resolveEpisodeCast(series, season, episode) {
    var episodeCast = castEntries(episode && episode.cast);
    if (episodeCast.length) return { entries: episodeCast, label: "Episode cast" };

    var seasonCast = castEntries(season && season.cast);
    if (seasonCast.length) return { entries: seasonCast, label: "Inherited from season" };

    var seriesCast = castEntries(series && series.cast);
    if (seriesCast.length) return { entries: seriesCast, label: "Inherited from series" };

    return { entries: [], label: "" };
  }

  function seasonCastBlock(series, season) {
    var resolved = resolveSeasonCast(series, season);
    var block = element("section", "library-season-cast");
    var heading = element("div", "library-section-heading compact");
    heading.appendChild(element("h4", "", "Characters & staff"));
    heading.appendChild(element("span", "library-section-note", resolved.label));
    block.appendChild(heading);

    var root = element("div", "library-cast compact");
    fillCastRoot(root, resolved.entries, "No cast metadata resolved for this season.");
    block.appendChild(root);
    return block;
  }

  function renderEpisodeCast(series, season, episode) {
    var resolved = resolveEpisodeCast(series, season, episode);
    var root = document.getElementById("library-episode-cast");
    var scope = document.getElementById("library-episode-cast-scope");
    if (scope) scope.textContent = resolved.label;
    fillCastRoot(root, resolved.entries, "No cast metadata resolved for this episode.");
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
      episodeRoot.appendChild(seasonCastBlock(series, season));
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
    setSeriesText("library-series-subtitle", year);
    renderSeriesArtwork(series,title);
    setSeriesText("library-series-english", series.english_name || series.title, "Not resolved yet");
    setSeriesText("library-series-year", series.publish_year, "Not resolved yet");
    setSeriesText("library-series-runtime", runtime(series.runtime_minutes));
    setSeriesText("library-series-airing", airingLabel(series));
    setSeriesText("library-series-age-rating", series.age_rating,
      Number(series.mature) === 1 ? "18+" : "Not resolved yet");
    renderTerms("library-series-genres", series.genres);
    renderTerms("library-series-themes", series.themes);
    setSeriesText("library-series-overview", series.overview, "Metadata has not been resolved yet.");
    var isMovie=series.library_type === "movie";
    setSeriesText("library-series-local-id", "Prime " + (isMovie ? "movie" : "series") + " · " + series.local_id);
    renderPeople(series);
    var seasonsSection=document.getElementById("library-series-seasons-section");
    if (seasonsSection) seasonsSection.hidden=isMovie;
    if (!isMovie) renderSeasons(series);
  }

  function renderTerms(id, values) {
    var root = document.getElementById(id);
    if (!root) return;
    root.replaceChildren();
    values = Array.isArray(values) ? values.filter(Boolean) : [];
    if (!values.length) {
      root.appendChild(element("span", "library-term-empty", "Not resolved yet"));
      return;
    }
    values.forEach(function (value) {
      root.appendChild(element("span", "library-term", String(value)));
    });
  }

  async function loadSeriesDetail(localId, silent) {
    if (state.busyDetail || !localId) return;
    state.busyDetail = true;
    try {
      var movie=state.openType === "movie";
      var payload = await fetchJson("/api/library/"+(movie ? "movies/" : "series/") + encodeURIComponent(localId));
      if (state.openSeriesId !== localId) return;
      var detail=movie ? payload.movie : payload.series;
      var signature = JSON.stringify(detail);
      if (signature !== state.detailSignature) {
        state.detailSignature = signature;
        renderSeriesDetail(detail);
      }
    } catch (error) {
      if (!silent) showMessage(error.message || "Could not load series details.", true);
    } finally {
      state.busyDetail = false;
    }
  }

  async function openLibraryItem(type,localId) {
    state.openSeriesId = localId;
    state.openType = type === "movie" ? "movie" : "series";
    state.detail = null;
    state.detailSignature = "";
    if (seriesModal) seriesModal.hidden = false;
    document.body.classList.add("library-modal-open");
    setSeriesText("library-series-title", state.openType === "movie" ? "Loading movie…" : "Loading series…");
    setSeriesText("library-series-subtitle", "Mediator catalogue");
    renderSeriesArtwork({},"Loading series");
    await loadSeriesDetail(localId, false);
  }

  function closeSeries() {
    closeEpisode();
    state.openSeriesId = null;
    state.openType = null;
    state.detail = null;
    state.detailSignature = "";
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
    renderEpisodeCast(series, season, episode);
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
  document.querySelectorAll("[data-library-people-tab]").forEach(function (node) {
    node.addEventListener("click", function () {
      selectPeopleTab(node.dataset.libraryPeopleTab);
    });
  });
  document.querySelectorAll("[data-library-kind]").forEach(function (node) {
    node.addEventListener("click",function () {
      state.kind=node.dataset.libraryKind === "movies" ? "movies" : "series";
      document.querySelectorAll("[data-library-kind]").forEach(function (button) {
        var selected=button.dataset.libraryKind === state.kind;
        button.classList.toggle("active",selected);
        button.setAttribute("aria-selected",selected ? "true" : "false");
      });
      renderTiles();
    });
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

  window.addEventListener("prime:maturechange", function (event) {
    state.mature=Number(event && event.detail && event.detail.mature) === 1 ? 1 : 0;
    if (shell) shell.dataset.mature=String(state.mature);
    renderTiles();
    if (state.detail) {
      renderSeriesArtwork(
        state.detail,text(state.detail.title || state.detail.english_name || state.detail.romaji_name,
                          "Untitled series"));
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (active()) loadTiles();
  });
  window.addEventListener("prime:tabchange", function (event) {
    if (event.detail && event.detail.id === "library") loadTiles();
  });
  window.addEventListener("beforeunload", function () { state.stopped = true; });

  loadTiles();
  window.setInterval(loadTiles, 10000);
}());
