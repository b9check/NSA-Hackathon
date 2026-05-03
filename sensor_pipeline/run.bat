@echo off
echo === Sensor Fusion Pipeline ===
echo.

if not exist "frontend\build" (
    echo Building frontend...
    cd frontend
    call npm install
    call npm run build
    cd ..
)

echo Starting server on http://localhost:8000
echo   - Tactical display: http://localhost:8000
echo   - WebSocket: ws://localhost:8000/ws
echo   - API: http://localhost:8000/api/scenario
echo.

cd backend
python server.py
