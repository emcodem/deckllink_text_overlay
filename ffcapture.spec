from PyInstaller.utils.hooks import collect_all

datas_av,  binaries_av,  hidden_av  = collect_all('av')
datas_cv2, binaries_cv2, hidden_cv2 = collect_all('cv2')
datas_qt,  binaries_qt,  hidden_qt  = collect_all('PyQt6')
datas_sd,  binaries_sd,  hidden_sd  = collect_all('sounddevice')

a = Analysis(
    ['src/main.py'],
    pathex=[SPECPATH],
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
    pyz, a.scripts, a.binaries, a.datas, [],
    name='FFCapture',
    console=True,
    icon=None,
    version='_version_info.txt',
)
