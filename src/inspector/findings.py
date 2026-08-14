"""每張影像的 findings 組裝：編號、標註整圖、context 裁切、給 VLM 的偵測 JSON。

編號（#1..N，依信心降冪）同時出現在標註圖與 JSON 中，讓 VLM 的回覆
能被 schema 強制對齊到具體 finding，防止張冠李戴。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from inspector.config import (
    ANNOTATED_MAX_SIDE,
    CROP_EXPAND,
    CROP_MIN_SIDE,
    CROP_UPSCALE_BELOW,
)
from inspector.detector import Detection

# 依 class_id 位置取色（RGB），跟類別名稱脫鉤——這樣任何領域的類別表
# （6 類 PCB 瑕疵、10 類 VisDrone 物件…）都能直接用，不用逐類別維護色表。
# 前 6 色沿用原 PCB 專案挑選、在深綠色基板上對比明顯的顏色，其餘為常見高對比色。
PALETTE: list[tuple[int, int, int]] = [
    (255, 64, 64),
    (255, 160, 0),
    (255, 255, 80),
    (255, 0, 255),
    (0, 224, 255),
    (255, 255, 255),
    (80, 255, 80),
    (255, 120, 180),
    (140, 90, 255),
    (0, 160, 255),
]

LabelRect = tuple[int, int, int, int]  # left, top, right, bottom；右/下為 exclusive


def _color_for(class_id: int) -> tuple[int, int, int]:
    return PALETTE[class_id % len(PALETTE)]


def _intersection_area(a: LabelRect, b: LabelRect) -> int:
    """兩個標籤矩形的交集面積；相鄰但未重疊時為 0。"""
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )


def _clamp_label_rect(
    left: int,
    top: int,
    width: int,
    height: int,
    canvas_size: tuple[int, int],
) -> LabelRect:
    """把標籤完整限制在畫布內；極小畫布則讓背景填滿可用範圍。"""
    canvas_w, canvas_h = canvas_size
    width = min(max(1, width), canvas_w)
    height = min(max(1, height), canvas_h)
    left = min(max(0, left), canvas_w - width)
    top = min(max(0, top), canvas_h - height)
    return left, top, left + width, top + height


def _place_label_rect(
    bbox: tuple[int, int, int, int],
    label_size: tuple[int, int],
    canvas_size: tuple[int, int],
    occupied: list[LabelRect],
    gap: int = 4,
) -> LabelRect:
    """依序嘗試框外/框內候選位置，優先選不與既有標籤碰撞者。

    所有候選都會先 clamp 到畫布；密集到完全無空位時，選總重疊面積最小者，
    因此輸出仍是 deterministic，也不會從影像右側或上下緣溢出。
    """
    x1, y1, x2, y2 = bbox
    label_w, label_h = label_size
    anchors = [
        (x1, y1 - gap - label_h),  # 框外上方，左對齊（一般情況）
        (x1, y2 + gap),  # 框外下方
        (x2 - label_w, y1 - gap - label_h),  # 上方，右對齊
        (x2 - label_w, y2 + gap),  # 下方，右對齊
        (x2 + gap, y1),  # 框右側
        (x1 - gap - label_w, y1),  # 框左側
        (x1, y1 + gap),  # 框內上方（最後備援）
        (x1, y2 - gap - label_h),  # 框內下方
    ]
    candidates: list[LabelRect] = []
    for left, top in anchors:
        rect = _clamp_label_rect(left, top, label_w, label_h, canvas_size)
        if rect not in candidates:
            candidates.append(rect)

    for rect in candidates:
        if all(_intersection_area(rect, other) == 0 for other in occupied):
            return rect
    return min(candidates, key=lambda rect: sum(_intersection_area(rect, other) for other in occupied))


@dataclass(frozen=True)
class Finding:
    finding_id: int  # 1-based，畫在標註圖上、也出現在偵測 JSON
    detection: Detection


@dataclass
class ImageFindings:
    image_path: Path
    image_size: tuple[int, int]  # (w, h)
    findings: list[Finding]


def build_findings(image_path: Path, image_size: tuple[int, int], detections: list[Detection]) -> ImageFindings:
    """依信心降冪排序並編號（#1 = 最高信心）。"""
    ordered = sorted(detections, key=lambda d: d.conf, reverse=True)
    findings = [Finding(i + 1, det) for i, det in enumerate(ordered)]
    return ImageFindings(image_path, image_size, findings)


