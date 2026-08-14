"""用合成 fixture 重新產生 README 用的示範標註圖（授權明確、可重現）。

輸入是 scripts/make_synthetic_pcb.py 畫出來的合成板面與它自己記錄的瑕疵座標
（ground truth），輸出走的是 src/inspector/findings.py 的真實標註渲染程式碼，
因此這張圖確實展示了標籤自動避讓、finding id 與類別的排版行為。

**這張圖不是模型預測結果**：偵測權重是在真實 HRIPCB 照片上訓練的，對合成板面
有明顯 domain shift（實測會把每個銲墊誤判為 missing_hole，conf 甚至到 0.83），
所以示範圖刻意改用 ground-truth 框，避免用一張失真的推論結果冒充偵測品質。
偵測器的真實表現數字一律以上游 pcb-defect-detection 專案的測試集結果為準。

用法：
    uv run python scripts/render_sample_assets.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

# 讓同目錄的 make_synthetic_pcb 可以被 import（inspector 已由 uv sync 安裝）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402

from inspector.detector import Detection  # noqa: E402
from inspector.findings import Finding, annotate  # noqa: E402
from make_synthetic_pcb import DEFECT_CLASSES, make_board  # noqa: E402

# 示範圖用固定 seed，重跑輸出一致。
SAMPLE_SEED = 20260814
# 假的信心值：只是為了讓標註圖呈現真實排版時的欄位樣貌，不代表任何模型輸出。
GROUND_TRUTH_CONF = 1.0


def render(seed: int, destination: Path) -> Path:
    image_bgr, boxes = make_board(seed)
    image = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))

    findings = [
        Finding(
            i + 1,
            Detection(
                class_id=DEFECT_CLASSES.index(b.cls),
                class_name=b.cls,
                xyxy=(float(b.x1), float(b.y1), float(b.x2), float(b.y2)),
                conf=GROUND_TRUTH_CONF,
            ),
        )
        for i, b in enumerate(boxes)
    ]

    destination.parent.mkdir(parents=True, exist_ok=True)
    annotate(image, findings).save(destination, quality=90)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets" / "sample_annotated.jpg",
    )
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args()
    path = render(args.seed, args.output)
    print(f"已產生示範標註圖：{path}")


if __name__ == "__main__":
    main()
