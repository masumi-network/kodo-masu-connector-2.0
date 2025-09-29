# Available Kodosumi Agents

## Quick Setup

For a clean installation (local laptop, new VM, CI runner, etc.) all services and the database bootstrap with a single command:

```bash
cp .env.example .env              # update secrets as needed
docker compose up -d --build      # or bash startup.sh
```

`database/init.sql` now contains the full schema (flows/job columns, cron history, constraints), so no extra migration step is required on fresh machines. The authenticator seeds an API key on first boot and the flow-sync job automatically populates the catalog once the key is available. For upgrades of an existing deployment, keep using `./scripts/apply_migrations.sh` to layer any new changes on top of live data.

The table below reflects the flows currently synced into the connector (last refreshed via `flow-sync` on the current environment). Endpoints are taken directly from the upstream Kodosumi catalog; if the host portion is `0.0.0.0` you will need to replace it with a routable hostname/IP before the starter can reach it.

| Flow | UID | Kodosumi Path | URL Identifier |
|------|-----|---------------|----------------|
| AI Movie Production Agent | fe4320953f14fae3d14440d1dbd145fa | `/-/0.0.0.0/8000/movie-production-agent/-/` | *(none)* |
| AttentionInsight Analysis Agent | bb2a50b07e301c7b11f5ca7d98e43041 | `/-/0.0.0.0/8000/attention-insights-agent/-/` | *(none)* |
| DeepSeek Research Agent | 79bf8b6d5ead8cb324ba510dcd50da02 | `/-/0.0.0.0/8000/deepseek-research-agent/-/` | *(none)* |
| LLMs.txt Generator Agent | b83b6c72c0c77f4519ad50ff1263bad4 | `/-/0.0.0.0/8000/llm-txt-agent/-/` | *(none)* |
| Media Trend Analysis Agent | ff875ccbd83c527c20838f78173c17c7 | `/-/0.0.0.0/8000/media-trend-agent/-/` | *(none)* |
| Meme Creator Agent | 6164617dc39ff681cf32031aa4354c47 | `/-/0.0.0.0/8000/meme-creator/-/` | *(none)* |
| SEO Analysis Agent | a3e28d905e25f0c2a462b72c79dfa56f | `/-/0.0.0.0/8000/seo-agent/-/` | *(none)* |
| Veo3 Video Generator | 81519db9ef3244dfd46e1a926fad1ddc | `/-/0.0.0.0/8000/veo3-video-generator/-/` | *(none)* |
| X (Twitter) User Analyzer | 618ff4bf9300e02107d3b4ac0cade7db | `/-/0.0.0.0/8000/x_analyzer/-/` | *(none)* |

> **Note**: Replace the `0.0.0.0` host with the actual Kodosumi host (e.g. `138.68.64.204`) when calling these endpoints manually or from the job starter. If the upstream catalog is updated with correct hosts, the next flow sync run will reflect the changes here.
