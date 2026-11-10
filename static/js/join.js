// Page publique : recherche du nom dans la liste de la classe (liste déroulante accessible).
(() => {
  const form = document.getElementById("join-form");
  if (!form) return;
  const input = form.querySelector("#name");
  const hidden = form.querySelector("#member_id");
  const list = form.querySelector("#combo-list");
  const picked = form.querySelector("#picked");
  const pickedName = form.querySelector("#picked-name");
  const allowUnlisted = form.dataset.allowUnlisted === "1";
  let results = [];
  let active = -1;
  let timer = null;
  let controller = null;

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };

  const highlight = (index) => {
    const options = list.querySelectorAll("li[role='option']");
    options.forEach((li, i) => li.setAttribute("aria-selected", String(i === index)));
    active = index;
    if (options[index]) {
      input.setAttribute("aria-activedescendant", options[index].id);
      options[index].scrollIntoView({ block: "nearest" });
    }
  };

  const choose = (item) => {
    input.value = item.name;
    hidden.value = item.id;
    pickedName.textContent = item.name;
    picked.classList.add("on");
    close();
  };

  const render = () => {
    list.replaceChildren();
    if (!results.length) {
      const li = document.createElement("li");
      li.className = "none";
      li.textContent = allowUnlisted
        ? "Aucun nom trouvé. Tu peux envoyer ton nom tel quel, il sera vérifié par ton délégué."
        : "Aucun nom trouvé. Vérifie l'orthographe ou préviens ton délégué.";
      list.appendChild(li);
    }
    results.forEach((item, i) => {
      const li = document.createElement("li");
      li.id = `opt-${item.id}`;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.textContent = item.name;
      li.addEventListener("mousedown", (event) => { event.preventDefault(); choose(item); });
      list.appendChild(li);
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = -1;
  };

  const search = async (query) => {
    if (controller) controller.abort();
    controller = new AbortController();
    try {
      const response = await fetch(`${form.dataset.searchUrl}?q=${encodeURIComponent(query)}`, {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      results = (await response.json()).results;
      render();
    } catch (error) {
      /* requête annulée ou réseau coupé : on ne fait rien */
    }
  };

  input.addEventListener("input", () => {
    hidden.value = "";
    picked.classList.remove("on");
    clearTimeout(timer);
    const query = input.value.trim();
    if (query.length < 2) { close(); return; }
    timer = setTimeout(() => search(query), 200);
  });

  input.addEventListener("keydown", (event) => {
    const count = results.length;
    if (list.hidden || !count) return;
    if (event.key === "ArrowDown") { event.preventDefault(); highlight((active + 1) % count); }
    else if (event.key === "ArrowUp") { event.preventDefault(); highlight((active - 1 + count) % count); }
    else if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(results[active]); }
    else if (event.key === "Escape") { close(); }
  });

  input.addEventListener("blur", () => setTimeout(close, 120));
})();
