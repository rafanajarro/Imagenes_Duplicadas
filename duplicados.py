"""Lógica de detección de imágenes duplicadas (exactas y visualmente iguales)."""

from __future__ import annotations

import csv
import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import io

from PIL import Image, ImageChops, ImageOps

Image.MAX_IMAGE_PIXELS = None  # permitir imágenes muy grandes

EXTENSIONES = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".bmp", ".dib",
    ".tif", ".tiff", ".webp", ".ico", ".ppm", ".pgm", ".pbm",
}

EXTENSIONES_HEIC = {".heic", ".heif", ".hif"}
EXTENSIONES_RAW = {
    ".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".raf", ".orf", ".rw2", ".rwl", ".pef", ".srw", ".x3f", ".3fr", ".erf",
    ".kdc", ".dcr", ".mrw", ".mef", ".mos", ".iiq", ".raw",
}

# Soporte opcional: si falta la librería, esos formatos simplemente no se buscan.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    EXTENSIONES |= EXTENSIONES_HEIC
    SOPORTE_HEIC = True
except ImportError:
    SOPORTE_HEIC = False

try:
    import rawpy
    EXTENSIONES |= EXTENSIONES_RAW
    SOPORTE_RAW = True
except ImportError:
    SOPORTE_RAW = False

TAM_HASH = 16  # dHash de 16x16 = 256 bits
BITS_HASH = TAM_HASH * TAM_HASH
TAM_MINI = 32  # miniatura para la verificación por zonas
BLOQUES = 8    # la verificación divide la imagen en BLOQUES x BLOQUES zonas

Progreso = Callable[[str, int, int], None]  # (etapa, actual, total)


@dataclass
class InfoImagen:
    ruta: Path
    tamano: int
    ancho: int = 0
    alto: int = 0
    sha256: str = ""
    dhash: int = 0
    miniatura: bytes = b""
    error: str = ""

    @property
    def pixeles(self) -> int:
        return self.ancho * self.alto


@dataclass
class Resultado:
    unicas: list[InfoImagen] = field(default_factory=list)
    grupos: list[list[InfoImagen]] = field(default_factory=list)  # [0] = la que se conserva
    errores: list[InfoImagen] = field(default_factory=list)  # imágenes que no se pudieron leer
    ignorados: list[Path] = field(default_factory=list)      # archivos que no son imágenes (videos, documentos...)
    otros_fallidos: list[tuple[Path, str]] = field(default_factory=list)  # de ignorados, los que no se pudieron copiar


def buscar_archivos(origen: Path, excluir: Iterable[Path] = ()) -> tuple[list[Path], list[Path]]:
    """Devuelve (imágenes, otros archivos) encontrados en origen y sus subcarpetas."""
    excluir = [p.resolve() for p in excluir]
    imagenes, otros = [], []
    for raiz, dirs, archivos in os.walk(origen):
        raiz_p = Path(raiz).resolve()
        dirs[:] = [d for d in dirs if (raiz_p / d).resolve() not in excluir]
        for nombre in archivos:
            (imagenes if Path(nombre).suffix.lower() in EXTENSIONES else otros).append(raiz_p / nombre)
    imagenes.sort()
    otros.sort()
    return imagenes, otros


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _dhash(img: Image.Image) -> int:
    """Hash de diferencias: compara cada píxel con su vecino en una miniatura en grises."""
    gris = img.convert("L").resize((TAM_HASH + 1, TAM_HASH), Image.Resampling.LANCZOS)
    px = gris.load()
    valor = 0
    for y in range(TAM_HASH):
        for x in range(TAM_HASH):
            valor = (valor << 1) | (px[x, y] > px[x + 1, y])
    return valor


def _miniatura(img: Image.Image) -> bytes:
    """Miniatura en grises con histograma ecualizado (neutraliza cambios de brillo, contraste y revelado)."""
    gris = img.convert("L").resize((128, 128), Image.Resampling.BOX)
    return ImageOps.equalize(gris).resize((TAM_MINI, TAM_MINI), Image.Resampling.BOX).tobytes()


def diferencia_zonas(a: InfoImagen, b: InfoImagen) -> int:
    """Mayor diferencia media (0-255) entre zonas equivalentes de dos imágenes.

    El hash es global y apenas cambia si solo una zona es distinta (una persona que se movió,
    un objeto añadido). Esta comprobación descarta esos casos.
    """
    ma = Image.frombytes("L", (TAM_MINI, TAM_MINI), a.miniatura)
    mb = Image.frombytes("L", (TAM_MINI, TAM_MINI), b.miniatura)
    return max(ImageChops.difference(ma, mb).resize((BLOQUES, BLOQUES), Image.Resampling.BOX).getdata())


