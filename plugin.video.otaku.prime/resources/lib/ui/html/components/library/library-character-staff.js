(function () {
  "use strict";

  var charactersRoot = document.getElementById("library-series-characters");
  var staffRoot = document.getElementById("library-series-staff");
  var actorSource = document.getElementById("library-linked-actor-source");
  var seasonsRoot = document.getElementById("library-series-seasons");
  var libraryGrid = document.getElementById("library-grid");
  var staffCount = document.getElementById("library-staff-count");
  var peopleCount = document.getElementById("library-people-count");
  var characterCount = document.getElementById("library-character-count");
  var staffMore = document.getElementById("library-staff-more");
  var staffVisibleRows = 1;
  if (!charactersRoot || !staffRoot || !actorSource) return;

  function staffColumns() {
    var tracks = window.getComputedStyle(staffRoot).gridTemplateColumns;
    if (!tracks || tracks === "none") return 1;
    return Math.max(1, tracks.trim().split(/\s+/).length);
  }

  function applyStaffDisclosure() {
    var cards = Array.prototype.slice.call(staffRoot.querySelectorAll(":scope > .staff-card"));
    var columns = staffColumns();
    var visible = Math.min(cards.length, columns * staffVisibleRows);
    cards.forEach(function (card, index) { card.hidden = index >= visible; });
    if (!staffMore) return;
    var hasMore = visible < cards.length;
    staffMore.hidden = cards.length <= columns && staffVisibleRows === 1;
    staffMore.setAttribute("aria-expanded", staffVisibleRows > 1 ? "true" : "false");
    var label = staffMore.querySelector("span");
    if (label) label.textContent = hasMore ? "Show 2 more rows" : "Show less";
  }

  function linkedStaffName(portrait) {
    var title = String(portrait && portrait.title || "").trim();
    return title.indexOf("View ") === 0 ? title.slice(5).trim() : "";
  }

  function cardName(card) {
    var heading = card && card.querySelector(".library-person-heading h4");
    return heading ? heading.textContent.trim() : "";
  }

  function linkedActorNames() {
    var names = new Set();
    charactersRoot.querySelectorAll(".library-person-portrait.has-staff-portrait").forEach(function (portrait) {
      var name = linkedStaffName(portrait);
      if (name) names.add(name.toLocaleLowerCase());
    });
    return names;
  }

  function repartitionStaff() {
    var linked = linkedActorNames();
    var all = [];
    actorSource.querySelectorAll(".staff-card").forEach(function (card) { all.push(card); });
    staffRoot.querySelectorAll(".staff-card").forEach(function (card) { all.push(card); });

    var latestByName = new Map();
    all.forEach(function (card) {
      var key = cardName(card).toLocaleLowerCase();
      if (key) latestByName.set(key, card);
    });

    actorSource.replaceChildren();
    staffRoot.replaceChildren();
    latestByName.forEach(function (card, key) {
      if (linked.has(key)) actorSource.appendChild(card);
      else staffRoot.appendChild(card);
    });

    if (!staffRoot.children.length) {
      var empty = document.createElement("p");
      empty.className = "library-muted";
      empty.textContent = "No standalone staff metadata resolved yet.";
      staffRoot.appendChild(empty);
    }

    var visibleStaff = staffRoot.querySelectorAll(".staff-card").length;
    var visibleCharacters = charactersRoot.querySelectorAll(".character-card").length;
    if (staffCount) staffCount.textContent = String(visibleStaff);
    if (characterCount) characterCount.textContent = String(visibleCharacters);
    if (peopleCount) peopleCount.textContent = visibleCharacters + " characters · " + visibleStaff + " staff";
    applyStaffDisclosure();
  }

  function staffCard(name) {
    if (!name) return null;
    var target = name.toLocaleLowerCase();
    var cards = actorSource.querySelectorAll(".staff-card");
    for (var index = 0; index < cards.length; index += 1) {
      if (cardName(cards[index]).toLocaleLowerCase() === target) return cards[index];
    }
    return null;
  }

  function staffOverlay(card, source, name) {
    var existing = card.querySelector(":scope > .library-staff-hover-content");
    if (existing && existing.dataset.staffName === name) return existing;
    if (existing) existing.remove();
    var overlay = document.createElement("div");
    overlay.className = "library-staff-hover-content";
    overlay.dataset.staffName = name;
    overlay.setAttribute("aria-hidden", "true");
    Array.prototype.forEach.call(source.children, function (child) {
      overlay.appendChild(child.cloneNode(true));
    });
    card.appendChild(overlay);
    return overlay;
  }

  function activate(card, portrait) {
    var name = linkedStaffName(portrait);
    var source = staffCard(name);
    if (!source) return;
    var overlay = staffOverlay(card, source, name);
    overlay.setAttribute("aria-hidden", "false");
    card.classList.add("staff-hover-active");
    card.setAttribute("aria-label", "Actor: " + name);
  }

  function deactivate(card) {
    card.classList.remove("staff-hover-active");
    var overlay = card.querySelector(":scope > .library-staff-hover-content");
    if (overlay) overlay.setAttribute("aria-hidden", "true");
    card.removeAttribute("aria-label");
  }

  function decorateCharacter(card) {
    if (!card || card.dataset.staffHoverReady === "1") return;
    var portrait = card.querySelector(".library-person-portrait.has-staff-portrait");
    if (!portrait) return;
    card.dataset.staffHoverReady = "1";
    portrait.addEventListener("pointerenter", function () { activate(card, portrait); });
    portrait.addEventListener("pointerleave", function () { deactivate(card); });
    portrait.addEventListener("focus", function () { activate(card, portrait); });
    portrait.addEventListener("blur", function () { deactivate(card); });
  }

  function cleanScopedPeople() {
    document.querySelectorAll(".library-season-cast").forEach(function (node) { node.remove(); });
  }

  function cleanDuplicateTileStatus() {
    if (!libraryGrid) return;
    libraryGrid.querySelectorAll(".library-tile-next").forEach(function (node) {
      if (node.textContent.trim().toLowerCase() === "finished airing") node.remove();
    });
  }

  var characterObserver = new MutationObserver(scheduleRefresh);
  var staffObserver = new MutationObserver(scheduleRefresh);
  var seasonObserver = seasonsRoot ? new MutationObserver(cleanScopedPeople) : null;
  var gridObserver = libraryGrid ? new MutationObserver(cleanDuplicateTileStatus) : null;
  var scheduled = false;

  function scheduleRefresh() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(function () {
      scheduled = false;
      characterObserver.disconnect();
      staffObserver.disconnect();
      repartitionStaff();
      charactersRoot.querySelectorAll(".character-card").forEach(decorateCharacter);
      cleanScopedPeople();
      cleanDuplicateTileStatus();
      characterObserver.observe(charactersRoot, { childList: true, subtree: true });
      staffObserver.observe(staffRoot, { childList: true, subtree: true });
    });
  }

  function resetStaffDisclosure() {
    staffVisibleRows = 1;
    applyStaffDisclosure();
  }

  if (staffMore) {
    staffMore.addEventListener("click", function () {
      var cards = staffRoot.querySelectorAll(":scope > .staff-card");
      var visibleLimit = staffColumns() * staffVisibleRows;
      staffVisibleRows = visibleLimit < cards.length ? staffVisibleRows + 2 : 1;
      applyStaffDisclosure();
    });
  }
  window.addEventListener("prime:librarymodalreset", resetStaffDisclosure);
  window.addEventListener("resize", applyStaffDisclosure);

  characterObserver.observe(charactersRoot, { childList: true, subtree: true });
  staffObserver.observe(staffRoot, { childList: true, subtree: true });
  if (seasonObserver) seasonObserver.observe(seasonsRoot, { childList: true, subtree: true });
  if (gridObserver) gridObserver.observe(libraryGrid, { childList: true, subtree: true });
  scheduleRefresh();
  cleanDuplicateTileStatus();
}());
