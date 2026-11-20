# Unslop UI report

Target: `site/dashboard.html`, `site/archive.html`, `site/news.html`, `site/js/subscribe.js` with `--surface`.
Verdict: heavy on `site/dashboard.html`, light on the other three files.
No `.unslop-ui/taste.md`, so T1 to T3 did not run.

Command (before and after): `python3 ~/.agents/skills/unslop-ui/scripts/count_tells.py --surface site/dashboard.html site/archive.html site/news.html site/js/subscribe.js`

## Counts

| Rule | Before | After | Kept |
|---|---|---|---|
| U1 eyebrow | 3 | 3 | 3 |
| U2 decorative numbering | 0 | 0 | |
| U3 subtitle reflex (read) | 4 | 0 | |
| U5 leaked design note (read) | 3 | 1 | 1 |
| U6 status string | 2 | 0 | |
| U9 badge word | 0 | 0 | |
| U10 status dot | 0 | 0 | |
| U11 emoji | 2 | 0 | |
| U11 decorative icon (read) | 1 | 0 | |
| U11 icon tile | 0 | 0 | |
| U12 chart-type title | 0 | 0 | |
| U13 reading instructions (read) | 1 | 0 | |
| U14 saying it twice (read) | 1 | 0 | |
| U15 chart scaffolding | 0 | 0 | |
| U16 legend | 0 | 0 | |
| unslop 13 em dash | 4 | 0 | |
| unslop 17 title case (read) | 8 | 0 | |
| U17 gradient or glow | 1 | 1 | 1 |
| U18 off-scale spacing | 0 | 0 | |
| U18 off-scale spacing (read) | 1 | 0 | |
| U19 extra-heavy weight | 2 | 0 | |
| U20 motion | 1 | 0 | |
| **Counter total** | **15** | **4** | |

Rows marked "(read)" come from reading, not from the counter.

## Rewritten

```
site/dashboard.html
"Scout Daily — Pipeline Monitor" → "Scout Daily pipeline monitor" (unslop 13)
"↻ Refresh" → "Refresh" (U11)
"Loading live token data…" → "Loading token data…" (U5)
"Trend — Last 7 Days" → "Last 7 days" (unslop 13, unslop 17)
"TOTAL TRACES (7 DAYS)" → "Total traces (7 days)" (unslop 17)
"AVG LATENCY P50" → "Avg latency p50" (unslop 17)
"AVG LATENCY P95" → "Avg latency p95" (unslop 17)
"INPUT TOKENS (7 DAYS)" → "Input tokens (7 days)" (unslop 17)
"OUTPUT TOKENS (7 DAYS)" → "Output tokens (7 days)" (unslop 17)
"AVG QUALITY SCORE" → "Avg quality score" (unslop 17)
"SCORES ANNOTATED" → "Scores annotated" (unslop 17)
"SNAPSHOT AGE" → "Snapshot age" (unslop 17)
"Langfuse snapshot not yet generated — will appear after next pipeline run." → "No Langfuse snapshot yet. It appears after the next pipeline run." (U4, unslop 13)
"${live|no data yet} · ${calls} calls · ${prompt} prompt / ${completion} completion" → "${calls} calls, ${prompt} prompt / ${completion} completion" (U6)
"Live from resp.usage · avg ${n} tokens/call · ⚠ ${m} usage-miss(es) · console.groq.com" → "Avg ${n} tokens/call. ${m} usage misses. console.groq.com" (U5, U6, U11)
"No token stats yet — will populate after next pipeline run · console.groq.com" → "No token stats yet. They appear after the next pipeline run. console.groq.com" (U4, U6, unslop 13)
"Bar height = papers scored. Colour: purple ≥50 avg, amber <50 avg." → "Purple: avg score ≥50. Amber: below 50." (U13)

site/js/subscribe.js
"Subscribed! ✅" → "Subscribed!" (U11)
```

Surface value changes (diff shows them): `transition: all 0.15s` → `transition: border-color 0.15s ease-out` (U20); `padding:5px 0` → `padding:4px 0` (U18); `font-weight: 800` → `600` in `site/archive.html` and `site/news.html` (U19). Deleted: 4 card subtitles (U3), the `.health-dot` span and its CSS (U14).

## Kept on purpose

- U1 `site/dashboard.html:107` `.section-title`: it is the section heading. No heading sits below it.
- U1 `site/dashboard.html:133` `.card-label`: it is the only name of each KPI value.
- U1 `site/dashboard.html:468`: orphan declarations with no selector. The browser does not apply them. See Tasks.
- U17 `site/dashboard.html:391` skeleton gradient: a tone shift inside the neutral surface on a loading skeleton.
- U5 `site/dashboard.html:856` "Gemini evaluates first 5 papers per run": it explains the pending count. The number is not in the scope code. See Tasks.
- `①②③` at `site/dashboard.html:765`: the order is a real rank.
- "!" in "Subscribed!": `audit/fixes/test_subscribe.js` asserts `startsWith('Subscribed!')`. No taste file, so T1 is off.
- Card subtitles "from arXiv today", "fetch − scored", "out of 100": each states a fact the label does not.
- `site/archive.html`, `site/news.html` copy: a plain redirect notice. Nothing to cut.

## Tasks

- [ ] `generate_html.py` (writes `site/index.html`): counter finds 14 hits. unslop 13 em dash ×9, U17 at `site/index.html:228`, U18 at `:303` and `:554`, U19 at `:82`, U6 at `:1401`. Fix in the Python template. Out of scope here.
- [ ] `generate_breakdowns.py` (writes `site/breakdowns/*.html`, 2137 files): generated output. Audit the template in the Python file.
- [ ] `send_daily_email.py`: the email HTML template is inside Python. Out of scope. Audit there.
- [ ] `site/issues/*.html`, `site/news/*.html`: archived generated editions with scraped content. Not edited. Example: masthead `font-weight: 900` (U19).
- [ ] `site/dashboard.html:461` (Warning): the `.stat-label {` selector is missing after the first "Responsive" comment. The declarations are dropped and the parser probably also drops `.stat-num`. Restore the selector.
- [ ] `site/dashboard.html:773` (Warning): `getElementById('dist-section')` returns null. `renderPipeline` throws a TypeError on each load, so "Score distribution" and "Top scored today" never render. The `#pipeline-lower` skeletons stay. Pre-existing on `main`.
- [ ] `site/dashboard.html:639`, `:765` (Warning): `p.title` and `p.hallucination_detail` go into `innerHTML` without escaping. Escape them.
- [ ] `site/dashboard.html:856`: confirm "first 5 papers per run" in the pipeline code, or remove the number.
- [ ] `audit/fixes/test_subscribe.js`: to drop the "!" in "Subscribed!", change the test first.
