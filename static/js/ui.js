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

// Prévisualisation des fichiers sûrs directement dans une fenêtre de la bibliothèque.
const previewDialog = document.getElementById("document-preview-dialog");
if (previewDialog) {
  const frame = previewDialog.querySelector("iframe");
  const title = previewDialog.querySelector("#document-preview-title");
  const download = previewDialog.querySelector("[data-preview-download]");
  const previous = previewDialog.querySelector("[data-preview-prev]");
  const next = previewDialog.querySelector("[data-preview-next]");
  const fullscreen = previewDialog.querySelector("[data-preview-fullscreen]");
  let previewLinks = [];
  let currentIndex = -1;

  const showPreview = (index) => {
    if (!previewLinks.length) return;
    currentIndex = (index + previewLinks.length) % previewLinks.length;
    const link = previewLinks[currentIndex];
    const previewUrl = link.dataset.previewUrl || link.href;
    if (title) title.textContent = link.dataset.previewTitle || "Document";
    if (frame) frame.src = previewUrl;
    if (download) {
      download.href = previewUrl.replace(/[?&]inline=1(?:&|$)/, "").replace(/[?&]$/, "");
      download.hidden = link.dataset.previewDownloadable === "false";
    }
    if (previous) previous.disabled = previewLinks.length < 2;
    if (next) next.disabled = previewLinks.length < 2;
  };

  const syncFullscreenButton = () => {
    const active = document.fullscreenElement === previewDialog || previewDialog.classList.contains("is-expanded");
    if (fullscreen) {
      fullscreen.textContent = active ? "⤢" : "⛶";
      fullscreen.setAttribute("aria-label", active ? "Réduire l’aperçu" : "Agrandir l’aperçu");
      fullscreen.title = active ? "Réduire l’aperçu" : "Agrandir l’aperçu";
    }
  };

  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-document-preview]");
    if (link) {
      event.preventDefault();
      previewLinks = [...document.querySelectorAll("[data-document-preview]")];
      showPreview(previewLinks.indexOf(link));
      previewDialog.showModal();
      return;
    }
    if (event.target.closest("[data-preview-prev]")) showPreview(currentIndex - 1);
    if (event.target.closest("[data-preview-next]")) showPreview(currentIndex + 1);
  });

  fullscreen?.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement === previewDialog) {
        await document.exitFullscreen();
      } else if (previewDialog.requestFullscreen) {
        await previewDialog.requestFullscreen();
      } else {
        previewDialog.classList.toggle("is-expanded");
        syncFullscreenButton();
      }
    } catch (error) {
      previewDialog.classList.toggle("is-expanded");
      syncFullscreenButton();
    }
  });

  document.addEventListener("fullscreenchange", syncFullscreenButton);
  document.addEventListener("keydown", (event) => {
    if (!previewDialog.open) return;
    if (event.key === "ArrowLeft") showPreview(currentIndex - 1);
    if (event.key === "ArrowRight") showPreview(currentIndex + 1);
  });
  previewDialog.addEventListener("close", async () => {
    if (frame) frame.src = "about:blank";
    previewDialog.classList.remove("is-expanded");
    if (document.fullscreenElement === previewDialog) await document.exitFullscreen();
    syncFullscreenButton();
  });
}
