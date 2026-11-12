// Fenêtre de confirmation (remplace window.confirm et window.alert).
// Retourne une promesse : true si l'utilisateur confirme, false sinon.
function ask(message, { title = "Confirmer l'action", ok = "Confirmer", danger = false, alertOnly = false } = {}) {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return Promise.resolve(true);
  dialog.querySelector("#confirm-title").textContent = title;
  dialog.querySelector("#confirm-message").textContent = message;
  const okButton = dialog.querySelector("#confirm-ok");
  const cancelButton = dialog.querySelector("#confirm-cancel");
  okButton.textContent = alertOnly ? "Compris" : ok;
  okButton.className = danger ? "btn danger" : "btn";
  cancelButton.style.display = alertOnly ? "none" : "";
  dialog.returnValue = "";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.showModal();
    (danger ? cancelButton : okButton).focus();
  });
}

// Un clic sur le fond sombre ferme la fenêtre sans confirmer.
document.getElementById("confirm-dialog")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close("cancel");
});

const DANGER_WORDS = /supprim|retir|révoqu|revoqu|effac/i;

function toneFor(label) {
  return DANGER_WORDS.test(label || "");
}

// Confirmation avant les actions sensibles : <form data-confirm="Message">
document.addEventListener("submit", (event) => {
  const form = event.target;
  const message = form.dataset && form.dataset.confirm;
  if (!message) return;
  if (form.dataset.confirmed) {
    delete form.dataset.confirmed;
    return;
  }
  event.preventDefault();
  const submitter = event.submitter;
  const label = submitter ? submitter.textContent.trim() : "Confirmer";
  ask(message, { ok: label, danger: toneFor(label) }).then((accepted) => {
    if (!accepted) return;
    form.dataset.confirmed = "1";
    form.requestSubmit(submitter || undefined);
  });
});

// Boutons HTMX avec hx-confirm="..." : même fenêtre, plus de popup du navigateur.
document.addEventListener("htmx:confirm", (event) => {
  if (!event.detail.question) return;
  event.preventDefault();
  const label = event.detail.elt ? event.detail.elt.textContent.trim() : "Confirmer";
  ask(event.detail.question, { ok: label, danger: toneFor(label) }).then((accepted) => {
    if (accepted) event.detail.issueRequest(true);
  });
});

// Bouton « Copier » : <button data-copy="#id-du-champ">
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const source = document.querySelector(button.dataset.copy);
  if (!source) return;
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(source.value);
    button.textContent = "Copié";
  } catch (error) {
    source.select();
    button.textContent = "Sélectionné, copie avec Ctrl+C";
  }
  setTimeout(() => { button.textContent = original; }, 1800);
});

document.querySelectorAll("[data-bulk-actions]").forEach((form) => {
  const all = form.querySelector("[data-select-all]");
  const rows = [...form.querySelectorAll("[data-member-select]")];
  const counter = form.querySelector("[data-selected-count]");
  const action = form.querySelector("[name='bulk_action']");
  const group = form.querySelector("[name='group_id']");
  const activity = form.querySelector("[name='activity_id']");

  const update = () => {
    const selected = rows.filter((checkbox) => checkbox.checked).length;
    if (counter) counter.textContent = String(selected);
    if (all) {
      all.checked = selected === rows.length && rows.length > 0;
      all.indeterminate = selected > 0 && selected < rows.length;
    }
    if (group) group.required = action.value === "assign";
    if (activity) activity.required = action.value === "clear_activity";
  };

  all?.addEventListener("change", () => {
    rows.forEach((checkbox) => { checkbox.checked = all.checked; });
    update();
  });
  rows.forEach((checkbox) => checkbox.addEventListener("change", update));
  action?.addEventListener("change", update);
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirmed) {
      delete form.dataset.confirmed;
      return;
    }
    if (!rows.some((checkbox) => checkbox.checked)) {
      event.preventDefault();
      ask("Sélectionne au moins une personne.", { title: "Aucune sélection", alertOnly: true });
      return;
    }
    if (action.value === "delete") {
      event.preventDefault();
      const submitter = event.submitter;
      ask("Supprimer les personnes sélectionnées de cette classe ? Cette action est définitive.", {
        title: "Supprimer la sélection", ok: "Supprimer", danger: true,
      }).then((accepted) => {
        if (!accepted) return;
        form.dataset.confirmed = "1";
        form.requestSubmit(submitter || undefined);
      });
    }
  });
  update();
});

document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-dialog-open]");
  if (opener) document.querySelector(opener.dataset.dialogOpen)?.showModal();
  const closer = event.target.closest("[data-dialog-close]");
  if (closer) closer.closest("dialog")?.close();
});