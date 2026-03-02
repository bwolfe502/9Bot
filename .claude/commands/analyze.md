Analyze all available 9Bot diagnostic data, document findings, and clean up.

Automatically detect and process data from ALL available sources.

All paths below are relative to the 9Bot repo root (where CLAUDE.md lives).

## Sources (check all, use what's available)

### 1. Local session data
- `stats/*.json` — action rates, template scores, transition timings, ADB perf, memory
- `logs/*.log` and `9bot.log` — warnings, errors, OCR output
- `debug/**/*.png` — failure screenshots, OCR crops, click trails
- `training_data/*.jsonl` — template match decisions, OCR reads, screen detection scores
- `training_data/images/*.jpg` — near-miss screenshots, low-confidence OCR crops, unknown screens

### 2. Inbox (bug reports from users)
Check `inbox/` for ZIP files or extracted folders:
- ZIPs: extract each to `/tmp/9bot_analysis/{name}/`, analyze, then delete the ZIP from inbox
- Folders: analyze contents directly, then delete the folder from inbox
- Contents follow the same structure as local data (stats/, logs/, debug/, training_data/)

### 3. Relay server uploads
Check the relay server for bug report ZIPs:
- Server: `1453.life`
- Secret: `base64.b64decode("MEpRR2l2bmJDMkNEUHlaS3dFVW5Qc1FrbGlWZ0phMXVZbmZ3MktOcHpYTQ==").decode()`
- List bots: `curl -s -H "Authorization: Bearer $SECRET" "https://1453.life/_admin"` (parse HTML for bot names)
- List uploads: `curl -s -H "Authorization: Bearer $SECRET" "https://1453.life/_admin/uploads/{bot_name}"` (parse for .zip filenames)
- Download: `curl -s -H "Authorization: Bearer $SECRET" -o /tmp/9bot_server/{filename} "https://1453.life/_admin/uploads/{bot_name}/{filename}"`
- Delete after analysis: `curl -s -X DELETE -H "Authorization: Bearer $SECRET" "https://1453.life/_admin/uploads/{bot_name}/{filename}"`
- If server is unreachable, skip and note it — don't abort

### Order of operations
1. Download server ZIPs first (network, may fail)
2. Process everything (local + inbox + server downloads)
3. Document findings
4. Delete/clean up only after findings are written

## Analysis

For each data source, extract:

### From stats JSON:
- Action success/failure rates — flag anything below 80% success
- Template misses — templates that never matched, with best scores (threshold is 0.8)
- Template hits — any matches below 0.85 confidence (borderline)
- Transition timings — transitions with high failure rates (met_count/count < 0.7) or budget overruns
- ADB performance — screenshot/tap/swipe avg times, slow operations (>2s)
- Memory — peak vs current, potential leaks (peak > 5x current)
- Region misses — templates found outside IMAGE_REGIONS (need widening)

### From logs:
- Recurring warnings/errors and their frequency
- OCR misreads — raw OCR text vs classified result
- Navigation failures — unknown screens, verify failures, recovery attempts
- Action cascades — one failure causing repeated downstream failures
- Any new error patterns not seen before

### From debug screenshots:
- Look at failure screenshots to identify what screen state caused failures
- Check OCR crop images for quality issues
- Note any patterns (same failure screenshot appearing repeatedly)

### From training data (training_data/*.jsonl + images/):
Each JSONL line has a `type` field: `template`, `ocr`, or `screen`.
- **Template near-misses**: templates frequently scoring 65-79% (conf field), candidates for template refresh
- **Template miss patterns**: which templates miss most often, on which devices
- **OCR failure patterns**: regions/text with consistently low confidence (avg_c < 70)
- **Screen detection gaps**: how often UNKNOWN is hit (hit=false), what the score distributions look like
- **Region drift**: images categorized as `region_drift` indicate UI elements that moved outside IMAGE_REGIONS
- **Volume stats**: total decisions logged, image capture rate, data size per session
- **Training images**: look at `training_data/images/*.jpg` to verify they capture useful edge cases

## Document findings

Write findings to `.claude/analysis/` (in-repo, syncs via git).

### File organization:
- **`MEMORY.md`** — Index with summary of known issues and links to topic files. Keep under 200 lines.
- **`template_issues.md`** — Template matching problems: low scores, region misses, platform diffs
- **`ocr_issues.md`** — OCR problems: misreads, platform differences, quest parsing
- **`timing_issues.md`** — Transition budgets, ADB performance, slow operations
- **`action_patterns.md`** — Action success rates, failure cascades, behavioral patterns
- **`session_history.md`** — Append-only log of analyzed sessions with date, duration, key metrics, source
- **`training_data.md`** — Training data patterns: near-miss templates, OCR weak spots, screen detection gaps

### Rules:
- **Update existing entries** when new data confirms or refutes them — don't duplicate
- **Remove entries** that are fixed (check git log for recent fixes)
- **Confidence levels**: "confirmed" (seen 3+ times), "suspected" (seen 1-2 times), "fixed" (resolved)
- **Include specific numbers**: template scores, success rates, timing percentiles
- **Note platform**: macOS-only, Windows-only, or both
- **Note source**: local, inbox (with filename), or server (with bot name)

## Clean up

After findings are fully documented:
1. Local: delete `stats/*.json`, `debug/**/*`, `training_data/*.jsonl`, `training_data/images/*.jpg`, `logs/*.log`, `9bot.log`
2. Inbox: delete analyzed ZIPs and extracted folders from `inbox/` (keep `.gitkeep`)
3. Server: delete analyzed ZIPs via DELETE API
4. Temp: `rm -rf /tmp/9bot_analysis /tmp/9bot_server`
5. Confirm what was analyzed, documented, and deleted

## Important

- Do NOT delete anything until findings are fully written
- The relay secret is sensitive — never log it or include it in output to the user
- If a source has no data, skip it silently
- Compare new findings against existing analysis entries — update, don't duplicate
- If an issue from analysis appears fixed (not seen in new data), mark as "possibly fixed" — wait for 2+ clean sessions before removing
