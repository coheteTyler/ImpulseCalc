# Install OpenFOAM inside WSL Ubuntu for ImpulseCalc
# Run:
#   powershell -ExecutionPolicy Bypass -File C:\Users\tyler\ImpulseCalc\scripts\install_openfoam_wsl.ps1

$ErrorActionPreference = "Continue"
Write-Host "Starting WSL Ubuntu..."
wsl -d Ubuntu -e true
if ($LASTEXITCODE -ne 0) {
    Write-Host "WSL Ubuntu failed to start. Run: wsl --install -d Ubuntu"
    exit 1
}

Write-Host "Installing OpenFOAM in WSL (this may take several minutes)..."

$bash = @'
set -e
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y

if apt-cache show openfoam >/dev/null 2>&1; then
  sudo apt-get install -y openfoam || true
fi

if ! command -v blockMesh >/dev/null 2>&1; then
  curl -s https://dl.openfoam.com/add-debian-repo.sh | sudo bash || true
  sudo apt-get update -y || true
  sudo apt-get install -y openfoam12-default || sudo apt-get install -y openfoam11-default || true
fi

if ! command -v blockMesh >/dev/null 2>&1; then
  sudo apt-get install -y openfoam-default || sudo apt-get install -y openfoam10 || true
fi

for f in /opt/openfoam*/etc/bashrc /usr/lib/openfoam/openfoam*/etc/bashrc; do
  if [ -f "$f" ]; then
    . "$f"
    break
  fi
done

if command -v blockMesh >/dev/null 2>&1; then
  echo "SUCCESS: $(command -v blockMesh)"
  blockMesh -help 2>&1 | head -n 3 || true
  echo "WM_PROJECT_DIR=$WM_PROJECT_DIR"
  exit 0
else
  echo "FAILED: blockMesh still not found after install"
  exit 2
fi
'@

wsl -d Ubuntu bash -lc $bash
exit $LASTEXITCODE
