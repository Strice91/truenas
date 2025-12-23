# truenas

Utilities to help automate TrueNAS SCALE backups and WireGuard-controlled replication windows.

This repository contains three small, self-contained Python scripts intended to be run on a TrueNAS SCALE system (tested on TrueNAS SCALE):

- scripts/export_config.py — export the TrueNAS configuration database and optional secret to a tarball for replication.
- scripts/start_wireguard.py — bring up a WireGuard interface (idempotent) prior to running replication.
- scripts/stop_wireguard.py — monitor TrueNAS replication activity and bring the WireGuard interface down when idle.

Table of Contents
- [Why this repo exists](#why-this-repo-exists)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Permissions & logging](#permissions--logging)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

Why this repo exists
--------------------
These scripts are designed to support a workflow where:
1. A WireGuard VPN is brought up just before ZFS replication runs,
2. The TrueNAS configuration (DB and optional secret) is exported to a dataset that gets replicated,
3. After replication completes (or no replication is detected), the WireGuard interface is brought down to reduce attack surface and resource usage.

Requirements
------------
- TrueNAS SCALE (these scripts have only been tested on SCALE)
- Python 3.9+ installed on the host (scripts use modern typing and the standard library)
- Root privileges for bringing interfaces up/down and accessing TrueNAS internals
- WireGuard tools: `wg`, `wg-quick`
- TrueNAS middleware CLI (for stop_wireguard): `midclt` (available on TrueNAS SCALE)
- A dataset on the TrueNAS host for storing exported tar files (for export_config.py)

Note: These scripts intentionally minimize Python dependencies and call system utilities directly. Each script includes a shebang so they can be run directly if executable.

Installation
------------
Clone the repository and make scripts executable:
```bash
git clone https://github.com/Strice91/truenas.git
cd truenas
chmod +x scripts/*.py
```

No pip install is required for the scripts themselves.

Usage
-----
Because each script contains a shebang, you can execute them directly (preferred):

1) Export TrueNAS config
```bash
# Example: include the secret and keep 7 days of exports
sudo ./scripts/export_config.py /mnt/pool/config-backups --include-secret --retention 7
```
- `destination`: path to dataset (must exist)
- `--include-secret`: include `pwenc_secret` to allow full restore
- `--retention N`: keep N days of exports locally

2) Start WireGuard (idempotent)
```bash
# bring up interface by name (reads /etc/wireguard/wg0.conf)
sudo ./scripts/start_wireguard.py --interface wg0

# or provide a specific config file
sudo ./scripts/start_wireguard.py --config /path/to/wg0.conf
```

3) Stop WireGuard after monitoring replication activity
```bash
# default polls for 60s with 5s interval
sudo ./scripts/stop_wireguard.py --interface wg0 --timeout 60 --interval 5

# or with explicit config path
sudo ./scripts/stop_wireguard.py --config /path/to/wg0.conf --timeout 90 --interval 10
```
- `--timeout`: grace period to wait while polling for active replication
- `--interval`: how often to poll during the grace period

Permissions & logging
---------------------
- All three scripts may require root privileges:
  - `export_config.py` reads system files under `/data` and writes to dataset paths.
  - `start/stop_wireguard.py` call `wg`/`wg-quick` which require root.
- Redirect stdout/stderr to log files when running from cron/systemd.
- Keep exported config dataset with restricted permissions and replicate to a secure remote.

Troubleshooting
---------------
- `export_config.py`: Ensure the destination directory exists and is writable by the user running the script.
- `start_wireguard.py`: If interface fails to bring up, check the config file at `/etc/wireguard/<name>.conf` and `wg-quick` logs.
- `stop_wireguard.py`: Requires `midclt` to query TrueNAS jobs. Run `midclt call core.get_jobs` manually to validate permission and output format.
- For intermittent failures, run the scripts manually with the same user and environment as the automated runner (use absolute paths if needed).

Contributing
------------
Small repo — contributions welcome:
- Open an issue describing the change.
- Fork -> branch -> PR. Keep changes focused.

License
-------
This project is licensed under the MIT License — see the LICENSE file.
