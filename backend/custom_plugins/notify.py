import os
import smtplib
from email.mime.text import MIMEText

from app.registry import node_type


@node_type(
    label="发送邮件",
    description="通过 SMTP 发送邮件",
    input_schema={
        "to": {"type": "string", "required": True, "description": "收件人邮箱"},
        "subject": {"type": "string", "required": False, "description": "邮件主题"},
        "body": {"type": "string", "required": False, "description": "邮件正文"},
    },
    output_schema={"type": "string", "description": "发送结果"},
)
async def send_email(ctx: dict) -> str:
    """动作节点：从 ctx 读取收件人、主题、正文，发送邮件。

    YAML 接线示例：
        inputs:
          to: "user@example.com"
          subject: "审核结果通知"
          body: $llm_chat
    """
    to = str(ctx.get("to", ""))
    subject = str(ctx.get("subject", "(无主题)"))
    body = str(ctx.get("body", ""))

    host = os.getenv("SMTP_HOST", "smtp.example.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("SMTP_SENDER", user)

    if not user:
        raise RuntimeError("未配置 SMTP_USER 环境变量，无法发送邮件")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.sendmail(sender, [to], msg.as_string())

    return f"邮件已发送至 {to}"


@node_type(
    label="发送短信",
    description="通过 HTTP API 发送短信",
    input_schema={
        "phone": {"type": "string", "required": True, "description": "手机号"},
        "content": {"type": "string", "required": False, "description": "短信内容"},
    },
    output_schema={"type": "string", "description": "发送结果"},
)
async def send_message(ctx: dict) -> str:
    """动作节点：从 ctx 读取手机号和内容，调用短信 API。

    YAML 接线示例：
        inputs:
          phone: "13800138000"
          content: $llm_chat
    """
    import httpx

    phone = str(ctx.get("phone", ""))
    content = str(ctx.get("content", ""))

    api_url = os.getenv("SMS_API_URL", "")
    api_key = os.getenv("SMS_API_KEY", "")

    if not api_url:
        raise RuntimeError("未配置 SMS_API_URL 环境变量，无法发送短信")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            api_url,
            json={"phone": phone, "content": content, "api_key": api_key},
            timeout=10,
        )
        resp.raise_for_status()

    return f"短信已发送至 {phone}"
