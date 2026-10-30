document.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-copy]');
  if (!button) return;
  const text = document.getElementById(button.dataset.copy).value;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = 'Copied';
  } catch {
    const input = document.getElementById(button.dataset.copy);
    input.focus(); input.select();
    button.textContent = 'Selected — use Copy';
  }
});
