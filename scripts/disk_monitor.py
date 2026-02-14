#!/usr/bin/env python3
import subprocess
import datetime
import os

# Configuration
DRIVES = ['/dev/sda', '/dev/sdb', '/dev/sdc', '/dev/sdd']
LOG_FILE = '/var/log/disk_status.csv'

def get_disk_status(drive):
    try:
        # -n standby: tells smartctl not to run if the drive is in standby
        # -i: gets basic info (we just care about the exit code/output)
        cmd = ['sudo', 'smartctl', '-i', '-n', 'standby', drive]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # smartctl exit codes:
        # 0: Device is awake and responding -> Active
        # 2: Device was in a low-power mode -> Standby
        if result.returncode == 2:
            return "S"
        elif result.returncode == 0:
            return "A"
        else:
            return "?" # Unknown/Error
            
    except Exception:
        return "E" # Execution Error

def main():
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    statuses = []
    
    for drive in DRIVES:
        status = get_disk_status(drive)
        statuses.append(status)
    
    # CSV Format: Timestamp,Status1,Status2,...
    log_line = f"{timestamp},{','.join(statuses)}"
    
    # Check if file exists to write header
    file_exists = os.path.isfile(LOG_FILE)
    
    with open(LOG_FILE, 'a') as f:
        if not file_exists:
            # Create header from drive names, e.g., /dev/sda -> sda
            headers = ["Timestamp"] + [d.split('/')[-1] for d in DRIVES]
            f.write(",".join(headers) + "\n")
        f.write(log_line + "\n")

if __name__ == "__main__":
    main()
