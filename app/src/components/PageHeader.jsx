// What this page is for, in one plain sentence, and what comes after it. Every page of a project opens with one.
import Icon from "./Icon.jsx";

export default function PageHeader({ title, lead, next }) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      <p>{lead}</p>
      {next && <div className="page-next"><Icon name="chevronRight" size={13} />{next}</div>}
    </header>
  );
}
