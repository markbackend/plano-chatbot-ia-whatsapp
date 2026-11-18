"""Guarda conversaciones, mensajes y uso de la IA en SQLite (sin servidor de base de datos)."""

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS conversaciones (
    telefono TEXT PRIMARY KEY,
    nombre TEXT,
    pausada_hasta TEXT,          -- mientras sea futura, el bot no contesta (atiende una persona)
    motivo_pausa TEXT,
    actualizada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mensajes (
    id TEXT PRIMARY KEY,
    telefono TEXT NOT NULL,
    direccion TEXT NOT NULL CHECK (direccion IN ('entrante', 'saliente')),
    texto TEXT NOT NULL,
    fecha TEXT NOT NULL,
    origen TEXT                  -- cliente | ia | sistema
);
CREATE INDEX IF NOT EXISTS mensajes_por_telefono ON mensajes (telefono, fecha);
CREATE TABLE IF NOT EXISTS uso_ia (
    dia TEXT PRIMARY KEY,
    tokens INTEGER NOT NULL DEFAULT 0,
    llamadas INTEGER NOT NULL DEFAULT 0
);
"""


class Almacen:
    def __init__(self, ruta: Path | str = ":memory:"):
        if str(ruta) != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(ruta), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(ESQUEMA)
        self._lock = threading.Lock()  # FastAPI procesa en varios hilos; SQLite prefiere de a uno

    # --- mensajes ---
    def ya_procesado(self, mensaje_id: str) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM mensajes WHERE id = ?", (mensaje_id,)).fetchone() is not None

    def guardar_mensaje(self, mensaje_id: str, telefono: str, direccion: str, texto: str, fecha: datetime,
                        origen: str = "cliente") -> bool:
        """Guarda el mensaje. Devuelve False si ya existía (Meta a veces repite avisos)."""
        with self._lock, self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO mensajes VALUES (?, ?, ?, ?, ?, ?)",
                                  (mensaje_id, telefono, direccion, texto, fecha.isoformat(), origen))
            return cur.rowcount == 1

    def historial(self, telefono: str, limite: int) -> list[dict]:
        """Últimos `limite` mensajes del chat, del más viejo al más nuevo, en formato de la IA."""
        with self._lock:
            filas = self.db.execute(
                "SELECT direccion, texto FROM mensajes WHERE telefono = ? ORDER BY fecha DESC, rowid DESC LIMIT ?",
                (telefono, limite)).fetchall()
        return [{"role": "user" if f["direccion"] == "entrante" else "assistant", "content": f["texto"]}
                for f in reversed(filas)]

    def mensajes_desde(self, telefono: str, desde: datetime) -> int:
        """Cuántos mensajes mandó el cliente desde `desde` (para el límite por hora)."""
        with self._lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM mensajes WHERE telefono = ? AND direccion = 'entrante' AND fecha >= ?",
                (telefono, desde.isoformat())).fetchone()[0]

    def mensajes_de(self, telefono: str, limite: int = 200) -> list[dict]:
        with self._lock:
            filas = self.db.execute(
                "SELECT direccion, texto, fecha, origen FROM mensajes WHERE telefono = ? "
                "ORDER BY fecha DESC, rowid DESC LIMIT ?", (telefono, limite)).fetchall()
        return [dict(f) for f in reversed(filas)]

    # --- conversaciones ---
    def tocar(self, telefono: str, nombre: str | None, fecha: datetime) -> bool:
        """Registra la conversación. Devuelve True si es la primera vez que escribe."""
        with self._lock, self.db:
            nueva = self.db.execute("SELECT 1 FROM conversaciones WHERE telefono = ?", (telefono,)).fetchone() is None
            self.db.execute(
                "INSERT INTO conversaciones (telefono, nombre, actualizada) VALUES (?, ?, ?) "
                "ON CONFLICT(telefono) DO UPDATE SET nombre = COALESCE(excluded.nombre, nombre), "
                "actualizada = excluded.actualizada",
                (telefono, nombre, fecha.isoformat()))
        return nueva

    def pausar(self, telefono: str, hasta: datetime | None, motivo: str = "") -> None:
        """Pausa el bot en ese chat hasta `hasta`. Con None lo reactiva."""
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO conversaciones (telefono, pausada_hasta, motivo_pausa, actualizada) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(telefono) DO UPDATE SET pausada_hasta = excluded.pausada_hasta, "
                "motivo_pausa = excluded.motivo_pausa",
                (telefono, hasta.isoformat() if hasta else None, motivo if hasta else None,
                 datetime.now().isoformat()))

    def pausada(self, telefono: str, ahora: datetime) -> bool:
        with self._lock:
            fila = self.db.execute("SELECT pausada_hasta FROM conversaciones WHERE telefono = ?", (telefono,)).fetchone()
        return bool(fila and fila["pausada_hasta"] and datetime.fromisoformat(fila["pausada_hasta"]) > ahora)

    def existe(self, telefono: str) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM conversaciones WHERE telefono = ?", (telefono,)).fetchone() is not None

    def conversaciones(self, limite: int = 100) -> list[dict]:
        with self._lock:
            filas = self.db.execute(
                "SELECT c.telefono, c.nombre, c.pausada_hasta, c.motivo_pausa, c.actualizada, "
                "(SELECT texto FROM mensajes m WHERE m.telefono = c.telefono ORDER BY fecha DESC, rowid DESC LIMIT 1) "
                "AS ultimo, (SELECT COUNT(*) FROM mensajes m WHERE m.telefono = c.telefono) AS cantidad "
                "FROM conversaciones c ORDER BY c.actualizada DESC LIMIT ?", (limite,)).fetchall()
        return [dict(f) for f in filas]

    def pausadas(self, ahora: datetime) -> list[str]:
        with self._lock:
            filas = self.db.execute("SELECT telefono FROM conversaciones WHERE pausada_hasta > ? ORDER BY telefono",
                                    (ahora.isoformat(),)).fetchall()
        return [f["telefono"] for f in filas]

    # --- uso de la IA (tope de tokens por día) ---
    def sumar_tokens(self, dia: str, tokens: int) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT INTO uso_ia (dia, tokens, llamadas) VALUES (?, ?, 1) ON CONFLICT(dia) DO UPDATE "
                            "SET tokens = tokens + excluded.tokens, llamadas = llamadas + 1", (dia, tokens))

    def tokens_del_dia(self, dia: str) -> int:
        with self._lock:
            fila = self.db.execute("SELECT tokens FROM uso_ia WHERE dia = ?", (dia,)).fetchone()
        return fila["tokens"] if fila else 0

    def uso(self, dias: int = 7) -> list[dict]:
        with self._lock:
            filas = self.db.execute("SELECT dia, tokens, llamadas FROM uso_ia ORDER BY dia DESC LIMIT ?",
                                    (dias,)).fetchall()
        return [dict(f) for f in filas]


def hace_una_hora(ahora: datetime) -> datetime:
    return ahora - timedelta(hours=1)
