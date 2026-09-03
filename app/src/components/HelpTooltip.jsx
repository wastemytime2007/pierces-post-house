/**
 * HelpTooltip
 * -----------
 * Small circular '?' icon that shows a tip on hover or focus. Used next
 * to section titles throughout the app to explain what each section is
 * without cluttering the always-visible UI.
 *
 * Two ways to surface the tip:
 *   1. Hover the '?' → tooltip appears (CSS :hover)
 *   2. Keyboard focus via Tab → tooltip appears (CSS :focus-within)
 *      This is important for a11y — hover-only is unusable with a
 *      keyboard or screen reader.
 *
 * The tooltip position is fixed at `top: 100%; left: 0` in CSS, meaning
 * it renders below+to-the-right of the icon. This is simple but can
 * clip on right-side-of-page icons. For those, pass `align="right"` to
 * flip the horizontal anchor.
 *
 * No external positioning library — pure CSS, no runtime cost beyond
 * a state-less component.
 *
 * Props
 *   children — the tip content (string or JSX)
 *   align    — "left" (default) or "right". Controls which side of the
 *              '?' icon the tooltip opens from.
 */
export default function HelpTooltip({ children, align = "left" }) {
  return (
    <span className={`help-tooltip-wrap help-tooltip-${align}`}>
      <button
        type="button"
        className="help-tooltip-trigger"
        aria-label="Help"
        // The trigger is a button so it's keyboard-focusable. We don't
        // need an onClick handler — CSS :hover and :focus-within on the
        // wrapping span control visibility. Pressing Enter does
        // nothing, but tabbing to it shows the tip.
        onClick={(e) => e.preventDefault()}
      >
        ?
      </button>
      <span className="help-tooltip-content" role="tooltip">
        {children}
      </span>
    </span>
  );
}
