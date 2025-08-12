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