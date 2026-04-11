#!/usr/bin/env python3
import subprocess
import sys
import argparse
from dataclasses import dataclass
from typing import Optional
from urllib.request import urlopen
from urllib.parse import quote

class ZfsCommandError(RuntimeError):
    """
    Raised when a 'zfs' subprocess command fails unexpectedly.
    Allows callers (e.g. main) to decide how to handle the failure
    rather than forcing a hard sys.exit() deep inside a class method.
    """

def format_size(size_bytes: float) -> str:
    """
    Format byte values into human-readable units (KB, MB, GB).

    :param size_bytes: The size in bytes to format.
    :return: A formatted string with appropriate units.
    """
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:>7.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:>7.1f} MB"
    else:
        return f"{size_bytes / 1024**3:>7.2f} GB"

@dataclass
class DatasetConfig:
    """
    Configuration container for dataset monitoring thresholds and settings.

    :ivar lookback: Number of snapshots to consider for average calculation.
    :ivar verbosity: Whether to enable detailed logging and output.
    :ivar ratio: Maximum allowed write-to-referenced size ratio (0.0 to disable).
    :ivar size: Minimum size threshold in bytes for an anomaly to be relevant.
    :ivar growth_factor: Sensitivity threshold for historical growth (0.0 to disable).
    """
    lookback: int
    verbosity: bool
    ratio: float
    size: int
    growth_factor: float

class ZfsDataset:
    """
    Represents a ZFS dataset and its health status.
    Snapshot data is fetched on construction so the object is immediately
    ready for evaluation.
    """
    def __init__(self, name: str, referenced_size: int, config: DatasetConfig):
        """
        Initialize a new ZFS dataset evaluator.

        :param name: The full path name of the ZFS dataset.
        :param referenced_size: Current referenced size of the dataset in bytes.
        :param config: Configuration settings for monitoring.
        """
        self.name = name
        self.referenced_size = referenced_size
        self.config = config
        self.name_width: int = 40
        self.current_written: Optional[int] = None
        self.avg_written: Optional[float] = None
        self.has_data: bool = self._fetch_snapshot_data()

    def _fetch_snapshot_data(self) -> bool:
        """
        Retrieves snapshot written-size history for this dataset using 'zfs list'.

        :return: True if sufficient snapshot history was found, False otherwise.
        :raises ZfsCommandError: If the 'zfs list' command fails.
        """
        try:
            # -p for exact bytes, -s creation to sort by time, -d 1 to only look at direct snapshots
            cmd = ["zfs", "list", "-t", "snapshot", "-o", "name,written", "-H", "-p", "-s", "creation", "-d", "1", self.name]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)

            lines = [line for line in result.stdout.strip().split('\n') if line]
            if not lines:
                return False

            # Take the last snapshots according to lookback count
            relevant_lines = lines[-(self.config.lookback + 1):]
            if len(relevant_lines) < 2:
                return False

            history = []
            for line in relevant_lines:
                parts = line.split('\t')
                if len(parts) == 2:
                    try:
                        history.append({"name": parts[0], "written": int(parts[1])})
                    except ValueError:
                        continue  # Skip snapshots with unknown/missing written size (e.g. '-')

            if not history:
                return False

            self.current_written = history[-1]["written"]
            previous_values = [h["written"] for h in history[:-1]]
            self.avg_written = sum(previous_values) / len(previous_values)

            if self.config.verbosity:
                for h in history:
                    is_current = h["name"] == history[-1]["name"]
                    prefix: str = "  *> " if is_current else "  -> "
                    print(f"{prefix}{h['name']:<55} | {format_size(h['written'])}")
                print(" " * 80)

            return True

        except subprocess.CalledProcessError as e:
            stderr_detail = f": {e.stderr.strip()}" if e.stderr else ""
            raise ZfsCommandError(
                f"'zfs list' failed for dataset '{self.name}' (exit {e.returncode}){stderr_detail}"
            ) from e
        except Exception as e:
            print(f"Warning: Unexpected error fetching snapshot data for '{self.name}': {type(e).__name__}: {e}", file=sys.stderr)
            return False

    @property
    def growth_alert(self) -> bool:
        """
        Check if the current snapshot write is a historical growth anomaly.

        Evaluates if 'current_written' exceeds both the configured '--size' floor 
        and the '--growth' factor relative to the historical average.

        :return: True if a growth anomaly is detected.
        """
        if not self.has_data or self.current_written is None or self.avg_written is None:
            return False
        return (
            self.config.growth_factor > 0
            and self.current_written > self.config.size
            and self.current_written > (self.avg_written * self.config.growth_factor)
        )

    @property
    def ratio(self) -> float:
        """
        Calculate the ratio of current snapshot write size to total referenced size.

        :return: The calculated ratio (0.0 to 1.0).
        """
        if self.current_written is None or self.referenced_size <= 0:
            return 0.0
        return self.current_written / self.referenced_size

    @property
    def ratio_alert(self) -> bool:
        """
        Check if the written/referenced ratio exceeds the configured threshold.

        Evaluates if 'current_written' exceeds both the configured '--size' floor
        and the '--ratio' limit relative to total referenced data.

        :return: True if a ratio anomaly is detected.
        """
        if not self.has_data or self.current_written is None:
            return False
        return (
            self.config.ratio > 0
            and self.current_written > self.config.size
            and self.ratio > self.config.ratio
        )

    def is_valid(self) -> tuple[bool, list[str]]:
        """
        Evaluate all active health checks for this dataset.

        :return: A tuple containing (is_healthy, list_of_alert_reasons).
        """
        reasons: list[str] = []
        if self.config.growth_factor > 0 and self.growth_alert:
            reasons.append("GROWTH")
        if self.config.ratio > 0 and self.ratio_alert:
            reasons.append("RATIO")

        return (len(reasons) == 0, reasons)

    @property
    def growth_multiple(self) -> Optional[float]:
        """
        Calculate the actual growth multiple relative to the historical average.

        :return: The multiple (current / avg) or None if data is missing.
        """
        if not self.has_data or self.current_written is None or not self.avg_written:
            return None
        return self.current_written / self.avg_written

    def format_row(self, name_width: int) -> str:
        """
        Generate a fully formatted table row string for this dataset.

        :param name_width: The width to use for the DATASET column name.
        :return: A pipe-separated string representing the dataset status row.
        """
        if not self.has_data:
            parts = [f"{self.name:<{name_width}}", f"{'N/A':>10}"]
            if self.config.growth_factor > 0:
                parts.append(f"{'N/A':>10}")
                parts.append(f"{'N/A':>8}")
            if self.config.ratio > 0:
                parts.append(f"{'N/A':>7}")
            parts.append("Skipped")
            return " | ".join(parts)

        valid, reasons = self.is_valid()
        row = [f"{self.name:<{name_width}}", format_size(self.current_written)]
        if self.config.growth_factor > 0:
            row.append(format_size(self.avg_written))
            gm = self.growth_multiple
            if gm is not None:
                flag = "!" if self.growth_alert else " "
                row.append(f"{gm:>6.1f}x{flag}")
            else:
                row.append(f"{'N/A':>8}")
        if self.config.ratio > 0:
            flag = "!" if self.ratio_alert else " "
            row.append(f"{self.ratio:>6.1%}{flag}")
        row.append("ALERT" if not valid else "OK")
        return " | ".join(row)

