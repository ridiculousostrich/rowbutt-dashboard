# Rowbutt Dashboard — Work Order

## Discoveries

- **Web UI** → running on `localhost:8123` (PID 6464) manually started, not via systemd. Empty dashboard — no cost data.
- **Systemd units were never deployed** — deploy/ files existed but were never `systemctl enable` enabled, so the aggregator timer never fired. The Sep 5 report was generated ad-hoc (Sep 6 12:23) showing all zeros.
- **Timer now installed + active** — `rowbutt-aggregator.timer` next trigger at 23:55:27 UTC.
  - Path expansion works (`%h` → /root, venv binary resolves).
  - Service fails on `pull-all` returns exit code 1 because no agent listens.
- **Agent target unreachable**: `gx10-e587:5000` (→ 192.168.1.11) connection refused.
- **192.168.1.12:5000** — also connection refused.
- **Data directory**: `/root/.rowbutt/` — agent.db (45KB), aggregator.db (40KB), `agents.json` configured to `http://gx10-e587:5000` (unreachable).

## Progress (this session)

1. Installed systemd units for the aggregator (service + timer):
   → `/etc/systemd/system/rowbutt-aggregator.service service`
   → `/etc/systemd/system/rowbutt-aggregator.service service`
   → /etc/systemd/system/rowbutt-aggregator.timer
2. Timer enabled + active, next trigger at 23:55:27 UTC
3. Service execution path verified — `%h` expands correctly

## Remaining Issues

### High Priority

- **No agent on GX10** — 192.168.1.11:5000 and .12:5000 both refused. Both GX10s need the `agent` daemon deployed + running.
   -  In `agents.json` URL must point to the actual agent location once it's up.
- **No data, all zeros** — expected without an agent.

### Medium

- **Web UI still runs manually** (PID 6464, no systemd). Should migrate to systemd later.
   - `deploy/rowbutt-web.service` exists but not installed.
