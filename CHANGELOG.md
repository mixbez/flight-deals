# Changelog

## [2.1] — 2026-03-19

### Added
- Admin command `/announce TEXT` — broadcasts a message to all users with Markdown preserved

## [2.0] — 2026-03-19

### Added
- Admin command `/analytics` — sends a clickable link to the analytics dashboard

## [1.9] — 2026-03-19

### Changed
- `/start` now shows the full command list (same as `/help`); admin users also see admin commands
- Deal messages: raw URL replaced with `[link](url)` Markdown hyperlink
- Deal messages: removed "Flight Deals Bot" footer from all messages; `by aboutmisha.com` remains, shown inline with the link
- Removed global `FOOTER` constant — it was appended to every message including non-deal ones

## [1.8] — 2026-03-19

### Added
- Analytics frontend at `/flights/analytics?token=...` (Chart.js dashboard)
- JSON data endpoint at `/flights/analytics/data?token=...`
- Token derived from `admin_chat_id` + `telegram_bot_token` — no new config needed
- Analytics URL logged to stdout on every startup
- Caddy route: `/flights/analytics*` → `backend-flightdeals:8080`
- Tracked events: user joins, deals sent per day with origin/destination breakdown
- Settings distributions: base price and days-ahead across all users
- `joined_at` field added to user records; existing users backfilled as `"before-analytics"`
- KPI cards: total users, total deals sent, today's deals, today's new users
- Charts: deals per day, joins per day, top origins, top destinations, price distribution, days distribution

## [1.7] — 2026-03-19

### Added
- Daily backup of `state.json` via `docker cp` from the running container
- Integrated into existing `/opt/backups/pg_backup.sh` (runs at 3am via cron)
- Backup stored as `/opt/backups/flightdeals_state_YYYY-MM-DD.json`, 7-day retention
- Graceful warning (no crash) if container is not running at backup time

## [1.6] — 2026-03-19

### Fixed
- Connecting flight pricing now uses estimated direct flight duration (from airport
  great-circle distance) as the price baseline, not actual travel time. A 10h connecting
  flight BUD→LON is now judged against the ~2.5h direct baseline — it can only pass if
  it's priced like a 2.5h flight, not like a 10h one.

### Added
- `AIRPORT_COORDS` dictionary with ~100 common airports (IATA → lat/lon)
- `estimated_flight_minutes(origin, destination)` — haversine + 850 km/h cruise speed
- Falls back to `base_duration_minutes` for unknown airport pairs
- `_haversine_km()` pure function (also covered in unit tests)

## [1.5] — 2026-03-19

### Added
- `/destination ANY` — "everywhere" round-trip mode: searches outbound to all destinations,
  then fetches return flights for the top 20 cheapest destinations found
- Respects `/tripdays` min/max range and all existing price/duration filters
- `trip_type_label()` helper to eliminate duplicate trip-type display logic
- `/settings` and preset display correctly show "везде (туда-обратно)" for ANY mode

## [1.4] — 2026-03-19

### Added
- `/tripdays MIN MAX` — set minimum and maximum days between outbound and return flights
- Default range: 1–30 days
- Round-trip combination loop now filters out pairs outside the configured range
- `/settings` shows current trip days range

## [1.3] — 2026-03-19

### Added
- `/savepreset NAME` — save current settings as a named preset (max 5 per user)
- `/loadpreset NAME` — restore settings from a preset
- `/deletepreset NAME` — remove a preset
- `/mypresets` — list all saved presets with a summary
- Preset names validated: alphanumeric, hyphens and underscores only
- Presets stored in `state.json` under each user's record

## [1.2] — 2026-03-19

### Added
- Admin command `/approval on|off` — toggle whether new users require manual approval
- When approval is off, `/start` auto-approves new users immediately
- `/approval` with no argument shows current status

## [1.1] — 2026-03-19

### Added
- Webhook-based Telegram integration (replaces GitHub Actions polling)
- Multi-user system with admin approval workflow
- Round-trip flight search (departure + return)
- Referral question onboarding for new users
- `/settings` command for users
- Admin commands: `/approve`, `/reject`, `/revoke`, `/userlist`, `/users`, `/write`
- Persistent state storage at `/app/data/state.json`
- Month-level API batching for Aviasales (reduces API calls)
- Deduplication of sent deals via MD5 hashing

### Fixed
- Empty destination parameter causing API errors
- `direct` field parameter name/value handling
- Set serialization for `sent_deals`
- Hourly check interval restored to 3600s (was accidentally left at 60s)

### Changed
- Architecture: from GitHub Actions cron job to persistent async aiohttp daemon
- State file moved to `/app/data/` for Docker volume persistence
