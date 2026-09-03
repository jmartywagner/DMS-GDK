Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $AppDir
$Engine = Join-Path $AppDir "dms1_audio_asset_engine.py"
$DefaultOut = Join-Path $RootDir "EXPORT_GDK"

function Find-Python {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return @{ Exe = $cmd.Source; Launcher = $false } }
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return @{ Exe = $cmd.Source; Launcher = $true } }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    [System.Windows.Forms.MessageBox]::Show(
        "Python 3 n'a pas été trouvé. Installe Python 3 puis relance l'outil.",
        "DMS-1 Audio Asset Builder",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}

function Invoke-Engine([string[]]$Arguments) {
    if ($Python.Launcher) {
        $text = (& $Python.Exe -3 $Engine @Arguments 2>&1 | Out-String).Trim()
    } else {
        $text = (& $Python.Exe $Engine @Arguments 2>&1 | Out-String).Trim()
    }
    $code = $LASTEXITCODE
    return @{ Code = $code; Text = $text }
}

function Format-Duration([double]$seconds) {
    if ($seconds -lt 1.0) { return ("{0:0} ms" -f ($seconds * 1000.0)) }
    return ("{0:0.00} s" -f $seconds)
}

function Format-KB([double]$bytes) {
    if ($bytes -lt 1024) { return ("{0:0} o" -f $bytes) }
    return ("{0:0.0} Ko" -f ($bytes / 1024.0))
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "DMS-1 Audio Asset Builder P0.1"
$form.StartPosition = "CenterScreen"
$form.Size = New-Object System.Drawing.Size(1080, 720)
$form.MinimumSize = New-Object System.Drawing.Size(900, 620)
$form.BackColor = [System.Drawing.Color]::FromArgb(24, 28, 33)
$form.ForeColor = [System.Drawing.Color]::Gainsboro
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.AllowDrop = $true

$title = New-Object System.Windows.Forms.Label
$title.Text = "DMS-1 AUDIO ASSET BUILDER"
$title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 18)
$title.ForeColor = [System.Drawing.Color]::FromArgb(235, 238, 241)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(22, 18)
$form.Controls.Add($title)

$sub = New-Object System.Windows.Forms.Label
$sub.Text = "Glisse tes WAV ici -> analyse -> ADPCM-A / ADPCM-B -> banque + fichiers C/H pour le GDK"
$sub.ForeColor = [System.Drawing.Color]::FromArgb(155, 169, 181)
$sub.AutoSize = $true
$sub.Location = New-Object System.Drawing.Point(25, 54)
$form.Controls.Add($sub)

$drop = New-Object System.Windows.Forms.Panel
$drop.Location = New-Object System.Drawing.Point(24, 86)
$drop.Size = New-Object System.Drawing.Size(1018, 92)
$drop.Anchor = "Top,Left,Right"
$drop.BackColor = [System.Drawing.Color]::FromArgb(35, 42, 49)
$drop.BorderStyle = "FixedSingle"
$drop.AllowDrop = $true
$form.Controls.Add($drop)

$dropLabel = New-Object System.Windows.Forms.Label
$dropLabel.Text = "DEPOSE ICI TES WAV OU UN DOSSIER ENTIER"
$dropLabel.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 13)
$dropLabel.ForeColor = [System.Drawing.Color]::FromArgb(100, 190, 225)
$dropLabel.AutoSize = $true
$dropLabel.Location = New-Object System.Drawing.Point(280, 21)
$dropLabel.AllowDrop = $true
$drop.Controls.Add($dropLabel)

$dropHelp = New-Object System.Windows.Forms.Label
$dropHelp.Text = "AUTO propose A pour les sons <= 1,5 s et B pour les sons plus longs. Tu peux forcer A ou B."
$dropHelp.ForeColor = [System.Drawing.Color]::FromArgb(170, 180, 188)
$dropHelp.AutoSize = $true
$dropHelp.Location = New-Object System.Drawing.Point(220, 52)
$dropHelp.AllowDrop = $true
$drop.Controls.Add($dropHelp)

