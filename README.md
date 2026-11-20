# Personal German practice podcast

Episodes are written as YAML in `episodes/`. On push, a GitHub Action synthesizes new ones with
Google Cloud Text-to-Speech (Chirp 3 HD voices), writes MP3s to `docs/audio/`, and rebuilds
`docs/feed.xml`, served by GitHub Pages. The feed sets `itunes:block` so it never appears in the
Apple Podcasts directory.

- Secret: `GCP_TTS_KEY` (service-account JSON, no roles needed)
- Pages: Settings → Pages → Deploy from branch → `main` / `docs`
- Subscribe: Apple Podcasts → Library → ••• → Follow a Show by URL → `https://<owner>.github.io/<repo>/feed.xml`
- Cost guard: `usage.json` tracks characters per month; builds stop at 900k (free tier is 1M)

Segment types: `en`, `de` (+`voice: A|B`, `slow`), `pause`, `drill` (EN prompt → pause → DE ×2),
`dialogue` (+`slow`, `shadow`). See `build.py`.
