import os
import shutil
import stat
import sys

def on_rm_error(func, path, exc_info):
    """
    Error handler for shutil.rmtree.
    Clears the read‑only attribute and retries the deletion.
    """
    # Clear the read-only attribute (Windows) or write permissions (Unix)
    os.chmod(path, stat.S_IWRITE)
    # Retry the original function (func) on the same path
    func(path)

def delete_folders_from_file(filename, dry_run=True, use_trash=False):
    """
    Deletes folders listed in a text file.

    Args:
        filename (str): Path to the .txt file.
        dry_run (bool): If True, only prints what would be deleted.
        use_trash (bool): If True, moves folders to recycle bin instead
                          of permanent deletion (requires send2trash).
    """
    if use_trash:
        try:
            from send2trash import send2trash
        except ImportError:
            print("send2trash not installed. Install it with: pip install send2trash")
            print("Falling back to permanent deletion (shutil.rmtree).")
            use_trash = False

    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        parts = line.split('|')
        if len(parts) < 3:
            print(f"Skipping malformed line: {line}")
            continue

        folder_path = parts[2].strip()
        folder_path = os.path.normpath(folder_path)   # resolves 'MAGNUS~1'

        if not os.path.exists(folder_path):
            print(f"Path does not exist: {folder_path}")
            continue

        if not os.path.isdir(folder_path):
            print(f"Not a directory: {folder_path}")
            continue

        if dry_run:
            print(f"[DRY RUN] Would delete: {folder_path}")
        else:
            try:
                if use_trash:
                    # send2trash may also need permissions; we can pre‑clear attributes
                    # but it handles read‑only files better on Windows.
                    send2trash(folder_path)
                    print(f"Moved to trash: {folder_path}")
                else:
                    # Use the custom error handler to handle read‑only files
                    shutil.rmtree(folder_path, onerror=on_rm_error)
                    print(f"Deleted permanently: {folder_path}")
            except Exception as e:
                print(f"Error deleting {folder_path}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python delete_temp_folders.py <filename> [--delete] [--trash]")
        print("  --delete   : Actually delete (without this, it's a dry run).")
        print("  --trash    : Move to recycle bin instead of permanent deletion.")
        sys.exit(1)

    filename = sys.argv[1]
    dry_run = True
    use_trash = False

    for arg in sys.argv[2:]:
        if arg == "--delete":
            dry_run = False
        elif arg == "--trash":
            use_trash = True

    delete_folders_from_file(filename, dry_run, use_trash)