"""一次性脚本：用 SQLite 实测 upstream_failed 状态迁移（升级改写 + 降级还原）。"""

import json
import os
import sqlite3

import app.core.config as cfg

# 把 DATABASE_URL 换成临时 SQLite（env.py 从 settings 读 URL）
cfg.Settings.DATABASE_URL = property(lambda self: "sqlite+aiosqlite:///./_mig_check2.db")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

if os.path.exists("_mig_check2.db"):
    os.remove("_mig_check2.db")

config = Config("alembic.ini")
config.set_main_option("script_location", "alembic")
command.upgrade(config, "c41f9a2d7e03")

# 旧版快照三形态：级联跳过 / 条件跳过 / 已完成
OLD_NODES = {
    "fetch": {"status": "completed", "output": 1, "attempts": 1, "duration_ms": 5, "skip_reason": None},
    "review": {"status": "skipped", "skip_reason": "upstream_failed"},
    "report": {"status": "skipped", "skip_reason": "condition_not_met"},
}

conn = sqlite3.connect("_mig_check2.db")
conn.execute(
    "insert into runs (name, config_file, mermaid, created_at, status, nodes) values ('t','t','','t','failed',?)",
    (json.dumps(OLD_NODES),),
)
conn.commit()
conn.close()

command.upgrade(config, "head")

conn = sqlite3.connect("_mig_check2.db")
nodes = json.loads(conn.execute("select nodes from runs").fetchone()[0])
print("after upgrade:", json.dumps(nodes, ensure_ascii=False))
assert nodes["fetch"]["status"] == "completed"
assert "skip_reason" not in nodes["fetch"]  # null 值的 skip_reason 一并清除
assert nodes["review"] == {"status": "upstream_failed"}  # 级联跳过 → 独立状态
assert nodes["report"] == {"status": "skipped"}  # 条件跳过仅去掉原因字段
conn.close()

# 降级还原：upstream_failed → skipped + skip_reason
command.downgrade(config, "c41f9a2d7e03")
conn = sqlite3.connect("_mig_check2.db")
nodes = json.loads(conn.execute("select nodes from runs").fetchone()[0])
print("after downgrade:", json.dumps(nodes, ensure_ascii=False))
assert nodes["review"] == {"status": "skipped", "skip_reason": "upstream_failed"}
assert nodes["report"] == {"status": "skipped"}
conn.close()

os.remove("_mig_check2.db")
print("MIGRATION OK")
