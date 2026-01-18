#!/usr/bin/env python3
"""
Daily TrueNAS replication health check.

- Checks if all enabled replication tasks are up-to-date
- Sends a simple up/down ping to Uptime Kuma
"""

import json
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional
import time
import os
import tempfile
from datetime import datetime, date

from urllib.request import urlopen
from urllib.parse import quote
import argparse
import sys


@dataclass
class ReplicationTask:
    """
    Represents a ZFS replication task on TrueNAS.

    :param id: Unique identifier of the replication task
    :param name: Human-readable task name
    :param enabled: Whether the task is enabled
    :param state: Last known state of the task (FINISHED, ERROR, RUNNING, etc.)
    :param last_datetime: Epoch timestamp (milliseconds) of the last run
    :param last_snapshot: Name of the last snapshot processed
    :param error: Error message if the task failed
    """

    id: int
    name: str
    enabled: bool

    # normalized / derived fields
    state: Optional[str] = None  # FINISHED / ERROR / RUNNING / ...
    last_datetime: Optional[int] = None  # epoch millis
    last_snapshot: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def from_midclt(cls, data: dict) -> "ReplicationTask":
        """
        Create a ReplicationTask instance from TrueNAS middleware JSON data.

        A typical task looks like this:
        {
            "id": 6,
            "enabled": true,
            "name": "some name",
            "state": {
                "state": "FINISHED",
                "datetime": {
                    "$date": 1768529192000
                },
                "warnings": [],
                "last_snapshot": "path/to/snapshot"
            },
        },
        {
            "id": 7,
            "enabled": true,
            "name": "some other name",
            "state": {
                "state": "ERROR",
                "datetime": {
                    "$date": 1768753995000
                },
                "error": "[Errno 101] Network is unreachable.",
                "last_snapshot": "path/to/another_snapshot"
            },
        }

        :param data: Dictionary from `midclt call replication.query`
        :return: ReplicationTask instance
        """
        state_block = data.get("state") or {}

        return cls(
            id=data["id"],
            name=data["name"],
            enabled=data.get("enabled", False),
            state=state_block.get("state"),
            last_datetime=(
                state_block.get("datetime", {}).get("$date")
                if isinstance(state_block.get("datetime"), dict)
                else None
            ),
            last_snapshot=state_block.get("last_snapshot"),
            error=state_block.get("error"),
        )

    @property
    def ok(self) -> bool:
        """
        Check if the replication task completed successfully (FINISHED without errors).

        :return: True if successful, False otherwise
        """
        return self.state == "FINISHED" and not self.error

    @property
    def ran_today(self) -> bool:
        """
        Check if the replication task ran today (local time).

        :return: True if last run was today, False otherwise
        """
        if not self.last_datetime:
            return False

        run_date = datetime.fromtimestamp(self.last_datetime / 1000).date()
        return run_date == date.today()

    def is_within_window(self, window: int) -> bool:
        """
        Check if the task finished successfully within the last X hours.

        :param window: allwoed time window in hours since the last replication
        """
        if not self.last_datetime or not self.ok:
            return False

        # Calculate time difference
        last_run_time = datetime.fromtimestamp(self.last_datetime / 1000)
        time_diff = datetime.now() - last_run_time

        return time_diff.total_seconds() <= (window * 3600)

    @property
    def up_to_date(self) -> bool:
        """
        Check if the replication task completed successfully today.

        :return: True if up-to-date, False otherwise
        """
        return self.ok and self.ran_today


def get_cache_path() -> str:
    """Return the path to the replication status cache file."""
    return os.path.join(tempfile.gettempdir(), "check_replication_cache.json")


def load_replication_cache(window: float) -> Optional[list[ReplicationTask]]:
    """
    Try to load replication tasks from cache.
    
    :param window: validity window in hours
    :return: List of ReplicationTask objects if cache is valid, None otherwise
    """
    cache_file = get_cache_path()
    if not os.path.exists(cache_file):
        return None

    try:
        mtime = os.path.getmtime(cache_file)
        age_hours = (time.time() - mtime) / 3600
        
        if age_hours > window:
            print(f"[{time.ctime()}] Cache expired (age: {age_hours:.2f}h > {window}h). Refreshing.")
            return None

        with open(cache_file, "r") as f:
            cached_data = json.load(f)
        
        tasks = []
        # Check for RUNNING state and reconstruct objects
        for task_dict in cached_data:
            # Flattened structure has 'state' as a string field
            if task_dict.get("state") == "RUNNING":
                print(f"[{time.ctime()}] Cache exists inside window but contains RUNNING tasks. Refreshing.")
                return None
            tasks.append(ReplicationTask(**task_dict))
        
        print(f"[{time.ctime()}] Using cached replication data (age: {age_hours:.2f}h).")
        return tasks

    except Exception as e:
        print(f"[{time.ctime()}] WARNING: Failed to read cache: {e}")
        return None


