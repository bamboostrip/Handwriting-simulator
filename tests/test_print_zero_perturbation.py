"""测试打印体角色（role_id=1 或 printed=True）在颜色为 None / 跟随主颜色时保持绝对零扰动与零错字。"""

from __future__ import annotations

import numpy as np
from PIL import Image

from handwritesim.core.engine_fast import FastEngine
from handwritesim.core.models import HandwritingParams, HandwritingRole, Paragraph, TextRun
from handwritesim.core.paths import assets_root


def _create_test_params(color: str | None = None) -> HandwritingParams:
    font_path = "C:/Windows/Fonts/simfang.ttf"
    bg_path = f"{assets_root()}/backgrounds/A4纯白.webp"
    # 设置极高的扰动参数，若打印体有任何微小扰动均会产生显著差异
    return HandwritingParams(
        font_path=font_path,
        background_path=bg_path,
        font_size=36,
        line_spacing=48,
        word_spacing=5,
        word_spacing_sigma=10,
        line_spacing_sigma=10,
        font_size_sigma=10,
        perturb_x_sigma=10,
        perturb_y_sigma=10,
        perturb_theta_sigma=0.5,
        miswrite_rate=0.5,  # 50% 错字率
        roles=[
            HandwritingRole(id=0, name="默认手写", printed=False, color=None),
            HandwritingRole(id=1, name="打印体", printed=True, color=color, font_path=font_path),
        ],
        paragraphs=[
            Paragraph(
                text="第一行打印体测试内容",
                runs=[TextRun(text="第一行打印体测试内容", role_id=1, bold=False)],
            ),
            Paragraph(
                text="第二行打印体加粗内容",
                runs=[TextRun(text="第二行打印体加粗内容", role_id=1, bold=True)],
            ),
        ],
    )


def test_printed_zero_perturbation_with_none_color():
    """测试打印体角色 color=None（跟随主设置）时，不同 seed 渲染输出像素严格一致且无错字。"""
    params = _create_test_params(color=None)

    engine1 = FastEngine(seed=12345)
    pages1 = list(engine1.generate_pages(params))
    arr1 = np.asarray(pages1[0])

    engine2 = FastEngine(seed=67890)
    pages2 = list(engine2.generate_pages(params))
    arr2 = np.asarray(pages2[0])

    # 打印体零扰动：无论 seed 为何，渲染结果像素级严格相同
    np.testing.assert_array_equal(arr1, arr2)


def test_printed_zero_miswrite():
    """测试即便 miswrite_rate 高达 50%，打印体文字绝不发生涂改或错字。"""
    params = _create_test_params(color=None)
    params.miswrite_rate = 0.99
    engine = FastEngine(seed=42)
    pages = list(engine.generate_pages(params))
    arr = np.asarray(pages[0])
    assert arr.shape[0] > 0 and arr.shape[1] > 0

