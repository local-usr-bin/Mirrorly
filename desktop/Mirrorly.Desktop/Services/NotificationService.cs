using Microsoft.Windows.AppNotifications;
using Microsoft.Windows.AppNotifications.Builder;

namespace Mirrorly.Desktop.Services;

public sealed class NotificationService
{
    public string ShowTest()
    {
        var manager = AppNotificationManager.Default;
        var notification = new AppNotificationBuilder()
            .AddText("Mirrorly")
            .AddText("Technical prototype notification")
            .BuildNotification();
        manager.Show(notification);
        // An accepted notification is not proof that Windows displayed a banner.
        return $"Test notification submitted (ID {notification.Id}; setting: {manager.Setting}). Check Windows notifications.";
    }
}
