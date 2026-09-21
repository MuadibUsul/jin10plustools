# 用本机 PowerPoint 渲染并检查生成的日报（仅开发机使用；服务端不依赖 PowerPoint）
#   powershell -File scripts/qa_render.ps1 out/xxx.pptx out/qa
# 检查项：正文实际排版高度是否超出文本框、元素是否重叠、是否超出正文区域
param([string]$Pptx, [string]$OutDir)

$ErrorActionPreference = 'Stop'
$Pptx = (Resolve-Path $Pptx).Path
New-Item -ItemType Directory -Force $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path

$bodyBottom = 25.05
$pp = New-Object -ComObject PowerPoint.Application
$p = $pp.Presentations.Open($Pptx, $true, $false, $false)
$issues = 0
try {
  for ($i = 1; $i -le $p.Slides.Count; $i++) {
    $s = $p.Slides.Item($i)
    $s.Export("$OutDir\page-$('{0:D2}' -f $i).png", 'PNG', 750, 1333)
    $frames = @()
    foreach ($sh in $s.Shapes) {
      if ($sh.Name -like 'body-*' -or $sh.Name -like 'image-*') {
        $top = $sh.Top / 72; $h = $sh.Height / 72
        $actual = $h
        if ($sh.Name -like 'body-*') {
          $actual = $sh.TextFrame.TextRange.BoundHeight / 72
          $lines = $sh.TextFrame.TextRange.Lines().Count
          if ($actual -gt $h + 0.02) { "  [溢出] 第 $i 页 $($sh.Name) 实际 $([math]::Round($actual,2))in > 框 $([math]::Round($h,2))in"; $issues++ }
        }
        $frames += [pscustomobject]@{ Name = $sh.Name; Top = $top; Bottom = $top + [math]::Max($actual, $h) }
      }
    }
    $frames = @($frames | Sort-Object Top)
    for ($k = 1; $k -lt $frames.Count; $k++) {
      if ($frames[$k].Top -lt $frames[$k - 1].Bottom - 0.01) { "  [重叠] 第 $i 页 $($frames[$k-1].Name) 与 $($frames[$k].Name)"; $issues++ }
    }
    foreach ($f in $frames) { if ($f.Bottom -gt $bodyBottom + 0.05) { "  [越界] 第 $i 页 $($f.Name) 底部 $([math]::Round($f.Bottom,2))in"; $issues++ } }
    $fill = if ($frames.Count) { [math]::Round(($frames[-1].Bottom), 2) } else { 0 }
    "第 $i 页：$($frames.Count) 个内容块，内容底部 $fill in"
  }
} finally { $p.Close(); $pp.Quit() }
"检查完成，问题 $issues 个"
