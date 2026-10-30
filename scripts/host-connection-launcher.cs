using System;
using System.Diagnostics;
using System.IO;
using System.Text;

// Compiled as a Windows application: neither this process nor its child
// allocates a console, including when Windows Terminal is the default host.
internal static class HostConnectionLauncher
{
    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    private static int Main(string[] args)
    {
        try
        {
            var arguments = new StringBuilder();
            foreach (string arg in args) arguments.Append(Quote(arg)).Append(' ');
            string powershell = Path.Combine(Environment.GetFolderPath(
                Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
            using (var child = Process.Start(new ProcessStartInfo(powershell, arguments.ToString()) {
                UseShellExecute = false,
                CreateNoWindow = true
            }))
            {
                // Keep Task Scheduler's running state and exit-code recovery.
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            try
            {
                // Overwrite a single diagnostic, rather than growing a log.
                File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                    "host-connection-launcher.error.log"),
                    DateTimeOffset.Now.ToString("o") + " " + error.GetType().Name);
            }
            catch { }
            return 1;
        }
    }
}
