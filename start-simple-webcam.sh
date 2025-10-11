#!/data/data/com.termux/files/usr/bin/bash
# Start the ultra-simple motion detection webcam server

set -euo pipefail

cd "$(dirname "$0")"

# Install only basic Python (no external dependencies needed!)
echo "📦 Installing Python..."
pkg install -y python || true

# Make the script executable
chmod +x simple-webcam.py

# Set camera permissions
echo "📷 Setting up camera permissions..."
termux-camera-info || echo "⚠️  Camera info not available, continuing..."

# Start the webcam server
echo "🚀 Starting ultra-simple motion detection webcam..."
echo "📦 Using ONLY built-in Python libraries - no pip install needed!"
echo "🔍 Motion detection based on file size comparison"
python simple-webcam.py
