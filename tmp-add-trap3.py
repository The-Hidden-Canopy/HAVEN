app = "native/Haven.Desktop/App.xaml.cs"
src = open(app, encoding="utf-8").read()
old = """    public App()
    {
        InitializeComponent();
        MergeDefaultTheme();
    }"""
new = """    private static string DbgPath => System.IO.Path.Combine(System.IO.Path.GetTempPath(), "haven-dbg.txt");

    public App()
    {
        UnhandledException += (_, e) =>
        {
            System.IO.File.AppendAllText(DbgPath, "UNHANDLED: " + e.Exception + System.Environment.NewLine);
        };
        InitializeComponent();
        MergeDefaultTheme();
    }"""
assert src.count(old) == 1
open(app, "w", encoding="utf-8").write(src.replace(old, new))
print("trap re-added")
