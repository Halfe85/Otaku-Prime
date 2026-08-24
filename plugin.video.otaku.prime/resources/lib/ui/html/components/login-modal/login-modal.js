(function () {
  var modal = document.getElementById("login-modal");
  if (!modal) return;
  var username = modal.querySelector('input[name="username"]');
  if (username) username.focus();
}());
