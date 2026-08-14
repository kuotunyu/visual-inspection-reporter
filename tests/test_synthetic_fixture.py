"""合成 fixture 產生器：確認可重現、涵蓋六類瑕疵、框在畫布內。

README 的示範標註圖由這支腳本產生，所以它的輸出穩定性直接影響素材可重現性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from make_synthetic_pcb import DEFECT_CLASSES, make_board  # noqa: E402

WIDTH, HEIGHT = 1280, 1024


def test_same_seed_is_reproducible():
    first, first_boxes = make_board(123)
    second, second_boxes = make_board(123)
    assert np.array_equal(first, second)
    assert first_boxes == second_boxes


def test_different_seed_changes_board():
    first, _ = make_board(1)
    second, _ = make_board(2)
    assert not np.array_equal(first, second)


def test_covers_every_defect_class():
    _, boxes = make_board(20260814)
    assert [b.cls for b in boxes] == list(DEFECT_CLASSES)


def test_image_shape_and_dtype():
    image, _ = make_board(7)
    assert image.shape == (HEIGHT, WIDTH, 3)
    assert image.dtype == np.uint8


@pytest.mark.parametrize("seed", [0, 42, 20260814])
def test_boxes_stay_inside_canvas_and_are_non_degenerate(seed):
    _, boxes = make_board(seed)
    for b in boxes:
        assert 0 <= b.x1 < b.x2 <= WIDTH, b
        assert 0 <= b.y1 < b.y2 <= HEIGHT, b
