#!/data/data/com.termux/files/usr/bin/bash
# Start the motion detection webcam server

set -euo pipefail

cd "$(dirname "$0")"

# Install required packages if not already installed
echo "📦 Installing required packages..."
pkg install -y python opencv-python numpy || true

# Install Python dependencies
echo "🐍 Installing Python dependencies..."
pip install opencv-python numpy || true

# Make the script executable
chmod +x motion-webcam.py

# Set camera permissions
echo "📷 Setting up camera permissions..."
termux-camera-info || echo "⚠️  Camera info not available, continuing..."

# Start the webcam server
echo "🚀 Starting motion detection webcam..."
python motion-webcam.py
