import { useEffect, useState, useRef } from "react";

/**
 * TourTooltip
 * -----------
 * A small card that positions itself relative to a target DOM element
 * and points at it with a CSS arrow. Used by the first-launch tour to
 * highlight key UI affordances without spotlighting or dimming the
 * rest of the screen.
 *
 * Why not a full-screen tour library (intro.js, shepherd.js)?
 *   - Those weigh in at 20-60KB each and embed their own styling
 *     system that fights our existing CSS vars.
 *   - They over-engineer for linearity (required Next/Back flow)
 *     when we want each tooltip to be independently dismissable.
 *   - The positioning math here is ~30 lines — not worth a dep.
 *
 * Caveats of this simple approach:
 *   - No collision detection: tooltips near the viewport edge can
 *     render slightly off-screen. If that happens, pick a different
 *     `side` in the parent.
 *   - No resize handling: tooltip reads the target's position once
 *     on mount. If the user resizes the window while the tooltip is
 *     open it can end up misaligned. Good enough for the first-launch
 *     flow where users aren't resizing.
 *
 * Props:
 *   targetRef    — React ref to the DOM element this tooltip points at.
 *   side         — "top" | "bottom" | "left" | "right". Which side of
 *                  the target the tooltip sits on.
 *   title        — heading text
 *   body         — paragraph text (plain string)
 *   onDismiss    — called when user clicks the X or the "Got it" button
 *   onSkipAll    — optional: called when user clicks "Skip tour"
 *   stepNumber   — e.g. 1 of 3 for the progress label (optional)
 *   totalSteps   — total count for the progress label (optional)
 */

export default function TourTooltip({
  targetRef,
  side = "bottom",
  title,
  body,
  onDismiss,
  onSkipAll,
  stepNumber,
  totalSteps,
}) {
  const [position, setPosition] = useState(null);
  const tooltipRef = useRef(null);

  // Compute position when the tooltip mounts. We also wait one frame
  // after mount so the tooltip's own dimensions are measurable (needed
  // for centering it against the target).
  useEffect(() => {
    const compute = () => {
      const target = targetRef?.current;
      const tooltip = tooltipRef.current;
      if (!target || !tooltip) return;

      const tRect = target.getBoundingClientRect();
      const ttRect = tooltip.getBoundingClientRect();
      const GAP = 12; // pixels between target and tooltip

      let top, left;
      switch (side) {
        case "top":
          top = tRect.top - ttRect.height - GAP;
          left = tRect.left + tRect.width / 2 - ttRect.width / 2;
          break;
        case "bottom":
          top = tRect.bottom + GAP;
          left = tRect.left + tRect.width / 2 - ttRect.width / 2;
          break;
        case "left":
          top = tRect.top + tRect.height / 2 - ttRect.height / 2;
          left = tRect.left - ttRect.width - GAP;
          break;
        case "right":
        default:
          top = tRect.top + tRect.height / 2 - ttRect.height / 2;
          left = tRect.right + GAP;
          break;
      }

      // Clamp to viewport so the tooltip never renders offscreen.
      // 8px margin from any edge.
      const MARGIN = 8;
      const maxLeft = window.innerWidth - ttRect.width - MARGIN;
      const maxTop = window.innerHeight - ttRect.height - MARGIN;
      left = Math.max(MARGIN, Math.min(left, maxLeft));
      top = Math.max(MARGIN, Math.min(top, maxTop));

      setPosition({ top, left });
    };

    // requestAnimationFrame so the tooltip has rendered and has
    // measurable dimensions before we position it.
    const raf = requestAnimationFrame(compute);

    // Re-compute on window resize so a user who resizes doesn't
    // see the tooltip drift forever. Cheap, single listener.
    window.addEventListener("resize", compute);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", compute);
    };
  }, [targetRef, side]);

  return (
    <div
      ref={tooltipRef}
      className={`tour-tooltip tour-tooltip-${side}`}
      style={position
        ? { top: position.top, left: position.left, visibility: "visible" }
        // Start hidden so we don't flash at (0,0) before positioning
        : { visibility: "hidden" }
      }
      role="tooltip"
    >
      <button
        className="tour-tooltip-close"
        onClick={onDismiss}
        aria-label="Dismiss"
      >
        ×
      </button>

      {(stepNumber && totalSteps) ? (
        <div className="tour-tooltip-step">
          {stepNumber} of {totalSteps}
        </div>
      ) : null}

      <div className="tour-tooltip-title">{title}</div>
      <div className="tour-tooltip-body">{body}</div>

      <div className="tour-tooltip-actions">
        {onSkipAll && (
          <button
            className="tour-tooltip-skip"
            onClick={onSkipAll}
          >
            Skip tour
          </button>
        )}
        <button
          className="tour-tooltip-ok"
          onClick={onDismiss}
        >
          Got it
        </button>
      </div>
    </div>
  );
}
