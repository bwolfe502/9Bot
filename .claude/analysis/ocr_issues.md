# OCR Issues

## AP read failures -- 21x across prior sessions [CONFIRMED]
- **Platform**: Windows
- **Warning**: "Could not read AP after 5 attempts" x21 (Mar 1-2 cross-session)
- **Impact**: Falls through to None return, callers handle gracefully
- **Possible causes**: Chat overlay covering AP bar, game animations, map zoom state
- **Action needed**: Add failure screenshot on AP read failure

## Quest OCR -- Working well [NORMAL]
- Session 12: 16 quest OCR snapshots captured. Noisy text but functionally reliable.
- OCR errors corrected by parser: "o/1" → 0/1, "Enemv" → "Enemy", garbled headers ignored
- Cap override system working: raw OCR values overridden to configured maximums
- Rally owner OCR: 69 reads in session 12, correctly extracting names ("Giger", "Morteza")
  despite 3 different OCR readings of same name

## Zero OCR training data [NEW, GAP]
- Training data JSONL has 0 OCR entries across 1,440 total entries
- Either `read_text()`/`read_number()` calls aren't being logged, or confidence thresholds
  for training data capture are not being crossed
- **Impact**: Can't assess OCR reliability from training data -- only from logs/stats
- **Action needed**: Verify training data collector hooks into OCR calls

## macOS Apple Vision -- Paren drop on titan counters [FIXED in dev]
- Not relevant to Windows sessions
