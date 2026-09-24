app = "native/Haven.Desktop/App.xaml.cs"
src = open(app, encoding="utf-8").read()

old = """        System.IO.File.WriteAllText(DbgPath, "app ctor begin" + System.Environment.NewLine);
        try
        {
            InitializeComponent();
            System.IO.File.AppendAllText(DbgPath, "app initcomponent ok" + System.Environment.NewLine);
        }"""
new = """        System.IO.File.WriteAllText(DbgPath, "app ctor begin" + System.Environment.NewLine);
        UnhandledException += (_, e) =>
        {
            System.IO.File.AppendAllText(DbgPath, "UNHANDLED: " + e.Exception + System.Environment.NewLine);
        };
        try
        {
            InitializeComponent();
            System.IO.File.AppendAllText(DbgPath, "app initcomponent ok" + System.Environment.NewLine);
        }"""
assert src.count(old) == 1
open(app, "w", encoding="utf-8").write(src.replace(old, new))
print("unhandled hook added")
