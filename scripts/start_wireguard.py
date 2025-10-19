#!/usr/bin/env python3
#
# wg-pre-replication.py
#
# DESCRIPTION:
# This script is designed to be called by a cron job immediately before the ZFS 
# replication task begins. Its sole purpose is to establish the necessary 
# WireGuard connection.
#
# It performs three checks:
# 1. Verifies that the configuration file '/etc/wireguard/<interface>.conf' exists.
# 2. Checks if the specified WireGuard interface is already active using 'wg show'.
# 3. If the interface is not active, it brings it up using 'wg-quick up'.
#
# The script is idempotent: it will do nothing if the interface is already up, 
# and it will not fail.
#
# USAGE (Cron Job):
#   * * * * * /path/to/wg-pre-replication.py <interface_name>
#
# EXAMPLE Cron Entry (Scheduled before replication):
#   0 2 * * * /usr/local/bin/wg-pre-replication.py wg0 >> /var/log/wg_pre_replication.log 2>&1
#
# ARGUMENTS:
#   interface:   The name of the WireGuard interface to manage (e.g., wg0).
#

import argparse
import subprocess
import sys
import os

def config_exists(interface):
    """Check if the WireGuard config file exists."""
    config_path = f"/etc/wireguard/{interface}.conf"
    print(f"Checking if config '{config_path}' exists.")
    return os.path.isfile(config_path)

def interface_exists(interface: str):
    """Check if a WireGuard interface is already active."""
    print(f"Checking if interface '{interface}' exists")
    try:
        # wg show returns exit code 0 if the interface is up
        output = subprocess.check_output(['wg', 'show', interface], stderr=subprocess.DEVNULL)
        print(f"Interface '{interface}' is already up.")
        return True
    except subprocess.CalledProcessError:
        # wg show returns non-zero if the interface is not up
        print(f"Interface '{interface}' does not exist.")
        return False

def bring_up_interface(interface: str):
    """Bring up the WireGuard interface using wg-quick."""
    print(f"Attempting to bring up interface '{interface}'.")
    try:
        subprocess.run(['wg-quick', 'up', interface], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Interface '{interface}' brought up successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to bring up '{interface}': {e}")
        # Exit with a non-zero code if interface creation fails
        sys.exit(1)

def main():
    print("### Establish Wireguard Connection ###")
    parser = argparse.ArgumentParser(description="Bring up a WireGuard interface if it's not already up.")
    parser.add_argument("interface", help="Name of the WireGuard interface (e.g. wg0)")
    args = parser.parse_args()

    # 1. Check for configuration file
    if not config_exists(args.interface):
        print(f"Error: Config file /etc/wireguard/{args.interface}.conf does not exist.")
        sys.exit(1)

    # 2. Check if interface is already up
    if not interface_exists(args.interface):
        # 3. Bring up the interface if it's not active
        bring_up_interface(args.interface)

    print("### DONE ###\n")
if __name__ == '__main__':
    main()