"""Markdown digest -> a self-contained HTML email body."""
from __future__ import annotations

import html

import markdown

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;">
<div style="max-width:680px;margin:0 auto;padding:24px 20px;background:#ffffff;
font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;
line-height:1.55;color:#1f2328;">
{body}
<hr style="border:none;border-top:1px solid #d0d7de;margin:24px 0 12px;">
<p style="font-size:12px;color:#656d76;margin:0;">gta6-emulation-watcher daily digest</p>
</div></body></html>
"""

# Email clients ignore most <style> blocks, so style the common tags inline.
INLINE = {
    "<h1>": '<h1 style="font-size:22px;margin:0 0 12px;">',
    "<h2>": '<h2 style="font-size:18px;margin:20px 0 8px;border-bottom:1px solid #d0d7de;padding-bottom:4px;">',
    "<h3>": '<h3 style="font-size:16px;margin:16px 0 6px;">',
    "<ul>": '<ul style="padding-left:20px;margin:6px 0;">',
    "<li>": '<li style="margin:4px 0;">',
    "<a ": '<a style="color:#0969da;" ',
    "<code>": '<code style="background:#f6f8fa;padding:1px 4px;border-radius:4px;">',
    "<blockquote>": '<blockquote style="margin:8px 0;padding-left:12px;border-left:3px solid #d0d7de;color:#57606a;">',
}


def to_html(md: str, title: str) -> str:
    body = markdown.markdown(md, extensions=["extra", "sane_lists"])
    for tag, styled in INLINE.items():
        body = body.replace(tag, styled)
    return TEMPLATE.format(title=html.escape(title), body=body)
