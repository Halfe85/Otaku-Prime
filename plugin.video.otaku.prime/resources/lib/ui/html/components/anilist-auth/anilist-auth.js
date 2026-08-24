(function () {
  var token = document.querySelector('textarea[name="token"]');
  var authorize = document.querySelector("[data-auth-popup]");
  var status = document.querySelector(".popup-status");
  if (token) token.addEventListener("paste", function () { token.classList.remove("invalid"); });
  if (!authorize) return;
  authorize.addEventListener("click", function (event) {
    event.preventDefault();
    var width = 720;
    var height = 760;
    var left = Math.max(0, (window.screen.width - width) / 2);
    var top = Math.max(0, (window.screen.height - height) / 2);
    var popup = window.open(authorize.href, "anilist-auth", "popup=yes,width=" + width + ",height=" + height + ",left=" + left + ",top=" + top);
    if (!popup) {
      window.open(authorize.href, "_blank", "noopener,noreferrer");
      if (status) status.textContent = "Authorization opened in a new tab. Return here and paste the token.";
      return;
    }
    if (status) status.textContent = "Complete authorization in the popup, then return here.";
    var watcher = window.setInterval(function () {
      if (!popup.closed) return;
      window.clearInterval(watcher);
      if (status) status.textContent = "Authorization finished. Paste the token from AniList below.";
      if (token) token.focus();
    }, 500);
  });
}());
