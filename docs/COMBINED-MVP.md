# One retained-lead interface

`/opportunities` displays the existing supervised pilot plus the separate
November 8 ShopGoodwill/HiBid email review. The sibling database is opened with
SQLite `mode=ro` and `query_only=ON`; no migrations, copied listings or new image
downloads occur. The email process on port 8002 is not needed. The foreground
eBay deletion supervisor still controls the lifetime of the combined interface.

The page names each source, original query and observation origin. It reuses the
existing conservative price, observed-time, deadline, assessment and missing-cost
projections. Unknown shipping remains unknown. Email quotes remain snapshots;
they do not become current prices merely because the page was opened. Expired
eBay records stay withheld, and eligible eBay cards use the same browser-expiry
script and locally gated photo route as `/feed`. Email photographs are not loaded
through the pilot's photo namespace. Email database failure produces a warning
without copying data or weakening pilot deletion checks.

This is an integrated view of existing evidence, not a newly executed shared
query. The pilot's blue-stone-ring query and email sterling-jewelry-lot query stay
distinct. There is no recurring discovery, current-price lookup, automatic bid,
purchase, profit guarantee or transfer of email records into the immutable cohort.
The page is read-only; existing source/listing links remain available for review.

Owner deployment: stop both foreground Radar processes with Ctrl-C, download and
checksum-verify `radar-combined-update.zip` in personal Ubuntu home, then run it
using the existing pilot `.venv/bin/python`. It accepts only the already installed
maintenance update (or this fully applied update), patches five UI/source files,
and starts the existing check-only supervisor. The existing token is read locally;
no credentials are created or changed. Both databases and acquisition markers
remain untouched by the update. Required deletion removals still occur during
maintenance. The launcher sets the explicit sibling email database path; no
filesystem search or additional server is used.

Code-only backups are stored in `combined-update-rollback`. Caught failures restore
old files and remove newly introduced code files; uncertain interruption requires
inspection before retry. Do not reset markers or use an acquisition ZIP to restart.
Use the new ZIP with `run` for subsequent maintenance-only starts. The previous
email-review ZIP pins older source hashes and should no longer be used to start
port 8002 after this update. The original email database remains preserved.
