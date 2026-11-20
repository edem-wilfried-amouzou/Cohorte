// Applique le thème enregistré avant l'affichage (évite le flash) et gère la bascule clair/sombre.
(function () {
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem("theme"); } catch (e) {}
  if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    function isDark() {
      var forced = root.getAttribute("data-theme");
      if (forced) return forced === "dark";
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
    function refresh() {
      var dark = isDark();
      button.setAttribute("aria-pressed", String(dark));
      button.setAttribute("aria-label", dark ? "Passer en mode clair" : "Passer en mode sombre");
      button.textContent = dark ? "☀" : "☾";
    }
    button.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      refresh();
    });
    refresh();
  });
})();
