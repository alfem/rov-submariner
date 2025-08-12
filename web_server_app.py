#!/usr/bin/env python3
"""
Web server with WebSockets for remote control with responsive interface
Requires: pip install flask flask-socketio opencv-python pyyaml
"""

from flask import Flask, render_template_string
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
    return render_template_string(HTML_TEMPLATE)

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

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.0/socket.io.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #87ceeb 0%, #b0e0e6 100%);
            min-height: 100vh;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 10px;
        }
        
        .tab-container {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 8px 32px rgba(31, 38, 135, 0.37);
        }
        
        .tabs {
            display: flex;
            background: rgba(255, 255, 255, 0.1);
        }
        
        .tab {
            flex: 1;
            padding: 15px 20px;
            background: transparent;
            border: none;
            color: white;
            cursor: pointer;
            transition: all 0.3s ease;
            font-size: 16px;
            font-weight: 500;
        }
        
        .tab.active {
            background: rgba(255, 255, 255, 0.2);
            border-bottom: 3px solid #00ff88;
        }
        
        .tab-content {
            display: none;
            padding: 20px;
            color: white;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .status-indicators {
            display: flex;
            gap: 15px;
            margin-bottom: 20px;
            flex-wrap: nowrap;
        }
        
        .indicator {
            background: rgba(255, 255, 255, 0.1);
            padding: 12px 15px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 1;
            min-width: 0;
        }
        
        .indicator-icon {
            width: 25px;
            height: 25px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            flex-shrink: 0;
        }
        
        .indicator-text {
            flex: 1;
            min-width: 0;
        }
        
        .indicator-label {
            font-size: 12px;
            opacity: 0.8;
            white-space: nowrap;
        }
        
        .indicator-value {
            font-size: 16px;
            font-weight: bold;
            white-space: nowrap;
        }
        
        .video-container {
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 20px;
            position: relative;
            aspect-ratio: 16/9;
        }
        
        #videoStream {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .controls-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        
        .joystick-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 10px;
        }
        
        .joystick {
            width: 150px;
            height: 150px;
            border: 3px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            position: relative;
            background: rgba(255, 255, 255, 0.1);
            cursor: pointer;
        }
        
        .joystick-knob {
            width: 40px;
            height: 40px;
            background: #00ff88;
            border-radius: 50%;
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            transition: all 0.1s ease;
            box-shadow: 0 4px 15px rgba(0, 255, 136, 0.4);
        }
        
        .depth-container {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .depth-slider {
            -webkit-appearance: none;
            width: 100%;
            height: 8px;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.3);
            outline: none;
        }
        
        .depth-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 25px;
            height: 25px;
            border-radius: 50%;
            background: #00ff88;
            cursor: pointer;
            box-shadow: 0 4px 15px rgba(0, 255, 136, 0.4);
        }
        
        .button-group {
            display: flex;
            gap: 15px;
            justify-content: center;
            flex-wrap: wrap;
        }
        
        .control-button {
            padding: 15px 30px;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
            text-transform: uppercase;
            letter-spacing: 1px;
            min-width: 120px;
        }
        
        .light-btn {
            background: #ff6b6b;
            color: white;
        }
        
        .light-btn.active {
            background: #51cf66;
        }
        
        .photo-btn {
            background: #4ecdc4;
            color: white;
        }
        
        .control-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.2);
        }
        
        .system-info, .log-container {
            background: rgba(255, 255, 255, 0.1);
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        
        .log-entries {
            max-height: 400px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            line-height: 1.4;
        }
        
        .log-entry {
            padding: 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            gap: 10px;
        }
        
        .log-timestamp {
            color: #64b5f6;
            min-width: 70px;
        }
        
        .log-level {
            min-width: 60px;
            font-weight: bold;
        }
        
        .log-level.INFO { color: #81c784; }
        .log-level.WARNING { color: #ffb74d; }
        .log-level.ERROR { color: #e57373; }
        .log-level.SUCCESS { color: #00ff88; }
        
        @media (max-width: 768px) {
            .controls-grid {
                grid-template-columns: 1fr;
            }
            
            .status-indicators {
                gap: 10px;
                margin-bottom: 15px;
            }
            
            .indicator {
                padding: 10px 12px;
                gap: 6px;
            }
            
            .indicator-icon {
                width: 20px;
                height: 20px;
                font-size: 14px;
            }
            
            .indicator-label {
                font-size: 11px;
            }
            
            .indicator-value {
                font-size: 14px;
            }
            
            .joystick {
                width: 120px;
                height: 120px;
            }
            
            .joystick-knob {
                width: 30px;
                height: 30px;
            }
            
            .button-group {
                flex-direction: column;
            }
            
            .control-button {
                min-width: unset;
            }
        }
        
        @media (max-width: 480px) {
            .container {
                padding: 5px;
            }
            
            .tab {
                padding: 12px 10px;
                font-size: 14px;
            }
            
            .tab-content {
                padding: 15px;
            }
            
            .status-indicators {
                gap: 8px;
            }
            
            .indicator {
                padding: 8px 10px;
                gap: 5px;
            }
            
            .indicator-icon {
                width: 18px;
                height: 18px;
                font-size: 12px;
            }
            
            .indicator-label {
                font-size: 10px;
            }
            
            .indicator-value {
                font-size: 13px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="tab-container">
            <div class="tabs">
                <button class="tab active" onclick="switchTab('control')">Control</button>
                <button class="tab" onclick="switchTab('system')">System</button>
                <button class="tab" onclick="switchTab('log')">Log</button>
            </div>
            
            <div id="control" class="tab-content active">
                <div class="status-indicators">
                    <div class="indicator">
                        <div class="indicator-icon">📶</div>
                        <div class="indicator-text">
                            <div class="indicator-label">WiFi Strength</div>
                            <div class="indicator-value" id="wifiStrength">--</div>
                        </div>
                    </div>
                    <div class="indicator">
                        <div class="indicator-icon">🔋</div>
                        <div class="indicator-text">
                            <div class="indicator-label">Battery</div>
                            <div class="indicator-value" id="battery">--</div>
                        </div>
                    </div>
                </div>
                
                <div class="video-container">
                    <img id="videoStream" alt="Video Stream" />
                </div>
                
                <div class="controls-grid">
                    <div class="joystick-container">
                        <h3>Joystick</h3>
                        <div class="joystick" id="joystick">
                            <div class="joystick-knob" id="joystickKnob"></div>
                        </div>
                        <div>X: <span id="joystickX">0</span>, Y: <span id="joystickY">0</span></div>
                    </div>
                    
                    <div class="depth-container">
                        <h3>Depth: <span id="depthValue">0</span></h3>
                        <input type="range" class="depth-slider" id="depthSlider" min="0" max="100" value="0">
                    </div>
                </div>
                
                <div class="button-group">
                    <button class="control-button light-btn" id="lightBtn" onclick="toggleLight()">Light</button>
                    <button class="control-button photo-btn" onclick="takePhoto()">Photo</button>
                </div>
            </div>
            
            <div id="system" class="tab-content">
                <div class="system-info">
                    <h2>System Information</h2>
                    <p><strong>Status:</strong> Connected</p>
                    <p><strong>WiFi:</strong> <span id="systemWifi">--</span>%</p>
                    <p><strong>Battery:</strong> <span id="systemBattery">--</span>%</p>
                    <p><strong>Depth:</strong> <span id="systemDepth">--</span></p>
                    <p><strong>Light:</strong> <span id="systemLight">OFF</span></p>
                </div>
            </div>
            
            <div id="log" class="tab-content">
                <div class="log-container">
                    <h2>System Logs</h2>
                    <div class="log-entries" id="logEntries"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        let isDragging = false;
        let joystickCenter = {x: 0, y: 0};
        
        // Initialization
        document.addEventListener('DOMContentLoaded', function() {
            initializeJoystick();
            setupDepthSlider();
        });
        
        // Tab handling
        function switchTab(tabName) {
            document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            
            event.target.classList.add('active');
            document.getElementById(tabName).classList.add('active');
        }
        
        // Initialize joystick
        function initializeJoystick() {
            const joystick = document.getElementById('joystick');
            const knob = document.getElementById('joystickKnob');
            const rect = joystick.getBoundingClientRect();
            joystickCenter = {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
            };
            
            joystick.addEventListener('mousedown', startDrag);
            joystick.addEventListener('touchstart', startDrag);
            document.addEventListener('mousemove', drag);
            document.addEventListener('touchmove', drag);
            document.addEventListener('mouseup', stopDrag);
            document.addEventListener('touchend', stopDrag);
            
            // Recalculate center on resize
            window.addEventListener('resize', () => {
                const rect = joystick.getBoundingClientRect();
                joystickCenter = {
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2
                };
            });
        }
        
        function startDrag(e) {
            isDragging = true;
            const joystick = document.getElementById('joystick');
            const rect = joystick.getBoundingClientRect();
            joystickCenter = {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
            };
        }
        
        function drag(e) {
            if (!isDragging) return;
            
            e.preventDefault();
            const clientX = e.clientX || (e.touches && e.touches[0].clientX);
            const clientY = e.clientY || (e.touches && e.touches[0].clientY);
            
            const deltaX = clientX - joystickCenter.x;
            const deltaY = clientY - joystickCenter.y;
            const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);
            const maxDistance = 55;
            
            let x = deltaX;
            let y = deltaY;
            
            if (distance > maxDistance) {
                x = (deltaX / distance) * maxDistance;
                y = (deltaY / distance) * maxDistance;
            }
            
            const knob = document.getElementById('joystickKnob');
            knob.style.transform = `translate(${x - 20}px, ${y - 20}px)`;
            
            const normalizedX = x / maxDistance;
            const normalizedY = -y / maxDistance;
            
            document.getElementById('joystickX').textContent = normalizedX.toFixed(2);
            document.getElementById('joystickY').textContent = normalizedY.toFixed(2);
            
            socket.emit('joystick_move', {x: normalizedX, y: normalizedY});
        }
        
        function stopDrag() {
            isDragging = false;
            const knob = document.getElementById('joystickKnob');
            knob.style.transform = 'translate(-20px, -20px)';
            
            document.getElementById('joystickX').textContent = '0.00';
            document.getElementById('joystickY').textContent = '0.00';
            
            socket.emit('joystick_move', {x: 0, y: 0});
        }
        
        // Configure depth slider
        function setupDepthSlider() {
            const slider = document.getElementById('depthSlider');
            slider.addEventListener('input', function() {
                const value = this.value;
                document.getElementById('depthValue').textContent = value;
                document.getElementById('systemDepth').textContent = value;
                socket.emit('depth_change', {value: parseInt(value)});
            });
        }
        
        // Light button
        function toggleLight() {
            socket.emit('light_toggle');
        }
        
        // Photo button
        function takePhoto() {
            socket.emit('take_photo');
        }
        
        // Socket.IO events
        socket.on('connect', function() {
            console.log('Connected to server');
        });
        
        socket.on('system_status', function(data) {
            document.getElementById('wifiStrength').textContent = data.wifi_strength + '%';
            document.getElementById('battery').textContent = data.battery + '%';
            document.getElementById('systemWifi').textContent = data.wifi_strength;
            document.getElementById('systemBattery').textContent = data.battery;
            
            if (data.depth !== undefined) {
                document.getElementById('depthSlider').value = data.depth;
                document.getElementById('depthValue').textContent = data.depth;
                document.getElementById('systemDepth').textContent = data.depth;
            }
        });
        
        socket.on('light_status', function(data) {
            const lightBtn = document.getElementById('lightBtn');
            const systemLight = document.getElementById('systemLight');
            
            if (data.status) {
                lightBtn.classList.add('active');
                systemLight.textContent = 'ON';
            } else {
                lightBtn.classList.remove('active');
                systemLight.textContent = 'OFF';
            }
        });
        
        socket.on('video_frame', function(data) {
            document.getElementById('videoStream').src = data.frame;
        });
        
        socket.on('new_log', function(log) {
            addLogEntry(log);
        });
        
        socket.on('logs', function(logs) {
            const logEntries = document.getElementById('logEntries');
            logEntries.innerHTML = '';
            logs.forEach(log => addLogEntry(log));
        });
        
        socket.on('photo_taken', function(data) {
            alert('Photo captured!');
        });
        
        function addLogEntry(log) {
            const logEntries = document.getElementById('logEntries');
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `
                <span class="log-timestamp">${log.timestamp}</span>
                <span class="log-level ${log.level}">${log.level}</span>
                <span class="log-message">${log.message}</span>
            `;
            logEntries.appendChild(entry);
            logEntries.scrollTop = logEntries.scrollHeight;
        }
    </script>
</body>
</html>
'''

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
