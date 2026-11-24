# Projects (repos pushed in the last 30 days)

Source: GitHub search `user:afarah101 pushed:>=2026-10-24`, shallow clones of each default branch, and GitHub issue search `is:issue is:open`. Built 2026-11-23. Agents use the project names below in the "Your link" line.

## Baseline approval rate

- Scout Actions emails, last 14 days (13–23 Nov 2026): 7 emails, 20 items offered. The 14 Nov TEST email is excluded.
- YES replies found: 0. Gmail search `subject:"Re: [Agent] Scout Actions"` returned no threads. Each thread has 1 message.
- **Baseline: 0 / 20 = 0%** (target 30%).

## Scout Daily — `afarah101/agent-builder-daily` (private)

- Goal: daily AI news, case studies and papers scout. Email digest and site at agent-builder-daily.vercel.app.
- Stack: Python, LangGraph, Groq (llama-3.1-8b-instant), LangFuse, GitHub Actions, Vercel.
- Key files: `orchestrator.py`, `send_daily_email.py`, `judge.py`, `quality_checks.py`, `.github/workflows/scout-daily.yml`, `.github/workflows/daily-email.yml`.
- Last 5 commits (main):
  - 82c98ed 2026-11-23 Scout Daily update - 2026-11-23 20:48:55
  - 6130ff2 2026-11-23 Scout Daily update - 2026-11-23 17:58:08
  - b9212e4 2026-11-23 Scout Daily email sent - 2026-11-23
  - 7786111 2026-11-22 Scout Daily update - 2026-11-22 23:31:25
  - 019bda1 2026-11-22 Scout Daily update - 2026-11-22 14:25:36
- Open issues: none.

## Aurora website — `afarah101/Aurora-Electrical-Website` (public)

