(() => {
  const cards = Array.from(document.querySelectorAll('.listing-card'));
  if (!cards.length) return;
  const bulk = document.getElementById('bulk');
  const picks = () => Array.from(document.querySelectorAll('.pick input'));
  const sync = () => { const n = picks().filter(p => p.checked).length; document.getElementById('bulk-count').textContent = n; bulk.hidden = n === 0; };
  if (bulk) {
    document.addEventListener('change', (e) => { if (e.target.matches('.pick input')) sync(); });
    document.getElementById('bulk-all').addEventListener('click', () => { picks().forEach(p => { p.checked = true; }); sync(); });
    document.getElementById('bulk-clear').addEventListener('click', () => { picks().forEach(p => { p.checked = false; }); sync(); });
    sync();
  }
  let idx = Math.max(0, cards.findIndex(c => c.id === location.hash.slice(1)));
  const mark = () => { cards.forEach(c => c.classList.remove('active')); const c = cards[idx]; if (c) { c.classList.add('active'); c.scrollIntoView({ block: 'center', behavior: 'smooth' }); } };
  const act = (key) => { const c = cards[idx]; if (!c) return; const el = c.querySelector(`[data-key="${key}"]`); if (!el) return; if (el.tagName === 'SUMMARY') { el.parentElement.open = true; el.parentElement.querySelector('button')?.focus(); } else if (el.tagName === 'A') { window.open(el.href, '_blank'); } else { el.click(); } };
  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, select, textarea')) return;
    const k = e.key.toLowerCase();
    if (k === 'j') { idx = Math.min(cards.length - 1, idx + 1); mark(); }
    else if (k === 'k') { idx = Math.max(0, idx - 1); mark(); }
    else if (['i', 'p', 'u', 'o', '1', '2', '3', '4', '5', '6'].includes(k)) { e.preventDefault(); act(k); }
    else if (k === 'x') { const p = cards[idx]?.querySelector('.pick input'); if (p) { p.checked = !p.checked; sync(); } }
    else if (k === 'escape') { cards[idx]?.querySelector('details.pass')?.removeAttribute('open'); }
    else return;
  });
  cards.forEach((c, i) => c.addEventListener('click', () => { idx = i; cards.forEach(x => x.classList.remove('active')); c.classList.add('active'); }));
  if (location.hash) mark();
})();
