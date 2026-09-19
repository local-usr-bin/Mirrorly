using System.ComponentModel;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public sealed class HomeViewModel : INotifyPropertyChanged
{
    private WorkerStatus status = new(WorkerState.Disconnected, "Not started.");
    public string StatusText => $"Worker status: {status.State}";
    public string Detail => status.Detail;
    public bool CanRequest => status.State == WorkerState.Connected;
    public string LastEvent { get; private set; } = "No test event received.";
    public event PropertyChangedEventHandler? PropertyChanged;

    // Called by the view's UI dispatcher; this model has no WinUI dependency.
    public void UpdateStatus(WorkerStatus value)
    {
        status = value;
        PropertyChanged?.Invoke(this, new(nameof(StatusText)));
        PropertyChanged?.Invoke(this, new(nameof(Detail)));
        PropertyChanged?.Invoke(this, new(nameof(CanRequest)));
    }

    public void ShowEvent(string text)
    {
        LastEvent = text;
        PropertyChanged?.Invoke(this, new(nameof(LastEvent)));
    }
}
