import subprocess
import sys
import argparse
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from urllib.request import urlopen
from urllib.parse import quote

def format_size(size_bytes: float) -> str:
    """
    Format byte values into human-readable units (KB, MB, GB).
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
    Configuration for dataset monitoring.
    """
    lookback: int
    verbosity: bool
    max_ratio: float
    min_relevance: int
    growth_factor: float

class ZfsDataset:
    """
    Represents a ZFS dataset and its health status.
    Snapshot data is fetched on construction so the object is immediately
    ready for evaluation.
    """
    def __init__(self, name: str, referenced_size: int, config: DatasetConfig):
        self.name = name
        self.referenced_size = referenced_size
        self.config = config
        self.name_width: int = 40
        self.current_written: Optional[int] = None
        self.avg_written: Optional[float] = None
        self.has_data: bool = self._fetch_snapshot_data()

    def _fetch_snapshot_data(self) -> bool:
        """
        Retrieves snapshot written-size history for this dataset.
        Returns True if successful and enough data was found, False otherwise.
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
                    history.append({"name": parts[0], "written": int(parts[1])})

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
            print(f"Error: 'zfs list' failed for dataset '{self.name}' (exit {e.returncode}).")
            if e.stderr:
                print(f"  stderr: {e.stderr.strip()}")
            sys.exit(1)
        except Exception as e:
            print(f"Warning: Unexpected error fetching snapshot data for '{self.name}': {type(e).__name__}: {e}")
            return False

    @property
    def growth_alert(self) -> bool:
        """
        True if the current snapshot write is a historical growth anomaly.
        Requires has_data to be True and growth_factor to be configured.
        """
        if not self.has_data or self.current_written is None or self.avg_written is None:
            return False
        return (
            self.config.growth_factor > 0
            and self.current_written > self.config.min_relevance
            and self.current_written > (self.avg_written * self.config.growth_factor)
        )

    @property
    def ratio(self) -> float:
        """
        The ratio of current snapshot write size to total referenced dataset size.
        """
        if self.current_written is None or self.referenced_size <= 0:
            return 0.0
        return self.current_written / self.referenced_size

    @property
    def ratio_alert(self) -> bool:
        """
        True if the written/referenced ratio exceeds the configured threshold.
        Requires has_data to be True and max_ratio to be configured.
        """
        if not self.has_data or self.current_written is None:
            return False
        return (
            self.config.max_ratio > 0
            and self.current_written > self.config.min_relevance
            and self.ratio > self.config.max_ratio
        )

    def is_valid(self) -> Tuple[bool, List[str]]:
        """
        Evaluates all configured checks and returns a tuple of
        (is_healthy, list_of_alert_reasons).
        """
        reasons: List[str] = []
        if self.config.growth_factor > 0 and self.growth_alert:
            reasons.append("GROWTH")
        if self.config.max_ratio > 0 and self.ratio_alert:
            reasons.append("RANSOMWARE")
        return (len(reasons) == 0, reasons)

    @property
    def growth_multiple(self) -> Optional[float]:
        """
        The actual growth multiple (current_written / avg_written).
        Returns None if no data or avg is zero.
        """
        if not self.has_data or not self.avg_written:
            return None
        return self.current_written / self.avg_written

    def __str__(self) -> str:
        """
        Returns a fully formatted table row for this dataset.
        """
        if not self.has_data:
            parts = [f"{self.name:<{self.name_width}}", f"{'N/A':>10}"]
            if self.config.growth_factor > 0:
                parts.append(f"{'N/A':>10}")
                parts.append(f"{'N/A':>8}")
            if self.config.max_ratio > 0:
                parts.append(f"{'N/A':>7}")
            parts.append("Skipped")
            return " | ".join(parts)

        valid, reasons = self.is_valid()
        row = [f"{self.name:<{self.name_width}}", format_size(self.current_written)]
        if self.config.growth_factor > 0:
            row.append(format_size(self.avg_written))
            gm = self.growth_multiple
            if gm is not None:
                flag = "!" if self.growth_alert else " "
                row.append(f"{gm:>6.1f}x{flag}")
            else:
                row.append(f"{'N/A':>8}")
        if self.config.max_ratio > 0:
            flag = "!" if self.ratio_alert else " "
            row.append(f"{self.ratio:>6.1%}{flag}")
        row.append("ALERT" if not valid else "OK")
        return " | ".join(row)

class DatasetFactory:
    """
    Factory class to discover and create ready-to-evaluate ZfsDataset objects.
    """
    def __init__(self, lookback: int, verbosity: bool, ratio: float, relevance_mb: int, growth: float, path_list: Optional[List[List[str]]] = None):
        self.config = DatasetConfig(
            lookback=lookback,
            verbosity=verbosity,
            max_ratio=ratio,
            min_relevance=relevance_mb * 1024 * 1024,
            growth_factor=growth
        )
        self.path_list = [p for sublist in path_list for p in sublist] if path_list else []

    def get_datasets(self) -> List[ZfsDataset]:
        """
        Discovers ZFS datasets and returns a list of fully prepared ZfsDataset objects.
        """
        all_discovered: Dict[str, int] = {}
        paths_to_query = self.path_list or [None]
        for path in paths_to_query:
            all_discovered.update(self._discover_datasets(path))

        return [ZfsDataset(name=n, referenced_size=s, config=self.config) for n, s in sorted(all_discovered.items())]

    @staticmethod
    def _discover_datasets(path: Optional[str] = None) -> Dict[str, int]:
        """
        Executes 'zfs list' and parses the output into a name -> size dictionary.
        """
        datasets: Dict[str, int] = {}
        cmd = ["zfs", "list", "-H", "-p", "-o", "name,referenced"]
        if path is not None:
            cmd = cmd + ["-r", path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            for line in result.stdout.strip().split('\n'):
                if line:
                    name, size = line.split('\t')
                    datasets[name] = int(size)
        except subprocess.CalledProcessError as e:
            print(f"Error: 'zfs list' failed while discovering datasets (exit {e.returncode}).")
            if e.stderr:
                print(f"  stderr: {e.stderr.strip()}")
            sys.exit(1)
        except Exception as e:
            print(f"Warning: Unexpected error during dataset discovery: {type(e).__name__}: {e}")

        return datasets


def notify_uptime_kuma(up: bool, kuma_url: str, kuma_token: str, msg: str = "OK") -> None:
    """
    Send a push notification to Uptime Kuma.
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
            print(f"\n[+] Sending heartbeat (UP) to Uptime Kuma...")
        else:
            print(f"\n[!] Sending anomaly alert (DOWN) to Uptime Kuma...")

        with urlopen(url, timeout=10) as response:
            if not (200 <= response.getcode() < 300):
                print(f"    Warning: Kuma returned HTTP {response.getcode()}")
            else:
                print("    Notification sent successfully.")
    except Exception as e:
        print(f"    Error: Failed to notify Uptime Kuma: {e}")

