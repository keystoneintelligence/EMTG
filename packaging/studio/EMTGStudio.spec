from pathlib import Path


root = Path(SPECPATH).resolve().parents[1]
frontend = root / "PyEMTG" / "Studio" / "frontend" / "dist"
if not (frontend / "index.html").is_file():
    raise SystemExit("EMTG Studio frontend is not built; run npm.cmd run build first")

datas = [
    (str(frontend), "PyEMTG/Studio/frontend/dist"),
    (str(root / "OptionsOverhaul" / "list_of_missionoptions.csv"), "OptionsOverhaul"),
    (str(root / "OptionsOverhaul" / "list_of_journeyoptions.csv"), "OptionsOverhaul"),
    (str(root / "PyEMTG" / "default.emtgopt"), "PyEMTG"),
]

a = Analysis(
    [str(root / "PyEMTG" / "Studio" / "launcher.py")],
    pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=[
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
        "PyEMTG.Studio.worker", "PyEMTG.Studio.materialize",
    ],
    hookspath=[], runtime_hooks=[], excludes=["wx"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="EMTGStudio",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="EMTGStudio")
