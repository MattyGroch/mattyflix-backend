# mattyflix-backend

The Mattyflix media stack (the *arr apps, SABnzbd, Seerr, Tautulli, Tracearr, Kometa, Maintainerr, and so on), deployed as one Portainer stack straight from this repo.

## Deploying in Portainer

1. **Stacks > Add stack**, build method **Repository**
   - Repository URL: `https://github.com/MattyGroch/mattyflix-backend`
   - Reference: `refs/heads/main`
   - Compose path: `docker-compose.yml`
2. **GitOps updates**: on, mechanism **Polling** (5m). A push to `main` redeploys the stack.
3. **Environment variables**: *Load variables from .env file* and upload your filled-in `stack.env`. Start from `stack.env.example`.
4. Deploy.

## Host requirements

- External Docker network `proxy`, shared with Traefik: `docker network create proxy`
- App config in `/var/config/<app>/`, owned by `PUID=1000` / `PGID=999`
- NAS shares mounted at `/mnt/nas1/mattyflix`, `/mnt/nas2/mattyflix`, `/mnt/nas3/mattyflix`
- Traefik routes web UIs through `traefik.*` labels on the `websecure` entrypoint

## Secrets

This repo is **public**. Keep every secret in Portainer's stack environment variables, which `docker-compose.yml` references as `${VAR}`. `.gitignore` blocks `.env` and `stack.env`.

## Caution

Run this stack on **one host at a time**. A second copy would resume the same SABnzbd queues, run the Maintainerr deletion rules twice, and have Kometa edit Plex metadata twice.
