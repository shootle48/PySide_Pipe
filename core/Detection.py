import logging
import time
import cv2
import numpy as np

logger = logging.getLogger(__name__)

def _check_cuda() -> bool:
    try:
        return (
            hasattr(cv2, "cuda") and
            callable(getattr(cv2.cuda, "createHoughCirclesDetector", None)) and
            cv2.cuda.getCudaEnabledDeviceCount() > 0
        )
    except Exception:
        return False

_USE_CUDA: bool = _check_cuda()
logger.info("Detection: CUDA=%s", _USE_CUDA)

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

def _build_cuda_detectors() -> dict:
    if not _USE_CUDA:
        return {}
    cache = {}
    for size, cfg in SIZE_DEF.items():
        cache[size] = {
            "outer": _make_cuda_detector(cfg["outer_circle"]),
            "inner": _make_cuda_detector(cfg["inner_circle"]),
        }
        logger.debug("Detection: pre-built CUDA detectors for size %s", size)
    return cache

def _make_cuda_detector(cfg_circle: dict):
    return cv2.cuda.createHoughCirclesDetector(
        dp=1,
        minDist=float(cfg_circle["minDist"]),
        cannyThreshold=int(cfg_circle["param1"]),
        votesThreshold=int(cfg_circle["param2"]),
        minRadius=int(cfg_circle["minRadius"]),
        maxRadius=int(cfg_circle["maxRadius"]),
    )

_CUDA_DETECTORS: dict = _build_cuda_detectors()