def main() -> None:
    """
    Main logic to coordinate ZFS monitoring checks.
    """
    parser = argparse.ArgumentParser(description="Monitor ZFS snapshots for unusual data growth.")
    parser.add_argument("-p", "--path", action="append", nargs="+", help="Base paths for datasets.")
    parser.add_argument("-l", "--lookback", type=int, default=5, help="Snapshots for average calculation (Default: 5)")
    parser.add_argument("-g", "--growth", type=float, default=0.0, help="Growth sensitivity factor (0.0 to disable)")
    parser.add_argument("-m", "--min-relevance", type=int, default=100, help="Min relevance in MB (Default: 100)")
    parser.add_argument("-r", "--max-ratio", type=float, default=0.0, help="Max written/referenced ratio (0.0 to disable)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Display detail snapshot history")
    parser.add_argument("--kuma-url", help="Uptime Kuma base URL")
    parser.add_argument("--kuma-token", help="Uptime Kuma push token")
    args = parser.parse_args()

    if args.growth <= 0 and args.max_ratio <= 0:
        parser.error("At least one check mode must be enabled: --growth or --max-ratio.")

    factory = DatasetFactory(
        lookback=args.lookback,
        verbosity=args.verbose,
        ratio=args.max_ratio,
        relevance_mb=args.min_relevance,
        growth=args.growth,
        path_list=args.path
    )
    datasets = factory.get_datasets()

    if not datasets:
        print("No datasets found to process.")
        sys.exit(0)

    name_width: int = max([len(d.name) for d in datasets] + [40])
    for ds in datasets:
        ds.name_width = name_width

    cols = [f"{'DATASET':<{name_width}}", f"{'CURRENT':>10}"]
    if args.growth > 0:
        cols.append(f"{'AVG':>10}")
        cols.append(f"{'GROWTH':>8}")
    if args.max_ratio > 0:
        cols.append(f"{'RATIO':>7}")
    cols.append("STATUS")

    header = " | ".join(cols)
    print(header)
    print("-" * len(header))

    anomalies: List[str] = []

    for ds in datasets:
        if not ds.has_data:
            if args.verbose:
                print(ds)
            continue

        valid, reasons = ds.is_valid()
        if not valid:
            anomalies.append(f"{ds.name} ({'+'.join(reasons)})")
        print(ds)

    if anomalies:
        summary = "ZFS Anomaly Detected: " + ", ".join(anomalies)
    else:
        summary = "ZFS Snapshots OK"

    if args.kuma_url:
        notify_uptime_kuma(not bool(anomalies), args.kuma_url, args.kuma_token, summary)

if __name__ == "__main__":
    main()