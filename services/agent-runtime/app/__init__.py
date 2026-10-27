"""App 层：use case 编排 + DI 容器 + 工厂。

本层只依赖 `domain.ports`（接口）与 `infra.*`（实现），不被 `infra` / `domain` 反向依赖。
公共出口：
- ``AppContainer``：DI 装配中心，构造一次全应用复用。
- ``build_*``：单独工厂函数，container 内部使用，也可独立装配（如评测脚本）。
"""

from app.container import AppContainer

__all__ = ["AppContainer"]
