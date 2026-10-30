document.addEventListener('DOMContentLoaded', () => {
  const dialog = document.querySelector('#photo-zoom');
  if (dialog) {
    document.querySelectorAll('a[data-zoom]').forEach(link => link.addEventListener('click', event => {
      event.preventDefault();
      dialog.querySelector('img').src = link.href;
      dialog.showModal();
    }));
    dialog.querySelector('[data-close-zoom]').addEventListener('click', () => dialog.close());
  }
  document.querySelectorAll('[data-photo-drop]').forEach(zone => {
    zone.addEventListener('dragover', event => { event.preventDefault(); });
    zone.addEventListener('drop', event => {
      event.preventDefault();
      zone.querySelector('input[type=file]').files = event.dataTransfer.files;
    });
  });
});
