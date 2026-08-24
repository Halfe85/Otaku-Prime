(function () {
  var token = document.querySelector('textarea[name="token"]');
  if (token) token.addEventListener("paste", function () { token.classList.remove("invalid"); });
}());
