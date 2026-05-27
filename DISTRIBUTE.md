# FFCapture — Distribution Guide

## Hard Requirement (Cannot Be Bundled)

The **BlackMagic DeckLink driver** must be installed on any target machine.
The app communicates with DeckLink hardware via COM — that is a kernel driver, not a Python package.
Download from [blackmagicdesign.com/support](https://www.blackmagicdesign.com/support) → Desktop Video.

Everything else described below can be made portable.

---

## Tool: PyInstaller (one-folder build)

PyInstaller embeds the Python interpreter and all native DLLs (FFmpeg via PyAV, Qt, OpenCV,
PortAudio) into a single folder you can xcopy to any Windows machine.

Install into the project venv:

```powershell
pip install pyinstaller
```

---

## Step 1 — Fix Frozen-App Path Issues in config.py

When PyInstaller freezes the app, `Path(__file__).parent.parent` inside `src/config.py` resolves
into the read-only `_MEIPASS` temp tree, not a writable location.

Edit `src/config.py` — replace the path block at the top:

```python
# Project paths
import sys as _sys

if getattr(_sys, 'frozen', False):
    # Running as a PyInstaller bundle — place logs next to the .exe
    PROJECT_ROOT = Path(_sys.executable).parent
else:
    PROJECT_ROOT = Path(__file__).parent.parent

SRC_DIR = PROJECT_ROOT / "src"
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
```

---

## Step 2 — Create the Spec File

Save this as `ffcapture.spec` in the project root:

```python
from PyInstaller.utils.hooks import collect_all

datas_av,  binaries_av,  hidden_av  = collect_all('av')
datas_cv2, binaries_cv2, hidden_cv2 = collect_all('cv2')
datas_qt,  binaries_qt,  hidden_qt  = collect_all('PyQt6')
datas_sd,  binaries_sd,  hidden_sd  = collect_all('sounddevice')

a = Analysis(
    ['src/main.py'],
    pathex=['.'],
    binaries=binaries_av + binaries_cv2 + binaries_qt + binaries_sd,
    datas=datas_av + datas_cv2 + datas_qt + datas_sd,
    hiddenimports=(
        hidden_av + hidden_cv2 + hidden_qt + hidden_sd + [
            'comtypes.stream',
            'comtypes.client',
            'win32api',
            'win32com',
            'win32com.client',
            'pywintypes',
        ]
    ),
    hookspath=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='FFCapture',
    console=False,       # set True temporarily to see startup errors
    icon=None,           # path to a .ico file if desired
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    name='ffcapture',
)
```

---

## Step 3 — Build

```powershell
pyinstaller ffcapture.spec
```

Output lands in `dist\ffcapture\`. A clean build takes 2–4 minutes on first run.

To force a clean rebuild:

```powershell
Remove-Item -Recurse -Force build, dist
pyinstaller ffcapture.spec
```

---

## Step 4 — Add Runtime Config Files

The build output does **not** include config files with site-specific paths. Copy these alongside
`FFCapture.exe` in `dist\ffcapture\` before zipping:

| File | Purpose |
|---|---|
| `src/config.py` | Device indices, output URLs, file paths |
| `src/config_subtitles.py` | Subtitle font, text file path, colors |

Operators edit these two files on the target machine. No Python knowledge needed — they are plain
key=value assignments.

**Common values to update on each deployment:**

- `CAPTURE_DEVICE_INDEX` / `PLAYOUT_DEVICE_INDEX` — DeckLink card slot order
- `SIMULATION_INPUT_FILE` — only used when `SIMULATE_HARDWARE=True`
- `OUTPUTS[...]["url"]` / `OUTPUTS[...]["path"]` — stream destination / recording path
- `TEXT_FILE` — path to the subtitle lines file
- `TEXT_FONT_PATH` — must exist on the target machine (default `C:\Windows\Fonts\arial.ttf` is safe)

---

## Step 5 — Test the Build Locally

Before shipping, smoke-test from the dist folder (not from source):

```powershell
cd dist\ffcapture
.\FFCapture.exe
```

Check `dist\ffcapture\logs\ffcapture.log` for startup errors.

If the window never appears, temporarily set `console=True` in the spec, rebuild, and read the
terminal output.

---

## Step 6 — Ship

Zip the entire `dist\ffcapture\` folder:

```powershell
Compress-Archive -Path dist\ffcapture -DestinationPath FFCapture-v1.0.zip
```

**Contents of the zip:**

```
ffcapture\
    FFCapture.exe
    _internal\          ← Python runtime, Qt DLLs, FFmpeg DLLs, OpenCV, etc.
    config.py           ← site-specific config (you added this)
    config_subtitles.py ← subtitle config (you added this)
    logs\               ← created on first run
```

**Target machine requires:**

- Windows 10 or 11 (64-bit)
- BlackMagic Desktop Video driver (hardware model matching `CAPTURE_DEVICE_INDEX`)
- No Python, no Visual C++ Redistributable, no other runtime

---

## Known Issues

**Qt platform plugin error** (`could not find or load the Qt platform plugin "windows"`):

Add to `src/main.py` before any PyQt6 import:

```python
import sys, os
if getattr(sys, 'frozen', False):
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(
        sys._MEIPASS, 'PyQt6', 'Qt6', 'plugins', 'platforms'
    )
```

**comtypes cache warning on first run** — harmless. comtypes generates a typelib cache in
`%LOCALAPPDATA%\comtypes\cache` on first launch; subsequent runs are silent.

**sounddevice / no audio devices found** — PortAudio (`_sounddevice.pyd` + `libportaudio*.dll`)
must be present in `_internal\`. Confirm with:

```powershell
Get-ChildItem dist\ffcapture\_internal\*portaudio* -Recurse
```

**Antivirus false positive** — PyInstaller bundles are sometimes flagged. If the .exe is blocked
on the target machine, add an exclusion for the `ffcapture\` folder in Windows Security.
