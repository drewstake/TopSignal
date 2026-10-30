if (-not ("TopSignalProcessJob" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class TopSignalProcessJob
{
    private const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const Int32 JobObjectExtendedLimitInformation = 9;

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        Int32 informationClass,
        ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION information,
        UInt32 informationLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr CreateKillOnClose()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject failed");

        JOBOBJECT_EXTENDED_LIMIT_INFORMATION information =
            new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        UInt32 length = (UInt32)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, ref information, length))
        {
            Int32 error = Marshal.GetLastWin32Error();
            CloseHandle(job);
            throw new Win32Exception(error, "SetInformationJobObject failed");
        }
        return job;
    }

    public static void Assign(IntPtr job, IntPtr process)
    {
        if (!AssignProcessToJobObject(job, process))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "AssignProcessToJobObject failed");
    }

    public static void Close(IntPtr job)
    {
        if (job != IntPtr.Zero)
            CloseHandle(job);
    }
}

'@
}

if (-not ('TopSignalSshProcess' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;

public static class TopSignalSshProcess
{
    public static Process Start(string executable, string arguments, string logPath)
    {
        var process = new Process();
        process.StartInfo = new ProcessStartInfo(executable, arguments) {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true
        };
        // Drain native stderr independently of the PowerShell runspace and keep
        // the same one-backup, 1 MiB bound even for a long-lived SSH connection.
        process.ErrorDataReceived += (sender, e) => {
            if (e.Data == null) return;
            try {
                if (File.Exists(logPath) && new FileInfo(logPath).Length > 1048576) {
                    if (File.Exists(logPath + ".previous")) File.Delete(logPath + ".previous");
                    File.Move(logPath, logPath + ".previous");
                }
                File.AppendAllText(logPath, DateTimeOffset.Now.ToString("o") + " " + e.Data + Environment.NewLine);
            } catch (IOException) { } catch (UnauthorizedAccessException) { }
        };
        try {
            process.Start();
            process.BeginErrorReadLine();
            return process;
        } catch {
            process.Dispose();
            throw;
        }
    }
}
'@
}


