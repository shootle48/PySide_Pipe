import logging
import time
import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _ms(t0: float) -> float:
    """คืน elapsed ms จาก t0 (perf_counter)"""
    return (time.perf_counter() - t0) * 1000

SIZE_DEF = {
    "L": {
        "outer_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=180, maxRadius=185),
        "inner_circle": dict(dp=1, minDist=200,  param1=50, param2=15,
                             minRadius=90,  maxRadius=100),
        "outer_shrink":    0.70,
        "inner_shrink":    0.90,
        "thresh_block":    301,
        "thresh_c":        1,
        "blur_ksize":      7,
        "morph_kernel":    5,
        "morph_iter":      1,
        "min_defect_area": 500,
    },
    "M": {
        "outer_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=150, maxRadius=155),
        "inner_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=65,  maxRadius=70),
        "outer_shrink":    0.70,
        "inner_shrink":    0.90,
        "thresh_block":    301,
        "thresh_c":        1,
        "blur_ksize":      7,
        "morph_kernel":    5,
        "morph_iter":      1,
        "min_defect_area": 500,
    },
    "S": {
        "outer_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=95,  maxRadius=100),
        "inner_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=40,  maxRadius=45),
        "outer_shrink":    0.70,
        "inner_shrink":    0.90,
        "thresh_block":    301,
        "thresh_c":        1,
        "blur_ksize":      7,
        "morph_kernel":    5,
        "morph_iter":      1,
        "min_defect_area": 500,
    },
}

