/* Local clock display and explicit owner POSTs only; no marketplace requests. */
(() => {
  // Only server-qualified production eBay content receives this marker. Withdrawal
  // makes no request and cannot refresh evidence or grant server-side access.
  const started = performance.now();
  const expiryCards = [...document.querySelectorAll('[data-source-expiry]')].map((card) => ({
    card,
    checked: Date.parse(card.dataset.sourceCheckedAt),
    expires: Date.parse(card.dataset.sourceExpiresAt),
  }));
  let withdrawn = 0;
  let expiryTimer;
  const withdrawExpired = () => {
    window.clearTimeout(expiryTimer);
    let next = 30000;
    expiryCards.forEach(({ card, checked, expires }) => {
      if (!card.isConnected) return;
      // A backwards wall-clock adjustment must not extend display lifetime.
      const now = Math.max(Date.now(), checked + Math.max(0, performance.now() - started));
      if (!Number.isFinite(checked) || !Number.isFinite(expires) || expires < checked || now > expires) {
        card.remove();
        withdrawn += 1;
      } else {
        next = Math.min(next, Math.max(1, expires - now + 1));
      }
    });
    if (withdrawn) {
      const notice = document.querySelector('#feed-expiry-status');
      if (notice) {
        notice.hidden = false;
        notice.querySelector('span').textContent = `${withdrawn} saved eBay ${withdrawn === 1 ? 'item on this page is' : 'items on this page are'} withheld because listing or photo freshness has expired or cannot be verified.`;
      }
      // Other pages were not inspected; do not invent replacement totals.
      document.querySelectorAll('.compact-tabs span, .feed-pagination > span').forEach((count) => {
        count.hidden = true;
      });
    }
    if (expiryCards.some(({ card }) => card.isConnected)) {
      expiryTimer = window.setTimeout(withdrawExpired, next);
    }
  };
  withdrawExpired();
  window.addEventListener('pageshow', withdrawExpired);
  window.addEventListener('focus', withdrawExpired);
  document.addEventListener('visibilitychange', withdrawExpired);
  ['click', 'submit'].forEach((type) => document.addEventListener(type, (event) => {
    const card = event.target.closest('[data-source-expiry]');
    if (!card) return;
    withdrawExpired();
    if (!card.isConnected) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true));
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
