"""Bundled, credential-free visual assets for Discord trade reports."""
from pathlib import Path


ASSET_ROOT = Path(__file__).with_name('discord_topbot_assets')
ASSETS = frozenset(f'topbot-{state}-v2.{suffix}'
                   for state in ('open', 'reconciling', 'complete')
                   for suffix in (('png',) if state == 'complete' else ('png', 'gif')))


def timeline_attachment(state, *, snapshot=False):
    extension = 'png' if snapshot or state == 'complete' else 'gif'
    filename = f'topbot-{state}-v2.{extension}'
    if filename not in ASSETS:
        raise ValueError('Invalid timeline state')
    descriptions = {
        'open': 'Entry confirmed. Trade open and monitoring. Result pending.',
        'reconciling': 'Entry confirmed. Reconciling exit fills. Result pending.',
        'complete': 'Entry confirmed. Trade closed. Result verified.',
    }
    return {'id': 0, 'filename': filename, 'description': descriptions[state]}


def attachment_files(data):
    """Resolve only bundled assets, never caller-provided filesystem paths."""
    if (data or {}).get('_receipt') is not None:
        if __package__:
            from .discord_topbot_receipt import canonical, receipt_attachment, render_receipt
        else:
            from discord_topbot_receipt import canonical, receipt_attachment, render_receipt
        model = data['_receipt']
        attachment = receipt_attachment(model)
        if data.get('attachments') != [attachment]:
            raise ValueError('Invalid receipt attachment')
        return [(attachment, render_receipt(canonical(model)))]
    result = []
    for item in (data or {}).get('attachments', []):
        filename = item['filename']
        if filename not in ASSETS or item['id'] != len(result):
            raise ValueError('Invalid report attachment')
        result.append((item, (ASSET_ROOT / filename).read_bytes()))
    return result
