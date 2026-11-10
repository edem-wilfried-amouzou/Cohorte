import io

import qrcode
import qrcode.image.svg


def qr_svg(url: str) -> bytes:
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue()
