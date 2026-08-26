from PIL import Image, ImageDraw, ImageFont

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

c1 = (91, 91, 255)
c2 = (235, 69, 158)
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / S
    col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)) + (255,)
    gd.line([(0, y), (S, y)], fill=col)

mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
r = int(S * 0.22)
md.rounded_rectangle([8, 8, S - 8, S - 8], radius=r, fill=255)
img.paste(grad, (0, 0), mask)

font = None
for fp in [r"C:\Windows\Fonts\seguiblk.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
           r"C:\Windows\Fonts\arialbd.ttf"]:
    try:
        font = ImageFont.truetype(fp, int(S * 0.42))
        break
    except OSError:
        continue

txt = "NC"
bbox = d.textbbox((0, 0), txt, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
tx, ty = (S - tw) // 2 - bbox[0], (S - th) // 2 - bbox[1] - int(S * 0.01)

shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
sd.text((tx + int(S * 0.008), ty + int(S * 0.010)), txt, font=font, fill=(20, 10, 60, 110))
img = Image.alpha_composite(img, shadow)
d = ImageDraw.Draw(img)
d.text((tx, ty), txt, font=font, fill=(255, 255, 255, 255))

hs = int(S * 0.085)
hx, hy = S - int(S * 0.17), S - int(S * 0.17)
heart = Image.new("RGBA", (S, S), (0, 0, 0, 0))
hd = ImageDraw.Draw(heart)
hd.polygon([(hx, hy + hs // 3), (hx + hs // 2, hy + hs),
            (hx + hs, hy + hs // 3)], fill=(255, 92, 176, 255))
hd.ellipse([hx - hs // 14, hy - hs // 4, hx + hs * 0.46, hy + hs * 0.45], fill=(255, 92, 176, 255))
hd.ellipse([hx + hs * 0.54, hy - hs // 4, hx + hs + hs // 14, hy + hs * 0.45], fill=(255, 92, 176, 255))
img = Image.alpha_composite(img, heart)

final = img.resize((256, 256), Image.LANCZOS)
final.save("namachan.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
final.save("namachan_preview.png")
print("OK: namachan.ico genere")
