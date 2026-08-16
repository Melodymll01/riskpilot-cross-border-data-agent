"""生成 12 张无真实数据依赖的合成图片。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluations.visual_retrieval.evaluator import (  # noqa: E402
    VisualDataset,
    VisualQuery,
    write_dataset,
)

_SAMPLES = (
    ("red_server", (190, 20, 20), "服务器机柜", "红色告警灯"),
    ("green_server", (20, 150, 70), "服务器机柜", "绿色正常灯"),
    ("blue_cloud", (70, 120, 210), "云服务", "海外云平台"),
    ("orange_database", (220, 120, 20), "数据库", "客户数据库"),
    ("purple_mobile", (140, 70, 180), "移动应用", "手机应用"),
    ("gray_camera", (100, 100, 100), "摄像头", "监控摄像头"),
    ("yellow_warning", (235, 190, 20), "风险告警", "黄色警告标志"),
    ("cyan_network", (20, 180, 190), "网络拓扑", "跨境网络连接"),
    ("pink_profile", (220, 100, 150), "用户画像", "客户个人信息"),
    ("brown_paper", (145, 90, 45), "合同材料", "纸质标准合同"),
    ("navy_car", (20, 40, 100), "智能汽车", "车辆轨迹数据"),
    ("white_lab", (235, 235, 235), "临床试验", "医疗研究数据"),
)


def generate(root: Path) -> Path:
    images_dir = root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    queries: list[VisualQuery] = []
    for index, (asset_id, color, title, subtitle) in enumerate(_SAMPLES, start=1):
        image = Image.new("RGB", (384, 256), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((25, 25, 359, 231), outline=(20, 20, 20), width=5)
        draw.text((45, 80), title, fill=(0, 0, 0))
        draw.text((45, 145), subtitle, fill=(0, 0, 0))
        image.save(images_dir / f"{asset_id}.png")
        queries.append(
            VisualQuery(
                query_id=f"VR-{index:03d}",
                query=f"{title} {subtitle}",
                relevant_asset_ids=[asset_id],
            )
        )
    dataset = VisualDataset(
        name="RiskPilot Synthetic Visual Retrieval",
        version="1.0",
        images_dir="images",
        queries=queries,
    )
    dataset_path = root / "visual_retrieval_eval_v1.json"
    write_dataset(dataset_path, dataset)
    return dataset_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "datasets" / "synthetic_v1",
    )
    args = parser.parse_args(argv)
    print(generate(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
