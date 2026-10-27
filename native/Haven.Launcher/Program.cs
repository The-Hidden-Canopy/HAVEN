using System.Diagnostics;
using System.Runtime.InteropServices;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        var root = AppContext.BaseDirectory;
        var python = Path.Combine(root, "runtime", "pythonw.exe");
        var native = Path.Combine(root, "native", "Haven.Desktop.exe");
        if (!File.Exists(python) || !File.Exists(native))
            return Fail("[HAVEN.CORE.STARTUP_FAILED] The packaged runtime or native client is missing. Restore the verified package.");
        try
        {
            var start = new ProcessStartInfo(python)
            {
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            // Isolate Python from user packages, PYTHONPATH and stale caches.
            foreach (var arg in new[] { "-I", "-S", "-B", "-c",
                "import runpy,sys,os; root=sys.argv.pop(1); sys.path.insert(0,root); sys.path.append(os.path.join(root,'runtime','packages')); runpy.run_module('haven.desktop',run_name='__main__')", root })
                start.ArgumentList.Add(arg);
            foreach (var arg in args) start.ArgumentList.Add(arg);
            start.ArgumentList.Add("--native-path");
            start.ArgumentList.Add(native);
            start.Environment["HAVEN_NATIVE_CLIENT"] = native;
            start.Environment["HAVEN_SKIP_NATIVE_BUILD"] = "1";
            start.Environment.Remove("PYTHONPATH");
            start.Environment.Remove("PYTHONHOME");
            using var process = Process.Start(start);
            if (process is null) return Fail("[HAVEN.CORE.STARTUP_FAILED] Core could not be started.");
            process.WaitForExit();
            return process.ExitCode;
        }
        catch
        {
            return Fail("[HAVEN.CORE.STARTUP_FAILED] Core could not be started. Inspect the selected installation's private diagnostics.");
        }
    }

    private static int Fail(string message)
    {
        _ = MessageBoxW(IntPtr.Zero, message, "HAVEN", 0x10);
        return 2;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr owner, string text, string caption, uint type);
}
