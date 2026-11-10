// Confirmation avant les actions sensibles : <form data-confirm="Message">
document.addEventListener("submit", (event) => {
  const message = event.target.dataset && event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
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
