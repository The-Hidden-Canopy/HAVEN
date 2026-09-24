app = "native/Haven.Desktop/App.xaml.cs"
src = open(app, encoding="utf-8").read()

old = """    public App()
    {
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        _window.Activate();
    }"""

new = """    private static string DbgPath => System.IO.Path.Combine(System.IO.Path.GetTempPath(), "haven-dbg.txt");

    public App()
    {
        System.IO.File.WriteAllText(DbgPath, "app ctor begin" + System.Environment.NewLine);
        try
        {
            InitializeComponent();
            System.IO.File.AppendAllText(DbgPath, "app initcomponent ok" + System.Environment.NewLine);
        }
        catch (System.Exception ex)
        {
            System.IO.File.AppendAllText(DbgPath, "app initcomponent FAIL: " + ex + System.Environment.NewLine);
            throw;
        }
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            System.IO.File.AppendAllText(DbgPath, "launched" + System.Environment.NewLine);
            _window = new MainWindow();
            _window.Activate();
            System.IO.File.AppendAllText(DbgPath, "activated" + System.Environment.NewLine);
        }
        catch (System.Exception ex)
        {
            System.IO.File.AppendAllText(DbgPath, "launch FAIL: " + ex + System.Environment.NewLine);
            throw;
        }
    }"""

assert src.count(old) == 1
open(app, "w", encoding="utf-8").write(src.replace(old, new))
print("app trap added")
