"""api 包：v1 路由（routes/schemas）已于 Step 029 退役删除。

现行 HTTP 接口全部在 ``api/v2/`` 下，通过 ``api.v2.build_v2_router(container)``
装配并由 ``main.py`` 挂到 ``/api/v2`` 前缀。
"""
