# main.py
"""
FTP RTU Downloader — Per-server sub-tabs, preview remote dir, auto-midnight scheduler,
and Save All Settings in Main tab.

Features added:
- One sub-tab per server inside Main (auto-generated / reloadable)
- Per-server independent settings persisted in settings.json
- Preview Remote Dir per server
- Global "Enable Auto Midnight Download (00:10)" checkbox
- Scheduler (uses `schedule` if available); runs downloads for all servers simultaneously
- Save All Settings button in Main tab to persist UI fields back to settings.json
- Settings tab supports editing host/port/user/pass/remote for each server
"""

import os
import json
import threading
import time
import traceback
from datetime import datetime, date, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

# Optional libs
try:
    from tkcalendar import DateEntry
    CALENDAR_AVAILABLE = True
except Exception:
    CALENDAR_AVAILABLE = False

try:
    import schedule
    SCHEDULE_AVAILABLE = True
except Exception:
    schedule = None
    SCHEDULE_AVAILABLE = False

from downloader import download_files_by_prefix, ftp_connect, download_single_by_path

SETTINGS_FILE = "settings.json"
HISTORY_FILE = "download_history.log"
PAD = {"padx": 6, "pady": 6}


def append_history(line: str):
    os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {line}\n")


class ServerController:
    """Manages a worker thread for a single server (pause/cancel/resume)."""
    def __init__(self, server_index, server_cfg, ui_update_fn):
        self.server_index = server_index
        self.cfg = server_cfg
        self.thread = None
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.running = False
        self.ui_update_fn = ui_update_fn
        self.last_result = None

    def start_download(self, stations, params):
        if self.running:
            return False
        self.pause_event.clear()
        self.cancel_event.clear()
        self.thread = threading.Thread(
            target=self._worker, args=(stations, params), daemon=True)
        self.thread.start()
        return True

    def _worker(self, stations, params):
        self.running = True
        host = self.cfg.get("host")
        port = int(self.cfg.get("port", 21))
        user = self.cfg.get("user", "")
        pwd = self.cfg.get("pass", "")
        remote_base = self.cfg.get("remote", "/")

        append_history(f"Server {host}:{port} - start downloads")
        try:
            total_downloaded = 0
            total_failed = 0

            # If single timestamp provided, let downloader handle by setting start_dt=end_dt
            for station in stations:
                if self.cancel_event.is_set():
                    break

                single_ts = params.get("single_ts", "") or ""
                if single_ts:
                    # convert YYMMDDHHMM to datetime
                    try:
                        yy = int(single_ts[0:2])
                        year = 2000 + yy if yy < 90 else 1900 + yy
                        m = int(single_ts[2:4])
                        d = int(single_ts[4:6])
                        H = int(single_ts[6:8])
                        M = int(single_ts[8:10])
                        single_dt = datetime(year, m, d, H, M)
                        start_dt = single_dt
                        end_dt = single_dt
                    except Exception as e:
                        self.ui_update_fn(self.server_index, f"Invalid single timestamp: {e}", None, None)
                        append_history(f"{host}: invalid single_ts {single_ts} -> {e}")
                        continue
                else:
                    start_dt = params["start_dt"]
                    end_dt = params["end_dt"]

                def cb(received, total, filename):
                    self.ui_update_fn(self.server_index, f"{filename} {received}/{total or '?'}", None, None)

                downloaded, failed = download_files_by_prefix(
                    host, user, pwd, remote_base, station,
                    start_dt, end_dt,
                    start_hour=params.get("start_hour", 0),
                    start_min=params.get("start_min", 0),
                    end_hour=params.get("end_hour", 23),
                    end_min=params.get("end_min", 55),
                    step_minutes=params.get("step_minutes", 15),
                    local_base=params.get("local_folder"),
                    state=params.get("state", ""),
                    port=port,
                    pause_event=self.pause_event,
                    cancel_event=self.cancel_event,
                    progress_callback=cb,
                    test_mode=params.get("test_mode", False)
                )

                total_downloaded += len(downloaded)
                total_failed += len(failed)
                self.ui_update_fn(self.server_index, f"Station {station}: {len(downloaded)} ok, {len(failed)} failed", None, None)
                append_history(f"{host}: station {station} -> {len(downloaded)} ok, {len(failed)} failed")

            self.ui_update_fn(self.server_index, f"Completed: {total_downloaded} ok, {total_failed} failed", None, None)
            append_history(f"{host}: completed: {total_downloaded} ok, {total_failed} failed")
            self.last_result = (total_downloaded, total_failed)
        except Exception as e:
            self.ui_update_fn(self.server_index, f"Error: {e}", None, None)
            append_history(f"{host}: error: {e}\n{traceback.format_exc()}")
        finally:
            self.running = False

    def pause(self):
        self.pause_event.set()
        append_history(f"Paused {self.cfg.get('host')}")

    def resume(self):
        self.pause_event.clear()
        append_history(f"Resumed {self.cfg.get('host')}")

    def cancel(self):
        self.cancel_event.set()
        append_history(f"Cancelled {self.cfg.get('host')}")


class FTPDownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FTP RTU Downloader")
        self.root.geometry("1000x780")

        self.servers = []
        self.server_tabs = {}   # idx -> ui dict
        self.controllers = {}   # idx -> ServerController

        self.scheduler_thread = None
        self.scheduler_stop_event = threading.Event()
        self.auto_midnight_enabled = False

        self._load_settings()
        self._build_ui()
        self._apply_settings_to_ui()

        # start scheduler if enabled and schedule available
        if self.auto_midnight_enabled and SCHEDULE_AVAILABLE:
            self.start_scheduler()

    # -------------------------
    # Settings load/save
    # -------------------------
    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            self.servers = []
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
                self.servers = cfg.get("servers", [])
                self.auto_midnight_enabled = cfg.get("auto_midnight", False)
        except Exception:
            self.servers = []
            self.auto_midnight_enabled = False

    def _save_settings(self):
        cfg = {
            "servers": self.servers,
            "auto_midnight": self.auto_midnight_enabled
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        append_history("Settings saved.")
        messagebox.showinfo("Settings", "Settings saved.")

    # -------------------------
    # UI build
    # -------------------------
    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        # Settings tab
        self.settings_frame = ttk.Frame(nb)
        nb.add(self.settings_frame, text="Settings")
        self._build_settings_tab(self.settings_frame)

        # Main tab container (contains inner notebook + global Main controls)
        self.main_frame_container = ttk.Frame(nb)
        nb.add(self.main_frame_container, text="Main")
        # Buttons & controls above the per-server sub-tabs
        top_controls = ttk.Frame(self.main_frame_container)
        top_controls.pack(fill="x", padx=6, pady=(6, 2))

        self.auto_var = tk.IntVar(value=1 if self.auto_midnight_enabled else 0)
        ttk.Checkbutton(top_controls, text="Enable Auto Midnight Download (00:10)", variable=self.auto_var,
                        command=self._on_toggle_auto_midnight).pack(side="left", padx=6)

        ttk.Button(top_controls, text="🔄 Reload Servers", command=self._create_server_tabs).pack(side="left", padx=6)
        ttk.Button(top_controls, text="💾 Save All Settings", command=self._save_all_from_ui).pack(side="left", padx=6)

        # inner notebook with one sub-tab per server
        self.main_tab = ttk.Notebook(self.main_frame_container)
        self.main_tab.pack(fill="both", expand=True, padx=6, pady=6)

        # History tab
        self.history_frame = ttk.Frame(nb)
        nb.add(self.history_frame, text="History")
        self._build_history_tab(self.history_frame)

    def _build_settings_tab(self, frame):
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="FTP Servers").grid(row=0, column=0, sticky="w", **PAD)
        self.server_listbox = tk.Listbox(frame, height=8)
        self.server_listbox.grid(row=1, column=0, rowspan=6, sticky="ns", **PAD)
        self.server_listbox.bind("<<ListboxSelect>>", self._on_server_select_settings)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=1, column=1, sticky="ne", **PAD)
        ttk.Button(btn_frame, text="Add", command=self._settings_add_server).grid(row=0, column=0, padx=4, pady=2)
        ttk.Button(btn_frame, text="Remove", command=self._settings_remove_server).grid(row=0, column=1, padx=4, pady=2)
        ttk.Button(btn_frame, text="Save Settings", command=self._save_settings_from_settings_tab).grid(row=0, column=2, padx=4, pady=2)

        # details area
        ttk.Label(frame, text="Host:").grid(row=2, column=1, sticky="w")
        self.s_host = tk.Entry(frame, width=40); self.s_host.grid(row=2, column=2, sticky="ew", **PAD)
        ttk.Label(frame, text="Port:").grid(row=3, column=1, sticky="w")
        self.s_port = tk.Entry(frame, width=8); self.s_port.grid(row=3, column=2, sticky="w", **PAD)
        ttk.Label(frame, text="User:").grid(row=4, column=1, sticky="w")
        self.s_user = tk.Entry(frame, width=30); self.s_user.grid(row=4, column=2, sticky="w", **PAD)
        ttk.Label(frame, text="Pass:").grid(row=5, column=1, sticky="w")
        self.s_pass = tk.Entry(frame, width=30, show="*"); self.s_pass.grid(row=5, column=2, sticky="w", **PAD)
        ttk.Label(frame, text="Remote base dir:").grid(row=6, column=1, sticky="w")
        self.s_remote = tk.Entry(frame, width=48); self.s_remote.grid(row=6, column=2, sticky="ew", **PAD)

        ttk.Button(frame, text="Test Connect", command=self._test_server_connect).grid(row=7, column=2, sticky="w", **PAD)
        ttk.Button(frame, text="Preview Remote Dir (Selected)", command=self._preview_remote_for_selected).grid(row=7, column=2, sticky="e", **PAD)

    def _build_history_tab(self, frame):
        self.history_text = tk.Text(frame, height=20, wrap="none")
        self.history_text.pack(fill="both", expand=True)
        btnf = ttk.Frame(frame)
        btnf.pack(fill="x", pady=6)
        ttk.Button(btnf, text="Reload History", command=self._load_history).pack(side="left", padx=6)
        ttk.Button(btnf, text="Clear History", command=self._clear_history).pack(side="left", padx=6)

    def _apply_settings_to_ui(self):
        self._refresh_server_listbox()
        self._create_server_tabs()
        self._load_history()

    # -------------------------
    # Settings tab handlers
    # -------------------------
    def _refresh_server_listbox(self):
        self.server_listbox.delete(0, 'end')
        for s in self.servers:
            label = f"{s.get('host')}:{s.get('port', 21)}"
            self.server_listbox.insert('end', label)

    def _settings_add_server(self):
        host = simpledialog.askstring("Add Server", "Enter host:")
        if not host:
            return
        entry = {
            "host": host,
            "port": 21,
            "user": "",
            "pass": "",
            "remote": "/",
            "stations": [],
            "state": "",
            "local_folder": os.path.join(os.getcwd(), "downloads")
        }
        self.servers.append(entry)
        self._refresh_server_listbox()
        self._create_server_tabs()
        self._save_settings()

    def _settings_remove_server(self):
        sel = self.server_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        host = self.servers[idx].get("host")
        del self.servers[idx]
        append_history(f"Removed server {host}")
        self._refresh_server_listbox()
        self._create_server_tabs()
        self._save_settings()

    def _on_server_select_settings(self, event=None):
        sel = self.server_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        s = self.servers[idx]
        self.s_host.delete(0, 'end'); self.s_host.insert(0, s.get("host", ""))
        self.s_port.delete(0, 'end'); self.s_port.insert(0, str(s.get("port", 21)))
        self.s_user.delete(0, 'end'); self.s_user.insert(0, s.get("user", ""))
        self.s_pass.delete(0, 'end'); self.s_pass.insert(0, s.get("pass", ""))
        self.s_remote.delete(0, 'end'); self.s_remote.insert(0, s.get("remote", "/"))

    def _save_settings_from_settings_tab(self):
        sel = self.server_listbox.curselection()
        if not sel:
            messagebox.showerror("Error", "Select a server to save edits")
            return
        idx = sel[0]
        try:
            port = int(self.s_port.get().strip() or "21")
        except Exception:
            port = 21
        self.servers[idx]["host"] = self.s_host.get().strip()
        self.servers[idx]["port"] = port
        self.servers[idx]["user"] = self.s_user.get().strip()
        self.servers[idx]["pass"] = self.s_pass.get().strip()
        self.servers[idx]["remote"] = self.s_remote.get().strip() or "/"
        self._refresh_server_listbox()
        self._create_server_tabs()
        self._save_settings()

    def _test_server_connect(self):
        sel = self.server_listbox.curselection()
        if not sel:
            messagebox.showerror("Error", "Select a server in Settings to test")
            return
        idx = sel[0]
        s = self.servers[idx]
        host = s.get("host")
        port = int(s.get("port", 21))
        user = s.get("user", "")
        pwd = s.get("pass", "")
        try:
            ftp = ftp_connect(host, user, pwd, port=port, retries=2)
            ftp.quit()
            messagebox.showinfo("OK", f"Connected to {host}:{port} successfully")
            append_history(f"Connected to {host}:{port}")
        except Exception as e:
            messagebox.showerror("Failed", f"Connect failed: {e}")
            append_history(f"Connect failed to {host}:{port} -> {e}")

    def _preview_remote_for_selected(self):
        sel = self.server_listbox.curselection()
        if not sel:
            messagebox.showerror("Error", "Select a server in Settings to preview")
            return
        idx = sel[0]
        s = self.servers[idx]
        self._preview_remote_dir_for_server(s)

    # -------------------------
    # Per-server Main UI creation
    # -------------------------
    def _create_server_tabs(self):
        # clear existing tabs
        for child in self.main_tab.winfo_children():
            child.destroy()
        self.server_tabs.clear()
        self.controllers.clear()

        for idx, s in enumerate(self.servers):
            tab = ttk.Frame(self.main_tab)
            title = f"{s.get('host')}:{s.get('port', 21)}"
            self.main_tab.add(tab, text=title)
            self.server_tabs[idx] = self._build_server_ui(tab, idx, s)

    def _build_server_ui(self, parent, idx, server_cfg):
        ui = {}
        # header
        ttk.Label(parent, text=f"Server: {server_cfg.get('host')}").grid(row=0, column=0, sticky="w", **PAD)

        # State
        ttk.Label(parent, text="State:").grid(row=1, column=0, sticky="w", **PAD)
        ui["state_var"] = tk.StringVar(value=server_cfg.get("state", ""))
        tk.Entry(parent, textvariable=ui["state_var"], width=30).grid(row=1, column=1, sticky="w", **PAD)

        # Local folder
        ttk.Label(parent, text="Local Folder:").grid(row=2, column=0, sticky="w", **PAD)
        ui["folder_var"] = tk.StringVar(value=server_cfg.get("local_folder", os.path.join(os.getcwd(), "downloads")))
        tk.Entry(parent, textvariable=ui["folder_var"], width=60).grid(row=2, column=1, sticky="w", **PAD)
        ttk.Button(parent, text="Browse", command=lambda u=ui: self._browse_folder(u)).grid(row=2, column=2, **PAD)

        # Stations
        station_frame = ttk.LabelFrame(parent, text="Stations")
        station_frame.grid(row=3, column=0, columnspan=3, sticky="ew", **PAD)
        ui["station_list"] = tk.Listbox(station_frame, height=6)
        ui["station_list"].pack(side="left", fill="y", padx=4, pady=4)
        for st in server_cfg.get("stations", []):
            ui["station_list"].insert("end", st)

        st_entry = tk.Entry(station_frame, width=20)
        st_entry.pack(side="left", padx=4)
        ttk.Button(station_frame, text="Add", command=lambda u=ui, e=st_entry, i=idx: self._add_station(u, e, i)).pack(side="left", padx=4)
        ttk.Button(station_frame, text="Remove", command=lambda u=ui, i=idx: self._remove_station(u, i)).pack(side="left", padx=4)

        # Date selection
        row = 4
        ttk.Label(parent, text="Start Date:").grid(row=row, column=0, sticky="w", **PAD)
        ui["start_date"] = DateEntry(parent, width=12) if CALENDAR_AVAILABLE else tk.Entry(parent, width=12)
        ui["start_date"].grid(row=row, column=1, sticky="w", **PAD)
        row += 1
        ttk.Label(parent, text="End Date:").grid(row=row, column=0, sticky="w", **PAD)
        ui["end_date"] = DateEntry(parent, width=12) if CALENDAR_AVAILABLE else tk.Entry(parent, width=12)
        ui["end_date"].grid(row=row, column=1, sticky="w", **PAD)

        # Single timestamp
        row += 1
        ttk.Label(parent, text="Single file (YYMMDDHHMM):").grid(row=row, column=0, sticky="w", **PAD)
        ui["single_ts"] = tk.Entry(parent, width=20)
        ui["single_ts"].grid(row=row, column=1, sticky="w", **PAD)

        # Controls: Download / Pause / Resume / Cancel / Preview Remote Dir
        row += 1
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(btn_frame, text="Download", command=lambda i=idx: self._start_download(i)).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Pause", command=lambda i=idx: self._pause_server(i)).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Resume", command=lambda i=idx: self._resume_server(i)).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=lambda i=idx: self._cancel_server(i)).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Preview Remote Dir", command=lambda s=server_cfg: self._preview_remote_dir_for_server(s)).pack(side="left", padx=8)

        # Status label
        ui["status_lbl"] = ttk.Label(parent, text="Idle")
        ui["status_lbl"].grid(row=row + 1, column=0, columnspan=3, sticky="w", **PAD)

        # create controller for this server
        self.controllers[idx] = ServerController(idx, server_cfg, self._server_ui_update)
        return ui

    # -------------------------
    # UI helpers (folders / stations)
    # -------------------------
    def _browse_folder(self, ui):
        d = filedialog.askdirectory()
        if d:
            ui["folder_var"].set(d)

    def _add_station(self, ui, entry, idx):
        val = entry.get().strip()
        if not val:
            return
        ui["station_list"].insert("end", val)
        self.servers[idx].setdefault("stations", []).append(val)
        self._save_settings()  # persist immediately

    def _remove_station(self, ui, idx):
        sel = ui["station_list"].curselection()
        if not sel:
            return
        pos = sel[0]
        val = ui["station_list"].get(pos)
        ui["station_list"].delete(pos)
        if val in self.servers[idx].get("stations", []):
            self.servers[idx]["stations"].remove(val)
        self._save_settings()

    # -------------------------
    # Download control logic
    # -------------------------
    def _build_params_for_server(self, idx):
        if idx not in self.server_tabs:
            return None, None
        ui = self.server_tabs[idx]
        state = ui["state_var"].get().strip()
        folder = ui["folder_var"].get().strip() or os.getcwd()
        stations = [ui["station_list"].get(i) for i in range(ui["station_list"].size())]
        if not stations:
            messagebox.showerror("Error", "Add at least one station")
            return None, None

        try:
            if CALENDAR_AVAILABLE:
                sd = ui["start_date"].get_date()
                ed = ui["end_date"].get_date()
                start_dt = datetime(sd.year, sd.month, sd.day)
                end_dt = datetime(ed.year, ed.month, ed.day)
            else:
                start_dt = datetime.strptime(ui["start_date"].get().strip(), "%Y-%m-%d")
                end_dt = datetime.strptime(ui["end_date"].get().strip(), "%Y-%m-%d")
        except Exception as e:
            messagebox.showerror("Error", f"Invalid date: {e}")
            return None, None

        params = {
            "start_dt": start_dt,
            "end_dt": end_dt,
            "start_hour": 0,
            "start_min": 0,
            "end_hour": 23,
            "end_min": 55,
            "step_minutes": 15,
            "local_folder": folder,
            "state": state,
            "single_ts": ui["single_ts"].get().strip(),
            "test_mode": False
        }
        # update server config and persist
        self.servers[idx]["state"] = state
        self.servers[idx]["local_folder"] = folder
        self.servers[idx]["stations"] = stations
        self._save_settings()
        return params, stations

    def _start_download(self, idx):
        params, stations = self._build_params_for_server(idx)
        if not params:
            return
        ctrl = self.controllers.get(idx)
        if not ctrl:
            ctrl = ServerController(idx, self.servers[idx], self._server_ui_update)
            self.controllers[idx] = ctrl
        ctrl.cfg = self.servers[idx]  # ensure cfg up-to-date
        started = ctrl.start_download(stations, params)
        if started:
            self.server_tabs[idx]["status_lbl"].config(text="Downloading...")
            append_history(f"Started download for server {self.servers[idx].get('host')}")
        else:
            messagebox.showinfo("Info", "Download already running for this server")

    def _pause_server(self, idx):
        ctrl = self.controllers.get(idx)
        if ctrl:
            ctrl.pause()
            self.server_tabs[idx]["status_lbl"].config(text="Paused")

    def _resume_server(self, idx):
        ctrl = self.controllers.get(idx)
        if ctrl:
            ctrl.resume()
            self.server_tabs[idx]["status_lbl"].config(text="Resuming")

    def _cancel_server(self, idx):
        ctrl = self.controllers.get(idx)
        if ctrl:
            ctrl.cancel()
            self.server_tabs[idx]["status_lbl"].config(text="Cancelled")

    def _server_ui_update(self, idx, text, *_):
        ui = self.server_tabs.get(idx)
        if ui:
            ui["status_lbl"].config(text=text)

    # -------------------------
    # Preview remote directory
    # -------------------------
    def _preview_remote_dir_for_server(self, server_cfg):
        host = server_cfg.get("host")
        if not host:
            messagebox.showerror("Error", "Server host not configured")
            return
        port = int(server_cfg.get("port", 21))
        user = server_cfg.get("user", "")
        pwd = server_cfg.get("pass", "")
        remote = server_cfg.get("remote", "/")

        try:
            ftp = ftp_connect(host, user, pwd, port=port, retries=2)
            files = []
            try:
                files = ftp.nlst(remote)
            except Exception:
                # try cwd then nlst
                try:
                    ftp.cwd(remote)
                    files = ftp.nlst()
                except Exception:
                    # fallback to root listing
                    try:
                        files = ftp.nlst()
                    except Exception:
                        files = []
            try:
                ftp.quit()
            except Exception:
                pass
            preview = "\n".join(files[:1000]) or "(empty)"
            dlg = tk.Toplevel(self.root)
            dlg.title(f"Preview {host}:{remote}")
            txt = tk.Text(dlg, width=100, height=30)
            txt.insert("1.0", preview)
            txt.pack(fill="both", expand=True)
            append_history(f"Previewed remote dir {remote} on {host}")
        except Exception as e:
            messagebox.showerror("Error", f"Preview failed: {e}")
            append_history(f"Preview failed for {host}{remote}: {e}")

    # -------------------------
    # Save All Settings (Main)
    # -------------------------
    def _save_all_from_ui(self):
        # Iterate over server_tabs and write back state/local/stations into self.servers
        for idx, ui in list(self.server_tabs.items()):
            if idx >= len(self.servers):
                continue
            self.servers[idx]["state"] = ui["state_var"].get().strip()
            self.servers[idx]["local_folder"] = ui["folder_var"].get().strip() or os.getcwd()
            self.servers[idx]["stations"] = [ui["station_list"].get(i) for i in range(ui["station_list"].size())]
            # single_ts / date not persisted (they are momentary)
        self.auto_midnight_enabled = bool(self.auto_var.get())
        self._save_settings()
        messagebox.showinfo("Saved", "All settings saved.")

        # If auto-midnight was enabled/disabled, start/stop scheduler accordingly
        if self.auto_midnight_enabled and SCHEDULE_AVAILABLE:
            self.start_scheduler()
        else:
            self.stop_scheduler()

    # -------------------------
    # History functions
    # -------------------------
    def _load_history(self):
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
                self.history_text.delete("1.0", "end")
                self.history_text.insert("1.0", fh.read())

    def _clear_history(self):
        if messagebox.askyesno("Confirm", "Clear history log?"):
            open(HISTORY_FILE, "w", encoding="utf-8").close()
            self._load_history()

    # -------------------------
    # Scheduler: Auto Midnight
    # -------------------------
    def _on_toggle_auto_midnight(self):
        self.auto_midnight_enabled = bool(self.auto_var.get())
        if self.auto_midnight_enabled:
            if not SCHEDULE_AVAILABLE:
                messagebox.showwarning("Schedule not available", "Python 'schedule' library not installed. Auto-midnight disabled.")
                self.auto_var.set(0)
                self.auto_midnight_enabled = False
                return
            self.start_scheduler()
        else:
            self.stop_scheduler()
        self._save_settings()

    def start_scheduler(self):
        if not SCHEDULE_AVAILABLE:
            append_history("Schedule not available; cannot start scheduler")
            return
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            return
        self.scheduler_stop_event.clear()
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        append_history("Scheduler started")

    def stop_scheduler(self):
        if self.scheduler_thread:
            self.scheduler_stop_event.set()
            append_history("Scheduler stopping")
            # don't join here (daemon thread) — it will exit soon

    def _scheduler_loop(self):
        # schedule job daily at 00:10
        schedule.clear()
        schedule.every().day.at("00:10").do(self._scheduled_job)
        while not self.scheduler_stop_event.is_set():
            try:
                schedule.run_pending()
            except Exception:
                pass
            time.sleep(5)

    def _scheduled_job(self):
        # build params for yesterday
        yesterday = date.today() - timedelta(days=1)
        params_map = {}
        for idx, s in enumerate(self.servers):
            # load per-server stations/local/state from saved config
            stations = s.get("stations", [])
            local_folder = s.get("local_folder", os.path.join(os.getcwd(), "downloads"))
            state = s.get("state", "")
            params = {
                "start_dt": datetime(yesterday.year, yesterday.month, yesterday.day),
                "end_dt": datetime(yesterday.year, yesterday.month, yesterday.day),
                "start_hour": 0,
                "start_min": 0,
                "end_hour": 23,
                "end_min": 55,
                "step_minutes": 15,
                "local_folder": local_folder,
                "state": state,
                "single_ts": "",
                "test_mode": False
            }
            params_map[idx] = (params, stations)

        # Start downloads simultaneously for all servers
        for idx, (params, stations) in params_map.items():
            if not stations:
                append_history(f"Scheduled: no stations configured for server idx {idx}")
                continue
            # ensure controller exists
            ctrl = self.controllers.get(idx)
            if not ctrl:
                ctrl = ServerController(idx, self.servers[idx], self._server_ui_update)
                self.controllers[idx] = ctrl
            ctrl.cfg = self.servers[idx]
            started = ctrl.start_download(stations, params)
            if started:
                # update UI label if tab exists
                ui = self.server_tabs.get(idx)
                if ui:
                    ui["status_lbl"].config(text="Scheduled Downloading...")
            append_history(f"Scheduler started download for server idx {idx}")

    # -------------------------
    # Misc helpers
    # -------------------------
    def _preview_remote_dir_for_server(self, server_cfg):
        """Simple directory preview, same behavior as previous working version."""
        host = server_cfg.get("host")
        if not host:
            messagebox.showerror("Error", "Server host not configured")
            return
        port = int(server_cfg.get("port", 21))
        user = server_cfg.get("user", "")
        pwd = server_cfg.get("pass", "")
        remote = server_cfg.get("remote", "/")

        try:
            ftp = ftp_connect(host, user, pwd, port=port, retries=2)
            files = []
            try:
                ftp.cwd(remote)
                files = ftp.nlst()
            except Exception:
                # fallback to listing root if remote path fails
                try:
                    ftp.cwd("/")
                    files = ftp.nlst()
                except Exception:
                    files = []
            ftp.quit()

            # show simple popup with file list
            top = tk.Toplevel(self.root)
            top.title(f"Remote Dir: {host}  ({remote})")
            text = tk.Text(top, width=80, height=30)
            text.pack(fill="both", expand=True)
            text.insert("1.0", "\n".join(files) if files else "(No files found)")
            append_history(f"Previewed {remote} on {host}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to preview: {e}")
            append_history(f"Preview failed for {host}: {e}")
            
    # Fix earlier accidental recursion by reusing the implementation method above
    # (But ensure name collision avoided — actually _preview_remote_dir_for_server is implemented above)
    # So the above line is not needed. Keep the accurate, implemented method used earlier.

def main():
    root = tk.Tk()
    app = FTPDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
