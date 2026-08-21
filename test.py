import types

# 1. 模拟全局注册表
REGISTRY = {}

# --------------------------------------------------
# 模拟第一次加载插件文件
# --------------------------------------------------
def load_plugin_v1():
    # 模拟 exec 执行插件源码
    code = """
def notify():
    return "v1: 旧代码"
"""
    scope = {}
    exec(code, scope)
    REGISTRY["notify"] = scope["notify"]

load_plugin_v1()

# 2. 模拟构建并启动一个 Run (DAG 构建阶段)
class Node:
    def __init__(self, name, func):
        self.name = name
        self.func = func # 抄走函数引用

running_run_node = Node("notification_step", REGISTRY["notify"])

print("=== 重载前 ===")
print(f"注册表中的函数地址 : {hex(id(REGISTRY['notify']))}")
print(f"Node节点持有的地址 : {hex(id(running_run_node.func))}")
print(f"节点执行结果       : {running_run_node.func()}\n")


# --------------------------------------------------
# 3. 模拟热重载插件 (重新 exec 文件)
# --------------------------------------------------
def reload_plugin_v2():
    code = """
def notify():
    return "v2: 新代码"
"""
    scope = {}
    exec(code, scope) # 再次执行 def，在内存创建全新函数对象
    REGISTRY["notify"] = scope["notify"] # 仅覆盖注册表里的 key

reload_plugin_v2()

print("=== 重载后 ===")
print(f"注册表中的函数地址 : {hex(id(REGISTRY['notify']))} (已被替换为新对象)")
print(f"旧 Node 持有的地址  : {hex(id(running_run_node.func))} (依然指向旧对象)")
print(f"旧 Node 执行结果    : {running_run_node.func()} (依然运行旧逻辑)")

# 4. 新起的 Run 才会拿到新代码
new_run_node = Node("notification_step", REGISTRY["notify"])
print(f"新 Node 执行结果    : {new_run_node.func()} (拿到新逻辑)")