def annotate(image: Image.Image, findings: list[Finding], max_side: int = ANNOTATED_MAX_SIDE) -> Image.Image:
    """在整圖上畫編號框（#id class），再把最長邊縮到 max_side。

    先在原解析度作畫再縮圖，框線與文字大小依影像尺寸調整。
    cv2.putText 不支援中文，圖上標籤用英文類名；中文對照在報告表格呈現。
    """
    canvas = np.asarray(image.convert("RGB")).copy()
    h, w = canvas.shape[:2]
    thickness = max(2, round(min(w, h) / 350))
    font_scale = min(max(min(w, h) / 1000.0, 0.7), 1.6)
    font = cv2.FONT_HERSHEY_SIMPLEX
    occupied_labels: list[LabelRect] = []
    pad_x, pad_y, gap = 4, 3, max(4, thickness * 2)

    for f in findings:
        x1, y1, x2, y2 = (round(v) for v in f.detection.xyxy)
        x1, x2 = sorted((min(max(x1, 0), w - 1), min(max(x2, 0), w - 1)))
        y1, y2 = sorted((min(max(y1, 0), h - 1), min(max(y2, 0), h - 1)))
        color = _color_for(f.detection.class_id)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)

        label = f"#{f.finding_id} {f.detection.class_name}"
        label_scale = font_scale
        (tw, th), baseline = cv2.getTextSize(label, font, label_scale, thickness)
        max_text_width = max(1, w - 2 * pad_x)
        if tw > max_text_width:
            label_scale *= max_text_width / tw
            (tw, th), baseline = cv2.getTextSize(label, font, label_scale, thickness)

        label_rect = _place_label_rect(
            (x1, y1, x2, y2),
            (tw + 2 * pad_x, th + baseline + 2 * pad_y),
            (w, h),
            occupied_labels,
            gap,
        )
        left, top, right, bottom = label_rect
        cv2.rectangle(canvas, (left, top), (right - 1, bottom - 1), color, -1)
        cv2.putText(
            canvas,
            label,
            (left + pad_x, top + pad_y + th),
            font,
            label_scale,
            (0, 0, 0),
            thickness,
            cv2.LINE_AA,
        )
        occupied_labels.append(label_rect)

    if max(w, h) > max_side:
        gain = max_side / max(w, h)
        canvas = cv2.resize(canvas, (round(w * gain), round(h * gain)), interpolation=cv2.INTER_AREA)
    return Image.fromarray(canvas)


def crop_finding(
    image: Image.Image,
    finding: Finding,
    expand: float = CROP_EXPAND,
    min_side: int = CROP_MIN_SIDE,
) -> Image.Image:
    """以 bbox 為中心外擴 expand 倍（至少 min_side px）從原圖裁切，保留周邊 context。"""
    x1, y1, x2, y2 = finding.detection.xyxy
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half_w = max((x2 - x1) * expand, min_side) / 2
    half_h = max((y2 - y1) * expand, min_side) / 2

    w, h = image.size
    left = max(0, round(cx - half_w))
    top = max(0, round(cy - half_h))
    right = min(w, round(cx + half_w))
    bottom = min(h, round(cy + half_h))
    crop = image.crop((left, top, right, bottom))

    # 小裁切放大再送 VLM：解析度不足時模型會把細微瑕疵（如殘銅細線）誤判為誤檢
    if max(crop.size) < CROP_UPSCALE_BELOW:
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    return crop


def findings_to_json(fi: ImageFindings, class_names_zh: dict[str, str]) -> list[dict]:
    """給 VLM 的偵測 JSON：id、類別（英/中）、信心、歸一化 bbox。"""
    w, h = fi.image_size
    return [
        {
            "id": f.finding_id,
            "class": f.detection.class_name,
            "class_zh": class_names_zh[f.detection.class_name],
            "confidence": round(f.detection.conf, 3),
            "bbox_norm": [
                round(f.detection.xyxy[0] / w, 4),
                round(f.detection.xyxy[1] / h, 4),
                round(f.detection.xyxy[2] / w, 4),
                round(f.detection.xyxy[3] / h, 4),
            ],
        }
        for f in fi.findings
    ]
