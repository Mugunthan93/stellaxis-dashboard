// Run in the built-in browser pane on https://www.instagram.com (logged in). Read-only GETs of one page's posts.
// Replace __PK__ with the page's numeric id (R/.pages.json), __DONE__ with the shortcodes already handled and
// __STATE__ with the saved {max_id, pages} (or null). It starts in the background and returns at once, because
// the pane's JS calls time out at 45 s. Read progress with JSON.stringify(window.__page).
// Instagram rate-limits bulk reads (a 429 on 2026-11-04 after ~30 saved-feed pages), so this is deliberately slow:
// one request every 20-30 s, 8 per call, and it stops for good on the first non-200. Never retry a 429 the same day.
const PK = '__PK__', DONE = new Set(__DONE__), STATE = __STATE__, BATCH = 8;
const H = { headers: { 'X-IG-App-ID': '936619743392459' }, credentials: 'include' };
const day = (t) => new Date(t * 1000).toISOString().slice(0, 10);
window.__page = { running: true, pk: PK, seen: 0, requests: 0, stopped: null, more: true, max_id: STATE?.max_id || '', items: [] };
(async () => {
  const P = window.__page;
  for (let i = 0; i < BATCH && P.more; i++) {
    const r = await fetch(`/api/v1/feed/user/${PK}/?count=12` + (P.max_id ? `&max_id=${encodeURIComponent(P.max_id)}` : ''), H);
    P.requests++;
    if (!r.ok) { P.stopped = `HTTP ${r.status}`; break; }
    const j = await r.json();
    for (const m of j.items || []) {
      P.seen++;
      if (!DONE.has(m.code)) P.items.push({ code: m.code, date: day(m.taken_at), reel: m.product_type === 'clips',
        plays: m.play_count || m.view_count || 0, cap: (m.caption?.text || '').replace(/\s+/g, ' ').slice(0, 160) });
    }
    P.more = !!j.more_available; P.max_id = j.next_max_id || '';
    await new Promise((res) => setTimeout(res, 20000 + Math.random() * 10000));
  }
  P.running = false;
})();
'started';