class Detection:
    VALID_SIZES = ("L", "M", "S")
    THRESH_MIN  = 0
    THRESH_MAX  = 30000

    def __init__(self, image: np.ndarray, size: str, defthresh: int = 0):
        size = size.upper().strip()
        if size not in self.VALID_SIZES:
            raise ValueError(f"size must be one of {self.VALID_SIZES}, got '{size}'")

        self.defthresh = max(self.THRESH_MIN, min(self.THRESH_MAX, int(defthresh)))
        if self.defthresh != defthresh:
            logger.warning(
                "Detection [%s]: defthresh=%s clamped to %s (valid range %s–%s)",
                size, defthresh, self.defthresh, self.THRESH_MIN, self.THRESH_MAX,
            )
        logger.debug("Detection [%s]: defthresh=%d", size, self.defthresh)

        self.image = image
        self.size  = size
        self.cfg   = dict(SIZE_DEF[size])
        self.success             = False
        self.error               = None
        self.verdict             = None
        self.defects             = []
        self.vis                 = image.copy()
        self.thresh              = None
        self.clean               = None
        self.roi_inner           = None
        self.roi_inner_raw       = None
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
            "defect_areas": [int(cv2.contourArea(c)) for c in self.defects],
        }

    def _hough_circles(self, img_gray: np.ndarray, circle_key: str) -> "np.ndarray | None":
        cfg_circle = self.cfg[f"{circle_key}_circle"]

        if _USE_CUDA and self.size in _CUDA_DETECTORS:
            try:
                detector = _CUDA_DETECTORS[self.size][circle_key]
                gpu = cv2.cuda_GpuMat()
                gpu.upload(img_gray)
                result = detector.detect(gpu)
                arr = result.download()
                if arr is not None and arr.size > 0 and arr.shape[1] > 0:
                    return arr
                return None
            except Exception as exc:
                logger.warning(
                    "Detection [%s]: CUDA detect failed (%s) → fallback CPU",
                    self.size, exc,
                )

        return cv2.HoughCircles(img_gray, cv2.HOUGH_GRADIENT, **cfg_circle)


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
        circles = self._hough_circles(gray, "outer")
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
        inner_circles = self._hough_circles(roi_inner, "inner")
        t_inner = _ms(t)

        if inner_circles is None:
            logger.warning("Detection [Size %s]: Inner Pipe Not Detected", self.size)
            self.error   = f"[Size {self.size}] Inner Pipe Not Detected"
            self.verdict = "NG"
            if DEBUG_DRAW:   # วงนอกเจอ — โชว์ไว้ debug ว่า inner fail ตรงไหน
                cv2.circle(self.vis, (x, y), r_outer, (0, 255, 0), 2)
            cv2.putText(self.vis, self.error,
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            logger.info(
                "Detection [%s] timing | pre=%.1f  outer=%.1f  inner=%.1f  TOTAL=%.1f ms",
                self.size, t_pre, t_outer, t_inner, _ms(t_all),
            )
            return

        cx, cy, r_inner_raw = np.round(inner_circles[0]).astype(int)[0]
        self.roi_inner_raw  = r_inner_raw
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
        all_areas = [(c, cv2.contourArea(c)) for c in contours]
        defects  = [c for c, area in all_areas if area >= self.defthresh]
        verdict  = "NG" if defects else "OK"
        self.defects = defects
        self.verdict = verdict
        t_contour = _ms(t)

        defect_areas = sorted(
            [cv2.contourArea(c) for c in defects],
            reverse=True,
        )
        if defect_areas:
            logger.info(
                "Detection [%s]: %s | defects=%d | areas(px²)=%s | thresh=%s | GPU=%s",
                self.size,
                verdict,
                len(defects),
                [f"{a:.0f}" for a in defect_areas],
                self.defthresh,
                _USE_CUDA,
            )
        else:
            rejected_areas = sorted([area for _, area in all_areas], reverse=True)
            logger.info(
                "Detection [%s]: OK | defects=0 | thresh=%s | rejected_areas(px²)=%s | GPU=%s",
                self.size,
                self.defthresh,
                [f"{a:.0f}" for a in rejected_areas[:5]],
                _USE_CUDA,
            )

        # ── 7. Draw ───────────────────────────────────────────────────────
        t = time.perf_counter()
        vis = self.image.copy()
        cv2.circle(vis, (x,  y),  r_outer, (0, 255, 0), 2)
        cv2.circle(vis, (cx, cy), r_inner, (255, 0, 0), 2)

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
        # _thr_src = "override" if self._threshold_source == "QSettings override" else "default"
        logger.info(
            "Detection [%s]: %s | defects=%d | min_defect_area=%d (%s) | "
            "pre=%.1f  outer=%.1f  inner=%.1f  thresh=%.1f  morph=%.1f  contour=%.1f  draw=%.1f  "
            "TOTAL=%.1f ms",
            self.size, verdict, len(defects),
            # self.cfg["min_defect_area"], _thr_src,
            t_pre, t_outer, t_inner, t_thresh, t_morph, t_contour, t_draw,
            t_total,
        )


# # Quick test
# if __name__ == "__main__":
#     import sys

#     # img_path = r"C:\Work\Praram Nine\Smartsense\Pipe_Detection_Dataset\Video_1_Size_L\frame_000000.jpg"
#     img_path = r"C:\Work\Praram Nine\Smartsense\Pipe_Detection_Dataset\Video_3_Size_M\frame_023027.jpg"
#     # img_path = r"C:\Users\Mate\Downloads\drive-download-20260524T075736Z-3-001\IMG_7037.PNG"
#     size     = "M"
#     defthresh = 3000

#     image = cv2.imread(img_path)
#     if image is None:
#         raise FileNotFoundError(f"Cannot read image: {img_path}")

#     det = Detection(image, size=size, defthresh=defthresh)
#     out = det.output

#     print(f"Result : {out['result']}")
#     print(f"Success: {out['success']}")
#     print(f"Inner radius: {det.roi_inner_raw}")
#     print(f"Defthresh   : {det.defthresh} px²")
#     print(f"Defect areas: {out['defect_areas']} px²")
#     if out["error"]:
#         print(f"Error  : {out['error']}")

#     cv2.imshow("Result", out["vis"])
#     # if det.thresh is not None:
#     #     cv2.imshow("Threshold", det.thresh)
#     # if det.clean is not None:
#     #     cv2.imshow("Clean", det.clean)
#     # if det.roi_inner is not None:
#     #     cv2.imshow("ROI Inner", det.roi_inner)

#     cv2.waitKey(0)
#     cv2.destroyAllWindows()

       
        

