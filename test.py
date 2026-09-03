global_ns = {}
script = """
def process():
    return 123

result = process()
"""
exec(script)  # ✅ 运行成功！
# print(global_ns["result"])
print(result)