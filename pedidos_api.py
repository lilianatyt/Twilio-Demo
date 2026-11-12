import json
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "pedidos.db"))

MENU = {
    "taco de trompo": 20,
    "taco de suadero": 20,
    "taco de bistec": 20,
    "torta de milanesa": 65,
    "torta de trompo": 65,
    "limonada": 30,
    "agua de jamaica": 30,
}
COSTO_ENVIO = 30
ENVIO_GRATIS_DESDE = 200
SE_PUEDE_CAMBIAR = {"recibido", "en preparación"}
COLUMNAS = "numero, nombre, direccion, productos, estatus, subtotal, envio, total"

PEDIDOS_DE_PRUEBA = [
    (1001, "Ana López", "Calle Hidalgo doce", [{"producto": "taco de trompo", "cantidad": 5}], "en camino"),
    (2002, "Luis Pérez", "Avenida Juárez cuarenta", [{"producto": "torta de milanesa", "cantidad": 2}], "en preparación"),
    (3003, "María Ruiz", "Calle Morelos ocho", [{"producto": "agua de jamaica", "cantidad": 1}], "entregado"),
    (4004, "Jorge Díaz", "Calle Allende tres", [{"producto": "taco de bistec", "cantidad": 3}], "cancelado"),
]


def conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def calcular_total(productos: list[dict]) -> dict:
    subtotal = 0
    for p in productos:
        nombre = p["producto"].lower().strip()
        if nombre not in MENU:
            raise HTTPException(400, f"El producto '{p['producto']}' no está en el menú.")
        if p["cantidad"] < 1:
            raise HTTPException(400, "La cantidad debe ser al menos uno.")
        subtotal += MENU[nombre] * p["cantidad"]
    envio = 0 if subtotal >= ENVIO_GRATIS_DESDE else COSTO_ENVIO
    return {"subtotal": subtotal, "envio": envio, "total": subtotal + envio}


def crear_tabla() -> None:
    with closing(conectar()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pedidos (
                numero INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                direccion TEXT NOT NULL,
                productos TEXT NOT NULL,
                estatus TEXT NOT NULL,
                subtotal INTEGER NOT NULL,
                envio INTEGER NOT NULL,
                total INTEGER NOT NULL,
                creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if conn.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] == 0:
            for numero, nombre, direccion, productos, estatus in PEDIDOS_DE_PRUEBA:
                t = calcular_total(productos)
                conn.execute(
                    "INSERT INTO pedidos (numero, nombre, direccion, productos, estatus, subtotal, envio, total) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (numero, nombre, direccion, json.dumps(productos, ensure_ascii=False),
                     estatus, t["subtotal"], t["envio"], t["total"]),
                )
            # Los pedidos nuevos empiezan en 5001
            conn.execute("UPDATE sqlite_sequence SET seq = 5000 WHERE name = 'pedidos'")


@asynccontextmanager
async def lifespan(app: FastAPI):
    crear_tabla()
    yield


app = FastAPI(lifespan=lifespan)


class Producto(BaseModel):
    producto: str
    cantidad: int


class NuevoPedido(BaseModel):
    nombre: str
    direccion: str
    productos: list[Producto]


class CambioPedido(BaseModel):
    direccion: str | None = None
    productos: list[Producto] | None = None


def formatear(fila: sqlite3.Row) -> dict:
    pedido = dict(fila)
    pedido["numero"] = str(pedido["numero"])
    pedido["productos"] = json.loads(pedido["productos"])
    return pedido


def buscar(conn: sqlite3.Connection, numero: str) -> sqlite3.Row:
    if not numero.isdigit():
        raise HTTPException(404, "No existe un pedido con ese número")
    fila = conn.execute(f"SELECT {COLUMNAS} FROM pedidos WHERE numero = ?", (int(numero),)).fetchone()
    if not fila:
        raise HTTPException(404, "No existe un pedido con ese número")
    return fila


def validar_cambiable(pedido: sqlite3.Row, accion: str) -> None:
    if pedido["estatus"] not in SE_PUEDE_CAMBIAR:
        raise HTTPException(409, f"El pedido ya está {pedido['estatus']} y no se puede {accion}.")


@app.get("/pedidos/{numero}")
def consultar_pedido(numero: str):
    with closing(conectar()) as conn:
        return formatear(buscar(conn, numero))


@app.post("/pedidos", status_code=201)
def crear_pedido(datos: NuevoPedido):
    productos = [p.model_dump() for p in datos.productos]
    t = calcular_total(productos)
    with closing(conectar()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO pedidos (nombre, direccion, productos, estatus, subtotal, envio, total) "
            "VALUES (?, ?, ?, 'recibido', ?, ?, ?)",
            (datos.nombre, datos.direccion, json.dumps(productos, ensure_ascii=False),
             t["subtotal"], t["envio"], t["total"]),
        )
        return formatear(buscar(conn, str(cur.lastrowid)))


@app.patch("/pedidos/{numero}")
def modificar_pedido(numero: str, cambios: CambioPedido):
    with closing(conectar()) as conn, conn:
        validar_cambiable(buscar(conn, numero), "modificar")
        if cambios.productos is not None:
            productos = [p.model_dump() for p in cambios.productos]
            t = calcular_total(productos)
            conn.execute(
                "UPDATE pedidos SET productos = ?, subtotal = ?, envio = ?, total = ? WHERE numero = ?",
                (json.dumps(productos, ensure_ascii=False), t["subtotal"], t["envio"], t["total"], int(numero)),
            )
        if cambios.direccion:
            conn.execute(
                "UPDATE pedidos SET direccion = ? WHERE numero = ?",
                (cambios.direccion, int(numero)),
            )
        return formatear(buscar(conn, numero))


@app.post("/pedidos/{numero}/cancelar")
def cancelar_pedido(numero: str):
    with closing(conectar()) as conn, conn:
        validar_cambiable(buscar(conn, numero), "cancelar")
        conn.execute("UPDATE pedidos SET estatus = 'cancelado' WHERE numero = ?", (int(numero),))
        return formatear(buscar(conn, numero))
