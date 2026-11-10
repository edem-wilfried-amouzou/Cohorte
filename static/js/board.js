// Tableau des groupes : glisser une personne d'une colonne à l'autre.
(() => {
  const board = document.getElementById("board");
  if (!board) return;
  let dragged = null;

  const toast = (message) => {
    const el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  };

  const refresh = (col) => {
    const list = col.querySelector(".col-list");
    const chips = [...list.querySelectorAll(".chip")].sort((a, b) => a.dataset.name.localeCompare(b.dataset.name, "fr"));
    chips.forEach((chip) => list.appendChild(chip));
    const empty = list.querySelector(".col-empty");
    if (empty) empty.hidden = chips.length > 0;
    const count = col.querySelector("[data-count]");
    if (count) {
      const capacity = col.dataset.capacity;
      count.textContent = capacity ? `${chips.length}/${capacity}` : String(chips.length);
      count.classList.toggle("full", Boolean(capacity) && chips.length >= Number(capacity));
    }
  };

  board.addEventListener("dragstart", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    dragged = chip;
    chip.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", chip.dataset.name);
  });

  board.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    board.querySelectorAll(".col.over").forEach((c) => c.classList.remove("over"));
    dragged = null;
  });

  board.addEventListener("dragover", (event) => {
    const col = event.target.closest(".col");
    if (!col || !dragged) return;
    event.preventDefault();
    board.querySelectorAll(".col.over").forEach((c) => c !== col && c.classList.remove("over"));
    col.classList.add("over");
  });

  board.addEventListener("drop", async (event) => {
    const col = event.target.closest(".col");
    if (!col || !dragged) return;
    event.preventDefault();
    col.classList.remove("over");
    const chip = dragged;
    const from = chip.closest(".col");
    if (from === col) return;

    const url = board.dataset.assignUrl.replace("/0/", `/${chip.dataset.id}/`);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": board.dataset.csrf, "X-Requested-With": "fetch" },
        body: new URLSearchParams({ groupe: col.dataset.groupId }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        toast(data.error || "Impossible de déplacer cette personne.");
        return;
      }
      col.querySelector(".col-list").appendChild(chip);
      refresh(col);
      refresh(from);
    } catch (error) {
      toast("Connexion perdue. Réessaie.");
    }
  });
})();
