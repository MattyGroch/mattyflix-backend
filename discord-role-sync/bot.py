"""Discord role sync for Mattyflix.

Keeps one Discord role (the "Member" role) in sync with who currently has
access to the Plex server:

  * People with Plex access get the role.
  * People who lose Plex access (Wizarr expiry, manual removal, etc.) lose it.

Plex users are matched to Discord accounts by the Discord ID they link in
Seerr (Profile -> Settings -> Notifications), plus an optional manual
links file for people who never linked it themselves.

Runs a full sync every SYNC_INTERVAL_MINUTES, and also checks new members
the moment they join the server.
"""

import asyncio
import json
import logging
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET

import aiohttp
import discord

log = logging.getLogger("role-sync")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable {name}")
    return value


DISCORD_TOKEN = env("DISCORD_TOKEN", required=True)
GUILD_ID = int(env("GUILD_ID", required=True))
ROLE_ID = int(env("ROLE_ID", required=True))
PLEX_TOKEN = env("PLEX_TOKEN", required=True)
PLEX_MACHINE_ID = env("PLEX_MACHINE_ID", required=True)
SEERR_URL = env("SEERR_URL", "http://seerr:5055").rstrip("/")
SEERR_API_KEY = env("SEERR_API_KEY", required=True)
LINKS_FILE = env("LINKS_FILE", "/config/links.json")
SYNC_INTERVAL_MINUTES = int(env("SYNC_INTERVAL_MINUTES", "15"))
MAX_REMOVALS_PER_RUN = int(env("MAX_REMOVALS_PER_RUN", "5"))
DRY_RUN = env("DRY_RUN", "true").lower() not in ("false", "0", "no")
PROTECTED_IDS = {int(x) for x in env("PROTECTED_DISCORD_IDS", "").replace(" ", "").split(",") if x}
SEERR_PUBLIC_URL = env("SEERR_PUBLIC_URL", "https://search.mattyflix.com").rstrip("/")
WELCOME_DM = env("WELCOME_DM", "true").lower() not in ("false", "0", "no")

WELCOME_TEXT = """\
👋 Welcome to the Mattyflix Discord!

Most of the server unlocks once your Discord is linked to your Plex account. It takes a minute:

1. Open {seerr}/profile/settings/notifications/discord and sign in with your **Plex** account (if it sends you to the home page after login, open the link again)
2. Paste this into the **User ID** field and save:
```
{discord_id}
```
3. That's it. Your access unlocks automatically within a few minutes.

Don't have Plex access yet? Ask whoever invited you for a Mattyflix invite link first.\
"""


class SourceError(Exception):
    """Plex or Seerr could not be read reliably; removals must be skipped."""


async def fetch_plex_access(session):
    """Return identifiers (user id, username, email; lowercased) of everyone shared on the server."""
    url = f"https://plex.tv/api/servers/{PLEX_MACHINE_ID}/shared_servers"
    async with session.get(url, params={"X-Plex-Token": PLEX_TOKEN}, timeout=aiohttp.ClientTimeout(total=30)) as r:
        if r.status != 200:
            raise SourceError(f"plex.tv returned HTTP {r.status}")
        root = ET.fromstring(await r.text())
    keys = set()
    count = 0
    for s in root.iter("SharedServer"):
        count += 1
        for attr in ("userID", "username", "email"):
            v = (s.get(attr) or "").strip().lower()
            if v:
                keys.add(v)
    if count == 0:
        raise SourceError("plex.tv returned zero shared users")
    return keys, count


async def fetch_seerr_links(session):
    """Return {discord_id: [plex identifiers]} from Seerr users who linked a Discord ID."""
    headers = {"X-Api-Key": SEERR_API_KEY}
    timeout = aiohttp.ClientTimeout(total=30)
    async with session.get(f"{SEERR_URL}/api/v1/user", params={"take": 1000}, headers=headers, timeout=timeout) as r:
        if r.status != 200:
            raise SourceError(f"Seerr user list returned HTTP {r.status}")
        users = (await r.json()).get("results", [])
    links = {}
    for u in users:
        async with session.get(f"{SEERR_URL}/api/v1/user/{u['id']}/settings/notifications", headers=headers, timeout=timeout) as r:
            if r.status != 200:
                raise SourceError(f"Seerr notification settings for user {u['id']} returned HTTP {r.status}")
            settings = await r.json()
        raw = settings.get("discordId") or settings.get("discordIds") or ""
        ids = raw if isinstance(raw, list) else str(raw).replace(" ", "").split(",")
        idents = [str(x).strip().lower() for x in (u.get("plexId"), u.get("plexUsername"), u.get("email")) if x]
        for did in ids:
            did = str(did).strip()
            if did.isdigit():
                links.setdefault(int(did), []).extend(idents)
    return links


