from pypdf import PdfReader, PdfWriter from reportlab.pdfgen import canvas from reportlab.lib.pagesizes import A4 from reportlab.lib.colors import Color import io, uuid from pathlib import Path

input_path = Path(r'/uploads/c0add430abf54c32b9112f8961dcb191.pdf') watermark_text = '机密文件'

reader = PdfReader(str(input_path))
packet = io.BytesIO()
c = canvas.Canvas(packet, pagesize=A4)
c.setFont("Helvetica", 40)
c.setFillColor(Color(0.5, 0.5, 0.5, alpha=0.3))
c.saveState()
c.translate(A4[0] / 2, A4[1] / 2)
c.rotate(45)
c.drawCentredString(0, 0, watermark_text)
c.restoreState()
c.save()
packet.seek(0)
watermark_page = PdfReader(packet).pages[0]

writer = PdfWriter()
for page in reader.pages:
    page.merge_page(watermark_page)
    writer.add_page(page)

out_name = input_path.parent / f'{uuid.uuid4().hex}_watermarked.pdf'
with open(out_name, "wb") as f:
    writer.write(f)
print(f'输出文件: {out_name}')