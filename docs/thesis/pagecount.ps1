param([string]$Docx, [string]$Pdf)
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($Docx, $false, $true)
    $doc.Repaginate()
    $pages = $doc.ComputeStatistics(2)
    $ps = $doc.PageSetup
    Write-Output ("pages={0}; margins L{1} R{2} T{3} B{4} (pt); words={5}" -f $pages, $ps.LeftMargin, $ps.RightMargin, $ps.TopMargin, $ps.BottomMargin, $doc.ComputeStatistics(0))
    $last = $doc.Paragraphs.Last.Range
    $lastPage = $last.Information(3)
    $y = $last.Information(6)
    Write-Output ("last paragraph on page {0}, vertical pos {1} pt from top of page (page height {2})" -f $lastPage, $y, $ps.PageHeight)
    if ($Pdf) { $doc.ExportAsFixedFormat($Pdf, 17) }
    $doc.Close($false)
} finally {
    $word.Quit()
}
