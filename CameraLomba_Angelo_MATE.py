import subprocess as sp
import numpy as np
import cv2
import os
import time
from ultralytics import YOLO

# ================= CONFIG =================
FFMPEG_BIN = "ffmpeg"
SDP_FILE = "HDExplorer.sdp"

WIDTH = 640
HEIGHT = 360

YOLO_MODEL = "KepitingAlaska.pt"
YOLO_CONF = 0.35
DETECT_EVERY_N_FRAMES = 3 
# =========================================

frame_size = WIDTH * HEIGHT * 3

def start_ffmpeg_proc(sdp_file, width, height):
    cmd = [
        FFMPEG_BIN,
        "-protocol_whitelist", "file,udp,rtp",
        "-i", sdp_file,
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-an", "-sn",
        "-vf", f"scale={width}:{height}",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "pipe:1"
    ]
    
    return sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, bufsize=frame_size)

def draw_hud(frame, mode_idx, fps, current_count, max_count, detected_names):
    h, w = frame.shape[:2]
    
    modes = ["OFF", "YOLO 1", "YOLO 2"]
    current_mode_name = modes[mode_idx]

    
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (320, 110), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

    
    cv2.putText(frame, f"MODE: {current_mode_name}", (15, 30), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    cv2.putText(frame, f"FPS: {fps:.1f}", (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    if mode_idx > 0: # Jika mode bukan OFF
        nama_objek = "kepiting" if detected_names else "-"
        cv2.putText(frame, f"Terdeteksi: {nama_objek}", (15, 80), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        
        limit_val = 2 if mode_idx == 1 else 10
        # Warna berubah hijau jika target tercapai
        color = (0, 200, 0) if max_count >= limit_val else (0, 165, 255)
        
       
        cv2.rectangle(frame, (w-230, 10), (w-10, 75), color, -1)
        
       
        cv2.putText(frame, "TARGET", (w-200, 35), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)
        
        
        cv2.putText(frame, f"{max_count}", (w-135, 68), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)
        
        
        cv2.putText(frame, "R: Reset | Y: Switch Mode", (w-210, 95), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

def main():
    if not os.path.exists(SDP_FILE):
        print(f"Error: {SDP_FILE} tidak ditemukan.")
        return

    print("[INFO] Loading YOLO model...")
    model = YOLO(YOLO_MODEL)
    
    print("[INFO] Starting FFmpeg Stream (SDP)...")
    proc = start_ffmpeg_proc(SDP_FILE, WIDTH, HEIGHT)

    mode_idx = 1 # Default ke YOLO 1
    frame_count = 0
    last_boxes = []
    last_detect_names = []
    
    current_crab_count = 0
    max_crab_count = 0 

    fps_last_time = time.time()
    fps_counter = 0
    fps = 0.0

    while True:
        
        raw = proc.stdout.read(frame_size)
        if not raw or len(raw) != frame_size: 
            print("[WARN] Stream terputus atau frame tidak lengkap.")
            break

        frame = np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3)).copy()
        frame_count += 1

        if mode_idx > 0: 
            if frame_count % DETECT_EVERY_N_FRAMES == 0:
                results = model.predict(frame, imgsz=320, conf=YOLO_CONF, verbose=False)[0]

                new_boxes = []
                for box in results.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    new_boxes.append((x1, y1, x2, y2))

                last_boxes = new_boxes
                last_detect_names = ["kepiting"] if new_boxes else []
                
                live_actual = len(new_boxes)
                current_crab_count = live_actual 

                
                limit = 2 if mode_idx == 1 else 10
                
                
                if live_actual > max_crab_count:
                    max_crab_count = min(live_actual, limit)

            
            for (x1, y1, x2, y2) in last_boxes:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

        
        fps_counter += 1
        if time.time() - fps_last_time >= 1.0:
            fps = fps_counter / (time.time() - fps_last_time)
            fps_last_time = time.time()
            fps_counter = 0

        
        draw_hud(frame, mode_idx, fps, current_crab_count, max_crab_count, last_detect_names)
        
        cv2.imshow("ROV MONITOR - YOLO MODE", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"): 
            break
        elif key == ord("y"): 
            
            mode_idx = (mode_idx + 1) % 3
            max_crab_count = 0 
            print(f"[MODE] Ganti ke: {mode_idx}")
        elif key == ord("r"): 
            max_crab_count = 0
            print("[INFO] Counter Reset.")
        elif key == ord("s"): 
            cv2.imwrite(f"rov_capture_{int(time.time())}.png", frame)

    proc.terminate()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()