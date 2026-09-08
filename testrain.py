from ultralytics import YOLO
import cv2
import time

# =========================================================
# CONFIGURATION
# =========================================================

MODEL_PATH = r"D:\Downloads\GUI-ROV-main (1)\GUI-ROV-main\best_new.pt"

CAMERA_INDEX = 0
CONFIDENCE = 0.40
IMG_SIZE = 640

# =========================================================
# LOAD YOLO MODEL
# =========================================================

print("Loading YOLO model...")

model = YOLO(MODEL_PATH)

print("Model berhasil dimuat.")

# =========================================================
# OPEN LAPTOP CAMERA
# =========================================================

cap = cv2.VideoCapture(CAMERA_INDEX)

# Resolusi kamera laptop
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

if not cap.isOpened():
    print("❌ Kamera laptop tidak dapat dibuka.")
    print("Coba ganti CAMERA_INDEX dari 0 menjadi 1.")
    exit()

print("✅ Kamera laptop aktif.")
print("Tekan Q untuk keluar.")

# =========================================================
# FPS
# =========================================================

prev_time = time.time()

# =========================================================
# MAIN LOOP
# =========================================================

while True:

    ret, frame = cap.read()

    if not ret:
        print("❌ Tidak dapat membaca kamera.")
        break

    # -----------------------------------------------------
    # YOLO DETECTION
    # -----------------------------------------------------

    results = model.predict(
        source=frame,
        conf=CONFIDENCE,
        imgsz=IMG_SIZE,
        device="cpu",
        verbose=False
    )

    # Frame hasil deteksi
    annotated = results[0].plot()

    # -----------------------------------------------------
    # CAMERA CENTER
    # -----------------------------------------------------

    height, width = annotated.shape[:2]

    center_x = width // 2
    center_y = height // 2

    # Crosshair tengah kamera
    cv2.line(
        annotated,
        (center_x - 20, center_y),
        (center_x + 20, center_y),
        (255, 255, 0),
        2
    )

    cv2.line(
        annotated,
        (center_x, center_y - 20),
        (center_x, center_y + 20),
        (255, 255, 0),
        2
    )

    # -----------------------------------------------------
    # INFORMATION DETECTION
    # -----------------------------------------------------

    for result in results:

        for box in result.boxes:

            x1, y1, x2, y2 = map(int, box.xyxy[0])

            confidence = float(box.conf[0])

            class_id = int(box.cls[0])

            class_name = model.names[class_id]

            # Titik tengah objek
            object_x = int((x1 + x2) / 2)
            object_y = int((y1 + y2) / 2)

            # Error terhadap pusat kamera
            error_x = object_x - center_x
            error_y = object_y - center_y

            # Titik tengah objek
            cv2.circle(
                annotated,
                (object_x, object_y),
                5,
                (0, 0, 255),
                -1
            )

            # Garis dari pusat kamera ke objek
            cv2.line(
                annotated,
                (center_x, center_y),
                (object_x, object_y),
                (255, 0, 255),
                1
            )

            # Informasi objek
            info = f"{class_name} {confidence:.2f}"

            cv2.putText(
                annotated,
                info,
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2
            )

            # Koordinat
            coordinate = f"X:{object_x} Y:{object_y}"

            cv2.putText(
                annotated,
                coordinate,
                (x1, y2 + 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 255),
                2
            )

            # Error
            error_text = f"EX:{error_x} EY:{error_y}"

            cv2.putText(
                annotated,
                error_text,
                (x1, y2 + 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                2
            )

    # -----------------------------------------------------
    # FPS
    # -----------------------------------------------------

    current_time = time.time()

    fps = 1 / max(current_time - prev_time, 0.001)

    prev_time = current_time

    cv2.putText(
        annotated,
        f"FPS: {fps:.1f}",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 255),
        2
    )

    # Status
    cv2.putText(
        annotated,
        "YOLOv8 - LAPTOP CAMERA",
        (20, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2
    )

    # -----------------------------------------------------
    # SHOW
    # -----------------------------------------------------

    cv2.imshow(
        "HIDRASHIP - YOLOv8 LIVE",
        annotated
    )

    # -----------------------------------------------------
    # EXIT
    # -----------------------------------------------------

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break

# =========================================================
# CLOSE
# =========================================================

cap.release()
cv2.destroyAllWindows()

print("Program selesai.")