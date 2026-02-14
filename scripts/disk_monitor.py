#!/usr/bin/env python3
import subprocess
import datetime

# Configuration
DRIVES = ['/dev/sda', '/dev/sdb', '/dev/sdc', '/dev/sdd']
LOG_FILE = '/var/log/disk_status.log'

def get_disk_status(drive):
    try:
        # -n standby: tells smartctl not to run if the drive is in standby
        # -i: gets basic info (we just care about the exit code/output)
        cmd = ['sudo', 'smartctl', '-i', '-n', 'standby', drive]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # smartctl exit codes:
        # 0: Device is awake and responding
        # 2: Device was in a low-power mode (because of the -n flag)
        if result.returncode == 2:
            return "STANDBY (Spun Down)"
        elif result.returncode == 0:
            return "ACTIVE (Spinning)"
        else:
            return f"CHECK FAILED (Code {result.returncode})"
            
    except Exception as e:
        return f"ERROR: {str(e)}"

def main():
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_lines = [f"--- {timestamp} ---"]
    
    for drive in DRIVES:
        status = get_disk_status(drive)
        output_lines.append(f"{drive}: {status}")
    
    with open(LOG_FILE, 'a') as f:
        f.write("\n".join(output_lines) + "\n\n")

if __name__ == "__main__":
    main()
