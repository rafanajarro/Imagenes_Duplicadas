"""Aplicación de escritorio para separar imágenes únicas de duplicadas."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import duplicados

# (umbral del hash, umbral de diferencia por zonas)
SENSIBILIDADES = {
    "Muy estricta (casi idénticas)": (6, 8),
    "Normal (recomendada)": (16, 15),
    "Flexible (incluye retoques fuertes, riesgo de falsos positivos)": (32, 24),
}


def abrir_carpeta(ruta: Path):
    if sys.platform == "win32":
        os.startfile(ruta)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(ruta)])


def _motivo(error: str) -> str:
    if error.startswith("UnidentifiedImageError"):
        return "archivo dañado o no es una imagen válida"
    if error.startswith("LibRaw"):
        return "RAW dañado o de un modelo no soportado"
    if error.startswith(("PermissionError", "OSError")):
        return "no se pudo abrir (permisos o archivo en uso)"
    return error[:80]


def _lista(titulo: str, elementos: list, origen: Path) -> str:
    """Lista de archivos con su ruta relativa a la carpeta de origen. Acepta rutas o tuplas (ruta, motivo)."""
    lineas = [f"{titulo} ({len(elementos)}):"]
    for e in elementos:
        ruta, motivo = e if isinstance(e, tuple) else (e, "")
        try:
            nombre = ruta.relative_to(origen.resolve())
        except ValueError:
            nombre = ruta
        lineas.append(f"  • {nombre}" + (f" — {motivo}" if motivo else ""))
    return "\n".join(lineas)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Buscador de imágenes duplicadas")
        self.geometry("720x520")
        self.minsize(560, 420)

        self.origen = tk.StringVar()
        self.destino = tk.StringVar()
        self.sensibilidad = tk.StringVar(value="Normal (recomendada)")
        self.estado = tk.StringVar(value="Selecciona las carpetas y pulsa «Iniciar».")
        self.cola: queue.Queue = queue.Queue()
        self.trabajando = False

        self._construir()
        self.after(100, self._procesar_cola)

    def _construir(self):
        marco = ttk.Frame(self, padding=16)
        marco.pack(fill="both", expand=True)
        marco.columnconfigure(1, weight=1)

        ttk.Label(marco, text="Carpeta de origen:").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(marco, textvariable=self.origen).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(marco, text="Examinar…", command=lambda: self._elegir(self.origen)).grid(row=0, column=2)

        ttk.Label(marco, text="Carpeta de destino:").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(marco, textvariable=self.destino).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(marco, text="Examinar…", command=lambda: self._elegir(self.destino)).grid(row=1, column=2)

        ttk.Label(marco, text="Sensibilidad:").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(marco, textvariable=self.sensibilidad, values=list(SENSIBILIDADES),
                     state="readonly").grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0))

        self.boton = ttk.Button(marco, text="Iniciar", command=self._iniciar)
        self.boton.grid(row=3, column=0, columnspan=3, pady=(16, 8), ipadx=20)

        self.barra = ttk.Progressbar(marco, mode="determinate")
        self.barra.grid(row=4, column=0, columnspan=3, sticky="ew")
        ttk.Label(marco, textvariable=self.estado).grid(row=5, column=0, columnspan=3, sticky="w", pady=4)

        self.resumen = tk.Text(marco, height=12, state="disabled", wrap="word")
        self.resumen.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        desplazar = ttk.Scrollbar(marco, orient="vertical", command=self.resumen.yview)
        desplazar.grid(row=6, column=3, sticky="ns", pady=(8, 0))
        self.resumen.configure(yscrollcommand=desplazar.set)
        marco.rowconfigure(6, weight=1)

    def _elegir(self, var: tk.StringVar):
        ruta = filedialog.askdirectory(initialdir=var.get() or None)
        if ruta:
            var.set(os.path.normpath(ruta))

    def _escribir_resumen(self, texto: str):
        self.resumen.configure(state="normal")
        self.resumen.delete("1.0", "end")
        self.resumen.insert("end", texto)
        self.resumen.configure(state="disabled")

    def _iniciar(self):
        if self.trabajando:
            return
        origen, destino = Path(self.origen.get().strip()), Path(self.destino.get().strip())
        if not self.origen.get().strip() or not origen.is_dir():
            messagebox.showerror("Error", "Selecciona una carpeta de origen válida.")
            return
        if not self.destino.get().strip():
            messagebox.showerror("Error", "Selecciona una carpeta de destino.")
            return
        if origen.resolve() == destino.resolve():
            messagebox.showerror("Error", "La carpeta de destino no puede ser la misma que la de origen.")
            return
        existentes = [d for d in ("Original", "Duplicadas", "No_leidas", "Otros") if (destino / d).exists() and any((destino / d).iterdir())]
        if existentes and not messagebox.askyesno(
                "Carpetas existentes",
                f"En el destino ya existen con contenido: {', '.join(existentes)}.\n"
                "Los archivos nuevos se añadirán sin sobrescribir los existentes. ¿Continuar?"):
            return

        self.trabajando = True
        self.boton.configure(state="disabled")
        self._escribir_resumen("")
        umbrales = SENSIBILIDADES[self.sensibilidad.get()]
        threading.Thread(target=self._trabajo, args=(origen, destino, umbrales), daemon=True).start()

    def _progreso(self, etapa: str, actual: int, total: int):
        self.cola.put(("progreso", etapa, actual, total))

    def _trabajo(self, origen: Path, destino: Path, umbrales: tuple[int, int]):
        try:
            self.cola.put(("progreso", "Buscando imágenes", 0, 0))
            rutas, otros = duplicados.buscar_archivos(origen, excluir=[destino])
            if not rutas and not otros:
                self.cola.put(("fin", "La carpeta de origen está vacía.", None))
                return

            infos = []
            with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 2))) as ex:
                for n, info in enumerate(ex.map(duplicados.analizar_imagen, rutas), 1):
                    infos.append(info)
                    self._progreso("Analizando", n, len(rutas))

            resultado = duplicados.agrupar(infos, *umbrales, progreso=self._progreso)
            resultado.ignorados = otros
            reporte = duplicados.copiar_resultado(resultado, destino, self._progreso)

            n_dup = sum(len(g) - 1 for g in resultado.grupos)
            texto = (
                f"Imágenes analizadas: {len(rutas)}\n"
                f"Sin duplicados: {len(resultado.unicas)}\n"
                f"Grupos de duplicados: {len(resultado.grupos)}\n"
                f"Copias duplicadas: {n_dup}\n"
                f"Imágenes que no se pudieron leer: {len(resultado.errores)}\n"
                f"Archivos que no son imágenes: {len(otros)}\n\n"
                f"Original: {len(resultado.unicas) + len(resultado.grupos)} imágenes → {destino / 'Original'}\n"
                f"Duplicadas: {n_dup} imágenes → {destino / 'Duplicadas'}\n"
            )
            fallidos = {r for r, _ in resultado.no_copiados}
            no_leidas = [(i.ruta, _motivo(i.error)) for i in resultado.errores if i.ruta not in fallidos]
            otros_copiados = [r for r in otros if r not in fallidos]
            if resultado.errores:
                texto += f"No_leidas: {len(no_leidas)} imágenes → {destino / 'No_leidas'}\n"
            if otros:
                texto += f"Otros: {len(otros_copiados)} archivos → {destino / 'Otros'}\n"
            texto += f"Reporte: {reporte}"
            if no_leidas:
                texto += "\n\n" + _lista("Imágenes que no se pudieron leer (copiadas a No_leidas)", no_leidas, origen)
            if otros_copiados:
                texto += "\n\n" + _lista("Archivos que no son imágenes (copiados a Otros)", otros_copiados, origen)
            if resultado.no_copiados:
                texto += "\n\n" + _lista("Archivos que NO se pudieron copiar",
                                         [(r, _motivo(e)) for r, e in resultado.no_copiados], origen)
            self.cola.put(("fin", texto, destino))
        except Exception as e:
            self.cola.put(("error", f"{type(e).__name__}: {e}", None))

    def _procesar_cola(self):
        try:
            while True:
                msg = self.cola.get_nowait()
                if msg[0] == "progreso":
                    _, etapa, actual, total = msg
                    if total:
                        self.barra.configure(mode="determinate", maximum=total, value=actual)
                        self.estado.set(f"{etapa}… {actual} de {total}")
                    else:
                        self.barra.configure(mode="indeterminate")
                        self.barra.start(10)
                        self.estado.set(f"{etapa}…")
                    if total:
                        self.barra.stop()
                else:
                    self.barra.stop()
                    self.barra.configure(mode="determinate", value=0)
                    self.trabajando = False
                    self.boton.configure(state="normal")
                    if msg[0] == "fin":
                        self.estado.set("Proceso terminado.")
                        self._escribir_resumen(msg[1])
                        if msg[2] and messagebox.askyesno("Terminado", "¿Abrir la carpeta de destino?"):
                            abrir_carpeta(msg[2])
                    else:
                        self.estado.set("Ocurrió un error.")
                        messagebox.showerror("Error", msg[1])
        except queue.Empty:
            pass
        self.after(100, self._procesar_cola)


if __name__ == "__main__":
    App().mainloop()
