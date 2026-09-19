using System.Reflection;

namespace Mirrorly.Desktop.Services;

public static class PrototypeConfiguration
{
    public const string PythonInterpreter = @"C:\Users\sakur\anaconda3\envs\mirrorly\python.exe";
    public static string WorkerScript => typeof(PrototypeConfiguration).Assembly
        .GetCustomAttributes<AssemblyMetadataAttribute>()
        .Single(a => a.Key == "Phase1AWorkerPath").Value!;
}
