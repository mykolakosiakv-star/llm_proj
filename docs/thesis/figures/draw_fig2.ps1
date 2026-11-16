param([string]$Json, [string]$Out21, [string]$Out22)
Add-Type -AssemblyName System.Drawing
$cfg = [System.IO.File]::ReadAllText($Json, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

function New-Canvas($w, $h) {
    $b = New-Object System.Drawing.Bitmap($w, $h)
    $b.SetResolution(300, 300)
    $g = [System.Drawing.Graphics]::FromImage($b)
    $g.SmoothingMode = 'AntiAlias'
    $g.TextRenderingHint = 'AntiAliasGridFit'
    $g.Clear([System.Drawing.Color]::White)
    return @{ bmp = $b; g = $g }
}

$black = [System.Drawing.Color]::Black
$pen = New-Object System.Drawing.Pen($black, 4)
$dash = New-Object System.Drawing.Pen($black, 3); $dash.DashStyle = 'Dash'
$arrow = New-Object System.Drawing.Pen($black, 5)
$arrow.CustomEndCap = New-Object System.Drawing.Drawing2D.AdjustableArrowCap(5, 6)
$brush = New-Object System.Drawing.SolidBrush($black)
$gray = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(238, 238, 238))
$white = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
$fTitle = New-Object System.Drawing.Font("Times New Roman", 11.5, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Point)
$fBody = New-Object System.Drawing.Font("Times New Roman", 9.5, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Point)
$fItal = New-Object System.Drawing.Font("Times New Roman", 9.5, [System.Drawing.FontStyle]::Italic, [System.Drawing.GraphicsUnit]::Point)
$center = New-Object System.Drawing.StringFormat; $center.Alignment = 'Center'; $center.LineAlignment = 'Center'
$top = New-Object System.Drawing.StringFormat; $top.Alignment = 'Center'; $top.LineAlignment = 'Near'
$left = New-Object System.Drawing.StringFormat; $left.Alignment = 'Near'; $left.LineAlignment = 'Center'

function RectF($x, $y, $w, $h) { return New-Object System.Drawing.RectangleF([single]$x, [single]$y, [single]$w, [single]$h) }
function Pt($x, $y) { return New-Object System.Drawing.PointF([single]$x, [single]$y) }

# ---------- Рисунок 2.1 ----------
$c = New-Canvas 2067 740
$g = $c.g
$bw = 288; $gap = 64; $x0 = 2; $by = 40; $bh = 360; $my = 500; $mh = 200
for ($i = 0; $i -lt 6; $i++) {
    $x = $x0 + $i * ($bw + $gap)
    $b = $cfg.f21.blocks[$i]
    $g.FillRectangle($gray, $x, $by, $bw, $bh)
    $g.DrawRectangle($pen, $x, $by, $bw, $bh)
    $g.DrawString($b.t, $fTitle, $brush, (RectF ($x + 10) ($by + 15) ($bw - 20) 140), $center)
    $g.DrawString($b.s, $fBody, $brush, (RectF ($x + 10) ($by + 170) ($bw - 20) 180), $top)
    if ($i -lt 5) {
        $g.DrawLine($arrow, ($x + $bw + 8), ($by + $bh / 2), ($x + $bw + $gap - 8), ($by + $bh / 2))
    }
    if ($b.m.Count -gt 0) {
        $g.DrawLine($dash, ($x + $bw / 2), ($by + $bh), ($x + $bw / 2), $my)
        $g.DrawRectangle($dash, $x, $my, $bw, $mh)
        $g.DrawString($cfg.f21.label, $fItal, $brush, (RectF ($x + 10) ($my + 14) ($bw - 20) 50), $top)
        $g.DrawString(($b.m -join "`n"), $fBody, $brush, (RectF ($x + 10) ($my + 70) ($bw - 20) 120), $top)
    }
}
$g.Dispose(); $c.bmp.Save($Out21, [System.Drawing.Imaging.ImageFormat]::Png); $c.bmp.Dispose()

# ---------- Рисунок 2.2 ----------
$c = New-Canvas 2067 970
$g = $c.g
$g.TranslateTransform(0, -150)
$f = $cfg.f22

function Box($g, $x, $y, $w, $h, $text, $fill) {
    if ($fill) { $g.FillRectangle($gray, $x, $y, $w, $h) }
    $g.DrawRectangle($pen, $x, $y, $w, $h)
    $g.DrawString($text, $fTitle, $brush, (RectF ($x + 10) $y ($w - 20) $h), $center)
}
function Diamond($g, $cx, $cy, $hw, $hh, $text) {
    $pts = @((Pt $cx ($cy - $hh)), (Pt ($cx + $hw) $cy), (Pt $cx ($cy + $hh)), (Pt ($cx - $hw) $cy))
    $g.FillPolygon($white, $pts)
    $g.DrawPolygon($pen, $pts)
    $g.DrawString($text, $fBody, $brush, (RectF ($cx - $hw + 70) ($cy - $hh + 20) (2 * $hw - 140) (2 * $hh - 40)), $center)
}
function Coin($g, $x, $y) {
    $g.FillEllipse($white, $x, $y, 58, 58)
    $g.DrawEllipse($pen, $x, $y, 58, 58)
    $g.DrawString('$', $fTitle, $brush, (RectF $x $y 58 58), $center)
}
function Label($g, $text, $x, $y) {
    $g.DrawString($text, $fItal, $brush, (RectF $x $y 260 50), $left)
}
function Path($g, $pts) { $g.DrawLines($arrow, [System.Drawing.PointF[]]$pts) }

# вузли
Box $g 10 230 220 140 $f.query $true
Diamond $g 520 300 190 110 $f.risky
Box $g 860 220 300 160 $f.cheap $true
Coin $g 1130 195
Diamond $g 1450 300 190 110 $f.own
$g.FillRectangle($gray, 1727, 470, 330, 150)
$g.DrawRectangle($pen, 1727, 470, 330, 150)
$g.DrawString($f.user, $fBody, $brush, (RectF 1737 470 310 150), $center)
Box $g 860 720 300 160 $f.strong $true
Coin $g 1130 695
Diamond $g 1450 800 190 110 $f.asks
Box $g 1790 950 267 120 $f.operator $true

# стрілки
Path $g @((Pt 232 300), (Pt 328 300))
Path $g @((Pt 710 300), (Pt 858 300))
Label $g $f.no 745 255
Path $g @((Pt 520 410), (Pt 520 800), (Pt 858 800))
Label $g $f.yes 545 540
Path $g @((Pt 1160 300), (Pt 1258 300))
Path $g @((Pt 1640 300), (Pt 1892 300), (Pt 1892 468))
Label $g $f.yes 1700 255
Path $g @((Pt 1450 410), (Pt 1450 560), (Pt 1010 560), (Pt 1010 718))
Label $g $f.next 1455 480
Path $g @((Pt 1160 800), (Pt 1258 800))
Path $g @((Pt 1640 800), (Pt 1892 800), (Pt 1892 622))
Label $g $f.no 1700 755
Path $g @((Pt 1450 910), (Pt 1450 1010), (Pt 1788 1010))
Label $g $f.yes 1480 930

$g.DrawString($f.legend, $fItal, $brush, (RectF 10 1040 1500 60), $left)
$g.Dispose(); $c.bmp.Save($Out22, [System.Drawing.Imaging.ImageFormat]::Png); $c.bmp.Dispose()
Write-Output "saved"
