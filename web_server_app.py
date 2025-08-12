#!/usr/bin/env python3
"""
Web server with WebSockets for remote control with responsive interface
Requires: pip install flask flask-socketio opencv-python pyyaml
"""

from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import cv2
import base64
import threading
import time
import json
import yaml
import os
import subprocess
import re
from datetime import datetime
import logging

# Load configuration
def load_config(config_file='config.yaml'):
    """Load configuration from YAML file"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(f"Configuration file {config_file} not found")
        # Return default configuration
        return {
            'server': {'host': '0.0.0.0', 'port': 5000, 'debug': False, 'secret_key': 'default-key'},
            'video': {'source': 'camera', 'camera_index': 0, 'fps': 30, 'stream_fps': 15, 'jpeg_quality': 70},
            'system': {'simulate_sensors': True, 'sensor_update_interval': 5.0, 'max_logs': 100, 
                      'initial_wifi_strength': 85, 'initial_battery': 67},
            'controls': {'joystick': {'max_radius': 55, 'deadzone': 0.05}, 
                        'depth': {'min_value': 0, 'max_value': 100, 'initial_value': 0}},
            'logging': {'level': 'INFO'},
            'network': {'cors_allowed_origins': '*', 'socketio_async_mode': 'threading'}
        }
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        return {}

# Load configuration
config = load_config()

# Logging configuration
log_level = getattr(logging, config.get('logging', {}).get('level', 'INFO').upper())
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = config['server']['secret_key']
socketio = SocketIO(app, 
                   cors_allowed_origins=config['network']['cors_allowed_origins'], 
                   async_mode=config['network']['socketio_async_mode'])

# Global variables for system status
system_status = {
    'wifi_strength': config['system']['initial_wifi_strength'],
    'battery': config['system']['initial_battery'],
    'depth': config['controls']['depth']['initial_value'],
    'light': config['controls'].get('light_initial_state', False),
    'joystick': {'x': 0, 'y': 0},
    'camera_active': True
}

# System logs list
system_logs = []

def get_wifi_signal_strength():
    """Get current WiFi signal strength on Raspberry Pi"""
    try:
        # Method 1: Use iwconfig
        result = subprocess.run(['iwconfig'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Search for line with signal information
            for line in result.stdout.split('\n'):
                if 'Signal level=' in line:
                    # Extract signal value (format: Signal level=-XX dBm)
                    match = re.search(r'Signal level=(-?\d+)', line)
                    if match:
                        signal_dbm = int(match.group(1))
                        # Convert dBm to percentage (approximate)
                        # -30 dBm = 100%, -90 dBm = 0%
                        percentage = max(0, min(100, (signal_dbm + 90) * 100 // 60))
                        return percentage
        
        # Method 2: Read /proc/net/wireless if iwconfig fails
        with open('/proc/net/wireless', 'r') as f:
            lines = f.readlines()
            if len(lines) > 2:  # Skip header lines
                data = lines[2].split()
                if len(data) >= 3:
                    # Third field is signal quality
                    quality = float(data[2])
                    # Convert quality to percentage (assuming 0-70 scale)
                    percentage = min(100, int((quality / 70) * 100))
                    return percentage
    
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError, IndexError, ValueError) as e:
        logger.warning(f"Could not get real WiFi signal: {e}")
    
    # Fallback: return simulated value
    return None

def add_log(message, level="INFO"):
    """Add a log entry to the system"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        'timestamp': timestamp,
        'level': level,
        'message': message
    }
    system_logs.append(log_entry)
    max_logs = config['system']['max_logs']
    if len(system_logs) > max_logs:  # Keep only the last N logs
        system_logs.pop(0)
    
    # Send log to all connected clients
    socketio.emit('new_log', log_entry)

