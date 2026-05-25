import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class Detection:
    def __init__(self, image):
        self.image = image
        self.verdict = "NG"   # default — overwritten by processing()
        self.processed_image = self.processing()

    def processing(self):
        gray_img = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
        blur_img = cv2.GaussianBlur(gray_img, (7, 7), 0)
        clahe = cv2.createCLAHE(clipLimit=10.0, tileGridSize=(20, 20))
        enhanced = clahe.apply(blur_img)

        circles = cv2.HoughCircles(
            enhanced,
            cv2.HOUGH_GRADIENT,
            dp=2,
            minDist=4000,
            param1=150,
            param2=10,
            minRadius=1,
            maxRadius=215,
        )

        if circles is None:
            logger.warning("Detection: outer circle not found")
            self.verdict = "NG"
            return self.image.copy()   # คืนรูปต้นฉบับ ไม่ crash

        logger.debug("Detection: found %d outer circle(s)", circles.shape[1])
        extract = np.round(circles[0]).astype(int)

        x, y, r_outer = extract[0]
        logger.debug("Detection: outer circle center=(%d, %d) r=%d", x, y, r_outer)

        mask_outer = np.zeros_like(gray_img)
        cv2.circle(mask_outer, (x, y), r_outer, 255, -1)
        roi = cv2.bitwise_and(gray_img, mask_outer)

        r_shrink = int(r_outer * 0.70)
        mask_inner_roi = np.zeros_like(gray_img)
        cv2.circle(mask_inner_roi, (x, y), r_shrink, 255, -1)
        roi_inner = cv2.bitwise_and(roi, mask_inner_roi)

        inner_circles = cv2.HoughCircles(
            roi_inner,
            cv2.HOUGH_GRADIENT,
            dp=1,
            minDist=4000,
            param1=100,
            param2=15,
            minRadius=20,
            maxRadius=int(r_shrink * 0.90),
        )

        # fallback: ใช้ outer center และ 50% r_outer ถ้าหาวงในไม่เจอ
        cx, cy = x, y
        r_inner = int(r_outer * 0.50)

        if inner_circles is None:
            logger.warning("Detection: inner circle not found — fallback r_inner=%d", r_inner)
        else:
            inner_extract = np.round(inner_circles[0]).astype(int)
            cx, cy, r_inner = inner_extract[0]
            r_inner = max(int(r_inner * 0.97), 10)
            logger.debug("Detection: inner circle center=(%d, %d) r=%d", cx, cy, r_inner)

        mask_inner_circle = np.zeros_like(gray_img)
        cv2.circle(mask_inner_circle, (cx, cy), r_inner, 255, -1)
        inner_circle_masked = cv2.bitwise_and(roi_inner, mask_inner_circle)

        blur_img = cv2.GaussianBlur(gray_img, (5, 5), 0)
        th = cv2.adaptiveThreshold(
            blur_img, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 301, 2,
        )
        th = cv2.bitwise_and(th, inner_circle_masked)

        kernel = np.ones((2, 2), np.uint8)
        clean = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=2)

        contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        defects = [c for c in contours if cv2.contourArea(c) > 50]
        verdict = "NG" if defects else "OK"
        self.verdict = verdict

        # ── Draw result on vis ────────────────────────────────────────────
        vis = self.image.copy()
        cv2.circle(vis, (x, y),   r_outer, (0, 255, 0), 2)
        cv2.circle(vis, (cx, cy), r_inner, (0, 0, 255), 2)

        for c in defects:
            bx, by, bw, bh = cv2.boundingRect(c)
            cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (0, 165, 255), 2)

        color = (0, 0, 255) if verdict == "NG" else (0, 200, 0)
        cv2.putText(vis, verdict, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        logger.info("Detection: %s | defects=%d", verdict, len(defects))
        return vis
