# Build Instructions — E-Paper Dashboard

## Prerequisites

- Python build environment at `C:\Tools\epaper-build\`
- Inno Setup installed (jrsoftware.org/isdl.php)
- All source files up to date in `Software\Desktop\`

---

## Step 1 — Delete old build output (if any)

Delete these folders/files if they exist inside `Software\Desktop\`:

- `dist\EPaperDashboard\` (folder)
- `dist\EPaperDashboard.exe` (leftover from onefile builds)
- `build\` (folder)

---

## Step 2 — Build the exe with PyInstaller

Open a terminal and run:

```powershell
cd "c:\Users\thors\Documents\GitHub\Steam-Machine-EPaper-Dashboard\Software\Desktop"
C:\Tools\epaper-build\Scripts\pyinstaller.exe epaper_dashboard.spec
```

If prompted `"ALL ITS CONTENTS will be REMOVED! Continue? (y/N)"` — type `y` and press Enter.

When complete, the output will be at:
```
Software\Desktop\dist\EPaperDashboard\
```

Verify the folder contains `EPaperDashboard.exe` and an `_internal\` subfolder before continuing.

---

## Step 3 — Build the installer with Inno Setup

1. Open **Inno Setup Compiler** from the Start menu
2. Click **File → Open** and select:
   ```
   Software\Desktop\installer.iss
   ```
3. Press **F9** (or click Build → Compile)

When complete, the installer will be at:
```
Software\Desktop\installer_output\EPaperDashboard_Setup.exe
```

---

## Step 4 — Test the installer

1. Uninstall any previous version via **Settings → Apps → E-Paper Dashboard → Uninstall**
2. Delete `%APPDATA%\EPaperDashboard\config.yml` if it exists (to test first-run flow)
3. Run `EPaperDashboard_Setup.exe`
4. On first launch, a dialog will appear — click OK, edit the config in Notepad, save and close Notepad
5. Verify the dashboard connects to the ESP32 and cycles through pages

---

## Notes

- The build environment at `C:\Tools\epaper-build\` must never be moved into the git repo
- `config.template.yml` is the clean config that ships with the installer — never commit personal API keys into it
- The user's live config lives at `%APPDATA%\EPaperDashboard\config.yml` and is never touched by reinstalls
- Logs are written to `%APPDATA%\EPaperDashboard\dashboard.log` for debugging
