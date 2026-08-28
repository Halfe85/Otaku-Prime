(function (global) {
  "use strict";

  function firstValue() {
    for (var index = 0; index < arguments.length; index += 1) {
      var value = arguments[index];
      if (value !== null && value !== undefined && String(value).trim() !== "") {
        return String(value);
      }
    }
    return "";
  }

  function safeImageUrl(value) {
    if (!value) return "";
    try {
      var parsed = new URL(String(value), global.location.href);
      return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
    } catch (_) {
      return "";
    }
  }

  function initials(value) {
    var parts = String(value || "?").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    return parts.slice(0, 2).map(function (part) { return part.charAt(0).toUpperCase(); }).join("");
  }

  function imageLayer(kind, url, name) {
    var layer = document.createElement("span");
    layer.className = "prime-cast-tile__face prime-cast-tile__face--" + kind;

    if (url) {
      var image = document.createElement("img");
      image.src = url;
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      layer.appendChild(image);
    } else {
      var fallback = document.createElement("span");
      fallback.className = "prime-cast-tile__placeholder";
      fallback.textContent = initials(name);
      fallback.setAttribute("aria-hidden", "true");
      layer.appendChild(fallback);
    }
    return layer;
  }

  function normalizeCastEntry(entry) {
    entry = entry || {};
    var person = entry.person || {};
    var character = entry.character || {};

    var personName = firstValue(
      person.name,
      person.english_name,
      entry.person_name,
      entry.actor_name,
      "Unknown actor"
    );
    var characterName = firstValue(
      character.name,
      character.english_name,
      entry.character_name,
      "Character not resolved"
    );

    return {
      personName: personName,
      characterName: characterName,
      personImage: safeImageUrl(firstValue(
        entry.person_image_url_override,
        person.image_url,
        entry.person_image_url,
        person.thumb_url
      )),
      characterImage: safeImageUrl(firstValue(
        entry.character_image_url_override,
        character.image_url,
        entry.character_image_url,
        character.thumb_url
      )),
      creditType: firstValue(entry.credit_type, "voice_actor"),
      sourceProvider: firstValue(entry.source_provider)
    };
  }

  function creditLabel(value) {
    var normalized = String(value || "").trim().toLowerCase().replace(/[_-]+/g, " ");
    if (normalized === "voice actor") return "Voice actor";
    if (normalized === "actor") return "Actor";
    if (normalized === "narrator") return "Narrator";
    return normalized ? normalized.replace(/\b\w/g, function (letter) { return letter.toUpperCase(); }) : "Cast";
  }

  function createCastTile(entry) {
    var item = normalizeCastEntry(entry);
    var tile = document.createElement("article");
    tile.className = "prime-cast-tile";
    tile.tabIndex = 0;
    tile.setAttribute(
      "aria-label",
      item.characterName + ", " + creditLabel(item.creditType) + " " + item.personName
    );
    tile.title = "Hover, focus, or tap to reveal " + item.personName;

    var orb = document.createElement("span");
    orb.className = "prime-cast-tile__orb";
    orb.setAttribute("aria-hidden", "true");
    orb.appendChild(imageLayer("character", item.characterImage, item.characterName));
    orb.appendChild(imageLayer("person", item.personImage, item.personName));

    var copy = document.createElement("span");
    copy.className = "prime-cast-tile__copy";

    var characterName = document.createElement("strong");
    characterName.className = "prime-cast-tile__character";
    characterName.textContent = item.characterName;

    var personName = document.createElement("span");
    personName.className = "prime-cast-tile__person";
    personName.textContent = item.personName;

    var role = document.createElement("span");
    role.className = "prime-cast-tile__role";
    role.textContent = creditLabel(item.creditType);

    copy.appendChild(characterName);
    copy.appendChild(personName);
    copy.appendChild(role);
    tile.appendChild(orb);
    tile.appendChild(copy);

    function togglePersonView() {
      tile.classList.toggle("prime-cast-tile--person");
    }

    tile.addEventListener("click", togglePersonView);
    tile.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      togglePersonView();
    });

    return tile;
  }

  global.PrimeUIElements = global.PrimeUIElements || {};
  global.PrimeUIElements.createCastTile = createCastTile;
  global.PrimeUIElements.normalizeCastEntry = normalizeCastEntry;
}(window));
