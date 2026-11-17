"""Generate a 2-bpp LVGL bitmap font from the official OFL Noto CJK source.

Build-only dependencies: Pillow and fontTools. No system font installation.
The generated font is named SmartRing CJK Bitmap, and retains the OFL notice.
"""
from pathlib import Path
import hashlib,json
from PIL import Image,ImageDraw,ImageFont
from fontTools.ttLib import TTFont

root=Path(__file__).resolve().parents[1]
source=root/'downloads/NotoCJK/NotoSansCJKsc-Regular.otf'
font=ImageFont.truetype(str(source),16)
available=TTFont(source).getBestCmap()
ranges=[(0x20,0xFF),(0x2000,0x206F),(0x2190,0x21FF),(0x3000,0x30FF),
        (0x3400,0x4DBF),(0x4E00,0x9FFF),(0xF900,0xFAFF),(0xFF00,0xFFEF)]
codes=sorted({code for start,end in ranges for code in range(start,end+1) if code in available}|{0x21B5,0xF130})
bitmap=bytearray();glyphs=['{0}'];metadata={}
for code in codes:
    if code==0x21B5:
        left,top,right,bottom=0,-14,16,2;advance=16
        image=Image.new('L',(16,16));draw=ImageDraw.Draw(image)
        draw.line([(12,2),(12,9),(2,9)],fill=255,width=2)
        draw.line([(5,6),(2,9),(5,12)],fill=255,width=2)
    elif code==0xF130:
        left,top,right,bottom=0,-14,16,2;advance=16
        image=Image.new('L',(16,16));draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((5,0,10,9),radius=2,fill=255)
        draw.arc((2,4,13,12),0,180,fill=255,width=2)
        draw.line([(8,12),(8,15),(4,15),(12,15)],fill=255,width=1)
    else:
        ch=chr(code);left,top,right,bottom=font.getbbox(ch,anchor='ls');advance=font.getlength(ch)
        image=Image.new('L',(max(0,right-left),max(0,bottom-top)))
        ImageDraw.Draw(image).text((-left,-top),ch,font=font,anchor='ls',fill=255)
    width,height=image.size;offset=len(bitmap)
    levels=[min(3,(value+42)//85) for value in image.tobytes()]
    for start in range(0,len(levels),4):
        chunk=levels[start:start+4]+[0]*3
        bitmap.append((chunk[0]<<6)|(chunk[1]<<4)|(chunk[2]<<2)|chunk[3])
    glyphs.append('{.bitmap_index=%d,.adv_w=%d,.box_w=%d,.box_h=%d,.ofs_x=%d,.ofs_y=%d}'%
                  (offset,round(advance*16),width,height,left,-bottom))
    metadata[str(code)]=dict(bitmap_index=offset,width=width,height=height,bytes=len(bitmap)-offset)
assert len(bitmap)<2**32 and len(codes)<65535
lines=['/* SmartRing CJK Bitmap: derived from Noto Sans CJK SC; see NotoCJK-LICENSE.txt. */',
       '#include "lvgl.h"','#if !LV_FONT_FMT_TXT_LARGE','#error Enable LV_FONT_FMT_TXT_LARGE','#endif',
       'static const uint8_t bitmap[] = {']
lines.extend(','.join(str(x) for x in bitmap[i:i+32])+',' for i in range(0,len(bitmap),32))
lines+=['};','static const lv_font_fmt_txt_glyph_dsc_t glyphs[] = {',',\n'.join(glyphs),'};',
        'static const uint16_t unicodes[] = {']
lines.extend(','.join(str(x-32) for x in codes[i:i+24])+',' for i in range(0,len(codes),24))
lines+=['};','static const lv_font_fmt_txt_cmap_t cmaps[] = {{',
        '.range_start=32,.range_length=65504,.glyph_id_start=1,.unicode_list=unicodes,',
        '.glyph_id_ofs_list=NULL,.list_length=%d,.type=LV_FONT_FMT_TXT_CMAP_SPARSE_TINY}};'%len(codes),
        'static const lv_font_fmt_txt_dsc_t description = {',
        '.glyph_bitmap=bitmap,.glyph_dsc=glyphs,.cmaps=cmaps,.cmap_num=1,.bpp=2,.bitmap_format=0,.stride=0};',
        'const lv_font_t smartring_cjk_16 = {',
        '.get_glyph_dsc=lv_font_get_glyph_dsc_fmt_txt,.get_glyph_bitmap=lv_font_get_bitmap_fmt_txt,',
        '.line_height=%d,.base_line=%d,.subpx=LV_FONT_SUBPX_NONE,.fallback=&lv_font_montserrat_16,.dsc=&description};'%
        (sum(font.getmetrics()),font.getmetrics()[1])]
output=root/'firmware/main/smartring_cjk_16.c'
output.write_text('\n'.join(lines)+'\n',encoding='utf8')
(root/'artifacts/cjk-font-coverage.json').write_text(json.dumps(dict(name='SmartRing CJK Bitmap',
    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),size_px=16,bpp=2,
    glyph_count=len(codes),bitmap_bytes=len(bitmap),codes=codes,glyphs=metadata)),encoding='utf8')
print(json.dumps(dict(glyph_count=len(codes),bitmap_bytes=len(bitmap),source_c_bytes=output.stat().st_size)))
