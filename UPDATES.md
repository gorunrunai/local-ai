# Updates

What's new in each version of GoRunRun Local AI, newest first. The app's **Settings → Updates** reads this
file from GitHub to tell you when a new version is out, and shows the notes below.

To update, run the install command again (it keeps your chats, settings and downloaded models):

```sh
curl -fsSL https://raw.githubusercontent.com/gorunrunai/local-ai/main/install.sh | bash
```

<!-- Releasing: bump VERSION, and add a "## <version> (<YYYY-MM-DD>)" section at the top. -->

## 0.1.0 (2026-09-25)

The first public release.

- Chat with text, photos, screenshots, documents, audio and video, all running on your Mac.
- Talk mode: spoken conversations you can interrupt, with a lava-lamp that shows what it's doing.
- Create videos with sound (LTX-2.3) or sharper silent ones (Wan 2.2), and animate your photos.
- Private web search you can switch off, so nothing goes online at all.
- Use it from your phone over your own Tailscale network, with a QR code to sign in.
- A setup summary that shows what your Mac can do, with a check for updates in Settings.