class DatasetFactory:
    """
    Factory class to discover and create ready-to-evaluate ZfsDataset objects.
    """
    def __init__(self, lookback: int, verbosity: bool, ratio: float, size_mb: int, growth: float, path_list: Optional[list[list[str]]] = None):
        """
        Initialize the dataset factory with provided monitoring settings.

        :param lookback: Number of snapshots to fetch.
        :param verbosity: Enable verbose output during data fetching.
        :param ratio: Sensitivity threshold for size ratios.
        :param size_mb: Minimum relevance floor in MB.
        :param growth: Sensitivity threshold for historical growth.
        :param path_list: Optional nested list of dataset paths to scan.
        """
        self.config = DatasetConfig(
            lookback=lookback,
            verbosity=verbosity,
            ratio=ratio,
            size=size_mb * 1024 * 1024,
            growth_factor=growth
        )
        self.path_list = [p for sublist in path_list for p in sublist] if path_list else []

    def get_datasets(self) -> list[ZfsDataset]:
        """
        Discover ZFS datasets and perform snapshot analysis for each.

        :return: List of target datasets, each fully initialized with historical data.
        :raises ZfsCommandError: If ZFS discovery fails.
        """
        all_discovered: dict[str, int] = {}
        paths_to_query = self.path_list or [None]
        for path in paths_to_query:
            all_discovered.update(self._discover_datasets(path))

        return [ZfsDataset(name=n, referenced_size=s, config=self.config) for n, s in sorted(all_discovered.items())]

    @staticmethod
    def _discover_datasets(path: Optional[str] = None) -> dict[str, int]:
        """
        Query ZFS to list datasets and their current sizes.

        :param path: Optional base dataset path to scan recursively.
        :return: Dictionary mapping dataset names to their referenced size in bytes.
        :raises ZfsCommandError: If the 'zfs list' command fails.
        """
        datasets: dict[str, int] = {}
        cmd = ["zfs", "list", "-H", "-p", "-o", "name,referenced"]
        if path is not None:
            cmd = cmd + ["-r", path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            for line in result.stdout.strip().split('\n'):
                if line:
                    name, size = line.split('\t')
                    try:
                        datasets[name] = int(size)
                    except ValueError:
                        continue  # Skip datasets with unknown/missing referenced size (e.g. '-')
        except subprocess.CalledProcessError as e:
            stderr_detail = f": {e.stderr.strip()}" if e.stderr else ""
            raise ZfsCommandError(
                f"'zfs list' failed during dataset discovery (exit {e.returncode}){stderr_detail}"
            ) from e
        except Exception as e:
            print(f"Warning: Unexpected error during dataset discovery: {type(e).__name__}: {e}", file=sys.stderr)

        return datasets


def notify_uptime_kuma(up: bool, kuma_url: str, kuma_token: str, msg: str = "OK") -> None:
    """
    Send a push notification heartbeat or alert to Uptime Kuma.

    :param up: Whether the system status is healthy (UP).
    :param kuma_url: Base URL for the Uptime Kuma instance.
    :param kuma_token: Security token for the push API.
    :param msg: Optional summary message to include in the push.
    """
    if not kuma_url or not kuma_token:
        return

    if not kuma_url.startswith(("http://", "https://")):
        kuma_url = f"https://{kuma_url}"

    status = "up" if up else "down"
    encoded_msg = quote(msg)
    base_url = kuma_url.rstrip("/")
    url = f"{base_url}/api/push/{kuma_token}?status={status}&msg={encoded_msg}"

    try:
        if up:
            print("\n[+] Sending heartbeat (UP) to Uptime Kuma...")
        else:
            print("\n[!] Sending anomaly alert (DOWN) to Uptime Kuma...")

        with urlopen(url, timeout=10) as response:
            if not (200 <= response.getcode() < 300):
                print(f"    Warning: Kuma returned HTTP {response.getcode()}")
            else:
                print("    Notification sent successfully.")
    except Exception as e:
        print(f"    Error: Failed to notify Uptime Kuma: {e}", file=sys.stderr)

def main() -> None:
    """
    Main logic to coordinate ZFS monitoring checks.
    """
    parser = argparse.ArgumentParser(description="Monitor ZFS snapshots for unusual data growth.")
    parser.add_argument("-p", "--path", action="append", nargs="+", help="Base paths for datasets.")
    parser.add_argument("-l", "--lookback", type=int, default=5, help="Snapshots for average calculation (Default: 5)")
    parser.add_argument("-g", "--growth", type=float, default=0.0, help="Growth sensitivity factor (0.0 to disable)")
    parser.add_argument("-s", "--size", type=int, default=100, help="Min relevance size in MB (Default: 100)")
    parser.add_argument("-r", "--ratio", type=float, default=0.0, help="Max written/referenced ratio (0.0 to disable)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Display detail snapshot history")
    parser.add_argument("--kuma-url", help="Uptime Kuma base URL")
    parser.add_argument("--kuma-token", help="Uptime Kuma push token")
    args = parser.parse_args()

    if args.growth <= 0 and args.ratio <= 0:
        parser.error("At least one check mode must be enabled: --growth or --ratio.")

    if args.kuma_url and not args.kuma_token:
        parser.error("--kuma-token is required when --kuma-url is provided.")

    factory = DatasetFactory(
        lookback=args.lookback,
        verbosity=args.verbose,
        ratio=args.ratio,
        size_mb=args.size,
        growth=args.growth,
        path_list=args.path
    )
    try:
        datasets = factory.get_datasets()
    except ZfsCommandError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not datasets:
        print("No datasets found to process.")
        sys.exit(0)

    name_width: int = max([len(d.name) for d in datasets] + [40])

    cols = [f"{'DATASET':<{name_width}}", f"{'CURRENT':>10}"]
    if args.growth > 0:
        cols.append(f"{'AVG':>10}")
        cols.append(f"{'GROWTH':>8}")
    if args.ratio > 0:
        cols.append(f"{'RATIO':>7}")
    cols.append("STATUS")

    header = " | ".join(cols)
    print(header)
    print("-" * len(header))

    anomalies: list[str] = []

    for ds in datasets:
        if not ds.has_data:
            if args.verbose:
                print(ds.format_row(name_width))
            continue

        valid, reasons = ds.is_valid()
        if not valid:
            anomalies.append(f"{ds.name} ({'+'.join(reasons)})")
        print(ds.format_row(name_width))

    if anomalies:
        summary = "ZFS Anomaly Detected: " + ", ".join(anomalies)
    else:
        summary = "ZFS Snapshots OK"

    if args.kuma_url:
        notify_uptime_kuma(not bool(anomalies), args.kuma_url, args.kuma_token, summary)

    sys.exit(1 if anomalies else 0)

if __name__ == "__main__":
    main()