# Turquoise Radar

Work in the existing isolated checkout; do not create worktrees unless requested.
Preserve financial correctness: use Decimal, explicit unknown costs, conservative
cent rounding, and evidence gates for Alaska shipping, melt, and attribution.
Never silently rewrite source queries or fabricate resale estimates/evidence.
Run `.venv/bin/pytest` and `.venv/bin/ruff check .` after changes; document behavior
and limitations in README/docs. Stay within the current task. Do not add scraping,
purchases, paid APIs, accounts, or alerts without an explicit later task.
Synthetic fixtures must remain clearly labeled and never imply historical purchases.

Task 003 discovery defaults to fixtures. Never make production requests merely
because credentials exist. Production requires explicit approval and configuration
plus per-run owner confirmation; this task does not authorize activation. Preserve
every query/item observation during deduplication, keep corrections separate from
transmitted phrases, never send auto_correct=OFF, and never infer Alaska eligibility
from requested location or a generic US shipping amount. Use mocked HTTP transport
for tests, ordered transactional migrations for schema changes, and keep fixture /
sandbox statistics separate from real marketplace performance. Do not merge task
branches without review.