$grid = New-Object System.Windows.Forms.DataGridView
$grid.Location = New-Object System.Drawing.Point(24, 195)
$grid.Size = New-Object System.Drawing.Size(1018, 330)
$grid.Anchor = "Top,Bottom,Left,Right"
$grid.BackgroundColor = [System.Drawing.Color]::FromArgb(18, 22, 26)
$grid.BorderStyle = "FixedSingle"
$grid.EnableHeadersVisualStyles = $false
$grid.ColumnHeadersDefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(43, 50, 57)
$grid.ColumnHeadersDefaultCellStyle.ForeColor = [System.Drawing.Color]::WhiteSmoke
$grid.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(26, 31, 36)
$grid.DefaultCellStyle.ForeColor = [System.Drawing.Color]::Gainsboro
$grid.DefaultCellStyle.SelectionBackColor = [System.Drawing.Color]::FromArgb(55, 88, 105)
$grid.DefaultCellStyle.SelectionForeColor = [System.Drawing.Color]::White
$grid.RowHeadersVisible = $false
$grid.AllowUserToAddRows = $false
$grid.AllowUserToDeleteRows = $false
$grid.MultiSelect = $true
$grid.SelectionMode = "FullRowSelect"
$grid.AutoSizeRowsMode = "None"
$grid.RowTemplate.Height = 25
$form.Controls.Add($grid)

$c0 = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
$c0.Name = "File"; $c0.HeaderText = "Fichier"; $c0.Width = 300; $c0.ReadOnly = $true
$grid.Columns.Add($c0) | Out-Null
$c1 = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
$c1.Name = "Duration"; $c1.HeaderText = "Durée"; $c1.Width = 85; $c1.ReadOnly = $true
$grid.Columns.Add($c1) | Out-Null
$c2 = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
$c2.Name = "Source"; $c2.HeaderText = "Source"; $c2.Width = 125; $c2.ReadOnly = $true
$grid.Columns.Add($c2) | Out-Null
$c3 = New-Object System.Windows.Forms.DataGridViewComboBoxColumn
$c3.Name = "Target"; $c3.HeaderText = "Cible"; $c3.Width = 90
[void]$c3.Items.Add("AUTO"); [void]$c3.Items.Add("A"); [void]$c3.Items.Add("B")
$grid.Columns.Add($c3) | Out-Null
$c4 = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
$c4.Name = "Size"; $c4.HeaderText = "Taille estimée"; $c4.Width = 180; $c4.ReadOnly = $true
$grid.Columns.Add($c4) | Out-Null
$c5 = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
$c5.Name = "Status"; $c5.HeaderText = "Détection"; $c5.AutoSizeMode = "Fill"; $c5.ReadOnly = $true
$grid.Columns.Add($c5) | Out-Null

$knownPaths = @{}

function Add-Paths([string[]]$paths) {
    if (-not $paths -or $paths.Count -eq 0) { return }
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllLines($tmp, $paths, [System.Text.Encoding]::UTF8)
        $form.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
        $result = Invoke-Engine @("--analyze-list", $tmp)
        if (-not $result.Text) { throw "Le moteur n'a retourné aucune donnée." }
        $items = $result.Text | ConvertFrom-Json
        foreach ($item in @($items)) {
            if (-not $item.path) { continue }
            $pathKey = ([string]$item.path).ToLowerInvariant()
            if ($knownPaths.ContainsKey($pathKey)) { continue }
            $knownPaths[$pathKey] = $true
            $idx = $grid.Rows.Add()
            $row = $grid.Rows[$idx]
            $row.Tag = [string]$item.path
            $row.Cells["File"].Value = [string]$item.name
            if ($item.ok) {
                $row.Cells["Duration"].Value = Format-Duration([double]$item.duration)
                $row.Cells["Source"].Value = ("{0} Hz / {1} ch / {2} bit" -f $item.source_rate, $item.channels, $item.bits)
                $row.Cells["Target"].Value = "AUTO"
                $row.Cells["Size"].Value = ("A {0} | B {1}" -f (Format-KB $item.estimated_a_bytes), (Format-KB $item.estimated_b_bytes))
                $state = "AUTO -> ADPCM-" + [string]$item.auto
                if ($item.warning) { $state += " | " + [string]$item.warning }
                $row.Cells["Status"].Value = $state
            } else {
                $row.Cells["Duration"].Value = "-"
                $row.Cells["Source"].Value = "ERREUR"
                $row.Cells["Target"].Value = "AUTO"
                $row.Cells["Size"].Value = "-"
                $row.Cells["Status"].Value = [string]$item.error
                $row.DefaultCellStyle.ForeColor = [System.Drawing.Color]::FromArgb(235, 135, 135)
            }
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Analyse WAV", "OK", "Error") | Out-Null
    } finally {
        $form.Cursor = [System.Windows.Forms.Cursors]::Default
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

$dragEnter = {
    param($sender, $e)
    if ($e.Data.GetDataPresent([System.Windows.Forms.DataFormats]::FileDrop)) {
        $e.Effect = [System.Windows.Forms.DragDropEffects]::Copy
    } else {
        $e.Effect = [System.Windows.Forms.DragDropEffects]::None
    }
}
$dragDrop = {
    param($sender, $e)
    $files = [string[]]$e.Data.GetData([System.Windows.Forms.DataFormats]::FileDrop)
    Add-Paths $files
}
$form.add_DragEnter($dragEnter); $form.add_DragDrop($dragDrop)
$drop.add_DragEnter($dragEnter); $drop.add_DragDrop($dragDrop)
$dropLabel.add_DragEnter($dragEnter); $dropLabel.add_DragDrop($dragDrop)
$dropHelp.add_DragEnter($dragEnter); $dropHelp.add_DragDrop($dragDrop)

$buttonY = 542
$btnAdd = New-Object System.Windows.Forms.Button
$btnAdd.Text = "+ WAV"
$btnAdd.Location = New-Object System.Drawing.Point(24, $buttonY)
$btnAdd.Size = New-Object System.Drawing.Size(80, 32)
$btnAdd.Anchor = "Bottom,Left"
$form.Controls.Add($btnAdd)
$btnAdd.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = "WAV PCM (*.wav)|*.wav"
    $dlg.Multiselect = $true
    if ($dlg.ShowDialog() -eq "OK") { Add-Paths $dlg.FileNames }
})

