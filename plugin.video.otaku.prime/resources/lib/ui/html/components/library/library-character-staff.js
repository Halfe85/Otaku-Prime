(function () {
  "use strict";

  var charactersRoot = document.getElementById("library-series-characters");
  var staffRoot = document.getElementById("library-series-staff");
  var seasonsRoot = document.getElementById("library-series-seasons");
  if (!charactersRoot || !staffRoot) return;

  function linkedStaffName(portrait) {
    var title = String(portrait && portrait.title || "").trim();
    return title.indexOf("View ") === 0 ? title.slice(5).trim() : "";
  }

  function staffCard(name) {
    if (!name) return null;
    var target = name.toLocaleLowerCase();
    var cards = staffRoot.querySelectorAll(".staff-card");
    for (var index = 0; index < cards.length; index += 1) {
      var heading = cards[index].querySelector(".library-person-heading h4");
      if (heading && heading.textContent.trim().toLocaleLowerCase() === target) {
        return cards[index];
      }
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
    card.setAttribute("aria-label", "Staff: " + name);
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
    document.querySelectorAll(".library-season-cast").forEach(function (node) {
      node.remove();
    });
  }

  function refresh() {
    charactersRoot.querySelectorAll(".character-card").forEach(decorateCharacter);
    cleanScopedPeople();
  }

  new MutationObserver(refresh).observe(charactersRoot, { childList: true, subtree: true });
  if (seasonsRoot) {
    new MutationObserver(cleanScopedPeople).observe(seasonsRoot, { childList: true, subtree: true });
  }

  refresh();
}());
