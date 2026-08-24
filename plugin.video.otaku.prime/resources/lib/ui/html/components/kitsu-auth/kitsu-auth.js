(function () {
  var form = document.querySelector(".credentials-form");
  if (!form) return;
  form.addEventListener("submit", function () {
    var button = form.querySelector('button[type="submit"]');
    if (button) { button.disabled = true; button.textContent = "Connecting…"; }
  });
}());