$btnAuto = New-Object System.Windows.Forms.Button
$btnAuto.Text = "AUTO"
$btnAuto.Location = New-Object System.Drawing.Point(114, $buttonY)
$btnAuto.Size = New-Object System.Drawing.Size(72, 32)
$btnAuto.Anchor = "Bottom,Left"
$form.Controls.Add($btnAuto)
$btnAuto.Add_Click({ foreach ($r in $grid.SelectedRows) { $r.Cells["Target"].Value = "AUTO" } })

$btnA = New-Object System.Windows.Forms.Button
$btnA.Text = "Forcer A"
$btnA.Location = New-Object System.Drawing.Point(196, $buttonY)
$btnA.Size = New-Object System.Drawing.Size(80, 32)
$btnA.Anchor = "Bottom,Left"
$form.Controls.Add($btnA)
$btnA.Add_Click({ foreach ($r in $grid.SelectedRows) { $r.Cells["Target"].Value = "A" } })

$btnB = New-Object System.Windows.Forms.Button
$btnB.Text = "Forcer B"
$btnB.Location = New-Object System.Drawing.Point(286, $buttonY)
$btnB.Size = New-Object System.Drawing.Size(80, 32)
$btnB.Anchor = "Bottom,Left"
$form.Controls.Add($btnB)
$btnB.Add_Click({ foreach ($r in $grid.SelectedRows) { $r.Cells["Target"].Value = "B" } })

$btnRemove = New-Object System.Windows.Forms.Button
$btnRemove.Text = "Retirer"
$btnRemove.Location = New-Object System.Drawing.Point(376, $buttonY)
$btnRemove.Size = New-Object System.Drawing.Size(74, 32)
$btnRemove.Anchor = "Bottom,Left"
$form.Controls.Add($btnRemove)
$btnRemove.Add_Click({
    $rows = @($grid.SelectedRows | Sort-Object Index -Descending)
    foreach ($r in $rows) {
        if ($r.Tag) { [void]$knownPaths.Remove(([string]$r.Tag).ToLowerInvariant()) }
        $grid.Rows.RemoveAt($r.Index)
    }
})

$btnClear = New-Object System.Windows.Forms.Button
$btnClear.Text = "Vider"
$btnClear.Location = New-Object System.Drawing.Point(460, $buttonY)
$btnClear.Size = New-Object System.Drawing.Size(64, 32)
$btnClear.Anchor = "Bottom,Left"
$form.Controls.Add($btnClear)
$btnClear.Add_Click({ $grid.Rows.Clear(); $knownPaths.Clear() })

$rateLabel = New-Object System.Windows.Forms.Label
$rateLabel.Text = "ADPCM-B Hz :"
$rateLabel.Location = New-Object System.Drawing.Point(550, ($buttonY + 8))
$rateLabel.AutoSize = $true
$rateLabel.Anchor = "Bottom,Left"
$form.Controls.Add($rateLabel)

