# Source handoff — 2026-11-09

This branch preserves the current development source for review and continuation.
It is not an installed pilot upgrade or authorization to run live collection.
Base checkpoint: `056e9b34269960c143da456c5f3d522229e036ba`.

## Implemented locally

- Buy It Now labels, source qualification, combined eBay/email lead view, and
  auction-ending-soon adapter options with synthetic tests.
- Repairs for deletion maintenance after GO/NO GO promotion, stale production
  records exposed by legacy routes, no-store responses and combined-feed expiry
  notices. Conservative deletion, identity and financial evidence gates remain.
- An unfinished finite discovery worker, durable request budgeting and optional
  retained-seller matching extension. These are offline prototypes, not deployed
  or production-qualified automation.

The owner previously reported a working local pilot and email review. Exact current
installed hashes have not been inspected. The new worker and receiver extension
have not been deployed. Source presence does not imply installation.

## Publication-only delta

The private source handoff ZIP preserves the exact pre-publication tree. For this
public branch, the worker's hard-coded live receiver address was removed. Set
`PILOT_MATCH_ENDPOINT` explicitly to an authorized HTTPS `/pilot-deletion`
operation before enabling the worker. Missing/invalid configuration fails before
credential prompts, activation or requests. Preview requires no endpoint.
The older HTTPS runbook also replaces the live hostname and registry location
with placeholders. The public snapshot is based directly on main to avoid publishing
unreviewed local intermediate history; the checkpoint above identifies its source.
Tests cover the configuration guard. No credentials or deployment configuration
are supplied; no deletion checks were relaxed.

The private ZIP also preserves unfinished, machine-specific updater drafts. Those
are intentionally excluded here, as are private handoff metadata and runtime data.

## Validation and next steps

The exact pre-sanitization snapshot passed 1,551 tests and Ruff. Run the full suite
and Ruff for this publication candidate; final execution evidence is recorded in
its draft PR. Browser checks passed for the earlier release repairs, but have not
been rerun for the worker-status UI.

The worker still needs independent review of restart/interruption behavior,
registration before retention, partial writes, all retained copies, and truthful
status after budget exhaustion/expiry. Finish one integrated update only after
review and final browser verification. Existing test fixtures are synthetic.

The prototype alternates one exact query between auctions and Buy It Now, at most
hourly, with 8 marketplace/image attempts per run, 32 per UTC day and 224 over a
seven-day activation. It is not an installed service. Receiver registration is
opt-in, bounded and undeployed. Do not reuse consumed one-search markers, reset
existing enrollment, or roll back maintenance to a scope that misses retained data.
Any live recurring run and receiver scope change need a concrete authorization
preview; account-specific API charges remain unverified. No bids, purchases or
paid AI are enabled by this handoff.

Only eBay has an official search adapter. ShopGoodwill/HiBid support email intake,
not automated live search. Bonanza/eBid are researched candidates without adapters;
Poshmark/Depop are not connected buyer-search sources. Connected tools in a chat
are not automatically an unattended credential channel for the local application.
See AUTOMATED-BUYER-MVP.md and MARKETPLACE-NEXT-SOURCES.md for context; earlier plan
status statements are historical, not evidence of deployment.

Use the existing pinned dependencies and synthetic tests. Do not copy private
runtime files into a clone, run production merely because credentials exist, or
modify the owner's installed application during source review.
