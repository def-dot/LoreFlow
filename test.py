import yaml

raw="""name: 文档入库
description: 实际示例：加载文档 → 切块 → 向量化 → 写入向量库。

nodes:
"""
config = yaml.safe_load(raw)
print(config)