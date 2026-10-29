"""Письма .eml: заголовки и текстовая часть. (.msg Outlook — в следующих версиях.)"""
from __future__ import annotations

import email
import re
from email import policy
from pathlib import Path

from .base import Block, ParsedDoc, clean


def parse_eml(path: Path, **_) -> ParsedDoc:
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    subject = str(msg.get("subject", "") or "").strip()
    header = clean(
        f"Тема: {subject}\nОт: {msg.get('from', '')}\nКому: {msg.get('to', '')}\nДата: {msg.get('date', '')}"
    )
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part is not None else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    blocks = [Block(text=header, kind="text", page=1, section="Письмо")]
    for para in body.split("\n\n"):
        t = clean(para)
        if t:
            blocks.append(Block(text=t, kind="text", page=1, section=subject or "Письмо"))
    return ParsedDoc(blocks=blocks, pages=1, title=subject or None)
