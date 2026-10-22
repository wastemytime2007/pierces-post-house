// The project's real order of work, as navigation: shoot it, find stories, trim in Premiere, finish. Each step says where it stands, so the person always knows what comes next.
import { Fragment } from "react";
import Icon from "./Icon.jsx";

const STEPS = [
  { id: "pm", n: 1, name: "Footage" },
  { id: "ideas", n: 2, name: "Stories" },
  { id: "trim", n: 3, name: "Trim" },
  { id: "review", n: 4, name: "Finish" },
];

export default function Journey({ active, onSelect, status }) {
  return (
    <div className="journey" role="tablist" aria-label="Project steps">
      {STEPS.map((s, i) => {
        const st = status[s.id] || {};
        return (
          <Fragment key={s.id}>
            <button
              role="tab"
              aria-selected={active === s.id}
              className={`jstep ${active === s.id ? "on" : ""} ${st.done ? "done" : ""}`}
              onClick={() => onSelect(s.id)}
              disabled={st.disabled}
              title={st.disabled ? "Available once the interviews are transcribed" : undefined}
            >
              <span className="jdot">{st.done ? <Icon name="check" size={12} /> : s.n}</span>
              <span className="jtext">
                <span className="jname">{s.name}</span>
                {st.text && <span className="jsub">{st.text}</span>}
              </span>
            </button>
            {i < STEPS.length - 1 && <span className={`jline ${st.done ? "done" : ""}`} aria-hidden="true" />}
          </Fragment>
        );
      })}
    </div>
  );
}