def save_replication_cache(tasks: list[ReplicationTask]) -> None:
    """
    Save replication tasks to cache.
    
    :param tasks: List of ReplicationTask objects
    """
    try:
        with open(get_cache_path(), "w") as f:
            data = [asdict(t) for t in tasks]
            json.dump(data, f)
        print(f"[{time.ctime()}] Replication cache updated.")
    except Exception as e:
        print(f"[{time.ctime()}] WARNING: Failed to write cache: {e}")


def get_replication_tasks(cache_validity_hours: float = 0) -> list[ReplicationTask]:
    """
    Query TrueNAS middleware for replication tasks and return ReplicationTask objects.
    
    :param cache_validity_hours: If > 0, try to read from cache if not older than this many hours.
    :return: List of ReplicationTask objects
    """
    tasks = None

    if cache_validity_hours > 0:
        tasks = load_replication_cache(cache_validity_hours)

    if tasks is not None:
        return tasks

    # If we are here, we need to fetch from midclt
    try:
        result = subprocess.run(
            ["midclt", "call", "replication.query"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(
            f"[{time.ctime()}] ERROR: midclt replication.query failed: {e.stderr.strip()}"
        )
        return []

    try:
        raw_tasks = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[{time.ctime()}] ERROR: Failed to parse replication.query JSON output")
        return []

    if not isinstance(raw_tasks, list):
        print(f"[{time.ctime()}] ERROR: Unexpected replication.query output format")
        return []

    tasks = []
    for task in raw_tasks:
        try:
            tasks.append(ReplicationTask.from_midclt(task))
        except KeyError as e:
            print(
                f"[{time.ctime()}] WARNING: Skipping malformed replication task "
                f"(missing field {e})"
            )
            
    # Save processed tasks to cache
    if cache_validity_hours > 0 and tasks:
        save_replication_cache(tasks)

    return tasks


def check_all_replications(window: int, enable_cache: bool = False) -> bool:
    """
    Check if all enabled replication tasks have successfully completed today.

    :param window: allwoed time window in hours since the last replication
    :param enable_cache: Whether to use cached replication state
    :return: True if all enabled tasks are up-to-date, False otherwise
    """
    tasks = get_replication_tasks(window if enable_cache else 0)
    enabled_tasks = [t for t in tasks if t.enabled]

    if not enabled_tasks:
        print(f"[{time.ctime()}] No enabled replication tasks found.")
        return True

    outdated = [t for t in enabled_tasks if not t.is_within_window(window)]

    if outdated:
        print(f"[{time.ctime()}] Found outdated replications within the {window}h window:")
        for t in outdated:
            reason = t.error or f"state={t.state} (Last run: {datetime.fromtimestamp(t.last_datetime/1000)})"
            print(f"  - {t.name}: {reason}")
        return False

    print(f"[{time.ctime()}] All replication tasks are up to date.")
    return True


def notify_uptime_kuma(
    up: bool, kuma_url: str, kuma_token: str, msg: str = "OK"
) -> bool:
    """
    Send a simple up/down ping to an Uptime Kuma monitor.

    :param up: True for "up", False for "down"
    :param kuma_url: Base URL of Uptime Kuma instance (without trailing slash)
    :param kuma_token: Push token for the monitor
    :param msg: Optional short message (default: "OK")
    :return: True if HTTP request succeeded (2xx), False otherwise
    """
    status = "up" if up else "down"
    encoded_msg = quote(msg)
    url = f"{kuma_url}/api/push/{kuma_token}?status={status}&msg={encoded_msg}&ping="

    try:
        with urlopen(url, timeout=10) as response:
            code = response.getcode()
        if 200 <= code < 300:
            print(f"[{time.ctime()}] Uptime Kuma notified successfully ({status}).")
            return True
        print(f"[{time.ctime()}] WARNING: Uptime Kuma returned HTTP {code}.")
        return False
    except Exception as e:
        print(f"[{time.ctime()}] ERROR: Failed to notify Uptime Kuma ({status}): {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Check TrueNAS replication health and notify Uptime Kuma."
    )
    parser.add_argument(
        "--kuma-url", required=True, help="Uptime Kuma base URL, e.g., kuma.example.com"
    )
    parser.add_argument("--kuma-token", required=True, help="Uptime Kuma push token")
    parser.add_argument(
        "--msg-up", default="Replication OK", help="Message when replication is healthy"
    )
    parser.add_argument(
        "--msg-down",
        default="Replication not up to date",
        help="Message when replication failed",
    )
    parser.add_argument(
        "--window", type=float, default=24, 
        help="The rolling window in hours to consider a backup 'current' (default: 24)"
    )
    parser.add_argument(
        "--enable-cache", action="store_true",
        help="Enable caching of replication state to avoid waking up drives"
    )
    args = parser.parse_args()

    if check_all_replications(args.window, args.enable_cache):
        notify_uptime_kuma(True, args.kuma_url, args.kuma_token, args.msg_up)
        sys.exit(0)
    else:
        notify_uptime_kuma(False, args.kuma_url, args.kuma_token, args.msg_down)
        sys.exit(1)


if __name__ == "__main__":
    main()
