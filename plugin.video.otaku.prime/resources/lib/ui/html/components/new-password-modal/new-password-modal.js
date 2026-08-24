(function () {
  var modal = document.getElementById("new-password-modal");
  if (!modal) return;
  var form = modal.querySelector("form");
  var password = form.querySelector('input[name="new_password"]');
  var confirmation = form.querySelector('input[name="confirm_password"]');
  var error = form.querySelector(".password-match-error");
  form.addEventListener("submit", function (event) {
    var matches = password.value === confirmation.value;
    error.hidden = matches;
    if (!matches) event.preventDefault();
  });
}());
