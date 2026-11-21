// Applique le thème avant l'affichage et suit le thème système tant que l'utilisateur ne choisit pas.
(function () {
  var root = document.documentElement;
  var saved = null;
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var followsSystem = true;
  try { saved = localStorage.getItem("theme"); } catch (e) {}

  function setTheme(theme) {
    root.setAttribute("data-theme", theme);
  }

  if (saved === "dark" || saved === "light") {
    followsSystem = false;
    setTheme(saved);
  } else {
    setTheme(media && media.matches ? "dark" : "light");
  }

  if (media) {
    var onSystemChange = function (event) {
      if (followsSystem) setTheme(event.matches ? "dark" : "light");
    };
    if (media.addEventListener) media.addEventListener("change", onSystemChange);
    else if (media.addListener) media.addListener(onSystemChange);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    function isDark() {
      return root.getAttribute("data-theme") === "dark";
    }
    function refresh() {
      var dark = isDark();
      button.setAttribute("aria-pressed", String(dark));
      button.setAttribute("aria-label", dark ? "Passer en mode clair" : "Passer en mode sombre");
      button.textContent = dark ? "☀" : "☾";
    }
    button.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      followsSystem = false;
      setTheme(next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      refresh();
    });
    refresh();
  });
})();
