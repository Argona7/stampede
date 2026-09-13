#!/usr/bin/env python3
"""One-time setup of the STAMPEDE Calls Telegram channel from the owner's account (Pyrogram user session).

- creates the public channel (title, description, brand avatar, first free @handle),
- creates a bot through @BotFather and makes it an admin that can post / edit / pin,
- writes TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL (chat id) and TELEGRAM_CHANNEL_USERNAME into .env (chmod 600),
- posts and pins a welcome message through the Bot API to prove the bot can post.

Idempotent: if .env already has TELEGRAM_CHANNEL / TELEGRAM_BOT_TOKEN the corresponding steps are skipped.
Secrets are never printed. Run with the tg-dump venv (Pyrogram 2):

    /Users/argona/tg_dump_venv/bin/python3 scripts/tg_setup.py [--handles stampede_calls stampede_signals stampedecalls]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import requests
from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import ChatPrivileges

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
AVATAR = ROOT / "assets" / "brand" / "avatar-1024.png"
CFG = Path("/Users/argona/.claude/skills/tg-dump/config/settings.json")
TITLE = "STAMPEDE Calls"
DESCRIPTION = (
    "Live runner calls from STAMPEDE on Robinhood Chain (PONS v2): wallet rotations → ENTER verdicts with "
    "probability, size, exit plan. Every call gets its +30/+60 min outcome. Recorded outcomes, not returns. "
    "Not advice. github.com/Argona7/stampede"
)
assert len(DESCRIPTION) <= 255, len(DESCRIPTION)
WELCOME = (
    "<b>STAMPEDE Calls</b>\n\n"
    "Live runner calls from the STAMPEDE engine on Robinhood Chain (PONS v2).\n"
    "A call = an <b>ENTER</b> verdict: many distinct wallets rotating into a young coin, scored out-of-sample, "
    "with probability of 2× in 30 min, suggested size and an exit plan.\n"
    "Every call is followed by its <b>+30 / +60 min outcome</b> as a reply — wins and losses alike.\n\n"
    "Recorded outcomes, not returns. Not financial advice.\n"
    "Source and how it works: github.com/Argona7/stampede"
)


def env_read() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def env_write(updates: dict[str, str]) -> None:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    keys = set(updates)
    out = []
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if k in keys:
            out.append(f"{k}={updates[k]}")
            keys.discard(k)
        else:
            out.append(line)
    if keys:
        out.append("")
        out.append("# STAMPEDE Calls (Telegram): written by scripts/tg_setup.py; never commit this file")
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "TELEGRAM_CHANNEL_USERNAME", "TELEGRAM_BOT_USERNAME"):
            if k in keys:
                out.append(f"{k}={updates[k]}")
    ENV.write_text("\n".join(out).rstrip("\n") + "\n")
    ENV.chmod(0o600)


async def botfather_reply(app: Client, after_id: int, timeout: float = 20.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        async for m in app.get_chat_history("BotFather", limit=1):
            if m.id > after_id and not m.outgoing:
                return m.text or m.caption or ""
        await asyncio.sleep(1.0)
    raise TimeoutError("BotFather did not answer in time")


async def ask_botfather(app: Client, text: str) -> str:
    sent = await app.send_message("BotFather", text)
    return await botfather_reply(app, sent.id)


async def create_bot(app: Client, base: str) -> tuple[str, str]:
    """Returns (bot_username, token). Tries base, base2, base_v2 ... until BotFather accepts."""
    reply = await ask_botfather(app, "/newbot")
    if "name" not in reply.lower():
        # a previous /newbot may be pending: cancel and retry once
        await ask_botfather(app, "/cancel")
        reply = await ask_botfather(app, "/newbot")
        if "name" not in reply.lower():
            raise RuntimeError("BotFather did not ask for a name (it said something else); stop and ask the owner")
    await ask_botfather(app, TITLE)
    candidates = [base, base + "2", base + "_v2", "stampedecalls_bot", "stampede_signals_bot"]
    for uname in candidates:
        reply = await ask_botfather(app, uname)
        m = re.search(r"(\d{6,}:[A-Za-z0-9_-]{30,})", reply)
        if m:
            return uname, m.group(1)
        if "taken" in reply.lower() or "occupied" in reply.lower() or "already" in reply.lower():
            continue
        if "sorry" in reply.lower() or "invalid" in reply.lower():
            continue
        raise RuntimeError("BotFather answered something unexpected while choosing the username; stop and ask the owner")
    raise RuntimeError("all bot usernames taken")


async def main(handles: list[str]) -> int:
    cfg = json.load(open(CFG))
    a = cfg["accounts"]["argona"]
    env = env_read()
    async with Client(a["session_name"], api_id=int(a["api_id"]), api_hash=a["api_hash"], workdir=a["session_workdir"]) as app:
        me = await app.get_me()
        print(f"account: @{me.username}")
        chat_id = env.get("TELEGRAM_CHANNEL")
        username = env.get("TELEGRAM_CHANNEL_USERNAME")
        if chat_id:
            chat = await app.get_chat(int(chat_id))
            print(f"channel exists: {chat.title} (@{chat.username})")
        else:
            chat = await app.create_channel(TITLE, DESCRIPTION)
            print(f"channel created: {chat.title} id={chat.id}")
            try:
                await app.set_chat_photo(chat.id, photo=str(AVATAR))
                print("avatar set")
            except RPCError as e:
                print(f"avatar failed: {e}")
            username = None
            for h in handles:
                try:
                    ok = await app.set_chat_username(chat.id, h)
                    if ok:
                        username = h
                        print(f"public handle: @{h}")
                        break
                except FloodWait as e:
                    print(f"flood wait {e.value}s on handle; sleeping")
                    await asyncio.sleep(e.value + 1)
                except RPCError as e:
                    print(f"@{h}: {type(e).__name__}")
            if not username:
                print("no public handle set (all candidates taken or limit reached); channel stays private for now")
            chat_id = str(chat.id)
            env_write({"TELEGRAM_CHANNEL": chat_id, "TELEGRAM_CHANNEL_USERNAME": username or ""})
        token = env.get("TELEGRAM_BOT_TOKEN")
        bot_username = env.get("TELEGRAM_BOT_USERNAME")
        if not token:
            bot_username, token = await create_bot(app, "stampede_calls_bot")
            env_write({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_BOT_USERNAME": bot_username})
            print(f"bot created: @{bot_username} (token written to .env, not printed)")
        # admin rights for the bot
        try:
            await app.promote_chat_member(int(chat_id), bot_username, ChatPrivileges(can_post_messages=True, can_edit_messages=True, can_delete_messages=True, can_pin_messages=True, can_manage_chat=True, can_invite_users=True))
            print(f"@{bot_username} promoted to admin")
        except RPCError as e:
            print(f"promote: {type(e).__name__}: {e}")
    # prove the bot can post: welcome + pin via Bot API
    api = f"https://api.telegram.org/bot{token}"
    r = requests.post(f"{api}/sendMessage", json={"chat_id": int(chat_id), "text": WELCOME, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
    if r.status_code != 200:
        print(f"bot post failed: HTTP {r.status_code}: {r.text[:200].replace(token, '***')}")
        return 1
    mid = r.json()["result"]["message_id"]
    requests.post(f"{api}/pinChatMessage", json={"chat_id": int(chat_id), "message_id": mid, "disable_notification": True}, timeout=20)
    link = f"https://t.me/{username}" if username else f"(private) chat {chat_id}"
    print(f"welcome posted and pinned (message {mid}); channel: {link}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--handles", nargs="*", default=["stampede_calls", "stampede_signals", "stampedecalls", "stampede_runner_calls"])
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.handles)))
