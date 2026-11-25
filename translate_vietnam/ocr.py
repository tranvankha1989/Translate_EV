"""Đọc chữ trong ảnh bằng bộ nhận dạng chữ (OCR) có sẵn của Windows 10/11.

Dùng khi dịch vùng màn hình mà Gemini hết lượt hoặc lỗi: chữ đọc được đem dịch bằng
Google Dịch / MyMemory. Gọi qua Windows PowerShell có sẵn trong máy, không cần cài thêm.
Chỉ đọc được ngôn ngữ có gói OCR trên máy (thường có tiếng Anh).
"""
import base64
import os
import subprocess
import tempfile

CREATE_NO_WINDOW = 0x08000000
TIMEOUT = 25  # giây

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($operation, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $null = $task.Wait(-1)
    $task.Result
}
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('__PATH__')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($engine -eq $null) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
}
if ($engine -eq $null) { exit 3 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$stream.Dispose()
foreach ($line in $result.Lines) { [Console]::WriteLine($line.Text) }
"""


class OcrError(RuntimeError):
    pass


def windows_ocr(png: bytes) -> str:
    """Chữ trong ảnh PNG, mỗi dòng một dòng. Lỗi (máy không có OCR, PowerShell bị chặn...): OcrError."""
    fd, path = tempfile.mkstemp(suffix=".png", prefix="tv_ocr_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png)
        script = _SCRIPT.replace("__PATH__", path.replace("'", "''"))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            done = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded],
                capture_output=True, timeout=TIMEOUT, creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise OcrError(f"Không chạy được bộ đọc chữ của Windows: {e!r}") from e
        if done.returncode == 3:
            raise OcrError("Máy chưa có gói đọc chữ (OCR) của Windows cho ngôn ngữ nào")
        if done.returncode != 0:
            message = done.stderr.decode("utf-8", "replace").strip().splitlines()
            raise OcrError(f"Bộ đọc chữ của Windows lỗi: {message[0][:160] if message else done.returncode}")
        return done.stdout.decode("utf-8", "replace").replace("\r\n", "\n").strip()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
