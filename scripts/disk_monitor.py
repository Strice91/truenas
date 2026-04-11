#!/usr/bin/env python3
import subprocess
import datetime
import os
from typing import List, Literal

# Configuration
DRIVES: List[str] = ['/dev/sda', '/dev/sdb', '/dev/sdc', '/dev/sdd']
LOG_FILE: str = '/var/log/disk_status.csv'

def get_disk_status(drive: str) -> str:
    """
    Check the current status of a disk using smartctl.

    - Returns "1" if the drive is active/awake.
    - Returns "0" if the drive is in standby (low-power mode).
    - Returns "?" if the status is unknown or an error occurred.
    - Returns "E" on execution errors.

    :param drive: The device path (e.g., '/dev/sda').
    :return: A status string ("1", "0", "?", or "E").
    """
    try:
        # -n standby: tells smartctl not to run if the drive is in standby
        # -i: gets basic info
        cmd = ['sudo', 'smartctl', '-i', '-n', 'standby', drive]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # smartctl exit codes:
        # 0: Device is awake and responding -> Active (1)
        # 2: Device was in a low-power mode -> Standby (0)
        if result.returncode == 2:
            return "0"
        elif result.returncode == 0:
            return "1"
        else:
            return "?" # Unknown/Error
            
    except Exception:
        return "E" # Execution Error

def main() -> None:
    """
    Main function to poll disk statuses and log them to a CSV file.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    statuses: List[str] = []
    
    for drive in DRIVES:
        status = get_disk_status(drive)
        statuses.append(status)
    
    # CSV Format: Timestamp,Status1,Status2,...
    log_line = f"{timestamp},{','.join(statuses)}"
    
    # Check if file exists to determine if we need a header
    file_exists = os.path.isfile(LOG_FILE)
    
    try:
        with open(LOG_FILE, 'a') as f:
            if not file_exists:
                # Create header from drive names, e.g., /dev/sda -> sda
                headers = ["Timestamp"] + [d.split('/')[-1] for d in DRIVES]
                f.write(",".join(headers) + "\n")
            f.write(log_line + "\n")
    except PermissionError:
        print(f"Error: No permission to write to {LOG_FILE}. Try running with sudo.")
    except Exception as e:
        print(f"Error logging disk status: {e}")

if __name__ == "__main__":
    main()

