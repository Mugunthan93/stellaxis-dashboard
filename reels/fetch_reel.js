// Run in the built-in browser pane on an open reel page (https://www.instagram.com/reel/<code>/). Reads the JSON the page
// already loaded and makes no request of its own. Returns one queue item for reel.py, or {code, err}.
// To list a page: open https://www.instagram.com/<user>/reels/, scroll to the bottom about every 7 s, and collect the
// /reel/<code>/ links. The grid carries no caption or video URL, so open each reel in turn, at least 10 s apart.
(() => {
  const code = location.pathname.split('/').filter(Boolean).pop();
  let found = null;
  const walk = (x, d) => {
    if (found || !x || typeof x !== 'object' || d > 40) return;
    if (Array.isArray(x)) { x.forEach((y) => walk(y, d + 1)); return; }
    if (x.code === code && (x.video_versions || x.image_versions2)) { found = x; return; }
    for (const k in x) if (x[k] && typeof x[k] === 'object') walk(x[k], d + 1);
  };
  document.querySelectorAll('script[type="application/json"]').forEach((s) => {
    if (!found && s.textContent.includes(code) && s.textContent.includes('video_versions')) try { walk(JSON.parse(s.textContent), 0); } catch (e) {}
  });
  if (!found) return JSON.stringify({ code, err: 'notfound' });
  const vv = [...(found.video_versions || [])].sort((a, b) => a.width - b.width);
  const c = [...(found.image_versions2?.candidates || [])].sort((a, b) => a.width - b.width);
  return JSON.stringify({ code, user: found.user?.username, taken: found.taken_at, caption: (found.caption?.text || '').slice(0, 1200),
    url: `https://www.instagram.com/reel/${code}/`, v: vv[0]?.url, thumb: (c.find((x) => x.width >= 480) || c[c.length - 1])?.url });
})();