$rateBox = New-Object System.Windows.Forms.NumericUpDown
$rateBox.Location = New-Object System.Drawing.Point(645, ($buttonY + 4))
$rateBox.Size = New-Object System.Drawing.Size(90, 26)
$rateBox.Minimum = 1000
$rateBox.Maximum = 55556
$rateBox.Value = 26000
$rateBox.Increment = 1000
$rateBox.Anchor = "Bottom,Left"
$form.Controls.Add($rateBox)

$outLabel = New-Object System.Windows.Forms.Label
$outLabel.Text = "Sortie GDK :"
$outLabel.Location = New-Object System.Drawing.Point(24, 594)
$outLabel.AutoSize = $true
$outLabel.Anchor = "Bottom,Left"
$form.Controls.Add($outLabel)

$outBox = New-Object System.Windows.Forms.TextBox
$outBox.Text = $DefaultOut
$outBox.Location = New-Object System.Drawing.Point(105, 590)
$outBox.Size = New-Object System.Drawing.Size(700, 27)
$outBox.Anchor = "Bottom,Left,Right"
$form.Controls.Add($outBox)

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = "Choisir..."
$btnBrowse.Location = New-Object System.Drawing.Point(815, 588)
$btnBrowse.Size = New-Object System.Drawing.Size(85, 30)
$btnBrowse.Anchor = "Bottom,Right"
$form.Controls.Add($btnBrowse)
$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "Choisir le dossier d'export GDK"
    $dlg.SelectedPath = $outBox.Text
    if ($dlg.ShowDialog() -eq "OK") { $outBox.Text = $dlg.SelectedPath }
})

$btnExport = New-Object System.Windows.Forms.Button
$btnExport.Text = "GENERER GDK"
$btnExport.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 10)
$btnExport.Location = New-Object System.Drawing.Point(910, 586)
$btnExport.Size = New-Object System.Drawing.Size(132, 34)
$btnExport.Anchor = "Bottom,Right"
$btnExport.BackColor = [System.Drawing.Color]::FromArgb(70, 126, 150)
$btnExport.ForeColor = [System.Drawing.Color]::White
$form.Controls.Add($btnExport)

$status = New-Object System.Windows.Forms.Label
$status.Text = "Prêt. Dépose des WAV."
$status.Location = New-Object System.Drawing.Point(24, 640)
$status.AutoSize = $true
$status.Anchor = "Bottom,Left"
$status.ForeColor = [System.Drawing.Color]::FromArgb(150, 165, 176)
$form.Controls.Add($status)

$btnExport.Add_Click({
    if ($grid.Rows.Count -eq 0) {
        [System.Windows.Forms.MessageBox]::Show("Ajoute d'abord au moins un WAV.", "DMS-1 Audio Asset Builder") | Out-Null
        return
    }
    $samples = @()
    foreach ($row in $grid.Rows) {
        if (-not $row.Tag) { continue }
        $target = [string]$row.Cells["Target"].Value
        if (-not $target) { $target = "AUTO" }
        $samples += @{ path = [string]$row.Tag; target = $target }
    }
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        @{ samples = $samples } | ConvertTo-Json -Depth 5 | Set-Content -Path $tmp -Encoding UTF8
        $out = $outBox.Text.Trim()
        if (-not $out) { $out = $DefaultOut }
        $status.Text = "Conversion en cours..."
        $form.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
        $form.Refresh()
        $result = Invoke-Engine @("--build", $tmp, "--out", $out, "--b-rate", [string][int]$rateBox.Value)
        $reply = $result.Text | ConvertFrom-Json
        if ($result.Code -ne 0 -or -not $reply.ok) {
            throw ([string]$reply.error)
        }
        $status.Text = ("Export terminé : {0} samples | {1:0.0} Ko" -f $reply.count, ($reply.bank_bytes / 1024.0))
        [System.Windows.Forms.MessageBox]::Show(
            "Export GDK terminé.`r`n`r`n$($reply.count) samples`r`nBanque : $([math]::Round($reply.bank_bytes / 1024.0, 1)) Ko`r`n`r`nDossier :`r`n$($reply.out)",
            "DMS-1 Audio Asset Builder",
            "OK",
            "Information"
        ) | Out-Null
    } catch {
        $status.Text = "Erreur d'export."
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Export GDK", "OK", "Error") | Out-Null
    } finally {
        $form.Cursor = [System.Windows.Forms.Cursors]::Default
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
})

[void]$form.ShowDialog()
