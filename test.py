# 1. 显式因果链：raise B from A（推荐）
def divide_from_dict(data: dict):
    try:
        num = data["value"]  # 可能抛出 KeyError
        return 100 / num  # 可能抛出 ZeroDivisionError
    except KeyError as exc:
        # 明确告诉 Python：因为缺了 key，所以我抛出 ValueError
        raise ValueError("数据缺失，无法计算") from exc


# 2. 隐藏底层细节：raise B from None
def divide_clean(data: dict):
    try:
        num = data["value"]
        return 100 / num
    except KeyError:
        # 彻底抹去 KeyError，只保留干净的 ValueError
        raise ValueError("数据缺失，无法计算") from None


# 3. 隐式报错：raise B (不带 from)
def divide_implicit(data: dict):
    try:
        num = data["value"]
        return 100 / num
    except KeyError:
        # 语言表达模糊，让人分不清是故意捕获还是二次 Bug
        raise ValueError("数据缺失，无法计算")
    

try:
    divide_from_dict({})
except Exception as e:
    print("=============")
    print(str(e))