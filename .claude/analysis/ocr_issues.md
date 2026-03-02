# OCR Issues

## AP read failures -- 21x across session [CONFIRMED]
- **Platform**: Windows
- **Session**: 2026-03-02 (all 4 log files, Mar 1 17:32 - Mar 2 14:48)
- **Warning**: "Could not read AP after 5 attempts" x21
- **Impact**: Falls through to None return, which callers handle gracefully
- **Possible causes**: Chat overlay covering AP bar, game animations, map zoom state
- **Action needed**: Add failure screenshot on AP read failure (noted in MEMORY.md WIP items)
- **Note**: ap_menu_crop.png debug image shows "26/350" -- OCR reads correctly when visible

## Quest OCR -- Working well [NORMAL]
- **Session**: 2026-03-02
- **Debug screenshot**: aq_ocr_crop.png shows alliance quest screen clearly readable
- **Quest types visible**: Gather(0/200,000), Fortress(960/1,200), Defeat Titans(4/5)
- **check_quests**: 4/4 success, avg 141.8s -- long but successful (includes OCR + dispatch time)

## macOS Apple Vision -- Paren drop on titan counters [FIXED in dev]
- Paren insertion + name trim, gated to darwin
- Not relevant to Windows sessions

## No new OCR issues detected in this session.
