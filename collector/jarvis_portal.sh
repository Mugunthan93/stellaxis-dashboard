#!/bin/sh
# Open the voice-only Jarvis portal full-screen in its own Chrome window, with no click needed to start.
# Its own profile (~/.stellaxis/portal-chrome) keeps the microphone permission you give it the first time,
# and the autoplay rule lets the page start listening by itself. Quit it with Cmd+Q.
exec open -na "Google Chrome" --args \
  --user-data-dir="$HOME/.stellaxis/portal-chrome" \
  --autoplay-policy=no-user-gesture-required \
  --no-first-run --no-default-browser-check \
  --kiosk --app=http://127.0.0.1:7777/
