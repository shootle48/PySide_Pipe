import logging
import time
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# วาดเส้น debug วงนอก/วงใน หรือไม่ — main_window override ตาม MA mode
# (define ไว้ที่นี่ default False ให้ Detection ใช้ standalone/benchmark ได้โดยไม่ NameError)
DEBUG_DRAW = False

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

SIZE_MAPPING = {
    "L": {
        "outer_circle": dict(dp=1, #ความละเอียดในการคำนวณ
                             minDist=3000, #ระยะห่างขั้นต่ำระหว่างวงกลม (px)
                             param1=50, #ความไวในการตรวจขอบ (edge detection threshold) ยิ่งน้อยยิ่งไว ตรวจเจอขอบได้ง่ายขึ้นแต่ก็ได้ขอบปลอมมากขึ้นด้วย
                             param2=15, #คะแนนการ Vote ที่จะมองว่าเป็นวงกลมยิ่งตั้งสูงยิ่งต้องเป็นวงกลมมากๆ
                             minRadius=180, maxRadius=185), #min/max ของรัศมีวงกลมที่ต้องการ
        "mid_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=135, maxRadius=140),
        "inner_circle": dict(dp=1, minDist=200,  param1=50, param2=15,
                             minRadius=95,  maxRadius=100), 
        "outer_shrink":    0.90,
        "inner_shrink":    0.90,
        "thresh_block":    301,
        "thresh_c":        1,
        "blur_ksize":      7,
        "morph_kernel":    5,
        "morph_iter":      1,
        "max_defect_area": 30000,
    },
    "M": {
        "outer_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=150, maxRadius=155),
        "mid_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=100, maxRadius=105),
        "inner_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=65,  maxRadius=70),
        "outer_shrink":    0.85,
        "inner_shrink":    0.90,
        "thresh_block":    301,
        "thresh_c":        1,
        "blur_ksize":      7,
        "morph_kernel":    5,
        "morph_iter":      1,
        "max_defect_area": 15000,
    },
    "S": {
        "outer_circle": dict(dp=1, minDist=3000, param1=25, param2=30,
                             minRadius=80,  maxRadius=90),
        "mid_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=55, maxRadius=60),
        "inner_circle": dict(dp=1, minDist=3000, param1=75, param2=15,
                             minRadius=35,  maxRadius=40),
        "outer_shrink":    0.90,
        "inner_shrink":    0.90,
        "thresh_block":    301,
        "thresh_c":        1,
        "blur_ksize":      7,
        "morph_kernel":    5,
        "morph_iter":      1,
        "max_defect_area": 6000,
    },
}

