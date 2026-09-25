using System.Text.Json;
using System.Text;
using Haven.Desktop.Setup;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    // -- appearance (spec 40) ---------------------------------------------------

    private readonly ThemeService _themeService = new();
    private bool _appearanceReady;

    private void OnAppearanceChanged(object sender, RoutedEventArgs args)
    {
        if (_appearanceReady)
        {
            _ = ApplyAppearanceAsync();
        }
    }

    private void OnAppearanceSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (_appearanceReady)
        {
            _ = ApplyAppearanceAsync();
        }
    }

    private async Task ApplyAppearanceAsync()
    {
        var mode = ModeDarkRadio.IsChecked == true
            ? "dark"
            : ModeLightRadio.IsChecked == true ? "light" : "system";
        var theme = (ThemePicker.SelectedItem as ComboBoxItem)?.Tag as string ?? ThemeService.DefaultTheme;
        var density = (DensityPicker.SelectedItem as ComboBoxItem)?.Tag as string ?? ThemeService.DefaultDensity;
        var motion = (MotionPicker.SelectedItem as ComboBoxItem)?.Tag as string ?? ThemeService.DefaultMotion;
        _themeService.SetAppearance(theme, mode, density, motion);
        _themeService.Apply(this);
        ApplyDensityPreview();
        await _themeService.SaveAsync();
    }

    private void ApplyDensityPreview()
    {
        // The global density token pass lands in phase 4; the preview block
        // shows the compact row height live.
        AppearancePreviewNav.Height = _themeService.Density == "compact" ? 30 : 38;
    }

    private void SyncAppearanceControls()
    {
        ModeSystemRadio.IsChecked = _themeService.Mode == "system";
        ModeLightRadio.IsChecked = _themeService.Mode == "light";
        ModeDarkRadio.IsChecked = _themeService.Mode == "dark";
        SelectAppearanceItem(ThemePicker, _themeService.Theme);
        SelectAppearanceItem(DensityPicker, _themeService.Density);
        SelectAppearanceItem(MotionPicker, _themeService.Motion);
        ApplyDensityPreview();
        _appearanceReady = true;
    }

    private static void SelectAppearanceItem(ComboBox picker, string tag)
    {
        foreach (var item in picker.Items.OfType<ComboBoxItem>())
        {
            if (item.Tag as string == tag)
            {
                picker.SelectedItem = item;
                return;
            }
        }
    }

}
