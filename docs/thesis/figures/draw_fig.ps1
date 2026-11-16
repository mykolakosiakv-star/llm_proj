param([string]$Json, [string]$Out)
Add-Type -AssemblyName System.Drawing
$cfg = [System.IO.File]::ReadAllText($Json, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

$W = 2067; $H = 1000
$bmp = New-Object System.Drawing.Bitmap($W, $H)
$bmp.SetResolution(300, 300)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = 'AntiAlias'
$g.TextRenderingHint = 'AntiAliasGridFit'
$g.Clear([System.Drawing.Color]::White)

$black = [System.Drawing.Color]::Black
$pen = New-Object System.Drawing.Pen($black, 4)
$dash = New-Object System.Drawing.Pen($black, 3)
$dash.DashStyle = 'Dash'
$arrow = New-Object System.Drawing.Pen($black, 6)
$arrow.CustomEndCap = New-Object System.Drawing.Drawing2D.AdjustableArrowCap(5, 6)
$brush = New-Object System.Drawing.SolidBrush($black)
$gray = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(238, 238, 238))
$fTitle = New-Object System.Drawing.Font("Times New Roman", 11.5, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Point)
$fBody = New-Object System.Drawing.Font("Times New Roman", 9.5, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Point)
$fItal = New-Object System.Drawing.Font("Times New Roman", 9.5, [System.Drawing.FontStyle]::Italic, [System.Drawing.GraphicsUnit]::Point)

$center = New-Object System.Drawing.StringFormat
$center.Alignment = 'Center'; $center.LineAlignment = 'Center'
$top = New-Object System.Drawing.StringFormat
$top.Alignment = 'Center'; $top.LineAlignment = 'Near'

$bw = 400; $gap = 150; $x0 = 8
$by = 40; $bh = 330
$ly = 470; $lh = 330

for ($i = 0; $i -lt 4; $i++) {
    $x = $x0 + $i * ($bw + $gap)
    $s = $cfg.stages[$i]
    # основний блок
    $g.FillRectangle($gray, $x, $by, $bw, $bh)
    $g.DrawRectangle($pen, $x, $by, $bw, $bh)
    $rt = New-Object System.Drawing.RectangleF(($x + 20), ($by + 20), ($bw - 40), 130)
    $g.DrawString($s.title, $fTitle, $brush, $rt, $center)
    $rs = New-Object System.Drawing.RectangleF(($x + 20), ($by + 160), ($bw - 40), 150)
    $g.DrawString($s.sub, $fBody, $brush, $rs, $top)
    # стрілка між блоками
    if ($i -lt 3) {
        $g.DrawLine($arrow, ($x + $bw + 14), ($by + $bh / 2), ($x + $bw + $gap - 14), ($by + $bh / 2))
    }
    # з'єднання з обмеженням
    $g.DrawLine($dash, ($x + $bw / 2), ($by + $bh), ($x + $bw / 2), $ly)
    # блок обмеження
    $g.DrawRectangle($dash, $x, $ly, $bw, $lh)
    $rl = New-Object System.Drawing.RectangleF(($x + 20), ($ly + 18), ($bw - 40), 50)
    $g.DrawString($cfg.limit_label, $fItal, $brush, $rl, $top)
    $rb = New-Object System.Drawing.RectangleF(($x + 20), ($ly + 80), ($bw - 40), 230)
    $g.DrawString($s.limit, $fBody, $brush, $rb, $top)
}

# нижня вісь
$ay = 900
$g.DrawLine($arrow, 20, $ay, ($W - 20), $ay)
$ra = New-Object System.Drawing.RectangleF(0, ($ay + 18), $W, 70)
$g.DrawString($cfg.axis, $fItal, $brush, $ra, $top)

$g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "saved $Out"
