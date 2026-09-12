import os 
pdf_path = "./backend/uploads/4cda2831c945403f8b8cf77ad64d881e.pdf" 
print(f"文件存在: {os.path.exists(pdf_path)}") 
print(f"文件大小: {os.path.getsize(pdf_path)} bytes") # 读取PDF内容 

from pypdf import PdfReader 
reader = PdfReader(pdf_path) 
print(f"页数: {len(reader.pages)}") # 提取第一页文本 
page = reader.pages[0] 
text = page.extract_text() 
print(f"第一页文本预览:\n{text[:500]}...")