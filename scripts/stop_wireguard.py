#!/usr/bin/env python3
#
# DESCRIPTION:
# This script monitors ZFS replication activity on a TrueNAS system and automatically
# shuts down a WireGuard interface if no replication is detected within a grace period.
#
# It is designed to be called periodically by a cron job (e.g., every 5–15 minutes).
# Internally, it uses the TrueNAS middleware command:
#     midclt call core.get_jobs
# to check for active ZFS replication jobs via the TrueNAS API.
#
# The script implements a configurable grace period by polling the replication state
# for the duration of the --timeout argument (e.g., 60 seconds).
#
# 1. It first checks if the specified WireGuard interface is UP. If it’s DOWN, the script exits immediately.
# 2. If the interface is UP, it continuously checks for ZFS replication jobs in the RUNNING state every --interval seconds.
# 3. If any replication job is found (RUNNING), the script exits immediately, leaving the interface up.
# 4. If the full --timeout elapses without detecting any active replication, the WireGuard interface
#    is brought down using `wg-quick down`.
#
# The JobState Enum ensures only valid TrueNAS job states are used (e.g., RUNNING, SUCCESS, FAILED, etc.).
#
# USAGE (Cron Job):
# The cron schedule should match or exceed the script’s internal --timeout value.
#
# Example: Run every 5 minutes, allowing 60 seconds of internal polling:
#   */5 * * * * /usr/local/bin/stop_wireguard.py wg0 --timeout 60 --interval 5 >> /var/log/stop_wireguard.log 2>&1
#
# REQUIREMENTS:
#   - TrueNAS system (CORE or SCALE)
#   - Python 3.9+
#   - WireGuard installed and configured
#   - Sufficient privileges to run `wg`, `wg-quick`, and `midclt`
#
# EXIT BEHAVIOR:
#   0 — Interface already down or successfully brought down
#   1 — Failed to bring interface down
#   (Non-zero for internal errors or invalid configuration)
#

import json
import subprocess
import time
import argparse
import sys
from pathlib import Path
from enum import Enum

class JobState(Enum):
    """Valid job states for TrueNAS core.get_jobs."""
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WAITING = "WAITING"
    ABORTED = "ABORTED"
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"

def get_zfs_replication_jobs(state: JobState | None = None) -> list:
    """ 
    Query TrueNAS middleware for replication jobs, optionally filtered by state.

    :param state: Optional JobState to filter by (e.g., RUNNING)
    :return: List of job dictionaries from TrueNAS middleware
    """
    query = [["method", "~", "replication"]]
    if state is not None:
        if not isinstance(state, JobState):
            raise ValueError(f"Invalid job state: '{state}'. Must be a JobState Enum member.")
        query.append(["state", "=", state.value])
    # query API
    result = subprocess.run(
        ["midclt", "call", "core.get_jobs", json.dumps(query)],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    # Parse result into a list
    jobs = json.loads(result.stdout)
    return jobs

def replication_running() -> bool:
    """
    Check if any ZFS replication jobs are currently running.
    
    :return: True if at least one replication job is RUNNING, False otherwise
    """
    jobs = get_zfs_replication_jobs(JobState.RUNNING)
    print(f"[{time.ctime()}] Found {len(jobs)} running replication job(s).")
    return bool(jobs)


def interface_is_up(config: Path) -> bool:
    """
    Check if the WireGuard interface is already active.

    :param config: Path to WireGuard config file
    :return: True if interface is UP, False if DOWN
    """
    try:
        subprocess.check_output(["wg", "show", config.stem], stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False


def bring_down_interface(config: Path):
    """
    Bring down the WireGuard interface using wg-quick.

    :param config: Path to WireGuard config file
    """
    print(f"[{time.ctime()}] Attempting to bring down '{config.stem}'...")
    try:
        subprocess.run(
            ["wg-quick", "down", config],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[{time.ctime()}] Interface '{config.stem}' successfully brought down.")
    except subprocess.CalledProcessError as e:
        print(f"[{time.ctime()}] Failed to bring down '{config.stem}': {e}")
        # Exit with error code if the shutdown itself fails
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Monitor TrueNAS ZFS replication activity for a grace period and shut down "
            "WireGuard interface if no jobs are running. Intended to be run periodically (e.g., cron)."
        )
    )
    parser.add_argument("--interface", help="WireGuard interface name (e.g. wg0)")
    parser.add_argument("--config", help="Path to WireGuard config file (e.g. /mnt/user/wg0.conf)")
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

    # Require at least one argument
    if not args.interface and not args.config:
        parser.error("You must specify either --interface or --config.")

    # Build path to config
    config =  Path(args.config if args.config else f"/etc/wireguard/{args.interface}.conf").resolve()

    # --- Initial Interface Check ---
    if not interface_is_up(config):
        print(
            f"[{time.ctime()}] Interface '{config.stem}' is already down. Exiting immediately."
        )
        sys.exit(0)

    print(f"[{time.ctime()}] Interface '{config.stem}' is UP. Starting ZFS replication monitor.")

    # Use script start time as the reference for the grace period
    start_time = time.time()

    print(
        f"[{time.ctime()}] Duration (Grace Period): {args.timeout}s | Polling Interval: {args.interval}s"
    )

    try:
        # Loop runs until the timeout expires
        while (time.time() - start_time) < args.timeout:

            if replication_running():
                # Activity found! Exit immediately, keeping the interface up.
                print(f"[{time.ctime()}] Active replication detected. Keeping interface '{config.stem}' up.")
                sys.exit(0)

            # Log status and wait
            idle_time = int(time.time() - start_time)
            print(f"[{time.ctime()}] No active replication. Polling for {idle_time}s...")

            # Calculate remaining time to sleep to avoid overshooting the timeout
            time_to_sleep = min(args.interval, args.timeout - idle_time)

            if time_to_sleep > 0:
                time.sleep(time_to_sleep)
            else:
                # Break out if the remaining time is 0 or less, which means we've hit the timeout.
                break

        # If the loop finished without finding activity, the grace period is over.
        print(
            f"[{time.ctime()}] Grace period ({args.timeout}s) expired. No replication detected."
        )

        # Since we checked if the interface was up at the start, we can proceed to bring it down.
        # However, checking again ensures idempotency in case of external changes.
        if interface_is_up(config):
            bring_down_interface(config)
        else:
            # This path is highly unlikely but included for robustness
            print(
                f"[{time.ctime()}] Interface '{config.stem}' was already down. No action needed."
            )

        sys.exit(0)

    except KeyboardInterrupt:
        print(f"\n[{time.ctime()}] Interrupted by user. Exiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()
