"""產生完全自製、授權明確的合成 PCB 影像，作為 demo 素材與測試 fixture。

HRIPCB 等公開 PCB 瑕疵資料集的授權標示為 Unknown，其轉換後影像不適合隨公開
repo 散布（見 README「資料集與授權」）。這支腳本用 OpenCV 幾何繪製從零合成
板面與六類瑕疵，輸出影像與本 repo 同授權，可自由公開。

合成板面「像 PCB」但不是任何真實產品的照片，也不是任何資料集影像的衍生物；
瑕疵座標由腳本自己決定，因此同時可當成偵測結果的 ground truth 對照。

用法：
    uv run python scripts/make_synthetic_pcb.py --output-dir sample_images_synthetic
    uv run python scripts/make_synthetic_pcb.py --output-dir out --count 3 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

# BGR（cv2 慣例）。色票刻意貼近常見綠色阻焊板，但屬自行挑選的數值。
BACKDROP = (176, 176, 176)
SUBSTRATE = (48, 104, 40)
MASK = (56, 132, 48)
COPPER = (72, 168, 62)
COPPER_EDGE = (34, 78, 30)
PAD = (176, 182, 186)
DRILL = (40, 44, 46)
SILK = (232, 236, 232)

DEFECT_CLASSES = (
    "missing_hole",
    "mouse_bite",
    "open_circuit",
    "short",
    "spur",
    "spurious_copper",
)


@dataclass(frozen=True)
class DefectBox:
    """腳本自己畫下的瑕疵位置——合成影像的 ground truth。"""

    cls: str
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class Board:
    canvas: np.ndarray
    rect: tuple[int, int, int, int]  # 板面 left, top, right, bottom
    h_traces: list[tuple[int, int, int, int]]  # 水平走線 (x1, y, x2, width)
    v_traces: list[tuple[int, int, int, int]]  # 垂直走線 (x, y1, y2, width)
    pads: list[tuple[int, int, int]]  # 銲墊 (cx, cy, radius)


def _draw_substrate(canvas: np.ndarray, rect: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = rect
    cv2.rectangle(canvas, (left, top), (right, bottom), SUBSTRATE, -1)
    inset = 6
    cv2.rectangle(canvas, (left + inset, top + inset), (right - inset, bottom - inset), MASK, -1)


def _draw_trace(canvas: np.ndarray, p1: tuple[int, int], p2: tuple[int, int], width: int) -> None:
    """走線畫兩層：外圈深色模擬阻焊邊緣，內層亮綠模擬覆銅。"""
    cv2.line(canvas, p1, p2, COPPER_EDGE, width + 4)
    cv2.line(canvas, p1, p2, COPPER, width)


def _draw_pad(canvas: np.ndarray, cx: int, cy: int, radius: int, *, drilled: bool = True) -> None:
    cv2.circle(canvas, (cx, cy), radius + 2, COPPER_EDGE, -1)
    cv2.circle(canvas, (cx, cy), radius, PAD, -1)
    if drilled:
        cv2.circle(canvas, (cx, cy), max(2, radius // 2), DRILL, -1)


def _build_board(rng: random.Random, width: int, height: int) -> Board:
    canvas = np.full((height, width, 3), BACKDROP, dtype=np.uint8)
    margin = 40
    rect = (margin, margin, width - margin, height - margin)
    _draw_substrate(canvas, rect)
    left, top, right, bottom = rect

    inner = (left + 40, top + 40, right - 40, bottom - 40)
    ix1, iy1, ix2, iy2 = inner

    h_traces: list[tuple[int, int, int, int]] = []
    for y in range(iy1 + 30, iy2 - 30, 62):
        x_end = rng.randrange(ix1 + (ix2 - ix1) // 2, ix2)
        w = rng.choice((7, 9, 11))
        _draw_trace(canvas, (ix1, y), (x_end, y), w)
        h_traces.append((ix1, y, x_end, w))

    v_traces: list[tuple[int, int, int, int]] = []
    for x in range(ix1 + 90, ix2 - 40, 74):
        y_end = rng.randrange(iy1 + (iy2 - iy1) // 2, iy2)
        w = rng.choice((7, 9))
        _draw_trace(canvas, (x, iy1), (x, y_end), w)
        v_traces.append((x, iy1, y_end, w))

    pads: list[tuple[int, int, int]] = []
    for row, (_, y, x_end, _) in enumerate(h_traces):
        for col in range(3):
            cx = ix1 + 60 + col * 130
            if cx > x_end - 20:
                continue
            radius = 13 if (row + col) % 3 else 16
            _draw_pad(canvas, cx, y, radius)
            pads.append((cx, y, radius))

    cv2.putText(canvas, "SYNTHETIC", (ix1 + 20, iy2 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, SILK, 2)
    cv2.putText(canvas, "FIXTURE", (ix2 - 150, iy1 - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, SILK, 2)
    return Board(canvas, rect, h_traces, v_traces, pads)


def _missing_hole(board: Board, rng: random.Random) -> DefectBox:
    """把一個銲墊的鑽孔補起來——外觀是「該有孔的地方沒有孔」。"""
    cx, cy, radius = rng.choice(board.pads)
    cv2.circle(board.canvas, (cx, cy), max(2, radius // 2) + 1, PAD, -1)
    pad = radius + 8
    return DefectBox("missing_hole", cx - pad, cy - pad, cx + pad, cy + pad)


def _mouse_bite(board: Board, rng: random.Random) -> DefectBox:
    """走線邊緣連續啃缺——用一排小圓形挖掉銅面。"""
    x1, y, x2, w = rng.choice(board.h_traces)
    x = rng.randrange(x1 + 120, max(x1 + 140, x2 - 60))
    bite = max(3, w // 2)
    for i in range(4):
        cv2.circle(board.canvas, (x + i * bite, y - w // 2), bite, MASK, -1)
    pad = 22
    return DefectBox("mouse_bite", x - pad, y - pad, x + 4 * bite + pad, y + pad)


def _open_circuit(board: Board, rng: random.Random) -> DefectBox:
    """走線中間斷開一小段。"""
    x1, y, x2, w = rng.choice(board.h_traces)
    x = rng.randrange(x1 + 160, max(x1 + 180, x2 - 80))
    gap = 14
    cv2.rectangle(
        board.canvas, (x, y - w // 2 - 2), (x + gap, y + w // 2 + 2), MASK, -1
    )
    pad = 24
    return DefectBox("open_circuit", x - pad, y - pad, x + gap + pad, y + pad)


def _short(board: Board, rng: random.Random) -> DefectBox:
    """相鄰兩條走線之間長出銅橋。"""
    idx = rng.randrange(0, len(board.h_traces) - 1)
    x1, y_a, x2, w = board.h_traces[idx]
    _, y_b, x2_b, _ = board.h_traces[idx + 1]
    x = rng.randrange(x1 + 200, max(x1 + 220, min(x2, x2_b) - 60))
    _draw_trace(board.canvas, (x, y_a), (x, y_b), max(6, w - 2))
    pad = 20
    return DefectBox("short", x - pad, y_a - pad, x + pad, y_b + pad)


def _spur(board: Board, rng: random.Random) -> DefectBox:
    """走線側邊多出一根短毛刺。"""
    x1, y, x2, w = rng.choice(board.h_traces)
    x = rng.randrange(x1 + 140, max(x1 + 160, x2 - 60))
    length = 26
    _draw_trace(board.canvas, (x, y), (x + 10, y - length), max(5, w - 3))
    pad = 20
    return DefectBox("spur", x - pad, y - length - pad, x + 10 + pad, y + pad)


def _spurious_copper(board: Board, rng: random.Random) -> DefectBox:
    """走線之間出現一塊不該存在的孤立銅。"""
    idx = rng.randrange(0, len(board.h_traces) - 1)
    x1, y_a, x2, _ = board.h_traces[idx]
    _, y_b, x2_b, _ = board.h_traces[idx + 1]
    x = rng.randrange(x1 + 240, max(x1 + 260, min(x2, x2_b) - 60))
    cy = (y_a + y_b) // 2
    blob = np.array(
        [[x, cy - 12], [x + 30, cy - 16], [x + 36, cy + 8], [x + 8, cy + 14]], dtype=np.int32
    )
    cv2.fillPoly(board.canvas, [blob], COPPER_EDGE)
    cv2.fillPoly(board.canvas, [(blob * 0.92 + np.array([x * 0.08, cy * 0.08])).astype(np.int32)], COPPER)
    pad = 20
    return DefectBox("spurious_copper", x - pad, cy - 16 - pad, x + 36 + pad, cy + 14 + pad)


_INJECTORS = {
    "missing_hole": _missing_hole,
    "mouse_bite": _mouse_bite,
    "open_circuit": _open_circuit,
    "short": _short,
    "spur": _spur,
    "spurious_copper": _spurious_copper,
}


def _add_capture_noise(canvas: np.ndarray, rng: random.Random) -> np.ndarray:
    """輕微的雜訊與亮度不均，讓合成板面不會過度乾淨。"""
    noise = np.random.default_rng(rng.randrange(2**31)).normal(0, 3.2, canvas.shape)
    h, w = canvas.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    vignette = 1.0 - 0.10 * (((xx - w / 2) / w) ** 2 + ((yy - h / 2) / h) ** 2) * 4
    out = canvas.astype(np.float32) * vignette[..., None] + noise
    return np.clip(out, 0, 255).astype(np.uint8)


def make_board(
    seed: int, classes: tuple[str, ...] = DEFECT_CLASSES, width: int = 1280, height: int = 1024
) -> tuple[np.ndarray, list[DefectBox]]:
    """回傳 (BGR 影像, 腳本畫下的瑕疵框)。同一個 seed 一定產生同一張圖。"""
    rng = random.Random(seed)
    board = _build_board(rng, width, height)
    boxes = [_INJECTORS[cls](board, rng) for cls in classes]
    return _add_capture_noise(board.canvas, rng), boxes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, required=True, help="影像輸出資料夾")
    parser.add_argument("--count", type=int, default=5, help="產生張數（預設 5）")
    parser.add_argument("--seed", type=int, default=20260814, help="亂數種子（固定則輸出可重現）")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i in range(args.count):
        seed = args.seed + i
        image, boxes = make_board(seed)
        name = f"synthetic_pcb_{i + 1:02d}.jpg"
        cv2.imwrite(str(args.output_dir / name), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        manifest.append({"image": name, "seed": seed, "defects": [asdict(b) for b in boxes]})

    (args.output_dir / "synthetic_manifest.json").write_text(
        json.dumps(
            {
                "generator": "scripts/make_synthetic_pcb.py",
                "license": "與本 repo 同授權（AGPL-3.0-or-later）；非任何資料集影像的衍生物",
                "boards": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已輸出 {args.count} 張合成影像與 ground-truth manifest 到 {args.output_dir}")


if __name__ == "__main__":
    main()
