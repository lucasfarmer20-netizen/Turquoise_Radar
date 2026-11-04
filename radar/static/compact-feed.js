/* Local clock display and explicit owner POSTs only; no marketplace requests. */
(() => {
  const status = document.querySelector('#feed-action-status');
  const announce = (message, error = false) => {
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('is-error', error);
  };
  const updateClocks = () => {
    document.querySelectorAll('[data-deadline]').forEach((clock) => {
      const card = clock.closest('.compact-card');
      if (!card || card.dataset.ended === 'true') return;
      const deadline = Date.parse(clock.dataset.deadline);
      if (!Number.isFinite(deadline)) return;
      const remaining = deadline - Date.now();
      if (remaining <= 0) {
        clock.textContent = 'ENDED';
        card.dataset.ended = 'true';
        const assessment = card.querySelector('[data-assessment]');
        if (assessment) {
          assessment.textContent = 'NEEDS INFO';
          assessment.className = 'assessment-needs-info';
        }
        const reason = card.querySelector('[data-reason]');
        if (reason) {
          reason.textContent = 'Ended; historical evidence only.';
          reason.title = reason.textContent;
        }
        return;
      }
      const minutes = Math.max(1, Math.ceil(remaining / 60000));
      const days = Math.floor(minutes / 1440);
      const hours = Math.floor((minutes % 1440) / 60);
      const mins = minutes % 60;
      clock.textContent = `Recorded close in ${days ? `${days}d ` : ''}${hours ? `${hours}h ` : ''}${mins}m`;
    });
  };
  updateClocks();
  window.setInterval(updateClocks, 30000);
  document.querySelectorAll('[data-compact-review]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      if (!window.fetch || !event.submitter) return;
      event.preventDefault();
      if (form.dataset.pending === 'true') return;
      const button = event.submitter;
      const decision = button.value;
      const body = new FormData(form);
      body.set('decision', decision);
      const controls = [...form.querySelectorAll('button')];
      form.dataset.pending = 'true';
      controls.forEach((control) => { control.disabled = true; });
      announce('Saving decision…');
      try {
        const response = await fetch(form.action, {
          method: 'POST', body, credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        });
        const result = await response.json();
        if (!response.ok || result.saved !== true) {
          throw new Error(typeof result.detail === 'string' ? result.detail : 'Decision was not saved. Try again.');
        }
        form.elements.expected_feedback_id.value = String(result.feedback_id);
        form.action = `/feed/review/listing/${result.listing_id}`;
        const card = form.closest('.compact-card');
        const research = card.querySelector('[data-research-link]');
        if (research) research.href = result.research_url;
        const cleared = result.decision === 'CLEAR';
        form.dataset.canClear = String(!cleared);
        controls.forEach((control) => {
          if (control.value === 'CLEAR') {
            control.hidden = cleared;
            return;
          }
          const selected = !cleared && control.value === result.decision;
          control.setAttribute('aria-pressed', String(selected));
          control.classList.toggle('is-selected', selected);
          control.textContent = `${control.value === 'GO' ? 'GO' : 'NO GO'}${selected ? ' ✓' : ''}`;
        });
        announce(cleared
          ? 'Decision cleared. Returned to review.'
          : `${result.decision === 'GO' ? 'GO' : 'NO GO'} saved. Use Clear to return to review.`);
      } catch (error) {
        announce(error instanceof Error ? error.message : 'Could not confirm the save. Retry safely or reload.', true);
      } finally {
        form.dataset.pending = 'false';
        controls.forEach((control) => { control.disabled = false; });
      }
    });
  });
})();
