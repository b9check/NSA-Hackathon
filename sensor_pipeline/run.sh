#!/bin/bash
# Start the sensor fusion pipeline
# Serves both backend API/WebSocket and frontend on http://localhost:8000

echo "=== Sensor Fusion Pipeline ==="
echo ""

# Build frontend if needed
if [ ! -d "frontend/build" ]; then
    echo "Building frontend..."
    cd frontend && npm install && npm run build && cd ..
fi

echo "Starting server on http://localhost:8000"
echo "  - Tactical display: http://localhost:8000"
echo "  - WebSocket: ws://localhost:8000/ws"
echo "  - API: http://localhost:8000/api/scenario"
echo ""

cd backend && python server.py
