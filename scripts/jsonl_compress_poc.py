#!/usr/bin/env python3
"""
Compresh JSONL POC — hash chain'siz basit transparent middleware testi.

Amaç: Cowork JSONL'ı modifiye edilince Cowork'ün
modifiye versiyonu Anthropic'e gönderip göndermediğini test etmek.

Kullanım:
    1. Yeni test Cowork session aç, 3-5 turn konuş, sonra session açıkken:
    2. python3 jsonl_compress_poc.py <jsonl_path>
       # veya path vermezsen otomatik bul:
    3. python3 jsonl_compress_poc.py --auto

Hipotez testi: Script tool_result block'larını [elided: ...] marker ile
değiştirir, JSONL'ı atomic rename ile yerine yazar. Sonra Cowork'te
"önceki bash çıktısında ne vardı?" sor:
  - Doğru hatırlıyor → memory'den okuyor (write-only log)
  - Yanlış / 'elided' diyor → JSONL'dan okuyor (authoritative source) ✓
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def find_active_jsonl():
    """En son modifiye olan Cowork session JSONL'ını bul."""
    home = Path.home()
    candidates = [
        home / ".claude" / "projects",
        home / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions",
    ]
    newest = None
    newest_mtime = 0
    for root in candidates:
        if not root.exists():
            continue
        for p in root.rglob("*.jsonl"):
            # subagent jsonl'ları atla
            if "subagents" in p.parts:
                continue
            mtime = p.stat().st_mtime
            if mtime > newest_mtime:
                newest_mtime = mtime
                newest = p
    return newest


def compute_hash(path):
    """SHA-256 — sadece referans için, chain yok."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def elide_tool_result(block):
    """tool_result block'unu kısa elision marker'ına çevir."""
    content = block.get("content", "")
    if isinstance(content, list):
        # tool_result.content da bir block listesi olabilir
        text_parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                text_parts.append(c.get("text", ""))
        full = " ".join(text_parts)
    else:
        full = str(content)

    preview = full[:60].replace("\n", " ")
    elided = {
        "type": "tool_result",
        "tool_use_id": block.get("tool_use_id"),
        "content": f"[ELIDED by Compresh POC — original ~{len(full)} chars, preview: '{preview}...']",
    }
    if "is_error" in block:
        elided["is_error"] = block["is_error"]
    return elided


def compress_messages(lines, protection_zone_n=4):
    """
    Basit Compresh simülasyonu:
    - Son N turn raw (Protection Zone)
    - Önceki turn'lerin tool_result block'ları elide
    - text, tool_use, thinking blokları olduğu gibi
    """
    # parse all
    parsed = []
    for line in lines:
        try:
            parsed.append((line, json.loads(line)))
        except json.JSONDecodeError:
            parsed.append((line, None))

    total = len(parsed)
    protection_start = max(0, total - protection_zone_n)

    out_lines = []
    elided_count = 0
    bytes_saved = 0

    for i, (orig_line, msg) in enumerate(parsed):
        if msg is None:
            out_lines.append(orig_line)
            continue

        # Protection Zone: raw bırak
        if i >= protection_start:
            out_lines.append(orig_line)
            continue

        # 'message' field yoksa (summary/control satırları) olduğu gibi bırak
        if "message" not in msg or not isinstance(msg.get("message"), dict):
            out_lines.append(orig_line)
            continue

        # Compress: tool_result block'larını elide
        content = msg["message"].get("content", [])
        if not isinstance(content, list):
            out_lines.append(orig_line)
            continue

        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                # Sadece büyük olanları elide (>200 char)
                orig_str = json.dumps(block)
                if len(orig_str) > 200:
                    new_content.append(elide_tool_result(block))
                    elided_count += 1
                    bytes_saved += len(orig_str) - len(json.dumps(new_content[-1]))
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        msg["message"]["content"] = new_content
        out_lines.append(json.dumps(msg) + "\n")

    return out_lines, elided_count, bytes_saved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="JSONL path (otomatik bulmak için --auto)")
    parser.add_argument("--auto", action="store_true", help="En son modifiye JSONL'ı otomatik bul")
    parser.add_argument("--protection-zone", type=int, default=4, help="Son N turn raw kalsın")
    parser.add_argument("--dry-run", action="store_true", help="Yedek al, modifiye et ama yerine yazma — sadece /tmp/'e çıkar")
    args = parser.parse_args()

    if args.auto or not args.path:
        path = find_active_jsonl()
        if not path:
            print("ERROR: Aktif JSONL bulunamadı.", file=sys.stderr)
            sys.exit(1)
        print(f"Otomatik bulundu: {path}")
    else:
        path = Path(args.path)

    if not path.exists():
        print(f"ERROR: Dosya yok: {path}", file=sys.stderr)
        sys.exit(1)

    # 1. Yedek al
    backup = path.with_suffix(f".jsonl.bak-{int(time.time())}")
    shutil.copy2(path, backup)
    print(f"Yedek: {backup}")

    # 2. Hash kaydet (referans)
    orig_hash = compute_hash(path)
    orig_size = path.stat().st_size

    # 3. Oku
    with open(path) as f:
        lines = f.readlines()
    print(f"Toplam satır: {len(lines)}, boyut: {orig_size:,} bytes, hash: {orig_hash}")

    # 4. Compress
    new_lines, elided, saved = compress_messages(lines, protection_zone_n=args.protection_zone)

    new_size = sum(len(l.encode()) for l in new_lines)
    print(f"Elided tool_result block'ları: {elided}")
    print(f"Bytes saved (raw text): {saved:,}")
    print(f"Yeni boyut: {new_size:,} bytes (% {100 * (orig_size - new_size) / orig_size:.1f} saving)")

    if args.dry_run:
        out_path = Path("/tmp/cowork-poc-cleaned.jsonl")
        with open(out_path, "w") as f:
            f.writelines(new_lines)
        print(f"Dry-run output: {out_path}")
        print("Yerine yazılmadı. --dry-run kapalı çalıştırırsan değiştirir.")
        return

    # 5. Atomic write: tmp dosyaya yaz, sonra rename
    tmp = path.with_suffix(f".jsonl.tmp-{os.getpid()}")
    with open(tmp, "w") as f:
        f.writelines(new_lines)
    os.replace(tmp, path)  # atomic on POSIX

    new_hash = compute_hash(path)
    print(f"\nYerine yazıldı.")
    print(f"Eski hash: {orig_hash}")
    print(f"Yeni hash: {new_hash}")
    print(f"\nŞimdi Cowork session'da yeni bir turn at:")
    print(f"  Örnek soru: 'Az önceki bash komutunda ne yaptık?'")
    print(f"  - Doğru cevap (içeriği hatırlıyor) → JSONL write-only log")
    print(f"  - 'ELIDED' diyor veya yanlış → JSONL authoritative source ✓ transparent middleware mümkün")
    print(f"\nGeri almak için: cp {backup} {path}")


if __name__ == "__main__":
    main()
