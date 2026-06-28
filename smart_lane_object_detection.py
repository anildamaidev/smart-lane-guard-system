import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog
import threading
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from ultralytics import YOLO
from playsound import playsound
import os
import time
import pyttsx3
import tkinter.messagebox
import urllib.request
import webbrowser
import json
from datetime import datetime
import math
import winsound
import wave
import struct

# Global variables
video_path = None
ip_camera_url = None
is_running = False
car_data = {}
frame_count = 0
save_snapshots = True
send_email_alerts = True

# Global variables for lane memory and alerts
prev_left_line = None
prev_right_line = None
frame_memory = 10
startup_frames = 30
current_frame = 0
last_alert_time = 0
alert_cooldown = 3.0
current_speed = 0  # Added for speed-based sensitivity

# Simplified warning sounds setup
WARNING_LEVELS = {
    'MILD': {'color': (0, 255, 255), 'frequency': 800, 'threshold': 0.15},
    'MODERATE': {'color': (0, 165, 255), 'frequency': 1000, 'threshold': 0.25},
    'SEVERE': {'color': (0, 0, 255), 'frequency': 1200, 'threshold': 0.35}
}

# Initialize pyttsx3
engine = pyttsx3.init()
engine_running = False

# Voice Alerts
def voice_alert(message):
    global engine_running
    if not engine_running:
        engine_running = True
        engine.say(message)
        engine.runAndWait()
        engine_running = False

# Toggle Snapshot Save
def toggle_snapshot_save():
    global save_snapshots
    save_snapshots = not save_snapshots
    status = "enabled" if save_snapshots else "disabled"
    print(f"Snapshot saving {status}")

# Toggle Email Alerts
def toggle_email_alerts():
    global send_email_alerts
    send_email_alerts = not send_email_alerts
    status = "enabled" if send_email_alerts else "disabled"
    print(f"Email alerts {status}")

# Email Alert with Snapshot
def send_email_alert(message, snapshot_path):
    if not send_email_alerts:
        return

    sender_email = "testlane1210@gmail.com"
    receiver_email = "anildamai283@gmail.com"
    password = "velm chas mkwc ecsq"

    try:
        # Check internet connectivity first
        try:
            urllib.request.urlopen('https://www.google.com', timeout=1)
        except:
            print("Email failed: No internet connection")
            return

        msg = MIMEMultipart()
        msg["Subject"] = "Lane/Collision Alert 🚨"
        msg["From"] = sender_email
        msg["To"] = receiver_email

        msg.attach(MIMEText(message, "plain"))

        if save_snapshots and snapshot_path and os.path.exists(snapshot_path):
            try:
                with open(snapshot_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(snapshot_path)}")
                    msg.attach(part)
            except Exception as e:
                print(f"Warning: Could not attach snapshot: {e}")

        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
                server.starttls()
                server.login(sender_email, password)
                server.sendmail(sender_email, receiver_email, msg.as_string())
            print("Email alert with snapshot sent!")
        except smtplib.SMTPAuthenticationError:
            print("Email failed: Authentication error. Please check your email credentials.")
        except smtplib.SMTPConnectError:
            print("Email failed: Could not connect to SMTP server. Check your internet connection.")
        except smtplib.SMTPException as e:
            print(f"Email failed: SMTP error: {e}")
    except Exception as e:
        print(f"Email failed: {e}")