class Detection:
    VALID_SIZES = ("L", "M", "S")

    def __init__(self, image: np.ndarray, size: str):
        size = size.upper().strip()
        if size not in self.VALID_SIZES:
            raise ValueError(f"size must be one of {self.VALID_SIZES}, got '{size}'")

        self.image = image
        self.size = size
        self.cfg    = SIZE_DEF[size]
        self.success             = False
        self.error               = None
        self.verdict             = None
        self.defects             = []
        self.vis                 = image.copy()
        self.thresh              = None
        self.clean               = None
        self.roi_inner           = None
        self.inner_circle_masked = None
        
        self.processing()

    @property
    def output(self):
        return {
            "result":  self.verdict,    # "OK" / "NG"
            "success":  self.success,   # True/False
            "error":    self.error,     # error message
            "vis":      self.vis,       # Result Image
            "defects":  self.defects,   # list of contours
        }

    def processing(self):
        cfg   = self.cfg
        t_all = time.perf_counter()

        # ── 1. Preprocess ─────────────────────────────────────────────────
        t = time.perf_counter()
        gray = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (cfg["blur_ksize"], cfg["blur_ksize"]), 0)
        adaptive_threshold = cv2.adaptiveThreshold(
            blur, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            cfg["thresh_block"],
            cfg["thresh_c"],
        )
        t_pre = _ms(t)

        # ── 2. Outer HoughCircles ─────────────────────────────────────────
        t = time.perf_counter()
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT,
                                   **dict(cfg["outer_circle"]))
        t_outer = _ms(t)

        if circles is None:
            logger.warning("Detection [Size %s]: Pipe not found", self.size)
            self.error   = f"[Size {self.size}] No Pipe Detected"
            self.verdict = "NG"
            cv2.putText(self.vis, self.error,
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            logger.info(
                "Detection [%s] timing | pre=%.1f  outer=%.1f  TOTAL=%.1f ms",
                self.size, t_pre, t_outer, _ms(t_all),
            )
            return

        x, y, r_outer = np.round(circles[0]).astype(int)[0]
        logger.debug("Detection [Size %s]: outer center=(%d,%d) r=%d",
                     self.size, x, y, r_outer)

        r_shrink  = int(r_outer * cfg["outer_shrink"])
        mask_roi  = np.zeros_like(gray)
        cv2.circle(mask_roi, (x, y), r_shrink, 255, -1)
        roi_inner      = cv2.bitwise_and(blur, mask_roi)
        self.roi_inner = roi_inner

        # ── 3. Inner HoughCircles ─────────────────────────────────────────
        t = time.perf_counter()
        inner_circles = cv2.HoughCircles(roi_inner, cv2.HOUGH_GRADIENT,
                                         **dict(cfg["inner_circle"]))
        t_inner = _ms(t)

        if inner_circles is None:
            logger.warning("Detection [Size %s]: Inner Pipe Not Detected", self.size)
            self.error   = f"[Size {self.size}] Inner Pipe Not Detected"
            self.verdict = "NG"
            cv2.putText(self.vis, self.error,
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            logger.info(
                "Detection [%s] timing | pre=%.1f  outer=%.1f  inner=%.1f  TOTAL=%.1f ms",
                self.size, t_pre, t_outer, t_inner, _ms(t_all),
            )
            return

        cx, cy, r_inner_raw = np.round(inner_circles[0]).astype(int)[0]
        r_inner = max(int(r_inner_raw * cfg["inner_shrink"]), 10)
        logger.debug("Detection [Size %s]: inner center=(%d,%d) r=%d",
                     self.size, cx, cy, r_inner)

        # ── 4. Threshold + Mask ───────────────────────────────────────────
        t = time.perf_counter()
        mask_inner = np.zeros(gray.shape, dtype=np.uint8)
        cv2.circle(mask_inner, (cx, cy), r_inner, 255, -1)
        th         = cv2.bitwise_and(adaptive_threshold, adaptive_threshold, mask=mask_inner)
        self.thresh = th
        t_thresh = _ms(t)

        # ── 5. Morphology ─────────────────────────────────────────────────
        t = time.perf_counter()
        k      = cfg["morph_kernel"]
        iters  = cfg["morph_iter"]
        kernel = np.ones((k, k), np.uint8)
        clean  = cv2.morphologyEx(th,    cv2.MORPH_OPEN,  kernel, iterations=iters)
        clean  = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, kernel, iterations=iters)
        self.clean = clean
        t_morph = _ms(t)

        # ── 6. Contour + Verdict ──────────────────────────────────────────
        t = time.perf_counter()
        contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        defects  = [c for c in contours if cv2.contourArea(c) > cfg["min_defect_area"]]
        verdict  = "NG" if defects else "OK"
        self.defects = defects
        self.verdict = verdict
        t_contour = _ms(t)

        # ── 7. Draw ───────────────────────────────────────────────────────
        t = time.perf_counter()
        vis = self.image.copy()
        for c in defects:
            bx, by, bw, bh = cv2.boundingRect(c)
            cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (0, 0, 255), 2)
            cv2.putText(vis, "Defect", (bx, by - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        color = (0, 0, 255) if verdict == "NG" else (0, 200, 0)
        cv2.putText(vis, verdict, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
        self.vis     = vis
        self.success = True
        t_draw = _ms(t)

        # ── Summary log ───────────────────────────────────────────────────
        t_total = _ms(t_all)
        logger.info(
            "Detection [%s]: %s | defects=%d | "
            "pre=%.1f  outer=%.1f  inner=%.1f  thresh=%.1f  morph=%.1f  contour=%.1f  draw=%.1f  "
            "TOTAL=%.1f ms",
            self.size, verdict, len(defects),
            t_pre, t_outer, t_inner, t_thresh, t_morph, t_contour, t_draw,
            t_total,
        )


# Quick test
if __name__ == "__main__":
    import sys

    # img_path = r"C:\Work\Praram Nine\Smartsense\Pipe_Detection_Dataset\Video_1_Size_L\frame_000000.jpg"
    img_path = r"C:\Work\Praram Nine\Smartsense\Pipe_Detection_Dataset\Video_3_Size_M\frame_023027.jpg"
    # img_path = r"C:\Users\Mate\Downloads\drive-download-20260524T075736Z-3-001\IMG_7037.PNG"
    size     = "M"

    image = cv2.imread(img_path)
    if image is None:
        raise FileNotFoundError(f"Cannot read image: {img_path}")

    det = Detection(image, size=size)
    out = det.output

    print(f"Result : {out['result']}")
    print(f"Success: {out['success']}")
    if out["error"]:
        print(f"Error  : {out['error']}")

    cv2.imshow("Result", out["vis"])
    # if det.thresh is not None:
    #     cv2.imshow("Threshold", det.thresh)
    # if det.clean is not None:
    #     cv2.imshow("Clean", det.clean)
    # if det.roi_inner is not None:
    #     cv2.imshow("ROI Inner", det.roi_inner)

    cv2.waitKey(0)
    cv2.destroyAllWindows()

       
        

