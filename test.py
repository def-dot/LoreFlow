from pydantic import BaseModel, Field
import json

class PayloadItem(BaseModel):
    key: str = Field(description="字段标识（英文）")
    label: str = Field(description="显示名称（中文）")
    value: str = Field(description="字段值")


class HumanParams(BaseModel):
    payload: list[PayloadItem] = Field(default_factory=dict, description="审核参数")



r = HumanParams.model_json_schema() 
print(json.dumps(r))