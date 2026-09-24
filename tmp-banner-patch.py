import io, sys

path = "native/Haven.Desktop/MainWindow.xaml"
text = io.open(path, encoding="utf-8").read()

open_anchor = '        <ScrollViewer x:Name="ContentScroll" Grid.Row="1" Grid.Column="2" Padding="30,16,30,24">'
assert text.count(open_anchor) == 1, f"open anchor count={text.count(open_anchor)}"

banner = (
    '        <Grid Grid.Row="1" Grid.Column="2">\n'
    '            <Grid.RowDefinitions>\n'
    '                <RowDefinition Height="Auto" />\n'
    '                <RowDefinition Height="*" />\n'
    '            </Grid.RowDefinitions>\n'
    '            <Border x:Name="ConnectionBanner" Grid.Row="0" Visibility="Collapsed"\n'
    '                    Background="{ThemeResource HavenWarningTintBrush}"\n'
    '                    BorderBrush="{ThemeResource HavenWarningBrush}" BorderThickness="0,0,0,1"\n'
    '                    Padding="30,8">\n'
    '                <StackPanel Orientation="Horizontal" Spacing="12" VerticalAlignment="Center">\n'
    '                    <TextBlock x:Name="ConnectionBannerText" VerticalAlignment="Center"\n'
    '                               Style="{StaticResource HavenBodyTextStyle}"\n'
    '                               Foreground="{ThemeResource HavenTextBrush}"\n'
    '                               TextWrapping="Wrap" />\n'
    '                    <Button x:Name="ConnectionBannerRetry" Content="Retry now" Click="OnConnectionRetryClicked" />\n'
    '                </StackPanel>\n'
    '            </Border>\n'
    '            <ScrollViewer x:Name="ContentScroll" Grid.Row="1" Padding="30,16,30,24">'
)
text = text.replace(open_anchor, banner, 1)

close_anchor = '        </ScrollViewer>\n\n        <!-- Persistent composer (spec 10): centered pill, mic left, circular send. -->'
assert text.count(close_anchor) == 1, f"close anchor count={text.count(close_anchor)}"
text = text.replace(
    close_anchor,
    '        </ScrollViewer>\n        </Grid>\n\n        <!-- Persistent composer (spec 10): centered pill, mic left, circular send. -->',
    1,
)

io.open(path, "w", encoding="utf-8", newline="").write(text)
print("banner patch applied")
