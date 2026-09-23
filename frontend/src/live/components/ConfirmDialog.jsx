// Styled in-app confirm, replacing window.confirm()'s native OS popup.
export default function ConfirmDialog({ title, message, confirmLabel = "Confirm", onConfirm, onCancel }) {
  return (
    <div className="mavis-confirm-overlay" onClick={onCancel}>
      <div className="mavis-confirm-card" onClick={(e) => e.stopPropagation()}>
        {title && <h3>{title}</h3>}
        <p className="muted">{message}</p>
        <div className="mavis-confirm-btns">
          <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
          <button type="button" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