# Save detection data to DB
def save_to_database(car_id, speed, distance):
    conn = sqlite3.connect("detection_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id TEXT,
            speed REAL,
            distance REAL
        )
    """)
    cursor.execute("INSERT INTO detections (car_id, speed, distance) VALUES (?, ?, ?)", 
                   (car_id, speed, distance))
    conn.commit()
    conn.close()

def get_speed_adjusted_threshold(base_threshold, speed):
    """Adjust sensitivity based on speed"""
    if speed < 30:  # City driving
        return base_threshold * 1.2  # Less sensitive
    elif speed > 60:  # Highway driving
        return base_threshold * 0.8  # More sensitive
    return base_threshold

# Night mode detection parameters
is_night_mode = False
NIGHT_THRESHOLD = 100  # Brightness threshold for night detection

def detect_lighting_conditions(frame):
    """Detect if the frame is captured in night conditions"""
    global is_night_mode
    
    # Convert to grayscale and calculate average brightness
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    avg_brightness = np.mean(gray)
    
    # Update night mode status
    is_night_mode = avg_brightness < NIGHT_THRESHOLD
    return is_night_mode

def adjust_frame_for_night(frame):
    """Enhance frame for better night vision"""
    if not is_night_mode:
        return frame
        
    # Apply CLAHE for better contrast
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    l = clahe.apply(l)
    
    # Merge channels
    lab = cv2.merge((l,a,b))
    enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    
    # Apply slight brightness increase
    brightness_factor = 1.2
    enhanced = cv2.convertScaleAbs(enhanced, alpha=brightness_factor, beta=10)
    
    return enhanced

# Data logging
class DrivingLogger:
    def __init__(self):
        self.log_file = f"driving_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        self.session_data = {
            'start_time': datetime.now().isoformat(),
            'lane_departures': [],
            'night_mode_activations': [],
            'total_driving_time': 0,
            'total_departures': 0,
            'speed_data': []
        }
        
    def log_lane_departure(self, warning_level, direction, speed):
        """Log a lane departure event"""
        event = {
            'timestamp': datetime.now().isoformat(),
            'warning_level': warning_level,
            'direction': direction,
            'speed': speed,
            'is_night_mode': is_night_mode
        }
        self.session_data['lane_departures'].append(event)
        self.session_data['total_departures'] += 1
        self.save_log()
        
    def log_night_mode(self, is_active):
        """Log night mode activation/deactivation"""
        event = {
            'timestamp': datetime.now().isoformat(),
            'is_active': is_active
        }
        self.session_data['night_mode_activations'].append(event)
        self.save_log()
        
    def log_speed(self, speed):
        """Log current speed"""
        self.session_data['speed_data'].append({
            'timestamp': datetime.now().isoformat(),
            'speed': speed
        })
        
    def save_log(self):
        """Save current session data to file"""
        self.session_data['total_driving_time'] = (
            datetime.now() - datetime.fromisoformat(self.session_data['start_time'])
        ).total_seconds()
        
        with open(self.log_file, 'w') as f:
            json.dump(self.session_data, f, indent=4)
            
    def generate_report(self):
        """Generate a summary report of the driving session"""
        report = {
            'total_driving_time_minutes': round(self.session_data['total_driving_time'] / 60, 2),
            'total_departures': self.session_data['total_departures'],
            'departures_by_severity': {},
            'night_mode_percentage': 0,
            'average_speed': 0
        }
        
        # Calculate departures by severity
        for departure in self.session_data['lane_departures']:
            level = departure['warning_level']
            report['departures_by_severity'][level] = report['departures_by_severity'].get(level, 0) + 1
            
        # Calculate night mode percentage
        night_mode_time = sum(1 for event in self.session_data['night_mode_activations'] if event['is_active'])
        if self.session_data['night_mode_activations']:
            report['night_mode_percentage'] = (night_mode_time / len(self.session_data['night_mode_activations'])) * 100
            
        # Calculate average speed
        speeds = [event['speed'] for event in self.session_data['speed_data']]
        if speeds:
            report['average_speed'] = sum(speeds) / len(speeds)
            
        return report

# Initialize logger
driving_logger = DrivingLogger()

# Curve detection parameters
CURVE_THRESHOLD = 0.1  # Threshold for curve detection
curve_memory = []
MAX_CURVE_MEMORY = 10

def detect_curve(left_line, right_line):
    """Detect if the road is curved and determine the direction"""
    if not left_line or not right_line:
        return None, 0
        
    # Calculate the average slope of both lines
    left_slope = (left_line[3] - left_line[1]) / (left_line[2] - left_line[0])
    right_slope = (right_line[3] - right_line[1]) / (right_line[2] - right_line[0])
    
    # Calculate slope difference
    slope_diff = abs(abs(left_slope) - abs(right_slope))
    
    # Store in curve memory
    curve_memory.append(slope_diff)
    if len(curve_memory) > MAX_CURVE_MEMORY:
        curve_memory.pop(0)
    
    # Average slope difference over recent frames
    avg_slope_diff = sum(curve_memory) / len(curve_memory)
    
    if avg_slope_diff > CURVE_THRESHOLD:
        # Determine curve direction based on relative slopes
        if abs(left_slope) > abs(right_slope):
            return 'right', avg_slope_diff
        else:
            return 'left', avg_slope_diff
    
    return None, 0

def adjust_roi_for_curve(image, curve_direction, curve_strength):
    """Adjust the ROI polygon based on curve detection"""
    height, width = image.shape[:2]
    
    # Base ROI points
    bottom_left = width * 0.15
    bottom_right = width * 0.85
    top_left = width * 0.45
    top_right = width * 0.55
    top_height = height * 0.55
    
    # Adjust ROI based on curve direction and strength
    if curve_direction == 'left':
        # Shift ROI to the left for left curves
        shift = min(width * 0.15 * curve_strength, width * 0.2)
        top_left -= shift
        top_right -= shift/2
    elif curve_direction == 'right':
        # Shift ROI to the right for right curves
        shift = min(width * 0.15 * curve_strength, width * 0.2)
        top_left += shift/2
        top_right += shift
    
    # Create the ROI polygon
    polygon = np.array([[
        (bottom_left, height),
        (top_left, top_height),
        (top_right, top_height),
        (bottom_right, height)
    ]], dtype=np.int32)
    
    return polygon

def detect_lanes(image):
    global prev_left_line, prev_right_line, current_frame, last_alert_time, current_speed, is_night_mode, frame_memory
    
    # Detect lighting conditions and log if changed
    new_night_mode = detect_lighting_conditions(image)
    if new_night_mode != is_night_mode:
        driving_logger.log_night_mode(new_night_mode)
    
    if is_night_mode:
        image = adjust_frame_for_night(image)
    
    height, width = image.shape[:2]
    current_frame += 1
    
    # Edge detection with lighting condition adjustment
    if is_night_mode:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 30, 100)
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)
    
    # Detect curve and adjust ROI
    curve_direction, curve_strength = detect_curve(prev_left_line, prev_right_line)
    roi_polygon = adjust_roi_for_curve(image, curve_direction, curve_strength)
    
    # Apply ROI mask
    mask = np.zeros_like(edges)
    cv2.fillPoly(mask, roi_polygon, 255)
    masked_edges = cv2.bitwise_and(edges, mask)
    
    # Adjust Hough parameters based on curve detection
    if curve_direction:
        # More lenient parameters for curved roads
        lines = cv2.HoughLinesP(
            masked_edges,
            rho=1,
            theta=np.pi/180,
            threshold=25,  # Lower threshold for curves
            minLineLength=30,  # Shorter minimum line length
            maxLineGap=60  # Larger max gap for curves
        )
    else:
        # Standard parameters for straight roads
        lines = cv2.HoughLinesP(
            masked_edges,
            rho=1,
            theta=np.pi/180,
            threshold=30,
            minLineLength=40,
            maxLineGap=50
        )
    
    left_lines = []
    right_lines = []
    
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 - x1 == 0:  # Avoid division by zero
                continue
                
            slope = (y2 - y1) / (x2 - x1)
            length = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            
            # Improved slope filtering
            if abs(slope) < 0.3:  # Filter out horizontal lines
                continue
            if length < 40:       # Filter out short lines
                continue
                
            # Improved lane classification
            if slope < 0 and x1 < width * 0.5:  # Left lane
                left_lines.append((x1, y1, x2, y2, abs(slope)))
            elif slope > 0 and x1 > width * 0.4:  # Right lane
                right_lines.append((x1, y1, x2, y2, abs(slope)))

    def average_line(lines, prev_line):
        if not lines:
            return prev_line
            
        # Sort lines by slope to get the most vertical ones
        lines.sort(key=lambda x: x[4], reverse=True)
        best_lines = lines[:3]  # Take top 3 most vertical lines
            
        x_coords = []
        y_coords = []
        for x1, y1, x2, y2, _ in best_lines:
            x_coords.extend([x1, x2])
            y_coords.extend([y1, y2])
            
        if len(x_coords) < 2:
            return prev_line
            
        poly = np.polyfit(y_coords, x_coords, deg=1)
        y1 = height
        y2 = int(height * 0.6)  # Extend lines higher up
        x1 = int(np.polyval(poly, y1))
        x2 = int(np.polyval(poly, y2))
        
        new_line = (x1, y1, x2, y2)
        
        # Smoother transition with previous line
        if prev_line is not None:
            x1 = int(0.8 * prev_line[0] + 0.2 * x1)
            x2 = int(0.8 * prev_line[2] + 0.2 * x2)
            new_line = (x1, y1, x2, y2)
            
        return new_line

    # Draw the lanes with memory
    line_image = np.zeros_like(image)
    
    # Update left and right lines with memory
    left_avg = average_line(left_lines, prev_left_line)
    right_avg = average_line(right_lines, prev_right_line)
    
    # Store current lines for next frame
    prev_left_line = left_avg if left_avg is not None else prev_left_line
    prev_right_line = right_avg if right_avg is not None else prev_right_line
    
    # Clear memory after too many frames without detection
    if not left_lines:
        frame_memory -= 1
        if frame_memory <= 0:
            prev_left_line = None
            frame_memory = 10
    else:
        frame_memory = 10
        
    if not right_lines:
        frame_memory -= 1
        if frame_memory <= 0:
            prev_right_line = None
            frame_memory = 10
    else:
        frame_memory = 10
    
    # Draw the lanes with improved visibility
    if prev_left_line:
        cv2.line(line_image, (prev_left_line[0], prev_left_line[1]), 
                (prev_left_line[2], prev_left_line[3]), (0, 255, 255), 8)  # Yellow color
    if prev_right_line:
        cv2.line(line_image, (prev_right_line[0], prev_right_line[1]), 
                (prev_right_line[2], prev_right_line[3]), (0, 255, 255), 8)  # Yellow color

    # Lane Departure Detection with speed-based sensitivity
    if current_frame > startup_frames and prev_left_line and prev_right_line:
        car_x = width // 2
        car_y = height - 20
        
        left_x = int(prev_left_line[0] + (prev_left_line[2] - prev_left_line[0]) * 
                    (car_y - prev_left_line[1]) / (prev_left_line[3] - prev_left_line[1]))
        right_x = int(prev_right_line[0] + (prev_right_line[2] - prev_right_line[0]) * 
                     (car_y - prev_right_line[1]) / (prev_right_line[3] - prev_right_line[1]))
        
        lane_center = (left_x + right_x) // 2
        lane_width = right_x - left_x
        
        # Calculate relative deviation (as percentage of lane width)
        center_deviation = abs(car_x - lane_center) / lane_width
        
        # Get warning level based on deviation
        warning_level = None
        for level, params in WARNING_LEVELS.items():
            adjusted_threshold = get_speed_adjusted_threshold(params['threshold'], current_speed)
            if center_deviation > adjusted_threshold:
                warning_level = level
        
        if warning_level and (time.time() - last_alert_time) >= alert_cooldown:
            params = WARNING_LEVELS[warning_level]
            
            # Visual warning based on severity
            cv2.putText(image, f"{warning_level} DEPARTURE WARNING!", 
                      (width//2 - 200, 100),
                      cv2.FONT_HERSHEY_SIMPLEX, 1.5, params['color'], 3)
            
            # Draw warning borders with severity color
            cv2.line(image, (0, 0), (width, 0), params['color'], 10)
            cv2.line(image, (0, height), (width, height), params['color'], 10)
            
            # Draw deviation indicator
            cv2.circle(image, (car_x, car_y), 10, params['color'], -1)
            cv2.line(image, (lane_center, car_y), (car_x, car_y), params['color'], 2)
            
            # Save snapshot with severity level
            if save_snapshots:
                snapshot_path = f"lane_departure_{warning_level}_{time.time()}.jpg"
                cv2.imwrite(snapshot_path, image)
            
            # Direction-specific alert message
            if car_x < lane_center:
                alert_message = f"{warning_level} Warning! Drifting Left"
            else:
                alert_message = f"{warning_level} Warning! Drifting Right"
            
            # Trigger alerts
            threading.Thread(target=play_warning_sound, args=(warning_level,)).start()
            threading.Thread(target=voice_alert, args=(alert_message,)).start()
            threading.Thread(target=send_email_alert, 
                           args=(f"Lane Departure Warning! {alert_message}", 
                                 snapshot_path if save_snapshots else None)).start()
            
            last_alert_time = time.time()

            # Log the lane departure
            departure_direction = "Left" if car_x < lane_center else "Right"
            driving_logger.log_lane_departure(warning_level, departure_direction, current_speed)

    # Log speed periodically (every 30 frames)
    if current_frame % 30 == 0:
        driving_logger.log_speed(current_speed)

    # Add curve indication to the frame
    if curve_direction:
        cv2.putText(image, f"Curve: {curve_direction.upper()}", 
                    (width-200, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                    1, (0, 255, 0), 2)

    # Combine with original image
    result = cv2.addWeighted(image, 0.8, line_image, 1.0, 0)
    
    return result

# Estimate Distance
def estimate_distance(height_in_pixels, frame_height):
    focal_length = 800
    car_height = 1.5
    return (focal_length * car_height) / max(height_in_pixels, 1)

# Speed Calculation
def calculate_speed(positions, frame_count):
    if len(positions) < 2:
        return 0
    dx = abs(positions[-1][0] - positions[0][0])
    dy = abs(positions[-1][1] - positions[0][1])
    dist = np.sqrt(dx*2 + dy*2)
    time = frame_count / 30
    return (dist / time) * 0.01  # km/h

# Object Detection
def detect_objects(image, model, classes, frame_count):
    global car_data
    results = model(image)
    for result in results:
        boxes = result.boxes.cpu().numpy()
        for box in boxes:
            x1, y1, x2, y2, conf, class_id = box.xyxy[0].tolist() + [box.conf[0].item(), box.cls[0].item()]
            if classes[int(class_id)] == 'car' and conf > 0.3:
                x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
                if x2 - x1 < 20 or y2 - y1 < 20:
                    continue
                distance = estimate_distance(y2 - y1, image.shape[0])
                car_id = f"{x1}_{y1}"
                if car_id not in car_data:
                    car_data[car_id] = {"positions": [], "speed": 0}
                car_data[car_id]["positions"].append((x1, y1))
                speed = 0
                if len(car_data[car_id]["positions"]) > 2:
                    speed = calculate_speed(car_data[car_id]["positions"], frame_count)
                save_to_database(car_id, speed, distance)

                # Check if car is in the same lane
                mid_x = (x1 + x2) // 2
                frame_mid = image.shape[1] // 2
                lane_width = image.shape[1] // 3  # Define lane width
                same_lane = abs(mid_x - frame_mid) < lane_width // 2  # Car must be in center lane

                if frame_count > 10 and same_lane and distance < 12:
                    cv2.putText(image, "Car Ahead Alert!", (x1, y1 - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    snapshot_path = f"alert_frame_{time.time()}.jpg"
                    if save_snapshots:
                        cv2.imwrite(snapshot_path, image)
                    threading.Thread(target=playsound, args=("warning_sound.mp3",)).start()
                    threading.Thread(target=send_email_alert, args=(f"Car ahead and too close! ID: {car_id}", snapshot_path)).start()
                    threading.Thread(target=voice_alert, args=("Warning! Car too close",)).start()
                else:
                    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)

                label = f"Car {conf:.2f} Dist:{distance:.1f}m Spd:{speed:.1f}km/h"
                cv2.putText(image, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
    return image

# Add at the top with other imports
import urllib.request
import time

def test_ip_camera_connection(url):
    """Test if IP camera is accessible"""
    try:
        # Try to access the IP camera stream
        stream = urllib.request.urlopen(url)
        if stream.getcode() == 200:
            return True
        return False
    except Exception as e:
        print(f"Connection test failed: {str(e)}")
        return False

def select_ip_camera():
    global ip_camera_url
    dialog = tk.Toplevel()
    dialog.title("Hotspot Camera Setup")
    dialog.geometry("450x350")
    
    # Guide text
    guide_text = """Quick Hotspot Setup:
