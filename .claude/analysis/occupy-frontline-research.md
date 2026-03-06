# Occupy Frontline — Protocol Research

Branch: `occupy-frontline`
Started: 2026-03-04

## Goal
Replace vision-based territory grid scanning with protocol data from
`KvkTerritoryInfoAck` / `KvkTerritoryInfoNtf` to get instant, accurate
tower ownership without screenshotting 576 squares.

---

## Protocol Messages

### `KvkTerritoryInfoAck`
- Sent in response to a territory info request (full grid snapshot at load time)
- Contains `lands: List<LandInfo>` — full list of all tower states

### `KvkTerritoryInfoNtf`
- Server push notification when a single tower changes state
- Contains `land: LandInfo` — single updated tower

### `LandInfo` fields
| Field | Type | Meaning |
|-------|------|---------|
| `coord` | Coord | Grid coordinates of tower |
| `type` | int | Tower type |
| `unionId` | long | Alliance currently owning this tower |
| `FactionId` | int | Team color (yellow/green/red/blue) owning it |
| `curId` | long | Current contesting entity ID |
| `curFactionId` | int | Team of current contester |
| `legionId` | long | Legion/troop occupying it |
| `curLegionId` | long | Current contesting legion |
| `buildAt` | long | Timestamp when captured |
| `cfgId` | int | Config ID (tower type/tier) |

### `Coord` fields
- TBD — need to verify if these are grid (row, col) or world coordinates

---

## Key Questions to Answer
- [ ] What format is `coord`? Grid (0-23, 0-23) or world coordinates?
- [ ] What value does `unionId` have for unowned towers (0? -1?)
- [ ] How does `FactionId` map to yellow/green/red/blue?
- [ ] Is `KvkTerritoryInfoAck` received automatically at login or does it require a request?
- [ ] Does `KvkTerritoryInfoNtf` fire for every tower change in real time?
- [ ] Can we identify which towers are adjacent to our alliance territory from this data alone?

---

## Findings Log

### 2026-03-04 — KvkTerritoryInfoAck first capture

- **629 lands** received when Territory screen opened
- Fires multiple times (3x in quick succession) — likely one per device connected
- **`coord`** = `{X, Z}` in world coordinates (1000x display scale), same as castle coords
  - e.g. `X:450000, Z:2550000` = grid tile (450, 2550) in display units
  - Need to convert to 24x24 grid: divide by tile size
- **`FactionId`** observed values: `1`, `2`, `4` — mapping to team colors TBD
- **`unionId`** absent when tower has no owner (unowned = field missing entirely)
- **`type: 6`** on all samples — territory tower type
- **`cfgId`** values: `10003`, `10014`, `10015` — likely tower tier
- **`curFactionId`** only present when tower is actively being contested
- **`buildAt: 1`** on all samples — possibly default/unset value for unowned towers

### Open Questions — Updated
- [x] What format is `coord`? → World coordinates (1000x), NOT grid indices
- [x] Convert world coord → 24x24 grid index:
  - Territory map spans (0,0) → (7200,7200) display units = (0,0) → (7200000,7200000) world coords
  - Each grid square = 300 display units = 300000 world units
  - `grid_col = world_X // 300000`
  - `grid_row = world_Z // 300000`
- [x] FactionId → team color mapping (from FactionPlaidColor hex values):
  - `1` = Red (`E08978`)
  - `2` = Blue (`7C9AE0`)
  - `3` = Green (`7CE179`)
  - `4` = Yellow (`F4E66C`)
- [x] Unowned towers → `unionId` field absent (not 0)
- [x] `unionId` — **NEVER populated** in any of 629 towers. Game tracks ownership by `FactionId` (team), not alliance. `unionId` likely only used in KvK server-vs-server mode.
- [x] `KvkTerritoryInfoAck` fires automatically when Territory screen opens
- [x] `KvkTerritoryInfoNtf` fires on individual tower state changes (confirmed in registry)

