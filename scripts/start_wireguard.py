#!/usr/bin/env python3
#
# DESCRIPTION:
# This script is designed to be called by a cron job immediately before the ZFS
# replication task begins. Its sole purpose is to establish the necessary
# WireGuard connection.
#
# You can provide either:
#   --interface <name>    (expects /etc/wireguard/<name>.conf)
#   --config <path>       (path to a specific WireGuard config file)
#
# It performs three checks:
# 1. Verifies that the configuration file exists.
# 2. Checks if the specified WireGuard interface is already active using 'wg show'.
# 3. If the interface is not active, it brings it up using 'wg-quick up'.
#
# The script is idempotent: it will do nothing if the interface is already up,
# and it will not fail.
#
# USAGE (Cron Job):
#   * * * * * /path/to/start_wireguard.py <interface_name>
#
# EXAMPLE Cron Entry (Scheduled before replication):
#   0 2 * * * /usr/local/bin/start_wireguard.py wg0 >> /var/log/start_wireguard.log 2>&1
#
# ARGUMENTS:
#   --interface:  WireGuard interface name (e.g. wg0)
#   --config:     Path to a WireGuard configuration file
#

import argparse
import subprocess
import sys
from pathlib import Path
import time


def interface_exists(config: Path):
    """Check if a WireGuard interface is already active."""
    print(f"[{time.ctime()}] Checking if interface '{config.stem}' exists.")
    try:
        # wg show returns exit code 0 if the interface is up
        _ = subprocess.check_output(
            ["wg", "show", config.stem], stderr=subprocess.DEVNULL
        )
        print(f"[{time.ctime()}] Interface '{config.stem}' is already up.")
        return True
    except subprocess.CalledProcessError:
        # wg show returns non-zero if the interface is not up
        print(f"[{time.ctime()}] Interface '{config.stem}' does not exist.")
        return False


def bring_up_interface(config: Path):
    """Bring up the WireGuard interface using wg-quick with a given config file."""
    print(f"[{time.ctime()}] Attempting to bring up WireGuard using config '{config}'.")
    try:
        subprocess.run(
            ["wg-quick", "up", config],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(
            f"[{time.ctime()}] WireGuard interface '{config.stem}' from '{config}' brought up successfully."
        )
    except subprocess.CalledProcessError as e:
        print(
            f"[{time.ctime()}] Failed to bring up WireGuard interface '{config.stem}' from '{config}': {e}"
        )
        # Exit with a non-zero code if interface creation fails
        sys.exit(1)


def main():
    print("### Establish Wireguard Connection ###")
    parser = argparse.ArgumentParser(
        description="Bring up a WireGuard interface if it's not already up."
    )
    parser.add_argument(
        "--interface", help="Name of the WireGuard interface (e.g. wg0)"
    )
    parser.add_argument(
        "--config", help="Path to a WireGuard config file (e.g. /mnt/user/wg0.conf)"
    )
    args = parser.parse_args()

    # Require at least one argument
    if not args.interface and not args.config:
        parser.error("You must specify either --interface or --config.")

    # Build path to config
    config = Path(
        args.config if args.config else f"/etc/wireguard/{args.interface}.conf"
    ).resolve()
    print(f"[{time.ctime()}] Using config file '{config}'")

    # 1. Check for configuration file
    if not config.is_file():
        print(f"[{time.ctime()}] Error: Config file '{config}' does not exist.")
        sys.exit(1)

    # 2. Check if interface is already up
    if not interface_exists(config):
        # 3. Bring up the interface if it's not active
        bring_up_interface(config)

    print("### DONE ###\n")


if __name__ == "__main__":
    main()
