#!/usr/bin/env python3
# Debug camera initialization

import subprocess
import os
import sys

def test_camera():
    print("🔍 Testing camera initialization...")
    
    # Test 1: Check if termux-camera-photo exists
    print("\n1. Checking termux-camera-photo command...")
    try:
        result = subprocess.run(["which", "termux-camera-photo"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ termux-camera-photo found: {result.stdout.strip()}")
        else:
            print("❌ termux-camera-photo not found")
            return False
    except Exception as e:
        print(f"❌ Error checking termux-camera-photo: {e}")
        return False
    
    # Test 2: Check camera info
    print("\n2. Checking camera info...")
    try:
        result = subprocess.run(["termux-camera-info"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f"✅ Camera info: {result.stdout.strip()}")
        else:
            print(f"⚠️  Camera info failed: {result.stderr.strip()}")
    except Exception as e:
        print(f"⚠️  Camera info error: {e}")
    
    # Test 3: Try to capture a photo
    print("\n3. Testing photo capture...")
    test_path = "/tmp/test_camera_debug.jpg"
    
    # Clean up any existing test file
    if os.path.exists(test_path):
        os.remove(test_path)
    
    try:
        result = subprocess.run([
            "termux-camera-photo", "-c", "0", test_path
        ], capture_output=True, text=True, timeout=10)
        
        print(f"Return code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        
        if result.returncode == 0 and os.path.exists(test_path):
            file_size = os.path.getsize(test_path)
            print(f"✅ Photo captured successfully! File size: {file_size} bytes")
            os.remove(test_path)
            return True
        else:
            print("❌ Photo capture failed")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Camera capture timed out")
        return False
    except Exception as e:
        print(f"❌ Camera capture error: {e}")
        return False

def test_camera_ids():
    """Test different camera IDs"""
    print("\n4. Testing different camera IDs...")
    
    for camera_id in ["0", "1", "2"]:
        print(f"\nTesting camera ID: {camera_id}")
        test_path = f"/tmp/test_cam_{camera_id}.jpg"
        
        try:
            result = subprocess.run([
                "termux-camera-photo", "-c", camera_id, test_path
            ], capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0 and os.path.exists(test_path):
                file_size = os.path.getsize(test_path)
                print(f"✅ Camera {camera_id} works! File size: {file_size} bytes")
                os.remove(test_path)
                return camera_id
            else:
                print(f"❌ Camera {camera_id} failed: {result.stderr}")
                
        except Exception as e:
            print(f"❌ Camera {camera_id} error: {e}")
    
    return None

if __name__ == "__main__":
    print("📱 Camera Debug Tool")
    print("=" * 50)
    
    # Test basic camera functionality
    if test_camera():
        print("\n🎉 Camera is working!")
    else:
        print("\n🔧 Trying different camera IDs...")
        working_camera = test_camera_ids()
        if working_camera:
            print(f"\n🎉 Camera {working_camera} is working!")
            print(f"Use CAMERA_ID={working_camera} in your script")
        else:
            print("\n❌ No working camera found")
            print("\nTroubleshooting steps:")
            print("1. Make sure Termux:API is installed")
            print("2. Grant camera permission to Termux")
            print("3. Try: termux-camera-info")
            print("4. Try: termux-camera-photo -c 0 /tmp/test.jpg")
