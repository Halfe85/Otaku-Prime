(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-tab]"));
  function selectTab(id) {
    tabs.forEach(function (tab) {
      var selected = tab.getAttribute("data-tab") === id;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      document.getElementById("panel-" + tab.getAttribute("data-tab")).hidden = !selected;
    });
    if (history.replaceState) history.replaceState(null, "", "#" + id);
  }
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () { selectTab(tab.getAttribute("data-tab")); });
  });
  var requested = location.hash.slice(1);
  if (document.getElementById("panel-" + requested)) selectTab(requested);

  var provider = document.getElementById("metadata-provider");
  function updateMetadataFields() {
    if (!provider) return;
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-metadata-provider-field]"),
      function (field) {
        field.hidden = field.getAttribute("data-metadata-provider-field") !== provider.value;
      }
    );
  }
  if (provider) {
    provider.addEventListener("change", updateMetadataFields);
    updateMetadataFields();
  }
}());
