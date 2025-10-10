#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Ultra-lightweight motion detection webcam server for Android/Termux
- Hosts a simple webpage showing live camera feed
- Detects motion using simple pixel differencing (very fast)
- Saves images when motion is detected
- Uses only PIL/Pillow - no OpenCV needed
- Runs every second with minimal CPU usage
"""

import os
import time
import threading
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageChops, ImageStat
import io

# Configuration
CAMERA_ID = os.environ.get("CAMERA_ID", "0")
MOTION_THRESHOLD = 20  # Lower = more sensitive (0-255)
MOTION_PIXEL_THRESHOLD = 50  # Minimum number of changed pixels
SAVE_DIR = "motion_captures"
PORT = 8080

# Global variables for motion detection
last_frame = None
motion_detected = False
current_frame_b64 = None
motion_count = 0

class MotionDetector:
    def __init__(self):
        self.last_frame = None
        self.motion_threshold = MOTION_THRESHOLD
        self.pixel_threshold = MOTION_PIXEL_THRESHOLD
        
    def init_camera(self):
        """Initialize camera using termux-camera-photo"""
        try:
            # Test if camera works
            test_path = "/tmp/test_cam.jpg"
            result = subprocess.run([
                "termux-camera-photo", "-c", str(CAMERA_ID), test_path
            ], capture_output=True, timeout=5)
            
            if result.returncode == 0 and os.path.exists(test_path):
                os.remove(test_path)
                return True
        except Exception as e:
            print(f"Camera test failed: {e}")
        return False
    
    def capture_frame(self):
        """Capture frame using termux-camera-photo"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            temp_path = f"/tmp/frame_{timestamp}.jpg"
            
            result = subprocess.run([
                "termux-camera-photo", "-c", str(CAMERA_ID), temp_path
            ], capture_output=True, timeout=3)
            
            if result.returncode == 0 and os.path.exists(temp_path):
                # Load with PIL
                image = Image.open(temp_path)
                os.remove(temp_path)
                return image
        except Exception as e:
            print(f"Frame capture failed: {e}")
        return None
    
    def detect_motion(self, frame):
        """Detect motion using simple pixel differencing with PIL"""
        if frame is None:
            return False, None
        
        # Convert to grayscale and resize for faster processing
        gray = frame.convert('L').resize((320, 240))
        
        if self.last_frame is None:
            self.last_frame = gray
            return False, frame
        
        # Compute difference
        diff = ImageChops.difference(self.last_frame, gray)
        
        # Convert to numpy-like array for thresholding
        diff_array = list(diff.getdata())
        
        # Count pixels above threshold
        changed_pixels = sum(1 for pixel in diff_array if pixel > self.motion_threshold)
        
        motion_detected = changed_pixels > self.pixel_threshold
        
        # Update last frame
        self.last_frame = gray
        
        return motion_detected, frame

class WebcamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global current_frame_b64, motion_detected, motion_count
        
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Motion Webcam</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { 
                        font-family: Arial, sans-serif; 
                        margin: 0; 
                        padding: 20px; 
                        background: #000; 
                        color: #fff;
                    }
                    .container { 
                        max-width: 800px; 
                        margin: 0 auto; 
                        text-align: center;
                    }
                    #webcam { 
                        max-width: 100%; 
                        height: auto; 
                        border: 2px solid #333;
                        border-radius: 10px;
                    }
                    .status {
                        margin: 10px 0;
                        padding: 10px;
                        border-radius: 5px;
                        font-weight: bold;
                    }
                    .motion { background: #ff4444; }
                    .no-motion { background: #44ff44; }
                    .info {
                        margin: 20px 0;
                        padding: 15px;
                        background: #333;
                        border-radius: 5px;
                    }
                    .stats {
                        display: flex;
                        justify-content: space-around;
                        margin: 20px 0;
                    }
                    .stat {
                        background: #222;
                        padding: 10px;
                        border-radius: 5px;
                        min-width: 100px;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>📹 Motion Detection Webcam</h1>
                    <div id="status" class="status no-motion">No Motion</div>
                    <img id="webcam" src="/stream" alt="Webcam Feed">
                    <div class="info">
                        <h3>📊 Statistics</h3>
                        <div class="stats">
                            <div class="stat">
                                <div>Motion Events</div>
                                <div id="motion-count">0</div>
                            </div>
                            <div class="stat">
                                <div>Status</div>
                                <div id="connection-status">Connected</div>
                            </div>
                        </div>
                    </div>
                    <div class="info">
                        <h3>ℹ️ Info</h3>
                        <p>• Motion detection runs every second</p>
                        <p>• Images are saved when motion is detected</p>
                        <p>• Check the 'motion_captures' folder for saved images</p>
                    </div>
                </div>
                
                <script>
                    let motionCount = 0;
                    let lastUpdate = Date.now();
                    
                    function updateImage() {
                        const img = document.getElementById('webcam');
                        const status = document.getElementById('status');
                        const countEl = document.getElementById('motion-count');
                        const connEl = document.getElementById('connection-status');
                        
                        // Add timestamp to prevent caching
                        img.src = '/stream?t=' + Date.now();
                        
                        // Check for updates
                        fetch('/status')
                            .then(response => response.json())
                            .then(data => {
                                if (data.motion_detected) {
                                    status.textContent = 'Motion Detected!';
                                    status.className = 'status motion';
                                    if (data.motion_count > motionCount) {
                                        motionCount = data.motion_count;
                                        countEl.textContent = motionCount;
                                    }
                                } else {
                                    status.textContent = 'No Motion';
                                    status.className = 'status no-motion';
                                }
                                connEl.textContent = 'Connected';
                                lastUpdate = Date.now();
                            })
                            .catch(() => {
                                connEl.textContent = 'Disconnected';
                            });
                    }
                    
                    // Update every second
                    setInterval(updateImage, 1000);
                    
                    // Initial load
                    updateImage();
                </script>
            </body>
            </html>
            """
            self.wfile.write(html.encode())
            
        elif self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-type', 'image/jpeg')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            
            if current_frame_b64:
                img_data = base64.b64decode(current_frame_b64)
                self.wfile.write(img_data)
            else:
                # Send placeholder image
                placeholder = Image.new('RGB', (640, 480), color='black')
                # Add text using PIL
                from PIL import ImageDraw, ImageFont
                draw = ImageDraw.Draw(placeholder)
                try:
                    # Try to use a default font
                    font = ImageFont.load_default()
                except:
                    font = None
                draw.text((200, 240), "No Camera Feed", fill='white', font=font)
                
                # Convert to bytes
                img_io = io.BytesIO()
                placeholder.save(img_io, format='JPEG', quality=85)
                self.wfile.write(img_io.getvalue())
                
        elif self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            status = {
                'motion_detected': motion_detected,
                'motion_count': motion_count,
                'timestamp': datetime.now().isoformat()
            }
            self.wfile.write(json.dumps(status).encode())
            
        else:
            self.send_response(404)
            self.end_headers()

def motion_worker():
    """Background thread for motion detection"""
    global last_frame, motion_detected, current_frame_b64, motion_count
    
    detector = MotionDetector()
    
    # Initialize camera
    if not detector.init_camera():
        print("❌ Camera initialization failed!")
        return
    
    print("✅ Camera initialized successfully")
    
    # Create save directory
    Path(SAVE_DIR).mkdir(exist_ok=True)
    
    while True:
        try:
            # Capture frame
            frame = detector.capture_frame()
            if frame is None:
                time.sleep(1)
                continue
            
            # Detect motion
            motion, processed_frame = detector.detect_motion(frame)
            
            # Update global state
            motion_detected = motion
            
            # Convert frame to base64 for web display
            img_io = io.BytesIO()
            frame.save(img_io, format='JPEG', quality=85)
            current_frame_b64 = base64.b64encode(img_io.getvalue()).decode()
            
            # Save image if motion detected
            if motion:
                motion_count += 1
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{SAVE_DIR}/motion_{timestamp}_{motion_count:04d}.jpg"
                frame.save(filename, 'JPEG', quality=95)
                print(f"📸 Motion detected! Saved: {filename}")
            
            time.sleep(1)  # Check every second
            
        except Exception as e:
            print(f"❌ Motion detection error: {e}")
            time.sleep(1)

def main():
    print("🚀 Starting Motion Detection Webcam Server...")
    print(f"📷 Camera ID: {CAMERA_ID}")
    print(f"🌐 Web interface: http://localhost:{PORT}")
    print(f"💾 Motion captures saved to: {SAVE_DIR}")
    print("Press Ctrl+C to stop")
    
    # Start motion detection thread
    motion_thread = threading.Thread(target=motion_worker, daemon=True)
    motion_thread.start()
    
    # Start web server
    try:
        server = HTTPServer(('0.0.0.0', PORT), WebcamHandler)
        print(f"✅ Web server started on port {PORT}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        server.shutdown()
    except Exception as e:
        print(f"❌ Server error: {e}")

if __name__ == "__main__":
    main()
