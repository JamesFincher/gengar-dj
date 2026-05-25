# Gengar DJ 📻

A Discord bot that runs a **silence-activated lofi radio** in voice channels. When nobody's talking for 25 seconds, it starts playing a shuffle of Hayden's custom Suno-generated lofi tracks. When someone speaks, it fades out. New songs can be created on-the-fly via `/create`, which routes through Hermes/Gengar for Suno AI generation.

This version is **100% stateless and cloud-native** — it offloads all audio file storage and playlist metadata directly to **Cloudflare R2** (using S3-compatible pre-signed URLs or public custom domains). No local disk, no PVC lag! ::]

Built for **Hayden** by **James** and **Gengar** ::]

## Features

- 🎧 **Silence-Activated Radio** — Joins voice channels, listens for quiet, plays lofi when nobody's talking
- ☁️ **Cloudflare R2 Backend** — Uploads and streams all audio files and metadata dynamically from R2 storage (0 egress fees!)
- 🎵 **Suno Integration** — `/create` generates custom lofi tracks via Suno AI, uploaded directly to R2
- 🎛 **Slash Commands** — Full radio control panel
- 🔗 **API-First** — Exposes an HTTP API for Hermes/Gengar callbacks
- ☸️ **k3s Native** — Stateless deployment in your homelab cluster with a Tailscale sidecar

## Architecture

```
┌─────────┐    /create [prompt]    ┌──────────────┐    POST /webhook    ┌─────────┐
│ Discord ├───────────────────────►│ Gengar DJ    ├───────────────────►│ Hermes  │
│  User   │                        │ (k3s Pod)    │                    │ Webhook │
└─────────┘                        │              │                    └────┬────┘
                                   │  ┌────────┐  │                        │
                                   │  │ Radio  │  │                        │ fires
                                   │  │  Cog   │  │                        │ agent
                                   │  ├────────┤  │                        │ run
                                   │  │ Player │  │                        │
                                   │  ├────────┤  │                   ┌────▼────┐
                                   │  │  API   │  │                   │ Gengar  │
                                   │  │ Server │  │◄──── callback ────│(Hermes) │
                                   │  └────▲───┘  │      file_key     │         │
                                   └───────┼──────┘                   │ Generates│
                                           │                          │  track & │
                                           │ uploads/                 │  uploads │
                                           │ updates                  │  to R2   │
                                           │                          └────┬─────┘
                                   ┌───────▼──────┐                        │
                                   │  Cloudflare  │◄───────────────────────┘
                                   │  R2 Storage  │
                                   └──────────────┘
```

## Slash Commands

### Radio Control
- `/play [genre] [silence]` — Start the lofi radio in your current VC
- `/stop` — Stop the radio and leave voice
- `/skip` — Skip the current track
- `/volume <0-100>` — Set playback volume
- `/status` — Show current radio state
- `/genre <style>` — Filter playlist by genre (lofi, jazzhop, citypop, etc.)
- `/silence <seconds>` — Set silence threshold (5-300s)

### Song Creation
- `/create <prompt> [style] [title] [play]` — Generate a new lofi track via Suno AI

### Admin
- `/playlist` — Show the current song library loaded from R2
- `/reload` — Reload songs from Cloudflare R2 (admin only)
- `/info` — Show bot technical info

## Deployment

### Prerequisites
- k3s cluster (gengar-lab namespace)
- Discord bot token with voice intents enabled
- Hermes webhook platform running (port 8644)
- Cloudflare R2 bucket with read/write credentials
- Suno account for `/create` functionality

### Quick Start

```bash
# 1. Create the Discord bot
#    — Go to https://discord.com/developers/applications
#    — Create application → Bot → Enable "Server Members Intent" & "Voice State Intent"
#    — Copy token

# 2. Create secrets (including Cloudflare R2 credentials)
kubectl create secret generic gengar-dj-secrets \
  --namespace gengar-lab \
  --from-literal=DISCORD_BOT_TOKEN='your-bot-token' \
  --from-literal=HERMES_WEBHOOK_SECRET='your-webhook-secret' \
  --from-literal=R2_ACCOUNT_ID='your-r2-account-id' \
  --from-literal=R2_ACCESS_KEY_ID='your-r2-access-key-id' \
  --from-literal=R2_SECRET_ACCESS_KEY='your-r2-secret-access-key' \
  --from-literal=R2_BUCKET_NAME='your-r2-bucket-name' \
  --from-literal=R2_PUBLIC_URL='https://your-public-domain-if-any.com'

# 3. Deploy ConfigMap and Service
kubectl apply -f deploy/k3s-configmap.yaml
kubectl apply -f deploy/k3s-service.yaml

# 4. Deploy Stateless Bot
kubectl apply -f deploy/k3s-deployment.yaml

# 5. Set up Gengar Webhook on Claw Node
hermes webhook subscribe gengar-dj-create \
  --events "song_create" \
  --prompt "A Discord user requested a new Suno song. Let's run the generator script with the following parameters:
Prompt: {payload.prompt}
Style Tags: {payload.style_tags}
Title: {payload.title}
Callback URL: {payload.callback_url}
Guild ID: {payload.guild_id}
Channel ID: {payload.channel_id}
User ID: {payload.user_id}
Play in VC: {payload.play_in_vc}

Instructions:
1. Run the Python generation script at '/home/james/.hermes/projects/gengar-dj/scripts/generate_song.py' using these exact inputs.
2. If play_in_vc is True, 'true', or True, include the '--play-in-vc' flag.
3. Report the execution logs and completion status back." \
  --deliver origin \
  --secret "your-webhook-secret"
```

## Cloudflare R2 Environment Variables

| Variable | Description |
|----------|-------------|
| `R2_ACCOUNT_ID` | Cloudflare Account ID (copied from the R2 dashboard URL) |
| `R2_ACCESS_KEY_ID` | S3-compatible Access Key ID |
| `R2_SECRET_ACCESS_KEY` | S3-compatible Secret Access Key |
| `R2_BUCKET_NAME` | Name of your R2 bucket (e.g. `gengar-dj-library`) |
| `R2_PUBLIC_URL` | Optional: Public dev subdomain or custom domain pointing to your bucket. If left blank, Gengar DJ automatically generates secure, private pre-signed S3v4 GET URLs valid for 1 hour. |

## How the Stateless Streaming Works

1. **Initialization:** On join, Gengar DJ retrieves a listing of all audio files in the Cloudflare R2 bucket and fetches `playlist.json` (which holds titles, custom artwork, and style tags).
2. **Silence Monitoring:** The bot joins the VC, starts the PCM energy tracker (`SilenceSink`), and listens.
3. **Stream Initiation:** Once the silence threshold is breached, the bot selects the next track. If `R2_PUBLIC_URL` is omitted, the bot generates a secure pre-signed S3v4 URL via boto3 on-the-fly and streams the audio buffer directly to Discord voice using FFmpeg:
   `discord.FFmpegPCMAudio(play_url, before_options="-reconnect 1 ...")`
4. **Instant Interruption:** The second voice activity is heard, the stream is halted and discarded. Silence resets the timer.

## License

MIT — do what you want, just don't blame us when the lofi hits too hard.
