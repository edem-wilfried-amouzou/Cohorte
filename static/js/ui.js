// Fenêtre d'import : onglets, nom du fichier choisi, dépôt par glisser-déposer.
document.querySelectorAll(".import-form").forEach((form) => {
  const tabs = form.querySelectorAll("[data-import-tab]");
  const panels = form.querySelectorAll("[data-import-panel]");
  tabs.forEach((tab) => tab.addEventListener("click", () => {
    tabs.forEach((other) => {
      const active = other === tab;
      other.classList.toggle("is-active", active);
      other.setAttribute("aria-selected", String(active));
    });
    panels.forEach((panel) => { panel.hidden = panel.dataset.importPanel !== tab.dataset.importTab; });
    if (tab.dataset.importTab === "text") form.querySelector("textarea")?.focus();
  }));

  const input = form.querySelector("input[type='file']");
  const zone = form.querySelector(".dropzone");
  const label = form.querySelector("[data-file-name]");
  input?.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (label) { label.textContent = file ? file.name : ""; label.hidden = !file; }
  });
  ["dragenter", "dragover"].forEach((name) => zone?.addEventListener(name, () => zone.classList.add("is-over")));
  ["dragleave", "drop"].forEach((name) => zone?.addEventListener(name, () => zone.classList.remove("is-over")));
});

// Un clic sur le fond sombre ferme les fenêtres de formulaire.
document.querySelectorAll("dialog.modal").forEach((dialog) => {
  if (dialog.id === "confirm-dialog") return;
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
});

// Liste publique : « Modifier » ouvre la fenêtre avec les informations de la personne.
const editDialog = document.getElementById("edit-dialog");
if (editDialog) {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-url]");
    if (!button) return;
    const form = editDialog.querySelector("form");
    form.action = button.dataset.editUrl;
    editDialog.querySelector("[data-edit-name]").textContent = button.dataset.name || "";
    form.querySelectorAll("[name]").forEach((field) => {
      if (["csrfmiddlewaretoken", "website"].includes(field.name)) return;
      const value = field.name === "sex" ? button.dataset.sex
        : field.name === "reference" ? button.dataset.reference
        : button.getAttribute(`data-c-${field.name}`);
      field.value = value || "";
    });
    editDialog.showModal();
  });
}