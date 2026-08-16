"""
VLM Curiosity Validator — 低频高成本审核，判断"好奇"是否值得。

YOLO 说"这里有东西" → Interest 累积 → 达到阈值 →
VLM 审核："这是墙吗？值得关注吗？"

结果：
- TRIVIAL: 永久降权，不再回访
- INTERESTING: 确认价值，允许进入世界模型
- UNCERTAIN: 保持观察但加速衰减

Intentional design: VLM 不参与实时循环（保持高频感知/低频认知原则）。
只在 anchor novelty 高到触发阈值时才调用。
"""

import json
import logging
import time
from enum import Enum
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("Interest.Verifier")


class Verdict(Enum):
    TRIVIAL = "trivial"         # 静态环境，永久忽略
    INTERESTING = "interesting"  # 值得持续关注
    UNCERTAIN = "uncertain"      # 无法判断，继续观察


class VLMVerifier:
    """Calls VLM to judge whether a detected change is worth attention."""

    PROMPT = """你是一个监控机器人的视觉审核模块。

画面中有一个被物体检测模型标记为异常的物体/区域。

请判断：
1. 这是什么？
2. 这是否属于长期静态环境的一部分（比如：墙面、地板、窗帘、阴影、反光）？
3. 是否值得未来持续关注？

只回复 JSON，不要其他内容：
{
  "object": "简短描述",
  "static_environment": true/false,
  "worth_tracking": true/false
}"""

    def __init__(self, vision_api=None):
        self._api = vision_api  # VisionAPI instance (optional, lazy init)
        self._call_count = 0
        self._last_call = 0.0
        self._min_interval = 30.0  # minimum seconds between VLM calls

    @property
    def call_count(self) -> int:
        return self._call_count

    def verify(self, frame: np.ndarray, anchor_id: str,
               objects_found: list) -> Tuple[Verdict, str]:
        """Send detection crop to VLM, return verdict + object description.

        Crops the frame to the highest-confidence detection bbox so VLM
        focuses on the specific region, not the entire room.

        Rate-limited: won't call more than once per 30s.
        """
        now = time.time()
        if now - self._last_call < self._min_interval:
            logger.debug("VLM verify throttled (last call %.0fs ago)",
                         now - self._last_call)
            return Verdict.UNCERTAIN, ""

        # Lazy init vision API
        if self._api is None:
            try:
                from runtime.utils.vision_api import VisionAPI
                self._api = VisionAPI()
            except Exception as e:
                logger.warning("VLM verifier not available: %s", e)
                return Verdict.UNCERTAIN, ""

        self._last_call = now
        self._call_count += 1

        # Crop to highest-confidence detection (VLM focuses on the specific region)
        crop = _crop_highest_confidence(frame, objects_found)
        if crop is None:
            crop = frame  # fallback: full frame

        # Build context-rich prompt
        objects_str = ", ".join(
            f"{o.get('class_name', '?')}({o.get('confidence', 0):.2f})"
            for o in objects_found[:5]
        ) if objects_found else "无"
        context = f"[位置: {anchor_id}] [YOLO: {objects_str}]"
        prompt = self.PROMPT + f"\n\n{context}"

        try:
            result = self._api.analyze_frame(crop, prompt)
            if result is None:
                return Verdict.UNCERTAIN, ""
            verdict, description = self._parse(result)
            logger.info("VLM verify [%s]: %s → %s (%s)",
                        anchor_id, objects_str, verdict.value, description)
            return verdict, description
        except Exception as e:
            logger.warning("VLM verify failed: %s", e)
            return Verdict.UNCERTAIN, ""

    def _parse(self, text: str) -> Tuple[Verdict, str]:
        """Parse VLM JSON response."""
        try:
            text = text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
        except (json.JSONDecodeError, IndexError):
            return Verdict.UNCERTAIN, text[:100]

        obj = data.get("object", "")
        static = data.get("static_environment", False)
        worth = data.get("worth_tracking", False)

        if static and not worth:
            return Verdict.TRIVIAL, obj
        elif worth:
            return Verdict.INTERESTING, obj
        else:
            return Verdict.UNCERTAIN, obj


def _crop_highest_confidence(frame: np.ndarray, objects: list) -> Optional[np.ndarray]:
    """Crop frame to the highest-confidence detection's bounding box."""
    if not objects:
        return None
    # Find detection with highest confidence
    best = max(objects, key=lambda o: o.get("confidence", 0))
    bbox = best.get("bbox")
    if not bbox:
        return None
    h, w = frame.shape[:2]
    x = max(0, int(bbox.get("x", 0)))
    y = max(0, int(bbox.get("y", 0)))
    bw = min(int(bbox.get("width", w)), w - x)
    bh = min(int(bbox.get("height", h)), h - y)
    if bw <= 0 or bh <= 0:
        return None
    # Add 20% padding so VLM sees context around the object
    px = int(bw * 0.2)
    py = int(bh * 0.2)
    x1 = max(0, x - px)
    y1 = max(0, y - py)
    x2 = min(w, x + bw + px)
    y2 = min(h, y + bh + py)
    return frame[y1:y2, x1:x2]
