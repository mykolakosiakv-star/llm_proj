param([string]$Docx, [string]$Pdf)
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($Docx)
    $doc.Repaginate()
    foreach ($t in $doc.TablesOfContents) { $t.Update() }
    $doc.Fields.Update() | Out-Null
    foreach ($t in $doc.TablesOfContents) { $t.Update() }
    $pages = $doc.ComputeStatistics(2)
    $doc.Save()
    $doc.ExportAsFixedFormat($Pdf, 17)
    Write-Output "pages=$pages"
    # номер сторінки початку кожного заголовка 1 рівня
    foreach ($p in $doc.Paragraphs) {
        if ($p.OutlineLevel -eq 1 -or $p.OutlineLevel -eq 2) {
            $txt = $p.Range.Text.Trim()
            if ($txt.Length -gt 0) {
                Write-Output ("{0}`t{1}" -f $p.Range.Information(3), $txt.Substring(0, [Math]::Min(70, $txt.Length)))
            }
        }
    }
    $doc.Close($false)
} finally {
    $word.Quit()
}
