# downloader.py
"""
FTP downloader utilities (updated).

- Automatic remote folder detection for patterns:
    /<base>/<YYYY>/<MM>/<DD>/
    /<base>/<YYYY>/<MM>/<DDMMYYYY>/
- Matches .txt files that start with station ID (both with/without timestamps)
- Saves to local folder: <local_base>/<State>/<StationID>/<YYYY>/<MM>/<DD>/
- Skips existing files (no overwrite)
- Uses pause_event / cancel_event for pause/cancel support
"""

import os
import time
import ftplib
from datetime import datetime, timedelta
import threading
from typing import List, Tuple, Callable, Optional

_cancel_global = False
def set_global_cancel(val: bool = True):
    global _cancel_global
    _cancel_global = val

def ftp_connect(host: str, user: str, passwd: str, port: int = 21,
                timeout: int = 30, retries: int = 3, delay: int = 2) -> ftplib.FTP:
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=timeout)
            ftp.login(user, passwd)
            ftp.set_pasv(True)
            return ftp
        except Exception as e:
            last_exc = e
            time.sleep(delay)
    raise ConnectionError(f"FTP connection failed after {retries} attempts: {last_exc}")

def list_files(ftp: ftplib.FTP, remote_dir: str) -> List[str]:
    """
    List files in remote_dir. Prefer MLSD, fallback to NLST.
    Returns list of filenames (str).
    Throws FileNotFoundError if remote_dir doesn't exist.
    """
    cur = None
    try:
        cur = ftp.pwd()
    except Exception:
        cur = None
    try:
        ftp.cwd(remote_dir)
    except Exception as e:
        raise FileNotFoundError(f"Remote directory not found: {remote_dir} ({e})")

    files = []
    # Try MLSD
    try:
        try:
            for name, facts in ftp.mlsd("."):
                t = facts.get("type", "")
                if t in ("file", ""):
                    files.append(name)
        except Exception:
            files = ftp.nlst()
            files = [f for f in files if f not in (".", "..")]
    finally:
        if cur:
            try:
                ftp.cwd(cur)
            except Exception:
                pass
    return files

def _safe_makedirs(path: str):
    # create path if not exists (race safe)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass

def download_file_with_progress(
        ftp: ftplib.FTP,
        remote_file: str,
        local_path: str,
        pause_event: threading.Event,
        cancel_event: threading.Event,
        progress_callback: Optional[Callable[[int, Optional[int], str], None]] = None,
        chunk_size: int = 8192
    ) -> bool:
    try:
        _safe_makedirs(os.path.dirname(local_path) or ".")
        total = None
        try:
            total = ftp.size(remote_file)
        except Exception:
            total = None

        received = 0
        with open(local_path, "wb") as f:
            def _callback(chunk):
                nonlocal received
                if cancel_event.is_set() or _cancel_global:
                    raise Exception("Cancelled")
                while pause_event.is_set():
                    time.sleep(0.2)
                    if cancel_event.is_set() or _cancel_global:
                        raise Exception("Cancelled")
                f.write(chunk)
                received += len(chunk)
                if progress_callback:
                    try:
                        progress_callback(received, total, os.path.basename(remote_file))
                    except Exception:
                        pass

            ftp.retrbinary(f"RETR {remote_file}", _callback, blocksize=chunk_size)
        return True
    except Exception:
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except Exception:
            pass
        return False

def build_possible_paths(base_path: str, date_obj: datetime) -> List[str]:
    """
    Build candidate remote paths for a given date.
    Returns list in preferred order (try first to last).
    """
    yyyy = date_obj.strftime("%Y")
    mm = date_obj.strftime("%m")
    dd = date_obj.strftime("%d")
    ddmmyyyy = date_obj.strftime("%d%m%Y")
    base = base_path.rstrip("/")
    # Two common variants
    p1 = f"{base}/{yyyy}/{mm}/{dd}/"
    p2 = f"{base}/{yyyy}/{mm}/{ddmmyyyy}/"
    # also try without trailing slash (some servers)
    alt = [p1, p2, p1.rstrip("/"), p2.rstrip("/")]
    # return unique preserving order
    seen = set()
    out = []
    for p in alt:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out

def find_existing_remote_path(ftp: ftplib.FTP, base_path: str, date_obj: datetime) -> Optional[str]:
    """
    Try candidate remote paths and return the first one that exists (cwd succeeds).
    """
    for candidate in build_possible_paths(base_path, date_obj):
        try:
            ftp.cwd(candidate)
            # ensure we are in that folder (some servers change)
            try:
                pwd = ftp.pwd()
            except Exception:
                pass
            return candidate
        except Exception:
            continue
    return None

def matches_station_file(filename: str, station_code: str) -> bool:
    # Must start with station code and end with .txt (case-insensitive)
    if not filename.lower().endswith(".txt"):
        return False
    return filename.startswith(station_code)

def _iter_range_datetimes(start_dt: datetime, end_dt: datetime, step_minutes: int = 15):
    cur = start_dt
    while cur <= end_dt:
        yield cur
        cur += timedelta(minutes=step_minutes)