1. Connect laptop to phone's hotspot
2. On your phone:
   - Open IP Webcam app
   - Click 'Start server'
   - Look at the IP address shown
3. Usually it will be: http://192.168.43.1:8080"""
    
    tk.Label(dialog, text=guide_text, justify='left', fg='blue').pack(pady=10, padx=20, anchor='w')
    
    # Connection inputs
    ip_frame = tk.Frame(dialog)
    ip_frame.pack(pady=5, padx=20, fill='x')
    tk.Label(ip_frame, text="IP Address:").pack(side='left')
    ip_entry = tk.Entry(ip_frame)
    ip_entry.pack(side='left', padx=5, expand=True, fill='x')
    ip_entry.insert(0, "192.168.128.133")
    
    port_frame = tk.Frame(dialog)
    port_frame.pack(pady=5, padx=20, fill='x')
    tk.Label(port_frame, text="Port:").pack(side='left')
    port_entry = tk.Entry(port_frame)
    port_entry.pack(side='left', padx=5, expand=True, fill='x')
    port_entry.insert(0, "8080")
    
    # Status label
    status_label = tk.Label(dialog, text="Ready to connect", fg="blue")
    status_label.pack(pady=5)
    
    def update_status(message, color="blue"):
        status_label.config(text=message, fg=color)
        dialog.update()
    
    def quick_connect():
        ip = ip_entry.get().strip()
        port = port_entry.get().strip()
        url = f"http://{ip}:{port}"
        
        try:
            update_status("Testing connection...", "blue")
            response = urllib.request.urlopen(url, timeout=1)
            if response.getcode() == 200:
                update_status("Server found! Ready to connect.", "green")
                return True
        except Exception as e:
            update_status("Cannot reach server. Check IP Webcam app.", "red")
            return False
    
    def test_connection():
        def run_test():
            buttons = [test_btn, connect_btn, cancel_btn]
            for btn in buttons:
                btn.config(state='disabled')
            
            result = quick_connect()
            
            for btn in buttons:
                btn.config(state='normal')
        
        threading.Thread(target=run_test, daemon=True).start()
    
    def connect():
        ip = ip_entry.get().strip()
        port = port_entry.get().strip()
        
        if not ip or not port:
            tk.messagebox.showerror("Error", "Please enter IP and Port")
            return
            
        global ip_camera_url
        ip_camera_url = f"http://{ip}:{port}/video"
        
        try:
            update_status("Initializing camera...", "blue")
            
            def try_connect():
                cap = cv2.VideoCapture(ip_camera_url)
                if not cap.isOpened():
                    raise Exception("Could not connect to camera")
                
                ret, frame = cap.read()
                cap.release()
                
                if not ret or frame is None:
                    raise Exception("Could not get video feed")
                
                return True
            
            connect_thread = threading.Thread(target=try_connect)
            connect_thread.daemon = True
            connect_thread.start()
            
            connect_thread.join(timeout=2.0)
            if connect_thread.is_alive():
                raise Exception("Connection timeout")
            
            update_status("Successfully connected!", "green")
            tk.messagebox.showinfo("Success", 
                "Connected to phone camera!\n\n"
                "1. Keep IP Webcam app open\n"
                "2. Click 'Start Processing'")
            dialog.destroy()
            
        except Exception as e:
            update_status("Connection failed. Check IP Webcam app.", "red")
            tk.messagebox.showerror("Error", 
                "Could not connect to camera.\n\n"
                "Please check:\n"
                "1. IP Webcam app is running\n"
                "2. Start server is pressed\n"
                "3. Phone and laptop on same hotspot\n"
                "4. Try refreshing the IP Webcam app")
    
    # Buttons
    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=10)
    
    test_btn = tk.Button(btn_frame, text="Test Connection", command=test_connection)
    test_btn.pack(side='left', padx=5)
    
    connect_btn = tk.Button(btn_frame, text="Connect", command=connect)
    connect_btn.pack(side='left', padx=5)
    
    cancel_btn = tk.Button(btn_frame, text="Cancel", command=dialog.destroy)
    cancel_btn.pack(side='left', padx=5)
    
    # Help text
    help_text = "Tip: If it doesn't connect, try refreshing the IP Webcam app"
    tk.Label(dialog, text=help_text, fg='gray').pack(pady=5)
    
    dialog.transient(root)
    dialog.grab_set()
    dialog.focus_set()

