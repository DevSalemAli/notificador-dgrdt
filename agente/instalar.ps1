# Instala el Agente Notificador DGRDT (una sola vez).
#  - Crea C:\Users\<usuario>\NotificadorDGRDT\venv con las librerias necesarias.
#  - Crea el acceso directo "Notificador DGRDT" en el escritorio.
$ErrorActionPreference = "Stop"
$aqui = Split-Path -Parent $MyInvocation.MyCommand.Path
$base = Join-Path $env:USERPROFILE "NotificadorDGRDT"
New-Item -ItemType Directory -Force $base | Out-Null

# 1) Buscar Python 3.10 o superior
$python = $null
try { if (Get-Command py -ErrorAction SilentlyContinue) { $python = (& py -3 -c "import sys; print(sys.executable)" 2>$null) } } catch {}
if (-not $python) { try { if (Get-Command python -ErrorAction SilentlyContinue) { $python = (& python -c "import sys; print(sys.executable)" 2>$null) } } catch {} }
if ($python) { $python = "$python".Trim() }
$ver = 0
if ($python) { try { $ver = [int](& $python -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])") } catch {} }
if (-not $python -or $ver -lt 310) {
    Write-Host "No encontre Python 3.10+. Instalalo desde https://www.python.org/downloads/ (tilda 'Add to PATH') y volve a ejecutar esto." -ForegroundColor Red
    exit 1
}

# 2) Entorno virtual + librerias
if (-not (Test-Path "$base\venv\Scripts\python.exe")) {
    Write-Host "Creando entorno de Python..."
    & $python -m venv "$base\venv"
}
Write-Host "Instalando librerias (puede tardar un minuto)..."
& "$base\venv\Scripts\python.exe" -m pip install --upgrade pip -q
& "$base\venv\Scripts\python.exe" -m pip install -r "$aqui\requirements.txt" -q

# 3) Acceso directo en el escritorio
$ws = New-Object -ComObject WScript.Shell
$desk = [Environment]::GetFolderPath("Desktop")
$lnk = $ws.CreateShortcut((Join-Path $desk "Notificador DGRDT.lnk"))
$lnk.TargetPath = "$base\venv\Scripts\pythonw.exe"
$lnk.Arguments = "`"$aqui\agente.py`""
$lnk.WorkingDirectory = $aqui
$lnk.Description = "Notificador DGRDT: genera, envia, sube a GEDO y agenda las notificaciones"
$lnk.IconLocation = "$env:SystemRoot\System32\imageres.dll,15"
$lnk.Save()
Write-Host "Listo. Abri 'Notificador DGRDT' desde el escritorio." -ForegroundColor Green
