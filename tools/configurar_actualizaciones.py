"""Run in the complete, private LexPro project folder on Windows."""
import argparse, ast, json, shutil, subprocess, sys, tempfile
from pathlib import Path
from datetime import datetime

INSTALL = r'''$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$config = Get-Content deployment_config.json -Raw | ConvertFrom-Json
if (-not (Test-Path "installer\LexPro.iss")) { throw "Falta installer\LexPro.iss. Use el proyecto completo." }
& ".\COMPILAR_EXE.ps1"
$appExe = Join-Path $PSScriptRoot "dist\LexPro_Ecuador_Enterprise.exe"
if (-not (Test-Path $appExe)) { throw "No se creo el ejecutable." }
if ($config.require_signed_updates) { & ".\FIRMAR_EXE.ps1" -Archivo $appExe }
$candidates = @((Join-Path ([Environment]::GetEnvironmentVariable("ProgramFiles(x86)")) "Inno Setup 6\ISCC.exe"), (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"))
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Instale Inno Setup 6." }
$match = [regex]::Match((Get-Content app_config.py -Raw), 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
if (-not $match.Success) { throw "APP_VERSION invalida." }
$setup = Join-Path $PSScriptRoot "dist\LexPro_Ecuador_Setup.exe"
if (Test-Path $setup) { Remove-Item -LiteralPath $setup }
& $iscc "/DMyAppVersion=$($match.Groups[1].Value)" ".\installer\LexPro.iss"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $setup)) { throw "Fallo Inno Setup." }
if ($config.require_signed_updates) { & ".\FIRMAR_EXE.ps1" -Archivo $setup }
Write-Host "Instalador creado: $setup" -ForegroundColor Green
'''
PUBLISH = r'''$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$config = Get-Content deployment_config.json -Raw | ConvertFrom-Json
$repository = $config.update_repository
if ($repository -ne "milindoecuador/lexpro-ecuador-updates") { throw "Repositorio inesperado." }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "Instale GitHub CLI y ejecute gh auth login." }
gh auth status
if ($LASTEXITCODE -ne 0) { throw "Falta autenticar GitHub CLI." }
$match = [regex]::Match((Get-Content app_config.py -Raw), 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
if (-not $match.Success) { throw "APP_VERSION invalida." }
$version = $match.Groups[1].Value
$setup = Join-Path $PSScriptRoot "dist\LexPro_Ecuador_Setup.exe"
if (-not (Test-Path $setup)) { throw "Ejecute CREAR_INSTALADOR.ps1." }
if ($config.require_signed_updates) {
    $sig = Get-AuthenticodeSignature -LiteralPath $setup
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Thumbprint -ne $config.signing_certificate_thumbprint) { throw "Firma incorrecta." }
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup).Hash.ToLowerInvariant()
$checksum = "$setup.sha256"
Set-Content -LiteralPath $checksum -Value "$hash  LexPro_Ecuador_Setup.exe" -Encoding ascii
gh release view "v$version" --repo $repository 2>$null
if ($LASTEXITCODE -eq 0) { throw "Version existente: incremente APP_VERSION. No se sobrescribe." }
gh release create "v$version" $setup $checksum --repo $repository --title "LexPro Ecuador v$version" --notes "Actualizacion de LexPro. Integridad comprobada mediante SHA-256." --draft
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el borrador." }
$verifyDir = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $verifyDir | Out-Null
try {
    gh release download "v$version" --repo $repository --dir $verifyDir --pattern "LexPro_Ecuador_Setup.exe" --pattern "LexPro_Ecuador_Setup.exe.sha256"
    if ($LASTEXITCODE -ne 0) { throw "No se pudieron descargar los archivos." }
    $remoteHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $verifyDir "LexPro_Ecuador_Setup.exe")).Hash.ToLowerInvariant()
    $remoteText = (Get-Content (Join-Path $verifyDir "LexPro_Ecuador_Setup.exe.sha256") -Raw).Trim()
    if ($remoteHash -ne $hash -or $remoteText -ne "$hash  LexPro_Ecuador_Setup.exe") { throw "Verificacion remota fallida; permanece como borrador." }
    gh release edit "v$version" --repo $repository --draft=false --latest
    if ($LASTEXITCODE -ne 0) { throw "No se pudo publicar." }
    $raw = gh api "repos/$repository/releases/latest"
    if ($LASTEXITCODE -ne 0) { throw "No se pudo consultar latest." }
    $latest = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
    if ($latest.tag_name -ne "v$version") { throw "latest no coincide." }
    Write-Host "PUBLICADO Y VERIFICADO: https://github.com/$repository/releases/tag/v$version" -ForegroundColor Green
} finally { Remove-Item -LiteralPath $verifyDir -Recurse -Force }
'''
TEST = r'''
import hashlib, io, pathlib, sys, tempfile, types, unittest
from unittest.mock import patch
qt = types.ModuleType("PyQt6.QtCore")
qt.QThread = object
qt.pyqtSignal = lambda *a: None
sys.modules["PyQt6"] = types.ModuleType("PyQt6")
sys.modules["PyQt6.QtCore"] = qt
db = types.ModuleType("database")
sys.modules["database"] = db
import app_config as c
import updater as u
class Response(io.BytesIO):
    headers = {}
class Tests(unittest.TestCase):
    def test_config(self):
        self.assertEqual(c.UPDATE_REPOSITORY, "milindoecuador/lexpro-ecuador-updates")
        self.assertTrue(c.updates_configured())
        self.assertFalse(c.REQUIRE_SIGNED_UPDATES)
    def test_versions(self):
        self.assertTrue(u.is_newer("1.0.1", "1.0.0"))
        self.assertFalse(u.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(u.is_newer("0.9.9", "1.0.0"))
        with self.assertRaises(u.UpdateError): u.is_newer("invalid")
    def test_no_upgrade(self):
        with patch.object(u, "_request_json", return_value={"tag_name": c.APP_VERSION}):
            self.assertIsNone(u.check_latest_release())
    def test_missing_assets(self):
        with patch.object(u, "_request_json", return_value={"tag_name": "999.0.0", "assets": []}):
            with self.assertRaises(u.UpdateError): u.check_latest_release()
    def test_release(self):
        assets = [{"name": name, "browser_download_url": "https://github.com/milindoecuador/lexpro-ecuador-updates/releases/download/v999.0.0/"+name} for name in [c.UPDATE_INSTALLER_NAME, c.UPDATE_INSTALLER_NAME+".sha256"]]
        with patch.object(u, "_request_json", return_value={"tag_name": "v999.0.0", "assets": assets}):
            self.assertEqual(u.check_latest_release().version, "999.0.0")
        for flag in ["draft", "prerelease"]:
            with patch.object(u, "_request_json", return_value={flag: True}):
                self.assertIsNone(u.check_latest_release())
    def test_limit(self):
        with patch.object(u.urllib.request, "urlopen", return_value=Response(b"12345")):
            with self.assertRaises(u.UpdateError): u._download_bytes("https://github.com/test", 4)
    def test_hash(self):
        data = b"MZ-fixture-only"
        digest = hashlib.sha256(data).hexdigest().encode()
        info = u.UpdateInfo("999.0.0", "", "", "https://github.com/setup", "https://github.com/checksum")
        with tempfile.TemporaryDirectory() as directory:
            db.obtener_ruta_estado = lambda name: str(pathlib.Path(directory)/name)
            def download(url, destination):
                destination.write_bytes(data)
                return hashlib.sha256(data).hexdigest()
            with patch.object(u, "_download_bytes", return_value=digest), patch.object(u, "_download_installer", side_effect=download):
                self.assertEqual(u.download_and_verify(info).read_bytes(), data)
            with patch.object(u, "_download_bytes", return_value=b"0"*64), patch.object(u, "_download_installer", side_effect=download):
                with self.assertRaises(u.UpdateError): u.download_and_verify(info)
                self.assertFalse((pathlib.Path(directory)/"updates"/(c.UPDATE_INSTALLER_NAME+".part")).exists())
    def test_invalid_hash(self):
        with patch.object(u, "_download_bytes", return_value=b"invalid"):
            with self.assertRaises(u.UpdateError): u.download_and_verify(u.UpdateInfo("", "", "", "", ""))
unittest.main()
'''
def swap(text, old, new, name):
    if text.count(old) != 1: raise RuntimeError("Contenido inesperado: "+name+". No se aplicaron cambios.")
    return text.replace(old, new, 1)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--build", action="store_true")
    p.add_argument("--publish", action="store_true")
    args = p.parse_args()
    if args.publish and not args.build: p.error("--publish requiere --build")
    root = Path.cwd()
    if (root/".lexpro_remote_patch_v1").exists(): raise RuntimeError("Parche ya aplicado. Ejecute los scripts PS1 para compilar/publicar.")
    names = ["app_config.py", "main.py", "updater.py", "COMPILAR_EXE.ps1", "CREAR_INSTALADOR.ps1", "PUBLICAR_ACTUALIZACION.ps1", "deployment_config.json"]
    original = {n: (root/n).read_text(encoding="utf-8-sig") for n in names}
    updated = {}
    cfg = json.loads(original["deployment_config.json"])
    cfg.update(update_repository="milindoecuador/lexpro-ecuador-updates", license_api_url="https://lexpro-licencias.milindoecuador.workers.dev", require_signed_updates=False)
    updated["deployment_config.json"] = json.dumps(cfg, indent=2)+"\n"
    app = swap(original["app_config.py"], 'encoding="utf-8"', 'encoding="utf-8-sig"', "app_config.py")
    app = swap(app, 'os.getenv("LEXPRO_REQUIRE_SIGNED_UPDATES", "1") != "0"', 'os.getenv("LEXPRO_REQUIRE_SIGNED_UPDATES", "1" if _DEPLOYMENT.get("require_signed_updates", True) else "0") != "0"', "app_config.py")
    updated["app_config.py"] = app
    maintext = swap(original["main.py"], 'self._descargar_actualizacion(info)', 'self._update_worker.finished.connect(lambda: self._descargar_actualizacion(info)) if self._update_worker.isRunning() else self._descargar_actualizacion(info)', "main.py")
    updated["main.py"] = swap(maintext, "La integridad y la firma digital del instalador fueron verificadas. ", "La integridad SHA-256 del instalador fue verificada. ", "main.py")
    build = swap(original["COMPILAR_EXE.ps1"], "py -m venv .venv", 'py -m venv .venv\n    if ($LASTEXITCODE -ne 0) { throw "Fallo al crear el entorno." }', "COMPILAR_EXE.ps1")
    build = swap(build, "& $pythonLexPro -m PyInstaller", 'if ($LASTEXITCODE -ne 0) { throw "Fallo al instalar dependencias." }\nif (Test-Path "dist\\LexPro_Ecuador_Enterprise.exe") { Remove-Item -LiteralPath "dist\\LexPro_Ecuador_Enterprise.exe" }\n& $pythonLexPro -m PyInstaller', "COMPILAR_EXE.ps1")
    updated["COMPILAR_EXE.ps1"] = swap(build, '$ejecutable = Join-Path', 'if ($LASTEXITCODE -ne 0) { throw "Fallo PyInstaller." }\n$ejecutable = Join-Path', "COMPILAR_EXE.ps1")
    updated["CREAR_INSTALADOR.ps1"] = INSTALL
    updated["PUBLICAR_ACTUALIZACION.ps1"] = PUBLISH
    for name, text in updated.items():
        if name.endswith(".py"): ast.parse(text, filename=name)
    with tempfile.TemporaryDirectory() as directory:
        for name in ["app_config.py", "deployment_config.json", "updater.py"]:
            (Path(directory)/name).write_text(updated.get(name, original[name]), encoding="utf-8")
        subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, "+repr(directory)+");\n"+TEST], cwd=directory, check=True)
    backup = root/("respaldo_actualizaciones_"+datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.mkdir()
    for name in updated: shutil.copy2(root/name, backup/name)
    try:
        for name, text in updated.items(): (root/name).write_text(text, encoding="utf-8-sig" if name.endswith(".ps1") else "utf-8")
    except Exception:
        for name in updated: shutil.copy2(backup/name, root/name)
        raise
    (root/".lexpro_remote_patch_v1").write_text(str(backup), encoding="utf-8")
    print("Configuracion aplicada; 8 pruebas aisladas aprobadas. Respaldo: "+str(backup))
    print("Pendiente: validacion de interfaz e instalacion real en Windows.")
    if args.build: subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root/"CREAR_INSTALADOR.ps1")], check=True)
    if args.publish: subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root/"PUBLICAR_ACTUALIZACION.ps1")], check=True)
if __name__ == "__main__": main()
