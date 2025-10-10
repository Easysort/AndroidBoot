#!/data/data/com.termux/files/usr/bin/bash
# Start the ultra-lightweight motion detection webcam server

set -euo pipefail

cd "$(dirname "$0")"

# Install required packages if not already installed
echo "📦 Installing required packages..."
pkg install -y python || true

# Install Python dependencies (only Pillow - much lighter than OpenCV)
echo "🐍 Installing Python dependencies..."
pip install Pillow || true

# Make the script executable
chmod +x motion-webcam.py

# Set camera permissions
echo "📷 Setting up camera permissions..."
termux-camera-info || echo "⚠️  Camera info not available, continuing..."

# Start the webcam server
echo "🚀 Starting ultra-lightweight motion detection webcam..."
echo "📱 Using only PIL/Pillow - no OpenCV needed!"
python motion-webcam.py