class CameraStream:
    def __init__(self):
        self.camera = None
        self.running = False
        self.frame = None
        self.lock = threading.Lock()
        self.video_config = config['video']
        
    def start(self):
        """Start camera stream"""
        try:
            source = self.video_config['source']
            
            if source == 'camera':
                camera_index = self.video_config['camera_index']
                self.camera = cv2.VideoCapture(camera_index)
                add_log(f"Attempting to connect camera index: {camera_index}")
                
            elif source == 'url':
                stream_url = self.video_config['stream_url']
                self.camera = cv2.VideoCapture(stream_url)
                add_log(f"Attempting to connect stream URL: {stream_url}")
                
            elif source == 'dummy':
                self.camera = None
                add_log("Dummy mode activated")
            
            # Configure camera if available
            if self.camera and self.camera.isOpened():
                # Configure resolution if specified
                if self.video_config.get('resolution'):
                    width, height = self.video_config['resolution']
                    self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                
                # Configure FPS
                fps = self.video_config['fps']
                self.camera.set(cv2.CAP_PROP_FPS, fps)
                
                add_log(f"Camera started successfully - Source: {source}")
            else:
                # If no physical camera, create dummy frame
                self.create_dummy_frame()
                add_log(f"Could not connect to source '{source}', using dummy frame", "WARNING")
            
            self.running = True
            self.thread = threading.Thread(target=self._capture_frames)
            self.thread.daemon = True
            self.thread.start()
            
        except Exception as e:
            add_log(f"Error starting camera: {str(e)}", "ERROR")
            self.create_dummy_frame()
    
    def create_dummy_frame(self):
        """Create a dummy frame for testing"""
        import numpy as np
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, "Camera Stream", (200, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(frame, datetime.now().strftime("%H:%M:%S"), (250, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        with self.lock:
            self.frame = frame
    
    def _capture_frames(self):
        """Capture frames continuously"""
        fps_delay = 1 / self.video_config['fps']
        while self.running:
            if self.camera and self.camera.isOpened():
                ret, frame = self.camera.read()
                if ret:
                    with self.lock:
                        self.frame = frame
                else:
                    self.create_dummy_frame()
            else:
                self.create_dummy_frame()
            time.sleep(fps_delay)
    
    def get_frame_base64(self):
        """Get current frame in base64"""
        with self.lock:
            if self.frame is not None:
                quality = self.video_config['jpeg_quality']
                _, buffer = cv2.imencode('.jpg', self.frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
                frame_base64 = base64.b64encode(buffer).decode('utf-8')
                return f"data:image/jpeg;base64,{frame_base64}"
        return None
    
    def stop(self):
        """Stop the stream"""
        self.running = False
        if self.camera:
            self.camera.release()

# Camera stream instance
camera_stream = CameraStream()

# Thread to update system status
def update_system_status():
    """Update real WiFi and simulate battery changes"""
    import random
    interval = config['system']['sensor_update_interval']
    
    while True:
        time.sleep(interval)
        
        # Get real WiFi signal if enabled
        if config['system'].get('detect_real_wifi', True):
            real_wifi = get_wifi_signal_strength()
            if real_wifi is not None:
                system_status['wifi_strength'] = real_wifi
            elif config['system']['simulate_sensors']:
                # Only simulate WiFi if real value cannot be obtained
                system_status['wifi_strength'] = max(10, min(100, system_status['wifi_strength'] + random.randint(-5, 5)))
        elif config['system']['simulate_sensors']:
            # Simulate WiFi if real detection is disabled
            system_status['wifi_strength'] = max(10, min(100, system_status['wifi_strength'] + random.randint(-5, 5)))
        
        # Simulate battery changes if enabled
        if config['system']['simulate_sensors']:
            system_status['battery'] = max(0, min(100, system_status['battery'] + random.randint(-2, 1)))
        
        # Send update to clients
        socketio.emit('system_status', {
            'wifi_strength': system_status['wifi_strength'],
            'battery': system_status['battery']
        })

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect(auth):
    from flask import request
    logger.info(f"Client connected: {request.sid}")
    emit('system_status', {
        'wifi_strength': system_status['wifi_strength'],
        'battery': system_status['battery'],
        'depth': system_status['depth'],
        'light': system_status['light']
    })
    emit('logs', system_logs)
    add_log("Client connected")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")
    add_log("Client disconnected")

@socketio.on('joystick_move')
def handle_joystick(data):
    system_status['joystick'] = data
    add_log(f"Joystick: X={data['x']:.2f}, Y={data['y']:.2f}")

@socketio.on('depth_change')
def handle_depth(data):
    system_status['depth'] = data['value']
    add_log(f"Depth changed to: {data['value']}")

@socketio.on('light_toggle')
def handle_light():
    system_status['light'] = not system_status['light']
    status = "ON" if system_status['light'] else "OFF"
    add_log(f"Light {status}")
    emit('light_status', {'status': system_status['light']}, broadcast=True)

@socketio.on('take_photo')
def handle_photo():
    add_log("Photo captured", "SUCCESS")
    emit('photo_taken', {'timestamp': datetime.now().isoformat()})

@socketio.on('get_frame')
def handle_get_frame():
    frame = camera_stream.get_frame_base64()
    if frame:
        emit('video_frame', {'frame': frame})

# Thread to send video frames
def video_stream_thread():
    stream_fps = config['video']['stream_fps']
    delay = 1 / stream_fps
    
    while True:
        if system_status['camera_active']:
            frame = camera_stream.get_frame_base64()
            if frame:
                socketio.emit('video_frame', {'frame': frame})
        time.sleep(delay)


if __name__ == '__main__':
    add_log("Starting server...")
    add_log(f"Configuration loaded: Video={config['video']['source']}, Port={config['server']['port']}")
    
    # Start camera
    camera_stream.start()
    
    # Start system update thread
    system_thread = threading.Thread(target=update_system_status)
    system_thread.daemon = True
    system_thread.start()
    add_log("System monitor started")
    
    video_thread = threading.Thread(target=video_stream_thread)
    video_thread.daemon = True
    video_thread.start()
    
    host = config['server']['host']
    port = config['server']['port']
    debug = config['server']['debug']
    
    add_log(f"Server started at http://{host}:{port}")
    
    try:
        socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        add_log("Closing server...")
        camera_stream.stop()