- Goal: marketing site for Aurora Electrical (south-west London electrician). Turns visitors into calls, WhatsApp messages and form enquiries.
- Stack: Next.js, React, TypeScript, Resend (contact email), Vercel Analytics, Vercel.
- Last 5 commits (main):
  - 5fe5425 2026-11-23 fix(seo): align JSON-LD with Google Business Profile (#28)
  - 57d34cb 2026-11-23 Merge pull request #25 from afarah101/feat/logo-dawn
  - e08f9e4 2026-11-23 Merge pull request #26 from afarah101/chore/seo-audit
  - 5749afa 2026-11-23 Merge pull request #27 from afarah101/fix/seo-metadata
  - 7306c12 2026-11-23 fix(seo): per-page titles, descriptions, canonical and Open Graph
- Open issues: none.

## Aurora growth system — `afarah101/aurora-growth-system` (private)

- Goal: growth plan for Aurora Electrical (Optomate). More direct customers without chasing, and a repeatable package for UK trades.
- Stack: docs only (Markdown plan, decisions, per-section folders 01–08).
- Last 5 commits (main):
  - 0449610 2026-11-09 docs: record ticket 06 code review outcomes
  - 8775a01 2026-11-08 docs: add Phone 2 to client register; reviewed yearly clean-out
  - c578801 2026-11-08 docs: automate client register Status and safe clean-out rule
  - 20a294d 2026-11-08 docs: add First contact date and Status columns to client register ticket
  - df4f61d 2026-11-08 docs: record shortened privacy notice decisions
- Open issues: none.

## DUS App — `afarah101/dus-app` (private)

- Goal: mosque community app (prayers, events, Qurʾān, adhkār) for Masjid Daar Us Sunnah.
- Stack: Expo 55, React Native 0.83, React 19, Firebase (Firestore, Crashlytics, Analytics, Functions), Groq crash monitor, GitHub Actions health check.
- Key metric: Firestore reads (DUS Health reports 1.20M against a 150k limit, 23 Nov).
- Last 5 commits (main):
  - 7f0ba7e 2026-11-21 perf(notifications): read only today's events in the minute check (#13)
  - f9f3be9 2026-11-15 fix(notifications): read event tokens only on a send minute (#12)
  - ae8df29 2026-11-14 fix(notifications): fall back across groq models in crash monitor (#11)
  - d0b5b73 2026-11-14 ci(health): add daily read-only backend health check (#10)
  - 0e6aa9b 2026-11-01 chore(prayer): lock the Jumuʿah rule in docs and tests, and run tests in CI (#9)
- Open issues: none.

## DUS Kiosk — `afarah101/dus-kiosk` (public)

- Goal: entrance TV board for the mosque. Next prayer, prayer times, events, reminder, contacts. Live from Firestore.
- Stack: static HTML, CSS, JavaScript, Firestore, healthchecks.io heartbeat, Vercel.
- Last 5 commits (main):
  - 4d6eca9 2026-10-31 Remove stale kiosk test fork
  - 52c30f4 2026-10-31 Add healthchecks.io heartbeat
  - f4c21a4 2026-10-31 fix(kiosk): show the Jumuʿah jamāʿah 15 minutes after the khuṭbah
  - 10f2674 2026-08-09 Each prayer row shows the next time it will be prayed
  - 0caea2a 2026-07-16 feat(kiosk): jama'ah times + Maghrib +10, rotating 3-event pager, Imam/Management contact rows, event description fallbacks, heartbeat writer, periodic data refresh + NEXT highlight fix
- Open issues: none.

## Football Twin — `afarah101/football-twin` (public)

- Goal: find players whose seasons look statistically alike in how they shoot and create. Claim Check tests football claims against data.
- Stack: Python (numpy, pandas, pyarrow), Groq, LangGraph, LangFuse, static site, GitHub Actions.
- Last 5 commits (main):
  - 5f1bc6e 2026-11-14 feat(#43): pooled player Verdicts from an Extraction (#58)
  - 5c01bb4 2026-11-13 ci: cancel a superseded verify run when a PR gets a new push (#56)
  - 4890fb9 2026-11-13 Merge pull request #55 from afarah101/chore/autonomy-setup
  - 7a75b6a 2026-11-13 chore: autonomy setup (deny rules, test Stop hook, CI, Claude review gate, CLAUDE.md rules)
  - be2870e 2026-11-13 chore: ignore .env*, caches, venvs, node_modules and .vercel
- Open issues (26): #1, #2, #3, #10, #12, #13, #14, #15, #16, #29, #31, #33, #36, #37, #38, #39, #40, #41, #44, #45, #46, #47, #48, #49, #50, #51. Main themes: Phase 1 spec, live Understat adapter (#14, #15, #16), static site launch (#13), Claim Check verdicts (#36–#51).

## Optomate lead signals — `afarah101/optomate-lead-signals` (private)

- Goal: find and rank sales leads (US plumbing and HVAC first) from public signals. Sales Researcher agent.
- Stack: Python, DuckDB, pytest. Folders `src/signals`, `src/leads`, `src/ranking`, `src/calibration`.
- Last 5 commits (main):
  - f7c255f 2026-11-18 Merge pull request #3 from afarah101/sync/mac-main
  - 2e767ba 2026-11-14 agents: Sales Researcher fixes for the 9b43910 audit
  - fe0f4b4 2026-11-14 agents: Sales Researcher fixes for the second 981c4a2 audit
  - 9b43910 2026-11-14 agents: Sales Researcher fixes for the 981c4a2 audit
  - 981c4a2 2026-11-14 agents: fix Sales Researcher audit failures at dc27e3f
- Open issues (1): #4 Harden quote and name redaction.

## Western Ballers news — `afarah101/wb-news` (private)

- Goal: news site for Western Ballers FC (match reports, issues, match centre).
- Stack: static HTML, CSS, JavaScript, Anime.js, sharp, Vercel.
- Last 5 commits (main):
  - 2b06320 2026-11-23 Type and spacing on DESIGN.md tokens (#25)
  - 1409f01 2026-11-23 style: type and spacing from DESIGN.md tokens only
  - 055b5dd 2026-11-23 Crest hero on the homepage (Anime.js) and DESIGN.md (#24)
  - 39fcf29 2026-11-23 fix: crest returns to full size when scrolled back to the top
  - 02964fa 2026-11-23 fix: crest hero motion review fixes
- Open issues: none.

## Not a repo but named in agent emails

- `optomate-audit-agent`, `agent-starter`: not pushed in the last 30 days. Not in this list. Items linked only to these get dropped.