_GIRO_RAW = {3: Image.Transpose.ROTATE_180, 5: Image.Transpose.ROTATE_90, 6: Image.Transpose.ROTATE_270}


def _abrir_raw(ruta: Path) -> tuple[Image.Image, int, int]:
    """Devuelve (imagen para el hash, ancho, alto) de un RAW, ya orientados.

    Usa la vista previa JPEG incrustada (rápido); si no existe, revela el RAW a media resolución.
    """
    with rawpy.imread(str(ruta)) as raw:
        giro = raw.sizes.flip
        ancho, alto = raw.sizes.width, raw.sizes.height
        if giro in (5, 6):
            ancho, alto = alto, ancho
        try:
            miniatura = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            miniatura = None

        if miniatura is not None and miniatura.format == rawpy.ThumbFormat.JPEG:
            img = Image.open(io.BytesIO(miniatura.data))
            if img.getexif().get(0x0112, 1) != 1:
                img = ImageOps.exif_transpose(img)
            elif giro in _GIRO_RAW:
                img = img.transpose(_GIRO_RAW[giro])
        elif miniatura is not None and miniatura.format == rawpy.ThumbFormat.BITMAP:
            img = Image.fromarray(miniatura.data)
            if giro in _GIRO_RAW:
                img = img.transpose(_GIRO_RAW[giro])
        else:
            img = Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True))  # ya orientada
    return img, ancho, alto


def analizar_imagen(ruta: Path) -> InfoImagen:
    info = InfoImagen(ruta=ruta, tamano=0)
    try:
        info.tamano = ruta.stat().st_size
        info.sha256 = _sha256(ruta)
        if ruta.suffix.lower() in EXTENSIONES_RAW:
            img, info.ancho, info.alto = _abrir_raw(ruta)
            info.dhash, info.miniatura = _dhash(img), _miniatura(img)
            return info
        with Image.open(ruta) as img:
            img.seek(0)
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                # aplanar transparencia sobre blanco para que no altere el hash
                rgba = img.convert("RGBA")
                fondo = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                img = Image.alpha_composite(fondo, rgba)
            info.ancho, info.alto = img.size
            info.dhash, info.miniatura = _dhash(img), _miniatura(img)
    except Exception as e:  # archivo corrupto o formato no soportado
        info.error = f"{type(e).__name__}: {e}"
    return info


class _BKTree:
    """Árbol BK para buscar hashes a distancia de Hamming <= umbral sin comparar todos contra todos."""

    def __init__(self):
        self.raiz = None  # (hash, idx, hijos)

    def agregar(self, h: int, idx: int):
        if self.raiz is None:
            self.raiz = (h, idx, {})
            return
        nodo = self.raiz
        while True:
            d = (h ^ nodo[0]).bit_count()
            if d in nodo[2]:
                nodo = nodo[2][d]
            else:
                nodo[2][d] = (h, idx, {})
                return

    def buscar(self, h: int, umbral: int) -> list[int]:
        res, pila = [], [self.raiz] if self.raiz else []
        while pila:
            nodo = pila.pop()
            d = (h ^ nodo[0]).bit_count()
            if d <= umbral:
                res.append(nodo[1])
            for dist, hijo in nodo[2].items():
                if d - umbral <= dist <= d + umbral:
                    pila.append(hijo)
        return res


def _aspecto_similar(a: InfoImagen, b: InfoImagen, tolerancia: float = 0.05) -> bool:
    ra, rb = a.ancho / a.alto, b.ancho / b.alto
    return abs(ra - rb) / max(ra, rb) <= tolerancia


