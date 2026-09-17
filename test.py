from pydantic import BaseModel, field_validator


class User(BaseModel):
  age: int

  # 1. before 模式：在转成 int 之前介入，可以处理带单位的字符串
  @field_validator("age", mode="before")
  def clean_age(cls, v):
    if isinstance(v, str) and v.endswith("岁"):
      return int(v.replace("岁", ""))
    return v

  # 2. after 模式：在转成 int 之后介入，用来做数值范围校验
  @field_validator("age", mode="after")
  def validate_age_range(cls, v):
    # 此时 v 保证已经是 int 类型了
    if v < 0 or v > 150:
      raise ValueError("年龄必须在 0 到 150 之间")
    return v


# 测试：
# 1. 传入 "18岁" -> before 把它变成 18 -> after 检查通过
# print(User(age="18岁"))  # 输出: age=18

# 2. 传入 -5 -> before 不动它 (-5) -> after 抛出 ValueError
User(age=-5)