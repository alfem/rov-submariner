# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a ROV (Remotely Operated Vehicle) Submariner control system with a Python Flask web server providing real-time video streaming and remote control capabilities through WebSockets.

## Architecture

The project consists of a single-file Flask application (`web_server_app.py`) that:

- Serves a responsive web interface with embedded HTML/CSS/JavaScript
- Provides real-time video streaming using OpenCV and WebSockets  
- Handles ROV control inputs (joystick, depth, lights, photo capture)
- Monitors system status (WiFi strength, battery, sensors)
- Supports configuration via YAML file (`config.yaml`)
- Includes real-time logging with different severity levels

Key components:
- **CameraStream class**: Manages video capture from camera/URL/dummy sources
- **System status monitoring**: WiFi detection via `iwconfig` and `/proc/net/wireless`
- **Real-time communication**: Flask-SocketIO for bidirectional WebSocket communication
- **Configuration system**: YAML-based config with sensible defaults

## Development Commands

### Environment Setup
```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip3 install -r requirements.txt
```

### Running the Application
```bash
# Run with default configuration
python3 web_server_app.py

# Run with custom config file
# Modify config.yaml as needed, then run normally
```

### Dependencies
Install from `requirements.txt`:
- Flask==3.0.0
- Flask-SocketIO==5.3.6
- opencv-python==4.10.0.84
- python-socketio==5.8.0
- eventlet==0.33.3
- numpy<2.0.0
- PyYAML==6.0.1

## Configuration

The application uses `config.yaml` for configuration with these main sections:
- **server**: Host, port, debug settings
- **video**: Camera source, resolution, FPS, quality settings
- **system**: Sensor simulation, WiFi detection, update intervals  
- **controls**: Joystick and depth control parameters
- **logging**: Log level and formatting
- **network**: CORS and SocketIO settings

## Current Development Status

Based on `TO-DO.txt`, pending tasks include:
- Extracting HTML/CSS from Python file to separate files
- Implementing actual photo capture functionality
- Adding real joystick/depth/light hardware interactions
- Adding system shutdown capability
- Converting Spanish comments to English

## Development Notes

- The application is designed to run on Raspberry Pi with real hardware
- Currently uses simulated sensors and dummy video when hardware unavailable
- Single-file architecture makes it easy to deploy but should be refactored for larger features
- Real-time video streaming uses base64 encoding over WebSockets
- System monitoring attempts real WiFi detection on Linux systems