@echo off
setlocal
cd /d "%~dp0"
python -m py_compile dms_scene_core.py dms_scene_builder.py || exit /b 1
python dms_scene_builder.py --self-test || exit /b 1
echo DMS SCENE BUILDER CHECK PASS
