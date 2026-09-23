# Buscador de imágenes duplicadas

Aplicación de escritorio que revisa todas las imágenes de una carpeta (incluidas subcarpetas) y detecta duplicadas por su **contenido**, no por el nombre.

## Requisitos

- **Python 3.10 o superior** ([python.org](https://www.python.org/downloads/)). En Windows, marca «Add python.exe to PATH» al instalarlo.
- Tkinter (la interfaz gráfica). Viene incluido con Python en Windows y macOS; en Linux instálalo aparte:
  - Debian/Ubuntu: `sudo apt install python3-tk python3-venv`
  - Fedora: `sudo dnf install python3-tkinter`

## Instalación y ejecución

```bash
git clone <url-del-repositorio>
cd Imagenes_Duplicadas
```

**Windows:** doble clic en `iniciar.bat`.

**macOS / Linux:**

```bash
chmod +x iniciar.sh
./iniciar.sh
```

La primera vez, el lanzador crea un entorno virtual (`.venv`) e instala las dependencias de `requirements.txt`; las siguientes veces abre la aplicación directamente.

<details>
<summary>Instalación manual</summary>

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
</details>

## Uso

1. Elige la carpeta de origen y la de destino.
2. Elige la sensibilidad (ver abajo; «Normal» es la recomendada).
3. Pulsa **Iniciar**. Se buscan imágenes también en las subcarpetas.

La carpeta de origen no se modifica: los archivos se **copian**.

## Resultado en la carpeta de destino

```
Destino/
├── Original/              una copia de cada imagen distinta
├── Duplicadas/
│   ├── grupo_001_foto/    copias extra de la imagen "foto" de Original
│   └── grupo_002_.../
├── Otros/                 videos, documentos y demás archivos que no son imágenes
└── reporte_duplicados.csv detalle de qué pasó con cada archivo
```

De cada grupo de duplicadas se conserva en `Original` la de **mayor resolución** (y, en empate, la de mayor peso).

Los archivos que no son imágenes (videos, documentos, etc.) se copian a `Otros` (se crea solo si hay alguno). Las imágenes dañadas o ilegibles **no se copian**. Al terminar, el resumen de la ventana y el reporte CSV listan los nombres y la cantidad de cada tipo.

## Cómo detecta duplicadas

- **Exactas**: mismo contenido de archivo (hash SHA-256), aunque el nombre sea distinto.
- **Visuales**: la misma imagen redimensionada, recomprimida o en otro formato (JPG/PNG/WebP/HEIC/RAW), en dos pasos:
  1. Un hash perceptual (dHash de 256 bits) encuentra candidatas parecidas. También se exige que la proporción ancho/alto sea similar.
  2. Se confirma comparando miniaturas ecualizadas zona por zona (8×8). Así se descartan fotos de la misma escena con alguna zona distinta (ráfagas, una persona que se movió, un encuadre desplazado).

### Sensibilidad

| Opción | Agrupa | No agrupa |
|---|---|---|
| Muy estricta | copias, recompresión, cambio de tamaño o formato | cualquier edición visible |
| **Normal** (recomendada) | lo anterior + ajustes leves de color/brillo | encuadres distintos, objetos añadidos o movidos |
| Flexible | lo anterior + revelados o retoques fuertes del mismo RAW | puede agrupar fotos de ráfaga casi idénticas |

Limitación: un cambio muy pequeño (un objeto que ocupa menos de ~1 % de la foto) no se distingue; esas fotos se tratan como duplicadas.

Formatos: JPG, PNG, GIF, BMP, TIFF, WebP, ICO, PPM/PGM/PBM, **HEIC/HEIF** (fotos de iPhone) y **RAW** de cámara (DNG, CR2, CR3, NEF, ARW, RAF, ORF, RW2, PEF, SRW, etc.).

- HEIC requiere `pillow-heif` y RAW requiere `rawpy` (incluidos en `requirements.txt`). Si faltan, esos formatos se ignoran.
- En los RAW se compara la vista previa JPEG que la cámara guarda dentro del archivo (mucho más rápido que revelarlo). Así, un RAW y el JPEG que la cámara sacó de la misma foto se detectan como duplicados; se conserva en `Original` el de mayor resolución/peso (normalmente el RAW).

## Estructura del proyecto

| Archivo | Contenido |
|---|---|
| `app.py` | Interfaz gráfica (Tkinter) |
| `duplicados.py` | Detección de duplicadas y copia de archivos |
| `iniciar.bat` / `iniciar.sh` | Lanzadores para Windows / macOS-Linux |
| `requirements.txt` | Dependencias (Pillow, pillow-heif, rawpy) |
