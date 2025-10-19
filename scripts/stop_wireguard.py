#!/usr/bin/env python3

import subprocess
import time
import argparse
import sys

def is_zfs_recv_running():
    """Return True if a zfs receive process is running."""
    try:
        output = subprocess.check_output(
            ['pgrep', '-f', 'zfs recv'],
            stderr=subprocess.DEVNULL
        )
        return bool(output.strip())
    except subprocess.CalledProcessError:
        # No process found
        return False

def interface_is_up(interface):
    """Check if the WireGuard interface is already active."""
    try:
        subprocess.check_output(['wg', 'show', interface], stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

def bring_down_interface(interface):
    """Bring down the WireGuard interface using wg-quick."""
    try:
        subprocess.run(['wg-quick', 'down', interface], check=True)
        print(f"✅ Interface {interface} brought down.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to bring down {interface}: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Monitor zfs recv process and shut down WireGuard if it stops.")
    parser.add_argument("interface", help="WireGuard interface name (e.g. wg0)")
    parser.add_argument("--timeout", type=int, default=60, help="Seconds to wait with no zfs recv before shutting down WG (default: 60)")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds (default: 5)")
    args = parser.parse_args()

    print(f"🛡️ Monitoring zfs recv process for activity...")
    print(f"WireGuard interface: {args.interface}")
    print(f"Timeout: {args.timeout}s | Interval: {args.interval}s")

    last_active = time.time()

    try:
        while True:
            time.sleep(args.interval)

            if is_zfs_recv_running():
                last_active = time.time()
                print(f"[{time.ctime()}] zfs recv is active.")
            else:
                idle_time = time.time() - last_active
                print(f"[{time.ctime()}] zfs recv not running for {int(idle_time)}s...")

                if idle_time >= args.timeout:
                    print(f"⚠️ zfs recv idle for {args.timeout}s. Shutting down {args.interface}...")
                    if interface_is_up(args.interface):
                        bring_down_interface(args.interface)
                    else:
                        print(f"ℹ️ Interface {args.interface} already down.")
                    break

    except KeyboardInterrupt:
        print("\n🛑 Interrupted. Exiting.")

if __name__ == "__main__":
    main()
