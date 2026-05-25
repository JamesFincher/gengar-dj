# Gengar DJ 📻

A Discord bot that runs a **silence-activated lofi radio** in voice channels. When nobody's talking for 25 seconds, it starts playing a shuffle of Hayden's custom Suno-generated lofi tracks. When someone speaks, it fades out. New songs can be created on-the-fly via `/create`, which routes through Hermes/Gengar for Suno AI generation.

Built for **Hayden** by **James** and **Gengar** ::]

## Features

- 🎧 **Silence-Activated Radio** — Joins voice channels, listens for quiet, plays lofi when nobody's talking
- 🎵 **Suno Integration** — `/create` generates custom lofi tracks via Suno AI, routed through Hermes
- 📋 **Smart Playlist** — Manages a library of pre-downloaded + Suno-generated tracks
- 🎛 **Slash Commands** — Full radio control panel
- 🔗 **API-First** — Exposes an HTTP API for Hermes/Gengar callbacks
- ☸️ **k3s Native** — Runs in your homelab cluster with Tailscale sidecar

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
                                   │  ├────────┤  │    song + file    │         │
                                   │  │ Songs/ │  │                   │ Generates│
                                   │  │ Queue  │  │                   │ Suno song│
                                   └──────────────┘                   └─────────┘
```

## Slash Commands

### Radio Control
| Command | Description |
|---------|-------------|
| `/play [genre] [silence]` | Start the lofi radio in your current VC |
| `/stop` | Stop the radio and leave voice |
| `/skip` | Skip the current track |
| `/volume <0-100>` | Set playback volume |
| `/status` | Show current radio state |
| `/genre <style>` | Filter playlist by genre (lofi, jazzhop, citypop, etc.) |
| `/silence <seconds>` | Set silence threshold (5-300s) |

### Song Creation
| Command | Description |
|---------|-------------|
| `/create <prompt> [style] [title] [play]` | Generate a new lofi track via Suno AI |

### Admin
| Command | Description |
|---------|-------------|
| `/playlist` | Show the current song library |
| `/reload` | Reload songs from disk (admin) |
| `/info` | Show bot technical info |

## Deployment

### Prerequisites
- k3s cluster (gengar-lab namespace)
- Discord bot token with voice intents enabled
- Hermes webhook platform running (port 8644)
- Suno account for `/create` functionality

### Quick Start

```bash
# 1. Create the Discord bot
#    — Go to https://discord.com/developers/applications
#    — Create application → Bot → Enable "Server Members Intent" & "Voice State Intent"
#    — Copy token

# 2. Create secrets
kubectl create secret generic gengar-dj-secrets \
  --namespace gengar-lab \
  --from-literal=DISCORD_BOT_TOKEN='your-bot-token' \
  --from-literal=HERMES_WEBHOOK_SECRET='your-webhook-secret'

kubectl create secret generic tailscale-auth \
  --namespace gengar-lab \
  --from-literal=authkey='tskey-auth-xxxx'

# 3. Deploy
kubectl apply -f deploy/k3s-configmap.yaml
kubectl apply -f deploy/k3s-deployment.yaml
kubectl apply -f deploy/k3s-service.yaml

# 4. Register slash commands
# The bot auto-registers commands on startup via tree.sync()

# 5. Set up Hermes webhook
hermes webhook subscribe gengar-dj-create \
  --events "song_create" \
  --prompt "A new song creation request arrived from Discord. Generate a lofi track using Suno AI. Details: {payload}" \
  --skills "songwriting-and-ai-music" \
  --deliver telegram \
  --secret "your-webhook-secret"
```

### Env Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | — | Discord bot token (required) |
| `HERMES_WEBHOOK_URL` | `http://100.70.184.45:8644/...` | Where to POST `/create` requests |
| `HERMES_WEBHOOK_SECRET` | — | HMAC secret for webhook auth |
| `BOT_API_PORT` | `8080` | Internal API server port |
| `BOT_CALLBACK_URL` | `http://gengar-dj-bot...` | URL for Hermes to POST back to |
| `SONGS_DIR` | `/data/songs` | Where MP3 files are stored |
| `SILENCE_THRESHOLD` | `25` | Seconds of silence before radio starts |
| `FADE_DURATION` | `3` | Seconds for audio crossfade |
| `LOG_LEVEL` | `INFO` | Python log level |

## `/create` Flow

1. User runs `/create "rainy lofi with vinyl crackle"` in Discord
2. Bot sends a POST to the Hermes webhook with prompt + callback URL
3. Hermes fires Gengar with the songwriting-and-ai-music skill
4. Gengar generates the Suno track using Camofox browser automation
5. Gengar downloads the MP3 and POSTs it to the bot's callback API
6. Bot adds the song to the playlist, posts a message, optionally plays in VC

## Voice Channel Flow

1. User runs `/play` while in a voice channel
2. Bot joins VC and starts a `SilenceSink` — an AudioSink that monitors PCM audio energy
3. When RMS energy drops below threshold for N seconds → radio starts
4. Bot shuffles through the song library (FFmpegPCMAudio)
5. When voice energy is detected → playback stops instantly
6. Silence resets → music resumes after N seconds

## Building the Container

```bash
docker build -t ghcr.io/jamesfincher/gengar-dj:latest -f deploy/Dockerfile .
docker push ghcr.io/jamesfincher/gengar-dj:latest
```

## License

MIT — do what you want, just don't blame us when the lofi hits too hard.
