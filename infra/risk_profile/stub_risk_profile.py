"""``RiskProfilePort`` 占位实现：模型未就绪时的明确兜底。

设计原则：
- **不静默返回 not_disclosed** —— 那会被上层误当作真实模型输出。
- 提供两种行为模式（``mode``）：
    * ``"raise"``（默认）：抛 ``RiskProfileNotReady``，由上层路由翻译成"敬请期待"。
    * ``"placeholder"``：返回 ``RiskProfile`` 但 ``metadata['stub'] = True``，便于
      前端在等待真实模型上线期间做端到端联调。

替换方法：在 ``app/factories.py`` 的 ``build_risk_profile()`` 里把默认实现切到
``HttpRiskProfileClient``（待 schema-evidence v1 模型部署后实现）。
"""

from __future__ import annotations

from typing import Literal

from domain.errors import RiskProfileNotReady
from domain.models import RiskProfile

StubMode = Literal["raise", "placeholder"]


class StubRiskProfileService:
    """占位 ``RiskProfilePort`` 实现。"""

    def __init__(self, mode: StubMode = "raise") -> None:
        if mode not in ("raise", "placeholder"):
            msg = f"mode 必须是 'raise' 或 'placeholder'，得到 {mode!r}"
            raise ValueError(msg)
        self._mode: StubMode = mode

    def assess(
        self,
        target: str,
        document: str | None = None,
        *,
        language: str = "zh",
    ) -> RiskProfile:
        if not target or not target.strip():
            msg = "target 不能为空"
            raise ValueError(msg)

        if self._mode == "raise":
            raise RiskProfileNotReady(
                "风险画像模型尚未部署（schema-evidence v1 训练中），"
                "目前以对话形式回答。模型上线后此接口会切换到真实评估服务。"
            )

        # placeholder 模式：返回带 stub 标记的占位结果
        return RiskProfile(
            target=target,
            evidence_state="not_disclosed",
            evidence_spans=[],
            explanation=(
                "[stub] 风险画像模型尚未部署，当前为占位输出。"
                "该接口将在 schema-evidence v1 训练完成后切换到真实模型。"
            ),
            metadata={
                "stub": True,
                "language": language,
                "has_document": document is not None,
            },
        )
