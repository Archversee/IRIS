import { useEffect, useState } from "react";

function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

/** The SOP page a rule came from: rendered page image (quote highlighted when
 *  the page has a text layer) or the extracted text (always highlighted). */
export default function SourceViewer({ page, quote, imageUrl, loadText, noImage = false }) {
  // noImage: the PDF itself isn't on this machine (imported rules) -- only the extracted text can be shown
  const [view, setView] = useState(noImage ? "text" : "image");
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setText(null); setErr(null);
    if (view !== "text" || !page) return;
    let off = false;
    loadText(page).then((p) => { if (!off) setText(p); }).catch((e) => { if (!off) setErr(e.message); });
    return () => { off = true; };
  }, [view, page, loadText]);

  if (!page) return null;

  let body = null;
  if (view === "image") {
    body = <div className="sop-page"><img alt={`SOP page ${page}`} src={imageUrl(page, quote || "")} /></div>;
  } else if (err) {
    body = <div className="err">{err}</div>;
  } else if (!text) {
    body = <div className="muted sop-small">Loading page…</div>;
  } else {
    // highlight the whole quoted passage; if the page's wording differs slightly
    // (line breaks, OCR), fall back to its first words up to the end of the sentence
    const all = (quote || "").trim().split(/\s+/).filter(Boolean);
    let parts = [text.text];
    if (all.length) {
      const full = new RegExp("(" + all.map(escRe).join("\\s+") + ")", "i");
      const head = new RegExp("(" + all.slice(0, 10).map(escRe).join("\\s+") + "[^]*?(?:\\.|$))", "i");
      parts = text.text.split(full);
      if (parts.length < 3) parts = text.text.split(head);
    }
    body = (
      <>
        {text.source === "ocr" && (
          <div className="sop-warn">Read with OCR{text.ocr_confidence ? ` (${Math.round(text.ocr_confidence)}% confidence)` : ""} — compare with the page image.</div>
        )}
        <pre className="sop-page-text">
          {parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>))}
        </pre>
      </>
    );
  }

  return (
    <div className="card sop-source">
      <div className="sop-source__head">
        <h3>SOP page {page}</h3>
        <div className="sop-seg" role="tablist">
          {!noImage && <button type="button" className={view === "image" ? "is-on" : ""} onClick={() => setView("image")}>Page image</button>}
          <button type="button" className={view === "text" ? "is-on" : ""} onClick={() => setView("text")}>Extracted text</button>
        </div>
      </div>
      {body}
    </div>
  );
}
