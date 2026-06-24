import logging
import time
import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEBUG_DRAW = True

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
        "outer_circle": dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=177, maxRadius=184),
        "mid_circle":   dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=135, maxRadius=140),
        "inner_circle": dict(dp=1, minDist=200,  param1=50, param2=15,
                             minRadius=90,  maxRadius=100),
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
        "mid_circle":   dict(dp=1, minDist=3000, param1=50, param2=15,
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
        "mid_circle":   dict(dp=1, minDist=3000, param1=50, param2=15,
                             minRadius=55,  maxRadius=60),
        "inner_circle": dict(dp=1, minDist=3000, param1=75, param2=15,
                             minRadius=33,  maxRadius=37),
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
            "mid":   _make_cuda_detector(cfg["mid_circle"]),
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
    PCT_MIN = 0
    PCT_MAX = 100  # รับค่า 0.0–1.0 (= 0%–100%)

    def __init__(
        self,
        image: np.ndarray,
        size: str,
        # ── outer (land / หน้าแปลน) ──────────────────────────────────────
        outer_pct:       int = 100,   # พื้นที่ขั้นต่ำของ defect (0=เข้มงวดสุด, 1=หย่อนสุด)
        outer_light_pct: int = 100,   # ความมืดขั้นต่ำ (0=ต้องมืดมาก, 1=ยอมรับทุกสี)
        # ── inner (รูท่อ) ─────────────────────────────────────────────────
        inner_pct:       int = 100,   # พื้นที่ขั้นต่ำของ defect
        inner_light_pct: int = 100,   # ความมืดขั้นต่ำ
    ):
        # ── validate size ─────────────────────────────────────────────────
        size = size.upper().strip()
        if size not in self.VALID_SIZES:
            size = "M"
            logger.warning("Detection: invalid size → fallback to M")

        # ── clamp parameters 0.0–1.0 ─────────────────────────────────────
        self.outer_pct       = max(self.PCT_MIN, min(self.PCT_MAX, int(outer_pct       or 0)))
        self.outer_light_pct = max(self.PCT_MIN, min(self.PCT_MAX, int(outer_light_pct or 0)))
        self.inner_pct       = max(self.PCT_MIN, min(self.PCT_MAX, int(inner_pct       or 0)))
        self.inner_light_pct = max(self.PCT_MIN, min(self.PCT_MAX, int(inner_light_pct or 0)))

        # warning ถ้าค่าที่ส่งมาเกิน range
        for name, raw in [
            ("outer_pct",       outer_pct),
            ("outer_light_pct", outer_light_pct),
            ("inner_pct",       inner_pct),
            ("inner_light_pct", inner_light_pct),
        ]:
            v = int(raw or 0)
            if v < self.PCT_MIN or v > self.PCT_MAX:
                logger.warning(
                    "Detection [%s]: %s=%.3f out of range → clamped to [%.1f, %.1f]",
                    size, name, v, self.PCT_MIN, self.PCT_MAX,
                )

        logger.info(
            "Detection [%s]: params | "
            "outer_pct=%.2f  outer_light_pct=%.2f | "
            "inner_pct=%.2f  inner_light_pct=%.2f | "
            "engine=%s",
            size,
            self.outer_pct, self.outer_light_pct,
            self.inner_pct, self.inner_light_pct,
            "CUDA" if _USE_CUDA else "CPU",
        )

        # ── state ─────────────────────────────────────────────────────────
        self.size    = size
        self.cfg     = dict(SIZE_MAPPING[size])
        self.error   = None
        self.verdict = None
        self.success = False

        self.thresh_px_outer = None   # px² threshold จริงที่คำนวณได้ (outer)
        self.thresh_px_inner = None   # px² threshold จริงที่คำนวณได้ (inner)
        self.brightness_thresh_outer = None  # ค่าสีที่ใช้กรอง outer
        self.brightness_thresh_inner = None  # ค่าสีที่ใช้กรอง inner

        self.defect1 = False   # รอยแตก / defect บน land (outer)
        self.defect2 = False   # เศษขี้เหล็ก / defect ใน bore (inner)

        # debug images
        self.masked_pipe       = None
        self.masked_pipe_land  = None
        self.inner_roi         = None
        self.inner_pipe_masked = None
        self.clean_land        = None
        self.clean             = None
        self.thresh            = None
        self.r_inner           = None

        # ── resize ────────────────────────────────────────────────────────
        h, w = image.shape[:2]
        if (w, h) != (1280, 720):
            image = cv2.resize(image, (1280, 720), interpolation=cv2.INTER_LINEAR)
            logger.info("Detection [%s]: resized %dx%d → 1280x720", size, w, h)

        self.image = image
        self.vis   = image.copy()

        self.processing()

    # ── public output ──────────────────────────────────────────────────────
    @property
    def output(self):
        return {
            "result":  self.verdict,
            "error":   self.error,
            "vis":     self.vis,
            "defect1": self.defect1,   # รอยแตก (outer/land)
            "defect2": self.defect2,   # เศษขี้เหล็ก (inner/bore)
        }

    # ── HoughCircles (CUDA → CPU fallback) ────────────────────────────────
    def _hough_circles(self, img_gray: np.ndarray, circle_key: str) -> "np.ndarray | None":
        cfg_circle = self.cfg[f"{circle_key}_circle"]
        logger.info("GPU=%s",_USE_CUDA)
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

    # ── helper: กรอง contour ด้วยพื้นที่ + ความมืด ──────────────────────
    def _filter_defects(
        self,
        contours: list,
        gray_src: np.ndarray,
        area_thresh: int,
        brightness_thresh: int,
        zone_label: str,
    ) -> tuple[list, list]:
        """
        คืน (defects, debug_info)
        defect = contour ที่ผ่านทั้ง 2 เงื่อนไข:
          1. area >= area_thresh
          2. mean brightness ภายใน contour <= brightness_thresh
             (ยิ่ง light_pct สูง → brightness_thresh สูง → ยอมรับสีสว่างขึ้น → เจอ defect เยอะขึ้น)
        """
        defects = []
        debug   = []

        for c in contours:
            area = cv2.contourArea(c)

            # สร้าง mask เฉพาะ contour นี้เพื่อวัดความสว่างเฉลี่ย
            mask_c = np.zeros(gray_src.shape, dtype=np.uint8)
            cv2.drawContours(mask_c, [c], -1, 255, -1)
            mean_brightness = float(cv2.mean(gray_src, mask=mask_c)[0])

            pass_area   = area >= area_thresh
            pass_bright = mean_brightness <= brightness_thresh

            debug.append({
                "area":       area,
                "brightness": mean_brightness,
                "pass_area":  pass_area,
                "pass_bright": pass_bright,
            })

            if pass_area and pass_bright:
                defects.append(c)

        # log รายละเอียดทุก contour ใน zone นี้
        logger.debug(
            "Detection [%s]: [%s] contour details: %s",
            self.size, zone_label,
            [
                f"area={d['area']:.0f}({'✓' if d['pass_area'] else '✗'}) "
                f"bright={d['brightness']:.1f}({'✓' if d['pass_bright'] else '✗'})"
                for d in debug
            ],
        )

        return defects, debug

    # ── main processing ────────────────────────────────────────────────────
    def processing(self):
        cfg   = self.cfg
        t_all = time.perf_counter()

        # ── 1. Preprocess ──────────────────────────────────────────────────
        t = time.perf_counter()
        gray = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
        blur = cv2.medianBlur(gray, cfg["blur_ksize"])

        # Global threshold — ใช้ทั้ง outer และ inner
        # ยิ่ง light_pct สูง → thresh_val สูง → จับ defect สีสว่างขึ้นได้ด้วย
        outer_thresh_val = int(255 * (0.2 * (self.outer_light_pct/100)))
        inner_thresh_val = int(255 * (0.6522 * (self.inner_light_pct/100)))

        self.brightness_thresh_outer = outer_thresh_val
        self.brightness_thresh_inner = inner_thresh_val

        # threshold ภาพ (ใช้เป็น source สำหรับ morphology)
        _, thresh_outer_img = cv2.threshold(blur, outer_thresh_val, 255, cv2.THRESH_BINARY_INV)
        thresh_inner_img = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            cfg["thresh_block"],
            cfg["thresh_c"],
        )

        t_pre = _ms(t)
        logger.info(
            "Detection [%s]: preprocess done | "
            "outer_thresh_val=%d  inner_thresh_val=%d | pre=%.1f ms",
            self.size, outer_thresh_val, inner_thresh_val, t_pre,
        )

        # ── 2. Outer HoughCircles ──────────────────────────────────────────
        t = time.perf_counter()
        outer_circle = self._hough_circles(gray, "outer")
        t_outer = _ms(t)

        if outer_circle is None:
            logger.warning("Detection [%s]: outer circle not found", self.size)
            self.error   = f"[Size {self.size}] Pipe not found"
            self.verdict = "NG"
            cv2.putText(self.vis, self.error, (20, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            logger.info("Detection [%s] timing | pre=%.1f  outer=%.1f  TOTAL=%.1f ms",
                        self.size, t_pre, t_outer, _ms(t_all))
            return

        ax, ay, r_outer = np.round(outer_circle[0]).astype(int)[0]
        r_outer_shrink  = int(r_outer * cfg["outer_shrink"])
        logger.info("Detection [%s]: outer circle | center=(%d,%d) r=%d  shrink_r=%d | %.1f ms",
                    self.size, ax, ay, r_outer, r_outer_shrink, t_outer)

        # mask วงนอก
        pipe_mask = np.zeros_like(gray)
        cv2.circle(pipe_mask, (ax, ay), r_outer_shrink, 255, -1)
        masked_pipe      = cv2.bitwise_and(gray, pipe_mask)
        self.masked_pipe = masked_pipe

        # ── 2b. Mid HoughCircles ───────────────────────────────────────────
        t = time.perf_counter()
        mid_circle = self._hough_circles(masked_pipe, "mid")
        t_mid = _ms(t)

        if mid_circle is None:
            logger.warning("Detection [%s]: mid circle not found", self.size)
            self.error   = f"[Size {self.size}] Mid pipe not found"
            self.verdict = "NG"
            cv2.putText(self.vis, self.error, (20, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            logger.info("Detection [%s] timing | pre=%.1f  outer=%.1f  mid=%.1f  TOTAL=%.1f ms",
                        self.size, t_pre, t_outer, t_mid, _ms(t_all))
            return

        mx, my, r_mid = np.round(mid_circle[0]).astype(int)[0]
        logger.info("Detection [%s]: mid circle | center=(%d,%d) r=%d | %.1f ms",
                    self.size, mx, my, r_mid, t_mid)

        mid_circle_mask = np.zeros_like(gray)
        cv2.circle(mid_circle_mask, (mx, my), r_mid, 255, -1)
        inner_roi      = cv2.bitwise_and(gray, mid_circle_mask)
        self.inner_roi = inner_roi

        # ── 3. Inner HoughCircles ──────────────────────────────────────────
        t = time.perf_counter()
        inner_circle = self._hough_circles(inner_roi, "inner")
        t_inner = _ms(t)

        if inner_circle is None:
            logger.warning("Detection [%s]: inner circle not found", self.size)
            self.error   = f"[Size {self.size}] Inner pipe not found"
            self.verdict = "NG"
            if DEBUG_DRAW:
                cv2.circle(self.vis, (ax, ay), r_outer, (0, 255, 0), 2)
            cv2.putText(self.vis, self.error, (20, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            logger.info(
                "Detection [%s] timing | pre=%.1f  outer=%.1f  mid=%.1f  inner=%.1f  TOTAL=%.1f ms",
                self.size, t_pre, t_outer, t_mid, t_inner, _ms(t_all))
            return

        cx, cy, r_inner = np.round(inner_circle[0]).astype(int)[0]
        self.r_inner    = r_inner
        r_inner_final   = max(int(r_inner * cfg["inner_shrink"]), 10)
        logger.info("Detection [%s]: inner circle | center=(%d,%d) r=%d  shrink_r=%d | %.1f ms",
                    self.size, cx, cy, r_inner, r_inner_final, t_inner)

        # ══════════════════════════════════════════════════════════════════
        # ── 4. OUTER DEFECT (land / หน้าแปลน) ────────────────────────────
        # โซน = วงแหวนระหว่าง r_outer_shrink กับ r_mid
        # ใช้ Global Threshold + เช็คพื้นที่ + เช็คความมืด
        # ══════════════════════════════════════════════════════════════════
        t = time.perf_counter()

        pipe_land_mask = np.zeros_like(gray)
        cv2.circle(pipe_land_mask, (ax, ay), r_outer, 255, -1)
        cv2.circle(pipe_land_mask, (mx, my), r_mid, 0, -1)           # เจาะรูตรงกลางออก
        masked_pipe_land      = cv2.bitwise_and(gray, pipe_land_mask)
        self.masked_pipe_land = masked_pipe_land

        # apply global threshold เฉพาะโซน land
        th_land = cv2.bitwise_and(thresh_outer_img, thresh_outer_img, mask=pipe_land_mask)

        k, iters = cfg["morph_kernel"], cfg["morph_iter"]
        kernel   = np.ones((k, k), np.uint8)
        clean_land = cv2.morphologyEx(th_land, cv2.MORPH_OPEN,  kernel, iterations=iters)
        clean_land = cv2.morphologyEx(clean_land, cv2.MORPH_CLOSE, kernel, iterations=iters)
        self.clean_land = clean_land

        contours_land, _ = cv2.findContours(clean_land, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # คำนวณ area threshold จริง (px²)
        # outer_pct=0 → thresh=0 (เข้มงวดสุด ทุก contour เป็น defect)
        # outer_pct=1 → thresh=area_land (ต้องใหญ่มากๆ ถึงเป็น defect)
        land_area_total   = float(np.pi * r_outer_shrink**2 - np.pi * r_mid**2)
        self.thresh_px_outer = ((1-(self.outer_pct/100))*(1/39.8)) * land_area_total

        logger.info(
            "Detection [%s]: [outer/land] "
            "zone_area=%.0f px²  outer_pct=%.2f  thresh_area=%.0f px²  "
            "brightness_thresh=%d  contours_found=%d",
            self.size,
            land_area_total, self.outer_pct, self.thresh_px_outer,
            self.brightness_thresh_outer, len(contours_land),
        )

        defects_land, dbg_land = self._filter_defects(
            contours_land, gray,
            area_thresh=self.thresh_px_outer,
            brightness_thresh=self.brightness_thresh_outer,
            zone_label="outer/land",
        )
        self.defect1 = len(defects_land) > 0

        areas_land_all = sorted([cv2.contourArea(c) for c in contours_land], reverse=True)
        logger.info(
            "Detection [%s]: [outer/land] result | "
            "total_contours=%d  passed=%d  defect1=%s | "
            "all_areas(px²)=%s",
            self.size,
            len(contours_land), len(defects_land), self.defect1,
            [f"{a:.0f}" for a in areas_land_all],
        )

        t_outer_defect = _ms(t)

        # ══════════════════════════════════════════════════════════════════
        # ── 5. INNER DEFECT (bore / รูท่อ) ───────────────────────────────
        # โซน = ภายใน r_inner_final
        # ใช้ Global Threshold + เช็คพื้นที่ + เช็คความมืด
        # ══════════════════════════════════════════════════════════════════
        t = time.perf_counter()

        inner_pipe_mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.circle(inner_pipe_mask, (cx, cy), r_inner_final, 255, -1)
        inner_pipe_masked      = cv2.bitwise_and(gray, inner_pipe_mask)
        self.inner_pipe_masked = inner_pipe_masked

        # apply global threshold เฉพาะโซน inner
        th_inner = cv2.bitwise_and(thresh_inner_img, thresh_inner_img, mask=inner_pipe_mask)
        self.thresh = th_inner

        clean_inner = cv2.morphologyEx(th_inner, cv2.MORPH_OPEN,  kernel, iterations=iters)
        clean_inner = cv2.morphologyEx(clean_inner, cv2.MORPH_CLOSE, kernel, iterations=iters)
        self.clean  = clean_inner

        contours_inner, _ = cv2.findContours(clean_inner, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # คำนวณ area threshold จริง (px²)
        inner_area_total     = float(np.pi * r_inner**2)
        self.thresh_px_inner = ((1-(self.inner_pct/100))*(1/4.55)) * inner_area_total

        logger.info(
            "Detection [%s]: [inner/bore] "
            "zone_area=%.0f px²  inner_pct=%.2f  thresh_area=%.0f px²  "
            "brightness_thresh=%d  contours_found=%d",
            self.size,
            inner_area_total, self.inner_pct, self.thresh_px_inner,
            self.brightness_thresh_inner, len(contours_inner),
        )

        defects_inner, dbg_inner = self._filter_defects(
            contours_inner, gray,
            area_thresh=self.thresh_px_inner,
            brightness_thresh=self.brightness_thresh_inner,
            zone_label="inner/bore",
        )
        self.defect2 = len(defects_inner) > 0

        areas_inner_all = sorted([cv2.contourArea(c) for c in contours_inner], reverse=True)
        logger.info(
            "Detection [%s]: [inner/bore] result | "
            "total_contours=%d  passed=%d  defect2=%s | "
            "all_areas(px²)=%s",
            self.size,
            len(contours_inner), len(defects_inner), self.defect2,
            [f"{a:.0f}" for a in areas_inner_all],
        )

        t_inner_defect = _ms(t)

        # ── 6. Verdict ────────────────────────────────────────────────────
        defects_all  = defects_land + defects_inner
        self.verdict = "NG" if defects_all else "OK"

        if defects_all:
            logger.info(
                "Detection [%s]: VERDICT=%s | "
                "defects=%d (land=%d, inner=%d) | "
                "outer_pct=%.2f outer_light=%.2f inner_pct=%.2f inner_light=%.2f | "
                "GPU=%s",
                self.size, self.verdict,
                len(defects_all), len(defects_land), len(defects_inner),
                self.outer_pct, self.outer_light_pct,
                self.inner_pct, self.inner_light_pct,
                _USE_CUDA,
            )
        else:
            logger.info(
                "Detection [%s]: VERDICT=OK | no defects | "
                "outer_pct=%.2f outer_light=%.2f inner_pct=%.2f inner_light=%.2f | "
                "GPU=%s",
                self.size,
                self.outer_pct, self.outer_light_pct,
                self.inner_pct, self.inner_light_pct,
                _USE_CUDA,
            )

        # ── 7. Draw ───────────────────────────────────────────────────────
        t = time.perf_counter()
        vis = self.image.copy()

        if DEBUG_DRAW:
            cv2.circle(vis, (ax, ay), r_outer,       (255,   0, 255), 2)
            cv2.circle(vis, (mx, my), r_mid,          (  0, 255, 255), 2)
            cv2.circle(vis, (cx, cy), r_inner,        (255, 144,  30), 2)

        for c in defects_land:
            lx, ly, lw, lh = cv2.boundingRect(c)
            cv2.rectangle(vis, (lx, ly), (lx + lw, ly + lh), (255, 0, 0), 2)
            cv2.putText(vis, "Crack", (lx, ly - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

        for c in defects_inner:
            ix, iy, iw, ih = cv2.boundingRect(c)
            cv2.rectangle(vis, (ix, iy), (ix + iw, iy + ih), (255, 0, 0), 2)
            cv2.putText(vis, "Iron Dust", (ix, iy - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

        color = (255, 0, 0) if self.verdict == "NG" else (0, 200, 0)
        cv2.putText(vis, self.verdict, (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        self.vis     = vis
        self.success = True
        t_draw = _ms(t)

        # ── timing summary ────────────────────────────────────────────────
        t_total = _ms(t_all)
        logger.info(
            "Detection [%s] timing | "
            "pre=%.1f  outer=%.1f  mid=%.1f  inner=%.1f  "
            "outer_defect=%.1f  inner_defect=%.1f  draw=%.1f  TOTAL=%.1f ms",
            self.size,
            t_pre, t_outer, t_mid, t_inner,
            t_outer_defect, t_inner_defect, t_draw,
            t_total,
        )


# # ── Quick test ─────────────────────────────────────────────────────────────
# if __name__ == "__main__":
#     logging.basicConfig(level=logging.INFO)

#     # img_path = r"C:\Work\Praram Nine\Smartsense\Pipe_Detection_Dataset\Video_1_Size_L\frame_000000.jpg"
#     # img_path = r"C:\Users\Mate\Downloads\290282f1-b2d1-4b78-b0bb-0360ef428428.jpg"
#     img_path = r"C:\Work\Praram Nine\Smartsense\Pipe_Detection_Dataset\Video_3_Size_M\frame_023027.jpg"
#     # img_path = r"C:\Users\Mate\Downloads\drive-download-20260524T075736Z-3-001\IMG_7037.PNG"
#     # img_path = r"C:\Users\Mate\Downloads\drive-download-20260524T075736Z-3-001\IMG_7036.PNG"
#     # img_path = r"C:\Work\Praram Nine\Smartsense\Pipe_Detection_Dataset\Video_2_Size_L\frame_012887.jpg"
#     size            = "M"
#     outer_pct       = 100
#     outer_light_pct = 50
#     inner_pct       = 100
#     inner_light_pct = 100

#     print(f"CUDA available: {_USE_CUDA}")

#     image = cv2.imread(img_path)
#     if image is None:
#         raise FileNotFoundError(f"Cannot read image: {img_path}")

#     h, w = image.shape[:2]
#     print(f"Image size  : {w} x {h} px")

#     det = Detection(
#         image,
#         size            = size,
#         outer_pct       = outer_pct,
#         outer_light_pct = outer_light_pct,
#         inner_pct       = inner_pct,
#         inner_light_pct = inner_light_pct,
#     )
#     out = det.output

#     print(f"Result      : {out['result']}")
#     print(f"Defect1 (Crack)     : {out['defect1']}")
#     print(f"Defect2 (Iron Dust) : {out['defect2']}")
#     print(f"thresh_px_outer     : {det.thresh_px_outer}")
#     print(f"thresh_px_inner     : {det.thresh_px_inner}")
#     print(f"brightness_outer    : {det.brightness_thresh_outer}")
#     print(f"brightness_inner    : {det.brightness_thresh_inner}")
#     if out["error"]:
#         print(f"Error       : {out['error']}")

#     cv2.imshow("Result", out["vis"])

#     if det.masked_pipe is not None:
#         cv2.imshow("Outer Masked", det.masked_pipe)
#     if det.masked_pipe_land is not None:
#         cv2.imshow("Land Masked", det.masked_pipe_land)
#     if det.inner_roi is not None:
#         cv2.imshow("Inner ROI", det.inner_roi)
#     if det.inner_pipe_masked is not None:
#         cv2.imshow("Inner Masked", det.inner_pipe_masked)
#     if det.clean_land is not None:
#         cv2.imshow("Threshold Land", det.clean_land)
#     if det.clean is not None:
#         cv2.imshow("Threshold Inner", det.clean)

#     cv2.waitKey(0)
#     cv2.destroyAllWindows()