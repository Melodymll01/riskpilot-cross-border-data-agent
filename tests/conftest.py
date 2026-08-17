"""conftest.py — pytest 全局测试配置 & 共享 fixtures。"""

import os
import sys
import tempfile

import pytest

# 确保项目根目录在 sys.path 上
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 离线测试默认使用本地 Provider 配置。这里只避免配置门禁和真实密钥依赖；
# 具体模型调用仍由各测试显式注入 Fake，普通 pytest 不访问本地 Ollama。
os.environ.setdefault("LLM_PROVIDER", "local")
os.environ.setdefault("EMBED_PROVIDER", "local")

# 测试时使用临时目录，不污染生产数据
os.environ.setdefault("CHROMA_PERSIST_DIR", tempfile.mkdtemp())
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp())


@pytest.fixture
def sample_texts():
    """提供一组用于测试的中文示例文本。"""
    return [
        "数据出境安全评估是指数据处理者向境外提供重要数据和个人信息时，"
        "由国家网信部门组织开展的安全评估活动。",
        "个人信息出境标准合同是个人信息处理者与境外接收方签订的合同，"
        "用于约定双方在个人信息出境活动中的权利和义务。",
        "根据《数据出境安全评估办法》第四条规定，数据处理者向境外提供数据，"
        "有下列情形之一的，应当通过所在地省级网信部门向国家网信部门申报数据出境安全评估：\n"
        "（一）数据处理者向境外提供重要数据；\n"
        "（二）关键信息基础设施运营者和处理100万人以上个人信息的数据处理者向境外提供个人信息；",
        "网络安全审查是指对关键信息基础设施运营者采购网络产品和服务，"
        "以及网络平台运营者开展数据处理活动影响或者可能影响国家安全的，"
        "进行的网络安全审查活动。",
        "《个人信息保护法》第三十八条规定，个人信息处理者因业务等需要，"
        "确需向中华人民共和国境外提供个人信息的，应当具备下列条件之一：\n"
        "（一）依照本法第四十条的规定通过国家网信部门组织的安全评估；\n"
        "（二）按照国家网信部门的规定经专业机构进行个人信息保护认证；",
    ]


@pytest.fixture
def raw_document():
    """提供一个 RawDocument 样例。"""
    from ingestion.unified_loader import RawDocument

    return RawDocument(
        content="这是一份测试文档的内容。\n\n第一章 总则\n\n第一条 为了规范数据出境活动。\n\n第二条 本办法适用于数据处理者。",
        source_type="file",
        source_name="test_doc.pdf",
        title="测试文档",
        source_url=None,
    )