def process_video():
    global video_path, ip_camera_url, is_running, car_data, frame_count
    
    if video_path is None and ip_camera_url is None:
        tk.messagebox.showerror("Error", "No video source selected.")
        return
        
    try:
        # Initialize video capture
        source = ip_camera_url if ip_camera_url else video_path
        cap = cv2.VideoCapture(source)
        
        if not cap.isOpened():
            raise Exception("Could not open video source")
        
        # Optimize camera settings for IP webcam
        if ip_camera_url:
            # Lower resolution for better performance
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            # Minimize latency
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # Set FPS
            cap.set(cv2.CAP_PROP_FPS, 30)
        
        # Create named window once
        window_name = "Lane & Object Detection"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 800, 600)
        
        # Load YOLO model
        model = YOLO("yolov8n.pt")
        with open("coco.names", "r") as f:
            classes = [line.strip() for line in f.readlines()]
            
        frame_count = 0
        skip_frames = 0
        last_process_time = time.time()
        
        # Initialize display frame
        display_frame = None
        
        while is_running:
            try:
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                
                frame_count += 1
                current_time = time.time()
                
                # Process every 2nd frame for IP camera to reduce load
                if ip_camera_url and skip_frames < 1:
                    skip_frames += 1
                    if display_frame is not None:
                        cv2.imshow(window_name, display_frame)
                    else:
                        cv2.imshow(window_name, frame)
                    continue
                
                skip_frames = 0
                
                # Resize frame for faster processing
                if ip_camera_url:
                    frame = cv2.resize(frame, (640, 480))
                
                # Process frame
                start_time = time.time()
                
                # Create a copy only if we're going to modify it
                processed_frame = frame.copy()
                
                # Process lanes
                processed_frame = detect_lanes(processed_frame)
                
                # Process objects
                processed_frame = detect_objects(processed_frame, model, classes, frame_count)
                
                # Calculate FPS
                process_time = time.time() - start_time
                fps = 1.0 / (current_time - last_process_time) if (current_time - last_process_time) > 0 else 0
                last_process_time = current_time
                
                # Add FPS to frame
                cv2.putText(processed_frame, f"FPS: {fps:.1f}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # Update display frame
                display_frame = processed_frame
                
                # Show frame using the named window
                cv2.imshow(window_name, display_frame)
                
                # Use a consistent wait time
                key = cv2.waitKey(15) & 0xFF  # 15ms delay for ~60fps maximum
                if key == ord('q'):
                    break
                    
            except Exception as e:
                print(f"Frame processing error: {str(e)}")
                continue
                
    except Exception as e:
        tk.messagebox.showerror("Error", 
            f"Failed to start video processing:\n{str(e)}")
    finally:
        is_running = False
        if 'cap' in locals():
            cap.release()
        cv2.destroyWindow(window_name)

# GUI Section
def select_video():
    global video_path
    video_path = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.avi")])

def start_processing():
    global is_running
    if is_running:
        tk.messagebox.showwarning("Warning", "Processing is already running!")
        return
    is_running = True
    
    # Create and start the processing thread
    process_thread = threading.Thread(target=process_video)
    process_thread.daemon = True
    process_thread.start()

def stop_processing():
    global is_running
    is_running = False
    # Allow time for the window to close properly
    time.sleep(0.2)
    cv2.destroyAllWindows()
    for i in range(4):
        cv2.waitKey(1)

def show_driving_report():
    """Display the driving report in a new window"""
    report = driving_logger.generate_report()
    
    report_window = tk.Toplevel(root)
    report_window.title("Driving Session Report")
    report_window.geometry("400x500")
    
    # Create text widget
    text_widget = tk.Text(report_window, wrap=tk.WORD, padx=10, pady=10)
    text_widget.pack(fill=tk.BOTH, expand=True)
    
    # Format and insert report
    report_text = f"""Driving Session Report
====================
Total Driving Time: {report['total_driving_time_minutes']} minutes
Total Lane Departures: {report['total_departures']}

Departures by Severity:
{'-' * 20}"""
    
    for level, count in report['departures_by_severity'].items():
        report_text += f"\n{level}: {count}"
    
    report_text += f"""
{'-' * 20}
Night Mode Usage: {report['night_mode_percentage']:.1f}%
Average Speed: {report['average_speed']:.1f} km/h
"""
    
    text_widget.insert(tk.END, report_text)
    text_widget.config(state=tk.DISABLED)

def play_warning_sound(warning_level):
    """Play warning sound using system beep"""
    try:
        frequency = WARNING_LEVELS[warning_level]['frequency']
        winsound.Beep(frequency, 500)  # frequency Hz for 500ms
    except Exception as e:
        print('\a')  # Fallback to system beep

# GUI
root = tk.Tk()
root.title("Smart Lane & Object Detection")

tk.Button(root, text="Select Video", command=select_video).pack(pady=10)
tk.Button(root, text="Select IP Camera", command=select_ip_camera).pack(pady=10)
tk.Button(root, text="Start Processing", command=start_processing).pack(pady=10)
tk.Button(root, text="Stop Processing", command=stop_processing).pack(pady=10)
tk.Button(root, text="Toggle Snapshot Save", command=toggle_snapshot_save).pack(pady=10)
tk.Button(root, text="Toggle Email Alerts", command=toggle_email_alerts).pack(pady=10)

# Add report button to GUI
tk.Button(root, text="Show Driving Report", command=show_driving_report).pack(pady=10)

# Update main section
if __name__ == "__main__":
    print("Starting Lane Detection System...")
    print("System ready - using system beep for warnings")
    
    root.mainloop()