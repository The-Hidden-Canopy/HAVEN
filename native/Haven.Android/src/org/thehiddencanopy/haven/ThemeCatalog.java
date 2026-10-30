package org.thehiddencanopy.haven;

// Generated from HAVEN Desktop ThemeService and Themes/*.xaml; do not edit by hand.
final class ThemeCatalog {
    static final String[] IDS = {"haven", "canopy", "ember", "mono"};
    static final String[] NAMES = {"Haven", "Canopy", "Ember", "Mono"};
    static final String[] MODES = {"system", "light", "dark"};
    static boolean validTheme(String value) { for (String id : IDS) if (id.equals(value)) return true; return false; }
    static String color(String theme, boolean dark, String token) {
        String palette = theme + (dark ? ".dark" : ".light");
        switch (palette) {
            case "haven.light":
                switch (token) {
                    case "HavenAccentColor": return "#2563EB";
                    case "HavenAccentHoverColor": return "#1D4FD7";
                    case "HavenAccentTintColor": return "#332563EB";
                    case "HavenCanvasColor": return "#F5F7FB";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#992563EB";
                    case "HavenHoverColor": return "#14000000";
                    case "HavenMutedTextColor": return "#5C6678";
                    case "HavenOrangeColor": return "#EA580C";
                    case "HavenOrangeTintColor": return "#33EA580C";
                    case "HavenPermissionGlowColor": return "#667C3AED";
                    case "HavenStrokeColor": return "#D8DFEA";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#FFFFFF";
                    case "HavenSurface2Color": return "#F0F3F8";
                    case "HavenTextColor": return "#172033";
                    case "HavenVioletColor": return "#7C3AED";
                    case "HavenVioletTintColor": return "#337C3AED";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D9141033";
                    case "HeroOverlayTintColorFade": return "#40141033";
                    case "Series1Color": return "#3B82F6";
                    case "Series2Color": return "#EA580C";
                    case "Series3Color": return "#0284C7";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#65A30D";
                    case "Series7Color": return "#8B5CF6";
                    case "Series8Color": return "#DC2626";
                    default: return null;
                }
            case "haven.dark":
                switch (token) {
                    case "HavenAccentColor": return "#3B82F6";
                    case "HavenAccentHoverColor": return "#5B96F5";
                    case "HavenAccentTintColor": return "#333B82F6";
                    case "HavenCanvasColor": return "#0B1220";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#993B82F6";
                    case "HavenHoverColor": return "#22FFFFFF";
                    case "HavenMutedTextColor": return "#8E9BB4";
                    case "HavenOrangeColor": return "#FF8A3D";
                    case "HavenOrangeTintColor": return "#33FF8A3D";
                    case "HavenPermissionGlowColor": return "#408B5CF6";
                    case "HavenStrokeColor": return "#27354F";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#111A2C";
                    case "HavenSurface2Color": return "#172238";
                    case "HavenTextColor": return "#F3F6FC";
                    case "HavenVioletColor": return "#8B5CF6";
                    case "HavenVioletTintColor": return "#338B5CF6";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D9141033";
                    case "HeroOverlayTintColorFade": return "#40141033";
                    case "Series1Color": return "#3B82F6";
                    case "Series2Color": return "#EA580C";
                    case "Series3Color": return "#0284C7";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#65A30D";
                    case "Series7Color": return "#8B5CF6";
                    case "Series8Color": return "#DC2626";
                    default: return null;
                }
            case "canopy.light":
                switch (token) {
                    case "HavenAccentColor": return "#0D9488";
                    case "HavenAccentHoverColor": return "#0F766E";
                    case "HavenAccentTintColor": return "#330D9488";
                    case "HavenCanvasColor": return "#F2FAF7";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#990D9488";
                    case "HavenHoverColor": return "#14000000";
                    case "HavenMutedTextColor": return "#51776D";
                    case "HavenOrangeColor": return "#EA580C";
                    case "HavenOrangeTintColor": return "#33EA580C";
                    case "HavenPermissionGlowColor": return "#667C3AED";
                    case "HavenStrokeColor": return "#C9E0D7";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#FFFFFF";
                    case "HavenSurface2Color": return "#E7F3EE";
                    case "HavenTextColor": return "#12312B";
                    case "HavenVioletColor": return "#3B82F6";
                    case "HavenVioletTintColor": return "#333B82F6";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D90E2420";
                    case "HeroOverlayTintColorFade": return "#400E2420";
                    case "Series1Color": return "#0D9488";
                    case "Series2Color": return "#EA580C";
                    case "Series3Color": return "#0284C7";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#16A34A";
                    case "Series7Color": return "#3B82F6";
                    case "Series8Color": return "#F43F5E";
                    default: return null;
                }
            case "canopy.dark":
                switch (token) {
                    case "HavenAccentColor": return "#2DD4BF";
                    case "HavenAccentHoverColor": return "#5EE0D1";
                    case "HavenAccentTintColor": return "#332DD4BF";
                    case "HavenCanvasColor": return "#081716";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#992DD4BF";
                    case "HavenHoverColor": return "#22FFFFFF";
                    case "HavenMutedTextColor": return "#84A89F";
                    case "HavenOrangeColor": return "#FB923C";
                    case "HavenOrangeTintColor": return "#33FB923C";
                    case "HavenPermissionGlowColor": return "#408B5CF6";
                    case "HavenStrokeColor": return "#294A43";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#102421";
                    case "HavenSurface2Color": return "#17302C";
                    case "HavenTextColor": return "#EFFCF8";
                    case "HavenVioletColor": return "#60A5FA";
                    case "HavenVioletTintColor": return "#3360A5FA";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D90E2420";
                    case "HeroOverlayTintColorFade": return "#400E2420";
                    case "Series1Color": return "#0D9488";
                    case "Series2Color": return "#EA580C";
                    case "Series3Color": return "#0284C7";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#16A34A";
                    case "Series7Color": return "#3B82F6";
                    case "Series8Color": return "#F43F5E";
                    default: return null;
                }
            case "ember.light":
                switch (token) {
                    case "HavenAccentColor": return "#EA580C";
                    case "HavenAccentHoverColor": return "#C74A0A";
                    case "HavenAccentTintColor": return "#33EA580C";
                    case "HavenCanvasColor": return "#FBF7F4";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#99EA580C";
                    case "HavenHoverColor": return "#14000000";
                    case "HavenMutedTextColor": return "#75655F";
                    case "HavenOrangeColor": return "#EA580C";
                    case "HavenOrangeTintColor": return "#33EA580C";
                    case "HavenPermissionGlowColor": return "#667C3AED";
                    case "HavenStrokeColor": return "#E7D7CC";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#FFFFFF";
                    case "HavenSurface2Color": return "#F5EDE8";
                    case "HavenTextColor": return "#2B211D";
                    case "HavenVioletColor": return "#8B5CF6";
                    case "HavenVioletTintColor": return "#338B5CF6";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D9201A16";
                    case "HeroOverlayTintColorFade": return "#40201A16";
                    case "Series1Color": return "#EA580C";
                    case "Series2Color": return "#6D28D9";
                    case "Series3Color": return "#0891B2";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#4D7C0F";
                    case "Series7Color": return "#F43F5E";
                    case "Series8Color": return "#0D9488";
                    default: return null;
                }
            case "ember.dark":
                switch (token) {
                    case "HavenAccentColor": return "#F97316";
                    case "HavenAccentHoverColor": return "#FB8B3D";
                    case "HavenAccentTintColor": return "#33F97316";
                    case "HavenCanvasColor": return "#121114";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#99F97316";
                    case "HavenHoverColor": return "#22FFFFFF";
                    case "HavenMutedTextColor": return "#A2959C";
                    case "HavenOrangeColor": return "#FB923C";
                    case "HavenOrangeTintColor": return "#33FB923C";
                    case "HavenPermissionGlowColor": return "#408B5CF6";
                    case "HavenStrokeColor": return "#40353B";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#1A181C";
                    case "HavenSurface2Color": return "#242026";
                    case "HavenTextColor": return "#FAF7F5";
                    case "HavenVioletColor": return "#A78BFA";
                    case "HavenVioletTintColor": return "#33A78BFA";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D9201A16";
                    case "HeroOverlayTintColorFade": return "#40201A16";
                    case "Series1Color": return "#EA580C";
                    case "Series2Color": return "#6D28D9";
                    case "Series3Color": return "#0891B2";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#4D7C0F";
                    case "Series7Color": return "#F43F5E";
                    case "Series8Color": return "#0D9488";
                    default: return null;
                }
            case "mono.light":
                switch (token) {
                    case "HavenAccentColor": return "#1F2329";
                    case "HavenAccentHoverColor": return "#000000";
                    case "HavenAccentTintColor": return "#331F2329";
                    case "HavenCanvasColor": return "#F7F8FA";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#991F2329";
                    case "HavenHoverColor": return "#14000000";
                    case "HavenMutedTextColor": return "#5A6068";
                    case "HavenOrangeColor": return "#4B5563";
                    case "HavenOrangeTintColor": return "#334B5563";
                    case "HavenPermissionGlowColor": return "#667C3AED";
                    case "HavenStrokeColor": return "#D9DDE3";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#FFFFFF";
                    case "HavenSurface2Color": return "#EEF0F3";
                    case "HavenTextColor": return "#17191C";
                    case "HavenVioletColor": return "#2563EB";
                    case "HavenVioletTintColor": return "#332563EB";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D916171A";
                    case "HeroOverlayTintColorFade": return "#4016171A";
                    case "Series1Color": return "#3B82F6";
                    case "Series2Color": return "#EA580C";
                    case "Series3Color": return "#0284C7";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#65A30D";
                    case "Series7Color": return "#8B5CF6";
                    case "Series8Color": return "#DC2626";
                    default: return null;
                }
            case "mono.dark":
                switch (token) {
                    case "HavenAccentColor": return "#E5E7EB";
                    case "HavenAccentHoverColor": return "#F3F4F6";
                    case "HavenAccentTintColor": return "#33E5E7EB";
                    case "HavenCanvasColor": return "#111316";
                    case "HavenDangerColor": return "#EF4444";
                    case "HavenDangerTintColor": return "#33EF4444";
                    case "HavenFocusRingColor": return "#99E5E7EB";
                    case "HavenHoverColor": return "#22FFFFFF";
                    case "HavenMutedTextColor": return "#9AA1AA";
                    case "HavenOrangeColor": return "#6B7280";
                    case "HavenOrangeTintColor": return "#336B7280";
                    case "HavenPermissionGlowColor": return "#408B5CF6";
                    case "HavenStrokeColor": return "#3A4048";
                    case "HavenSuccessColor": return "#22C55E";
                    case "HavenSuccessTintColor": return "#3322C55E";
                    case "HavenSurface1Color": return "#191C20";
                    case "HavenSurface2Color": return "#22262B";
                    case "HavenTextColor": return "#F8FAFC";
                    case "HavenVioletColor": return "#60A5FA";
                    case "HavenVioletTintColor": return "#3360A5FA";
                    case "HavenWarningColor": return "#F59E0B";
                    case "HavenWarningTintColor": return "#33F59E0B";
                    case "HeroOverlayTintColor": return "#D916171A";
                    case "HeroOverlayTintColorFade": return "#4016171A";
                    case "Series1Color": return "#3B82F6";
                    case "Series2Color": return "#EA580C";
                    case "Series3Color": return "#0284C7";
                    case "Series4Color": return "#D97706";
                    case "Series5Color": return "#D946EF";
                    case "Series6Color": return "#65A30D";
                    case "Series7Color": return "#8B5CF6";
                    case "Series8Color": return "#DC2626";
                    default: return null;
                }
            default: return null;
        }
    }
}
