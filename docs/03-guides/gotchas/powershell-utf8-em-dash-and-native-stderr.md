# Gotcha — PowerShell .ps1 con em-dashes y stderr de nativos

**Síntomas**:

1. PowerShell falla al parsear el script con
   `TerminatorExpectedAtEndOfString` apuntando a una línea que parece
   correcta.

   ```
   En C:\...\scripts\dev\run-human-tests-05.ps1: 118 Carácter: 67
   + ... ides\human-tests\05-mcp-tools-avanzadas.md" -ForegroundColor DarkGray
   +                                               ~~~~~~~~~~~~~~~~~~~~~~~~~~~
   Falta la cadena en el terminador: ".
   ```

2. El script parsea bien pero salta a mitad de ejecución con
   `NativeCommandError` y un mensaje del estilo:

   ```
   python.exe : Processing request of type ListToolsRequest
   En ...\scripts\dev\run-human-tests-05.ps1: 88 Carácter: 5
   +     & $VenvPython $script
   +     ~~~~~~~~~~~~~~~~~~~~~
       + CategoryInfo : NotSpecified: (...:String) [], RemoteException
       + FullyQualifiedErrorId : NativeCommandError
   ```

   El exit code del native exe era `0` (success), pero PowerShell
   marca el run como fallido.

## Causa raíz

Son **dos trampas distintas** de Windows PowerShell 5.1; ambas pegan
a launchers de tests:

### 1) Encoding del fichero `.ps1`

PowerShell 5.1 (`powershell.exe`) lee `.ps1` como **cp1252** por
defecto si el fichero no tiene BOM. Si grabas el script en UTF-8
sin BOM y mete cualquier carácter de más de 1 byte (em-dashes `—`,
acentos en cadenas, `…`, comillas tipográficas `“ ”`), el parser
mal-interpreta los bytes y termina buscando un `"` que nunca llega.

### 2) Stderr de native exe + `$ErrorActionPreference="Stop"`

PowerShell 5.1 trata cada línea de stderr de un native exe como un
`NativeCommandError`. Con el preference en `Stop`, esa "error
record" detiene el script — aunque el exit code del exe sea 0.

Trampa adicional: la doc del Bash tool del repo dice
"avoid `2>&1` on native executables" porque eso **empeora** el
caso: PowerShell envuelve cada línea redirigida en un `ErrorRecord`
y además marca `$?` como `$false`. La forma sana es NO redirigir y
o bien bajar el preference, o bien aceptar stderr como ruido normal.

## Fix

**Para (1) em-dashes y otros UTF-8**: pure ASCII. Reemplaza `—` por
`-`, `…` por `...`, comillas tipográficas por las normales. Mismo
tratamiento que ya aplicamos en
`scripts/dev/run-human-tests.ps1` (Plan 04.5).

```python
# scripts/dev/<your>.ps1 fix one-liner
.venv/Scripts/python -c "
path = 'scripts/dev/run-human-tests-05.ps1'
content = open(path, encoding='utf-8').read()
open(path, 'w', encoding='utf-8', newline='').write(content.replace('—', '-'))
"
```

**Para (2) stderr de nativos**: baja el preference solo alrededor
del native call y restáuralo después. NO uses `2>&1`.

```powershell
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $VenvPython $script
    $code = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevErr
}
```

El "PASS/FAIL real" sale de `$LASTEXITCODE`, no de la interpretación
que PowerShell hace del stream de error.

### 3) Stdout de native exe dentro de una función mete basura en el retorno

**Síntoma**: una función que envuelve un native call reporta exit
"raro" — vacío, array, o lo que sea menos el número entero esperado:

```
[FAIL] setup_demo_05.py termino con exit
... (toda la salida del script python aparece aquí) ...
0
```

**Causa**: si dentro de una función PowerShell haces
`& $exe args` sin redirigir, **cada línea de stdout del exe se
añade al pipeline de retorno de la función**. Cuando el llamador
hace `$code = Invoke-NativeScript ...`, recibe un array con todas
esas líneas + el `$LASTEXITCODE`. La condición `$code -ne 0`
evalúa truthy contra el array y el launcher cree que falló.

**Fix**: pipe a `Out-Host` (o `Out-Default`) para empujar el output
al terminal sin pasar por el pipeline:

```powershell
function Invoke-NativeScript {
    param([string]$Path)
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPython $Path | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevErr
    }
    return $code   # SOLO el int — el output ya fue al host
}
```

## Cómo evitarlo desde el principio

Cuando escribas un `.ps1` nuevo en este repo:

- **Solo ASCII en cadenas y comentarios** del script — el código
  Python o Markdown que llama puede tener acentos sin problema, pero
  el `.ps1` que los lanza no.
- **Si vas a llamar un native exe** (python, docker, npm, node,
  cargo) y no quieres que su stderr mate el script, envuelve la
  llamada con el patrón de `$ErrorActionPreference="Continue"` de
  arriba.
- **Si el native call está dentro de una función** que devuelve un
  exit code, pipea el output a `Out-Host` para que no contamine el
  retorno de la función.

## Referencias

- `scripts/dev/run-human-tests-05.ps1` — usa el patrón correcto.
- `scripts/dev/run-human-tests.ps1` (Plan 04.5) — tuvo la misma
  trampa de encoding al crearse; fix idéntico (ASCII puro).
- Bash tool docs del repo, sección PowerShell: "Avoid `2>&1` on
  native executables".
