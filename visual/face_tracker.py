"""Rastreo facial local con OpenCV para el efecto parallax del núcleo.

La cámara se procesa en tu equipo: no se guarda ni se envía ninguna imagen.
"""

from __future__ import annotations

import sys
import threading
import time

from core import visual_state


class FaceTracker:
    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            import cv2  # noqa: F401
        except ImportError:
            print("Rastreo facial desactivado: py -m pip install \"opencv-python>=4.8,<5\"")
            return False
        self._thread = threading.Thread(target=self._run, name="jarvis-face", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        visual_state.set_face(0.0, 0.0, False)

    def _run(self) -> None:
        import cv2

        capture = None
        backends = (
            [cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY]
            if sys.platform.startswith("win")
            else [cv2.CAP_ANY]
        )
        indexes = [self.camera_index] + [i for i in range(3) if i != self.camera_index]
        for index in indexes:
            for backend in backends:
                candidate = cv2.VideoCapture(index, backend)
                ok, _frame = candidate.read() if candidate.isOpened() else (False, None)
                if ok:
                    capture = candidate
                    break
                candidate.release()
            if capture is not None:
                break
        if capture is None:
            print(
                "Rastreo facial: no se pudo abrir la cámara web. Cierra otras apps que la usen "
                "y revisa Configuración > Privacidad > Cámara (permitir apps de escritorio)."
            )
            return
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        if cascade.empty():
            print("Rastreo facial: tu OpenCV no trae el modelo de rostros. Usa: py -m pip install \"opencv-python>=4.8,<5\"")
            capture.release()
            return
        last_seen = 0.0
        smooth_x = smooth_y = 0.0
        try:
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    time.sleep(0.1)
                    continue
                frame = cv2.flip(frame, 1)  # espejo: mover la cabeza a tu derecha = +x
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(
                    gray, scaleFactor=1.2, minNeighbors=5, minSize=(40, 40)
                )
                height, width = gray.shape
                if len(faces):
                    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                    target_x = ((x + w / 2) / width - 0.5) * 2
                    target_y = ((y + h / 2) / height - 0.5) * 2
                    smooth_x += (target_x - smooth_x) * 0.45
                    smooth_y += (target_y - smooth_y) * 0.45
                    last_seen = time.monotonic()
                    visual_state.set_face(smooth_x, smooth_y, True)
                elif time.monotonic() - last_seen > 1.2:
                    visual_state.set_face(0.0, 0.0, False)
                time.sleep(0.03)
        finally:
            capture.release()
