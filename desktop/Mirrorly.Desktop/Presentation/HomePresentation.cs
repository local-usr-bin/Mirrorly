using System.Globalization;
using System.Text.RegularExpressions;

namespace Mirrorly.Desktop.Presentation;

// Display-only records; none authorize or execute operations.
public enum HomeScenario { Healthy, DestinationUnavailable, Failed, CompletedWithIssues, FinalizationProblem, Running, Queued, TwoBackups, ThreeBackups, ManyBackups, Empty, LongPath }
public enum StatusTone { Success, Warning, Error, Working, Neutral }
public enum ShellPage { Home, Backups, Restore, Activity, Settings, BackupSetup, Diagnostics }
public record StatusPresentation(string Title, string Detail, string NextStep, string Action, StatusTone Tone, string Symbol, bool Busy = false);
public record BackupPresentation(string Id, string Name, string Source, string Destination, string LastBackup, string Status, StatusTone Tone, int Recency, string? SavedSnapshotPath = null, string LastBackupLabel = "Last backup:", DateTimeOffset? SavedVersionCreatedAt = null)
{
    public bool NeedsAttention => Tone is StatusTone.Warning or StatusTone.Error;
}
public record ActivityPresentation(string Title, string Backup, string When, StatusTone Tone = StatusTone.Success)
{
    public string Symbol => Tone switch { StatusTone.Warning => "⚠", StatusTone.Error => "⊗", _ => "✓" };
}
public record HomeFixture(StatusPresentation Status, IReadOnlyList<BackupPresentation> Backups, IReadOnlyList<ActivityPresentation> Activity);

public static class HomePolicy
{
    public const int RecentActivityLimit = 3;
    public const int ManyBackupPreviewLimit = 4;
    public const double StackedStatusBelow = 720;
    public const double CompactActivityBelow = 480;
    // Compact navigation preserves content room on medium windows; see PHASE1C calibration.
    public const double ExpandedNavigationAt = 1280;
    public const double CompactNavigationAt = 640;
    public static readonly bool DecorationsEnabled = true;
    public static bool StackStatus(double width) => width < StackedStatusBelow;
    // Saved-version time is a Home display hint, never Backup baseline selection.
    // Require an explicit offset so sorting cannot vary with the desktop time zone.
    public static DateTimeOffset? SavedVersionTime(string? value)
    {
        if (string.IsNullOrWhiteSpace(value) || !Regex.IsMatch(value, @"(?:Z|[+-]\d{2}:\d{2})$", RegexOptions.IgnoreCase) ||
            !DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsed)) return null;
        return parsed.ToUniversalTime();
    }
    public static IReadOnlyList<BackupPresentation> Preview(IReadOnlyList<BackupPresentation> backups) =>
        backups.OrderByDescending(b => b.NeedsAttention)
            .ThenByDescending(b => b.SavedVersionCreatedAt.HasValue)
            .ThenByDescending(b => b.SavedVersionCreatedAt)
            .ThenByDescending(b => b.Recency)
            .ThenBy(b => b.Id, StringComparer.Ordinal).Take(ManyBackupPreviewLimit).ToArray();
}

