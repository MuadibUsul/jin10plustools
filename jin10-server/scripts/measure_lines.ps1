# 用 PowerPoint 实测校准 PPT 中每个文本框的行数，写入 measured.json
param([string]$Dir)
$Dir = (Resolve-Path $Dir).Path
$pp = New-Object -ComObject PowerPoint.Application
$p = $pp.Presentations.Open("$Dir\calibration.pptx", $true, $false, $false)
$rows = @()
try {
  for ($i = 1; $i -le $p.Slides.Count; $i++) {
    foreach ($sh in $p.Slides.Item($i).Shapes) {
      if ($sh.Name -like 'body-*') {
        $rows += [pscustomobject]@{ slide = $i; shape = $sh.Name; lines = $sh.TextFrame.TextRange.Lines().Count }
      }
    }
  }
} finally { $p.Close(); $pp.Quit() }
$rows | ConvertTo-Json | Out-File -Encoding utf8 "$Dir\measured.json"
"实测 $($rows.Count) 个文本框"
