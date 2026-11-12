"""Build the small Discord progress-strip assets (development-only Pillow).

Run on Windows with Segoe UI, or pass --font / --bold-font on other platforms.
The reporter ships the output files and does not need Pillow or system fonts.
"""
import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).with_name('discord_topbot_assets')
BG, TRACK = '#242429', '#414149'
GREEN, AMBER, WHITE, MUTED = '#45d6a0', '#f3bd57', '#ececf1', '#9898a5'
SCALE = 2


def render(state, frame, font_path, bold_path):
    image = Image.new('RGB', (840 * SCALE, 230 * SCALE), BG)
    draw = ImageDraw.Draw(image)

    def line(points, fill, width):
        draw.line([(int(x*SCALE), int(y*SCALE)) for x, y in points], fill=fill,
                  width=width*SCALE, joint='curve')

    def circle(x, y, r, fill=None, outline=None, width=1):
        draw.ellipse(tuple(int(v*SCALE) for v in (x-r, y-r, x+r, y+r)),
                     fill=fill, outline=outline, width=width*SCALE)

    def label(x, y, text, color, size, bold=False):
        font = ImageFont.truetype(str(bold_path if bold else font_path), size*SCALE)
        draw.text((x*SCALE, y*SCALE), text, font=font, fill=color, anchor='mt')

    xs, cy = (140, 420, 700), 77
    line([(xs[0]+40, cy), (xs[1]-40, cy)], GREEN, 5)
    line([(xs[1]+40, cy), (xs[2]-40, cy)], GREEN if state == 'complete' else TRACK, 5)
    for index, x in enumerate(xs):
        done = state == 'complete' or index == 0
        if done:
            circle(x, cy, 38, fill=GREEN)
            line([(x-15, cy), (x-4, cy+11), (x+17, cy-12)], '#16372d', 6)
        elif index == 1:
            circle(x, cy, 47, fill='#3b3224')
            circle(x, cy, 36, outline='#715c35', width=4)
            # A rotating ring indicates waiting, never percent completion.
            start = frame * 360 / 32 - 90
            bounds = tuple(int(v*SCALE) for v in (x-36, cy-36, x+36, cy+36))
            draw.arc(bounds, start, start+105, fill=AMBER, width=6*SCALE)
            for angle in (start, start+105):
                radians = math.radians(angle)
                circle(x+36*math.cos(radians), cy+36*math.sin(radians), 3, fill=AMBER)
            circle(x, cy, 7, fill=AMBER)
        else:
            circle(x, cy, 38, fill=BG, outline=TRACK, width=4)
            circle(x, cy, 6, fill=MUTED)
    middle = 'Trade closed' if state == 'complete' else 'Reconciling' if state == 'reconciling' else 'Trade open'
    labels = ('Entry filled', middle, 'Result verified')
    for i, x in enumerate(xs):
        color = WHITE if state == 'complete' or i == 0 else AMBER if i == 1 else MUTED
        label(x, 145, labels[i], color, 36, True)
    return image.resize((840, 230), Image.Resampling.LANCZOS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', default='C:/Windows/Fonts/segoeui.ttf')
    parser.add_argument('--bold-font', default='C:/Windows/Fonts/seguisb.ttf')
    args = parser.parse_args()
    ROOT.mkdir(exist_ok=True)
    for state in ('open', 'reconciling', 'complete'):
        first = render(state, 0, args.font, args.bold_font)
        first.save(ROOT / f'topbot-{state}-v2.png', optimize=True)
        if state != 'complete':
            frames = [render(state, n, args.font, args.bold_font) for n in range(32)]
            frames[0].save(ROOT / f'topbot-{state}-v2.gif', save_all=True,
                           append_images=frames[1:], duration=60, loop=0, optimize=True,
                           disposal=1)
    for path in sorted(ROOT.iterdir()):
        print(f'{path.name}: {path.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
