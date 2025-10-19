#!/usr/bin/env python3
#
# DESCRIPTION:
# This script is designed to be called periodically by a cron job (e.g., every 5-15 minutes).
#
# To implement a grace period the script runs a continuous internal
# polling loop for a duration equal to the --timeout argument (e.g., 60 seconds).
#
# 1. It first checks if the specified WireGuard interface is UP. If it's DOWN, the script exits immediately.
# 2. If the interface is UP, it starts a timer and checks for 'zfs recv' every --interval seconds (e.g., 5 seconds).
# 3. If 'zfs recv' is found, the script exits immediately, leaving the interface up.
# 4. If the full --timeout (e.g., 60 seconds) elapses without finding 'zfs recv',
#    it shuts down the WireGuard interface and then exits.
#
# USAGE (Cron Job):
# The cron interval must be equal to or greater than the script's internal --timeout.
#
#   * * * * * /path/to/stop_wireguard.py <interface_name> [OPTIONS]
#
# EXAMPLE Cron Entry (Runs every 5 minutes, allowing 60 seconds of polling):
#   */5 * * * * /usr/local/bin/stop_wireguard.py wg0 --timeout 60 --interval 5 >> /var/log/stop_wireguard.log 2>&1
#

import subprocess
import time
import argparse
import sys


def is_zfs_recv_running():
    """Return True if a zfs receive process is running."""
    try:
        output = subprocess.check_output(
            ["pgrep", "-f", "zfs recv"], stderr=subprocess.DEVNULL
        )
        return bool(output.strip())
    except subprocess.CalledProcessError:
        # No process found
        return False


def interface_is_up(interface):
    """Check if the WireGuard interface is already active."""
    try:
        subprocess.check_output(["wg", "show", interface], stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False


def bring_down_interface(interface):
    """Bring down the WireGuard interface using wg-quick."""
    print(f"[{time.ctime()}] Attempting to bring down {interface}...")
    try:
        subprocess.run(
            ["wg-quick", "down", interface],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[{time.ctime()}] Interface {interface} successfully brought down.")
    except subprocess.CalledProcessError as e:
        print(f"[{time.ctime()}] Failed to bring down {interface}: {e}")
        # Exit with error code if the shutdown itself fails
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Monitor zfs recv process for a grace period and shut down " \
        "WireGuard if it remains inactive. Designed to be run once per execution (e.g., by cron)."
    )
    parser.add_argument("interface", help="WireGuard interface name (e.g. wg0)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Seconds the script will run, acting as the grace period (default: 60)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Polling interval in seconds (default: 5)",
    )
    args = parser.parse_args()

    interface = args.interface
    timeout = args.timeout
    interval = args.interval

    # --- Initial Interface Check ---
    if not interface_is_up(interface):
        print(
            f"[{time.ctime()}] Interface {interface} is already down. Exiting immediately."
        )
        sys.exit(0)

    print(f"[{time.ctime()}] Interface {interface} is UP. Starting zfs recv monitor.")

    # Use script start time as the reference for the grace period
    start_time = time.time()

    print(
        f"[{time.ctime()}] Duration (Grace Period): {timeout}s | Polling Interval: {interval}s"
    )

    try:
        # Loop runs until the timeout expires
        while (time.time() - start_time) < timeout:

            if is_zfs_recv_running():
                # Activity found! Exit immediately, keeping the interface up.
                print(f"[{time.ctime()}] zfs recv is active. Monitor complete.")
                sys.exit(0)

            # Log status and wait
            idle_time = int(time.time() - start_time)
            print(f"[{time.ctime()}] zfs recv not running. Polling for {idle_time}s...")

            # Calculate remaining time to sleep to avoid overshooting the timeout
            time_to_sleep = min(interval, timeout - idle_time)

            if time_to_sleep > 0:
                time.sleep(time_to_sleep)
            else:
                # Break out if the remaining time is 0 or less, which means we've hit the timeout.
                break

        # If the loop finished without finding activity, the grace period is over.
        print(
            f"[{time.ctime()}] Grace period ({timeout}s) expired without zfs recv activity. Initiating shutdown."
        )

        # Since we checked if the interface was up at the start, we can proceed to bring it down.
        # However, checking again ensures idempotency in case of external changes.
        if interface_is_up(interface):
            bring_down_interface(interface)
        else:
            # This path is highly unlikely but included for robustness
            print(
                f"[{time.ctime()}] Interface {interface} was brought down by another process. Doing nothing."
            )

        sys.exit(0)

    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
