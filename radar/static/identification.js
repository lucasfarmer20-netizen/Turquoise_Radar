/* Local crop coordinates only. No image upload beyond the existing same-origin form. */
document.querySelectorAll('.maker-crop').forEach(form => {
  const canvas = form.querySelector('canvas');
  const ctx = canvas.getContext('2d');
  const image = new Image();
  let start = null;
  function draw() {
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    const scale = canvas.width / image.naturalWidth;
    const p = ['x1', 'y1', 'x2', 'y2'].map(k => Number(form.elements[k].value) * scale);
    ctx.strokeStyle = '#25dcb8'; ctx.lineWidth = 3;
    ctx.strokeRect(p[0], p[1], p[2] - p[0], p[3] - p[1]);
  }
  image.onload = () => {
    canvas.width = Math.min(600, image.naturalWidth);
    canvas.height = Math.round(image.naturalHeight * canvas.width / image.naturalWidth);
    draw();
  };
  image.src = canvas.dataset.cropImage;
  function point(event) {
    const r = canvas.getBoundingClientRect();
    return [Math.max(0, Math.min(image.naturalWidth, Math.round((event.clientX-r.left)/r.width*image.naturalWidth))),
      Math.max(0, Math.min(image.naturalHeight, Math.round((event.clientY-r.top)/r.height*image.naturalHeight)))];
  }
  canvas.addEventListener('pointerdown', e => {
    start = point(e); canvas.setPointerCapture(e.pointerId); e.preventDefault();
  });
  canvas.addEventListener('pointermove', e => {
    if (!start) return;
    const end = point(e);
    const p = [Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])];
    ['x1','y1','x2','y2'].forEach((k,i) => {form.elements[k].value = p[i];});
    draw();
  });
  canvas.addEventListener('pointerup', () => {start = null;});
  canvas.addEventListener('pointercancel', () => {start = null;});
  form.querySelectorAll('input[type=number]').forEach(input => input.addEventListener('input', draw));
});
