# Indian Screener Dashboard

PKScreener-powered dashboard with:
- Python backend API (`server.py`) on port `5050`
- Static HTML frontend (`dashboard.html`) on port `8080`
- One-click start/stop scripts for Windows

## Repository Contents

- `dashboard.html`, `styles.css`, `app.js` : frontend
- `server.py` : backend that runs scans and serves API
- `run_dashboard_one_click.bat` : starts both backend and frontend
- `stop_dashboard_servers.bat` : stops managed processes
- `pkscreener.ini` : runtime config for PKScreener

## First-Time Setup (Any Laptop)

1. Install Python 3.10+.
2. Clone this repository.
3. Run:

```bat
run_dashboard_one_click.bat
```

What the script does automatically:
- Creates `.venv` if missing
- Installs dependencies from `requirements.txt`
- Starts backend at `http://127.0.0.1:5050`
- Starts frontend at `http://127.0.0.1:8080/dashboard.html`

To stop everything:

```bat
stop_dashboard_servers.bat
```

## Clone And Run On Other Laptop

```powershell
git clone https://github.com/VenkyHari98/indian_screener-dashboard.git
Set-Location indian_screener-dashboard
run_dashboard_one_click.bat
```

## Notes

- `.venv`, logs, cache, and generated scan outputs are intentionally ignored from Git.
- If Windows blocks script execution, run from Command Prompt (`cmd`) or use PowerShell with appropriate execution policy for the current session.