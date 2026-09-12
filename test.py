def create_functions():
    funcs = []
    for i in range(3):
        # 定义内部函数并放入列表
        def func():
            return i  # 闭包引用了外部循环变量 i
        funcs.append(func)
    return funcs

# 获取生成的 3 个函数
f0, f1, f2 = create_functions()

# 预期输出: 0, 1, 2
# 实际输出: 2, 2, 2
print(f0())  # 2
print(f1())  # 2
print(f2())  # 2