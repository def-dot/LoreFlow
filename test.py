
import importlib.util
import sys
from pathlib import Path

# 1. 准备环境：创建 plugins 目录并写入一个简易插件文件
plugins_dir = Path("plugins")
plugins_dir.mkdir(exist_ok=True)

plugin_path = plugins_dir / "hello_plugin.py"
# plugin_path.write_text(
#     'def run(name):\n    return f"Hello, {name}! 插件已成功调用。"\n',
#     encoding="utf-8",
# )

# 2. 动态加载逻辑
module_name = f"{plugins_dir.name}.{plugin_path.stem}"
print(module_name)  

# plugins.hello_plugin plugins/hello_plugin.py
spec = importlib.util.spec_from_file_location(module_name, plugin_path)
module = importlib.util.module_from_spec(spec)
sys.modules[module_name] = module
spec.loader.exec_module(module)

# 3. 使用加载后的模块
result = module.run("开发者")
print(result)  # 控制台输出: Hello, 开发者! 插件已成功调用。
