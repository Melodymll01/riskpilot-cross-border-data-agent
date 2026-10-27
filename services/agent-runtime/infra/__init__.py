"""Infra 层：domain.ports 的具体适配器实现。

每个子模块封装一个或多个外部依赖（数据库 / LLM / 向量库 / 网络搜索 / Evidence 服务），
对外只暴露实现 `domain.ports` 中 Protocol 的类。
"""
