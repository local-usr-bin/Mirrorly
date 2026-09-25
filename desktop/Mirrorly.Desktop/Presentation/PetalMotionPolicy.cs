using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.Presentation;

// Session-only, presentation-only admission gate. Suppressed starts are consumed,
// so returning to Home or switching plant hosts cannot replay a stale flourish.
public sealed class PetalMotionPolicy
{
    private readonly HashSet<string> seenOperations = new(StringComparer.Ordinal);

    public bool ShouldPlay(WorkerAdmission admission, bool homePlantVisible, bool animationsEnabled, bool highContrast)
    {
        if (string.IsNullOrWhiteSpace(admission.RequestId) || string.IsNullOrWhiteSpace(admission.OperationId) ||
            !seenOperations.Add(admission.OperationId)) return false;
        return homePlantVisible && animationsEnabled && !highContrast;
    }
}
