// Run in the built-in browser pane on https://www.instagram.com (logged in). Read-only GETs of the Saved feed.
// Two modes, picked by the placeholders:
//   LIST  — replace __WANT__ with null. Returns the unprocessed saved posts in compact form
//           {seen, more, items:[{code,user,date,cap}]} (cap = first 160 chars) so you can pick the AI ones.
//   MEDIA — replace __WANT__ with a JSON array of shortcodes. Returns full items for exactly those codes
//           (caption, one combined audio+video URL `v`, cover `thumb`, images for photo posts) and also keeps
//           the JSON in window.__q; read it in 10000-character slices with window.__q.slice(i, i + 10000).
// Always replace __PROCESSED__ with the JSON array of shortcodes already done (ok / caption-only / skipped).
const DONE = new Set(__PROCESSED__), WANT = __WANT__ ? new Set(__WANT__) : null, PAGES = 10, LIST_MAX = 60;
const H = { headers: { 'X-IG-App-ID': '936619743392459' }, credentials: 'include' };
const day = (t) => new Date(t * 1000).toISOString().slice(0, 10);
const full = (m) => {
  const vv = [...(m.video_versions || [])].sort((a, b) => a.width - b.width);  // progressive mp4: audio + video
  const c = [...((m.image_versions2 || m.carousel_media?.[0]?.image_versions2)?.candidates || [])].sort((a, b) => a.width - b.width);
  const o = { code: m.code, user: m.user?.username, taken: m.taken_at, dur: m.video_duration ? Math.round(m.video_duration) : null,
    caption: m.caption?.text || '', url: `https://www.instagram.com/${m.product_type === 'clips' ? 'reel' : 'p'}/${m.code}/`,
    v: vv[0]?.url, thumb: (c.find((x) => x.width >= 480) || c[c.length - 1])?.url };
  if (!o.v) o.imgs = (m.carousel_media || [m]).map((x) => x.image_versions2?.candidates?.[0]?.url).filter(Boolean).slice(0, 10);
  return o;
};
let out = [], seen = 0, max_id = '', more = true;
for (let p = 0; p < PAGES && more; p++) {
  const r = await fetch('/api/v1/feed/saved/posts/' + (max_id ? `?max_id=${encodeURIComponent(max_id)}` : ''), H);
  if (!r.ok) { out = [{ error: `HTTP ${r.status}` }]; break; }
  const j = await r.json();
  for (const it of j.items || []) {
    const m = it.media; seen++;
    if (WANT ? WANT.has(m.code) : !DONE.has(m.code))
      out.push(WANT ? full(m) : { code: m.code, user: m.user?.username, date: day(m.taken_at), cap: (m.caption?.text || '').replace(/\s+/g, ' ').slice(0, 160) });
  }
  more = j.more_available; max_id = j.next_max_id;
  if (WANT ? out.length >= WANT.size : out.length >= LIST_MAX) break;
  await new Promise((res) => setTimeout(res, 1200));
}
if (WANT) { window.__q = JSON.stringify(out); JSON.stringify({ seen, found: out.length, bytes: window.__q.length, codes: out.map((o) => o.code) }); }
else JSON.stringify({ seen, more, items: out });
