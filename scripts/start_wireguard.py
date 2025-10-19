#!/usr/bin/env python3

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
    print(f"Checking if inteface '{interface}' exists")
    try:
        output = subprocess.check_output(['wg', 'show', interface], stderr=subprocess.DEVNULL)
        print(f"Interface '{interface}' is already up.")
        return True
    except subprocess.CalledProcessError:
        print(f"Interface '{interface}' does not exist.")
        return False

def bring_up_interface(interface: str):
    """Bring up the WireGuard interface using wg-quick."""
    try:
        subprocess.run(['wg-quick', 'up', interface], check=True)
        print(f"Interface '{interface}' brought up successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to bring up '{interface}': {e}")

def main():
    print("### Establish Wireguard Connection ###")
    parser = argparse.ArgumentParser(description="Bring up a WireGuard interface if it's not already up.")
    parser.add_argument("interface", help="Name of the WireGuard interface (e.g. wg0)")
    args = parser.parse_args()

    if not config_exists(args.interface):
        print(f"Error: Config file /etc/wireguard/{args.interface}.conf does not exist.")
        sys.exit(1)

    if not interface_exists(args.interface):
        bring_up_interface(args.interface)

    print("### DONE ###\n")
if __name__ == '__main__':
    main()
