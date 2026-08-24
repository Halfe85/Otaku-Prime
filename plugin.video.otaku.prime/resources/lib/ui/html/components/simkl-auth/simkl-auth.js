(function () {
  var status = document.querySelector("[data-simkl-poll]");
  if (!status) return;
  var interval = Math.max(1, Number(status.getAttribute("data-interval")) || 5) * 1000;
  function poll() {
    fetch("/watchlist/simkl/status", { credentials: "same-origin", cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(function (result) {
        if (result.status === "connected") { window.location.reload(); return; }
        if (result.status === "expired" || result.status === "error") {
          status.textContent = result.message || "Authorization expired. Generate a new code.";
          return;
        }
        window.setTimeout(poll, Math.max(interval, (result.retry_after || 0) * 1000));
      })
      .catch(function () { window.setTimeout(poll, interval); });
  }
  window.setTimeout(poll, interval);
}());