def download_files_by_prefix(
        host: str, user: str, passwd: str,
        remote_dir_base: str,
        station_id: str,
        start_dt: datetime,
        end_dt: datetime,
        start_hour: int = 0, start_min: int = 0,
        end_hour: int = 23, end_min: int = 55,
        step_minutes: int = 15,
        local_base: str = "downloads",
        state: str = "",
        port: int = 21,
        pause_event: Optional[threading.Event] = None,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[int, Optional[int], str], None]] = None,
        test_mode: bool = False,
        retries: int = 3
    ) -> Tuple[List[str], List[str]]:
    """
    High-level downloader that:
    - For each datetime in range (by step_minutes) determines the appropriate remote folder
      automatically by trying multiple folder patterns.
    - In each found folder, lists files and downloads those that start with station_id and end with .txt.
    - Saves to local_base/state/station_id/YYYY/MM/DD/<file>
    - Skips download if local file already exists.
    """
    if pause_event is None:
        pause_event = threading.Event()
    if cancel_event is None:
        cancel_event = threading.Event()

    downloaded = []
    failed = []

    ftp = ftp_connect(host, user, passwd, port=port, retries=retries)

    # Build datetimes list by day/time window
    wanted_dt_list = []
    cur_day = start_dt.date()
    while cur_day <= end_dt.date():
        start_dt_day = datetime(cur_day.year, cur_day.month, cur_day.day, start_hour, start_min)
        end_dt_day = datetime(cur_day.year, cur_day.month, cur_day.day, end_hour, end_min)
        cur = start_dt_day
        while cur <= end_dt_day:
            wanted_dt_list.append(cur)
            cur += timedelta(minutes=step_minutes)
        cur_day = (datetime(cur_day.year, cur_day.month, cur_day.day) + timedelta(days=1)).date()

    if not wanted_dt_list:
        try:
            ftp.quit()
        except Exception:
            pass
        return downloaded, failed

    # For optimization, group by date (we want each date folder only once)
    dates_seen = set()

    for dt in wanted_dt_list:
        if cancel_event.is_set() or _cancel_global:
            break
        date_key = (dt.year, dt.month, dt.day)
        if date_key in dates_seen:
            continue
        dates_seen.add(date_key)

        # find which remote path exists for this date
        remote_path = find_existing_remote_path(ftp, remote_dir_base, dt)
        if not remote_path:
            # not found; continue
            continue

        # list files in that folder
        try:
            files = []
            try:
                files = ftp.nlst(remote_path)
            except Exception:
                # try cwd then nlst
                try:
                    ftp.cwd(remote_path)
                    files = ftp.nlst()
                except Exception:
                    files = []
        except Exception:
            files = []

        # iterate matching files
        for fname in files:
            if cancel_event.is_set() or _cancel_global:
                break
            # some NLST returns full path, normalize to basename
            base_fname = os.path.basename(fname)
            if not matches_station_file(base_fname, station_id):
                continue
            # build local path: local_base/State/StationID/YYYY/MM/DD/<filename>
            yyyy = dt.strftime("%Y")
            mm = dt.strftime("%m")
            dd = dt.strftime("%d")
            local_dir = os.path.join(local_base, (state or "").strip(), station_id, yyyy, mm, dd)
            _safe_makedirs(local_dir)
            local_path = os.path.join(local_dir, base_fname)
            # skip if exists
            if os.path.exists(local_path):
                downloaded.append(local_path)  # mark as already present
                continue
            # If test mode, create dummy file
            if test_mode:
                try:
                    with open(local_path, "w", encoding="utf-8") as fh:
                        fh.write(f"TEST-DUMMY for {base_fname}\n")
                    downloaded.append(local_path)
                    continue
                except Exception:
                    failed.append(f"{remote_path}/{base_fname}")
                    continue
            # perform download
            try:
                # ensure cwd to remote folder to use simple filename RETR
                try:
                    ftp.cwd(remote_path)
                    ok = download_file_with_progress(ftp, base_fname, local_path, pause_event, cancel_event, progress_callback)
                except Exception:
                    # fallback to retrieving using full path
                    ok = download_file_with_progress(ftp, f"{remote_path.rstrip('/')}/{base_fname}", local_path, pause_event, cancel_event, progress_callback)
                if ok:
                    downloaded.append(local_path)
                else:
                    failed.append(f"{remote_path}/{base_fname}")
            except Exception:
                failed.append(f"{remote_path}/{base_fname}")
                # remove partial maybe handled in download_file_with_progress

    try:
        ftp.quit()
    except Exception:
        pass

    return downloaded, failed

def download_single_by_path(host: str, user: str, passwd: str,
                            remote_file: str, local_file: str,
                            port: int = 21,
                            pause_event: Optional[threading.Event] = None,
                            cancel_event: Optional[threading.Event] = None,
                            progress_callback: Optional[Callable[[int, Optional[int], str], None]] = None,
                            retries: int = 3) -> bool:
    if pause_event is None:
        pause_event = threading.Event()
    if cancel_event is None:
        cancel_event = threading.Event()

    ftp = ftp_connect(host, user, passwd, port=port, retries=retries)
    try:
        ok = download_file_with_progress(ftp, remote_file, local_file, pause_event, cancel_event, progress_callback)
        try:
            ftp.quit()
        except Exception:
            pass
        return ok
    finally:
        pass