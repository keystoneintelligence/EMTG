from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def test_regenerated_sources_match_committed_sources(tmp_path):
    shutil.copytree(ROOT/'OptionsOverhaul', tmp_path/'OptionsOverhaul')
    (tmp_path/'src/Core').mkdir(parents=True)
    (tmp_path/'PyEMTG').mkdir()
    shutil.copytree(ROOT/'PyEMTG/OptionsOverhaul', tmp_path/'PyEMTG/OptionsOverhaul')
    subprocess.run([sys.executable, str(ROOT/'PyEMTG/OptionsOverhaul/make_EMTG_missionoptions_journeyoptions.py'), '--root', str(tmp_path)], check=True)
    for relative in ['src/Core/missionoptions.cpp', 'src/Core/missionoptions.h', 'src/Core/journeyoptions.cpp', 'src/Core/journeyoptions.h', 'PyEMTG/MissionOptions.py', 'PyEMTG/JourneyOptions.py']:
        assert (tmp_path/relative).read_text() == (ROOT/relative).read_text(), relative
