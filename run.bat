@echo off
echo ===================================================
echo   Starting OrderCore Matching Engine Terminal
echo   URL: http://127.0.0.1:8000
echo ===================================================
start http://127.0.0.1:8000
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
pause
