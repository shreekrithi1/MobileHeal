# How the team worked

## Product Manager
- ⚡ **RICE prioritiser** — top feature: Product catalog with search and filters (score 240)
- ⚡ **Edge-case miner** — 23 unhappy paths captured as acceptance criteria
- ⚡ **North-star metrics** — Conversion rate from product view to purchase: ≥ 60% of customers weekly by month 3; Repeat purchase rate within 30 days: ≥ 50 per week by month 2

## Engineering Manager
- ⚡ **Risk radar** — 5 risks, each with a mitigation — top: Scope creep beyond the MVP
- ⚡ **RACI planner** — 6 deliverables assigned; I'm accountable for all of them
- ⚡ **Quality scorecard** — 100/100 — Product 20/20, Design 20/20, Engineering 20/20, QA 20/20, Security 20/20

## Product Designer
- ⚡ **Palette generator** — 10-step scale from #4338CA (50 → 900)
- ⚡ **Accessibility auditor** — 5/5 WCAG checks pass (primary 7.9:1, accent 4.64:1)
- ⚡ **State designer** — 15 empty / loading / error states specified

## Backend Engineer
- ⚡ **Relationship inference** — no links between records
- ⚡ **Search & sort** — every list supports ?q=…&sort=field&order=desc
- ⚡ **Seed data** — 15 realistic sample records loaded so the prototype demos well
- ⚡ **CSV export** — /apps/pawmart/api/<list>.csv for every record type

## Frontend Engineer
- ⚡ **Live search** — search box on every list, server-side ?q=
- ⚡ **Smart pickers** — no linked fields in this app
- ⚡ **One-tap export** — ⬇ CSV button on every list
- ⚡ **Responsive & dark mode** — phone-first layout, follows the system theme

## QA Engineer
- ⚡ **Acceptance runner** — 30/30 CRUD & validation tests pass
- ⚡ **Fuzzer** — 3/3 fuzz attacks handled (XSS, 5 000 chars, wrong types)
- ⚡ **Load probe** — 20 ms ✓
- ⚡ **Feature checks** — 3/3 — search, sort, relations, CSV

## Security & DevOps
- ⚡ **Secret scanner** — no secrets found
- ⚡ **OWASP checklist** — 5/5 applicable Top-10 items covered
- ⚡ **SBOM** — FastAPI, SQLite (stdlib), Browser (no frameworks, no CDN) — no third-party scripts
- ⚡ **Merge-request bot** — feature/app-2-pawmart with 9 files
