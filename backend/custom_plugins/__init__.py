"""示例插件包 — 演示 NODE_MODULES 插件机制。

配置 NODE_MODULES 声明子模块（见 app/core/config.py），启动时由
app.registry.plugins.load_plugins 导入，@node 装饰器自动注册进
REGISTRY。使用示例见 app/pipelines/07_plugin_demo.yaml。
"""