def agrupar(infos: list[InfoImagen], umbral: int, umbral_zonas: int,
            progreso: Progreso | None = None) -> Resultado:
    """umbral: distancia de Hamming máxima entre hashes (sobre 256 bits) para ser candidatas.
    umbral_zonas: diferencia máxima permitida en cualquier zona (ver diferencia_zonas)."""
    resultado = Resultado()
    validas = []
    for i in infos:
        (resultado.errores if i.error else validas).append(i)

    padre = list(range(len(validas)))

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(a, b):
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            padre[rb] = ra

    # 1) duplicados exactos (mismo contenido de archivo)
    por_sha: dict[str, int] = {}
    for idx, info in enumerate(validas):
        if info.sha256 in por_sha:
            unir(por_sha[info.sha256], idx)
        else:
            por_sha[info.sha256] = idx

    # 2) duplicados visuales (mismos píxeles aunque cambie formato, tamaño o compresión)
    arbol = _BKTree()
    representantes = list(por_sha.values())
    for n, idx in enumerate(representantes):
        info = validas[idx]
        for otro in arbol.buscar(info.dhash, umbral):
            if _aspecto_similar(info, validas[otro]) and diferencia_zonas(info, validas[otro]) <= umbral_zonas:
                unir(otro, idx)
        arbol.agregar(info.dhash, idx)
        if progreso:
            progreso("Comparando", n + 1, len(representantes))

    grupos: dict[int, list[InfoImagen]] = {}
    for idx, info in enumerate(validas):
        grupos.setdefault(raiz(idx), []).append(info)

    for miembros in grupos.values():
        if len(miembros) == 1:
            resultado.unicas.append(miembros[0])
        else:
            # se conserva la de mayor resolución, luego la de mayor peso, luego la de ruta más corta
            miembros.sort(key=lambda i: (-i.pixeles, -i.tamano, len(str(i.ruta)), str(i.ruta)))
            resultado.grupos.append(miembros)
    resultado.grupos.sort(key=lambda g: str(g[0].ruta).lower())
    resultado.unicas.sort(key=lambda i: str(i.ruta).lower())
    return resultado


def _destino_libre(carpeta: Path, nombre: str) -> Path:
    destino = carpeta / nombre
    base, ext = Path(nombre).stem, Path(nombre).suffix
    n = 1
    while destino.exists():
        destino = carpeta / f"{base}_{n}{ext}"
        n += 1
    return destino


def _nombre_seguro(texto: str, largo: int = 40) -> str:
    limpio = "".join(c if c.isalnum() or c in "-_ " else "_" for c in texto).strip()
    return limpio[:largo] or "imagen"


def copiar_resultado(resultado: Resultado, destino: Path, progreso: Progreso | None = None) -> Path:
    dir_orig = destino / "Original"
    dir_dup = destino / "Duplicadas"
    dir_orig.mkdir(parents=True, exist_ok=True)
    dir_dup.mkdir(parents=True, exist_ok=True)

    total = len(resultado.unicas) + sum(len(g) for g in resultado.grupos) + len(resultado.ignorados)
    hechos = 0
    filas = []

    def avanzar():
        nonlocal hechos
        hechos += 1
        if progreso:
            progreso("Copiando", hechos, total)

    for info in resultado.unicas:
        final = _destino_libre(dir_orig, info.ruta.name)
        shutil.copy2(info.ruta, final)
        filas.append(["", "única", info.ruta, final, f"{info.ancho}x{info.alto}", info.tamano])
        avanzar()

    for n, grupo in enumerate(resultado.grupos, 1):
        conservada, copias = grupo[0], grupo[1:]
        final = _destino_libre(dir_orig, conservada.ruta.name)
        shutil.copy2(conservada.ruta, final)
        filas.append([n, "conservada", conservada.ruta, final,
                      f"{conservada.ancho}x{conservada.alto}", conservada.tamano])
        avanzar()

        dir_grupo = dir_dup / f"grupo_{n:03d}_{_nombre_seguro(final.stem)}"
        dir_grupo.mkdir(exist_ok=True)
        for info in copias:
            final_c = _destino_libre(dir_grupo, info.ruta.name)
            shutil.copy2(info.ruta, final_c)
            tipo = "duplicada exacta" if info.sha256 == conservada.sha256 else "duplicada visual"
            filas.append([n, tipo, info.ruta, final_c, f"{info.ancho}x{info.alto}", info.tamano])
            avanzar()

    if resultado.ignorados:
        dir_otros = destino / "Otros"
        dir_otros.mkdir(exist_ok=True)
    for ruta in resultado.ignorados:
        # un video o documento bloqueado no debe detener la copia del resto
        try:
            final = _destino_libre(dir_otros, ruta.name)
            shutil.copy2(ruta, final)
            filas.append(["", "no es imagen", ruta, final, "", final.stat().st_size])
        except OSError as e:
            resultado.otros_fallidos.append((ruta, f"{type(e).__name__}: {e}"))
            filas.append(["", "no es imagen (no se pudo copiar)", ruta, "", f"{type(e).__name__}: {e}", ""])
        avanzar()

    for info in resultado.errores:
        filas.append(["", "error de lectura (no copiada)", info.ruta, "", info.error, info.tamano])

    reporte = destino / "reporte_duplicados.csv"
    with open(reporte, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["grupo", "tipo", "archivo_origen", "archivo_destino", "resolución", "bytes"])
        w.writerows(filas)
    return reporte
