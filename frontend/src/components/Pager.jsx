// Shared page navigation ("1–10 of 42   ‹ Prev 1 … 4 5 [6] 7 8 … 20 Next ›"),
// used by the Leaderboard and Sessions lists. Styles: .mg-pager in styles.css.

// 1 … 4 5 [6] 7 8 … 20
function pageList(page, pages) {
  const want = new Set([1, pages, page - 2, page - 1, page, page + 1, page + 2]);
  const out = [];
  for (let p = 1; p <= pages; p++) {
    if (!want.has(p)) continue;
    if (out.length && p - out[out.length - 1] > 1) out.push("…");
    out.push(p);
  }
  return out;
}

// page is 1-based; shown = rows on the current page; renders nothing when total is 0.
export default function Pager({ page, pageSize, total, shown, onPage, label = "Pages" }) {
  if (!total) return null;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = (page - 1) * pageSize + 1;
  return (
    <nav className="mg-pager" aria-label={label}>
      <span className="mg-pager__info">{first}–{first + shown - 1} of {total}</span>
      <div className="mg-pager__btns">
        <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
        {pageList(page, pages).map((p, i) => p === "…"
          ? <span key={`gap${i}`} className="mg-pager__gap">…</span>
          : <button key={p} type="button" className={p === page ? "is-on" : ""} onClick={() => onPage(p)}>{p}</button>)}
        <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next ›</button>
      </div>
    </nav>
  );
}
