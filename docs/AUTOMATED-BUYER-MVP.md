# Automated buyer MVP

Product requirement: unattended discovery and qualification into one useful buyer
feed. Owner file import, email snapshots and manual browsing are transitional
inputs, not the finished workflow. This document supersedes the manual-test end
state in MVP-SOURCE-AUDIT.md.

## Verified starting point

- discovery/service.py plans exact queries, deduplicates listings, records each
  observation and finishes run status. It is invoked per run, not a scheduler.
  Its default production adapter does not perform stable seller-ID hydration or
  deletion enrollment before it writes observations. Do not put it on a timer
  unchanged; the one-search controller previously supplied those missing gates.
- discovery/config.py Budgets limits queries/results/pages per invocation. It does
  not impose a durable daily OAuth/detail/image request budget or prevent duplicate
  runs after restart.
- ebay_deletion/pilot_job.py has a single-instance lock and recurring deletion
  checks. Its child feed explicitly disables source acquisition and strips eBay
  credentials. The running maintenance loop is not automated discovery.
- The receiver currently accepts one immutable <=3-seller cohort. Adding a second
  fixed cohort would enable one more demo, but still would not support unattended
  discoveries. Do not build a succession of one-off cohorts as the product.
- analysis.py, finance.py and search_evaluation.py already provide conservative
  buyer calculations, missing-cost checks and evidence evaluation. They cannot
  conjure trustworthy resale values, delivery eligibility or unreported fees.
- eBay is the only actual official search adapter. Existing alternative-marketplace
  registry names and tested email parsers are not active recurring connectors.

## Minimal runnable shape

Keep one application, SQLite database, local photo store, and the existing receiver.
Use one supervised worker, not a queue service or multiple per-source daemons:

1. Recover a durable run record after restart without repeating reserved requests;
   acquire the existing exclusive write/maintenance lock.
2. Process deletion notices for retained sellers, including interrupted cleanup.
3. If a saved source/query is due and remaining request allowance permits, reserve
   the run and each outbound attempt durably. Skip missed intervals after sleep;
   do not replay a backlog. Pause on auth/rate errors instead of retry storms.
4. Search through the permitted adapter; hydrate stable identity, check deletion,
   and register newly retained identities before committing their data. Save only
   inventoried local images. Deletion matching must cover observations as well as
   normalized rows, photos and any promoted records.
5. Update existing listings, record observed times and reevaluate the buyer card.
   Scheduled end time is not guaranteed final price; unknown shipping or fees
   remain unknown. Expired/incomplete records do not become actionable bargains.
6. Publish the existing combined feed and status: last successful run, next due,
   remaining budget, source failure and why a candidate needs more evidence.

Retain existing deletion-before-serve behavior for this first increment; changing
it is optional and should not become another per-page-proof project.

## First shipping slice

One configured eBay query with BIN and ending-soon auction modes, one page per
mode, capped retained results/photos and explicit finite daily attempt budget.
Use existing source parameters and ZIP99669. Refreshes consume the same daily
budget as discovery. Cadence/caps require one precise activation preview; no
recurring production run is currently activated. The old eight-call authorization
and consumed markers are historical and cannot fund a new perpetual worker.

Required implementation work:
- Integrate the existing hydration/photo/deletion sequence into the repeatable
  ingestion path instead of bypassing it with raw run_discovery on a timer.
- Replace the single-cohort assumption with a bounded retained-seller registry on
  the same restricted receiver operation. Preserve old enrollment and data; cap
  active identities and request rate, reject unknown scope, and never remove a
  seller from maintenance coverage while any retained copy remains. This changes
  approved connection scope and requires a reviewed receiver deployment.
- Add durable due-run/attempt accounting and a narrowly scoped worker entrypoint.
  Keep credentials in the worker, not the UI child, logs, command line or artifacts.
- Provide one combined local update and one activation preview, not serial patches.

Tests must use synthetic HTTP: repeated discovery/dedup, new sellers under deletion
coverage, MATCH/HOLD and outages, request exhaustion, crash/restart reservations,
expired photos/listings, missing costs and actual rendered cards. Live qualification
then measures candidates with usable photos, fees/shipping coverage and freshness;
it must not describe unknown economics as buying opportunities.

## Second source and actual autonomy

Bonanza is the next official buyer-search candidate, followed by eBid. Before live
use, establish working developer access, permitted retention/photo use and request
limits; then add a small adapter to the same worker. No new accounts or credentials
have been created. See MARKETPLACE-NEXT-SOURCES.md for verified API documentation
and the Poshmark/Depop access limitations.

Email sources can become automatic only with a separately authorized mailbox
subscription/polling connection that the running app can use. A ChatGPT-connected
mailbox is not automatically a credential or runtime channel for the local app.
Even automatic email ingestion cannot supply price/end/shipping fields missing
from the email. It must not be sold as full marketplace search.

Local automation works only while Ubuntu and its worker are running. Truly
always-on operation eventually needs an available authorized hosted runtime and
its recurring-cost approval. Hosting migration, dashboards, paid valuation models,
automatic bidding and general crawling are not prerequisites for proving this
worker and are outside this build slice.

Status: architecture/code-path review complete; no scheduler, recurring collection,
new registry scope, persistent eBay credential, receiver change or deployment was
activated by this review. Independent defect reports remain to be incorporated
when provided.
