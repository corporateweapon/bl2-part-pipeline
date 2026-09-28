param([string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
# DPI-aware first (F36): otherwise Screen.Bounds is in logical pixels and a scaled desktop
# (4K at 150 %) is captured as its top-left 2560x1440 only.
Add-Type -Namespace Bl2 -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[Bl2.Dpi]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output "saved $Out $($b.Width)x$($b.Height)"