def _build_cuda_detectors() -> dict:
    if not _USE_CUDA:
        return {}
    cache = {}
    for size, cfg in SIZE_MAPPING.items():
        cache[size] = {
            "outer": _make_cuda_detector(cfg["outer_circle"]),
            "mid"  : _make_cuda_detector(cfg["mid_circle"]),
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
    PCT_MIN = 0.0
    PCT_MAX = 100.0   # threshold = % ของพื้นที่วงใน (inner circle)

    def __init__(self, image: np.ndarray, size: str, defthresh_pct: float = 0.0):
        size = size.upper().strip()
        if size not in self.VALID_SIZES:
            size = "M"
            logger.warning("Wrong Definition of Size, the system changes it to M size as the default")

        # threshold = % ของพื้นที่วงใน (pct/100 × พื้นที่วงในจริง คำนวณตอน processing)
        # default 0.0 → 0% → 0 px² = เข้มงวดสุด (production ไม่ override ก็ใช้ค่านี้)
        pct = 0.0 if defthresh_pct is None else float(defthresh_pct)
        self.defthresh_pct = max(self.PCT_MIN, min(self.PCT_MAX, pct))
        if pct < self.PCT_MIN or pct > self.PCT_MAX:
            logger.warning(
                "Detection [%s]: defthresh_pct=%s clamped to %s (valid range %s–%s%%)",
                size, pct, self.defthresh_pct, self.PCT_MIN, self.PCT_MAX,
            )
        logger.info(
            "Detection [%s]: [STEP] รับ threshold = %.2f%% ของพื้นที่วงใน",
            size, self.defthresh_pct,
        )
        self.thresh_px1 = None
        self.thresh_px2 = None   # px² จริง — คำนวณตอน processing (ต้องรู้ r_inner)        
        self.size  = size
        self.cfg   = dict(SIZE_MAPPING[size])
        self.error               = None
        self.verdict             = None
        h, w = image.shape[:2]
        if (w, h) != (1280, 720):
            image = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_LINEAR)
            logger.info(
                "Detection [%s]: resized %dx%d → %dx%d",
                size, w, h, 1280, 720,
            )
        self.image = image
        self.vis                 = image.copy()
        self.clean               = None
        self.masked_pipe         = None
        self.inner_roi      = None
        self.r_inner = None
        self.masked_pipe_land = None
        self.inner_pipe_masked = None
        
        self.defectCrack = None
        self.defect1 = False
        self.defect2 = False
        
        self.processing()

    @property
    def output(self):
        return {
            "result":  self.verdict,
            "error":    self.error,
            "vis":      self.vis,
            "defect1" : self.defect1, # รอยแตก
            "defect2" : self.defect2 # เศษขี้เหล็ก
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
        logs = {
            "size": self.size
        }

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
        outer_circle = self._hough_circles(gray, "outer")
        t_outer = _ms(t)

        if outer_circle is None:
            logger.warning("Detection [Size %s]: Pipe not found", self.size)
            self.error   = f"[Size {self.size}] Pipe not found"
            self.verdict = "NG"
            cv2.putText(self.vis, self.error,
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 2)
            logger.info(
                "Detection [%s] timing | pre=%.1f  outer=%.1f  TOTAL=%.1f ms",
                self.size, t_pre, t_outer, _ms(t_all),
            )
            return

        ax, ay, r_outer  = np.round(outer_circle[0]).astype(int)[0]
        logger.debug("Detection [Size %s]: outer center=(%d,%d) r=%d",
                     self.size, ax, ay, r_outer)

        r_outer_shrink  = int(r_outer * cfg["outer_shrink"])
        logs["r_outer"] = int(r_outer)
        pipe_mask  = np.zeros_like(gray)
        cv2.circle(pipe_mask, (ax, ay), r_outer_shrink, 255, -1)
        masked_pipe      = cv2.bitwise_and(gray, pipe_mask)
        self.masked_pipe = masked_pipe

        mid_circle = self._hough_circles(masked_pipe, "mid")
        if mid_circle is None:
            logger.warning("Detection [%s]: Mid Pipe not found", self.size)
            self.error = f"Detection [{self.size}]: Pipe not found"
            self.verdict = "NG"
            cv2.putText(self.vis, self.error,
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 2)
            return
        
        bx, by, r_mid = np.round(mid_circle[0]).astype(int)[0]
        logs["r_mid"] = int(r_mid)
        mid_circle_mask  = np.zeros_like(gray)
        cv2.circle(mid_circle_mask, (bx, by), r_mid, 255, -1)
        inner_roi = cv2.bitwise_and(gray, mid_circle_mask)
        self.inner_roi = inner_roi

        # ── 3. Inner HoughCircles ─────────────────────────────────────────
        t = time.perf_counter()
        inner_circle = self._hough_circles(inner_roi, "inner")
        t_inner = _ms(t)

        if inner_circle is None:
            logger.warning("Detection [Size %s]: Inner Pipe Not Detected", self.size)
            self.error   = f"[Size {self.size}] Inner Pipe Not Detected"
            self.verdict = "NG"
            if DEBUG_DRAW:   # วงนอกเจอ — โชว์ไว้ debug ว่า inner fail ตรงไหน
                cv2.circle(self.vis, (ax, ay), r_outer, (0, 255, 0), 2)
            cv2.putText(self.vis, self.error,
                        (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 2)
            logger.info(
                "Detection [%s] timing | pre=%.1f  outer=%.1f  inner=%.1f  TOTAL=%.1f ms",
                self.size, t_pre, t_outer, t_inner, _ms(t_all),
            )
            return

        cx, cy, r_inner = np.round(inner_circle[0]).astype(int)[0]
        self.r_inner  = r_inner
        r_inner_final = max(int(r_inner * cfg["inner_shrink"]), 10)
        logger.debug("Detection [Size %s]: inner center=(%d,%d) r=%d",
                     self.size, cx, cy, r_inner_final)

        # ── 4. Threshold + Mask ───────────────────────────────────────────
        pipe_land_mask = np.zeros_like(gray)
        cv2.circle(pipe_land_mask, (ax, ay), r_outer_shrink, 255, -1)
        cv2.circle(pipe_land_mask, (bx, by), r_mid, 0, -1)
        masked_pipe_land = cv2.bitwise_and(gray, pipe_land_mask)
        self.masked_pipe_land = masked_pipe_land

        th_land = cv2.bitwise_and(adaptive_threshold, adaptive_threshold, mask=pipe_land_mask)
        k, iters = cfg["morph_kernel"], cfg["morph_iter"]
        kernel = np.ones((k, k), np.uint8)
        clean_land = cv2.morphologyEx(th_land, cv2.MORPH_OPEN,  kernel, iterations=iters)
        clean_land = cv2.morphologyEx(clean_land, cv2.MORPH_CLOSE, kernel, iterations=iters)
        self.clean_land = clean_land
        contours_land, _ = cv2.findContours(clean_land, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        all_areas_land = [(c, cv2.contourArea(c)) for c in contours_land]
        
        inner_area_px1 = float((np.pi * (r_outer_shrink ** 2)) - (np.pi * (r_mid ** 2)))
        self.thresh_px1 = ((self.defthresh_pct / 100.0)*(1/39.8)) * inner_area_px1
        logger.info(
            "Detection [%s]: [STEP] threshold %.2f%% × พื้นที่ตรวจ %.0f px² ขนาด Defect = %.0f px²",
            self.size, self.defthresh_pct, inner_area_px1, self.thresh_px1,
        )
        defects_land   = [c for c, area in all_areas_land if area >= self.thresh_px1]
        self.defect1 = len(defects_land)> 0

        # log พื้นที่ contour โซน land (detect1) — เอาเฉพาะตัวเลข px² เรียงใหญ่→เล็ก
        # (ห้าม print all_areas_land ตรงๆ — ข้างในมี contour ndarray ยาวมากอ่านไม่ได้)
        areas_land = sorted((area for _, area in all_areas_land), reverse=True)
        logger.info(
            "Detection [%s]: [detect1/land] contours=%d | เกิน thresh=%d | areas(px²)=%s",
            self.size, len(areas_land), len(defects_land),
            [f"{a:.0f}" for a in areas_land],
        )

        
        t = time.perf_counter()
        inner_pipe_mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.circle(inner_pipe_mask, (cx, cy), r_inner_final, 255, -1)
        inner_pipe_masked = cv2.bitwise_and(gray, inner_pipe_mask)
        self.inner_pipe_masked = inner_pipe_masked

        th         = cv2.bitwise_and(adaptive_threshold, adaptive_threshold, mask=inner_pipe_mask)
        self.thresh = th
        t_thresh = _ms(t)

        # ── 5. Morphology ─────────────────────────────────────────────────
        t = time.perf_counter()
        k , iters    = cfg["morph_kernel"], cfg["morph_iter"]
        kernel = np.ones((k, k), np.uint8)
        clean  = cv2.morphologyEx(th,    cv2.MORPH_OPEN,  kernel, iterations=iters)
        clean  = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, kernel, iterations=iters)
        self.clean = clean
        t_morph = _ms(t)

        # ── 6. Contour + Verdict ──────────────────────────────────────────
        t = time.perf_counter()

        # แปลง threshold → px² : % ของพื้นที่วงใน
        inner_area_px2 = float(np.pi * (r_inner ** 2))
        self.thresh_px2 = ((self.defthresh_pct / 100.0)*(1/4.55)) * inner_area_px2
        logger.info(
            "Detection [%s]: [STEP] threshold %.2f%% × พื้นที่วงใน %.0f px² (r_inner=%d) = %.0f px²",
            self.size, self.defthresh_pct, inner_area_px2, r_inner, self.thresh_px2,
        )

        contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        all_areas = [(c, cv2.contourArea(c)) for c in contours]
        defects  = [c for c, area in all_areas if area >= self.thresh_px2]
        self.defect2 = len(defects)> 0

        defects_all = defects_land + defects  # รวม defect ทั้ง land และ inner
        verdict = "NG" if defects_all else "OK"

        self.defects = defects_all
        self.verdict = verdict

        t_contour = _ms(t)

        # อธิบาย threshold ที่ใช้จริง (สำหรับ debug)
        thr_desc = f"{self.defthresh_pct:.2f}%={self.thresh_px2:.0f}px²"

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
                thr_desc,
                _USE_CUDA,
            )
        else:
            rejected_areas = sorted([area for _, area in all_areas], reverse=True)
            logger.info(
                "Detection [%s]: OK | defects=0 | thresh=%s | rejected_areas(px²)=%s | GPU=%s",
                self.size,
                thr_desc,
                [f"{a:.0f}" for a in rejected_areas[:5]],
                _USE_CUDA,
            )

        # ── 7. Draw ───────────────────────────────────────────────────────
        t = time.perf_counter()
        vis = self.image.copy()
        if DEBUG_DRAW:   # วงกลม debug เฉพาะ MA mode (ลูกค้าไม่เห็นใน production)
            cv2.circle(vis, (ax,  ay),  r_outer, (255, 0, 255), 2)
            cv2.circle(vis, (bx,  by),  r_mid, (0, 255, 255), 2)
            cv2.circle(vis, (cx, cy), r_inner, (255, 144, 30), 2)

        for c in defects_land:
            bx, by, bw, bh = cv2.boundingRect(c)
            cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (255, 0, 0), 2)
            cv2.putText(vis, f"Crack", (bx, by - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

        for c in defects:
            bx, by, bw, bh = cv2.boundingRect(c)
            cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (255, 0, 0), 2)
            cv2.putText(vis, "Iron Dust", (bx, by - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
        color = (255, 0, 0) if verdict == "NG" else (0, 200, 0)
        cv2.putText(vis, verdict, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
        self.vis     = vis
        self.success = True
        t_draw = _ms(t)

        # ── Summary log (timing breakdown) ─────────────────────────────────
        t_total = _ms(t_all)
        logger.info(
            "Detection [%s] timing | "
            "pre=%.1f  outer=%.1f  inner=%.1f  thresh=%.1f  morph=%.1f  contour=%.1f  draw=%.1f  "
            "TOTAL=%.1f ms",
            self.size,
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
#     defthresh_pct = 10.0   # % ของพื้นที่วงใน (None = ใช้ default px²)

#     image = cv2.imread(img_path)
#     if image is None:
#         raise FileNotFoundError(f"Cannot read image: {img_path}")

#     det = Detection(image, size=size, defthresh_pct=defthresh_pct)
#     out = det.output

#     print(f"Result : {out['result']}")
#     print(f"Success: {out['success']}")
#     print(f"Inner radius: {det.roi_inner_raw}")
#     print(f"Threshold   : {det.thresh_px2} px² (จาก {det.defthresh_pct}%)")
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

       
        

