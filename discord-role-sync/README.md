# discord-role-sync

Keeps the Discord **Member** role (role ID `1174435703615135858`, formerly "Plex Access") in sync with who has access to the Plex server.

- **Plex access** means being on the server's share list at plex.tv. When Wizarr expires or removes someone, they drop off that list.
- **Discord identity** comes from the Discord ID each user links in Seerr (*Profile → Settings → Notifications → Discord ID*), plus an optional manual file for people who never link it.
- Runs a full sync every 15 minutes, and checks new members the moment they join, so someone with access gets the role right after joining.

The role is matched by ID, so renaming it in Discord doesn't affect anything.

## Safety

- **Dry run by default.** With `ROLE_SYNC_DRY_RUN=true` it only logs `[dry run] would ADD/REMOVE …`. Set it to `false` once the log looks right.
- If plex.tv or Seerr can't be read, or plex.tv returns zero users, **no removals happen** that run.
- More than `ROLE_SYNC_MAX_REMOVALS` (default 5) removals in one run are logged, not applied.
- The server owner and any IDs in `ROLE_SYNC_PROTECTED_IDS` never lose the role.

## Manual links

For members who never linked Discord in Seerr, create `/var/config/discord-role-sync/links.json` on the Docker host:

```json
{
  "123456789012345678": "their-plex-username-or-email"
}
```

The file is re-read on every sync, so no restart is needed.

## Requirements

- The bot (PlexBot) needs the **Manage Roles** permission, and its own role must be **above** the Member role.
- The **Server Members Intent** must be enabled for the bot in the Discord developer portal (it is).
- Environment variables: see `stack.env.example`.
