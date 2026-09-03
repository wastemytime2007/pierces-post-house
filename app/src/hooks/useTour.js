import { useState, useCallback } from "react";

/**
 * useTour — minimal tour state machine.
 *
 * Given an ordered list of step names, tracks which step is currently
 * active (or null if the tour is dismissed). Exposes methods for
 * advancing, skipping, or resuming the tour.
 *
 * The tour is DISMISSED (returns null for current) when:
 *   - start() was never called, OR
 *   - all steps have been seen (auto-advances past the end), OR
 *   - skip() was called.
 *
 * Usage:
 *   const tour = useTour(["newProject", "settings", "recent"]);
 *   ...
 *   <button ref={newProjectRef}>New</button>
 *   {tour.isActive("newProject") && (
 *     <TourTooltip
 *       targetRef={newProjectRef}
 *       onDismiss={tour.next}
 *       onSkipAll={tour.skip}
 *       stepNumber={tour.stepIndex + 1}
 *       totalSteps={tour.totalSteps}
 *       ...
 *     />
 *   )}
 *
 * We intentionally don't persist tour state inside the hook — the
 * parent decides when to start() the tour (typically once, based on
 * the tour_seen flag in settings) and when to mark it as seen
 * (typically when skip() is called or when the tour auto-advances
 * past the last step).
 */

export function useTour(stepNames) {
  const [stepIndex, setStepIndex] = useState(-1); // -1 = not started

  const start = useCallback(() => {
    if (stepNames.length > 0) setStepIndex(0);
  }, [stepNames.length]);

  const next = useCallback(() => {
    setStepIndex((prev) => {
      const nextIdx = prev + 1;
      // Past the last step → tour is complete, return -1 to close
      if (nextIdx >= stepNames.length) return -1;
      return nextIdx;
    });
  }, [stepNames.length]);

  const skip = useCallback(() => {
    setStepIndex(-1);
  }, []);

  const isActive = useCallback(
    (name) => stepIndex >= 0 && stepNames[stepIndex] === name,
    [stepIndex, stepNames]
  );

  const currentStep = stepIndex >= 0 ? stepNames[stepIndex] : null;

  return {
    start,
    next,
    skip,
    isActive,
    stepIndex,
    currentStep,
    totalSteps: stepNames.length,
    isDone: stepIndex === -1 && currentStep === null,
  };
}
