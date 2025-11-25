# FTP RTU Downloader

A multi-threaded Python application for downloading RTU (Remote Terminal Unit) data files from multiple FTP servers with scheduling capabilities, pause/resume functionality, and automatic folder detection.

## Features

### Core Functionality
- **Multi-Server Support**: Manage and download from multiple FTP servers simultaneously
- **Per-Server Configuration**: Independent settings for each server (host, port, credentials, remote directories)
- **Intelligent Path Detection**: Automatically detects remote folder structures:
  - `/<base>/<YYYY>/<MM>/<DD>/`
  - `/<base>/<YYYY>/<MM>/<DDMMYYYY>/`
- **Station-Based Filtering**: Download files for specific station IDs
- **Date Range Selection**: Download files across custom date ranges with time window filtering

### Download Control
- **Pause/Resume/Cancel**: Full control over ongoing downloads per server
- **Progress Tracking**: Real-time download progress with file-by-file status updates
- **Skip Existing Files**: Automatically skips files that already exist locally (no overwrites)
- **Multi-threaded**: Each server runs in its own thread for simultaneous downloads

### Scheduling
- **Auto Midnight Downloads**: Schedule automatic downloads at 00:10 daily for previous day's data
- **Simultaneous Execution**: All configured servers download simultaneously during scheduled runs
- **Persistent Settings**: All configurations saved and restored between sessions

### User Interface
- **Tabbed Interface**: 
  - **Settings Tab**: Manage server connections and test connectivity
  - **Main Tab**: Per-server sub-tabs with independent download controls
  - **History Tab**: View and manage download history logs
- **Remote Directory Preview**: Browse remote FTP directories before downloading
- **Calendar Date Picker**: Easy date selection (when `tkcalendar` is installed)

## Installation

### Requirements
- Python 3.7+
- Required packages:
  ```bash
  pip install tkinter
  ```

### Optional Dependencies
For enhanced functionality:
```bash
pip install tkcalendar schedule
```

- `tkcalendar`: Enables calendar date picker widgets
- `schedule`: Required for auto midnight scheduling feature

### Setup
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd ftp-rtu-downloader
   ```

2. Install dependencies:
   ```bash
   pip install tkcalendar schedule
   ```

3. Run the application:
   ```bash
   python main.py
   ```

## Usage

### Initial Setup

1. **Add FTP Servers** (Settings Tab):
   - Click "Add" to create a new server entry
   - Enter server details:
     - Host (IP address or domain)
     - Port (default: 21)
     - Username
     - Password
     - Remote base directory path
   - Click "Save Settings" to persist configuration
   - Use "Test Connect" to verify credentials

2. **Configure Stations** (Main Tab):
   - Navigate to the server's sub-tab in Main
   - Enter State identifier (optional)
   - Set local download folder
   - Add station IDs (one at a time)
   - Click "💾 Save All Settings" to persist

### Manual Download

1. Select the server tab in Main
2. Choose date range:
   - Use date pickers for Start Date and End Date
   - OR enter single timestamp in YYMMDDHHMM format
3. Click "Download" to start
4. Use "Pause", "Resume", or "Cancel" as needed
5. Monitor progress in the status label

### Scheduled Downloads

1. In Main tab, check "Enable Auto Midnight Download (00:10)"
2. Click "💾 Save All Settings"
3. The scheduler will automatically download previous day's data at 00:10 daily
4. All configured servers with stations will run simultaneously

### File Organization

Downloaded files are organized locally as:
```
<local_base>/<State>/<StationID>/<YYYY>/<MM>/<DD>/<filename>
```

Example:
```
downloads/TX/STAT01/2024/12/15/STAT01_2412151200.txt
```

## Configuration Files

### settings.json
Stores all application configuration:
- Server credentials and connection details
- Station lists per server
- Local folder paths
- Auto midnight scheduling preference

### download_history.log
Timestamped log of all download operations:
- Download start/completion events
- Files downloaded per station
- Errors and warnings
- Scheduler activity

## Features Detail

### Remote Path Auto-Detection
The application automatically tries multiple folder structure patterns:
- `/base/2024/12/15/`
- `/base/2024/12/15122024/`
- Variations with/without trailing slashes

This ensures compatibility with different FTP server configurations.

### File Matching
Files are matched if they:
- Start with the station ID prefix
- End with `.txt` extension (case-insensitive)

Examples for station "STAT01":
- ✅ `STAT01_2412151200.txt`
- ✅ `STAT01.txt`
- ❌ `OTHER_2412151200.txt`

### Preview Remote Directory
Before downloading, use "Preview Remote Dir" to:
- Verify remote folder structure
- Check available files
- Confirm station file naming patterns

## Troubleshooting

### Connection Issues
- Verify host, port, username, and password in Settings
- Use "Test Connect" to diagnose connection problems
- Check firewall settings for FTP access (port 21 or custom)

### Missing Files
- Use "Preview Remote Dir" to verify remote folder structure
- Confirm date format matches server's organization
- Check that station IDs match file prefixes exactly

### Scheduler Not Working
- Ensure `schedule` package is installed: `pip install schedule`
- Verify "Enable Auto Midnight Download" is checked
- Check download_history.log for scheduler activity

### Files Not Downloading
- Verify files don't already exist locally (app skips existing files)
- Check station ID matches file naming convention
- Ensure date range includes files you expect
- Review download_history.log for error details

## Development

### Project Structure
```
ftp-rtu-downloader/
├── main.py              # GUI application and scheduler
├── downloader.py        # FTP download utilities
├── settings.json        # Configuration (auto-generated)
└── download_history.log # Activity log (auto-generated)
```

### Key Classes
- `FTPDownloaderApp`: Main application GUI and logic
- `ServerController`: Per-server thread management
- `download_files_by_prefix()`: Core download function with path detection


## Support

For issues, questions, or contributions, please [open an issue](link-to-issues) on GitHub.
