@echo off
echo ==========================================
echo   Apollo Hospital Voice AI Assistant
echo   v2.0 - Hackathon Demo (Windows)
echo ==========================================

echo.
echo Using Python 3.11...
py -3.11 -m pip install -r requirements.txt
py -3.11 -m pip install piper-tts

echo.
echo Starting server...
py -3.11 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
