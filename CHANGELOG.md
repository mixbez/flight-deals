# Changelog

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
