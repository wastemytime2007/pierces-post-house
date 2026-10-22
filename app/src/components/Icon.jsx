// One line-icon set, one stroke weight (SF Symbols style). Used where an icon labels an action or a kind of thing, never as decoration.
const PATHS = {
  film: <><rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="M7.5 5v14M16.5 5v14M3 9.5h4.5M3 14.5h4.5M16.5 9.5H21M16.5 14.5H21" /></>,
  sparkles: <><path d="M10.5 3.5l1.6 4.4 4.4 1.6-4.4 1.6-1.6 4.4-1.6-4.4L4.5 9.5l4.4-1.6 1.6-4.4z" /><path d="M18 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8L18 14z" /></>,
  folder: <path d="M3 8a2.5 2.5 0 012.5-2.5H9l2 2.5h7.5A2.5 2.5 0 0121 10.5v6A2.5 2.5 0 0118.5 19h-13A2.5 2.5 0 013 16.5V8z" />,
  plus: <path d="M12 5v14M5 12h14" />,
  captions: <><rect x="3" y="5" width="18" height="14" rx="3" /><path d="M7 10.5h3.5M7 14h6M14 10.5h3M16 14h1" /></>,
  music: <><path d="M9 17.5V6l10-2v11.5" /><circle cx="6.5" cy="17.5" r="2.5" /><circle cx="16.5" cy="15.5" r="2.5" /></>,
  graphics: <><rect x="4" y="4" width="16" height="16" rx="3.5" /><path d="M8.5 9h7M12 9v7" /></>,
  sfx: <><path d="M4 10v4h3.5L12 18V6L7.5 10H4z" /><path d="M15.5 9a4 4 0 010 6M18 6.5a7.5 7.5 0 010 11" /></>,
  bleep: <path d="M4 10v4M8 6.5v11M12 9v6M16 4.5v15M20 9.5v5" />,
  cut: <><circle cx="6" cy="7" r="2.5" /><circle cx="6" cy="17" r="2.5" /><path d="M8 8.6L20 18M8 15.4L20 6" /></>,
  transition: <><rect x="3" y="6" width="12" height="12" rx="2.5" /><path d="M9 6h9.5A2.5 2.5 0 0121 8.5v7a2.5 2.5 0 01-2.5 2.5H15" /></>,
  pause: <><rect x="7" y="5" width="3.5" height="14" rx="1" /><rect x="13.5" y="5" width="3.5" height="14" rx="1" /></>,
  play: <path d="M8 5.5v13l11-6.5-11-6.5z" />,
  chevronRight: <path d="M9.5 5l7 7-7 7" />,
  chevronLeft: <path d="M14.5 5l-7 7 7 7" />,
  ellipsis: <><circle cx="5.5" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="18.5" cy="12" r="1.2" /></>,
  refresh: <path d="M20 11a8 8 0 10-2.3 6.3M20 6v5h-5" />,
  arrowRight: <path d="M5 12h14M13 6l6 6-6 6" />,
  arrowUpRight: <path d="M7 17L17 7M9 7h8v8" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  mic: <><rect x="9" y="3.5" width="6" height="11" rx="3" /><path d="M5.5 11.5a6.5 6.5 0 0013 0M12 18v3" /></>,
  image: <><rect x="3" y="5" width="18" height="14" rx="2.5" /><circle cx="9" cy="10" r="1.6" /><path d="M4 17l5-4.5 3.5 3L16 12l4 4" /></>,
  doc: <><path d="M7 3.5h7l4 4v13H7z" /><path d="M14 3.5v4h4M10 12h5M10 15.5h5" /></>,
  sliders: <><path d="M4 8h9M17 8h3M4 16h3M11 16h9" /><circle cx="15" cy="8" r="2" /><circle cx="9" cy="16" r="2" /></>,
  activity: <path d="M3 12h4l2.5-6 4 12 2.5-6H21" />,
  trash: <path d="M5 7h14M10 7V5h4v2M7 7l.8 12h8.4L17 7M10.5 10.5v6M13.5 10.5v6" />,
  list: <path d="M8 6.5h12M8 12h12M8 17.5h12M4 6.5h.01M4 12h.01M4 17.5h.01" />,
  folderOpen: <path d="M3 8a2.5 2.5 0 012.5-2.5H9l2 2.5h7.5A2.5 2.5 0 0121 10.5V11M3.5 17.5l2-6.5h16l-2 6.5a2 2 0 01-1.9 1.5H5.5a2 2 0 01-2-1.5z" />,
};

export default function Icon({ name, size = 16, className = "", title }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : "true"}>
      {PATHS[name] || null}
    </svg>
  );
}
