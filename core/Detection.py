import cv2
import numpy as np
from scipy.signal import find_peaks

class Detection:
    def __init__(self, image):
        self.image = image 
        self.processed_image = self.processing()

    def processing(self):
        gray_img = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=10.0, tileGridSize=(20, 20))
        enhanced = clahe.apply(gray_img)

       
        circles = cv2.HoughCircles(
            enhanced,
            cv2.HOUGH_GRADIENT,
            dp=1,
            minDist=4000,
            param1=150,
            param2=10,
            minRadius=1,
            maxRadius=215
        )
        if circles is None:
            print("หาวงไม่เจอ")
        
        print("จำนวนวง:", circles.shape[1])
        extract = np.round(circles[0]).astype(int)

        vis_circles = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        for x, y, r in extract:
            cv2.circle(vis_circles, (x, y), r, (0, 255, 0), 2)
            cv2.circle(vis_circles, (x, y), 4, (0, 0, 255), -1)
        
        extract = np.round(circles[0]).astype(int)
        x, y, r_outer = extract[0]
        print(f"วงที่เลือก: center=({x}, {y}), r_outer={r_outer}")

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
            minRadius=1,
            maxRadius= int(r_shrink * 0.90)
        )

        if inner_circles is None:
            print("หาวงในไม่เจอ")
            r_inner = int(r_outer * 0.50)
            print(f"fallback r_inner={r_inner}")

        else:
            inner_extract = np.round(inner_circles[0]).astype(int)
            cx, cy, r_inner = inner_extract[0]
            r_inner = max(int(r_inner*0.97), 10)
            print(f"วงในที่เลือก: center=({cx}, {cy}), r_inner={r_inner}")

            vis_inner = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            for cx, cy, r in inner_extract:
                cv2.circle(vis_inner, (cx, cy), r, (0, 255, 0), 2)
                cv2.circle(vis_inner, (cx, cy), 4, (0, 0, 255), -1)

            mask_inner_circle = np.zeros_like(gray_img)
            cv2.circle(mask_inner_circle, (cx, cy), r_inner, 255, -1)
            inner_circle_masked = cv2.bitwise_and(roi_inner, mask_inner_circle)

        vis = self.image.copy()
        cv2.circle(vis, (x, y), r_outer, (0, 255, 0),  2)
        cv2.circle(vis, (cx, cy), r_inner, (0, 0, 255),  2)

        blur_img = cv2.GaussianBlur(gray_img, (5, 5), 0)
        
        th = cv2.adaptiveThreshold(
            blur_img, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 51, 4
            )
        
        th = cv2.bitwise_and(th, inner_circle_masked)

        kernel = np.ones((2, 2), np.uint8)
        clean  = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=2)

        contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        defects = [c for c in contours if cv2.contourArea(c) > 50]
        result  = "NG" if defects else "OK"

        for c in defects:
            bx, by, bw, bh = cv2.boundingRect(c)
            cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (0, 165, 255), 2)

            color = (0, 0, 255) if result == "NG" else (0, 200, 0)
            cv2.putText(vis, f"{result} | Size: Unknown",
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        return vis