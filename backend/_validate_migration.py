"""一次性脚本：用 SQLite 实测 0002 迁移（去重、部分唯一索引、消费后新行）。"""

import os
import sqlite3

import app.core.config as cfg

# 把 DATABASE_URL 换成临时 SQLite（env.py 从 settings 读 URL）
cfg.Settings.DATABASE_URL = property(lambda self: "sqlite+aiosqlite:///./_mig_check.db")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

if os.path.exists("_mig_check.db"):
    os.remove("_mig_check.db")

config = Config("alembic.ini")
config.set_main_option("script_location", "alembic")
command.upgrade(config, "0001")

# 模拟旧版双批残留：同一节点两条未消费决策
conn = sqlite3.connect("_mig_check.db")
conn.execute(
    "insert into review_decisions (run_id, node_name, approve, reason, created_at)"
    " values (1,'n',1,'old','t1')"
)
conn.execute(
    "insert into review_decisions (run_id, node_name, approve, reason, created_at)"
    " values (1,'n',0,'newer','t2')"
)
conn.commit()
conn.close()

command.upgrade(config, "head")

conn = sqlite3.connect("_mig_check.db")
rows = conn.execute("select id, approve, reason, consumed_at from review_decisions").fetchall()
print("rows after upgrade:", rows)
assert len(rows) == 1 and rows[0][1] == 0, rows  # 去重只留最新（approve=0）
idx = conn.execute(
    "select sql from sqlite_master where type='index' and name='uq_review_decisions_pending'"
).fetchone()
print("index:", idx)
assert idx is not None

# 部分唯一索引生效：pending 重复插入被拒
try:
    conn.execute("insert into review_decisions (run_id, node_name, approve) values (1,'n',1)")
    conn.commit()
    raise SystemExit("FAIL: duplicate pending row allowed")
except sqlite3.IntegrityError:
    print("partial unique index enforced OK")

# 消费后允许新行（审计历史保留）
conn.execute("update review_decisions set consumed_at='t3'")
conn.execute("insert into review_decisions (run_id, node_name, approve) values (1,'n',1)")
conn.commit()
print("rows after consumed + new:", conn.execute("select count(*) from review_decisions").fetchone())
conn.close()

os.remove("_mig_check.db")
print("MIGRATION OK")
