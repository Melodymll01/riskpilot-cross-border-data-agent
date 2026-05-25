"""test_chat_db.py — SQLite 对话持久化测试。"""

import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """每个测试使用独立的临时数据库。"""
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr("data.chat_db.DB_PATH", tmp)
    # 重新初始化
    import data.chat_db as db
    db.DB_PATH = tmp
    # 清除线程本地缓存的连接
    if hasattr(db._local, "conn"):
        try:
            db._local.conn.close()
        except Exception:
            pass
        del db._local.conn
    db.init_db()
    yield tmp
    # 关闭连接后再清理文件
    if hasattr(db._local, "conn"):
        try:
            db._local.conn.close()
        except Exception:
            pass
        del db._local.conn
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except PermissionError:
        pass  # Windows 文件锁，忽略


class TestConversations:
    """对话 CRUD 测试。"""

    def test_create_conversation(self):
        from data.chat_db import create_conversation
        conv = create_conversation("测试对话")
        assert conv.id
        assert conv.title == "测试对话"
        assert conv.created_at > 0

    def test_list_conversations(self):
        from data.chat_db import create_conversation, list_conversations
        create_conversation("对话1")
        create_conversation("对话2")
        convs = list_conversations()
        assert len(convs) == 2
        # 应按更新时间倒序
        assert convs[0].updated_at >= convs[1].updated_at

    def test_get_conversation(self):
        from data.chat_db import create_conversation, get_conversation
        conv = create_conversation("获取测试")
        result = get_conversation(conv.id)
        assert result is not None
        assert result.title == "获取测试"
        assert result.messages == []

    def test_get_nonexistent(self):
        from data.chat_db import get_conversation
        assert get_conversation("nonexistent-id") is None

    def test_delete_conversation(self):
        from data.chat_db import create_conversation, delete_conversation, get_conversation
        conv = create_conversation("删除测试")
        assert delete_conversation(conv.id) is True
        assert get_conversation(conv.id) is None

    def test_delete_nonexistent(self):
        from data.chat_db import delete_conversation
        assert delete_conversation("nonexistent") is False

    def test_update_title(self):
        from data.chat_db import create_conversation, update_conversation_title, get_conversation
        conv = create_conversation("旧标题")
        assert update_conversation_title(conv.id, "新标题") is True
        updated = get_conversation(conv.id)
        assert updated.title == "新标题"


class TestMessages:
    """消息 CRUD 测试。"""

    def test_add_message(self):
        from data.chat_db import create_conversation, add_message, get_conversation
        conv = create_conversation("消息测试")
        msg = add_message(conv.id, "user", "你好")
        assert msg.role == "user"
        assert msg.content == "你好"

        result = get_conversation(conv.id)
        assert len(result.messages) == 1
        assert result.messages[0].content == "你好"

    def test_message_order(self):
        from data.chat_db import create_conversation, add_message, get_conversation
        conv = create_conversation("顺序测试")
        add_message(conv.id, "user", "问题1")
        add_message(conv.id, "ai", "回答1")
        add_message(conv.id, "user", "问题2")

        result = get_conversation(conv.id)
        assert len(result.messages) == 3
        assert result.messages[0].role == "user"
        assert result.messages[1].role == "ai"

    def test_message_with_citations(self):
        from data.chat_db import create_conversation, add_message, get_conversation
        conv = create_conversation("引用测试")
        citations = [{"source": "test.pdf", "text": "片段"}]
        add_message(conv.id, "ai", "回答", citations)

        result = get_conversation(conv.id)
        assert result.messages[0].citations == citations

    def test_cascade_delete(self):
        """删除对话应级联删除所有消息。"""
        from data.chat_db import create_conversation, add_message, delete_conversation, get_conversation
        conv = create_conversation("级联测试")
        add_message(conv.id, "user", "消息1")
        add_message(conv.id, "ai", "消息2")

        delete_conversation(conv.id)
        assert get_conversation(conv.id) is None

    def test_updated_at_changes(self):
        """添加消息应更新对话的 updated_at。"""
        import time
        from data.chat_db import create_conversation, add_message, get_conversation
        conv = create_conversation("时间测试")
        original_time = conv.updated_at

        time.sleep(0.01)
        add_message(conv.id, "user", "新消息")

        updated = get_conversation(conv.id)
        assert updated.updated_at > original_time
