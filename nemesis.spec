# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


datas = [('logo2.png', '.')]
binaries = []
gpg_dir = Path('gpg')
if (gpg_dir / 'bin' / 'gpg.exe').is_file():
    # A aplicação é x64. Não empacotamos a cópia bin_32, que além de
    # desnecessária depende de uma DLL MinGW de 32 bits ausente na distribuição.
    for origem in gpg_dir.rglob('*'):
        relativo = origem.relative_to(gpg_dir)
        if origem.is_file() and relativo.parts[0] != 'bin_32':
            datas.append((str(origem), str(Path('gpg') / relativo.parent)))

# O Python portátil usado para o build não expõe o Tk ao detector do
# PyInstaller dentro do ambiente protegido. Incluímos os componentes oficiais
# explicitamente para que o executável permaneça independente.
python_root = Path(sys.base_prefix)
tcl_root = python_root / 'tcl'
if (tcl_root / 'tcl8.6' / 'init.tcl').is_file():
    datas.extend([
        (str(tcl_root / 'tcl8.6'), '_tcl_data'),
        (str(tcl_root / 'tcl8'), '_tcl_data/tcl8'),
        (str(tcl_root / 'tk8.6'), '_tk_data'),
        (str(python_root / 'Lib' / 'tkinter'), 'tkinter'),
    ])
    binaries.extend([
        (str(python_root / 'DLLs' / '_tkinter.pyd'), '.'),
        (str(python_root / 'DLLs' / 'tcl86t.dll'), '.'),
        (str(python_root / 'DLLs' / 'tk86t.dll'), '.'),
    ])

a = Analysis(
    ['nemesis.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        '_tkinter',
        'tkinter',
        'PIL.Image',
        'PIL.ImageTk',
        'ttkbootstrap',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_tkinter.py'],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='nemesis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # Altere para True se precisar ver o console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo2.ico',
    version='version_info.txt',
)