public static class HomeFixtures
{
    public static HomeFixture Create(HomeScenario scenario)
    {
        var status = scenario switch
        {
            HomeScenario.DestinationUnavailable => new("Backup needs attention", "The backup destination isn't available.", "Connect the drive, then try again.", "Try again", StatusTone.Warning, "⚠"),
            HomeScenario.Failed => new("Backup couldn't finish", "No new backup version was saved.", "View details before trying again. The cause hasn't been confirmed.", "Try again", StatusTone.Error, "⊗"),
            HomeScenario.CompletedWithIssues => new("Backup completed with issues", "A backup version was saved, but some items were skipped.", "Review the skipped items before relying on this version.", "View details", StatusTone.Warning, "⚠"),
            HomeScenario.FinalizationProblem => new("A backup version was saved", "Finalization encountered a problem after the version was saved.", "View details to check what still needs attention.", "View details", StatusTone.Warning, "⚠"),
            HomeScenario.Running => new("Backing up Documents…", "Preparing this backup.", "Progress is shown here when it's available.", "Backing up…", StatusTone.Working, "↻", true),
            HomeScenario.Queued => new("Documents is waiting", "This backup is in the queue.", "It will start after the current backup finishes.", "Waiting in queue", StatusTone.Neutral, "◷", true),
            HomeScenario.ManyBackups => new("Some backups need attention", "Two destinations aren't available.", "Connect the drives, then review the affected backups.", "View details", StatusTone.Warning, "⚠"),
            _ => new StatusPresentation("Last backup completed successfully", "Today, 14:32", "Your saved version is ready to browse.", "Back up now", StatusTone.Success, "✓")
        };
        var doc = new BackupPresentation("documents", "Documents", @"C:\Users\Liu\Documents",
            @"E:\Mirrorly\Documents\MirrorlyRepo", "Today, 14:32", "Completed successfully", StatusTone.Success, 20);
        if (scenario is HomeScenario.DestinationUnavailable or HomeScenario.Failed or HomeScenario.CompletedWithIssues or HomeScenario.FinalizationProblem or HomeScenario.Running or HomeScenario.Queued)
            doc = doc with { Status = scenario switch {
                HomeScenario.DestinationUnavailable => "Destination unavailable",
                HomeScenario.Failed => "No new version saved",
                HomeScenario.CompletedWithIssues => "Completed with issues",
                HomeScenario.FinalizationProblem => "Saved · needs attention",
                HomeScenario.Running => "Preparing backup",
                _ => "Waiting in queue"
            }, Tone = status.Tone, LastBackup = scenario is HomeScenario.CompletedWithIssues or HomeScenario.FinalizationProblem ? "Today, 14:32" : "Yesterday, 21:04" };
        if (scenario == HomeScenario.LongPath)
            doc = doc with {
                Source = @"C:\Users\Liu\Documents\Research\2026\International collaboration\Field observations and reference material\Very long folder name for layout verification",
                Destination = @"E:\Mirrorly\Documents\Research archive\International collaboration and supporting material\MirrorlyRepo"
            };
        var backups = new List<BackupPresentation> { doc };
        if (scenario is HomeScenario.TwoBackups or HomeScenario.ThreeBackups or HomeScenario.ManyBackups)
            backups.Add(new("photos", "Photos", @"D:\Photos", @"E:\Mirrorly\Photos\MirrorlyRepo", "Today, 12:10", "Completed successfully", StatusTone.Success, 19));
        if (scenario is HomeScenario.ThreeBackups or HomeScenario.ManyBackups)
            backups.Add(new("projects", "Projects", @"D:\Projects", @"F:\Mirrorly\Projects\MirrorlyRepo", "Yesterday, 18:20", "Completed successfully", StatusTone.Success, 18));
        if (scenario == HomeScenario.ManyBackups)
            for (var i = 4; i <= 20; i++)
                backups.Add(new($"backup-{i}", i == 4 ? "Research" : i == 5 ? "Family archive" : $"Archive {i}",
                    $@"D:\Archive {i}", $@"G:\Mirrorly\Archive {i}\MirrorlyRepo", "Yesterday, 10:30",
                    i <= 5 ? "Destination unavailable" : "Completed successfully",
                    i <= 5 ? StatusTone.Warning : StatusTone.Success, 20 - i));
        if (scenario == HomeScenario.Empty) backups.Clear();
        var latestActivity = scenario switch
        {
            HomeScenario.Failed => new ActivityPresentation("Backup failed · no new version saved", "Documents", "Today, 14:32", StatusTone.Error),
            HomeScenario.CompletedWithIssues => new("Backup completed with issues", "Documents", "Today, 14:32", StatusTone.Warning),
            HomeScenario.FinalizationProblem => new("Version saved · finalization needs attention", "Documents", "Today, 14:32", StatusTone.Warning),
            HomeScenario.DestinationUnavailable => new("Destination unavailable", "Documents", "Today, 14:32", StatusTone.Warning),
            HomeScenario.ManyBackups => new("Destination unavailable", "Research", "Today, 14:32", StatusTone.Warning),
            HomeScenario.Running or HomeScenario.Queued => new("Backup completed", "Documents", "Yesterday, 21:04"),
            _ => new("Backup completed", "Documents", "Today, 14:32")
        };
        var activity = scenario == HomeScenario.Empty ? Array.Empty<ActivityPresentation>() : new[] {
            latestActivity,
            new ActivityPresentation("Verification completed", "Documents", "Yesterday, 21:04"),
            new ActivityPresentation("Backup completed", "Documents", "Yesterday, 18:20")
        };
        return new(status, backups, activity);
    }
}
