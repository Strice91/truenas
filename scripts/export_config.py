#!/usr/bin/env python3
#
# DESCRIPTION:
# This script is designed to be called by a cron job or manual trigger to
# export the TrueNAS configuration database and secret seed.
#
# The exported .tar file is intended to be placed in a dataset that is
# subsequently backed up via ZFS replication to a remote system.
#
# It performs four checks:
# 1. Verifies the script is running with root privileges.
# 2. Ensures the target destination directory exists.
# 3. Compresses freenas-v1.db (and optionally pwenc_secret) into a .tar file.
# 4. Cleans up old exports in the destination based on a retention policy.
#
# USAGE:
#   python3 export_config.py /path/to/backup/dataset [--include-secret] [--retention 5]
#
# EXAMPLE Cron Entry:
#   0 1 * * * /usr/bin/python3 /root/scripts/export_config.py /mnt/pool/configs --include-secret
#

import argparse
import subprocess
import sys
import os
import time
import tarfile
import glob
from pathlib import Path
from typing import List


def check_root() -> None:
    """Verify that the script is executed with root privileges."""
    if os.geteuid() != 0:
        print(
            f"[{time.ctime()}] ERROR: This script must be run as root to access configuration files."
        )
        sys.exit(1)


def get_system_info() -> tuple[str, str]:
    """
    Extract system hostname and TrueNAS version.

    Returns:
        tuple: (hostname, version_string)
    """
    hostname = subprocess.getoutput("hostname").strip()
    version = "unknown"
    version_path = Path("/etc/version")

    if version_path.is_file():
        try:
            content = version_path.read_text().strip()
            # Equivalent to cut -d'-' -f2
            version = content.split("-")[1] if "-" in content else content
        except (IndexError, IOError):
            pass
    return hostname, version


def cleanup_old_exports(destination: Path, hostname: str, days: int) -> None:
    """
    Delete exported files older than the specified retention period.

    Args:
        destination: Path to the directory containing exports.
        hostname: The hostname prefix to match.
        days: Number of days to retain files.
    """
    print(
        f"[{time.ctime()}] Cleaning up exports in '{destination}' older than {days} days."
    )
    cutoff = time.time() - (days * 86400)

    # Matches files starting with 'config-export-[hostname]'
    pattern = str(destination / f"config-export-{hostname}-*.tar")
    for f_str in glob.glob(pattern):
        f_path = Path(f_str)
        if f_path.stat().st_mtime < cutoff:
            try:
                f_path.unlink()
                print(f"[{time.ctime()}] Deleted old export: {f_path.name}")
            except OSError as e:
                print(f"[{time.ctime()}] Error deleting {f_path.name}: {e}")


def run_export(destination: Path, include_secret: bool, retention: int) -> None:
    """
    Archive the configuration files into a tarball.

    Args:
        destination: Path where the tarball will be saved.
        include_secret: Whether to include the pwenc_secret file.
        retention: Days to keep old backups.
    """
    hostname, version = get_system_info()
    timestamp = time.strftime("%Y%m%d%H%M%S")
    filename = f"config-export-{hostname}-{version}-{timestamp}.tar"
    target_path = destination / filename

    # Files always located in /data/
    source_dir = Path("/data")
    files_to_archive: List[str] = ["freenas-v1.db"]

    if include_secret:
        files_to_archive.append("pwenc_secret")

    print(f"[{time.ctime()}] Starting export to '{target_path}'")

    try:
        with tarfile.open(target_path, "w") as tar:
            for file_name in files_to_archive:
                file_path = source_dir / file_name
                if file_path.exists():
                    tar.add(file_path, arcname=file_name)
                else:
                    print(
                        f"[{time.ctime()}] Warning: {file_name} not found in {source_dir}"
                    )

        print(f"[{time.ctime()}] Configuration successfully exported.")
        cleanup_old_exports(destination, hostname, retention)

    except Exception as e:
        print(f"[{time.ctime()}] CRITICAL: Export failed: {e}")
        sys.exit(1)


def main():
    print("### TrueNAS Configuration Export ###")

    parser = argparse.ArgumentParser(
        description="Export TrueNAS config for ZFS replication."
    )
    parser.add_argument(
        "destination",
        help="Path to the destination dataset (e.g. /mnt/pool/backup_dataset)",
    )
    parser.add_argument(
        "--include-secret",
        action="store_true",
        help="Include the pwenc_secret key for full recovery",
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=5,
        help="Number of days to keep local exports (default: 5)",
    )

    args = parser.parse_args()

    # 1. Verify environment
    check_root()

    # 2. Verify destination
    dest_path = Path(args.destination).resolve()
    if not dest_path.is_dir():
        print(
            f"[{time.ctime()}] Error: Destination directory '{dest_path}' does not exist."
        )
        sys.exit(1)

    # 3. Perform export
    run_export(dest_path, args.include_secret, args.retention)

    print("### DONE ###\n")


if __name__ == "__main__":
    main()
