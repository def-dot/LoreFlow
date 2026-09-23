import os
import smtplib
from email.mime.text import MIMEText

from pydantic import BaseModel, Field

from app.registry import func


class SendEmailInput(BaseModel):
    to: str = Field(description="收件人邮箱")
    subject: str = Field(default="(无主题)", description="邮件主题")
    body: str = Field(default="", description="邮件正文")


class SendEmailOutput(BaseModel):
    result: str = Field(description="发送结果")


@func(
    label="发送邮件",
    description="通过 SMTP 发送邮件",
    tool=False,
)
async def send_email(params: SendEmailInput) -> SendEmailOutput:
    """动作节点：发送邮件。

    YAML 接线示例：
        inputs:
          to: "user@example.com"
          subject: "审核结果通知"
          body: $llm_chat
    """

    host = os.getenv("SMTP_HOST", "smtp.example.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("SMTP_SENDER", user)

    if not user:
        raise RuntimeError("未配置 SMTP_USER 环境变量，无法发送邮件")

    msg = MIMEText(params.body, "plain", "utf-8")
    msg["Subject"] = params.subject
    msg["From"] = sender
    msg["To"] = params.to

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.sendmail(sender, [params.to], msg.as_string())

    return SendEmailOutput(result=f"邮件已发送至 {params.to}")


class SendMessageInput(BaseModel):
    phone: str = Field(description="手机号")
    content: str = Field(default="", description="短信内容")


class SendMessageOutput(BaseModel):
    result: str = Field(description="发送结果")


@func(
    label="发送短信",
    description="通过 HTTP API 发送短信",
    tool=False,
)
async def send_message(params: SendMessageInput) -> SendMessageOutput:
    """动作节点：调用短信 API。

    YAML 接线示例：
        inputs:
          phone: "13800138000"
          content: $llm_chat
    """
    import httpx

    api_url = os.getenv("SMS_API_URL", "")
    api_key = os.getenv("SMS_API_KEY", "")

    if not api_url:
        raise RuntimeError("未配置 SMS_API_URL 环境变量，无法发送短信")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            api_url,
            json={"phone": params.phone, "content": params.content, "api_key": api_key},
            timeout=10,
        )
        resp.raise_for_status()

    return SendMessageOutput(result=f"短信已发送至 {params.phone}")