(function () {
  var callback = document.querySelector('textarea[name="callback_url"]');
  var authorize = document.querySelector("[data-auth-popup]");
  var status = document.querySelector(".popup-status");
  if (!authorize) return;
  authorize.addEventListener("click", function (event) {
    event.preventDefault();
    var width = 720;
    var height = 760;
    var left = Math.max(0, (window.screen.width - width) / 2);
    var top = Math.max(0, (window.screen.height - height) / 2);
    var popup = window.open(authorize.href, "mal-auth", "popup=yes,width=" + width + ",height=" + height + ",left=" + left + ",top=" + top);
    if (!popup) {
      window.open(authorize.href, "_blank", "noopener,noreferrer");
      if (status) status.textContent = "Authorization opened in a new tab. Return here with the final URL.";
      return;
    }
    if (status) status.textContent = "Approve access, copy the final URL, then close the popup.";
    var watcher = window.setInterval(function () {
      if (!popup.closed) return;
      window.clearInterval(watcher);
      if (status) status.textContent = "Paste the complete MyAnimeList URL below.";
      if (callback) callback.focus();
    }, 500);
  });
}());
