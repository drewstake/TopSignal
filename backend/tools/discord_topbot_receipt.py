"""Deterministic receipt cards from the reporter's validated public data only."""
from datetime import datetime
from functools import lru_cache
import hashlib
import io
import json
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont


REVISION = 'receipt-v3'
BG, TEXT, MUTED, LINE = '#242429', '#f1f1f5', '#b7b7c2', '#464650'


def canonical(model):
    return json.dumps(model, sort_keys=True, separators=(',', ':'))


def receipt_attachment(model):
    extension = 'gif' if model['state'] != 'complete' and not model['snapshot'] else 'png'
    digest = hashlib.sha256((REVISION + canonical(model)).encode()).hexdigest()[:16]
    details = [f"TopBot. {model['mode']}. {model['symbol']} {model['side']}. {model['status']}.",
               f"{model['quantity']} contracts. Trade #{model['id']}. Times Eastern.",
               f"Entry {model['entry']} at {local_time(model['entry_at'])}."]
    if model.get('exit'):
        details.append(f"Exit {model['exit']} at {local_time(model['exit_at'])}.")
    for key in ('stop', 'target', 'gross', 'fees', 'net'):
        if model.get(key) is not None:
            details.append(f'{key.capitalize()} {model[key]}.')
    if model.get('estimated_fees'):
        details.append('Fees include estimated commission.')
    if model['snapshot']:
        details.append('Snapshot at time of check.')
    return {'id': 0, 'filename': f'{REVISION}-{digest}.{extension}',
            'description': ' '.join(details)[:1024]}


def local_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York'))


@lru_cache(maxsize=32)
def font(size, bold=False):
    for path in (f"C:/Windows/Fonts/{'seguisb' if bold else 'segoeui'}.ttf",
                 f"DejaVuSans{'-Bold' if bold else ''}.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def clock(value, entry_at):
    date = local_time(value)
    prefix = date.strftime('%m/%d ') if date.date() != local_time(entry_at).date() else ''
    return prefix + date.strftime('%I:%M %p').lstrip('0')


@lru_cache(maxsize=64)
def render_receipt(encoded):
    model = json.loads(encoded)
    color = '#45d6a0' if model['color'] == 0x2ECC71 else '#f07777' if model['color'] == 0xE74C3C else '#f3bd57'
    # A landscape attachment avoids the embed's smaller, padded image box.
    image = Image.new('RGB', (1000, 720), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 7, 719), fill=color)

    def text(x, y, value, size=44, fill=TEXT, bold=False, right=False, max_width=928):
        selected = font(size, bold)
        while draw.textlength(value, font=selected) > max_width and size > 16:
            size -= 1
            selected = font(size, bold)
        draw.text((x, y), value, font=selected, fill=fill, anchor='rt' if right else 'lt')

    def rule(y):
        draw.line((36, y, 964, y), fill=LINE, width=2)

    text(36, 26, model['symbol'] + ' ' + model['side'], 54, bold=True, max_width=535)
    text(964, 32, model['mode'], 34, MUTED, right=True, max_width=365)
    text(964, 83, model['status'], 32, color, True, True, max_width=290)
    date = local_time(model['entry_at'])
    contract = 'contract' if model['quantity'] == '1' else 'contracts'
    text(36, 98, f"{model['quantity']} {contract} · {date.strftime('%b')} {date.day}, {date.year} · #{model['id']}", 30, MUTED, max_width=640)
    text(36, 175, 'Net P&L', 44, MUTED)
    text(964, 151, model.get('net') or '—', 78, color, True, True, max_width=660)
    rule(240)
    rows = [('Entry', model['entry'] + ' · ' + clock(model['entry_at'], model['entry_at'])),
            ('Exit', model['exit'] + ' · ' + clock(model['exit_at'], model['entry_at']) if model.get('exit') else '—'),
            ('Stop', model.get('stop') or '—'), ('Target', model.get('target') or '—')]
    for y, (label, value) in zip((260, 314, 368, 422), rows):
        text(36, y, label, 44, MUTED)
        text(964, y, value, 44, right=True, max_width=710)
    rule(478)
    for y, label, value in ((492, 'Gross', model.get('gross') or '—'),
                             (546, 'Fees', (model.get('fees') or '—') + (' (est.)' if model.get('estimated_fees') else ''))):
        text(36, y, label, 44, MUTED)
        text(964, y, value, 46, color if label == 'Gross' and model.get('gross') else TEXT,
             label == 'Gross', True, max_width=710)
    state = model['state']
    animated = state != 'complete' and not model['snapshot']
    frames = []
    for frame in range(32 if animated else 1):
        canvas = image.copy()
        progress = ImageDraw.Draw(canvas)
        xs, cy = (180, 500, 820), 628
        green, amber = '#45d6a0', '#f3bd57'
        progress.line((204, cy, 476, cy), fill=green, width=4)
        progress.line((524, cy, 796, cy), fill=green if state == 'complete' else LINE, width=4)
        labels = ('Entry filled', 'Trade closed' if state == 'complete' else
                  'Reconciling' if state == 'reconciling' else 'Trade open', 'Result verified')
        for index, x in enumerate(xs):
            bounds = (x-24, cy-24, x+24, cy+24)
            done = state == 'complete' or index == 0
            if done:
                progress.ellipse(bounds, fill=green)
                progress.line((x-10, cy, x-3, cy+7, x+11, cy-8), fill=BG, width=4)
            else:
                progress.ellipse(bounds, outline=LINE, width=4)
                if index == 1:
                    start = frame * 360 / 32 - 90
                    progress.arc(bounds, start, start+110, fill=amber, width=5)
            progress.text((x, 662), labels[index], font=font(34, True),
                          fill=TEXT if done else amber if index == 1 else MUTED, anchor='mt')
        note = 'USD · Times ET' + (' · Snapshot' if model['snapshot'] else '')
        progress.text((500, 703), note, font=font(24), fill=MUTED, anchor='mm')
        frames.append(canvas)
    output = io.BytesIO()
    if animated:
        frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:], duration=60,
                       loop=0, optimize=True, disposal=1)
    else:
        frames[0].save(output, format='PNG', optimize=True)
    return output.getvalue()
