#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Ultra-simple motion detection webcam server for Android/Termux
- Uses ONLY built-in Python libraries - no external dependencies
- Hosts a simple webpage showing live camera feed
- Detects motion using basic file size comparison
- Saves images when motion is detected
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

# Configuration
CAMERA_ID = os.environ.get("CAMERA_ID", "0")
MOTION_SIZE_THRESHOLD = 50000  # Minimum file size difference to consider motion
SAVE_DIR = "motion_captures"
PORT = 8080

# Global variables for motion detection
last_image_size = 0
motion_detected = False
current_frame_b64 = None
motion_count = 0

class SimpleMotionDetector:
    def __init__(self):
        self.last_image_size = 0
        self.size_threshold = MOTION_SIZE_THRESHOLD
        
    def init_camera(self):
        """Initialize camera using termux-camera-photo"""
        print(f"🔍 Testing camera ID: {CAMERA_ID}")
        
        # First check if termux-camera-photo exists
        try:
            result = subprocess.run(["which", "termux-camera-photo"], capture_output=True)
            if result.returncode != 0:
                print("❌ termux-camera-photo not found. Install Termux:API")
                return False
        except Exception as e:
            print(f"❌ Error checking termux-camera-photo: {e}")
            return False
        
        # Try different camera IDs if the default doesn't work
        camera_ids_to_try = [str(CAMERA_ID), "0", "1", "2"]
        
        for cam_id in camera_ids_to_try:
            try:
                print(f"🔍 Testing camera ID: {cam_id}")
                test_path = f"/tmp/test_cam_{cam_id}.jpg"
                
                result = subprocess.run([
                    "termux-camera-photo", "-c", cam_id, test_path
                ], capture_output=True, text=True, timeout=10)
                
                print(f"Return code: {result.returncode}")
                if result.stderr:
                    print(f"Stderr: {result.stderr}")
                
                if result.returncode == 0 and os.path.exists(test_path):
                    file_size = os.path.getsize(test_path)
                    print(f"✅ Camera {cam_id} works! File size: {file_size} bytes")
                    os.remove(test_path)
                    # Update the global camera ID
                    global CAMERA_ID
                    CAMERA_ID = cam_id
                    return True
                else:
                    print(f"❌ Camera {cam_id} failed")
                    
            except subprocess.TimeoutExpired:
                print(f"❌ Camera {cam_id} timed out")
            except Exception as e:
                print(f"❌ Camera {cam_id} error: {e}")
        
        print("❌ No working camera found")
        print("Troubleshooting:")
        print("1. Make sure Termux:API is installed")
        print("2. Grant camera permission to Termux")
        print("3. Try running: python test-camera.py")
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
                return temp_path
        except Exception as e:
            print(f"Frame capture failed: {e}")
        return None
    
    def detect_motion(self, image_path):
        """Detect motion using simple file size comparison"""
        if image_path is None or not os.path.exists(image_path):
            return False, None
        
        try:
            current_size = os.path.getsize(image_path)
            
            if self.last_image_size == 0:
                self.last_image_size = current_size
                return False, image_path
            
            # Simple motion detection based on file size change
            size_diff = abs(current_size - self.last_image_size)
            motion_detected = size_diff > self.size_threshold
            
            # Update last size
            self.last_image_size = current_size
            
            return motion_detected, image_path
            
        except Exception as e:
            print(f"Motion detection error: {e}")
            return False, image_path

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
                <title>Simple Motion Webcam</title>
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
                    <h1>📹 Simple Motion Webcam</h1>
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
                        <p>• Uses file size comparison (no external libraries)</p>
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
                # Send placeholder image (simple black image)
                placeholder = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x03\x01"\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\x00\xff\xd9'
                self.wfile.write(placeholder)
                
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
    global last_image_size, motion_detected, current_frame_b64, motion_count
    
    detector = SimpleMotionDetector()
    
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
            image_path = detector.capture_frame()
            if image_path is None:
                time.sleep(1)
                continue
            
            # Detect motion
            motion, processed_path = detector.detect_motion(image_path)
            
            # Update global state
            motion_detected = motion
            
            # Convert frame to base64 for web display
            try:
                with open(image_path, 'rb') as f:
                    img_data = f.read()
                    current_frame_b64 = base64.b64encode(img_data).decode()
            except Exception as e:
                print(f"Error reading image: {e}")
            
            # Save image if motion detected
            if motion:
                motion_count += 1
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{SAVE_DIR}/motion_{timestamp}_{motion_count:04d}.jpg"
                
                # Copy the image to the motion captures folder
                try:
                    with open(image_path, 'rb') as src:
                        with open(filename, 'wb') as dst:
                            dst.write(src.read())
                    print(f"📸 Motion detected! Saved: {filename}")
                except Exception as e:
                    print(f"Error saving image: {e}")
            
            # Clean up temp file
            try:
                os.remove(image_path)
            except:
                pass
            
            time.sleep(1)  # Check every second
            
        except Exception as e:
            print(f"❌ Motion detection error: {e}")
            time.sleep(1)

def main():
    print("🚀 Starting Simple Motion Detection Webcam Server...")
    print(f"📷 Camera ID: {CAMERA_ID}")
    print(f"🌐 Web interface: http://localhost:{PORT}")
    print(f"💾 Motion captures saved to: {SAVE_DIR}")
    print("📦 Using ONLY built-in Python libraries - no external dependencies!")
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
