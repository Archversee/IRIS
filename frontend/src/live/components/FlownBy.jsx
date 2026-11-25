import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import { FLOWN_BY_KEY, useAuth } from "../../auth/AuthContext.jsx";

// Who is flying -- asked on the start menus, because it decides whose flight it
// is: the name MAVIS uses, the AI debrief, the pilot on the Save Session page,
// and who can open the review. A trainee always flies as themselves. An
// instructor picks one of their trainees (an administrator: any trainee), or
// themselves; nothing can be started until they have.
const ME = "me";
const nameOf = (u) => u.full_name || u.username;

// -> {pilot, ...props for <FlownBy>}; pilot is null until somebody is chosen
export function useFlownBy() {
  const { user } = useAuth();
  const isTrainee = user.role === "trainee";
  const [trainees, setTrainees] = useState(null);
  const [failed, setFailed] = useState(false);
  // remembered in this tab until they log out, so one trainee's lessons don't
  // each have to ask again
  const key = FLOWN_BY_KEY + user.id;
  const [value, setValue] = useState(() => {
    try { return sessionStorage.getItem(key) || ""; } catch { return ""; }
  });

  useEffect(() => {
    if (isTrainee) return;
    api.listPilots().then(setTrainees).catch(() => { setTrainees([]); setFailed(true); });
  }, [isTrainee]);

  const choose = (v) => {
    setValue(v);
    try { sessionStorage.setItem(key, v); } catch { /* storage unavailable -- asked again next time */ }
  };
  const pilot = isTrainee || value === ME ? user : (trainees || []).find((t) => t.id === value) || null;
  return { pilot, isTrainee, trainees, failed, value: pilot ? value : "", choose, user };
}

export default function FlownBy({ pilot, isTrainee, trainees, failed, value, choose, user, disabled }) {
  if (isTrainee) {
    return (
      <div className="flown-by">
        <span className="flown-by__label">Pilot</span>
        <b>{nameOf(user)}</b>
      </div>
    );
  }
  return (
    <div className={"flown-by" + (pilot ? "" : " is-needed")}>
      <label className="flown-by__label" htmlFor="flown-by">Who is flying?</label>
      <select id="flown-by" value={value} disabled={disabled || !trainees} onChange={(e) => choose(e.target.value)}>
        <option value="">{trainees ? "Select the pilot" : "Loading…"}</option>
        {trainees?.length > 0 && (
          <optgroup label={user.role === "admin" ? "Trainees" : "Your trainees"}>
            {trainees.map((t) => <option key={t.id} value={t.id}>{nameOf(t)}</option>)}
          </optgroup>
        )}
        <option value={ME}>{nameOf(user)} (me)</option>
      </select>
      {!pilot && trainees && (
        <span className="flown-by__hint">
          {failed ? "Couldn't load your trainees."
            : trainees.length ? "Select the pilot to start."
              : user.role === "admin" ? "There are no trainee accounts yet."
                : "No trainees are assigned to you yet. An administrator can assign them in Accounts."}
        </span>
      )}
    </div>
  );
}
