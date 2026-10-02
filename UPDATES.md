# Updates

What's new in each version of GoRunRun Local AI, newest first. The app's **Settings → Updates** reads this
file from GitHub to tell you when a new version is out, and shows the notes below.

To update, run the install command again (it keeps your chats, settings and downloaded models):

```sh
curl -fsSL https://raw.githubusercontent.com/gorunrunai/local-ai/main/install.sh | bash
```

<!-- Releasing: bump VERSION, and add a "## <version> (<YYYY-MM-DD>)" section at the top. -->

## 0.1.1 (2026-10-02)

Longer videos, a save dialog for downloads, and an installer that copes better when something goes wrong.

- **Choose how long videos can be.** Settings → Models → Video generation now has a slider for each video
  engine. LTX-2.3 can go up to 20 seconds (it was fixed at 10), or less if your Mac's memory can't fit it.
  Below the slider you see how much memory a clip that long uses and roughly how long it takes to render.
  Warnings appear when a length is longer than has been tested, when it means unloading the chat model
  while the clip renders, or when the render will take several minutes. Wan 2.2 stays at up to 5 seconds,
  the length it was trained on.
- **Video memory is planned for the clip's length.** Longer clips now get the extra memory they need set
  aside before rendering starts. A clip too big for your Mac is refused with a clear message instead of
  slowing everything down.
- **Choose where downloads are saved.** In the Mac app, downloading a video, an exported chat or an artifact
  now asks where to save it and what to call it, starting in your Downloads folder. The chime that played
  when a download finished, which sounded like an error, is gone.
- **The Mac app is downloaded ready-made** instead of being built on your Mac, so the install no longer
  depends on Apple's developer tools working. If the download isn't possible, it's built on your Mac as
  before.
- **The install finishes even if the Mac app can't be set up.** GoRunRun Local AI then starts in the
  background and opens in your browser, and the installer explains how to fix the problem and add the app
  later.
- **A clearer download progress bar.** It shows a percentage and how many GB are done, moves steadily
  instead of pausing, and fits the width of your Terminal window.
- **Stopped installs pick up where they left off.** If a first install was stopped part way, running it
  again treats it as a new install (so video creation is offered by default) rather than an update.

## 0.1.0 (2026-09-25)

The first public release.

- Chat with text, photos, screenshots, documents, audio and video, all running on your Mac.
- Talk mode: spoken conversations you can interrupt, with a lava-lamp that shows what it's doing.
- Create videos with sound (LTX-2.3) or sharper silent ones (Wan 2.2), and animate your photos.
- Private web search you can switch off, so nothing goes online at all.
- Use it from your phone over your own Tailscale network, with a QR code to sign in.
- A setup summary that shows what your Mac can do, with a check for updates in Settings.
