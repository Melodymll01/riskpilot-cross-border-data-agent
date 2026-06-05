"""``RiskProfilePort`` 适配器集合（schema-evidence-risk-profiling 接入位）。

当前阶段仅提供 ``StubRiskProfileService`` —— evidence-state 模型仍在
``schema-evidence-risk-profiling`` 仓库训练（evidence_v1），尚未部署。本占位
实现满足 ``RiskProfilePort`` 契约，让上层路由可以提前装配；真实模型上线后，
新增 ``HttpRiskProfileClient`` 替换工厂里的默认绑定即可。
"""

from infra.risk_profile.stub_risk_profile import StubRiskProfileService

__all__ = ["StubRiskProfileService"]