def load_manual_links():
    """links.json: {"<discord id>": "<plex username or email>"}"""
    try:
        with open(LINKS_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise SourceError(f"{LINKS_FILE} is not valid JSON: {e}")
    return {int(k): [str(v).strip().lower()] for k, v in data.items() if str(k).isdigit() and v}


class RoleSync(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.entitled = None
        self.entitled_at = 0.0
        self.lock = asyncio.Lock()

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        self.loop.create_task(self.sync_loop())

    async def close(self):
        await self.http_session.close()
        await super().close()

    async def load_entitled(self):
        """Return the set of Discord IDs whose linked Plex account currently has access."""
        plex_keys, plex_count = await fetch_plex_access(self.http_session)
        links = await fetch_seerr_links(self.http_session)
        for did, idents in load_manual_links().items():
            links.setdefault(did, []).extend(idents)
        entitled = {did for did, idents in links.items() if any(i in plex_keys for i in idents)}
        log.info("Plex users with access: %d | Discord IDs linked: %d | entitled: %d", plex_count, len(links), len(entitled))
        self.entitled, self.entitled_at = entitled, time.monotonic()
        return entitled

    async def set_role(self, member, role, add, reason):
        verb = "ADD" if add else "REMOVE"
        if DRY_RUN:
            log.info("[dry run] would %s %s for %s (%s)", verb, role.name, member, reason)
            return
        if add:
            await member.add_roles(role, reason=reason)
        else:
            await member.remove_roles(role, reason=reason)
        log.info("%s %s for %s (%s)", verb, role.name, member, reason)

    async def sync(self):
        async with self.lock:
            guild = self.get_guild(GUILD_ID)
            role = guild.get_role(ROLE_ID) if guild else None
            if not guild or not role:
                log.error("Guild %s or role %s not found", GUILD_ID, ROLE_ID)
                return
            try:
                entitled = await self.load_entitled()
                sources_ok = True
            except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("Could not read Plex/Seerr (%s); adding only, skipping removals", e)
                entitled, sources_ok = self.entitled or set(), False
            protected = PROTECTED_IDS | {guild.owner_id}
            to_remove = []
            for m in guild.members:
                if m.bot:
                    continue
                has = role in m.roles
                if m.id in entitled and not has:
                    await self.set_role(m, role, True, "has Plex access")
                elif has and m.id not in entitled and m.id not in protected:
                    to_remove.append(m)
            if not to_remove:
                return
            if not sources_ok:
                log.warning("Skipping %d removal(s) because sources were unavailable", len(to_remove))
            elif len(to_remove) > MAX_REMOVALS_PER_RUN:
                log.warning("%d removals exceed MAX_REMOVALS_PER_RUN=%d; not removing anyone. Would remove: %s",
                            len(to_remove), MAX_REMOVALS_PER_RUN, ", ".join(str(m) for m in to_remove))
            else:
                for m in to_remove:
                    await self.set_role(m, role, False, "no current Plex access")

    async def sync_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.sync()
            except Exception:
                log.exception("Sync failed")
            await asyncio.sleep(SYNC_INTERVAL_MINUTES * 60)

    async def on_ready(self):
        log.info("Logged in as %s | guild %s | role %s | dry run: %s", self.user, GUILD_ID, ROLE_ID, DRY_RUN)

    async def on_member_join(self, member):
        if member.guild.id != GUILD_ID or member.bot:
            return
        try:
            if self.entitled is None or time.monotonic() - self.entitled_at > 60:
                await self.load_entitled()
        except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("New member %s: could not refresh sources (%s); using last known list", member, e)
        role = member.guild.get_role(ROLE_ID)
        if role and self.entitled and member.id in self.entitled:
            await self.set_role(member, role, True, "joined with Plex access")
        else:
            log.info("New member %s has no linked Plex access (yet)", member)
            await self.send_welcome(member)

    async def send_welcome(self, member):
        """DM an unlinked new member their Discord ID and how to link it in Seerr."""
        if not WELCOME_DM:
            return
        if DRY_RUN:
            log.info("[dry run] would DM link instructions to %s", member)
            return
        try:
            await member.send(WELCOME_TEXT.format(seerr=SEERR_PUBLIC_URL, discord_id=member.id))
            log.info("Sent link instructions to %s", member)
        except discord.Forbidden:
            log.info("Could not DM %s (DMs closed)", member)


if __name__ == "__main__":
    RoleSync().run(DISCORD_TOKEN, log_handler=None)
