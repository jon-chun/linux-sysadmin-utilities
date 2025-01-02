import os
import subprocess
import shutil
from typing import Dict, Optional, Tuple, List
from pathlib import Path
import logging

logging.basicConfig(
   level=logging.INFO,
   format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def find_large_directories(min_size_mb: int = 100) -> List[Tuple[str, float]]:
   """Scan /home for directories exceeding minimum size threshold"""
   large_dirs = []
   for root, dirs, _ in os.walk('/home'):
       for dir in dirs:
           path = os.path.join(root, dir)
           size_mb = calculate_dir_size(path) / 1024
           if size_mb > min_size_mb:
               large_dirs.append((path, size_mb))
   return sorted(large_dirs, key=lambda x: x[1], reverse=True)

def prompt_directory_move(dir_info: List[Tuple[str, float]], limit: int = 10) -> List[str]:
   """Present top directories and get user approval for each"""
   dirs_to_move = []
   
   for i, (path, size_mb) in enumerate(dir_info[:limit]):
       print(f"\n{i+1}/{min(limit, len(dir_info))}")
       print(f"Directory: {path}")
       print(f"Size: {size_mb:.2f} MB")
       
       while True:
           response = input("Move this directory? [y/n]: ").lower()
           if response in ['y', 'n']:
               break
           print("Please enter 'y' or 'n'")
           
       if response == 'y':
           dirs_to_move.append(path)
           print(f"Marked for moving: {path}")
       else:
           print(f"Skipping: {path}")
           
   return dirs_to_move

def check_disk_usage(path: str) -> Optional[Dict[str, int]]:
   """Check available space on filesystem containing path"""
   try:
       usage = shutil.disk_usage(path)
       return {
           'total': usage.total // 1024,
           'used': usage.used // 1024, 
           'available': usage.free // 1024
       }
   except OSError as e:
       logger.error(f"Failed to check disk usage: {e}")
       return None

def calculate_dir_size(directory: str) -> Optional[int]:
   """Calculate total size of directory in KB"""
   try:
       return sum(
           f.stat().st_size for f in Path(directory).rglob('*') if f.is_file()
       ) // 1024
   except OSError as e:
       logger.error(f"Failed to calculate directory size: {e}")
       return None

def verify_mount_point(mount_point: str) -> bool:
   """Create and verify mount point exists"""
   try:
       Path(mount_point).mkdir(parents=True, exist_ok=True)
       return True
   except OSError as e:
       logger.error(f"Failed to create mount point: {e}")
       return False

def mount_drive(drive: str, mount_point: str) -> bool:
   """Mount specified drive at mount point"""
   if not verify_mount_point(mount_point):
       return False
   try:
       subprocess.run(['sudo', 'mount', drive, mount_point],
                     capture_output=True, text=True, check=True)
       return True
   except subprocess.CalledProcessError as e:
       logger.error(f"Mount error: {e.stderr}")
       return False

def transfer_directory(source: str, destination: str) -> bool:
   """Transfer directory using rsync with verification"""
   try:
       # First rsync with dry-run to verify
       subprocess.run(['sudo', 'rsync', '-avP', '--dry-run', source, destination],
                     check=True)
       # Actual transfer if dry-run succeeds
       subprocess.run(['sudo', 'rsync', '-avP', '--delete', source, destination],
                     check=True)
       return True
   except subprocess.CalledProcessError as e:
       logger.error(f"Transfer error: {e}")
       return False

def create_symlink(source: str, link_name: str) -> bool:
   """Replace original directory with symlink to new location"""
   try:
       link_path = Path(link_name)
       if link_path.is_symlink():
           link_path.unlink()
       elif link_path.exists():
           subprocess.run(['sudo', 'rm', '-rf', str(link_path)], check=True)
       link_path.symlink_to(source)
       return True
   except (OSError, subprocess.CalledProcessError) as e:
       logger.error(f"Symlink error: {e}")
       return False

def verify_transfer(source: str, destination: str) -> bool:
   """Verify successful transfer by comparing directory contents"""
   try:
       result = subprocess.run(['sudo', 'diff', '-r', source, destination],
                             capture_output=True)
       return result.returncode == 0
   except subprocess.CalledProcessError as e:
       logger.error(f"Verification failed: {e}")
       return False

def update_fstab(drive: str, mount_point: str) -> bool:
   """Add mount configuration to /etc/fstab if not present"""
   try:
       fstab_path = Path('/etc/fstab')
       fstab_entry = f"{drive} {mount_point} ext4 defaults 0 2\n"
       
       content = fstab_path.read_text()
       if fstab_entry not in content:
           subprocess.run(['sudo', 'tee', '-a', str(fstab_path)],
                        input=fstab_entry, text=True, check=True)
       return True
   except Exception as e:
       logger.error(f"Failed to update fstab: {e}")
       return False

def main():
   # Configuration
   drive = '/dev/nvme0n1p1'  # Target drive (13% used)
   mount_point = '/mnt/temp'
   min_dir_size_mb = 1000  # Only move directories > 1GB
   
   # Find and rank large directories
   logger.info("Scanning for large directories...")
   large_dirs = find_large_directories(min_dir_size_mb)
   if not large_dirs:
       logger.info("No large directories found")
       return
   
   # Get user selection
   logger.info("\nSelect directories to move:")
   selected_dirs = prompt_directory_move(large_dirs)
   if not selected_dirs:
       logger.info("No directories selected")
       return

   # Verify space requirements
   source_space = check_disk_usage('/')
   if not source_space:
       return

   total_required = sum(calculate_dir_size(d) for d in selected_dirs)
   if total_required > source_space['available']:
       logger.error(f"Insufficient space: Need {total_required//1024}MB")
       return

   # Mount and transfer
   if not mount_drive(drive, mount_point):
       return

   for source_dir in selected_dirs:
       destination = str(Path(mount_point) / Path(source_dir).name)
       logger.info(f"\nProcessing: {source_dir}")
       
       if transfer_directory(source_dir, destination):
           if verify_transfer(source_dir, destination):
               if create_symlink(destination, source_dir):
                   logger.info(f"Successfully moved: {source_dir}")
                   continue
       
       logger.error(f"Failed to process: {source_dir}")

   if update_fstab(drive, mount_point):
       logger.info("\nUpdated fstab successfully")
       logger.info("Disk space rebalancing complete")

if __name__ == '__main__':
   main()